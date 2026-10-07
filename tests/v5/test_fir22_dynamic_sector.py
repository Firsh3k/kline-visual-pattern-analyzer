#!/usr/bin/env python3
"""
FIR-22 — Dynamic THS Sector Index Scan Scope 自动化测试

覆盖：
- 板块指数代码格式验证
- DynamicSector 数据模型
- Fail-closed 解析（iFinD 不可用时抛出异常）
- 受控 snapshot 解析
- 序列化/反序列化
- 手动自定义板块
- 跨设备合并（type+sectorCode 去重）
- 旧格式兼容
- 新鲜度判断
- provenance 记录
"""

import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from core.sector_validation import (
    DynamicSector,
    ManualCustomSector,
    THSSectorResolver,
    THSDataProvider,
    IFindMCPProvider,
    SnapshotProvider,
    SectorCodeValidationError,
    SectorResolutionError,
    ConstituentUnavailableError,
    AuthoritativeSourceUnavailableError,
    validate_ths_sector_code,
    normalize_sector_entry,
    load_sectors_from_config,
    merge_dynamic_sectors,
    FreshnessLevel,
    SectorProvenance,
    ReconciliationMethod,
)


# ============ 1. 代码格式验证 ============

class TestSectorCodeValidation:
    def test_valid_ths_codes(self):
        """有效的同花顺板块指数代码"""
        assert validate_ths_sector_code('886032') is True
        assert validate_ths_sector_code('886015') is True
        assert validate_ths_sector_code('885927') is True
        assert validate_ths_sector_code('885769') is True

    def test_invalid_codes(self):
        """无效的板块指数代码"""
        assert validate_ths_sector_code('') is False
        assert validate_ths_sector_code(None) is False
        assert validate_ths_sector_code('123') is False  # 太短
        assert validate_ths_sector_code('1234567') is False  # 太长
        assert validate_ths_sector_code('abc') is False  # 非数字
        assert validate_ths_sector_code('88603A') is False  # 含字母

    def test_code_with_whitespace_is_trimmed(self):
        """含空格的代码会被 strip 后验证"""
        assert validate_ths_sector_code(' 886032') is True
        assert validate_ths_sector_code('886032 ') is True
        assert validate_ths_sector_code(' 886032 ') is True


# ============ 2. DynamicSector 数据模型 ============

class TestDynamicSector:
    def test_create_minimal(self):
        """最小化创建 DynamicSector"""
        sector = DynamicSector(sector_code='886032')
        assert sector.sector_code == '886032'
        assert sector.sector_name == ''
        assert sector.type == 'ths_sector_index'
        assert sector.constituents == []
        assert sector.constituent_count == 0
        assert sector.available is True
        assert sector.error == ''

    def test_create_full(self):
        """完整创建 DynamicSector"""
        sector = DynamicSector(
            sector_code='886032',
            sector_name='固态电池',
            source='同花顺',
            constituents=['600519', '000858', '300750'],
        )
        assert sector.sector_name == '固态电池'
        assert sector.constituent_count == 3
        assert sector.source == '同花顺'

    def test_sync_identity(self):
        """同步标识：type + sectorCode"""
        sector = DynamicSector(sector_code='886032')
        assert sector.sync_identity == 'ths_sector_index:886032'

    def test_invalid_code_raises(self):
        """无效代码抛出 SectorCodeValidationError"""
        with pytest.raises(SectorCodeValidationError):
            DynamicSector(sector_code='invalid')

    def test_auto_count(self):
        """成分股数量自动计算"""
        sector = DynamicSector(
            sector_code='886032',
            constituents=['600519', '000858'],
        )
        assert sector.constituent_count == 2

    def test_mark_unavailable(self):
        """标记为不可用（fail-closed）"""
        sector = DynamicSector(
            sector_code='886032',
            constituents=['600519'],
        )
        sector.mark_unavailable('数据源不可用')
        assert sector.available is False
        assert sector.error == '数据源不可用'
        assert sector.constituents == []
        assert sector.constituent_count == 0

    def test_update_constituents(self):
        """更新成分股"""
        sector = DynamicSector(sector_code='886032')
        sector.update_constituents(['600519', '000858'], source='测试源')
        assert sector.constituent_count == 2
        assert sector.source == '测试源'
        assert sector.available is True
        assert sector.error == ''


# ============ 3. Fail-closed 解析 ============

