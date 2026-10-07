#!/usr/bin/env python3
"""
FIR-22 — THS Sector Index Resolver

同花顺板块指数解析器。

核心原则（与 FIR-15 对齐）：
- 同花顺是权威源，不推断或编造板块成员关系
- Fail-closed：如果板块代码无法解析、成分股数据不可用、新鲜度无法确认，
  抛出明确异常，绝不静默替换其他提供商
- 记录 provenance（source, sectorCode, sectorName, constituentUpdatedAt）
- Provider 架构：支持 iFinD MCP 回调、受控 snapshot、自定义 provider
- 提供 resolve_with_snapshot() 用于受控测试/CI/离线环境

使用方式：
    from core.sector_validation import THSSectorResolver, IFindMCPProvider

    # 1. 使用 iFinD MCP provider（通过回调传入实际的 MCP 调用函数）
    def ifind_search_fn(query):
        # 调用 mcp__ifind_finance__search_securities
        return result

    provider = IFindMCPProvider(search_fn=ifind_search_fn)
    resolver = THSSectorResolver(provider=provider)
    sector = resolver.resolve("886032")

    # 2. 受控 snapshot（用于测试）
    sector = resolver.resolve_with_snapshot(
        "886032", "固态电池", ["600519", "000858"],
        source="ths_ifind_snapshot_20261006"
    )
"""

import json
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

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
    DEFAULT_PROVENANCE_DIR,
)
from .reconciler import AuthoritativeSourceUnavailableError
from .types import (
    FreshnessLevel,
    ReconciliationMethod,
    SectorProvenance,
)


# ============ 权威日期验证（Fail-closed） ============

def validate_authoritative_date(as_of: str) -> str:
    """
    验证权威 as-of 日期。

    Fail-closed 原则：
    - 必须是可解析的 ISO8601 或 YYYY-MM-DD 格式
    - 不能是未来日期
    - 不接受空字符串或 None
    - 验证失败抛出 AuthoritativeSourceUnavailableError

    Args:
        as_of: 权威数据有效日期

    Returns:
        验证后的日期字符串（ISO8601 格式）

    Raises:
        AuthoritativeSourceUnavailableError: 日期缺失、无效或为未来日期
    """
    if not as_of or not isinstance(as_of, str):
        raise AuthoritativeSourceUnavailableError(
            "权威 as-of 日期缺失。必须提供明确的权威数据有效日期。"
        )

    as_of = as_of.strip()
    if not as_of:
        raise AuthoritativeSourceUnavailableError(
            "权威 as-of 日期为空。必须提供明确的权威数据有效日期。"
        )

    # 尝试解析日期
    parsed = None
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d', '%Y/%m/%d'):
        try:
            parsed = datetime.strptime(as_of, fmt)
            break
        except ValueError:
            continue

    if parsed is None:
        # 尝试 fromisoformat（Python 3.7+）
        try:
            parsed = datetime.fromisoformat(as_of.replace('Z', '+00:00'))
        except (ValueError, AttributeError):
            raise AuthoritativeSourceUnavailableError(
                f"权威 as-of 日期格式无效: '{as_of}'。"
                f"必须是 ISO8601 或 YYYY-MM-DD 格式。"
            )

    # 如果是 naive datetime，假设为 UTC
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    # 检查是否为未来日期
    now = datetime.now(timezone.utc)
    if parsed > now:
        raise AuthoritativeSourceUnavailableError(
            f"权威 as-of 日期为未来日期: '{as_of}'。"
            f"当前时间: {now.isoformat()}。"
            f"未来日期不能作为权威数据有效时间。"
        )

    return parsed.isoformat()


# ============ Provider 接口 ============

