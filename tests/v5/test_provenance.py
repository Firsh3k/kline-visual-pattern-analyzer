"""
P0-2 / FIR-13: provenance 真实传递测试

覆盖：
- data_collector._build_provenance 从 MarketDataSeries 提取真实字段
- quality_report 缺失 / 完整率不足 / 未知缺失天数 → data_quality 非 OK
- 旧格式缓存（纯 OHLCV 列表）兼容加载，provenance 为 unknown
- signal_generator.generate_signals 透传 provenance dict 到 SignalOccurrence
- 无法获取 provenance 时标记 "unknown"，不硬编码 multi_source/qfq
"""

import os
import sys
import json
import tempfile

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.types import (  # noqa: E402
    OHLCV,
    MarketDataSeries,
    MarketDataBar,
    DataProvenance,
    DataQualityReport,
    DataSourceType,
    DataQualityFlag,
    AdjustmentType,
    FrequencyType,
)
from core.calibration.data_collector import (  # noqa: E402
    CalibrationDataCollector,
    UNKNOWN_PROVENANCE,
)
from core.calibration.signal_generator import CalibrationSignalGenerator  # noqa: E402
from core.calibration.types import MarketRegime, CalibrationDataset  # noqa: E402


# canonical provenance 完整字段集（与 data_collector.UNKNOWN_PROVENANCE 对齐）
CANONICAL_PROVENANCE_FIELDS = [
    "data_source", "adjustment", "frequency", "schema_version",
    "trading_calendar", "fetched_at", "last_bar_date", "bar_count",
    "date_range", "data_quality", "quality_detail", "provenance_hash",
]

CANONICAL_DATA_QUALITY_FIELDS = [
    "status", "missing_days", "suspended_days", "ohlc_violations",
    "adjustment_consistency", "source_consistency",
    "anomalies_count", "completeness_ratio",
]


# ---------------------------------------------------------------------------
# 构造工具
# ---------------------------------------------------------------------------

def _make_bar(date: str, close: float) -> MarketDataBar:
    return MarketDataBar(
        date=date, open=close - 0.1, high=close + 0.2, low=close - 0.2,
        close=close, volume=1000000.0,
        provenance=DataProvenance(
            source=DataSourceType.SINA, url="", fetched_at="2026-09-01T15:00:00+08:00",
            raw_payload_hash="sha256:002415samplehash", adjustment_applied=AdjustmentType.FORWARD,
            quality_flag=DataQualityFlag.OK,
        ),
    )


def _make_series(quality_report=None, source=DataSourceType.SINA,
                 adjustment=AdjustmentType.FORWARD) -> MarketDataSeries:
    bars = [_make_bar("2026-08-28", 100.0), _make_bar("2026-09-01", 101.5)]
    return MarketDataSeries(
        symbol="sz002415", name="海康威视", frequency=FrequencyType.DAILY,
        adjustment=adjustment, trading_calendar="SSE", source=source,
        fetched_at="2026-09-01T15:30:00+08:00", bars=bars,
        quality_report=quality_report,
    )


def _good_quality_report() -> DataQualityReport:
    return DataQualityReport(
        total_days=365, expected_trading_days=243, actual_bars=243,
        suspended_days=0, missing_days=0, completeness_ratio=1.0,
        adjustment_consistent=True, unknown_missing_days=0,
    )


def _collector(tmp_path) -> CalibrationDataCollector:
    return CalibrationDataCollector(cache_dir=str(tmp_path), use_cache=True)


# ---------------------------------------------------------------------------
# _build_provenance 字段提取
# ---------------------------------------------------------------------------

