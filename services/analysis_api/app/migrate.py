"""
自选股 / tombstones / 板块配置的非破坏性迁移（FIR-31 §7）

用法：
  python -m app.migrate --source ../../config/watchlist.json [--user owner] [--dry-run]

步骤：
  1. 读取源文件原始字节，计算 sha256；原样快照到 <DATA_DIR>/migrations/
  2. 迁移前用 SQLite backup API 备份数据库到 <DATA_DIR>/backups/（回滚 = 用备份文件替换）
  3. 把源文件转换为与线上相同的合并操作（add / remove / setting / 板块），按同一合并规则原子应用：
     - 源文件中同时出现在 watchlist 与 tombstones 的代码：tombstone 优先（与 V5 页面语义一致），记录在日志；
     - 数据库已有、源文件没有的条目 **保留**（不覆盖、不删除）；
     - 源 tombstone 只删除早于它加入的条目（与线上规则一致）；
  4. 逐项对账：源文件每只股票要么在库中，要么被更晚的 tombstone 阻止；每个源 tombstone 都在库中；
     迁移前库中的条目除被源 tombstone 合法删除外全部保留。任何一项不满足 → 回滚并报错。
  5. 写迁移日志（JSON）到 <DATA_DIR>/migrations/ 与数据库 migrations 表。重复执行幂等（opId 由源 sha 派生）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .db import Database, dumps
from .userstate import apply_ops, fmt, norm_time, snapshot, utcnow


class MigrationError(RuntimeError):
    pass


def _source_time(cfg: Dict[str, Any]) -> str:
    for key in ("exportTime",):
        if isinstance(cfg.get(key), str) and "T" in cfg[key]:
            return norm_time(cfg[key])
    meta = cfg.get("syncMeta") or {}
    if isinstance(meta.get("mergedAt"), str):
        return norm_time(meta["mergedAt"])
    if isinstance(cfg.get("exportDate"), str):
        return norm_time(cfg["exportDate"] + "T00:00:00Z")
    raise MigrationError("源文件缺少时间信息（exportTime / syncMeta.mergedAt / exportDate）")


def _custom_entries(raw) -> List[Dict[str, Any]]:
    if isinstance(raw, dict):
        return [{"name": k, "codes": v} for k, v in raw.items()]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict) and "name" in x]
    return []


def backup_db(db: Database, dest_dir: Path, tag: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"analysis_{tag}.sqlite3"
    with db._lock:
        out = sqlite3.connect(str(dest))
        db._conn.backup(out)
        out.close()
    return dest


def restore_db(db: Database, backup: Path) -> None:
    """用备份覆盖当前数据库内容（同一连接内，原子替换页面）"""
    with db._lock:
        src = sqlite3.connect(str(backup))
        src.backup(db._conn)
        src.close()


def migrate(db: Database, source: Path, data_dir: Path, sectors, user_id: str = "owner",
            dry_run: bool = False) -> Dict[str, Any]:
    raw = Path(source).read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    cfg = json.loads(raw.decode("utf-8"))
    if cfg.get("version") not in ("1.0", "1.1", "1.2", 3, "3"):
        raise MigrationError(f"不支持的源文件版本：{cfg.get('version')!r}")
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    mig_id = f"watchlist:{sha}"
    log: Dict[str, Any] = {"id": mig_id, "source": str(source), "source_sha256": sha, "started_at": fmt(utcnow()),
                           "user_id": user_id, "dry_run": dry_run, "skipped": [], "notes": []}
    already = db.query("SELECT applied_at FROM migrations WHERE id=?", (mig_id,))
    if already:
        log["notes"].append(f"同一源文件已于 {already[0]['applied_at']} 迁移过；本次重放（幂等）")

    src_time = _source_time(cfg)
    src_tombs = {}
    for t in cfg.get("tombstones") or []:
        code, at = t.get("code"), t.get("deletedAt")
        if isinstance(code, str) and isinstance(at, str):
            src_tombs[code] = max(src_tombs.get(code, ""), norm_time(at))
    src_codes = [c for c in dict.fromkeys(cfg.get("watchlist") or []) if isinstance(c, str)]
    ops: List[Dict[str, Any]] = []
    for code, at in sorted(src_tombs.items()):
        ops.append({"opId": f"mig-{sha[:16]}-rm-{code}", "op": "remove", "code": code, "at": at})
    for code in src_codes:
        if code in src_tombs:
            log["skipped"].append({"code": code, "reason": "源文件中同时存在 tombstone，按 V5 语义不复活"})
            continue
        ops.append({"opId": f"mig-{sha[:16]}-add-{code}", "op": "add", "code": code, "at": src_time})
    from .userstate import _SETTING_KEYS
    for k, v in (cfg.get("settings") or {}).items():
        if k in _SETTING_KEYS:
            ops.append({"opId": f"mig-{sha[:16]}-set-{k}", "op": "setting", "key": k, "value": v, "at": src_time})
        else:
            log["skipped"].append({"setting": k, "reason": "V6 不支持的设置项（原值保留在源快照中）"})

    before = snapshot(db, user_id)
    log["before"] = {"watchlist": len(before["watchlist"]), "tombstones": len(before["tombstones"]),
                     "version": before["version"]}
    log["source_counts"] = {"watchlist": len(src_codes), "tombstones": len(src_tombs),
                            "customSectors": len(_custom_entries(cfg.get("customSectors"))),
                            "dynamicSectors": len(cfg.get("dynamicSectors") or []),
                            "dynamicSectorTombstones": len(cfg.get("dynamicSectorTombstones") or [])}
    if dry_run:
        log["planned_ops"] = ops
        return log

    mig_dir = Path(data_dir) / "migrations"
    mig_dir.mkdir(parents=True, exist_ok=True)
    snap_path = mig_dir / f"{ts}_source_{sha[:16]}.json"
    snap_path.write_bytes(raw)
    backup = backup_db(db, Path(data_dir) / "backups", ts)
    log["source_snapshot"] = snap_path.name
    log["db_backup"] = str(backup)

    try:
        if ops:
            for i in range(0, len(ops), 400):
                res = apply_ops(db, user_id, ops[i:i + 400])
                log.setdefault("op_results", []).extend(res["results"])
        # 板块：自定义（遵守 tombstone）、动态（保留原 provenance）、动态 tombstone
        for entry in _custom_entries(cfg.get("customSectors")):
            try:
                sectors.put_custom(user_id, entry["name"], entry["codes"], entry.get("updatedAt") or src_time)
            except Exception as e:
                log["skipped"].append({"customSector": entry["name"], "reason": str(e)})
        for t in cfg.get("dynamicSectorTombstones") or []:
            if isinstance(t, dict) and t.get("sectorCode"):
                sectors.delete_dynamic(user_id, str(t["sectorCode"]), t.get("deletedAt"))
        for d in cfg.get("dynamicSectors") or []:
            _import_dynamic(db, sectors, user_id, d, src_time, log)
        _reconcile(db, user_id, before, src_codes, src_tombs, src_time, log)
    except Exception as e:
        restore_db(db, backup)            # 自动回滚到迁移前
        log["result"] = "failed_rolled_back"
        log["error"] = str(e)
        (mig_dir / f"{ts}_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
        raise
    after = snapshot(db, user_id)
    log["after"] = {"watchlist": len(after["watchlist"]), "tombstones": len(after["tombstones"]),
                    "version": after["version"]}
    log["finished_at"] = fmt(utcnow())
    log["result"] = "ok"
    (mig_dir / f"{ts}_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    with db.tx() as c:
        c.execute("INSERT OR REPLACE INTO migrations(id, applied_at, source_sha256, log) VALUES(?,?,?,?)",
                  (mig_id, log["finished_at"], sha, dumps(log)))
    return log


def _import_dynamic(db, sectors, user_id, d, src_time, log):
    from core.sector_validation.dynamic_sector import DynamicSector
    try:
        ds = DynamicSector.from_dict(d)
    except Exception as e:
        log["skipped"].append({"dynamicSector": d.get("sectorCode"), "reason": f"格式不合法：{e}"})
        return
    at = norm_time(d.get("updatedAt")) if d.get("updatedAt") else src_time
    with db.tx() as c:
        t = c.execute("SELECT deleted_at FROM dynamic_sector_tombstones WHERE user_id=? AND sector_code=?",
                      (user_id, ds.sector_code)).fetchone()
        if t and t["deleted_at"] >= at:
            log["skipped"].append({"dynamicSector": ds.sector_code, "reason": "已被更晚的 tombstone 删除"})
            return
        cur = c.execute("SELECT 1 FROM dynamic_sectors WHERE user_id=? AND sector_code=?", (user_id, ds.sector_code)).fetchone()
        if cur:
            log["skipped"].append({"dynamicSector": ds.sector_code, "reason": "库中已存在，保留库中版本"})
            return
        c.execute("INSERT INTO dynamic_sectors(user_id, sector_code, data, added_at, updated_at) VALUES(?,?,?,?,?)",
                  (user_id, ds.sector_code, dumps(ds.to_dict()), at, at))
        Database.bump(c, user_id)


def _reconcile(db, user_id, before, src_codes, src_tombs, src_time, log):
    after = snapshot(db, user_id)
    have = {w["code"]: w["addedAt"] for w in after["watchlist"]}
    tombs = {t["code"]: t["deletedAt"] for t in after["tombstones"]}
    problems = []
    for code in src_codes:
        if code in src_tombs:
            if code in have and have[code] <= src_tombs[code]:
                problems.append(f"{code} 应被源 tombstone 阻止却仍在自选股中")
            continue
        if code not in have and not (code in tombs and tombs[code] >= src_time):
            problems.append(f"{code} 丢失（既不在自选股中，也没有更晚的删除记录）")
    for code, at in src_tombs.items():
        if code not in tombs and code not in have:
            problems.append(f"tombstone {code} 丢失")
        if code in tombs and tombs[code] < at:
            problems.append(f"tombstone {code} 的删除时间被改早")
    for w in before["watchlist"]:
        if w["code"] not in have:
            t = src_tombs.get(w["code"])
            if not (t and t >= w["addedAt"]):
                problems.append(f"迁移前已有的 {w['code']} 被意外删除")
    log["reconciliation"] = {"checked_source_codes": len(src_codes), "checked_tombstones": len(src_tombs),
                             "checked_existing": len(before["watchlist"]), "problems": problems}
    if problems:
        raise MigrationError("迁移对账失败：" + "；".join(problems[:10]))


def main(argv: Optional[List[str]] = None) -> int:
    from .config import REPO_ROOT, Settings
    from .sectors import SectorService
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", type=Path, required=True)
    ap.add_argument("--user", default="owner")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    s = Settings.from_env()
    db = Database(s.data_dir / "analysis.sqlite3")
    sectors = SectorService(db, s.preset_sectors_path, s.data_dir / "sector_evidence", REPO_ROOT)
    try:
        log = migrate(db, args.source, s.data_dir, sectors, args.user, args.dry_run)
    except MigrationError as e:
        print(f"迁移失败（数据库未变更或已可用备份恢复）：{e}", file=sys.stderr)
        return 2
    print(json.dumps({k: v for k, v in log.items() if k not in ("op_results", "planned_ops")}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