class THSDataProvider(ABC):
    """
    同花顺数据提供商接口。

    所有 provider 必须返回权威的同花顺板块数据。
    不允许使用非同花顺数据源（如东方财富）作为 ths_sector_index 的 provider。
    """

    @abstractmethod
    def fetch_sector(self, sector_code: str) -> Dict[str, Any]:
        """
        获取板块元数据和成分股。

        Args:
            sector_code: 同花顺板块指数代码（如 "886032"）

        Returns:
            {
                'sectorName': str,
                'constituents': List[str],
                'source': str,
                'asOf': str (ISO8601),
            }

        Raises:
            SectorResolutionError: 板块代码不存在或无法解析
            ConstituentUnavailableError: 成分股数据不可用
            AuthoritativeSourceUnavailableError: 权威源不可用
        """
        pass

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider 名称"""
        pass


class IFindMCPProvider(THSDataProvider):
    """
    同花顺 iFinD MCP 数据提供商。

    通过回调函数调用 iFinD MCP 的 search_securities 工具获取板块成分股。
    这是生产环境的权威数据源。

    使用方式：
        def ifind_search_fn(query, market="股票"):
            # 实际调用 mcp__ifind_finance__search_securities
            return {'securities': [...]}

        provider = IFindMCPProvider(search_fn=ifind_search_fn)
    """

    def __init__(self, search_fn: Callable[[str, str], Dict[str, Any]]):
        """
        初始化 iFinD MCP provider。

        Args:
            search_fn: 回调函数，接受 (query, market) 参数，返回 iFinD search_securities 结果。
                       函数签名: search_fn(query: str, market: str = "股票") -> dict
        """
        self._search_fn = search_fn

    @property
    def name(self) -> str:
        return "ths_ifind_mcp"

    def fetch_sector(self, sector_code: str) -> Dict[str, Any]:
        """
        通过 iFinD MCP 获取板块成分股。

        使用自然语言查询："同花顺板块代码{sector_code}的所有成分股"

        Args:
            sector_code: 同花顺板块指数代码

        Returns:
            标准化的板块数据

        Raises:
            AuthoritativeSourceUnavailableError: iFinD MCP 调用失败
            ConstituentUnavailableError: 成分股列表为空
        """
        query = f"同花顺板块代码{sector_code}的所有成分股"

        try:
            result = self._search_fn(query, "股票")
        except Exception as e:
            raise AuthoritativeSourceUnavailableError(
                f"iFinD MCP 调用失败: {e}"
            ) from e

        # 解析 iFinD search_securities 返回结果
        # 结果格式可能是 {'securities': [...]} 或 {'data': [...]} 或直接列表
        constituents = self._extract_constituents(result)

        if not constituents:
            raise ConstituentUnavailableError(
                f"iFinD MCP 返回板块 {sector_code} 的成分股列表为空。"
                f"请确认板块代码正确，或 iFinD 数据源是否包含该板块。"
            )

        # 尝试从结果中提取板块名称（fail-closed：缺失时为 None，不伪造）
        sector_name = self._extract_sector_name(result, sector_code)

        # 尝试从结果中提取权威 as-of 时间戳（fail-closed：缺失/无效/未来都抛出异常）
        raw_as_of = self._extract_as_of(result)
        if not raw_as_of:
            raise AuthoritativeSourceUnavailableError(
                f"iFinD MCP 响应中缺少权威 as-of 时间戳。"
                f"无法确认板块 {sector_code} 成分股数据的新鲜度。"
            )
        # 验证日期格式和有效性（缺失/无效/未来都 fail-closed）
        as_of = validate_authoritative_date(raw_as_of)

        return {
            'sectorName': sector_name,  # 可能为 None（权威源未返回名称）
            'constituents': constituents,
            'source': '同花顺 iFinD',
            'asOf': as_of,
        }

    def _extract_as_of(self, result: Any) -> Optional[str]:
        """从 iFinD 结果中提取权威 as-of 时间戳"""
        if isinstance(result, dict):
            for key in ('asOf', 'as_of', 'date', 'timestamp', 'updatedAt', 'updated_at', 'dataDate'):
                if key in result and result[key]:
                    val = str(result[key]).strip()
                    if val:
                        return val
        return None

    def _extract_constituents(self, result: Any) -> List[str]:
        """从 iFinD search_securities 结果中提取股票代码列表"""
        if not result:
            return []

        # 尝试多种可能的结果格式
        securities = None

        if isinstance(result, list):
            securities = result
        elif isinstance(result, dict):
            for key in ('securities', 'data', 'results', 'items', 'list'):
                if key in result and isinstance(result[key], list):
                    securities = result[key]
                    break

        if not securities:
            return []

        codes = []
        for item in securities:
            if isinstance(item, str):
                # 直接是股票代码字符串
                if len(item) == 6 and item.isdigit():
                    codes.append(item)
            elif isinstance(item, dict):
                # 字典格式，尝试提取代码字段
                for key in ('code', 'securityCode', 'stock_code', 'thscode', 'symbol', 'id'):
                    if key in item:
                        code = str(item[key])
                        # 清理可能的前缀（如 SH600519 -> 600519）
                        code = ''.join(c for c in code if c.isdigit())
                        if len(code) == 6:
                            codes.append(code)
                            break

        # 去重并保持顺序
        seen = set()
        unique_codes = []
        for code in codes:
            if code not in seen:
                seen.add(code)
                unique_codes.append(code)

        return unique_codes

    def _extract_sector_name(self, result: Any, sector_code: str) -> Optional[str]:
        """
        从 iFinD 结果中尝试提取板块名称。

        Fail-closed：如果权威响应中没有板块名称，返回 None，
        不使用 "板块{code}" 等占位符伪造元数据。
        调用方必须处理 None 的情况（显示为 UNKNOWN 或 fail-closed）。
        """
        if isinstance(result, dict):
            for key in ('sectorName', 'sector_name', 'name', 'plateName', 'conceptName'):
                if key in result and result[key]:
                    name = str(result[key]).strip()
                    if name and not name.startswith('板块'):
                        return name
        # 无法提取时返回 None（不伪造板块名称）
        return None


class SnapshotProvider(THSDataProvider):
    """
    受控 snapshot 数据提供商。

    用于测试、CI、离线环境。接受预先获取的板块数据 snapshot。
    snapshot 必须有明确的来源和时间戳。
    """

    def __init__(self, snapshots: Dict[str, Dict[str, Any]]):
        """
        初始化 snapshot provider。

        Args:
            snapshots: {sector_code: {sectorName, constituents, source, asOf}}
        """
        self._snapshots = snapshots

    @property
    def name(self) -> str:
        return "controlled_snapshot"

    def fetch_sector(self, sector_code: str) -> Dict[str, Any]:
        if sector_code not in self._snapshots:
            raise SectorResolutionError(
                f"Snapshot 中不包含板块 {sector_code}"
            )
        snap = self._snapshots[sector_code]

        # Fail-closed：snapshot 必须明确提供 source 和 asOf
        source = snap.get('source')
        if not source or source == 'controlled_snapshot':
            raise AuthoritativeSourceUnavailableError(
                f"Snapshot for sector {sector_code} missing explicit authoritative source. "
                f"Got: '{source}'. Must provide a real source identifier (e.g., '同花顺 iFinD')."
            )

        as_of = snap.get('asOf')
        if not as_of:
            raise AuthoritativeSourceUnavailableError(
                f"Snapshot for sector {sector_code} missing authoritative asOf timestamp. "
                f"Must provide the data's effective date."
            )
        # 验证日期格式和有效性（缺失/无效/未来都 fail-closed）
        as_of = validate_authoritative_date(as_of)

        sector_name = snap.get('sectorName')
        if not sector_name:
            # 不伪造板块名称，返回 None 让调用方处理
            sector_name = None

        return {
            'sectorName': sector_name,
            'constituents': list(snap.get('constituents', [])),
            'source': source,
            'asOf': as_of,
        }


# ============ Resolver ============

class THSSectorResolver:
    """
    同花顺板块指数解析器。

    负责：
    1. 验证板块指数代码格式
    2. 通过 provider 获取板块名称和成分股
    3. 记录 provenance（与 FIR-15 对齐）
    4. Fail-closed 错误处理
    5. 缓存恢复

    生产环境必须传入有效的 THSDataProvider（如 IFindMCPProvider）。
    没有 provider 时 resolve() 会 fail-closed。
    """

    def __init__(
        self,
        provider: Optional[THSDataProvider] = None,
        provenance_dir: Optional[str] = None,
    ):
        """
        初始化解析器。

        Args:
            provider: 同花顺数据提供商。生产环境必须传入 IFindMCPProvider。
                     为 None 时 resolve() 会 fail-closed（AuthoritativeSourceUnavailableError）。
            provenance_dir: provenance 存储目录（可选）
        """
        self.provider = provider
        self.provenance_dir = provenance_dir or DEFAULT_PROVENANCE_DIR

    def resolve(self, sector_code: str) -> DynamicSector:
        """
        解析同花顺板块指数代码，获取板块元数据和成分股。

        Fail-closed：
        - 代码格式无效 → SectorCodeValidationError
        - 没有 provider → AuthoritativeSourceUnavailableError
        - 板块代码不存在 → SectorResolutionError
        - 成分股数据不可用 → ConstituentUnavailableError

        Args:
            sector_code: 同花顺板块指数代码（如 "886032"）

        Returns:
            DynamicSector（包含板块名称、成分股、provenance）

        Raises:
            SectorCodeValidationError: 代码格式无效
            AuthoritativeSourceUnavailableError: 没有 provider 或权威源不可用
            SectorResolutionError: 板块解析失败
            ConstituentUnavailableError: 成分股数据不可用
        """
        # 1. 验证代码格式
        if not validate_ths_sector_code(sector_code):
            raise SectorCodeValidationError(
                f"无效的同花顺板块指数代码格式: '{sector_code}'。"
                f"应为 6 位数字（如 886032）。"
            )

        # 2. 检查 provider（fail-closed）
        if self.provider is None:
            raise AuthoritativeSourceUnavailableError(
                f"THSSectorResolver 未配置数据 provider，无法解析板块 '{sector_code}'。"
                f"生产环境必须传入 IFindMCPProvider 或其他 THSDataProvider 实现。"
                f"受控测试请使用 resolve_with_snapshot()。"
            )

        # 3. 通过 provider 获取板块数据
        sector_data = self.provider.fetch_sector(sector_code)

        # 4. 验证权威 as-of（fail-closed：不允许 fallback 到当前时间）
        as_of = sector_data.get('asOf')
        if not as_of:
            raise AuthoritativeSourceUnavailableError(
                f"Provider 返回的板块数据缺少权威 as-of 时间戳。"
                f"无法确认板块 {sector_code} 成分股数据的新鲜度。"
            )
        # 验证日期格式和有效性（缺失/无效/未来都 fail-closed）
        as_of = validate_authoritative_date(as_of)

        # 5. 验证权威板块名称（P1: 缺失名称标记为 unavailable，不伪造）
        sector_name = sector_data.get('sectorName')
        if not sector_name:
            # 权威源未返回板块名称 → 标记为 unavailable，不使用占位符
            sector_name = None

        # 6. 构建 DynamicSector
        sector = DynamicSector(
            sector_code=sector_code,
            sector_name=sector_name,  # 可能为 None（权威源未返回名称）
            source=sector_data.get('source', self.provider.name),
            constituents=sector_data.get('constituents', []),
            constituent_count=len(sector_data.get('constituents', [])),
        )

        # 7. 记录 provenance（与 FIR-15 对齐）
        self._save_provenance(sector, as_of)

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
        不依赖实时 provider。snapshot 必须有明确的来源和时间戳。

        Fail-closed：
        - source 不能是 'controlled_snapshot'（必须明确权威来源）
        - snapshot_date 必须明确提供（不能默认当前时间）
        - reconciliation_method 根据 source 自动判断：
          - 包含 "ths"/"ifind"/"同花顺" → THS_IFIND
          - 其他 → MANUAL

        Args:
            sector_code: 同花顺板块指数代码
            sector_name: 板块名称
            constituents: 成分股代码列表
            source: snapshot 来源标识（必须明确，如 "同花顺 iFinD snapshot 20261006"）
            snapshot_date: snapshot 日期（必须明确提供，YYYY-MM-DD）

        Returns:
            DynamicSector

        Raises:
            AuthoritativeSourceUnavailableError: source 或 snapshot_date 不明确
        """
        # 验证代码格式
        if not validate_ths_sector_code(sector_code):
            raise SectorCodeValidationError(
                f"无效的同花顺板块指数代码格式: '{sector_code}'。"
            )

        # Fail-closed：必须明确提供 source 和 snapshot_date
        if not source or source == 'controlled_snapshot':
            raise AuthoritativeSourceUnavailableError(
                f"resolve_with_snapshot requires explicit authoritative source. "
                f"Got: '{source}'. Must provide a real source identifier."
            )
        if not snapshot_date:
            raise AuthoritativeSourceUnavailableError(
                "resolve_with_snapshot requires explicit snapshot_date (YYYY-MM-DD). "
                "Cannot use current date as authoritative as-of."
            )
        # 验证 snapshot_date 格式和有效性（缺失/无效/未来都 fail-closed）
        validated_date = validate_authoritative_date(snapshot_date)
        snapshot_date = validated_date[:10]  # 提取日期部分

        # 根据 source 判断 reconciliation_method
        source_lower = source.lower()
        if any(kw in source_lower for kw in ('ths', 'ifind', '同花顺', 'tonghuashun')):
            recon_method = ReconciliationMethod.THS_IFIND
        else:
            recon_method = ReconciliationMethod.MANUAL

        # 构建 DynamicSector
        sector = DynamicSector(
            sector_code=sector_code,
            sector_name=sector_name,
            source=source,
            constituents=list(constituents),
            constituent_count=len(constituents),
        )

        # 记录 provenance（使用 snapshot 日期作为权威对账日期）
        as_of = f"{snapshot_date}T00:00:00+00:00"
        provenance = SectorProvenance(
            sector_name=sector_name,
            sector_code=sector_code,
            source=source,
            local_refresh_date=snapshot_date,
            authoritative_reconciliation_date=snapshot_date,
            reconciliation_method=recon_method,
            reconciliation_result=f"snapshot: matched={len(constituents)}, method={recon_method.value}",
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

        重新通过 provider 获取成分股列表，更新 provenance。

        Args:
            sector: 要刷新的 DynamicSector

        Returns:
            更新后的 DynamicSector

        Raises:
            AuthoritativeSourceUnavailableError: 没有 provider
        """
        if self.provider is None:
            raise AuthoritativeSourceUnavailableError(
                "THSSectorResolver 未配置数据 provider，无法刷新板块。"
            )

        # 重新获取数据
        sector_data = self.provider.fetch_sector(sector.sector_code)
        sector.update_constituents(
            sector_data.get('constituents', []),
            source=sector_data.get('source', self.provider.name),
        )
        as_of = sector_data.get('asOf', datetime.now(timezone.utc).isoformat())
        self._save_provenance(sector, as_of)
        return sector

    def _save_provenance(self, sector: DynamicSector, as_of: str):
        """
        保存板块 provenance（与 FIR-15 对齐）。

        根据 provider 类型自动判断 reconciliation_method：
        - IFindMCPProvider → THS_IFIND
        - SnapshotProvider → 根据 source 判断
        - 其他 → MANUAL

        Args:
            sector: DynamicSector
            as_of: 数据时间戳 ISO8601
        """
        today = datetime.now(timezone.utc).strftime('%Y-%m-%d')

        # 从 as_of 提取日期部分
        try:
            recon_date = as_of[:10] if len(as_of) >= 10 else today
        except (IndexError, TypeError):
            recon_date = today

        # 根据 provider 类型判断 reconciliation_method
        if isinstance(self.provider, IFindMCPProvider):
            recon_method = ReconciliationMethod.THS_IFIND
        elif isinstance(self.provider, SnapshotProvider):
            source_lower = (sector.source or '').lower()
            if any(kw in source_lower for kw in ('ths', 'ifind', '同花顺', 'tonghuashun')):
                recon_method = ReconciliationMethod.THS_IFIND
            else:
                recon_method = ReconciliationMethod.MANUAL
        else:
            recon_method = ReconciliationMethod.MANUAL

        if sector.provenance is None:
            sector.provenance = SectorProvenance(
                sector_name=sector.sector_name or sector.sector_code,
                sector_code=sector.sector_code,
                source=sector.source,
                local_refresh_date=today,
                authoritative_reconciliation_date=recon_date,
                reconciliation_method=recon_method,
                reconciliation_result=f"matched={sector.constituent_count}, asOf={as_of}, method={recon_method.value}",
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
            return self._build_cached_sector(sector_code, provenance)

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
                        return self._build_cached_sector(sector_code, provenance)
                except (json.JSONDecodeError, IOError, KeyError):
                    continue
        except OSError:
            pass

        return None

    def _build_cached_sector(self, sector_code: str, provenance: SectorProvenance) -> DynamicSector:
        """从 provenance 构建缓存的 DynamicSector（成分股为空，需要重新获取）"""
        sector = DynamicSector(
            sector_code=sector_code,
            sector_name=provenance.sector_name,
            source=provenance.source,
            constituents=[],  # 成分股不存储在 provenance 中
            constituent_count=provenance.total_constituents,
        )
        sector.provenance = provenance
        return sector


# ============ 配置加载/合并（从 dynamic_sector.py 迁移，保持向后兼容） ============

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
            remote_updated = merged[identity].updated_at or ''
            local_updated = sector.updated_at or ''
            if local_updated > remote_updated:
                merged[identity] = sector
        else:
            merged[identity] = sector

    # 按 sector_code 排序
    return sorted(merged.values(), key=lambda s: s.sector_code)
