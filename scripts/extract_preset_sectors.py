#!/usr/bin/env python3
"""
从 V5 生产页面 kline_analyzer.html 原样提取 PRESET_SECTORS（17 个预置板块）→ config/preset_sectors.json

V6 由分析服务提供板块列表（FIR-31 §6C），成分股必须与旧页面逐只一致；本脚本可重复运行，
CI 用 --check 校验输出与页面一致（.github/workflows/ci.yml analysis-api 任务）。
来源说明：7 个板块（创新药/CRO概念/细胞免疫治疗/AI视频/PET铜箔/MLCC概念/PCB概念）为同花顺 iFinD 2026-10-02
获取（sector_data/new_sectors.js），其余 10 个为页面手工维护；权威对账状态见 sector_data/provenance/*.json。
"""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "kline_analyzer.html"
OUT = ROOT / "config" / "preset_sectors.json"
IFIND = {"创新药": "886015", "CRO概念": "885927", "细胞免疫治疗": "885769", "AI视频": "886068",
         "PET铜箔": "886020", "MLCC概念": "886112", "PCB概念": "885959"}


def extract(html: str):
    m = re.search(r"const PRESET_SECTORS = \{(.*?)\n\};", html, re.S)
    if not m:
        raise SystemExit("找不到 PRESET_SECTORS")
    sectors = []
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        mm = re.match(r"'([^']+)'\s*:\s*\[(.*)\]\s*,?$", line)
        if not mm:
            raise SystemExit(f"无法解析：{line[:80]}")
        codes = re.findall(r"'(\d{6})'", mm.group(2))
        if len(codes) != mm.group(2).count("'") // 2:
            raise SystemExit(f"含非 6 位数字代码：{mm.group(1)}")
        sectors.append((mm.group(1), codes))
    return sectors


def build():
    raw = SRC.read_bytes()
    sectors = extract(raw.decode("utf-8"))
    out = {"schema_version": 1,
           "source_file": "kline_analyzer.html",
           "source_sha256": hashlib.sha256(raw).hexdigest(),
           "sectors": []}
    for name, codes in sectors:
        prov_path = ROOT / "sector_data" / "provenance" / f"{name}_provenance.json"
        out["sectors"].append({
            "name": name,
            "sector_code": IFIND.get(name, ""),
            "origin": "ths_ifind_2026-10-02" if name in IFIND else "manual_page_preset",
            "codes": codes,
            "provenance_file": str(prov_path.relative_to(ROOT)) if prov_path.exists() else None,
        })
    return out


if __name__ == "__main__":
    data = build()
    text = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text(encoding="utf-8") == text else 1)
    OUT.write_text(text, encoding="utf-8")
    print(f"{len(data['sectors'])} 个预置板块 → {OUT.relative_to(ROOT)}")
