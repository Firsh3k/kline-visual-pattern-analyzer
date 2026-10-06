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
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from core.sector_validation import (
    DynamicSector,
    ManualCustomSector,
    THSSectorResolver,
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
    def test_resolve_without_ifind_raises(self):
        """iFinD 不可用时 resolve 抛出 AuthoritativeSourceUnavailableError"""
        resolver = THSSectorResolver(ifind_available=False)
        with pytest.raises(AuthoritativeSourceUnavailableError):
            resolver.resolve('886032')

    def test_resolve_invalid_code_raises_first(self):
        """无效代码先抛出 SectorCodeValidationError（在 iFinD 检查之前）"""
        resolver = THSSectorResolver(ifind_available=False)
        with pytest.raises(SectorCodeValidationError):
            resolver.resolve('invalid')

    def test_resolve_with_snapshot_works(self):
        """受控 snapshot 解析不依赖 iFinD"""
        resolver = THSSectorResolver(ifind_available=False)
        sector = resolver.resolve_with_snapshot(
            '886032', '固态电池', ['600519', '000858'],
            source='test_snapshot'
        )
        assert sector.sector_name == '固态电池'
        assert sector.constituent_count == 2
        assert sector.provenance is not None

    def test_snapshot_invalid_code_raises(self):
        """snapshot 解析也验证代码格式"""
        resolver = THSSectorResolver()
        with pytest.raises(SectorCodeValidationError):
            resolver.resolve_with_snapshot('bad', '测试', [])


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
    def test_fresh_sector(self):
        """新鲜板块（7天内更新）"""
        resolver = THSSectorResolver()
        sector = resolver.resolve_with_snapshot(
            '886032', '测试', ['600519'],
            snapshot_date=datetime.now(timezone.utc).strftime('%Y-%m-%d')
        )
        assert sector.get_freshness_level() == FreshnessLevel.FRESH
        assert sector.is_fresh() is True

    def test_stale_sector(self):
        """过期板块（超过30天）"""
        old_date = (datetime.now(timezone.utc) - timedelta(days=60)).strftime('%Y-%m-%d')
        resolver = THSSectorResolver()
        sector = resolver.resolve_with_snapshot(
            '886032', '测试', ['600519'],
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
    def test_snapshot_records_provenance(self):
        """snapshot 解析记录 provenance"""
        resolver = THSSectorResolver()
        sector = resolver.resolve_with_snapshot(
            '886032', '固态电池', ['600519'],
            source='test_snapshot_20261006'
        )
        assert sector.provenance is not None
        assert sector.provenance.sector_code == '886032'
        assert sector.provenance.source == 'test_snapshot_20261006'
        assert sector.provenance.authoritative_reconciliation_date is not None
        assert sector.provenance.reconciliation_method == ReconciliationMethod.THS_IFIND

    def test_provenance_persisted(self):
        """provenance 被持久化到文件"""
        with tempfile.TemporaryDirectory() as tmpdir:
            resolver = THSSectorResolver(provenance_dir=tmpdir)
            resolver.resolve_with_snapshot(
                '886032', '固态电池', ['600519'],
                source='test'
            )
            # 检查 provenance 文件是否创建
            files = os.listdir(tmpdir)
            assert len(files) > 0


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
        """snapshot 解析后可以从缓存恢复基本信息"""
        with tempfile.TemporaryDirectory() as tmpdir:
            resolver = THSSectorResolver(provenance_dir=tmpdir)
            resolver.resolve_with_snapshot(
                '886032', '固态电池', ['600519'],
                source='test'
            )
            cached = resolver.get_cached_sector('886032')
            assert cached is not None
            assert cached.sector_code == '886032'
            assert cached.sector_name == '固态电池'
            # 成分股不存储在 provenance 中，所以为空
            assert cached.constituents == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
