#!/usr/bin/env python3
"""
每日快照：在任何能上网的机器上（GitHub Actions、豆包的服务器、你的电脑）跑一遍 V6 分析，
输出静态网页数据和一份文字报告。不需要常驻服务器。

做法：在本进程里启动 V6 Python 分析服务（与线上同一套代码、同一个 core），依次调用它的接口，
把响应原样存成 JSON，静态网页（apps/web + js/api-static.js）直接读取这些文件。

    python pipeline/daily_snapshot.py                       # 读 config/daily_snapshot.json
    python pipeline/daily_snapshot.py --codes 002436 600519 --ths 886032
    python pipeline/daily_snapshot.py --site dist --report report.md   # 生成完整静态网站（网页 + 数据）

输出（--out，默认 dist/data）：
    snapshot.json            生成时间、扫描范围清单、失败清单
    analyses/<代码>.json     POST /api/v1/analyses 的响应（个股页）
    scans/<n>.json           每个扫描范围的结果（GET /api/v1/scans/<id>/results，未加过滤）
    market.json calibration.json meta_patterns.json version.json watchlist.json sectors.json
报告（--report，默认打印到终端）：Markdown 文字版，适合直接贴到聊天里看。

需要：pip install -r services/analysis_api/requirements-dev.txt
"""
from __future__ import annotations

