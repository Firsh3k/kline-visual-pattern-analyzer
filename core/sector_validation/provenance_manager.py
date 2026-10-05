"""
FIR-15 — Sector Provenance Manager
管理板块数据的 provenance 元数据，包括来源、刷新日期、对账结果。

Provenance 文件存储在 sector_data/provenance/ 目录下，
每个板块一个 JSON 文件，命名格式：{板块名称}_provenance.json
"""

import json
import os
import re
from datetime import datetime, timezone
from typing import Dict, Optional, List

from .types import SectorProvenance, ReconciliationMethod, FreshnessLevel


# 默认 provenance 存储目录
DEFAULT_PROVENANCE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "sector_data", "provenance"
)


def _sanitize_filename(name: str) -> str:
    """将板块名称转换为安全的文件名"""
    # 移除或替换不安全的字符
    safe = re.sub(r'[\\/:*?"<>|]', '_', name)
    return safe


def get_provenance_path(sector_name: str, provenance_dir: Optional[str] = None) -> str:
    """获取板块 provenance 文件路径"""
    directory = provenance_dir or DEFAULT_PROVENANCE_DIR
    filename = f"{_sanitize_filename(sector_name)}_provenance.json"
    return os.path.join(directory, filename)


def load_provenance(
    sector_name: str,
    provenance_dir: Optional[str] = None,
) -> Optional[SectorProvenance]:
    """
    加载板块 provenance 元数据。

    Args:
        sector_name: 板块名称
        provenance_dir: provenance 存储目录（可选）

    Returns:
        SectorProvenance 或 None（文件不存在时）
    """
    path = get_provenance_path(sector_name, provenance_dir)
    if not os.path.exists(path):
        return None

    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return SectorProvenance.from_dict(data)
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        # provenance 文件损坏，返回 None
        print(f"Warning: Failed to load provenance for {sector_name}: {e}")
        return None


def save_provenance(
    provenance: SectorProvenance,
    provenance_dir: Optional[str] = None,
) -> str:
    """
    保存板块 provenance 元数据。

    Args:
        provenance: SectorProvenance 对象
        provenance_dir: provenance 存储目录（可选）

    Returns:
        保存的文件路径
    """
    directory = provenance_dir or DEFAULT_PROVENANCE_DIR
    os.makedirs(directory, exist_ok=True)

    path = get_provenance_path(provenance.sector_name, directory)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(provenance.to_dict(), f, ensure_ascii=False, indent=2)

    return path


def update_provenance_after_refresh(
    sector_name: str,
    sector_code: str,
    source: str,
    total_constituents: int,
    source_url: str = "",
    provenance_dir: Optional[str] = None,
) -> SectorProvenance:
    """
    刷新后更新 provenance。

    Args:
        sector_name: 板块名称
        sector_code: 板块代码
        source: 数据来源
        total_constituents: 成分股总数
        source_url: 数据来源 URL（可选）
        provenance_dir: provenance 存储目录（可选）

    Returns:
        更新后的 SectorProvenance
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today = now.strftime('%Y-%m-%d')

    # 尝试加载现有 provenance
    existing = load_provenance(sector_name, provenance_dir)

    if existing:
        # 更新现有 provenance
        existing.source = source
        existing.source_url = source_url
        existing.fetched_at = now_iso
        existing.refresh_date = today
        existing.total_constituents = total_constituents
        existing.sector_code = sector_code
        provenance = existing
    else:
        # 创建新 provenance
        provenance = SectorProvenance(
            sector_name=sector_name,
            sector_code=sector_code,
            source=source,
            source_url=source_url,
            fetched_at=now_iso,
            refresh_date=today,
            reconciliation_method=ReconciliationMethod.NOT_RECONCILED,
            total_constituents=total_constituents,
            version="1.0",
        )

    save_provenance(provenance, provenance_dir)
    return provenance


def update_provenance_after_reconciliation(
    sector_name: str,
    method: ReconciliationMethod,
    verified_count: int,
    reconciliation_date: Optional[str] = None,
    provenance_dir: Optional[str] = None,
) -> Optional[SectorProvenance]:
    """
    对账后更新 provenance。

    Args:
        sector_name: 板块名称
        method: 对账方法
        verified_count: 已验证数量
        reconciliation_date: 对账日期（可选，默认今天）
        provenance_dir: provenance 存储目录（可选）

    Returns:
        更新后的 SectorProvenance 或 None（provenance 不存在时）
    """
    provenance = load_provenance(sector_name, provenance_dir)
    if not provenance:
        return None

    if reconciliation_date is None:
        reconciliation_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    provenance.reconciliation_method = method
    provenance.reconciliation_date = reconciliation_date
    provenance.verified_count = verified_count

    save_provenance(provenance, provenance_dir)
    return provenance


def check_freshness(
    sector_name: str,
    current_date: Optional[str] = None,
    max_fresh_days: int = 7,
    max_aging_days: int = 30,
    provenance_dir: Optional[str] = None,
) -> FreshnessLevel:
    """
    检查板块数据的新鲜度。

    Args:
        sector_name: 板块名称
        current_date: 当前日期（可选，默认今天）
        max_fresh_days: 新鲜阈值（天）
        max_aging_days: 老化阈值（天）
        provenance_dir: provenance 存储目录（可选）

    Returns:
        FreshnessLevel
    """
    provenance = load_provenance(sector_name, provenance_dir)
    if not provenance:
        return FreshnessLevel.UNKNOWN

    if current_date is None:
        current_date = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    return provenance.get_freshness_level(current_date, max_fresh_days, max_aging_days)


def list_all_provenances(
    provenance_dir: Optional[str] = None,
) -> Dict[str, SectorProvenance]:
    """
    列出所有板块的 provenance。

    Args:
        provenance_dir: provenance 存储目录（可选）

    Returns:
        {板块名称: SectorProvenance}
    """
    directory = provenance_dir or DEFAULT_PROVENANCE_DIR
    if not os.path.exists(directory):
        return {}

    result = {}
    for filename in os.listdir(directory):
        if filename.endswith('_provenance.json'):
            sector_name = filename[:-len('_provenance.json')]
            provenance = load_provenance(sector_name, directory)
            if provenance:
                result[sector_name] = provenance

    return result


def get_stale_sectors(
    current_date: Optional[str] = None,
    max_stale_days: int = 30,
    provenance_dir: Optional[str] = None,
) -> List[str]:
    """
    获取所有过期的板块名称列表。

    Args:
        current_date: 当前日期（可选）
        max_stale_days: 过期阈值（天）
        provenance_dir: provenance 存储目录（可选）

    Returns:
        过期板块名称列表
    """
    all_provenances = list_all_provenances(provenance_dir)
    stale = []

    for name, provenance in all_provenances.items():
        freshness = check_freshness(name, current_date, max_aging_days=max_stale_days,
                                     provenance_dir=provenance_dir)
        if freshness == FreshnessLevel.STALE:
            stale.append(name)

    return stale
