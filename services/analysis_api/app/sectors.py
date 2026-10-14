"""
板块：预置 / 自定义 / 同花顺动态板块（FIR-15 / FIR-22 / FIR-29 / FIR-31 §6C-D）

同花顺动态板块状态机（fail-closed）：
  pending      —— 只做了 6 位格式校验，没有权威成分股（禁止扫描）
  verified     —— 权威快照（来源 + as_of_date + 成分股）已对账，且 as_of_date 在 7 天内（可扫描）
  stale        —— 已对账但超过 7 天（禁止扫描，需重新验证）
  unavailable  —— 权威源明确无法提供（禁止扫描）

自动获取（THS_SECTOR_SOURCE=web，见 ths_web.py）：
  - 添加板块、刷新（不带 snapshot 的验证请求）、扫描当天尚未拉取过的板块时，自动从同花顺网页接口拉取成分股，
    生成与手工快照同格式的快照，走下面同一套校验与存证；
  - 拉取失败不猜测、不换源：板块保持原状态，并返回失败原因和手工验证指令。

验证入口：POST /api/v1/sectors/{code}/verification
  - 带 snapshot：按 core.sector_validation.pipeline.load_authoritative_snapshot 的同一规则校验，
    通过后写入成分股与 provenance，原始快照存证；
  - 不带 snapshot：已配置自动获取时先自动拉取；未配置或失败时返回 manual_required + 可复制的验证指令（例如“帮我验证同花顺板块 886032”）
    和快照格式说明。服务端不猜测板块名称或成分股。
"""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core import analysis as A
from core.sector_validation.dynamic_sector import DynamicSector, validate_ths_sector_code
from core.sector_validation.pipeline import AuthoritativeSnapshotError, load_authoritative_snapshot
from core.sector_validation.reconciler import reconcile_codes
from core.sector_validation.types import FreshnessLevel, ReconciliationMethod, SectorProvenance

from .db import Database, dumps
from .errors import Conflict, InvalidRequest, NotFound
from .ths_web import SOURCE as THS_WEB_SOURCE, SectorSourceError
from .userstate import fmt, norm_time, utcnow, valid_code

FRESH_DAYS = 7
_NAME_RE = re.compile(r"^[\w一-鿿·（）()\-+ ]{1,20}$")
MAX_CUSTOM_CODES = 300


def verification_instruction(code: str) -> Dict[str, Any]:
    return {
        "status": "manual_required",
        "instruction": f"帮我验证同花顺板块 {code}",
        "how": "把上面这句话发给已接入同花顺 iFinD 的 Claude 助手 / Skill，"
               "得到权威快照 JSON 后，在本页“提交验证快照”处粘贴提交。",
        "snapshot_schema": {
            "schema_version": "1.0",
            "source": "ths_ifind（必填：权威来源名称）",
            "source_url": "可选",
            "as_of_date": "YYYY-MM-DD（权威数据日期，不得晚于今天）",
            "sectors": {"<板块名称>": {"sector_code": code, "codes": ["600000", "..."]}},
        },
    }


def _today() -> str:
    return datetime.now(A.CN_TZ).date().isoformat()


def _dyn_status(ds: DynamicSector, today: str) -> Dict[str, Any]:
    if not ds.available:
        return {"status": "unavailable", "scannable": False, "freshness": "unknown",
                "reason": ds.error or "权威源无法提供成分股"}
    if ds.provenance is None or not ds.provenance.authoritative_reconciliation_date or not ds.constituents:
        return {"status": "pending", "scannable": False, "freshness": FreshnessLevel.NOT_RECONCILED.value,
                "reason": "尚未完成权威验证"}
    level = ds.provenance.get_freshness_level(today, max_fresh_days=FRESH_DAYS)
    if level == FreshnessLevel.FRESH:
        return {"status": "verified", "scannable": True, "freshness": level.value, "reason": None}
    return {"status": "stale", "scannable": False, "freshness": level.value,
            "reason": f"权威对账日期 {ds.provenance.authoritative_reconciliation_date} 已超过 {FRESH_DAYS} 天，请重新验证"}