import argparse
import json
import re
import secrets
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "analysis_api"
for p in (str(ROOT), str(SERVICE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi.testclient import TestClient  # noqa: E402

from app.config import Settings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.providers import LiveProvider  # noqa: E402
from app.ths_web import THSWebSectorSource  # noqa: E402
from core import analysis as A  # noqa: E402

DEFAULT_CONFIG = ROOT / "config" / "daily_snapshot.json"
USER = "owner"
STAGE_TEXT = {"bull": {"forming": "构建中", "pre_break": "突破前夜", "breakout_day": "突破当日", "post_break": "已突破",
                      "retest": "回踩确认", "failed_break": "突破失败"},
              "bear": {"forming": "构建中", "pre_break": "跌破前夜", "breakout_day": "跌破当日", "post_break": "已跌破",
                       "retest": "反抽确认", "failed_break": "跌破失败"}}
DISCLAIMER = "形态分析是技术面参考，分数是算法信号强度而不是胜率，不构成投资建议。"


class CachingLiveProvider(LiveProvider):
    """同一次运行里每只股票只拉一次行情（扫描和个股页共用）"""

    def __init__(self):
        self._cache: Dict[Any, Any] = {}

    def daily(self, code, adjustment):
        key = (code, adjustment)
        if key not in self._cache:
            self._cache[key] = super().daily(code, adjustment)
        return self._cache[key]

    def index_daily(self):
        if "index" not in self._cache:
            self._cache["index"] = super().index_daily()
        return self._cache["index"]


def load_config(path: Path, codes: List[str], ths: List[str]) -> Dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    wl = cfg.get("watchlist", [])
    if isinstance(wl, str):   # 指向自选股文件
        wl = json.loads((ROOT / wl).read_text(encoding="utf-8")).get("watchlist", [])
    return {"watchlist": list(dict.fromkeys(codes or wl)),
            "ths_sectors": list(dict.fromkeys(ths or cfg.get("ths_sectors", []))),
            "preset_sectors": cfg.get("preset_sectors", [])}


def dump(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def run_scan(c: TestClient, scope: Dict[str, Any], timeout: float = 1800) -> Dict[str, Any]:
    r = c.post("/api/v1/scans", json={"scope": scope})
    if r.status_code != 202:
        return {"error": r.json().get("error", {}).get("message", r.text)}
    job_id = r.json()["job"]["jobId"]
    t0 = time.time()
    while time.time() - t0 < timeout:
        job = c.get(f"/api/v1/scans/{job_id}").json()["job"]
        if job["status"] not in ("queued", "running"):
            break
        time.sleep(0.5)
    res = c.get(f"/api/v1/scans/{job_id}/results").json()
    return {"job": job, "results": res}


def build(cfg: Dict[str, Any], out: Path, log=print, provider=None, sector_source=None) -> Dict[str, Any]:
    tmp = Path(tempfile.mkdtemp(prefix="kline-snapshot-"))
    settings = Settings(api_token=secrets.token_hex(32), data_dir=tmp, provider="live", ths_sector_source="web",
                        scan_concurrency=6, scan_max_codes=3000, per_code_timeout_s=60, environment="production")
    app = create_app(settings, provider=provider or CachingLiveProvider(),
                     sector_source=sector_source or THSWebSectorSource(timeout=15))
    snap: Dict[str, Any] = {"generated_at": datetime.now(A.CN_TZ).isoformat(timespec="seconds"),
                            "scopes": [], "sector_fetch": [], "failures": []}
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {settings.api_token}", "X-User-Id": USER})
        # 1. 同花顺板块：自动拉成分股
        for code in cfg["ths_sectors"]:
            r = c.post("/api/v1/sectors/dynamic", json={"sectorCode": code}).json()
            sec, af = r.get("sector") or {}, r.get("autoFetch") or {}
            snap["sector_fetch"].append({"sectorCode": code, "ok": bool(sec.get("scannable")), "name": sec.get("name"),
                                         "count": sec.get("count"), "error": af.get("error") or r.get("error", {}).get("message")})
            log(f"板块 {code}: {'%s %s 只' % (sec.get('name'), sec.get('count')) if sec.get('scannable') else '失败 ' + str(af.get('error'))}")
        # 2. 自选股
        if cfg["watchlist"]:
            ops = [{"opId": f"snap-{i:06d}", "op": "add", "code": code} for i, code in enumerate(cfg["watchlist"])]
            r = c.patch("/api/v1/watchlist", json={"ops": ops})
            if r.status_code >= 400:
                snap["failures"].append({"scope": "watchlist", "error": r.json().get("error", {}).get("message")})
        sectors = c.get("/api/v1/sectors").json()
        # 3. 扫描（不加过滤：网页端按需筛选）
        scopes = []
        if cfg["watchlist"]:
            scopes.append(("watchlist", "自选股", {"type": "watchlist"}))
        for d in sectors["dynamic"]:
            if d["scannable"]:
                scopes.append((d["id"], f"{d['name'] or d['sectorCode']}（同花顺 {d['sectorCode']}）",
                               {"type": "sectors", "ids": [d["id"]]}))
        for name in cfg["preset_sectors"]:
            scopes.append((f"preset:{name}", name, {"type": "sectors", "ids": [f"preset:{name}"]}))
        codes: List[str] = []
        for i, (sid, label, scope) in enumerate(scopes):
            log(f"扫描 {label} …")
            r = run_scan(c, scope)
            if "error" in r:
                snap["failures"].append({"scope": sid, "error": r["error"]})
                log(f"  失败：{r['error']}")
                continue
            dump(out / "scans" / f"{i}.json", r)
            res = r["results"]
            snap["scopes"].append({"id": sid, "label": label, "file": f"scans/{i}.json", "total": res["total"],
                                   "succeeded": len(res["items"]), "failed": len(res["failures"])})
            codes += [it["code"] for it in res["items"]]
            log(f"  {len(res['items'])} 只成功，{len(res['failures'])} 只失败")
        # 4. 个股页数据（行情已缓存，不重复拉取）
        for code in dict.fromkeys(codes):
            r = c.post("/api/v1/analyses", json={"code": code})
            if r.status_code == 200:
                dump(out / "analyses" / f"{code}.json", r.json())
        # 5. 其余只读接口
        for name, path in (("market", "/api/v1/market"), ("calibration", "/api/v1/calibration"),
                           ("meta_patterns", "/api/v1/meta/patterns"), ("version", "/api/v1/version"),
                           ("watchlist", "/api/v1/watchlist")):
            r = c.get(path)
            dump(out / f"{name}.json", r.json())
            if r.status_code != 200:
                snap["failures"].append({"scope": name, "error": r.json().get("error", {}).get("message")})
        dump(out / "sectors.json", sectors)
        app.state.scans.shutdown()
    snap["analysed"] = len(set(codes))
    dump(out / "snapshot.json", snap)
    return snap


def report(out: Path, snap: Dict[str, Any], recent_bars: int = 5, min_score: int = 60) -> str:
    """文字版：每个范围里最近 N 根 K 线内出现、分数达标的形态"""
    lines = [f"# K线形态每日扫描 {snap['generated_at'][:16].replace('T', ' ')}", ""]
    m = json.loads((out / "market.json").read_text(encoding="utf-8")).get("market_env")
    if m:
        lines += [f"**大盘（上证）**：{m['stage']}，{m['score']} 分。{m['advice']}", ""]
    for s in snap["sector_fetch"]:
        if not s["ok"]:
            lines.append(f"- ⚠️ 同花顺板块 {s['sectorCode']} 拉取失败：{s['error']}")
    for sc in snap["scopes"]:
        res = json.loads((out / sc["file"]).read_text(encoding="utf-8"))["results"]
        hits = []
        for it in res["items"]:
            for pid, p in it["patterns_latest"].items():
                if p["bars_since"] <= recent_bars and p["score"] >= min_score:
                    hits.append((p["score"], it, p))
        hits.sort(key=lambda x: -x[0])
        lines += [f"## {sc['label']}（{sc['succeeded']} 只，近 {recent_bars} 根K线内分数≥{min_score} 的形态 {len(hits)} 个）", ""]
        if hits:
            lines += ["| 股票 | 收盘 | 形态 | 方向 | 阶段 | 分数 | 关键日 |", "|---|---|---|---|---|---|---|"]
            for score, it, p in hits[:40]:
                lines.append(f"| {it['name']} {it['code']} | {it['close']:.2f}（{it['change_pct']:+.2f}%） | {p['pattern_name']} | "
                             f"{'看涨' if p['direction'] == 'bull' else '看跌'} | {STAGE_TEXT.get(p['direction'], {}).get(p['stage'], p['stage'])} | {score} | {p['last_key_date']} |")
            if len(hits) > 40:
                lines.append(f"| …还有 {len(hits) - 40} 个 | | | | | | |")
        else:
            lines.append("没有符合条件的形态。")
        if res["failures"]:
            lines.append(f"\n失败 {len(res['failures'])} 只：" + "，".join(f"{f['code']}（{f['kind']}）" for f in res["failures"][:20]))
        lines.append("")
    for f in snap["failures"]:
        lines.append(f"- ⚠️ {f['scope']}：{f['error']}")
    lines += ["", f"_{DISCLAIMER}_"]
    return "\n".join(lines)


def build_site(site: Path) -> None:
    """把 apps/web 复制到 site/，并把 API 客户端换成读取 data/ 的静态版"""
    web = ROOT / "apps" / "web"
    for item in web.iterdir():
        dst = site / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)
    index = site / "index.html"
    html, n = re.subn(r'<script src="js/api\.js" defer></script>', '<script src="js/api-static.js" defer></script>',
                      index.read_text(encoding="utf-8"))
    if n != 1:
        raise RuntimeError("index.html 里没有找到 js/api.js，无法切换到静态版")
    index.write_text(html, encoding="utf-8")
    (site / ".nojekyll").write_text("", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V6 每日快照：静态网页数据 + 文字报告")
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--codes", nargs="*", default=[], help="股票代码（覆盖配置里的自选股）")
    ap.add_argument("--ths", nargs="*", default=[], help="同花顺板块指数代码（覆盖配置）")
    ap.add_argument("--out", type=Path, help="只输出数据到此目录（默认 dist/data）")
    ap.add_argument("--site", type=Path, help="输出完整静态网站到此目录（网页 + data/）")
    ap.add_argument("--report", type=Path, help="报告写到文件（默认打印）")
    ap.add_argument("--recent-bars", type=int, default=5)
    ap.add_argument("--min-score", type=int, default=60)
    args = ap.parse_args(argv)
    cfg = load_config(args.config, args.codes, args.ths)
    if not cfg["watchlist"] and not cfg["ths_sectors"] and not cfg["preset_sectors"]:
        print("没有要扫描的股票或板块（检查 config/daily_snapshot.json 或传 --codes / --ths）")
        return 2
    log = (lambda *a: print(*a, file=sys.stderr)) if args.report is None else print
    out = args.site / "data" if args.site else (args.out or ROOT / "dist" / "data")
    snap = build(cfg, out, log=log)
    if args.site:
        build_site(args.site)
    text = report(out, snap, args.recent_bars, args.min_score)
    if args.report:
        args.report.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0 if snap["scopes"] else 1


if __name__ == "__main__":
    sys.exit(main())
