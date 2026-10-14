"""
同花顺板块成分股：网页接口自动获取

输入 6 位板块指数代码（如 886032），从同花顺网页行情使用的板块成分排行接口拉取成分股：
  https://d.10jqka.com.cn/v2/blockrank/<代码>/199112/d1000.js
返回 JSONP：quotebridge_v2_blockrank_<代码>_199112_d1000({"block": {...}, "items": [{"5": "600519", "55": "贵州茅台", ...}]})

原则（与 FIR-15 / FIR-22 一致）：
- 只认同花顺这一个来源；拿不到就明确报错，绝不换成其他提供商的板块成分；
- 不猜测成分股：返回为空、格式不对、代码不合法一律报错；
- 这是非官方网页接口，同花顺可能调整或限制访问，失败时网页仍可走手工快照验证。
"""
from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from core import analysis as A

SOURCE = "ths_web"
URL_TEMPLATE = "https://d.10jqka.com.cn/v2/blockrank/{code}/199112/d1000.js"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/126.0 Safari/537.36",
    "Referer": "https://q.10jqka.com.cn/",
    "Accept": "*/*",
}
_JSONP_RE = re.compile(r"^[^(]*\((.*)\)\s*;?\s*$", re.S)


class SectorSourceError(RuntimeError):
    """自动获取失败（网络、被限制、格式变化、板块不存在）"""


@dataclass
class SectorMembers:
    sector_code: str
    name: Optional[str]
    codes: List[str]
    source_url: str
    fetched_at: str
    skipped: List[str] = field(default_factory=list)   # 无法识别交易所的代码（如 B 股），不参与扫描


def _http_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout, context=ssl.create_default_context()) as r:
        return r.read()


def parse_blockrank(code: str, raw: bytes) -> Dict[str, Any]:
    text = raw.decode("utf-8", errors="replace").strip()
    m = _JSONP_RE.match(text)
    if not m:
        raise SectorSourceError(f"同花顺返回的板块 {code} 数据格式无法识别（接口可能已调整或访问被限制）")
    try:
        data = json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise SectorSourceError(f"同花顺返回的板块 {code} 数据不是合法 JSON") from e
    if not isinstance(data, dict):
        raise SectorSourceError(f"同花顺返回的板块 {code} 数据格式无法识别")
    return data


class THSWebSectorSource:
    name = SOURCE

    def __init__(self, timeout: float = 6.0, http_get: Optional[Callable[[str, float], bytes]] = None,
                 clock: Optional[Callable[[], datetime]] = None):
        self.timeout = timeout
        self._get = http_get or _http_get
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def fetch(self, code: str) -> SectorMembers:
        url = URL_TEMPLATE.format(code=code)
        try:
            raw = self._get(url, self.timeout)
        except urllib.error.HTTPError as e:
            raise SectorSourceError(f"同花顺接口拒绝了板块 {code} 的请求（HTTP {e.code}），可能是代码不存在或访问被限制") from e
        except Exception as e:  # 网络 / TLS / 超时
            raise SectorSourceError(f"连接同花顺失败：{str(e)[:200]}") from e
        data = parse_blockrank(code, raw)
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise SectorSourceError(f"同花顺没有返回板块 {code} 的成分股（代码不存在，或不是板块指数代码）")
        codes: List[str] = []
        skipped: List[str] = []
        for it in items:
            c = str(it.get("5", "")).strip() if isinstance(it, dict) else ""
            if not re.fullmatch(r"\d{6}", c):
                raise SectorSourceError(f"同花顺返回的板块 {code} 成分股代码不合法：{c!r}")
            try:
                A.exchange_symbol(c)
                codes.append(c)
            except A.InvalidInputError:
                skipped.append(c)
        codes = list(dict.fromkeys(codes))
        if not codes:
            raise SectorSourceError(f"板块 {code} 没有可识别的 A 股成分股")
        block = data.get("block") if isinstance(data.get("block"), dict) else {}
        name = str(block.get("name") or "").strip() or None
        fetched_at = self._clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        return SectorMembers(code, name, codes, url, fetched_at, skipped)


def make_sector_source(kind: str):
    return THSWebSectorSource() if kind == "web" else None


if __name__ == "__main__":   # 本机连通性自测：python -m app.ths_web 886032
    import sys
    m = THSWebSectorSource().fetch(sys.argv[1] if len(sys.argv) > 1 else "886032")
    print(json.dumps(m.__dict__, ensure_ascii=False, indent=2))
