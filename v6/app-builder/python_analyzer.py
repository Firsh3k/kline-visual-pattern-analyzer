#!/usr/bin/env python3
"""
python_analyzer.py - K线形态分析本地执行器（App Builder 部署版 · V6 阶段1）

唯一算法 Source of Truth：Python Canonical Core（core.analysis.analyze）。
形态识别、关键点、颈线 / 目标 / 止损、阶段、评分、量价全部由 core 计算；
本脚本只负责：取行情（腾讯前复权）→ 调 core.analysis → 输出 core 原生文档。

口径（与 main 一致）：
    - 个股一律前复权 qfq，固定 250 根窗口；qfq 只走腾讯，失败即报错，绝不静默回退不复权；
    - 每种形态取【最近一次】实例（按关键点最大 index），旧形态不会冒充当前信号（core.analysis.pattern_summary）；
    - 8 种形态（W底 / 头肩底 / 平台突破 / 均线多头；M头 / 头肩顶 / 平台破位 / 均线空头）全部由 core 输出；
    - 支持 --as-of 回测：core 只使用截至该日的 K 线，不看未来；
    - 不输出任何综合“推荐度/关注分”，也不输出无来源的历史成功率（每个形态的 score 仅为算法信号强度）。

⚠️ fail-closed：行情不可用 / 复权方式不一致 / core 校验失败，一律非零退出 + stderr 错误，
   绝不返回骨架、随机或静默降级结果。

用法:
    python3 python_analyzer.py <6位代码> [--as-of YYYY-MM-DD] [--adjustment qfq|none]
    python3 python_analyzer.py --mode market          # 大盘环境（上证指数，腾讯不复权）
    python3 python_analyzer.py --mode health

输出 (stdout, JSON)：core.analysis 的完整文档 + summary（扫描列表用）。失败写 stderr 并退出码 1。
"""
import argparse
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

# 部署时 python_analyzer.py 与 core/ 同目录（app_builder 的 process.cwd()）。
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

CODE_RE = re.compile(r"^\d{6}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
FETCH_CALENDAR_DAYS = 420          # 约覆盖 250 个交易日，core 固定截取最后 250 根
HTTP_TIMEOUT = 10
# 同一腾讯 K 线 API 的等价子域：web.* 为标准入口，裸 ifzq 在部分出口 IP 被 WAF 拦截时作为传输层兜底。
_TENCENT_KLINE_HOSTS = ["web.ifzq.gtimg.cn", "ifzq.gtimg.cn"]
_TENCENT_QUOTE_HOSTS = ["qt.gtimg.cn", "ifzq.gtimg.cn"]


def _fail(message: str, **extra):
    """统一 fail-closed 错误输出（stderr JSON + 退出码 1）。"""
    payload = {"error": message, "fail_closed": True}
    payload.update(extra)
    print(json.dumps(payload, ensure_ascii=False), file=sys.stderr)
    sys.exit(1)


def _fetch_series(symbol: str, adjustment: str):
    """用 core 的 TencentDataProvider 拉日线；qfq 严格只接受 qfqday，失败换等价子域后仍失败即报错。"""
    from core import AdjustmentType, FrequencyType
    import core.market_data.tencent_provider as tp

    adj = AdjustmentType.FORWARD if adjustment == "qfq" else AdjustmentType.NONE
    start = (datetime.now(_import_cn_tz()) - timedelta(days=FETCH_CALENDAR_DAYS)).strftime("%Y-%m-%d")

    template = tp._TENCENT_URL
    last_err = None
    for host in _TENCENT_KLINE_HOSTS:
        tp._TENCENT_URL = template.replace("web.ifzq.gtimg.cn", host)
        try:
            series = tp.TencentDataProvider().fetch_klines(
                symbol, start_date=start, frequency=FrequencyType.DAILY, adjustment=adj)
            if not series.bars:
                raise RuntimeError(f"{symbol} 行情返回为空")
            if series.adjustment != adj:
                raise RuntimeError(f"复权方式不一致：请求 {adj.value}，返回 {series.adjustment.value}，拒绝混用")
            return series
        except Exception as exc:  # noqa: BLE001 - 记录后尝试下一 host，全部失败再 fail-closed
            last_err = exc
        finally:
            tp._TENCENT_URL = template
    raise RuntimeError(f"腾讯行情源不可用（{symbol} {adjustment}）：{last_err}")


def _import_cn_tz():
    from core.analysis import CN_TZ
    return CN_TZ


def _now_cn():
    return datetime.now(_import_cn_tz())


def _fetch_name(code: str, symbol: str) -> str:
    """从腾讯实时行情接口取中文名（GBK，字段用 ~ 分隔，[1] 为名称）；失败回退代码，不阻断分析。"""
    for host in _TENCENT_QUOTE_HOSTS:
        try:
            url = f"https://{host}/q={symbol}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            raw = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read().decode("gbk", "ignore")
            m = re.search(r'="([^"]+)"', raw)
            if m:
                fields = m.group(1).split("~")
                if len(fields) > 1 and fields[1].strip():
                    return fields[1].strip()
        except Exception:  # noqa: BLE001
            continue
    return code


def _series_to_bars(series) -> list:
    return [{"day": b.date, "open": float(b.open), "high": float(b.high),
             "low": float(b.low), "close": float(b.close), "volume": float(b.volume)}
            for b in series.bars]


def _provenance(series, adjustment: str) -> dict:
    last = series.bars[-1]
    raw_hash = last.provenance.raw_payload_hash if getattr(last, "provenance", None) else None
    qr = None
    if getattr(series, "quality_report", None) is not None:
        try:
            qr = series.quality_report.to_dict()
        except Exception:  # noqa: BLE001
            qr = None
    return {
        "source": getattr(series.source, "value", "tencent"),
        "mode": "live",
        "adjustment": getattr(series.adjustment, "value", adjustment),
        "fetched_at": getattr(series, "fetched_at", None),
        "raw_payload_sha256": raw_hash,
        "quality_report": qr,
    }


def analyze_stock(code: str, as_of: str | None = None, adjustment: str = "qfq") -> dict:
    from core import analysis as A
    from core.interfaces import DataProviderError, MarketDataError

    InvalidInputError = A.InvalidInputError
    symbol = A.exchange_symbol(code)
    try:
        series = _fetch_series(symbol, adjustment)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"行情获取失败（fail-closed，未降级）：{exc}") from exc

    bars = _series_to_bars(series)
    name = _fetch_name(code, symbol)
    prov = _provenance(series, adjustment)
    try:
        doc = A.analyze(code, bars, adjustment=adjustment, provenance=prov,
                        as_of=as_of, name=name)
    except InvalidInputError as exc:
        raise RuntimeError(f"行情数据未通过 core 严格校验：{exc}") from exc
    except (DataProviderError, MarketDataError) as exc:
        raise RuntimeError(f"行情数据错误：{exc}") from exc

    # 扫描列表 / 自选股摘要：每种形态最近一次，与个股页同源。
    doc["summary"] = A.pattern_summary(doc)
    # 兼容字段：引擎标记（NestJS 生产路径校验）、旧前端 K 线格式。
    doc["engine"]["analyzer"] = "real_core"
    doc["engine"]["data_source"] = prov["source"]
    doc["data_length"] = len(doc["bars"])
    doc["latest_date"] = doc["as_of"]
    doc["latest_close"] = doc["bars"][-1][4]
    doc["klines"] = [
        {"date": b[0], "open": b[1], "high": b[2], "low": b[3], "close": b[4], "volume": b[5]}
        for b in doc["bars"]
    ]
    return doc


