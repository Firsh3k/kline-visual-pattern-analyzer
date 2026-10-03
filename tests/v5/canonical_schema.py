"""
Canonical Schema 提取器 — Python 端

从 core/types.py 动态提取所有数据类的字段名、类型、默认值，
以及枚举值，输出为 machine-readable JSON。

这是 cross-runtime contract parity test 的 Python 端参考实现。
"""

import json
import sys
import os
from dataclasses import fields, is_dataclass, MISSING
from typing import get_type_hints, Optional, List, Dict, Any, get_origin, get_args

# 确保项目根目录在 path 中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core import (
    OHLCV, StockData, PivotPoint, PatternKeyPoint, PatternResult,
    SignalResult, MarketRegime, FakeSignalRisk, AnalysisResult,
    BacktestResult, BacktestSummary,
    TrendDirection, PatternStage, SignalType, ConfidenceLevel,
)


def extract_enum_schema(enum_cls):
    """提取枚举的所有值"""
    return {
        'name': enum_cls.__name__,
        'values': [e.value for e in enum_cls],
        'members': {e.name: e.value for e in enum_cls},
    }


def extract_dataclass_schema(cls):
    """提取数据类的字段 schema"""
    type_hints = get_type_hints(cls)
    field_schemas = []

    for f in fields(cls):
        # 确定类型
        type_name = type_hints[f.name].__name__ if hasattr(type_hints[f.name], '__name__') else str(type_hints[f.name])

        # 处理 Optional / List / Dict 等泛型
        origin = get_origin(type_hints[f.name])
        args = get_args(type_hints[f.name])

        is_optional = origin is type(None) or (origin is not None and type(None) in args)
        is_list = origin is list or (origin is not None and list in [origin])
        is_dict = origin is dict

        # 简化类型名称
        if is_optional and args:
            inner = args[0] if args[0] is not type(None) else args[1]
            simple_type = f"Optional[{inner.__name__ if hasattr(inner, '__name__') else str(inner)}]"
        elif is_list and args:
            inner = args[0]
            simple_type = f"List[{inner.__name__ if hasattr(inner, '__name__') else str(inner)}]"
        elif is_dict:
            simple_type = "Dict[str, Any]"
        else:
            simple_type = type_name

        # 是否有默认值
        has_default = f.default is not MISSING or f.default_factory is not MISSING
        default_value = None
        if f.default is not MISSING:
            default_value = f.default
        elif f.default_factory is not MISSING:
            default_value = f.default_factory() if callable(f.default_factory) else str(f.default_factory)

        field_schema = {
            'name': f.name,
            'type': simple_type,
            'is_optional': is_optional,
            'has_default': has_default,
            'default': default_value if has_default else None,
            'nullable': is_optional,  # Optional 字段可以为 null
        }
        field_schemas.append(field_schema)

    return {
        'name': cls.__name__,
        'fields': field_schemas,
        'field_count': len(field_schemas),
        'required_fields': [f['name'] for f in field_schemas if not f['has_default']],
        'optional_fields': [f['name'] for f in field_schemas if f['has_default']],
    }


def extract_full_schema():
    """提取完整的 canonical schema"""
    enums = [
        ('TrendDirection', TrendDirection),
        ('PatternStage', PatternStage),
        ('SignalType', SignalType),
        ('ConfidenceLevel', ConfidenceLevel),
    ]

    dataclasses = [
        ('OHLCV', OHLCV),
        ('StockData', StockData),
        ('PivotPoint', PivotPoint),
        ('PatternKeyPoint', PatternKeyPoint),
        ('PatternResult', PatternResult),
        ('SignalResult', SignalResult),
        ('MarketRegime', MarketRegime),
        ('FakeSignalRisk', FakeSignalRisk),
        ('AnalysisResult', AnalysisResult),
        ('BacktestResult', BacktestResult),
        ('BacktestSummary', BacktestSummary),
    ]

    schema = {
        'version': '5.0.0',
        'source': 'python-core-types',
        'enums': {name: extract_enum_schema(cls) for name, cls in enums},
        'dataclasses': {name: extract_dataclass_schema(cls) for name, cls in dataclasses},
    }

    return schema


if __name__ == '__main__':
    schema = extract_full_schema()
    print(json.dumps(schema, indent=2, ensure_ascii=False, default=str))