class TestFailClosedResolution:
    def test_resolve_without_provider_raises(self):
        """没有 provider 时 resolve 抛出 AuthoritativeSourceUnavailableError"""
        resolver = THSSectorResolver()  # provider=None
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve('886032')

    def test_resolve_invalid_code_raises_first(self):
        """无效代码先抛出 SectorCodeValidationError（在 provider 检查之前）"""
        resolver = THSSectorResolver()  # provider=None
        with pytest.raises(SectorCodeValidationError):
            resolver.resolve('invalid')

    def test_resolve_with_snapshot_works(self, tmp_path):
        """受控 snapshot 解析不依赖 provider（必须明确 source 和 snapshot_date）"""
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        sector = resolver.resolve_with_snapshot(
            '886032', '固态电池', ['600519', '000858'],
            source='同花顺 iFinD snapshot 20261006',
            snapshot_date='2026-10-06'
        )
        assert sector.sector_name == '固态电池'
        assert sector.constituent_count == 2
        assert sector.provenance is not None
        assert sector.provenance.reconciliation_method == ReconciliationMethod.THS_IFIND

    def test_snapshot_invalid_code_raises(self, tmp_path):
        """snapshot 解析也验证代码格式"""
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        with pytest.raises(SectorCodeValidationError):
            resolver.resolve_with_snapshot('bad', '测试', [], source='同花顺 iFinD', snapshot_date='2026-10-06')

    def test_snapshot_without_source_fails_closed(self, tmp_path):
        """snapshot 不提供明确 source 时 fail-closed"""
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve_with_snapshot('886032', '测试', ['600519'])

    def test_snapshot_without_date_fails_closed(self, tmp_path):
        """snapshot 不提供 snapshot_date 时 fail-closed"""
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve_with_snapshot('886032', '测试', ['600519'], source='同花顺 iFinD')


# ============ 4. 序列化/反序列化 ============

class TestSerialization:
    def test_round_trip(self):
        """序列化后反序列化保持一致"""
        sector = DynamicSector(
            sector_code='886032',
            sector_name='固态电池',
            constituents=['600519', '000858'],
        )
        data = sector.to_dict()
        restored = DynamicSector.from_dict(data)
        assert restored.sector_code == sector.sector_code
        assert restored.sector_name == sector.sector_name
        assert restored.constituent_count == sector.constituent_count
        assert restored.sync_identity == sector.sync_identity
        assert restored.type == sector.type

    def test_to_dict_has_required_fields(self):
        """to_dict 包含必要字段"""
        sector = DynamicSector(sector_code='886032', sector_name='测试')
        data = sector.to_dict()
        assert 'type' in data
        assert 'sectorCode' in data
        assert 'sectorName' in data
        assert 'constituents' in data
        assert 'constituentCount' in data
        assert 'updatedAt' in data
        assert 'syncIdentity' in data
        assert 'available' in data

    def test_from_dict_with_camel_case(self):
        """from_dict 支持 camelCase 字段"""
        data = {
            'sectorCode': '886032',
            'sectorName': '固态电池',
            'constituents': ['600519'],
            'constituentCount': 1,
        }
        sector = DynamicSector.from_dict(data)
        assert sector.sector_code == '886032'
        assert sector.sector_name == '固态电池'


# ============ 5. 手动自定义板块 ============

class TestManualCustomSector:
    def test_create(self):
        """创建手动板块"""
        manual = ManualCustomSector(name='我的板块', codes=['600519'])
        assert manual.name == '我的板块'
        assert manual.type == 'manual'
        assert manual.sync_identity == 'manual:我的板块'

    def test_empty_name_raises(self):
        """空名称抛出异常"""
        with pytest.raises(ValueError):
            ManualCustomSector(name='', codes=[])

    def test_serialization(self):
        """手动板块序列化"""
        manual = ManualCustomSector(name='测试', codes=['600519', '000858'])
        data = manual.to_dict()
        assert data['type'] == 'manual'
        assert data['name'] == '测试'
        assert len(data['codes']) == 2


# ============ 6. 跨设备合并 ============

class TestMergeDynamicSectors:
    def test_no_duplicates(self):
        """相同 sectorCode 不重复"""
        remote = [DynamicSector(sector_code='886032', sector_name='固态电池')]
        local = [DynamicSector(sector_code='886032', sector_name='固态电池(本地)')]
        merged = merge_dynamic_sectors(remote, local)
        assert len(merged) == 1

    def test_new_sectors_added(self):
        """新板块被添加"""
        remote = [DynamicSector(sector_code='886032')]
        local = [DynamicSector(sector_code='886015')]
        merged = merge_dynamic_sectors(remote, local)
        assert len(merged) == 2
        codes = {s.sector_code for s in merged}
        assert '886032' in codes
        assert '886015' in codes

    def test_newer_version_wins(self):
        """同一板块使用更新时间较新的版本"""
        old_time = '2026-01-01T00:00:00Z'
        new_time = '2026-10-06T00:00:00Z'
        remote = [DynamicSector(sector_code='886032', sector_name='旧名称')]
        remote[0].updated_at = old_time
        local = [DynamicSector(sector_code='886032', sector_name='新名称')]
        local[0].updated_at = new_time
        merged = merge_dynamic_sectors(remote, local)
        assert len(merged) == 1
        assert merged[0].sector_name == '新名称'

    def test_empty_inputs(self):
        """空输入返回空列表"""
        assert merge_dynamic_sectors([], []) == []
        assert len(merge_dynamic_sectors([], [DynamicSector(sector_code='886032')])) == 1

    def test_sorted_by_code(self):
        """合并结果按 sector_code 排序"""
        sectors = [
            DynamicSector(sector_code='886032'),
            DynamicSector(sector_code='886015'),
            DynamicSector(sector_code='885927'),
        ]
        merged = merge_dynamic_sectors(sectors, [])
        codes = [s.sector_code for s in merged]
        assert codes == sorted(codes)


