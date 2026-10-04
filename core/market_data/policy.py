"""
FIR-23: 数据源/复权策略（Blocker 7）

- UseCase：技术分析用前复权（QFQ），交易成交价用不复权（NONE）。
- MultiSourceDataProvider 对 qfq/hfq 请求：优先 Tencent；Tencent 失败则
  raise DataProviderError（fail closed），绝不静默回退到 Sina raw。
"""

from enum import Enum
from ..types import AdjustmentType


class UseCase(Enum):
    """使用场景 -> 决定默认复权方式"""
    TECHNICAL_ANALYSIS = "technical_analysis"   # 形态/指标/技术分析
    TRANSACTION_PRICE = "transaction_price"     # 真实成交价/交易决策


# 默认策略映射
DEFAULT_POLICY = {
    UseCase.TECHNICAL_ANALYSIS: AdjustmentType.FORWARD,   # qfq
    UseCase.TRANSACTION_PRICE: AdjustmentType.NONE,       # raw
}


def adjustment_for(use_case: UseCase) -> AdjustmentType:
    """返回该场景应使用的复权方式。"""
    return DEFAULT_POLICY[use_case]
