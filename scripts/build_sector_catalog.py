#!/usr/bin/env python3
"""
FIR-41 第 4 项：生成部署用的板块清单 v6/app-builder/sector_data/catalog.json。

    python3 scripts/build_sector_catalog.py            # 重新生成
    python3 scripts/build_sector_catalog.py --check    # CI：清单与来源不一致则 exit 1
    python3 scripts/build_sector_catalog.py --adopt-snapshot snap.json   # 核对流程第 1 步，见下

来源（都在仓库根 sector_data/）：
- 同花顺板块成分：new_sectors.js（NEW_SECTORS）
- 每个同花顺板块的溯源：provenance/<板块>_provenance.json（来源、数据日期、权威核对日期与结果）
- 人工样例：curated_samples.json（没有来源，永远不会显示已核对）

接入规则（“只接入有来源、有截止日的数据”）：同花顺板块必须有 provenance，且 source 非空、
local_refresh_date 是合法日期，否则生成失败。是否“已核对”不在这里判断——那与“今天”有关，
由服务端按 catalog 里的核对日期和 freshness_days 实时计算（规则与 core.sector_validation 一致，有跨运行时测试）。

核对流程（把板块从“未核对”变成“已核对”）：
1. 用 iFinD 等权威源导出带 source / as_of_date 的快照 JSON（格式见 core/sector_validation/pipeline.py
   load_authoritative_snapshot），执行 --adopt-snapshot：把快照里各板块的成分写回 new_sectors.js；
2. python3 scripts/refresh_sector_membership.py --reconcile-with-snapshot snap.json：与快照对账，
   写入 provenance 的权威核对日期与结果（matched / local_only / source_only）；
3. 重新执行本脚本生成 catalog.json，提 PR → QA → 部署。完全一致（local_only=0, source_only=0）
   且核对日期在 30 天内的板块才会显示“已核对”。
"""
import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.sector_validation.types import SectorProvenance  # noqa: E402

SECTOR_DIR = ROOT / "sector_data"
MEMBERSHIP = SECTOR_DIR / "new_sectors.js"
OUT = ROOT / "v6" / "app-builder" / "sector_data" / "catalog.json"

SCHEMA = "sector-catalog-1"
FRESH_DAYS, AGING_DAYS = 7, 30          # 与 SectorProvenance.get_freshness_level 默认值一致
CODE_RE = re.compile(r"^\d{6}$")
NEW_SECTORS_RE = re.compile(r"const NEW_SECTORS\s*=\s*(\{[\s\S]*?\});")
RESULT_RE = re.compile(r"matched=(\d+),\s*local_only=(\d+),\s*source_only=(\d+)")


class CatalogError(Exception):
    pass


def load_membership(path=MEMBERSHIP):
    m = NEW_SECTORS_RE.search(path.read_text(encoding="utf-8"))
    if not m:
        raise CatalogError(f"{path} 里找不到 NEW_SECTORS")
    # 与 refresh_sector_membership.load_sectors_from_js 相同的解析（允许 JS 的单引号与尾逗号）
    body = re.sub(r"'([^']*)'", r'"\1"', m.group(1))
    body = re.sub(r",\s*([}\]])", r"\1", body)
    return json.loads(body)


def _date(value, what):
    try:
        datetime.strptime(str(value), "%Y-%m-%d")
        return str(value)
    except (TypeError, ValueError):
        raise CatalogError(f"{what} 不是合法日期：{value!r}")


def _codes(codes, what):
    bad = [c for c in codes if not CODE_RE.match(str(c))]
    if bad:
        raise CatalogError(f"{what} 有非法代码：{bad[:5]}")
    if len(set(codes)) != len(codes):
        raise CatalogError(f"{what} 有重复代码")
    return [str(c) for c in codes]


def ths_entry(name, codes, prov_path):
    if not prov_path.exists():
        raise CatalogError(f"同花顺板块 {name} 没有 provenance（{prov_path.name}）：没有来源 / 截止日的数据不接入")
    prov = SectorProvenance.from_dict(json.loads(prov_path.read_text(encoding="utf-8")))
    if not prov.source.strip():
        raise CatalogError(f"同花顺板块 {name} 的 provenance 没有 source")
    as_of = _date(prov.local_refresh_date, f"{name} 的数据日期 local_refresh_date")
    if not prov.sector_code:
        raise CatalogError(f"同花顺板块 {name} 的 provenance 没有板块代码")
    reconciled = prov.authoritative_reconciliation_date
    reconciliation = None
    if reconciled:
        _date(reconciled, f"{name} 的权威核对日期")
        m = RESULT_RE.search(prov.reconciliation_result or "")
        if not m:
            raise CatalogError(f"{name} 有核对日期但核对结果无法解析：{prov.reconciliation_result!r}")
        reconciliation = {"matched": int(m.group(1)), "local_only": int(m.group(2)), "source_only": int(m.group(3))}
    return {
        "id": f"ths:{prov.sector_code}",
        "name": name,
        "kind": "ths_concept",
        "sector_code": prov.sector_code,
        "constituents": _codes(codes, f"板块 {name}"),
        "source": prov.source,
        "source_url": prov.source_url or None,
        "as_of": as_of,
        "fetched_at": prov.fetched_at or None,
        "reconciled_at": reconciled or None,
        "reconciliation_method": prov.reconciliation_method.value,
        "reconciliation": reconciliation,
        "note": None,
    }


