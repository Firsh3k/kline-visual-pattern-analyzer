#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 apps/web-v5（V5 界面 · V6 引擎）打包成一个自包含的 HTML 文件，用于放到其他网站托管（如妙搭 / 豆包应用）。

    python3 scripts/build_v5_single.py                       # 写到 dist/kline-v5.html
    python3 scripts/build_v5_single.py --engine https://kline.example.com --out /tmp/x.html

- 把 css/app.css 内联为 <style>，把 js/*.js 按 index.html 中的顺序内联为 <script>
- --engine：写入默认的分析引擎地址（<meta name="kline-engine">）；不写时网页会让用户在“引擎地址”里填写
- 引擎（V6 BFF）的 CORS_ALLOWED_ORIGINS 必须包含托管该文件的网页来源
只用 Python 标准库。
"""
import argparse
import html
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "apps" / "web-v5"
DEFAULT_OUT = ROOT / "dist" / "kline-v5.html"

LINK_RE = re.compile(r'<link rel="stylesheet" href="([^"]+)">')
SCRIPT_RE = re.compile(r'<script src="([^"]+)" defer></script>')
META_RE = re.compile(r'<meta name="kline-engine" content="[^"]*">')


def normalize_engine(url: str) -> str:
    u = urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ValueError(f"--engine 必须是 http:// 或 https:// 开头的网址：{url!r}")
    return f"{u.scheme}://{u.netloc}{u.path.rstrip('/')}"


def _read(rel: str) -> str:
    p = (SRC / rel).resolve()
    if SRC.resolve() not in p.parents:
        raise ValueError(f"引用了 apps/web-v5 之外的文件：{rel}")
    return p.read_text(encoding="utf-8")


def build(engine: str = "") -> str:
    page = (SRC / "index.html").read_text(encoding="utf-8")
    scripts = SCRIPT_RE.findall(page)
    if not scripts:
        raise ValueError("index.html 里没有找到 <script src=... defer>")

    def css(m):
        body = _read(m.group(1)).replace("</style", "<\\/style")
        return f"<style>\n{body}</style>"

    page = LINK_RE.sub(css, page)
    # 内联后脚本不再 defer：统一放到 </body> 前按原顺序执行（此时 DOM 已就绪，与 defer 等价）
    page = SCRIPT_RE.sub("", page)
    page = re.sub(r"\n{3,}", "\n\n", page)
    parts = []
    for name in scripts:
        js = _read(name).replace("</script", "<\\/script")
        parts.append("<script>\n/* " + name + " */\n" + js + "</script>")
    inline = "\n".join(parts)
    if "</body>" not in page:
        raise ValueError("index.html 缺少 </body>")
    page = page.replace("</body>", inline + "\n</body>", 1)
    if not META_RE.search(page):
        raise ValueError('index.html 缺少 <meta name="kline-engine">')
    tag = f'<meta name="kline-engine" content="{html.escape(engine, quote=True)}">'
    page = META_RE.sub(lambda _m: tag, page, count=1)
    return page


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="打包 V5 界面（V6 引擎）为单个 HTML 文件")
    ap.add_argument("--engine", default="", help="默认分析引擎（V6 BFF）地址，如 https://kline.example.com")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"输出文件（默认 {DEFAULT_OUT.relative_to(ROOT)}）")
    a = ap.parse_args(argv)
    try:
        engine = normalize_engine(a.engine) if a.engine else ""
        page = build(engine)
    except (ValueError, OSError) as e:
        print(f"打包失败：{e}", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    print(f"已生成 {out}（{len(page.encode('utf-8')) // 1024} KB）" + (f"，默认引擎 {engine}" if engine else "，未写入默认引擎"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