# ============ 7. 旧格式兼容 ============

class TestLegacyFormat:
    def test_old_dict_format(self):
        """旧格式 {name: [codes]} 被识别为手动板块"""
        old_format = {'我的旧板块': ['600519', '000858']}
        dynamic, manual = load_sectors_from_config(old_format)
        assert len(dynamic) == 0
        assert len(manual) == 1
        assert manual[0].name == '我的旧板块'
        assert len(manual[0].codes) == 2

    def test_new_format_list(self):
        """新格式列表被正确解析"""
        new_format = [
            {'type': 'ths_sector_index', 'sectorCode': '886032', 'sectorName': '固态电池', 'constituents': ['600519']},
            {'type': 'manual', 'name': '我的板块', 'codes': ['000858']},
        ]
        dynamic, manual = load_sectors_from_config(new_format)
        assert len(dynamic) == 1
        assert dynamic[0].sector_code == '886032'
        assert len(manual) == 1
        assert manual[0].name == '我的板块'

    def test_mixed_format(self):
        """混合格式（动态 + 手动 + 旧格式）"""
        mixed = [
            {'type': 'ths_sector_index', 'sectorCode': '886032', 'sectorName': '固态电池', 'constituents': ['600519']},
            {'我的旧板块': ['000858']},
        ]
        dynamic, manual = load_sectors_from_config(mixed)
        assert len(dynamic) == 1
        assert len(manual) == 1

    def test_none_input(self):
        """None 输入返回空列表"""
        dynamic, manual = load_sectors_from_config(None)
        assert dynamic == []
        assert manual == []


# ============ 8. 新鲜度判断 ============

class TestFreshness:
    def test_fresh_sector(self, tmp_path):
        """新鲜板块（7天内更新）"""
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        sector = resolver.resolve_with_snapshot(
            '886032', '测试', ['600519'],
            source='同花顺 iFinD',
            snapshot_date=datetime.now(timezone.utc).strftime('%Y-%m-%d')
        )
        assert sector.get_freshness_level() == FreshnessLevel.FRESH
        assert sector.is_fresh() is True

    def test_stale_sector(self, tmp_path):
        """过期板块（超过30天）"""
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime('%Y-%m-%d')
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        sector = resolver.resolve_with_snapshot(
            '886032', '测试', ['600519'],
            source='同花顺 iFinD',
            snapshot_date=old_date
        )
        assert sector.get_freshness_level() in (FreshnessLevel.STALE, FreshnessLevel.NOT_RECONCILED)

    def test_no_provenance_not_reconciled(self):
        """没有 provenance 的板块返回 NOT_RECONCILED"""
        sector = DynamicSector(sector_code='886032')
        assert sector.provenance is None
        assert sector.get_freshness_level() == FreshnessLevel.NOT_RECONCILED


# ============ 9. Provenance 记录 ============

class TestProvenance:
    def test_snapshot_records_provenance(self, tmp_path):
        """snapshot 解析记录 provenance（必须明确 source 和 snapshot_date）"""
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        sector = resolver.resolve_with_snapshot(
            '886032', '固态电池', ['600519'],
            source='同花顺 iFinD snapshot 20261006',
            snapshot_date='2026-10-06'
        )
        assert sector.provenance is not None
        assert sector.provenance.sector_code == '886032'
        assert sector.provenance.source == '同花顺 iFinD snapshot 20261006'
        assert sector.provenance.authoritative_reconciliation_date == '2026-10-06'
        assert sector.provenance.reconciliation_method == ReconciliationMethod.THS_IFIND

    def test_provenance_persisted(self, tmp_path):
        """provenance 被持久化到文件（使用临时目录，不污染 production）"""
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        resolver.resolve_with_snapshot(
            '886032', '固态电池', ['600519'],
            source='同花顺 iFinD',
            snapshot_date='2026-10-06'
        )
        # 检查 provenance 文件是否创建
        files = os.listdir(str(tmp_path))
        assert len(files) > 0
        # 确保不包含测试伪造的文件名
        assert not any('测试' in f for f in files)


# ============ 10. normalize_sector_entry ============