class TestBuildProvenance:
    def test_full_provenance_extracted(self):
        series = _make_series(quality_report=_good_quality_report())
        prov = CalibrationDataCollector._build_provenance(series)
        assert prov["data_source"] == "sina"
        assert prov["adjustment"] == "qfq"
        assert prov["data_quality"]["status"] == "OK"
        assert prov["fetched_at"] == "2026-09-01T15:30:00+08:00"
        assert prov["last_bar_date"] == "2026-09-01"
        # quality_detail 来自 quality_report.to_dict()
        assert prov["quality_detail"] is not None
        assert prov["quality_detail"]["actual_bars"] == 243

    def test_tencent_source_and_raw_adjustment(self):
        series = _make_series(
            quality_report=_good_quality_report(),
            source=DataSourceType.TENCENT, adjustment=AdjustmentType.NONE)
        prov = CalibrationDataCollector._build_provenance(series)
        assert prov["data_source"] == "tencent"
        assert prov["adjustment"] == "none"
        assert prov["data_quality"]["status"] == "OK"

    def test_no_quality_report_marks_unknown(self):
        series = _make_series(quality_report=None)
        prov = CalibrationDataCollector._build_provenance(series)
        assert prov["data_quality"]["status"] == "unknown"
        assert prov["quality_detail"] is None
        # 其他字段仍从 series 真实提取
        assert prov["data_source"] == "sina"

    def test_low_completeness_not_ok(self):
        qr = _good_quality_report()
        qr.completeness_ratio = 0.90  # < 0.95
        series = _make_series(quality_report=qr)
        prov = CalibrationDataCollector._build_provenance(series)
        assert "low_completeness" in prov["data_quality"]["status"]
        assert prov["data_quality"]["status"] != "OK"

    def test_unknown_missing_days_not_ok(self):
        qr = _good_quality_report()
        qr.unknown_missing_days = 3
        series = _make_series(quality_report=qr)
        prov = CalibrationDataCollector._build_provenance(series)
        # fail-closed：unknown_missing_days>0 归入 has_missing
        assert "has_missing" in prov["data_quality"]["status"]
        assert prov["data_quality"]["status"] != "OK"

    def test_none_series_returns_unknown(self):
        prov = CalibrationDataCollector._build_provenance(None)
        # 标量字符串字段为 unknown
        for key in ("data_source", "adjustment", "frequency",
                    "schema_version", "trading_calendar",
                    "fetched_at", "last_bar_date", "provenance_hash"):
            assert prov[key] == "unknown"
        # 数值 / 容器字段取缺省
        assert prov["bar_count"] == 0
        assert prov["date_range"] == {"start": "unknown", "end": "unknown"}
        assert prov["data_quality"]["status"] == "unknown"
        assert prov["quality_detail"] is None

    # ---- FIR-23 扩展 canonical 字段 ----
    def test_extended_canonical_fields(self):
        series = _make_series(quality_report=_good_quality_report())
        prov = CalibrationDataCollector._build_provenance(series)
        # 频率 / schema 版本 / 交易日历
        assert prov["frequency"] == "daily"
        assert prov["schema_version"] == "1.0"
        assert prov["trading_calendar"] == "SSE"
        # bar_count / date_range 派生自 bars
        assert prov["bar_count"] == 2
        assert prov["date_range"] == {"start": "2026-08-28", "end": "2026-09-01"}
        # provenance_hash 从 bars[0].provenance.raw_payload_hash 提取
        assert prov["provenance_hash"] == "sha256:002415samplehash"
        # 完整 canonical 字段集齐全
        for f in CANONICAL_PROVENANCE_FIELDS:
            assert f in prov, f"missing canonical field: {f}"

    def test_data_quality_fail_closed_missing(self):
        qr = _good_quality_report()
        qr.missing_days = 3
        series = _make_series(quality_report=qr)
        prov = CalibrationDataCollector._build_provenance(series)
        assert "has_missing" in prov["data_quality"]["status"]
        assert prov["data_quality"]["status"] != "OK"
        assert prov["data_quality"]["missing_days"] == 3

    def test_data_quality_fail_closed_adjustment(self):
        qr = _good_quality_report()
        qr.adjustment_consistent = False
        series = _make_series(quality_report=qr)
        prov = CalibrationDataCollector._build_provenance(series)
        assert "adjustment_unverified" in prov["data_quality"]["status"]
        assert prov["data_quality"]["adjustment_consistency"] == "unverified"

    def test_data_quality_fail_closed_anomalies(self):
        qr = _good_quality_report()
        qr.anomalies = [{"type": "gap", "date": "2026-09-01"}]
        series = _make_series(quality_report=qr)
        prov = CalibrationDataCollector._build_provenance(series)
        assert "has_anomalies" in prov["data_quality"]["status"]
        assert prov["data_quality"]["anomalies_count"] == 1

    def test_data_quality_ok_when_all_good(self):
        series = _make_series(quality_report=_good_quality_report())
        prov = CalibrationDataCollector._build_provenance(series)
        assert prov["data_quality"]["status"] == "OK"
        # 完整 data_quality 子字段集齐全
        for f in CANONICAL_DATA_QUALITY_FIELDS:
            assert f in prov["data_quality"], f"missing dq field: {f}"

    def test_unknown_fields_when_series_none(self):
        prov = CalibrationDataCollector._build_provenance(None)
        # 所有新字段为 unknown / 0 / None，绝不硬编码假值
        assert prov["frequency"] == "unknown"
        assert prov["schema_version"] == "unknown"
        assert prov["trading_calendar"] == "unknown"
        assert prov["bar_count"] == 0
        assert prov["date_range"] == {"start": "unknown", "end": "unknown"}
        assert prov["provenance_hash"] == "unknown"
        dq = prov["data_quality"]
        assert dq["status"] == "unknown"
        assert dq["missing_days"] is None
        assert dq["suspended_days"] is None
        assert dq["anomalies_count"] is None
        assert dq["completeness_ratio"] is None