def analyze_market(as_of: str | None = None) -> dict:
    from core import analysis as A
    series = _fetch_series("sh000001", "none")
    last_close = float(series.bars[-1].close)
    if not (500 < last_close < 20000):
        raise RuntimeError(f"上证指数收盘价 {last_close} 量级不合理，疑似取到个股，拒绝使用")
    bars = _series_to_bars(series)
    env = A.analyze_market(bars, as_of=as_of)
    if env is None:
        raise RuntimeError("大盘环境分析为空（数据不足）")
    return {"market_env": env, "provenance": _provenance(series, "none"),
            "calendar": A.calendar_info("sh000001", env["as_of"], _now_cn())}


def health_check() -> dict:
    try:
        from core import analysis as A  # noqa: F401
        core_ok = True
        version = A.CORE_VERSION
    except Exception as exc:  # noqa: BLE001
        core_ok, version = False, str(exc)
    return {
        "status": "ok" if core_ok else "degraded",
        "python_version": sys.version.split()[0],
        "core_installed": core_ok,
        "core_version": version,
        "analyzer": "real_core" if core_ok else "unavailable",
        "adjustment": "qfq",
        "window": 250,
        "fail_closed": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="K线形态分析 - 本地 Python Canonical Core 执行器")
    parser.add_argument("code", nargs="?", help="6位股票代码")
    parser.add_argument("--as-of", dest="as_of", default=None, help="回测截止日 YYYY-MM-DD（不含未来）")
    parser.add_argument("--adjustment", default="qfq", choices=["qfq", "none"], help="复权方式，默认 qfq")
    parser.add_argument("--mode", default="analyze", choices=["analyze", "health", "market"])
    args = parser.parse_args()

    if args.mode == "health":
        print(json.dumps(health_check(), ensure_ascii=False))
        return 0

    if args.as_of is not None and not DATE_RE.match(args.as_of):
        _fail(f"--as-of 必须为 YYYY-MM-DD：{args.as_of!r}")

    if args.mode == "market":
        try:
            print(json.dumps(analyze_market(args.as_of), ensure_ascii=False))
            return 0
        except Exception as exc:  # noqa: BLE001
            _fail(str(exc))

    if not args.code:
        _fail("缺少股票代码参数")
    if not CODE_RE.match(args.code):
        _fail(f"股票代码必须为 6 位数字：{args.code!r}")

    try:
        result = analyze_stock(args.code, as_of=args.as_of, adjustment=args.adjustment)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        _fail(str(exc), code=args.code)
        return 1


if __name__ == "__main__":
    sys.exit(main())