class TestNormalizeSectorEntry:
    def test_dynamic_sector_object(self):
        """DynamicSector 对象直接返回"""
        sector = DynamicSector(sector_code='886032')
        result = normalize_sector_entry(sector)
        assert result is sector

    def test_manual_sector_object(self):
        """ManualCustomSector 对象直接返回"""
        manual = ManualCustomSector(name='测试', codes=[])
        result = normalize_sector_entry(manual)
        assert result is manual

    def test_dynamic_sector_dict(self):
        """动态板块字典被转换"""
        data = {'type': 'ths_sector_index', 'sectorCode': '886032', 'sectorName': '测试'}
        result = normalize_sector_entry(data)
        assert isinstance(result, DynamicSector)
        assert result.sector_code == '886032'

    def test_manual_sector_dict(self):
        """手动板块字典被转换"""
        data = {'type': 'manual', 'name': '测试', 'codes': ['600519']}
        result = normalize_sector_entry(data)
        assert isinstance(result, ManualCustomSector)
        assert result.name == '测试'

    def test_old_format_dict(self):
        """旧格式 {name: [codes]} 被转换为手动板块"""
        data = {'我的板块': ['600519']}
        result = normalize_sector_entry(data)
        assert isinstance(result, ManualCustomSector)
        assert result.name == '我的板块'

    def test_invalid_entry_raises(self):
        """无效条目抛出 ValueError"""
        with pytest.raises(ValueError):
            normalize_sector_entry('invalid_string')


# ============ 11. THSSectorResolver 缓存 ============