# ---------------------------------------------------------------------------
# 缓存格式：新格式写入 / 旧格式兼容
# ---------------------------------------------------------------------------

class TestCacheFormat:
    def test_roundtrip_new_format(self, tmp_path):
        c = _collector(tmp_path)
        klines = [OHLCV(date="2026-09-01", open=100.0, high=101.0, low=99.0,
                        close=100.5, volume=1000.0)]
        prov = {"data_source": "sina", "adjustment": "qfq", "data_quality": "OK"}
        path = os.path.join(str(tmp_path), "x_qfq.json")
        c._save_cache(path, klines, prov)

        # 文件是新格式 dict
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
        assert isinstance(raw, dict)
        assert "klines" in raw and "provenance" in raw

        loaded = c._load_cache(path)
        assert loaded is not None
        got_klines, got_prov = loaded
        assert len(got_klines) == 1
        assert got_klines[0].close == 100.5
        assert got_prov["data_source"] == "sina"
        assert got_prov["adjustment"] == "qfq"

    def test_legacy_list_cache_compat(self, tmp_path):
        """旧格式纯 OHLCV 列表 → 兼容加载，provenance 为 unknown 占位"""
        c = _collector(tmp_path)
        path = os.path.join(str(tmp_path), "legacy_qfq.json")
        old = [OHLCV(date="2026-09-01", open=100.0, high=101.0, low=99.0,
                     close=100.5, volume=1000.0).to_dict()]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(old, f)

        loaded = c._load_cache(path)
        assert loaded is not None
        got_klines, got_prov = loaded
        assert len(got_klines) == 1
        assert got_klines[0].close == 100.5
        # provenance 全部 unknown
        assert got_prov["data_source"] == "unknown"
        assert got_prov["adjustment"] == "unknown"
        assert got_prov["data_quality"]["status"] == "unknown"

    def test_missing_cache_returns_none(self, tmp_path):
        c = _collector(tmp_path)
        assert c._load_cache(os.path.join(str(tmp_path), "nope.json")) is None


# ---------------------------------------------------------------------------
# generate_signals 透传 provenance
# ---------------------------------------------------------------------------

def _rising_klines(n=90):
    from datetime import date, timedelta
    out = []
    base = date(2024, 1, 2)
    for i in range(n):
        c = 100.0 + i * 0.3
        d = (base + timedelta(days=i)).isoformat()
        out.append(OHLCV(date=d, open=c - 0.1, high=c + 0.2, low=c - 0.2,
                         close=c, volume=2000.0))
    return out


