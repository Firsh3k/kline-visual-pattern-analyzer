/**
 * Canonical Schema — JS 端
 *
 * 定义所有 V5 canonical 类型的字段名、枚举值、null 语义。
 * 与 Python 端 core/types.py 对应，通过 cross-runtime parity 测试保证一致。
 *
 * 这是 machine-readable contract，不是运行时类型检查器。
 * Web 端生产代码（kline_analyzer.html）中的对象必须遵守此 schema。
 */

const CANONICAL_SCHEMA = {
  version: '5.0.0',
  source: 'js-canonical-schema',

  enums: {
    TrendDirection: {
      name: 'TrendDirection',
      values: ['bull', 'bear', 'neutral'],
      members: { BULL: 'bull', BEAR: 'bear', NEUTRAL: 'neutral' }
    },
    PatternStage: {
      name: 'PatternStage',
      values: ['forming', 'pre_break', 'breakout_day', 'post_break', 'retest', 'failed_break', 'complete'],
      members: {
        FORMING: 'forming',
        PRE_BREAK: 'pre_break',
        BREAKOUT_DAY: 'breakout_day',
        POST_BREAK: 'post_break',
        RETEST: 'retest',
        FAILED_BREAK: 'failed_break',
        COMPLETE: 'complete'
      }
    },
    SignalType: {
      name: 'SignalType',
      values: ['buy', 'sell', 'watch', 'none'],
      members: { BUY: 'buy', SELL: 'sell', WATCH: 'watch', NONE: 'none' }
    },
    ConfidenceLevel: {
      name: 'ConfidenceLevel',
      values: ['S', 'A', 'B', 'C', 'D'],
      members: { S: 'S', A: 'A', B: 'B', C: 'C', D: 'D' }
    },
    AdjustmentType: {
      name: 'AdjustmentType',
      values: ['none', 'qfq', 'hfq'],
      members: { NONE: 'none', FORWARD: 'qfq', BACKWARD: 'hfq' }
    },
    FrequencyType: {
      name: 'FrequencyType',
      values: ['daily', 'weekly', 'monthly'],
      members: { DAILY: 'daily', WEEKLY: 'weekly', MONTHLY: 'monthly' }
    },
    DataSourceType: {
      name: 'DataSourceType',
      values: ['sina', 'tencent', 'ifind', 'tongdaxin', 'manual'],
      members: { SINA: 'sina', TENCENT: 'tencent', IFIND: 'ifind', TONGDAXIN: 'tongdaxin', MANUAL: 'manual' }
    },
    DataQualityFlag: {
      name: 'DataQualityFlag',
      values: ['ok', 'suspended', 'missing', 'adjusted', 'partial'],
      members: { OK: 'ok', SUSPENDED: 'suspended', MISSING: 'missing', ADJUSTED: 'adjusted', PARTIAL: 'partial' }
    }
  },

  dataclasses: {
    OHLCV: {
      name: 'OHLCV',
      field_count: 7,
      required_fields: ['date', 'open', 'high', 'low', 'close', 'volume'],
      optional_fields: ['amount'],
      fields: [
        { name: 'date', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'open', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'high', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'low', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'close', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'volume', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'amount', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true }
      ]
    },

    StockData: {
      name: 'StockData',
      field_count: 6,
      required_fields: ['code', 'name', 'klines'],
      optional_fields: ['market', 'source', 'last_updated'],
      fields: [
        { name: 'code', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'name', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'klines', type: 'List[OHLCV]', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'market', type: 'str', is_optional: false, has_default: true, default: 'A股', nullable: false },
        { name: 'source', type: 'str', is_optional: false, has_default: true, default: '', nullable: false },
        { name: 'last_updated', type: 'str', is_optional: false, has_default: true, default: '', nullable: false }
      ]
    },

    PivotPoint: {
      name: 'PivotPoint',
      field_count: 8,
      required_fields: ['index', 'date', 'price', 'pivot_type'],
      optional_fields: ['strength', 'confirmed', 'confirmed_at_index', 'confirmed_at_date'],
      fields: [
        { name: 'index', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'date', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'price', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'pivot_type', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'strength', type: 'int', is_optional: false, has_default: true, default: 1, nullable: false },
        { name: 'confirmed', type: 'bool', is_optional: false, has_default: true, default: true, nullable: false },
        { name: 'confirmed_at_index', type: 'Optional[int]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'confirmed_at_date', type: 'Optional[str]', is_optional: true, has_default: true, default: null, nullable: true }
      ]
    },

    PatternKeyPoint: {
      name: 'PatternKeyPoint',
      field_count: 5,
      required_fields: ['name', 'index', 'date', 'price'],
      optional_fields: ['description'],
      fields: [
        { name: 'name', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'index', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'date', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'price', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'description', type: 'str', is_optional: false, has_default: true, default: '', nullable: false }
      ]
    },

    PatternResult: {
      name: 'PatternResult',
      field_count: 14,
      required_fields: ['pattern_id', 'pattern_name', 'direction', 'stage', 'score', 'confidence', 'key_points'],
      optional_fields: ['neckline_price', 'breakout_price', 'target_price', 'stop_loss', 'notes', 'category', 'evidence'],
      fields: [
        { name: 'pattern_id', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'pattern_name', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'direction', type: 'TrendDirection', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'stage', type: 'PatternStage', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'score', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'confidence', type: 'ConfidenceLevel', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'key_points', type: 'List[PatternKeyPoint]', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'neckline_price', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'breakout_price', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'target_price', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'stop_loss', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'notes', type: 'str', is_optional: false, has_default: true, default: '', nullable: false },
        { name: 'category', type: 'str', is_optional: false, has_default: true, default: 'price_pattern', nullable: false },
        { name: 'evidence', type: 'Dict[str, Any]', is_optional: false, has_default: true, default: {}, nullable: false }
      ]
    },

    SignalResult: {
      name: 'SignalResult',
      field_count: 10,
      required_fields: ['signal_type', 'pattern_id', 'pattern_name', 'score', 'confidence', 'trigger_price'],
      optional_fields: ['target_price', 'stop_loss', 'reason', 'risks'],
      fields: [
        { name: 'signal_type', type: 'SignalType', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'pattern_id', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'pattern_name', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'score', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'confidence', type: 'ConfidenceLevel', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'trigger_price', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'target_price', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'stop_loss', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'reason', type: 'str', is_optional: false, has_default: true, default: '', nullable: false },
        { name: 'risks', type: 'List[str]', is_optional: false, has_default: true, default: [], nullable: false }
      ]
    },

    MarketRegime: {
      name: 'MarketRegime',
      field_count: 7,
      required_fields: ['regime', 'score', 'trend'],
      optional_fields: ['stage', 'advice', 'impact_on_stock', 'details'],
      fields: [
        { name: 'regime', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'score', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'trend', type: 'TrendDirection', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'stage', type: 'str', is_optional: false, has_default: true, default: '', nullable: false },
        { name: 'advice', type: 'str', is_optional: false, has_default: true, default: '', nullable: false },
        { name: 'impact_on_stock', type: 'int', is_optional: false, has_default: true, default: 0, nullable: false },
        { name: 'details', type: 'Dict[str, Any]', is_optional: false, has_default: true, default: {}, nullable: false }
      ]
    },

    FakeSignalRisk: {
      name: 'FakeSignalRisk',
      field_count: 4,
      required_fields: ['risk_score', 'risk_level'],
      optional_fields: ['risks', 'suggestions'],
      fields: [
        { name: 'risk_score', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'risk_level', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'risks', type: 'List[Dict[str, Any]]', is_optional: false, has_default: true, default: [], nullable: false },
        { name: 'suggestions', type: 'List[str]', is_optional: false, has_default: true, default: [], nullable: false }
      ]
    },

    AnalysisResult: {
      name: 'AnalysisResult',
      field_count: 10,
      required_fields: ['stock', 'patterns', 'buy_signals', 'sell_signals'],
      optional_fields: ['market_regime', 'fake_signal_risk', 'overall_score', 'recommendation', 'summary', 'metadata'],
      fields: [
        { name: 'stock', type: 'StockData', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'patterns', type: 'List[PatternResult]', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'buy_signals', type: 'List[SignalResult]', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'sell_signals', type: 'List[SignalResult]', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'market_regime', type: 'Optional[MarketRegime]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'fake_signal_risk', type: 'Optional[FakeSignalRisk]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'overall_score', type: 'int', is_optional: false, has_default: true, default: 0, nullable: false },
        { name: 'recommendation', type: 'str', is_optional: false, has_default: true, default: '', nullable: false },
        { name: 'summary', type: 'str', is_optional: false, has_default: true, default: '', nullable: false },
        { name: 'metadata', type: 'Dict[str, Any]', is_optional: false, has_default: true, default: {}, nullable: false }
      ]
    },

    BacktestResult: {
      name: 'BacktestResult',
      field_count: 11,
      required_fields: ['code', 'signal_date', 'horizon_days', 'entry_price'],
      optional_fields: ['return_pct', 'mfe_pct', 'mae_pct', 'final_price', 'actual_days', 'is_complete', 'notes'],
      fields: [
        { name: 'code', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'signal_date', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'horizon_days', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'entry_price', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'return_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'mfe_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'mae_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'final_price', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'actual_days', type: 'Optional[int]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'is_complete', type: 'bool', is_optional: false, has_default: true, default: false, nullable: false },
        { name: 'notes', type: 'str', is_optional: false, has_default: true, default: '', nullable: false }
      ]
    },

    BacktestSummary: {
      name: 'BacktestSummary',
      field_count: 13,
      required_fields: ['horizon_days', 'sample_size'],
      optional_fields: ['win_rate', 'avg_return_pct', 'median_return_pct', 'avg_mfe_pct', 'avg_mae_pct', 'max_return_pct', 'min_return_pct', 'profit_factor', 'results', 'incomplete_count', 'notes'],
      fields: [
        { name: 'horizon_days', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'sample_size', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'win_rate', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'avg_return_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'median_return_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'avg_mfe_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'avg_mae_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'max_return_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'min_return_pct', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'profit_factor', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'results', type: 'List[BacktestResult]', is_optional: false, has_default: true, default: [], nullable: false },
        { name: 'incomplete_count', type: 'int', is_optional: false, has_default: true, default: 0, nullable: false },
        { name: 'notes', type: 'str', is_optional: false, has_default: true, default: '', nullable: false }
      ]
    },

    DataProvenance: {
      name: 'DataProvenance',
      field_count: 6,
      required_fields: ['source', 'url', 'fetched_at', 'raw_payload_hash', 'adjustment_applied', 'quality_flag'],
      optional_fields: [],
      fields: [
        { name: 'source', type: 'DataSourceType', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'url', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'fetched_at', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'raw_payload_hash', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'adjustment_applied', type: 'AdjustmentType', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'quality_flag', type: 'DataQualityFlag', is_optional: false, has_default: false, default: null, nullable: false }
      ]
    },

    MarketDataBar: {
      name: 'MarketDataBar',
      field_count: 8,
      required_fields: ['date', 'open', 'high', 'low', 'close', 'volume', 'provenance'],
      optional_fields: ['amount'],
      fields: [
        { name: 'date', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'open', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'high', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'low', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'close', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'volume', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'provenance', type: 'DataProvenance', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'amount', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true }
      ]
    },

    MarketDataSeries: {
      name: 'MarketDataSeries',
      field_count: 10,
      required_fields: ['symbol', 'name', 'frequency', 'adjustment', 'trading_calendar', 'source', 'fetched_at', 'bars'],
      optional_fields: ['schema_version', 'quality_report'],
      fields: [
        { name: 'symbol', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'name', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'frequency', type: 'FrequencyType', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'adjustment', type: 'AdjustmentType', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'trading_calendar', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'source', type: 'DataSourceType', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'fetched_at', type: 'str', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'bars', type: 'List[MarketDataBar]', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'schema_version', type: 'str', is_optional: false, has_default: true, default: '1.0', nullable: false },
        { name: 'quality_report', type: 'Optional[DataQualityReport]', is_optional: true, has_default: true, default: null, nullable: true }
      ]
    },

    DataQualityReport: {
      name: 'DataQualityReport',
      field_count: 11,
      required_fields: ['total_days', 'expected_trading_days', 'actual_bars', 'suspended_days', 'missing_days', 'completeness_ratio', 'adjustment_consistent'],
      optional_fields: ['unknown_missing_days', 'source_consistency', 'anomalies', 'notes'],
      fields: [
        { name: 'total_days', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'expected_trading_days', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'actual_bars', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'suspended_days', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'missing_days', type: 'int', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'completeness_ratio', type: 'float', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'adjustment_consistent', type: 'bool', is_optional: false, has_default: false, default: null, nullable: false },
        { name: 'unknown_missing_days', type: 'int', is_optional: false, has_default: true, default: 0, nullable: false },
        { name: 'source_consistency', type: 'Optional[float]', is_optional: true, has_default: true, default: null, nullable: true },
        { name: 'anomalies', type: 'List[Dict[str, Any]]', is_optional: false, has_default: true, default: [], nullable: false },
        { name: 'notes', type: 'str', is_optional: false, has_default: true, default: '', nullable: false }
      ]
    }
  }
};

// 导出 schema（Node.js 环境）
if (typeof module !== 'undefined' && module.exports) {
  module.exports = CANONICAL_SCHEMA;
}