class SectorService:
    def __init__(self, db: Database, presets_path: Path, evidence_dir: Path, repo_root: Path, auto_source=None):
        self.db = db
        self.auto_source = auto_source      # THSWebSectorSource 或 None（只走手工快照）
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        raw = json.loads(Path(presets_path).read_text(encoding="utf-8"))
        self.presets = []
        for s in raw["sectors"]:
            prov = None
            if s.get("provenance_file"):
                p = repo_root / s["provenance_file"]
                if p.exists():
                    prov = SectorProvenance.from_dict(json.loads(p.read_text(encoding="utf-8")))
            self.presets.append({**s, "_prov": prov})

    # ------------------------------------------------------------ 读取
    def list(self, user_id: str, today: Optional[str] = None) -> Dict[str, Any]:
        today = today or _today()
        presets = []
        for s in self.presets:
            prov: Optional[SectorProvenance] = s["_prov"]
            level = prov.get_freshness_level(today) if prov else FreshnessLevel.UNKNOWN
            presets.append({
                "type": "preset", "id": f"preset:{s['name']}", "name": s["name"], "sectorCode": s["sector_code"] or None,
                "codes": s["codes"], "count": len(s["codes"]), "origin": s["origin"], "scannable": True,
                "membership": {"freshness": level.value,
                               "authoritativeReconciliationDate": prov.authoritative_reconciliation_date if prov else None,
                               "note": "成分股来自 V5 页面预置列表" + ("（同花顺 iFinD 2026-10-02 获取）"
                                                                 if s["origin"].startswith("ths") else "（手工维护）")
                               + ("，尚未经权威对账" if level in (FreshnessLevel.NOT_RECONCILED, FreshnessLevel.UNKNOWN) else "")},
            })
        custom = [{"type": "custom", "id": f"custom:{r['name']}", "name": r["name"], "codes": json.loads(r["codes"]),
                   "count": len(json.loads(r["codes"])), "updatedAt": r["updated_at"], "scannable": True}
                  for r in self.db.query("SELECT name, codes, updated_at FROM custom_sectors WHERE user_id=? ORDER BY name",
                                         (user_id,))]
        dynamic = []
        for r in self.db.query("SELECT sector_code, data, added_at, updated_at FROM dynamic_sectors WHERE user_id=? "
                               "ORDER BY added_at, sector_code", (user_id,)):
            ds = DynamicSector.from_dict(json.loads(r["data"]))
            st = _dyn_status(ds, today)
            item = {"type": "ths_dynamic", "id": f"ths:{ds.sector_code}", "sectorCode": ds.sector_code,
                    "name": ds.sector_name or None, "codes": ds.constituents if st["scannable"] else [],
                    "count": ds.constituent_count, "addedAt": r["added_at"], "updatedAt": r["updated_at"],
                    "provenance": ds.provenance.to_dict() if ds.provenance else None, **st}
            if not st["scannable"]:
                item["verification"] = verification_instruction(ds.sector_code)
            dynamic.append(item)
        tomb = lambda t, k: [{k: r[0], "deletedAt": r[1]} for r in self.db.query(  # noqa: E731
            f"SELECT {'name' if t == 'custom' else 'sector_code'}, deleted_at FROM "
            f"{'custom_sector_tombstones' if t == 'custom' else 'dynamic_sector_tombstones'} WHERE user_id=?", (user_id,))]
        return {"version": self.db.version(user_id), "today": today, "freshDays": FRESH_DAYS,
                "presets": presets, "custom": custom, "dynamic": dynamic,
                "tombstones": {"custom": tomb("custom", "name"), "dynamic": tomb("dynamic", "sectorCode")}}

    def resolve_scope(self, user_id: str, ids: List[str]) -> List[Dict[str, Any]]:
        """扫描范围解析；任何不可扫描的动态板块 → 409（禁止扫描）

        配置了自动获取时，扫描前先把今天还没拉取过的动态板块重新拉一次；
        拉取失败时仍按原状态判断（7 天内的旧成分股可以扫描，否则 409）。"""
        if self.auto_source is not None:
            today = _today()
            for d in self.list(user_id, today)["dynamic"]:
                if d["id"] in ids and (d["provenance"] or {}).get("authoritative_reconciliation_date") != today:
                    self.auto_verify(user_id, d["sectorCode"], today)
        listing = self.list(user_id)
        by_id = {s["id"]: s for s in listing["presets"] + listing["custom"] + listing["dynamic"]}
        out = []
        for sid in ids:
            s = by_id.get(sid)
            if s is None:
                raise NotFound(f"板块不存在：{sid}")
            if not s["scannable"]:
                raise Conflict(f"板块 {s.get('name') or s.get('sectorCode')} 当前状态为 {s['status']}，禁止扫描：{s['reason']}",
                               code="sector_not_scannable",
                               details={"sector": sid, "status": s["status"], "verification": s.get("verification")})
            out.append({"id": sid, "name": s.get("name") or s.get("sectorCode"), "codes": s["codes"]})
        return out

    # ------------------------------------------------------------ 自定义板块
    def put_custom(self, user_id: str, name: str, codes: List[str], at: Optional[str] = None) -> Dict[str, Any]:
        if not isinstance(name, str) or not _NAME_RE.match(name.strip()):
            raise InvalidRequest("板块名称需为 1~20 个字符（中文、字母、数字及常用符号）")
        name = name.strip()
        if any(p["name"] == name for p in self.presets):
            raise Conflict(f"“{name}”与预置板块重名")
        if not isinstance(codes, list) or not codes or len(codes) > MAX_CUSTOM_CODES:
            raise InvalidRequest(f"成分股需为 1~{MAX_CUSTOM_CODES} 个股票代码")
        seen, clean, bad = set(), [], []
        for c in codes:
            try:
                valid_code(c)
            except Exception:
                bad.append(c)
                continue
            if c not in seen:
                seen.add(c)
                clean.append(c)
        if bad:
            raise InvalidRequest("存在无效股票代码", details={"invalid": bad[:50]})
        at = norm_time(at)
        with self.db.tx() as c:
            t = c.execute("SELECT deleted_at FROM custom_sector_tombstones WHERE user_id=? AND name=?", (user_id, name)).fetchone()
            if t and t["deleted_at"] >= at:
                raise Conflict("该板块已在其他设备上被删除（之后的修改不会复活它）", code="deleted_later")
            c.execute("INSERT INTO custom_sectors(user_id, name, codes, updated_at) VALUES(?,?,?,?) "
                      "ON CONFLICT(user_id, name) DO UPDATE SET codes=excluded.codes, updated_at=excluded.updated_at "
                      "WHERE excluded.updated_at >= custom_sectors.updated_at", (user_id, name, dumps(clean), at))
            c.execute("DELETE FROM custom_sector_tombstones WHERE user_id=? AND name=?", (user_id, name))
            v = Database.bump(c, user_id)
        return {"version": v, "name": name, "codes": clean}

    def delete_custom(self, user_id: str, name: str, at: Optional[str] = None) -> Dict[str, Any]:
        at = norm_time(at)
        with self.db.tx() as c:
            cur = c.execute("SELECT updated_at FROM custom_sectors WHERE user_id=? AND name=?", (user_id, name)).fetchone()
            if cur is not None and cur["updated_at"] > at:
                return {"version": self.db.version(user_id, c), "status": "ignored", "reason": "updated_later"}
            c.execute("DELETE FROM custom_sectors WHERE user_id=? AND name=?", (user_id, name))
            c.execute("INSERT INTO custom_sector_tombstones(user_id, name, deleted_at) VALUES(?,?,?) ON CONFLICT(user_id, name) "
                      "DO UPDATE SET deleted_at = MAX(deleted_at, excluded.deleted_at)", (user_id, name, at))
            v = Database.bump(c, user_id)
        return {"version": v, "status": "applied"}

    # ------------------------------------------------------------ 同花顺动态板块
    def add_dynamic(self, user_id: str, code: str, at: Optional[str] = None) -> Dict[str, Any]:
        if not validate_ths_sector_code(code if isinstance(code, str) else ""):
            raise InvalidRequest("同花顺板块指数代码应为 6 位数字（如 886032）")
        at = norm_time(at)
        with self.db.tx() as c:
            t = c.execute("SELECT deleted_at FROM dynamic_sector_tombstones WHERE user_id=? AND sector_code=?",
                          (user_id, code)).fetchone()
            if t and t["deleted_at"] >= at:
                raise Conflict("该动态板块已在其他设备上被删除（之后的操作不会复活它）", code="deleted_later")
            cur = c.execute("SELECT 1 FROM dynamic_sectors WHERE user_id=? AND sector_code=?", (user_id, code)).fetchone()
            if cur is None:   # 重复添加不重置已有验证结果（不 dead-end，也不丢证据）
                ds = DynamicSector(sector_code=code, available=True)
                c.execute("INSERT INTO dynamic_sectors(user_id, sector_code, data, added_at, updated_at) VALUES(?,?,?,?,?)",
                          (user_id, code, dumps(ds.to_dict()), at, at))
            c.execute("DELETE FROM dynamic_sector_tombstones WHERE user_id=? AND sector_code=?", (user_id, code))
            v = Database.bump(c, user_id)
        item = next(d for d in self.list(user_id)["dynamic"] if d["sectorCode"] == code)
        out: Dict[str, Any] = {"version": v, "sector": item}
        if self.auto_source is not None and item["status"] != "verified":
            res = self.auto_verify(user_id, code)
            out["autoFetch"] = res["autoFetch"]
            if res["autoFetch"]["ok"]:
                out["version"], out["sector"] = res["version"], res["sector"]
        return out

    def auto_verify(self, user_id: str, code: str, today: Optional[str] = None) -> Dict[str, Any]:
        """从同花顺网页接口拉取成分股并按快照规则记录；失败返回原因，不改动已有数据"""
        today = today or _today()
        try:
            m = self.auto_source.fetch(code)
        except SectorSourceError as e:
            return {"autoFetch": {"ok": False, "source": THS_WEB_SOURCE, "error": str(e)}}
        snapshot = {
            "schema_version": "1.0", "source": THS_WEB_SOURCE, "source_url": m.source_url, "as_of_date": today,
            "snapshot_id": f"{THS_WEB_SOURCE}:{code}:{m.fetched_at}",
            "sectors": {m.name or f"同花顺板块{code}": {"sector_code": code, "codes": m.codes}},
        }
        try:
            res = self.verify(user_id, code, snapshot, today)
        except (InvalidRequest, NotFound) as e:
            return {"autoFetch": {"ok": False, "source": THS_WEB_SOURCE, "error": e.message}}
        res["autoFetch"] = {"ok": True, "source": THS_WEB_SOURCE, "fetchedAt": m.fetched_at,
                            "count": len(m.codes), "skipped": m.skipped[:50]}
        return res

    def delete_dynamic(self, user_id: str, code: str, at: Optional[str] = None) -> Dict[str, Any]:
        at = norm_time(at)
        with self.db.tx() as c:
            cur = c.execute("SELECT added_at FROM dynamic_sectors WHERE user_id=? AND sector_code=?", (user_id, code)).fetchone()
            if cur is not None and cur["added_at"] > at:
                return {"version": self.db.version(user_id, c), "status": "ignored", "reason": "added_later"}
            c.execute("DELETE FROM dynamic_sectors WHERE user_id=? AND sector_code=?", (user_id, code))
            c.execute("INSERT INTO dynamic_sector_tombstones(user_id, sector_code, deleted_at) VALUES(?,?,?) "
                      "ON CONFLICT(user_id, sector_code) DO UPDATE SET deleted_at = MAX(deleted_at, excluded.deleted_at)",
                      (user_id, code, at))
            v = Database.bump(c, user_id)
        return {"version": v, "status": "applied"}

    def verify(self, user_id: str, code: str, snapshot: Optional[Dict[str, Any]], today: Optional[str] = None) -> Dict[str, Any]:
        rows = self.db.query("SELECT data FROM dynamic_sectors WHERE user_id=? AND sector_code=?", (user_id, code))
        if not rows:
            raise NotFound(f"动态板块 {code} 不存在，请先添加")
        if snapshot is None:
            if self.auto_source is not None:
                res = self.auto_verify(user_id, code, today)
                if res["autoFetch"]["ok"]:
                    return res
                return {**verification_instruction(code), "autoFetch": res["autoFetch"]}
            return verification_instruction(code)
        today = today or _today()
        raw = json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8")
        sha = hashlib.sha256(raw).hexdigest()
        with tempfile.NamedTemporaryFile("wb", suffix=".json", dir=self.evidence_dir, delete=False) as f:
            f.write(raw)
            tmp = Path(f.name)
        try:
            snap = load_authoritative_snapshot(str(tmp))   # 与 FIR-15 CI 门禁同一校验
        except AuthoritativeSnapshotError as e:
            tmp.unlink(missing_ok=True)
            raise InvalidRequest(f"权威快照不合法：{e}", code="invalid_snapshot")
        if snap["as_of_date"] > today:
            tmp.unlink(missing_ok=True)
            raise InvalidRequest(f"快照日期 {snap['as_of_date']} 晚于今天 {today}", code="invalid_snapshot")
        matches = [(n, s) for n, s in snap["sectors"].items() if s["sector_code"] == code]
        if len(matches) != 1:
            tmp.unlink(missing_ok=True)
            raise InvalidRequest(f"快照中必须恰好有一个 sector_code = {code} 的板块（实际 {len(matches)} 个）",
                                 code="invalid_snapshot")
        name, payload = matches[0]
        bad = []
        for c in payload["codes"]:
            try:
                valid_code(c)
            except Exception:
                bad.append(c)
        if bad:
            tmp.unlink(missing_ok=True)
            raise InvalidRequest("快照含无效股票代码", code="invalid_snapshot", details={"invalid": bad[:50]})
        codes = list(dict.fromkeys(payload["codes"]))
        evidence = self.evidence_dir / f"{code}_{snap['as_of_date']}_{sha[:16]}.json"
        tmp.replace(evidence)
        ds = DynamicSector.from_dict(json.loads(rows[0]["data"]))
        rec = reconcile_codes(name, ds.constituents, codes, source=snap["source"])
        ds.sector_name = name
        ds.update_constituents(codes, source=snap["source"])
        method = (ReconciliationMethod.THS_WEB if snap["source"] == THS_WEB_SOURCE
                  else ReconciliationMethod.THS_IFIND if "ifind" in snap["source"].lower() else ReconciliationMethod.MANUAL)
        ds.provenance = SectorProvenance(
            sector_name=name, sector_code=code, source=snap["source"], source_url=snap["source_url"],
            fetched_at=fmt(utcnow()), local_refresh_date=today,
            authoritative_reconciliation_date=snap["as_of_date"], reconciliation_method=method,
            reconciliation_result=f"matched={len(rec.matched)}, removed={len(rec.only_in_local)}, added={len(rec.only_in_source)}",
            total_constituents=len(codes), verified_count=len(codes),
            notes=f"snapshot_id={snap['snapshot_id']}; evidence={evidence.name}; sha256={sha}")
        with self.db.tx() as c:
            c.execute("UPDATE dynamic_sectors SET data=?, updated_at=? WHERE user_id=? AND sector_code=?",
                      (dumps(ds.to_dict()), fmt(utcnow()), user_id, code))
            v = Database.bump(c, user_id)
        item = next(d for d in self.list(user_id, today)["dynamic"] if d["sectorCode"] == code)
        return {"status": "recorded", "version": v, "sector": item,
                "reconciliation": {"matched": len(rec.matched), "removed": rec.only_in_local[:100],
                                   "added": rec.only_in_source[:100]},
                "evidence": {"file": evidence.name, "sha256": sha, "snapshot_id": snap["snapshot_id"]}}

    def export(self, user_id: str) -> Dict[str, Any]:
        lst = self.list(user_id)
        dyn_rows = self.db.query("SELECT data FROM dynamic_sectors WHERE user_id=?", (user_id,))
        return {
            "customSectors": [{"name": s["name"], "codes": s["codes"], "updatedAt": s["updatedAt"]} for s in lst["custom"]],
            "dynamicSectors": [json.loads(r["data"]) for r in dyn_rows],
            "dynamicSectorTombstones": lst["tombstones"]["dynamic"],
        }
