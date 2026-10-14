"""
自选股 / 设置：基于操作的原子合并（FIR-19 / FIR-20 / FIR-21 / FIR-31 §7）

合并规则（与 V5 页面 mergeWatchlistsWithTombstones 的语义一致，并补上时间顺序）：
- add(code, at)：若存在 tombstone 且 deleted_at >= at → 忽略（删除不复活）；否则加入并清除 tombstone。
- remove(code, at)：若条目 added_at > at → 忽略（之后又加过）；否则删除并记 tombstone（保留最晚 deleted_at）。
- clear(at)：对 added_at <= at 的所有条目执行 remove。
- 每个操作带 opId，重复提交幂等；整批在一个事务中原子应用；版本号 +1（ETag）。
- 客户端时间晚于服务器 5 分钟以上 → 以服务器时间为准（防止未来时间戳永久压制其他设备）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from core import analysis as A

from .db import Database, dumps
from .errors import InvalidRequest, PreconditionFailed

_OPID_RE = re.compile(r"^[A-Za-z0-9_.:-]{8,80}$")
_SETTING_KEYS = {"scanMinScore", "scanSignalType", "scanPatterns", "scanMaxBarsSince", "chartRange", "theme"}
MAX_OPS = 500
MAX_WATCHLIST = 500
CLOCK_SKEW = timedelta(minutes=5)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def fmt(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def norm_time(value: Optional[str], now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    if value is None:
        return fmt(now)
    if not isinstance(value, str):
        raise InvalidRequest("时间必须是 ISO8601 字符串")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise InvalidRequest(f"无法识别的时间：{value!r}") from e
    if dt.tzinfo is None:
        raise InvalidRequest(f"时间必须带时区：{value!r}")
    if dt > now + CLOCK_SKEW:
        dt = now
    return fmt(dt)


def valid_code(code: Any) -> str:
    if not isinstance(code, str):
        raise InvalidRequest("股票代码必须是字符串")
    A.exchange_symbol(code)   # 6 位数字 + 可识别交易所，否则 InvalidInputError
    return code


# ------------------------------------------------------------------ 读取

def snapshot(db: Database, user_id: str) -> Dict[str, Any]:
    wl = db.query("SELECT code, added_at, tags FROM watchlist WHERE user_id=? ORDER BY added_at, code", (user_id,))
    ts = db.query("SELECT code, deleted_at FROM watchlist_tombstones WHERE user_id=? ORDER BY code", (user_id,))
    st = db.query("SELECT key, value FROM settings WHERE user_id=? ORDER BY key", (user_id,))
    import json
    return {
        "version": db.version(user_id),
        "watchlist": [{"code": r["code"], "addedAt": r["added_at"], "tags": json.loads(r["tags"])} for r in wl],
        "tombstones": [{"code": r["code"], "deletedAt": r["deleted_at"]} for r in ts],
        "settings": {r["key"]: json.loads(r["value"]) for r in st},
    }


def etag(version: int) -> str:
    return f'W/"v{version}"'


# ------------------------------------------------------------------ 写入

def _add(c, user_id, code, at, tags) -> Tuple[str, Optional[str]]:
    t = c.execute("SELECT deleted_at FROM watchlist_tombstones WHERE user_id=? AND code=?", (user_id, code)).fetchone()
    if t and t["deleted_at"] >= at:
        return "ignored", "deleted_later"          # 删除不复活
    cur = c.execute("SELECT added_at FROM watchlist WHERE user_id=? AND code=?", (user_id, code)).fetchone()
    if cur is None:
        n = c.execute("SELECT COUNT(*) AS n FROM watchlist WHERE user_id=?", (user_id,)).fetchone()["n"]
        if n >= MAX_WATCHLIST:
            return "ignored", "watchlist_full"
        c.execute("INSERT INTO watchlist(user_id, code, added_at, tags) VALUES(?,?,?,?)",
                  (user_id, code, at, dumps(tags or [])))
    else:
        if tags is not None:
            c.execute("UPDATE watchlist SET tags=? WHERE user_id=? AND code=?", (dumps(tags), user_id, code))
        if at > cur["added_at"]:
            c.execute("UPDATE watchlist SET added_at=? WHERE user_id=? AND code=?", (at, user_id, code))
    c.execute("DELETE FROM watchlist_tombstones WHERE user_id=? AND code=?", (user_id, code))
    return "applied", None


def _remove(c, user_id, code, at) -> Tuple[str, Optional[str]]:
    cur = c.execute("SELECT added_at FROM watchlist WHERE user_id=? AND code=?", (user_id, code)).fetchone()
    if cur is not None and cur["added_at"] > at:
        return "ignored", "added_later"
    c.execute("DELETE FROM watchlist WHERE user_id=? AND code=?", (user_id, code))
    c.execute("INSERT INTO watchlist_tombstones(user_id, code, deleted_at) VALUES(?,?,?) "
              "ON CONFLICT(user_id, code) DO UPDATE SET deleted_at = MAX(deleted_at, excluded.deleted_at)",
              (user_id, code, at))
    return "applied", None


def _validate_tags(tags):
    if tags is None:
        return None
    if not isinstance(tags, list) or len(tags) > 10 or not all(isinstance(t, str) and 0 < len(t) <= 20 for t in tags):
        raise InvalidRequest("tags 必须是最多 10 个、每个 1~20 字的字符串")
    return sorted(set(t.strip() for t in tags if t.strip()))


def apply_ops(db: Database, user_id: str, ops: List[Dict[str, Any]], if_match: Optional[str] = None,
              now: Optional[datetime] = None) -> Dict[str, Any]:
    if not isinstance(ops, list) or not ops:
        raise InvalidRequest("ops 必须是非空数组")
    if len(ops) > MAX_OPS:
        raise InvalidRequest(f"单次最多 {MAX_OPS} 个操作")
    now = now or utcnow()
    # 先完整校验，再进入事务（整批原子）
    parsed = []
    for i, op in enumerate(ops):
        if not isinstance(op, dict):
            raise InvalidRequest(f"ops[{i}] 必须是对象")
        kind, op_id = op.get("op"), op.get("opId")
        if not isinstance(op_id, str) or not _OPID_RE.match(op_id):
            raise InvalidRequest(f"ops[{i}].opId 必须是 8~80 位字母数字（幂等键）")
        at = norm_time(op.get("at"), now)
        if kind in ("add", "remove"):
            parsed.append((op_id, kind, valid_code(op.get("code")), at, _validate_tags(op.get("tags"))))
        elif kind == "clear":
            parsed.append((op_id, kind, None, at, None))
        elif kind == "setting":
            key = op.get("key")
            if key not in _SETTING_KEYS:
                raise InvalidRequest(f"不支持的设置项：{key!r}")
            parsed.append((op_id, kind, key, at, op.get("value")))
        else:
            raise InvalidRequest(f"ops[{i}].op 只能是 add / remove / clear / setting")

    results = []
    with db.tx() as c:
        current = db.version(user_id, c)
        if if_match is not None and if_match != etag(current):
            raise PreconditionFailed("数据已被其他设备修改，请刷新后重试", details={"currentVersion": current})
        changed = False
        for op_id, kind, arg, at, extra in parsed:
            prev = c.execute("SELECT result FROM applied_ops WHERE user_id=? AND op_id=?", (user_id, op_id)).fetchone()
            if prev:
                results.append({"opId": op_id, "status": "duplicate"})
                continue
            if kind == "add":
                status, reason = _add(c, user_id, arg, at, extra)
            elif kind == "remove":
                status, reason = _remove(c, user_id, arg, at)
            elif kind == "clear":
                codes = [r["code"] for r in c.execute(
                    "SELECT code FROM watchlist WHERE user_id=? AND added_at<=?", (user_id, at)).fetchall()]
                for code in codes:
                    _remove(c, user_id, code, at)
                status, reason = "applied", None
            else:
                c.execute("INSERT INTO settings(user_id, key, value, updated_at) VALUES(?,?,?,?) "
                          "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at "
                          "WHERE excluded.updated_at >= settings.updated_at", (user_id, arg, dumps(extra), at))
                status, reason = "applied", None
            changed = changed or status == "applied"
            r = {"opId": op_id, "status": status}
            if reason:
                r["reason"] = reason
            c.execute("INSERT INTO applied_ops(user_id, op_id, applied_at, result) VALUES(?,?,?,?)",
                      (user_id, op_id, fmt(now), dumps(r)))
            results.append(r)
        version = Database.bump(c, user_id) if changed else current
    return {"version": version, "results": results}


# ------------------------------------------------------------------ 导出（V5 v1.2 格式，用于备份 / GitHub）

def export_v12(db: Database, user_id: str, sectors_export: Dict[str, Any]) -> Dict[str, Any]:
    s = snapshot(db, user_id)
    now = utcnow()
    return {
        "version": "1.2",
        "exportDate": now.strftime("%Y-%m-%d"),
        "exportTime": fmt(now),
        "description": "K线形态分析系统 - 自选股导出（V6 分析服务）",
        "watchlist": [w["code"] for w in s["watchlist"]],
        "tombstones": s["tombstones"],
        "customSectors": sectors_export.get("customSectors", []),
        "dynamicSectors": sectors_export.get("dynamicSectors", []),
        "dynamicSectorTombstones": sectors_export.get("dynamicSectorTombstones", []),
        "settings": s["settings"],
        "stats": {"watchlistCount": len(s["watchlist"]), "tombstoneCount": len(s["tombstones"]),
                  "customSectorsCount": len(sectors_export.get("customSectors", [])),
                  "dynamicSectorsCount": len(sectors_export.get("dynamicSectors", [])),
                  "dynamicSectorTombstoneCount": len(sectors_export.get("dynamicSectorTombstones", []))},
        "syncMeta": {"exportedBy": "analysis_api", "stateVersion": s["version"]},
    }