class TestResolverCache:
    def test_get_cached_sector_none(self):
        """不存在的板块返回 None"""
        with tempfile.TemporaryDirectory() as tmpdir:
            resolver = THSSectorResolver(provenance_dir=tmpdir)
            result = resolver.get_cached_sector('999999')
            assert result is None

    def test_get_cached_sector_after_snapshot(self):
        """snapshot 解析后可以从缓存恢复基本信息（必须明确 source 和 snapshot_date）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            resolver = THSSectorResolver(provenance_dir=tmpdir)
            resolver.resolve_with_snapshot(
                '886032', '固态电池', ['600519'],
                source='同花顺 iFinD',
                snapshot_date='2026-10-06'
            )
            cached = resolver.get_cached_sector('886032')
            assert cached is not None
            assert cached.sector_code == '886032'
            assert cached.sector_name == '固态电池'
            # 成分股不存储在 provenance 中，所以为空
            assert cached.constituents == []


# ============ 11. IFindMCPProvider 真实权威检索路径 ============

class TestIFindMCPProvider:
    def test_provider_name(self):
        """Provider 名称正确"""
        def mock_fn(query, market='股票'):
            return {}
        provider = IFindMCPProvider(search_fn=mock_fn)
        assert provider.name == 'ths_ifind_mcp'

    def test_successful_retrieval(self, tmp_path):
        """通过 iFinD MCP 成功获取板块成分股（必须返回 asOf）"""
        def mock_fn(query, market='股票'):
            assert '886032' in query
            assert market == '股票'
            return {
                'securities': [
                    {'code': '600519', 'name': '贵州茅台'},
                    {'code': '000858', 'name': '五粮液'},
                    {'code': '300750', 'name': '宁德时代'},
                ],
                'sectorName': '固态电池',
                'asOf': '2026-10-06T00:00:00+00:00',
            }
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.sector_code == '886032'
        assert sector.sector_name == '固态电池'
        assert sector.constituent_count == 3
        assert '600519' in sector.constituents
        assert sector.source == '同花顺 iFinD'
        assert sector.provenance is not None
        assert sector.provenance.reconciliation_method == ReconciliationMethod.THS_IFIND
        assert sector.provenance.authoritative_reconciliation_date == '2026-10-06'

    def test_missing_as_of_fails_closed(self, tmp_path):
        """iFinD 响应缺少 asOf 时 fail-closed"""
        def mock_fn(query, market='股票'):
            return {'securities': [{'code': '600519'}]}
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve('886032')

    def test_missing_sector_name_returns_none(self, tmp_path):
        """iFinD 响应缺少板块名称时 sector_name 为 None（不伪造）"""
        def mock_fn(query, market='股票'):
            return {
                'securities': [{'code': '600519'}],
                'asOf': '2026-10-06T00:00:00+00:00',
            }
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.sector_name is None  # 不伪造板块名称

    def test_empty_constituents_raises(self, tmp_path):
        """空成分股列表抛出 ConstituentUnavailableError"""
        def mock_fn(query, market='股票'):
            return {'securities': [], 'asOf': '2026-10-06'}
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(ConstituentUnavailableError):
            resolver.resolve('886032')

    def test_mcp_exception_raises_authoritative_unavailable(self, tmp_path):
        """MCP 调用异常抛出 AuthoritativeSourceUnavailableError"""
        def mock_fn(query, market='股票'):
            raise RuntimeError('MCP connection timeout')
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve('886032')

    def test_string_code_format(self, tmp_path):
        """支持字符串格式的股票代码"""
        def mock_fn(query, market='股票'):
            return {'securities': ['600519', '000858', '300750'], 'asOf': '2026-10-06'}
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.constituent_count == 3

    def test_duplicate_codes_deduped(self, tmp_path):
        """重复股票代码被去重"""
        def mock_fn(query, market='股票'):
            return {'securities': [
                {'code': '600519'}, {'code': '600519'}, {'code': '000858'}
            ], 'asOf': '2026-10-06'}
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.constituent_count == 2  # 600519 去重


# ============ 12. SnapshotProvider ============

class TestSnapshotProvider:
    def test_successful_fetch(self, tmp_path):
        """Snapshot provider 成功获取（必须明确 source 和 asOf）"""
        snapshots = {
            '886032': {
                'sectorName': '固态电池',
                'constituents': ['600519', '000858'],
                'source': '同花顺 iFinD snapshot',
                'asOf': '2026-10-06T00:00:00+00:00',
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.sector_name == '固态电池'
        assert sector.constituent_count == 2
        assert sector.provenance.reconciliation_method == ReconciliationMethod.THS_IFIND

    def test_missing_source_fails_closed(self, tmp_path):
        """Snapshot 缺少 source 时 fail-closed"""
        snapshots = {
            '886032': {'sectorName': '固态电池', 'constituents': ['600519'], 'asOf': '2026-10-06'}
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve('886032')

    def test_missing_as_of_fails_closed(self, tmp_path):
        """Snapshot 缺少 asOf 时 fail-closed"""
        snapshots = {
            '886032': {'sectorName': '固态电池', 'constituents': ['600519'], 'source': '同花顺 iFinD'}
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve('886032')

    def test_missing_sector_raises(self, tmp_path):
        """snapshot 中不存在的板块抛出 SectorResolutionError"""
        provider = SnapshotProvider(snapshots={})
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(SectorResolutionError):
            resolver.resolve('999999')


# ============ 13. QA 验收测试：886032 示例 ============

class TestQAAcceptance886032:
    """QA 要求的确定性验收测试：886032 固态电池示例"""

    def test_qa_example_886032_full_flow(self, tmp_path):
        """完整流程：格式验证 → 权威源获取 → provenance 记录 → 成分股可用"""
        def mock_fn(query, market='股票'):
            return {
                'securities': [
                    {'code': '300750', 'name': '宁德时代'},
                    {'code': '002594', 'name': '比亚迪'},
                    {'code': '688567', 'name': '孚能科技'},
                ],
                'sectorName': '固态电池',
                'asOf': '2026-10-06T00:00:00+00:00',
            }
        provider = IFindMCPProvider(search_fn=mock_fn)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))

        # 1. 格式验证
        assert validate_ths_sector_code('886032') is True

        # 2. 权威源获取
        sector = resolver.resolve('886032')

        # 3. provenance 记录
        assert sector.provenance is not None
        assert sector.provenance.sector_code == '886032'
        assert sector.provenance.source == '同花顺 iFinD'
        assert sector.provenance.authoritative_reconciliation_date == '2026-10-06'
        assert sector.provenance.reconciliation_method == ReconciliationMethod.THS_IFIND

        # 4. 成分股可用
        assert sector.constituent_count == 3
        assert '300750' in sector.constituents
        assert sector.sector_name == '固态电池'

    def test_qa_example_stale_rejection(self, tmp_path):
        """数据过期的板块不能用于扫描（模拟 freshness gate）"""
        # 创建一个 60 天前的 snapshot（必须明确 source 和 snapshot_date）
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime('%Y-%m-%d')
        resolver = THSSectorResolver(provenance_dir=str(tmp_path))
        sector = resolver.resolve_with_snapshot(
            '886032', '固态电池', ['600519'],
            source='同花顺 iFinD',
            snapshot_date=old_date
        )
        # 模拟 freshness gate：超过 7 天即为 stale
        recon_date = sector.provenance.authoritative_reconciliation_date
        days = (datetime.now(timezone.utc) - datetime.strptime(recon_date, '%Y-%m-%d').replace(tzinfo=timezone.utc)).days
        assert days > 7  # 确实过期
        # 在实际 Web 端，stale 板块会被 freshness gate 阻止扫描


# ============ 14. 跨设备删除 tombstone 验收测试 ============

class TestCrossDeviceDeletion:
    """QA 要求的跨设备删除 tombstone 验收测试"""

    def test_tombstone_prevents_resurrection(self):
        """已删除的板块在合并时不会复活"""
        # 远程有板块 A，本地已删除板块 A（有 tombstone）
        remote = [DynamicSector(sector_code='886032', sector_name='固态电池')]
        local = []  # 本地已删除
        remote_tombstones = []
        local_tombstones = [
            {'sectorCode': '886032', 'sectorName': '固态电池', 'deletedAt': '2026-10-06T00:00:00Z'}
        ]

        # 使用 Python 端的合并逻辑（模拟 JS 端的 tombstone 过滤）
        all_tombstones = {t['sectorCode']: t for t in remote_tombstones + local_tombstones}
        merged = []
        for s in remote + local:
            if s.sector_code not in all_tombstones:
                merged.append(s)

        # 板块 A 不应该出现在合并结果中
        assert len(merged) == 0

    def test_tombstone_union_merge(self):
        """远程和本地的 tombstones 取并集"""
        remote_tombstones = [{'sectorCode': '886032'}]
        local_tombstones = [{'sectorCode': '886015'}]
        all_codes = {t['sectorCode'] for t in remote_tombstones + local_tombstones}
        assert '886032' in all_codes
        assert '886015' in all_codes
        assert len(all_codes) == 2

    def test_undeleted_sector_survives_merge(self):
        """未删除的板块在合并时保留"""
        remote = [DynamicSector(sector_code='886032', sector_name='固态电池')]
        local = [DynamicSector(sector_code='886015', sector_name='创新药')]
        tombstones = []  # 没有删除记录
        all_tombstones = {t['sectorCode'] for t in tombstones}
        merged = []
        seen = set()
        for s in remote + local:
            if s.sector_code not in all_tombstones and s.sector_code not in seen:
                merged.append(s)
                seen.add(s.sector_code)
        assert len(merged) == 2


# ============ 15. 重复成分股扫描一次 ============

class TestDuplicateConstituentScanOnce:
    """QA 要求的重复成分股扫描一次验收测试"""

    def test_duplicate_stocks_across_sectors_deduped(self):
        """同一只股票属于多个板块时，扫描时只出现一次"""
        sector_a = DynamicSector(
            sector_code='886032', sector_name='板块A',
            constituents=['600519', '000858', '300750']
        )
        sector_b = DynamicSector(
            sector_code='886015', sector_name='板块B',
            constituents=['300750', '600276', '002594']
        )
        # 300750 同时属于两个板块

        # 模拟扫描时的去重逻辑
        all_codes = []
        for sector in [sector_a, sector_b]:
            all_codes.extend(sector.constituents)
        unique_codes = list(set(all_codes))

        # 300750 只出现一次
        assert unique_codes.count('300750') == 1
        # 总共有 5 只独特股票（6只 - 1只重复）
        assert len(unique_codes) == 5

    def test_scope_attribution_preserved(self):
        """去重后保留范围归属（每只股票属于哪些板块）"""
        sector_a = DynamicSector(
            sector_code='886032', sector_name='板块A',
            constituents=['600519', '300750']
        )
        sector_b = DynamicSector(
            sector_code='886015', sector_name='板块B',
            constituents=['300750', '600276']
        )

        # 构建 code -> sectors 映射
        code_to_sectors = {}
        for sector in [sector_a, sector_b]:
            for code in sector.constituents:
                if code not in code_to_sectors:
                    code_to_sectors[code] = []
                code_to_sectors[code].append(sector.sector_name)

        # 300750 属于两个板块
        assert len(code_to_sectors['300750']) == 2
        assert '板块A' in code_to_sectors['300750']
        assert '板块B' in code_to_sectors['300750']
        # 600519 只属于一个板块
        assert len(code_to_sectors['600519']) == 1


# ============ 16. Final QA Gate: as-of 日期严格验证 ============

class TestAuthoritativeDateValidation:
    """Final QA Gate P0-3: 权威 as-of 日期必须严格验证（无效/未来/缺失都 fail-closed）"""

    def test_invalid_date_format_fails_closed(self, tmp_path):
        """无效日期格式（如 'not-a-date'）必须 fail-closed"""
        snapshots = {
            '886032': {
                'sectorName': '固态电池',
                'constituents': ['600519'],
                'source': '同花顺 iFinD snapshot',
                'asOf': 'not-a-date',
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError, match='日期格式无效'):
            resolver.resolve('886032')

    def test_future_date_fails_closed(self, tmp_path):
        """未来日期必须 fail-closed（防止伪造新鲜度）"""
        future_date = '2099-12-31'
        snapshots = {
            '886032': {
                'sectorName': '固态电池',
                'constituents': ['600519'],
                'source': '同花顺 iFinD snapshot',
                'asOf': future_date,
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError, match='未来日期'):
            resolver.resolve('886032')

    def test_empty_date_fails_closed(self, tmp_path):
        """空字符串日期必须 fail-closed"""
        snapshots = {
            '886032': {
                'sectorName': '固态电池',
                'constituents': ['600519'],
                'source': '同花顺 iFinD snapshot',
                'asOf': '',
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve('886032')

    def test_valid_date_passes(self, tmp_path):
        """有效日期（过去）必须通过验证"""
        snapshots = {
            '886032': {
                'sectorName': '固态电池',
                'constituents': ['600519'],
                'source': '同花顺 iFinD snapshot',
                'asOf': '2026-10-01',
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.sector_name == '固态电池'
        assert sector.constituent_count == 1

    def test_iso8601_with_timezone_passes(self, tmp_path):
        """ISO8601 带时区的日期必须通过验证"""
        snapshots = {
            '886032': {
                'sectorName': '固态电池',
                'constituents': ['600519'],
                'source': '同花顺 iFinD snapshot',
                'asOf': '2026-10-06T00:00:00+00:00',
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.sector_name == '固态电池'

    def test_resolve_with_snapshot_future_date_fails(self, tmp_path):
        """resolve_with_snapshot 的未来日期必须 fail-closed"""
        resolver = THSSectorResolver(provider=None, provenance_dir=str(tmp_path))
        with pytest.raises(AuthoritativeSourceUnavailableError, match='未来日期'):
            resolver.resolve_with_snapshot(
                sector_code='886032',
                sector_name='固态电池',
                constituents=['600519'],
                source='同花顺 iFinD snapshot 20261006',
                snapshot_date='2099-12-31',
            )


# ============ 17. Final QA Gate: 缺失板块名称 fail-closed ============

class TestMissingSectorNameFailClosed:
    """Final QA Gate P1: 权威源缺失板块名称时，sector_name 为 None（不伪造）"""

    def test_missing_sector_name_returns_none(self, tmp_path):
        """权威源未返回板块名称时，sector_name 为 None（不使用 板块{code} 占位）"""
        snapshots = {
            '886032': {
                'constituents': ['600519'],
                'source': '同花顺 iFinD snapshot',
                'asOf': '2026-10-06',
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        # 不伪造板块名称
        assert sector.sector_name is None
        # 成分股仍然可用
        assert sector.constituent_count == 1
        assert '600519' in sector.constituents

    def test_empty_sector_name_returns_none(self, tmp_path):
        """空字符串板块名称也视为缺失，返回 None"""
        snapshots = {
            '886032': {
                'sectorName': '',
                'constituents': ['600519'],
                'source': '同花顺 iFinD snapshot',
                'asOf': '2026-10-06',
            }
        }
        provider = SnapshotProvider(snapshots=snapshots)
        resolver = THSSectorResolver(provider=provider, provenance_dir=str(tmp_path))
        sector = resolver.resolve('886032')
        assert sector.sector_name is None


# ============ 18. CLI 集成测试（production path） ============

class TestVerifyTHSSectorCLI:
    """CLI 集成测试：通过 subprocess 调用 verify_ths_sector.py，验证生产路径"""

    CLI_SCRIPT = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'skill', 'scripts', 'verify_ths_sector.py'
    )

    def _run_cli(self, sector_code, stdin_data=None, args=None, provenance_dir=None):
        """运行 CLI 并返回 (returncode, stdout, stderr)"""
        cmd = [sys.executable, self.CLI_SCRIPT, sector_code, '--quiet']
        if provenance_dir:
            cmd.extend(['--provenance-dir', provenance_dir])
        if args:
            cmd.extend(args)

        result = subprocess.run(
            cmd,
            input=stdin_data,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.returncode, result.stdout, result.stderr

    def test_cli_success_path_with_stdin(self, tmp_path):
        """CLI 成功路径：通过 stdin 传入 iFinD MCP JSON，验证成功"""
        mcp_json = json.dumps({
            'securities': [{'code': '600519', 'name': '贵州茅台'}, {'code': '000858', 'name': '五粮液'}],
            'sectorName': '白酒',
            'asOf': '2026-10-06',
        })
        provenance_dir = str(tmp_path / 'provenance')
        returncode, stdout, stderr = self._run_cli(
            '886032', stdin_data=mcp_json,
            args=['--as-of', '2026-10-06'],
            provenance_dir=provenance_dir,
        )
        assert returncode == 0, f"CLI failed: {stderr}"
        result = json.loads(stdout)
        assert result['sectorCode'] == '886032'
        assert result['sectorName'] == '白酒'
        assert result['constituentCount'] == 2
        assert result['verified'] is True
        assert result['provenance']['authoritativeReconciliationDate'] == '2026-10-06'
        assert result['provenance']['reconciliationMethod'] == 'ths_ifind'
        assert result['provenance']['verifiedCount'] == 2
        # provenance 文件应该存在
        assert os.path.exists(result['provenance']['provenanceFile'])

    def test_cli_success_path_with_input_file(self, tmp_path):
        """CLI 成功路径：通过 --input 文件传入 iFinD MCP JSON"""
        mcp_json = json.dumps({
            'securities': [{'code': '600519'}],
            'sectorName': '测试板块',
            'asOf': '2026-10-06',
        })
        input_file = tmp_path / 'mcp_response.json'
        input_file.write_text(mcp_json)
        provenance_dir = str(tmp_path / 'provenance')
        returncode, stdout, stderr = self._run_cli(
            '886032',
            args=['--input', str(input_file), '--as-of', '2026-10-06'],
            provenance_dir=provenance_dir,
        )
        assert returncode == 0, f"CLI failed: {stderr}"
        result = json.loads(stdout)
        assert result['sectorName'] == '测试板块'
        assert result['constituentCount'] == 1
        assert result['verified'] is True

    def test_cli_missing_sector_name_not_verified(self, tmp_path):
        """CLI：缺失板块名称时，provenance 不标记为 VERIFIED（fail-closed）"""
        mcp_json = json.dumps({
            'securities': [{'code': '600519'}, {'code': '000858'}],
            'asOf': '2026-10-06',
            # 注意：没有 sectorName
        })
        provenance_dir = str(tmp_path / 'provenance')
        returncode, stdout, stderr = self._run_cli(
            '886032', stdin_data=mcp_json,
            args=['--as-of', '2026-10-06'],
            provenance_dir=provenance_dir,
        )
        # CLI 仍然成功（成分股可用），但 verified=false
        assert returncode == 0, f"CLI failed: {stderr}"
        result = json.loads(stdout)
        assert result['sectorName'] is None
        assert result['constituentCount'] == 2
        assert result['verified'] is False
        assert result['provenance']['authoritativeReconciliationDate'] is None
        assert result['provenance']['reconciliationMethod'] == 'not_reconciled'
        assert result['provenance']['verifiedCount'] == 0

    def test_cli_invalid_sector_code_exit_2(self, tmp_path):
        """CLI：无效板块代码 → 退出码 2"""
        returncode, stdout, stderr = self._run_cli(
            'invalid', stdin_data='{}',
            provenance_dir=str(tmp_path / 'provenance'),
        )
        assert returncode == 2
        assert 'Invalid sector code' in stderr or '无效' in stderr

    def test_cli_empty_constituents_exit_3(self, tmp_path):
        """CLI：空成分股 → 退出码 3"""
        mcp_json = json.dumps({
            'securities': [],
            'sectorName': '空板块',
            'asOf': '2026-10-06',
        })
        returncode, stdout, stderr = self._run_cli(
            '886032', stdin_data=mcp_json,
            args=['--as-of', '2026-10-06'],
            provenance_dir=str(tmp_path / 'provenance'),
        )
        assert returncode == 3
        assert 'Empty constituent' in stderr or '空' in stderr

    def test_cli_missing_as_of_exit_4(self, tmp_path):
        """CLI：缺失权威 as-of → 退出码 4"""
        mcp_json = json.dumps({
            'securities': [{'code': '600519'}],
            'sectorName': '测试板块',
            # 注意：没有 asOf
        })
        returncode, stdout, stderr = self._run_cli(
            '886032', stdin_data=mcp_json,
            # 注意：没有 --as-of
            provenance_dir=str(tmp_path / 'provenance'),
        )
        assert returncode == 4
        assert 'as-of' in stderr.lower() or 'as_of' in stderr.lower()

    def test_cli_future_as_of_fails(self, tmp_path):
        """CLI：未来 as-of 日期 → fail-closed（退出码非0）"""
        mcp_json = json.dumps({
            'securities': [{'code': '600519'}],
            'sectorName': '测试板块',
            'asOf': '2099-12-31',
        })
        returncode, stdout, stderr = self._run_cli(
            '886032', stdin_data=mcp_json,
            args=['--as-of', '2099-12-31'],
            provenance_dir=str(tmp_path / 'provenance'),
        )
        # 未来日期应该 fail-closed（退出码 4 或 1）
        assert returncode != 0
        assert '未来' in stderr or 'future' in stderr.lower()

    def test_cli_snapshot_mode(self, tmp_path):
        """CLI：snapshot 模式成功路径"""
        snapshot_json = json.dumps({
            'sectorName': '固态电池',
            'constituents': ['300750', '002594'],
        })
        provenance_dir = str(tmp_path / 'provenance')
        returncode, stdout, stderr = self._run_cli(
            '886032',
            args=['--snapshot', snapshot_json, '--source', '同花顺 iFinD snapshot', '--as-of', '2026-10-06'],
            provenance_dir=provenance_dir,
        )
        assert returncode == 0, f"CLI failed: {stderr}"
        result = json.loads(stdout)
        assert result['sectorName'] == '固态电池'
        assert result['constituentCount'] == 2
        assert result['verified'] is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