class TestGeneratorProvenancePassThrough:
    def test_provenance_dict_overrides_signals(self):
        gen = CalibrationSignalGenerator()
        klines = _rising_klines()
        regime = {k.date: MarketRegime.RANGE for k in klines}
        prov = {"data_source": "tencent", "adjustment": "qfq",
                "data_quality": "OK", "fetched_at": "2026-09-01T00:00:00+08:00"}
        sigs = gen.generate_signals(klines, "600519", regime, provenance=prov)
        assert sigs
        for s in sigs:
            assert s.data_source == "tencent"   # 来自 provenance，不是 multi_source
            assert s.adjustment == "qfq"
            assert s.data_quality == "OK"

    def test_explicit_params_used_when_no_provenance(self):
        gen = CalibrationSignalGenerator()
        klines = _rising_klines()
        regime = {k.date: MarketRegime.RANGE for k in klines}
        sigs = gen.generate_signals(klines, "600519", regime,
                                    data_source="multi_source", adjustment="qfq")
        assert sigs
        for s in sigs:
            assert s.data_source == "multi_source"
            assert s.adjustment == "qfq"

    def test_unknown_provenance_not_hardcoded(self):
        """provenance 字段为 unknown 时透传 unknown，而非默认 multi_source/qfq"""
        gen = CalibrationSignalGenerator()
        klines = _rising_klines()
        regime = {k.date: MarketRegime.RANGE for k in klines}
        prov = dict(UNKNOWN_PROVENANCE)  # 全 unknown
        sigs = gen.generate_signals(klines, "600519", regime, provenance=prov)
        assert sigs
        for s in sigs:
            assert s.data_source == "unknown"
            assert s.adjustment == "unknown"
            # data_quality 在 SignalOccurrence 中为字符串（provenance dict 的 status 字段）
            assert s.data_quality == "unknown"

    def test_fetch_stock_data_backward_compat_returns_list(self, tmp_path, monkeypatch):
        """fetch_stock_data 仍返回 List[OHLCV]（向后兼容）"""
        c = _collector(tmp_path)
        series = _make_series(quality_report=_good_quality_report())

        class _FakeProvider:
            def fetch_klines(self, **kwargs):
                return series

        monkeypatch.setattr(c, "_provider", _FakeProvider())
        klines = c.fetch_stock_data("002415", "2026-08-01", "2026-09-01")
        assert isinstance(klines, list)
        assert len(klines) == 2
        # 同时 with_provenance 返回 tuple
        klines2, prov = c.fetch_stock_data_with_provenance(
            "002415", "2026-08-01", "2026-09-01")
        assert len(klines2) == 2
        assert prov["data_source"] == "sina"


# ---------------------------------------------------------------------------
# FIR-23: provenance_manifest 端到端保留（dataset.metadata）
# ---------------------------------------------------------------------------

class TestProvenanceManifest:
    def _manifest_pair(self):
        """构造两只股票的 provenance（一正常一 unknown）。"""
        good = CalibrationDataCollector._build_provenance(
            _make_series(quality_report=_good_quality_report()))
        unknown = dict(UNKNOWN_PROVENANCE)
        return {"600519": good, "000001": unknown}

    def test_manifest_structure_in_dataset(self):
        manifest = self._manifest_pair()
        ds = CalibrationDataset(
            stock_pool=["600519", "000001"],
            start_date="2026-08-01", end_date="2026-09-01",
            metadata={
                "provenance_summary": {
                    "data_source_distribution": {"sina": 1, "unknown": 1},
                },
                "provenance_manifest": manifest,
            },
        )
        # 序列化 → 反序列化后结构完整
        raw = ds.to_dict()
        rt = CalibrationDataset.from_dict(raw)
        got = rt.metadata["provenance_manifest"]
        assert set(got.keys()) == {"600519", "000001"}
        # JSON 往返（dict 可序列化）
        text = json.dumps(raw, ensure_ascii=False)
        again = json.loads(text)
        assert "provenance_manifest" in again["metadata"]
        assert again["metadata"]["provenance_manifest"]["600519"]["data_source"] == "sina"

    def test_manifest_per_code_complete_fields(self):
        manifest = self._manifest_pair()
        for code, prov in manifest.items():
            # 每只股票的 provenance 包含全部 canonical 字段
            for f in CANONICAL_PROVENANCE_FIELDS:
                assert f in prov, f"{code} 缺少 canonical 字段: {f}"
            # data_quality 为 dict 且含全部子字段
            dq = prov["data_quality"]
            assert isinstance(dq, dict)
            for f in CANONICAL_DATA_QUALITY_FIELDS:
                assert f in dq, f"{code}.data_quality 缺少子字段: {f}"
            # date_range 为 dict
            assert isinstance(prov["date_range"], dict)
            assert "start" in prov["date_range"] and "end" in prov["date_range"]
