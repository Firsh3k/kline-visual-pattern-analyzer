#!/usr/bin/env python3
"""
FIR-22 — THS Sector Index Resolver

同花顺板块指数解析器。

核心原则（与 FIR-15 对齐）：
- 同花顺是权威源，不推断或编造板块成员关系
- Fail-closed：如果板块代码无法解析、成分股数据不可用、新鲜度无法确认，
  抛出明确异常，绝不静默替换其他提供商
- 记录 provenance（source, sectorCode, sectorName, constituentUpdatedAt）
- 在有 iFinD MCP 的环境中实际调用，在没有的环境中 fail-closed
- 提供 resolve_with_snapshot() 用于受控测试/CI/离线环境

使用方式：
    from core.sector_validation import THSSectorResolver

    resolver = THSSectorResolver()

    # 1. 验证并解析板块元数据
    try:
        sector = resolver.resolve("886032")
        print(f"板块: {sector.sector_name}, 成分股: {sector.constituent_count}")
    except SectorResolutionError as e:
        print(f"解析失败: {e}")

    # 2. 受控 snapshot（用于测试）
    sector = resolver.resolve_with_snapshot(
        "886032", "固态电池", ["600519", "000858"],
        source="ths_ifind_snapshot_20261006"
    )
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .dynamic_sector import (
    DynamicSector,
    SectorCodeValidationError,
    SectorResolutionError,
    ConstituentUnavailableError,
    validate_ths_sector_code,
)
from .provenance_manager import (
    load_provenance,
    save_provenance,
    update_provenance_after_reconciliation,
    DEFAULT_PROVENANCE_DIR,
)
from .reconciler import AuthoritativeSourceUnavailableError
from .types import (
    FreshnessLevel,
    ReconciliationMethod,
    SectorProvenance,
)


class THSSectorResolver:
    """
    同花顺板块指数解析器。

    负责：
    1. 验证板块指数代码格式
    2. 解析板块名称和元数据
    3. 获取成分股列表
    4. 记录 provenance
    5. Fail-closed 错误处理
    """

    def __init__(
        self,
        provenance_dir: Optional[str] = None,
        ifind_available: Optional[bool] = None,
    ):
        """
        初始化解析器。

        Args:
            provenance_dir: provenance 存储目录（可选，默认 DEFAULT_PROVENANCE_DIR）
            ifind_available: iFinD MCP 是否可用（可选，None 表示自动检测）
        """
        self.provenance_dir = provenance_dir or DEFAULT_PROVENANCE_DIR
        self._ifind_available = ifind_available

    def _detect_ifind_availability(self) -> bool:
        """
        检测 iFinD MCP 工具是否可用。

        在当前环境中，iFinD MCP 工具通常不可用（需要特定的运行时连接）。
        子类或调用方可以通过 ifind_available 参数覆盖。

        Returns:
            True 如果 iFinD MCP 可用
        """
        if self._ifind_available is not None:
            return self._ifind_available

        # 自动检测：尝试导入 iFinD 相关模块
        # 在标准 Python 环境中，iFinD MCP 工具不可用
        # 实际可用性需要在有 MCP 连接的运行时中确认
        try:
            # 尝试检测是否在 MCP 运行时环境中
            # 这里使用保守策略：默认不可用，需要显式启用
            return False
        except Exception:
            return False

    def resolve(self, sector_code: str) -> DynamicSector:
        """
        解析同花顺板块指数代码，获取板块元数据和成分股。

        Fail-closed：
        - 代码格式无效 → SectorCodeValidationError
        - iFinD MCP 不可用 → AuthoritativeSourceUnavailableError
        - 板块代码不存在 → SectorResolutionError
        - 成分股数据不可用 → ConstituentUnavailableError

        Args:
            sector_code: 同花顺板块指数代码（如 "886032"）

        Returns:
            DynamicSector（包含板块名称、成分股、provenance）

        Raises:
            SectorCodeValidationError: 代码格式无效
            AuthoritativeSourceUnavailableError: iFinD MCP 不可用
            SectorResolutionError: 板块解析失败
            ConstituentUnavailableError: 成分股数据不可用
        """
        # 1. 验证代码格式
        if not validate_ths_sector_code(sector_code):
            raise SectorCodeValidationError(
                f"无效的同花顺板块指数代码格式: '{sector_code}'。"
                f"应为 6 位数字（如 886032）。"
            )

        # 2. 检查 iFinD MCP 可用性（fail-closed）
        if not self._detect_ifind_availability():
            raise AuthoritativeSourceUnavailableError(
                f"同花顺 iFinD 权威源不可用，无法解析板块 '{sector_code}'。"
                f"请在有 iFinD MCP 工具的环境中运行，或使用 resolve_with_snapshot() 进行受控对账。"
            )

        # 3. 实际调用 iFinD MCP 获取板块数据
        # 注意：这部分需要在有 iFinD MCP 的运行时中实现
        # 当前环境中不会执行到这里（因为 _detect_ifind_availability 返回 False）
        sector_data = self._fetch_from_ifind(sector_code)

        # 4. 构建 DynamicSector
        sector = DynamicSector(
            sector_code=sector_code,
            sector_name=sector_data.get('sectorName', ''),
            source='同花顺',
            constituents=sector_data.get('constituents', []),
            constituent_count=len(sector_data.get('constituents', [])),
        )

        # 5. 记录 provenance
        self._save_provenance(sector)

        return sector

    def resolve_with_snapshot(
        self,
        sector_code: str,
        sector_name: str,
        constituents: List[str],
        source: str = "controlled_snapshot",
        snapshot_date: Optional[str] = None,
    ) -> DynamicSector:
        """
        使用受控 snapshot 解析板块（用于测试/CI/离线环境）。

        与 resolve() 不同，此函数接受预先获取的板块数据，
        不依赖实时 iFinD MCP 连接。snapshot 必须有明确的来源和时间戳。

        Args:
            sector_code: 同花顺板块指数代码
            sector_name: 板块名称
            constituents: 成分股代码列表
            source: snapshot 来源标识（如 "ths_ifind_snapshot_20261006"）
            snapshot_date: snapshot 日期（可选，默认今天）

        Returns:
            DynamicSector
        """
        # 验证代码格式
        if not validate_ths_sector_code(sector_code):
            raise SectorCodeValidationError(
                f"无效的同花顺板块指数代码格式: '{sector_code}'。"
            )

        if snapshot_date is None:
            snapshot_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        # 构建 DynamicSector
        sector = DynamicSector(
            sector_code=sector_code,
            sector_name=sector_name,
            source=source,
            constituents=list(constituents),
            constituent_count=len(constituents),
        )

        # 记录 provenance（使用 snapshot 日期作为权威对账日期）
        provenance = SectorProvenance(
            sector_name=sector_name,
            sector_code=sector_code,
            source=source,
            local_refresh_date=snapshot_date,
            authoritative_reconciliation_date=snapshot_date,
            reconciliation_method=ReconciliationMethod.THS_IFIND,
            reconciliation_result=f"snapshot: matched={len(constituents)}",
            total_constituents=len(constituents),
            verified_count=len(constituents),
        )
        sector.provenance = provenance

        # 保存 provenance
        save_provenance(provenance, self.provenance_dir)

        return sector

    def refresh(self, sector: DynamicSector) -> DynamicSector:
        """
        刷新板块成分股数据。

        重新从权威源获取成分股列表，更新 provenance。

        Args:
            sector: 要刷新的 DynamicSector

        Returns:
            更新后的 DynamicSector

        Raises:
            AuthoritativeSourceUnavailableError: iFinD MCP 不可用
        """
        if not self._detect_ifind_availability():
            raise AuthoritativeSourceUnavailableError(
                f"同花顺 iFinD 权威源不可用，无法刷新板块 '{sector.sector_code}'。"
            )

        # 重新获取数据
        sector_data = self._fetch_from_ifind(sector.sector_code)
        sector.update_constituents(
            sector_data.get('constituents', []),
            source='同花顺',
        )
        self._save_provenance(sector)
        return sector

    def _fetch_from_ifind(self, sector_code: str) -> Dict[str, Any]:
        """
        从 iFinD MCP 获取板块数据。

        注意：此方法需要在有 iFinD MCP 工具的运行时中实现。
        当前环境中不会执行到这里（因为 _detect_ifind_availability 返回 False）。

        实际实现应该：
        1. 调用 iFinD MCP 的板块成分股接口
        2. 解析返回的板块名称和成分股列表
        3. 返回标准化的数据结构

        Args:
            sector_code: 板块指数代码

        Returns:
            {'sectorName': str, 'constituents': List[str]}
        """
        # 占位实现：在有 iFinD MCP 的环境中替换为实际调用
        # 这里不应该被执行到（因为 fail-closed 检查会先抛出异常）
        raise AuthoritativeSourceUnavailableError(
            "_fetch_from_ifind 未在当前环境中实现。"
            "需要在有 iFinD MCP 工具的运行时中实现此方法。"
        )

    def _save_provenance(self, sector: DynamicSector):
        """
        保存板块 provenance。

        Args:
            sector: DynamicSector
        """
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        if sector.provenance is None:
            sector.provenance = SectorProvenance(
                sector_name=sector.sector_name or sector.sector_code,
                sector_code=sector.sector_code,
                source=sector.source,
                local_refresh_date=today,
                authoritative_reconciliation_date=today,
                reconciliation_method=ReconciliationMethod.THS_IFIND,
                reconciliation_result=f"matched={sector.constituent_count}",
                total_constituents=sector.constituent_count,
                verified_count=sector.constituent_count,
            )

        save_provenance(sector.provenance, self.provenance_dir)

    def get_cached_sector(self, sector_code: str) -> Optional[DynamicSector]:
        """
        从缓存/provenance 中获取已解析的板块。

        如果板块之前解析过且 provenance 存在，可以从 provenance 恢复基本信息。
        但成分股列表需要重新获取（provenance 不存储成分股）。

        Args:
            sector_code: 板块指数代码

        Returns:
            DynamicSector（如果缓存存在）或 None
        """
        # 尝试直接用 sector_code 作为文件名查找
        provenance = load_provenance(sector_code, self.provenance_dir)
        if provenance is not None:
            sector = DynamicSector(
                sector_code=sector_code,
                sector_name=provenance.sector_name,
                source=provenance.source,
                constituents=[],
                constituent_count=provenance.total_constituents,
            )
            sector.provenance = provenance
            return sector

        # 遍历 provenance 目录，按 sector_code 字段匹配
        try:
            if not os.path.isdir(self.provenance_dir):
                return None
            for filename in os.listdir(self.provenance_dir):
                if not filename.endswith('_provenance.json'):
                    continue
                filepath = os.path.join(self.provenance_dir, filename)
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    # 同时支持 snake_case 和 camelCase
                    file_sector_code = data.get('sector_code') or data.get('sectorCode')
                    if file_sector_code == sector_code:
                        provenance = SectorProvenance.from_dict(data)
                        sector = DynamicSector(
                            sector_code=sector_code,
                            sector_name=provenance.sector_name,
                            source=provenance.source,
                            constituents=[],
                            constituent_count=provenance.total_constituents,
                        )
                        sector.provenance = provenance
                        return sector
                except (json.JSONDecodeError, IOError, KeyError):
                    continue
        except OSError:
            pass

        return None


def load_sectors_from_config(config_data: Any) -> Tuple[List[DynamicSector], List[Any]]:
    """
    从配置数据中加载板块列表。

    支持混合格式：
    - 动态板块（ths_sector_index）
    - 手动自定义板块（manual）
    - 旧格式 {name: [codes]}

    Args:
        config_data: 配置数据（列表或字典）

    Returns:
        (dynamic_sectors, manual_sectors) — 动态板块列表和手动板块列表
    """
    from .dynamic_sector import normalize_sector_entry, ManualCustomSector

    dynamic_sectors: List[DynamicSector] = []
    manual_sectors: List[ManualCustomSector] = []

    if config_data is None:
        return dynamic_sectors, manual_sectors

    # 处理列表格式
    if isinstance(config_data, list):
        for entry in config_data:
            try:
                normalized = normalize_sector_entry(entry)
                if isinstance(normalized, DynamicSector):
                    dynamic_sectors.append(normalized)
                elif isinstance(normalized, ManualCustomSector):
                    manual_sectors.append(normalized)
            except (ValueError, SectorCodeValidationError):
                continue  # 跳过无法识别的条目

    # 处理旧字典格式 {name: [codes]}
    elif isinstance(config_data, dict):
        for name, codes in config_data.items():
            if isinstance(codes, list):
                manual_sectors.append(ManualCustomSector(name=name, codes=codes))

    return dynamic_sectors, manual_sectors


def merge_dynamic_sectors(
    remote_sectors: List[DynamicSector],
    local_sectors: List[DynamicSector],
) -> List[DynamicSector]:
    """
    合并远程和本地的动态板块列表（跨设备同步）。

    合并规则：
    - 使用 sync_identity（type + sectorCode）作为唯一标识
    - 同一板块代码不重复
    - 如果远程和本地都有同一板块，使用更新时间较新的版本
    - 远程独有的板块保留
    - 本地独有的板块保留

    Args:
        remote_sectors: 远程板块列表
        local_sectors: 本地板块列表

    Returns:
        合并后的板块列表（去重，按 sector_code 排序）
    """
    merged: Dict[str, DynamicSector] = {}

    # 先加入远程板块
    for sector in remote_sectors:
        merged[sector.sync_identity] = sector

    # 再加入本地板块（同一标识时使用更新时间较新的版本）
    for sector in local_sectors:
        identity = sector.sync_identity
        if identity in merged:
            # 比较更新时间，使用较新的版本
            remote_updated = merged[identity].updated_at or ''
            local_updated = sector.updated_at or ''
            if local_updated > remote_updated:
                merged[identity] = sector
        else:
            merged[identity] = sector

    # 按 sector_code 排序
    return sorted(merged.values(), key=lambda s: s.sector_code)