def build(sector_dir=SECTOR_DIR):
    membership = load_membership(sector_dir / "new_sectors.js")
    sectors = []
    for name, codes in membership.items():
        sectors.append(ths_entry(name, codes, sector_dir / "provenance" / f"{name}_provenance.json"))
    samples = json.loads((sector_dir / "curated_samples.json").read_text(encoding="utf-8"))["samples"]
    ths_names = set(membership)
    for s in samples:
        if s["name"] in ths_names:
            raise CatalogError(f"人工样例 {s['name']} 与同花顺板块重名")
        sectors.append({
            "id": f"sample:{s['name']}",
            "name": s["name"],
            "kind": "curated_sample",
            "sector_code": None,
            "constituents": _codes(s["constituents"], f"样例 {s['name']}"),
            "source": None,
            "source_url": None,
            "as_of": None,
            "fetched_at": None,
            "reconciled_at": None,
            "reconciliation_method": "not_reconciled",
            "reconciliation": None,
            "note": s.get("note") or "人工挑选样例，无来源",
        })
    ids = [s["id"] for s in sectors]
    if len(set(ids)) != len(ids):
        raise CatalogError("板块 id 重复")
    return {
        "schema": SCHEMA,
        "generated_by": "scripts/build_sector_catalog.py",
        "sources": {
            "membership": "sector_data/new_sectors.js",
            "provenance": "sector_data/provenance/",
            "samples": "sector_data/curated_samples.json",
        },
        "freshness_days": {"fresh": FRESH_DAYS, "aging": AGING_DAYS},
        "sectors": sectors,
    }


def render(catalog):
    return json.dumps(catalog, ensure_ascii=False, indent=1) + "\n"


def adopt_snapshot(path, membership_path=MEMBERSHIP):
    """核对流程第 1 步：把权威快照里的成分写回 new_sectors.js（只改快照里有的板块）"""
    from core.sector_validation.pipeline import load_authoritative_snapshot
    snap = load_authoritative_snapshot(str(path))
    text = membership_path.read_text(encoding="utf-8")
    membership = load_membership(membership_path)
    changed = []
    for name, payload in snap["sectors"].items():
        codes = sorted(set(payload["codes"]))
        if membership.get(name) != codes:
            changed.append(f"{name}: {len(membership.get(name) or [])} → {len(codes)}")
        membership[name] = codes
    body = "{\n" + ",\n".join(f"    {json.dumps(k, ensure_ascii=False)}: {json.dumps(v)}" for k, v in membership.items()) + "\n}"
    text = NEW_SECTORS_RE.sub(lambda _m: f"const NEW_SECTORS = {body};", text, count=1)
    membership_path.write_text(text, encoding="utf-8")
    return snap, changed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="只检查 catalog.json 是否与来源一致")
    ap.add_argument("--adopt-snapshot", metavar="SNAPSHOT", help="把权威快照的成分写回 new_sectors.js（核对流程第 1 步）")
    ap.add_argument("--sector-dir", default=str(SECTOR_DIR), help="来源目录（默认仓库根 sector_data/；测试可指向临时副本）")
    ap.add_argument("--out", default=str(OUT), help="输出文件（默认 v6/app-builder/sector_data/catalog.json）")
    args = ap.parse_args(argv)
    sector_dir, out_path = Path(args.sector_dir), Path(args.out)
    if args.adopt_snapshot:
        snap, changed = adopt_snapshot(Path(args.adopt_snapshot), sector_dir / "new_sectors.js")
        print(f"已按快照（{snap['source']}，{snap['as_of_date']}）更新 new_sectors.js：" + ("；".join(changed) or "无变化"))
        print(f"下一步：python3 scripts/refresh_sector_membership.py --reconcile-with-snapshot {args.adopt_snapshot}，"
              "然后 python3 scripts/build_sector_catalog.py")
        return 0
    try:
        out = render(build(sector_dir))
    except CatalogError as e:
        print(f"生成失败：{e}", file=sys.stderr)
        return 2
    if args.check:
        current = out_path.read_text(encoding="utf-8") if out_path.exists() else ""
        if current != out:
            print("catalog.json 与 sector_data 来源不一致：请运行 python3 scripts/build_sector_catalog.py 重新生成", file=sys.stderr)
            return 1
        print("catalog.json 与来源一致")
        return 0
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(out, encoding="utf-8")
    cat = json.loads(out)
    print(f"已生成 {out_path}：{len(cat['sectors'])} 个板块")
    return 0


if __name__ == "__main__":
    sys.exit(main())
