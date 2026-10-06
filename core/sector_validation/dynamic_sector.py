#!/usr/bin/env python3
"""
FIR-22 — Dynamic THS Sector Index Model

动态同花顺板块指数扫描范围的数据模型。

与 FIR-15 的关系：
- 复用 SectorProvenance 的 provenance/freshness 规则
- DynamicSector 包含一个内嵌的 provenance 引用
- 成员关系验证与 FIR-15 对齐（离线 valid → UNVERIFIED，权威 matched → VERIFIED）

数据模型：
{
    "type": "ths_sector_index",
    "sectorCode": "886032",
    "sectorName": "固态电池",
    "source": "同花顺",
    "constituents": ["600519", "000858", ...],
    "constituentCount": 100,
    "updatedAt": "2026-10-06T00:00:00Z",
    "provenance": { ... SectorProvenance ... }
}

同步标识：type + sectorCode（不是仅 display name）
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .types import FreshnessLevel, SectorProvenance


class DynamicSectorError(Exception):
    """动态板块操作异常基类"""
    pass


class SectorCodeValidationError(DynamicSectorError):
    """板块指数代码格式无效"""
    pass


class SectorResolutionError(DynamicSectorError):
    """板块解析失败（权威源不可用或代码不存在）"""
    pass


class ConstituentUnavailableError(DynamicSectorError):
    """成分股数据不可用"""
    pass


def validate_ths_sector_code(sector_code: str) -> bool:
    """
    验证同花顺板块指数代码格式。

    同花顺概念板块代码通常为 6 位数字，以 88 开头（如 886015、885927）。
    行业板块代码可能以 88 开头，指数代码可能以 00/39 开头。

    对于 FIR-22，我们接受 6 位数字代码，具体有效性由权威源解析确认。

    Args:
        sector_code: 板块指数代码

    Returns:
        True 如果格式有效
    """
    if not sector_code or not isinstance(sector_code, str):
        return False
    code = sector_code.strip()
    # 6 位数字
    if not code.isdigit() or len(code) != 6:
        return False
    # 同花顺概念/行业板块通常以 88 开头
    # 也接受其他 6 位数字（由权威源最终确认）
    return True


@dataclass
class DynamicSector:
    """
    动态同花顺板块指数扫描范围。

    与手动自定义板块的区别：
    - type = "ths_sector_index"（手动板块 type = "manual"）
    - 有 sectorCode（同花顺板块指数代码）
    - 成分股由权威源动态获取，不是手动输入
    - 有 provenance 记录来源和新鲜度
    - 支持刷新成员关系

    同步标识：type + sectorCode
    """
    sector_code: str                              # 同花顺板块指数代码（如 "886032"）
    sector_name: str = ""                         # 板块名称（解析后填充）
    source: str = "同花顺"                         # 数据来源
    constituents: List[str] = field(default_factory=list)  # 成分股代码列表
    constituent_count: int = 0                    # 成分股数量
    updated_at: str = ""                          # 最后更新时间 ISO8601
    provenance: Optional[SectorProvenance] = None  # provenance 元数据
    error: str = ""                               # 错误信息（解析失败时）
    available: bool = True                        # 是否可用（成分股数据是否可获取）

    # 类型标识，用于同步和序列化
    type: str = "ths_sector_index"

    def __post_init__(self):
        """初始化后验证"""
        if not validate_ths_sector_code(self.sector_code):
            raise SectorCodeValidationError(
                f"无效的同花顺板块指数代码格式: '{self.sector_code}'。"
                f"应为 6 位数字（如 886032）。"
            )
        if not self.updated_at:
            self.updated_at = datetime.now(timezone.utc).isoformat()
        if self.constituents and not self.constituent_count:
            self.constituent_count = len(self.constituents)

    @property
    def sync_identity(self) -> str:
        """
        同步标识：type + sectorCode。

        用于跨设备同步时的唯一标识，避免仅按名称匹配导致重复。
        """
        return f"{self.type}:{self.sector_code}"

    def get_freshness_level(self, current_date: Optional[str] = None) -> FreshnessLevel:
        """
        获取板块新鲜度等级。

        基于 provenance 的权威对账时间。
        如果没有 provenance，返回 NOT_RECONCILED。

        Args:
            current_date: 当前日期（可选，默认今天）

        Returns:
            FreshnessLevel
        """
        if self.provenance is None:
            return FreshnessLevel.NOT_RECONCILED
        if current_date is None:
            current_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        return self.provenance.get_freshness_level(current_date)

    def is_fresh(self, max_age_days: int = 7) -> bool:
        """
        检查板块数据是否新鲜（权威对账在指定天数内）。

        Args:
            max_age_days: 最大新鲜天数（默认 7 天）

        Returns:
            True 如果新鲜
        """
        if not self.provenance or not self.provenance.authoritative_reconciliation_date:
            return False
        try:
            recon_date = datetime.strptime(
                self.provenance.authoritative_reconciliation_date, '%Y-%m-%d'
            ).replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            days = (now - recon_date).days
            return days <= max_age_days
        except (ValueError, TypeError):
            return False

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典"""
        return {
            'type': self.type,
            'sectorCode': self.sector_code,
            'sectorName': self.sector_name,
            'source': self.source,
            'constituents': self.constituents,
            'constituentCount': self.constituent_count,
            'updatedAt': self.updated_at,
            'provenance': self.provenance.to_dict() if self.provenance else None,
            'error': self.error,
            'available': self.available,
            'syncIdentity': self.sync_identity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'DynamicSector':
        """从字典反序列化"""
        provenance = None
        if data.get('provenance'):
            provenance = SectorProvenance.from_dict(data['provenance'])

        return cls(
            sector_code=str(data.get('sectorCode', data.get('sector_code', ''))),
            sector_name=str(data.get('sectorName', data.get('sector_name', ''))),
            source=str(data.get('source', '同花顺')),
            constituents=list(data.get('constituents', [])),
            constituent_count=int(data.get('constituentCount', data.get('constituent_count', 0))),
            updated_at=str(data.get('updatedAt', data.get('updated_at', ''))),
            provenance=provenance,
            error=str(data.get('error', '')),
            available=bool(data.get('available', True)),
        )

    def mark_unavailable(self, error: str):
        """
        标记板块为不可用（成分股数据无法获取）。

        Fail-closed：不使用猜测/过期的成分股列表。
        """
        self.available = False
        self.error = error
        self.constituents = []
        self.constituent_count = 0

    def update_constituents(self, constituents: List[str], source: str = "同花顺"):
        """
        更新成分股列表。

        Args:
            constituents: 新的成分股代码列表
            source: 数据来源
        """
        self.constituents = list(constituents)
        self.constituent_count = len(constituents)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        self.source = source
        self.available = True
        self.error = ""


@dataclass
class ManualCustomSector:
    """
    手动自定义板块（现有格式的封装）。

    与 DynamicSector 的区别：
    - type = "manual"
    - 没有 sectorCode
    - 成分股由用户手动输入
    - 没有 provenance（或 provenance 标记为 manual）

    同步标识：type + name
    """
    name: str                                  # 板块名称
    codes: List[str] = field(default_factory=list)  # 股票代码列表
    type: str = "manual"                       # 类型标识

    def __post_init__(self):
        if not self.name:
            raise ValueError("手动自定义板块名称不能为空")

    @property
    def sync_identity(self) -> str:
        """同步标识：type + name"""
        return f"{self.type}:{self.name}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            'type': self.type,
            'name': self.name,
            'codes': self.codes,
            'syncIdentity': self.sync_identity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ManualCustomSector':
        return cls(
            name=str(data.get('name', '')),
            codes=list(data.get('codes', [])),
        )


def normalize_sector_entry(entry: Any) -> Any:
    """
    规范化板块条目。

    支持两种格式：
    1. 旧格式：{name: [codes]}（字典，单个键值对）
    2. 新格式：DynamicSector 或 ManualCustomSector 对象/字典

    Args:
        entry: 板块条目

    Returns:
        DynamicSector 或 ManualCustomSector
    """
    if isinstance(entry, DynamicSector) or isinstance(entry, ManualCustomSector):
        return entry

    if isinstance(entry, dict):
        # 新格式：有 type 字段
        if 'type' in entry:
            if entry['type'] == 'ths_sector_index':
                return DynamicSector.from_dict(entry)
            elif entry['type'] == 'manual':
                return ManualCustomSector.from_dict(entry)
        # 旧格式：{name: [codes]} — 单个键值对
        if len(entry) == 1:
            name, codes = next(iter(entry.items()))
            if isinstance(codes, list):
                return ManualCustomSector(name=name, codes=codes)

    raise ValueError(f"无法识别的板块条目格式: {type(entry)}")
