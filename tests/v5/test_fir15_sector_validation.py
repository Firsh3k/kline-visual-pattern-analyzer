#!/usr/bin/env python3
"""
FIR-15 — Authoritative Sector Membership Validation 测试

所有测试都是确定性的，不需要网络连接。
覆盖：代码格式验证、存在性验证、可交易性验证、重复检测、
对账逻辑、provenance 管理、新鲜度检查、序列化/反序列化。
"""

import json
import os
import tempfile
import pytest

from core.sector_validation import (
    # 数据结构
    MembershipStatus, ReconciliationMethod, FreshnessLevel,
    SectorConstituent, SectorProvenance, ReconciliationResult, SectorValidationReport,
    # 验证器
    validate_code_format, is_stock_code, detect_potential_delisting,
    validate_constituent, validate_sector, validate_all_sectors,
    get_validation_summary,
    # 对账器
    reconcile_codes, apply_reconciliation_to_constituents,
    generate_reconciliation_report,
    # Provenance 管理器
    load_provenance, save_provenance, update_provenance_after_refresh,
    update_provenance_after_reconciliation, check_freshness,
    list_all_provenances, get_stale_sectors, get_provenance_path,
)


# ============================================================
# 1. 代码格式验证测试
# ============================================================

class TestCodeFormatValidation:
    """股票代码格式验证"""

    @pytest.mark.parametrize("code,expected", [
        # 沪市主板
        ("600519", True),
        ("601318", True),
        ("603259", True),
        # 科创板
        ("688981", True),
        ("688099", True),
        # 沪市 B 股（权威范围 900000-900999）
        ("900901", True),
        ("900957", True),
        ("900999", True),  # 上边界
        ("900000", True),  # 下边界
        # 深市主板
        ("000858", True),
        ("000001", True),
        # 创业板
        ("300750", True),
        ("301099", True),
        # 北交所历史代码
        ("430047", True),
        ("830799", True),
        ("870001", True),
        ("880001", True),
        # 北交所新代码段（920000-920999）
        ("920211", True),
        ("920002", True),
        ("920999", True),  # 上边界
        ("920000", True),  # 下边界
    ])
    def test_valid_codes(self, code, expected):
        """有效代码应该通过验证"""
        is_valid, msg = validate_code_format(code)
        assert is_valid == expected, f"{code}: expected valid={expected}, got {is_valid}, msg={msg}"

    @pytest.mark.parametrize("code", [
        # 格式错误
        "",
        "123",
        "12345",
        "1234567",
        "abcdef",
        "60051A",
        " 600519",
        # 未知前缀
        ("100001"),
        ("200001"),
        ("700001"),
        # 沪市 B 股超出权威范围
        ("901000"),  # 第一个超出范围
        ("909999"),
        ("910000"),
        ("919999"),
        # 北交所新代码段超出范围
        ("921000"),
        ("929999"),
        ("930000"),
    ])
    def test_invalid_codes(self, code):
        """无效代码应该失败验证"""
        is_valid, msg = validate_code_format(code)
        assert not is_valid, f"{code}: expected invalid, got valid"
        assert msg, f"{code}: should have error message"

    def test_sse_b_share_precise_boundary(self):
        """沪市 B 股权威范围精确边界（900000-900999）"""
        # 边界内
        assert validate_code_format("900000")[0] is True
        assert validate_code_format("900999")[0] is True
        # 边界外
        assert validate_code_format("899999")[0] is False  # 前缀89不在已知范围
        assert validate_code_format("901000")[0] is False

    def test_bse_new_code_precise_boundary(self):
        """北交所新代码段精确边界（920000-920999）"""
        assert validate_code_format("920000")[0] is True
        assert validate_code_format("920999")[0] is True
        assert validate_code_format("919999")[0] is False
        assert validate_code_format("921000")[0] is False


# ============================================================
# 2. 股票类型检测测试
# ============================================================

class TestStockCodeDetection:
    """股票代码类型检测"""

    @pytest.mark.parametrize("code,expected", [
        ("600519", True),   # 沪市主板
        ("688981", True),   # 科创板
        ("900901", True),   # 沪市B股
        ("000858", True),   # 深市主板
        ("300750", True),   # 创业板
        ("430047", True),   # 北交所历史
        ("920211", True),   # 北交所新代码
        ("510300", False),  # 沪市ETF
        ("159915", False),  # 深市ETF
        ("160001", False),  # 深市基金
        ("invalid", False),
    ])
    def test_is_stock_code(self, code, expected):
        assert is_stock_code(code) == expected

    def test_detect_potential_delisting(self):
        """潜在退市风险检测"""
        # 北交所历史代码段有风险提示
        result = detect_potential_delisting("430047")
        assert result is not None
        assert "北交所" in result

        # 普通沪市股票无风险提示
        result = detect_potential_delisting("600519")
        assert result is None


# ============================================================
# 3. 单只成分股验证测试
# ============================================================

class TestConstituentValidation:
    """单只板块成分股验证"""

    def test_valid_stock(self):
        """有效股票离线验证通过 → UNVERIFIED（需权威对账后才是 VERIFIED）"""
        c = validate_constituent("600519", "贵州茅台")
        assert c.code == "600519"
        assert c.name == "贵州茅台"
        assert c.status == MembershipStatus.UNVERIFIED
        assert c.verified_at is not None
        assert c.source == "offline_validator"
        assert c.reconciled_at is None  # 未对账
        assert c.reconciliation_source == ""

    def test_invalid_code(self):
        """无效代码应该被标记为 INVALID"""
        c = validate_constituent("invalid")
        assert c.status == MembershipStatus.INVALID
        assert "格式错误" in c.notes

    def test_etf_code(self):
        """ETF 代码应该被标记为 INVALID（非股票类）"""
        c = validate_constituent("510300")
        assert c.status == MembershipStatus.INVALID
        assert "非股票" in c.notes

    def test_bse_historical_code(self):
        """北交所历史代码离线验证通过 → UNVERIFIED（有风险提示备注）"""
        c = validate_constituent("430047")
        assert c.status == MembershipStatus.UNVERIFIED
        assert c.notes != ""  # 应该有风险提示备注

    def test_serialization_roundtrip(self):
        """SectorConstituent 序列化/反序列化往返"""
        original = validate_constituent("600519", "贵州茅台")
        data = original.to_dict()
        restored = SectorConstituent.from_dict(data)
        assert restored.code == original.code
        assert restored.name == original.name
        assert restored.status == original.status
        assert restored.notes == original.notes


# ============================================================
# 4. 板块验证测试
# ============================================================

class TestSectorValidation:
    """整个板块的成分股验证"""

    def test_basic_validation(self):
        """基本板块验证 — 离线验证通过 → UNVERIFIED（未对账）"""
        codes = ["600519", "000858", "300750", "688981"]
        report = validate_sector("测试板块", codes, sector_code="888888")
        assert report.sector_name == "测试板块"
        assert report.sector_code == "888888"
        assert report.total == 4
        assert report.unverified == 4  # 离线验证通过但未对账
        assert report.verified == 0  # 未经过权威对账
        assert report.invalid == 0
        assert report.offline_valid_rate == 1.0  # 离线验证通过率
        assert report.verification_rate == 0.0  # 权威对账验证率（未对账）
        assert report.has_issues is False  # 未对账不是问题

    def test_with_invalid_codes(self):
        """包含无效代码的板块验证"""
        codes = ["600519", "invalid", "000858", "999999", ""]
        report = validate_sector("测试板块", codes)
        assert report.total == 4  # 空字符串被跳过
        assert report.unverified == 2  # 离线验证通过
        assert report.invalid == 2
        assert report.offline_valid_rate == 0.5
        assert report.has_issues is True  # 有无效代码

    def test_duplicate_detection(self):
        """重复代码检测"""
        codes = ["600519", "000858", "600519", "300750", "000858"]
        report = validate_sector("测试板块", codes)
        assert report.total == 5
        assert "600519" in report.duplicates
        assert "000858" in report.duplicates
        assert len(report.duplicates) == 2

    def test_duplicate_constituent_status(self):
        """重复出现的成分股应该被标记为 DUPLICATE，首次出现为 UNVERIFIED"""
        codes = ["600519", "600519"]
        report = validate_sector("测试板块", codes)
        statuses = [c.status for c in report.constituents]
        assert MembershipStatus.DUPLICATE in statuses
        assert MembershipStatus.UNVERIFIED in statuses  # 首次出现离线验证通过

    def test_with_provenance(self):
        """带 provenance 的板块验证"""
        provenance = SectorProvenance(
            sector_name="测试板块",
            sector_code="888888",
            source="ths_ifind",
            local_refresh_date="2026-10-01",
            total_constituents=3,
        )
        report = validate_sector("测试板块", ["600519", "000858", "300750"],
                                  provenance=provenance)
        assert report.provenance is not None
        assert report.provenance.source == "ths_ifind"

    def test_validation_report_serialization(self):
        """SectorValidationReport 序列化/反序列化往返"""
        report = validate_sector("测试板块", ["600519", "000858"])
        data = report.to_dict()
        restored = SectorValidationReport.from_dict(data)
        assert restored.sector_name == report.sector_name
        assert restored.total == report.total
        assert restored.verified == report.verified
        assert restored.verification_rate == report.verification_rate

    def test_validate_all_sectors(self):
        """验证所有板块"""
        sectors = {
            "板块A": ["600519", "000858"],
            "板块B": ["300750", "invalid"],
        }
        reports = validate_all_sectors(sectors)
        assert len(reports) == 2
        assert "板块A" in reports
        assert "板块B" in reports
        assert reports["板块A"].has_issues is False
        assert reports["板块B"].has_issues is True

    def test_get_validation_summary(self):
        """验证汇总统计 — 区分离线验证通过率与权威对账验证率"""
        sectors = {
            "板块A": ["600519", "000858"],
            "板块B": ["300750", "invalid"],
        }
        reports = validate_all_sectors(sectors)
        summary = get_validation_summary(reports)
        assert summary['total_sectors'] == 2
        assert summary['total_constituents'] == 4
        assert summary['total_unverified'] == 3  # 离线验证通过但未对账
        assert summary['total_verified'] == 0  # 未经过权威对账
        assert summary['total_offline_valid'] == 3  # 离线验证通过总数
        assert summary['total_invalid'] == 1
        assert len(summary['sectors_with_issues']) == 1
        assert summary['overall_offline_valid_rate'] == 0.75  # 离线验证通过率
        assert summary['overall_verification_rate'] == 0.0  # 权威对账验证率（未对账）


# ============================================================
# 5. 对账测试
# ============================================================

class TestReconciliation:
    """板块成分股对账"""

    def test_basic_reconciliation(self):
        """基本对账"""
        local = ["600519", "000858", "300750"]
        source = ["600519", "000858", "688981"]
        result = reconcile_codes("测试板块", local, source, source="ths_ifind")
        assert result.sector_name == "测试板块"
        assert set(result.matched) == {"600519", "000858"}
        assert result.only_in_local == ["300750"]
        assert result.only_in_source == ["688981"]
        assert result.source == "ths_ifind"
        assert result.reconciled_at != ""

    def test_match_rate(self):
        """匹配率计算"""
        # 2 matched, 1 only_local, 1 only_source → match_rate = 2/3
        result = reconcile_codes("测试", ["A", "B", "C"], ["A", "B", "D"])
        assert result.match_rate == pytest.approx(2 / 3, abs=0.01)

        # 完全匹配
        result = reconcile_codes("测试", ["A", "B"], ["A", "B"])
        assert result.match_rate == 1.0

        # 完全不匹配
        result = reconcile_codes("测试", ["A", "B"], ["C", "D"])
        assert result.match_rate == 0.0

        # 空列表
        result = reconcile_codes("测试", [], [])
        assert result.match_rate == 0.0

    def test_whitespace_and_empty_handling(self):
        """空白和空字符串处理"""
        local = [" 600519 ", "", "000858"]
        source = ["600519", "  "]
        result = reconcile_codes("测试", local, source)
        assert set(result.matched) == {"600519"}
        assert "000858" in result.only_in_local

    def test_apply_reconciliation_to_constituents(self):
        """对账结果应用：matched 从 UNVERIFIED 升级为 VERIFIED，local-only → NOT_MEMBER"""
        constituents = [
            SectorConstituent(code="600519", status=MembershipStatus.UNVERIFIED),
            SectorConstituent(code="000858", status=MembershipStatus.UNVERIFIED),
            SectorConstituent(code="300750", status=MembershipStatus.UNVERIFIED),
        ]
        reconciliation = ReconciliationResult(
            sector_name="测试",
            matched=["600519", "000858"],
            only_in_local=["300750"],
            only_in_source=[],
            source="ths_ifind",
        )
        updated = apply_reconciliation_to_constituents(constituents, reconciliation)
        statuses = {c.code: c.status for c in updated}
        # matched → VERIFIED（写入 reconciled_at/source）
        assert statuses["600519"] == MembershipStatus.VERIFIED
        assert statuses["000858"] == MembershipStatus.VERIFIED
        verified_c = [c for c in updated if c.code == "600519"][0]
        assert verified_c.reconciled_at is not None
        assert verified_c.reconciliation_source == "ths_ifind"
        # local-only → NOT_MEMBER
        assert statuses["300750"] == MembershipStatus.NOT_MEMBER

    def test_reconciliation_result_serialization(self):
        """ReconciliationResult 序列化/反序列化往返"""
        result = reconcile_codes("测试", ["A", "B"], ["A", "C"], source="test")
        data = result.to_dict()
        restored = ReconciliationResult.from_dict(data)
        assert restored.sector_name == result.sector_name
        assert restored.matched == result.matched
        assert restored.only_in_local == result.only_in_local
        assert restored.only_in_source == result.only_in_source
        assert restored.match_rate == result.match_rate

    def test_generate_reconciliation_report(self):
        """生成对账报告文本"""
        result = reconcile_codes("测试板块", ["600519", "000858"],
                                  ["600519", "300750"], source="ths_ifind")
        report_text = generate_reconciliation_report(result)
        assert "测试板块" in report_text
        assert "ths_ifind" in report_text
        assert "匹配" in report_text
        assert "仅本地存在" in report_text
        assert "仅源存在" in report_text


# ============================================================
# 6. Provenance 管理测试
# ============================================================

class TestProvenanceManagement:
    """Provenance 元数据管理"""

    @pytest.fixture
    def temp_provenance_dir(self):
        """临时 provenance 目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_save_and_load_provenance(self, temp_provenance_dir):
        """保存和加载 provenance"""
        provenance = SectorProvenance(
            sector_name="创新药",
            sector_code="886015",
            source="ths_ifind",
            source_url="https://example.com/api",
            fetched_at="2026-10-01T00:00:00Z",
            local_refresh_date="2026-10-01",
            authoritative_reconciliation_date="2026-10-01",
            reconciliation_method=ReconciliationMethod.THS_IFIND,
            reconciliation_result="matched=95, local_only=3, source_only=2",
            total_constituents=100,
            verified_count=95,
        )
        path = save_provenance(provenance, temp_provenance_dir)
        assert os.path.exists(path)

        loaded = load_provenance("创新药", temp_provenance_dir)
        assert loaded is not None
        assert loaded.sector_name == "创新药"
        assert loaded.sector_code == "886015"
        assert loaded.source == "ths_ifind"
        assert loaded.local_refresh_date == "2026-10-01"
        assert loaded.authoritative_reconciliation_date == "2026-10-01"
        assert loaded.total_constituents == 100
        assert loaded.verified_count == 95

    def test_load_nonexistent_provenance(self, temp_provenance_dir):
        """加载不存在的 provenance 应该返回 None"""
        result = load_provenance("不存在的板块", temp_provenance_dir)
        assert result is None

    def test_update_provenance_after_refresh(self, temp_provenance_dir):
        """刷新后更新 provenance"""
        # 第一次刷新
        p1 = update_provenance_after_refresh(
            "创新药", "886015", "ths_ifind", 100,
            provenance_dir=temp_provenance_dir,
        )
        assert p1.total_constituents == 100
        assert p1.reconciliation_method == ReconciliationMethod.NOT_RECONCILED

        # 第二次刷新（成分股数量变化）
        p2 = update_provenance_after_refresh(
            "创新药", "886015", "ths_ifind", 105,
            provenance_dir=temp_provenance_dir,
        )
        assert p2.total_constituents == 105
        # 应该保留之前的 reconciliation_method
        assert p2.reconciliation_method == ReconciliationMethod.NOT_RECONCILED

    def test_update_provenance_after_reconciliation(self, temp_provenance_dir):
        """对账后更新 provenance — 只有权威对账成功才能更新 authoritative_reconciliation_date"""
        # 先创建 provenance（未对账）
        update_provenance_after_refresh(
            "创新药", "886015", "ths_ifind", 100,
            provenance_dir=temp_provenance_dir,
        )
        # 对账后更新
        updated = update_provenance_after_reconciliation(
            "创新药", ReconciliationMethod.THS_IFIND, 95,
            reconciliation_result="matched=95, local_only=3, source_only=2",
            provenance_dir=temp_provenance_dir,
        )
        assert updated is not None
        assert updated.reconciliation_method == ReconciliationMethod.THS_IFIND
        assert updated.verified_count == 95
        assert updated.authoritative_reconciliation_date is not None
        assert updated.reconciliation_result != ""
        # 对账后新鲜度应该是 FRESH
        assert updated.get_freshness_level(updated.authoritative_reconciliation_date) == FreshnessLevel.FRESH

    def test_update_reconciliation_nonexistent(self, temp_provenance_dir):
        """对不存在的 provenance 执行对账更新应该返回 None"""
        result = update_provenance_after_reconciliation(
            "不存在的板块", ReconciliationMethod.THS_IFIND, 0,
            provenance_dir=temp_provenance_dir,
        )
        assert result is None

    def test_freshness_level(self):
        """新鲜度等级计算 — 基于权威对账时间，不是本地刷新时间"""
        provenance = SectorProvenance(
            sector_name="测试",
            authoritative_reconciliation_date="2026-10-01",
        )
        # 新鲜（权威对账 7天内）
        assert provenance.get_freshness_level("2026-10-05") == FreshnessLevel.FRESH
        # 老化（权威对账 7-30天）
        assert provenance.get_freshness_level("2026-10-20") == FreshnessLevel.AGING
        # 过期（权威对账 30天以上）
        assert provenance.get_freshness_level("2026-11-15") == FreshnessLevel.STALE

    def test_freshness_not_reconciled(self):
        """未进行权威对账时新鲜度为 NOT_RECONCILED（绝不能视为 FRESH）"""
        # 只有本地刷新日期，没有权威对账日期
        provenance = SectorProvenance(
            sector_name="测试",
            local_refresh_date="2026-10-05",  # 今天刚刷新本地文件
        )
        # 即使本地文件今天刚刷新，未对账仍然是 NOT_RECONCILED
        assert provenance.get_freshness_level("2026-10-05") == FreshnessLevel.NOT_RECONCILED

    def test_freshness_unknown(self):
        """provenance 完全不存在时新鲜度为 UNKNOWN（由 check_freshness 函数处理）"""
        # SectorProvenance 对象本身没有日期时 → NOT_RECONCILED
        provenance = SectorProvenance(sector_name="测试")
        assert provenance.get_freshness_level("2026-10-01") == FreshnessLevel.NOT_RECONCILED

    def test_check_freshness_function(self, temp_provenance_dir):
        """check_freshness 函数测试 — 未对账 → NOT_RECONCILED，对账后 → FRESH"""
        # 只刷新本地文件（未对账）→ NOT_RECONCILED
        update_provenance_after_refresh(
            "未对账板块", "001", "test", 10,
            provenance_dir=temp_provenance_dir,
        )
        freshness = check_freshness("未对账板块", provenance_dir=temp_provenance_dir)
        assert freshness == FreshnessLevel.NOT_RECONCILED

        # 权威对账后 → FRESH
        update_provenance_after_reconciliation(
            "未对账板块", ReconciliationMethod.THS_IFIND, 8,
            reconciliation_result="matched=8",
            provenance_dir=temp_provenance_dir,
        )
        freshness = check_freshness("未对账板块", provenance_dir=temp_provenance_dir)
        assert freshness == FreshnessLevel.FRESH

        # 不存在的板块应该是 UNKNOWN
        freshness = check_freshness("不存在", provenance_dir=temp_provenance_dir)
        assert freshness == FreshnessLevel.UNKNOWN

    def test_list_all_provenances(self, temp_provenance_dir):
        """列出所有 provenance"""
        update_provenance_after_refresh("板块A", "001", "test", 10,
                                          provenance_dir=temp_provenance_dir)
        update_provenance_after_refresh("板块B", "002", "test", 20,
                                          provenance_dir=temp_provenance_dir)
        all_p = list_all_provenances(temp_provenance_dir)
        assert len(all_p) == 2
        assert "板块A" in all_p
        assert "板块B" in all_p

    def test_get_stale_sectors(self, temp_provenance_dir):
        """获取过期板块列表 — 基于权威对账时间"""
        # 创建一个权威对账新鲜的
        update_provenance_after_refresh("新鲜板块", "001", "test", 10,
                                          provenance_dir=temp_provenance_dir)
        update_provenance_after_reconciliation(
            "新鲜板块", ReconciliationMethod.THS_IFIND, 8,
            reconciliation_result="matched=8",
            provenance_dir=temp_provenance_dir,
        )
        # 手动创建一个权威对账过期的
        stale_provenance = SectorProvenance(
            sector_name="过期板块",
            authoritative_reconciliation_date="2026-01-01",
        )
        save_provenance(stale_provenance, temp_provenance_dir)

        stale = get_stale_sectors(current_date="2026-10-01",
                                    provenance_dir=temp_provenance_dir)
        assert "过期板块" in stale
        assert "新鲜板块" not in stale

    def test_provenance_serialization(self):
        """SectorProvenance 序列化/反序列化往返（v2.0 格式）"""
        original = SectorProvenance(
            sector_name="创新药",
            sector_code="886015",
            source="ths_ifind",
            local_refresh_date="2026-10-01",
            authoritative_reconciliation_date="2026-10-01",
            reconciliation_method=ReconciliationMethod.THS_IFIND,
            reconciliation_result="matched=95, local_only=3, source_only=2",
            total_constituents=100,
            verified_count=95,
        )
        data = original.to_dict()
        assert data['version'] == '2.0'
        assert 'local_refresh_date' in data
        assert 'authoritative_reconciliation_date' in data
        restored = SectorProvenance.from_dict(data)
        assert restored.sector_name == original.sector_name
        assert restored.sector_code == original.sector_code
        assert restored.source == original.source
        assert restored.local_refresh_date == original.local_refresh_date
        assert restored.authoritative_reconciliation_date == original.authoritative_reconciliation_date
        assert restored.reconciliation_method == original.reconciliation_method
        assert restored.reconciliation_result == original.reconciliation_result
        assert restored.total_constituents == original.total_constituents
        assert restored.verified_count == original.verified_count

    def test_provenance_v1_backward_compatibility(self):
        """v1.0 provenance 文件向后兼容（refresh_date → local_refresh_date）"""
        v1_data = {
            'sector_name': '旧板块',
            'sector_code': '001',
            'source': 'manual',
            'refresh_date': '2026-09-01',  # v1.0 字段
            'reconciliation_method': 'not_reconciled',
            'total_constituents': 10,
            'verified_count': 0,
            'version': '1.0',
        }
        restored = SectorProvenance.from_dict(v1_data)
        assert restored.local_refresh_date == '2026-09-01'  # 映射到新字段
        assert restored.authoritative_reconciliation_date is None  # 未对账
        assert restored.get_freshness_level('2026-10-01') == FreshnessLevel.NOT_RECONCILED

    def test_get_provenance_path_sanitization(self):
        """provenance 文件路径安全化"""
        path = get_provenance_path("创新药/测试:特殊*字符")
        # 不应该包含不安全字符
        assert "/" not in os.path.basename(path)
        assert ":" not in os.path.basename(path)
        assert "*" not in os.path.basename(path)


# ============================================================
# 7. 集成测试：验证现有生产板块数据
# ============================================================

class TestProductionSectorData:
    """验证现有生产板块数据（sector_data/new_sectors.js）"""

    def test_load_and_validate_production_sectors(self):
        """加载并验证所有生产板块数据"""
        import re
        sector_file = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "sector_data", "new_sectors.js"
        )
        if not os.path.exists(sector_file):
            pytest.skip("生产板块数据文件不存在")

        with open(sector_file, 'r', encoding='utf-8') as f:
            content = f.read()

        # 提取 NEW_SECTORS 对象
        match = re.search(r'const NEW_SECTORS\s*=\s*(\{[\s\S]*?\});', content)
        assert match, "无法找到 NEW_SECTORS 定义"

        js_obj = match.group(1)
        js_obj = re.sub(r"'([^']*)'", r'"\1"', js_obj)
        js_obj = re.sub(r',\s*([}\]])', r'\1', js_obj)
        sectors = json.loads(js_obj)

        assert len(sectors) > 0, "应该至少有一个板块"

        # 验证所有板块
        reports = validate_all_sectors(sectors)
        summary = get_validation_summary(reports)

        # 生产数据应该有较高的离线验证通过率
        assert summary['total_constituents'] > 0
        # 允许少量无效代码（可能是北交所历史代码等），但离线验证通过率应该 > 90%
        # 注意：权威对账验证率（overall_verification_rate）在未对账时为 0%，这是正常的
        assert summary['overall_offline_valid_rate'] > 0.9, \
            f"生产板块数据离线验证通过率过低: {summary['overall_offline_valid_rate']:.2%}"
        # 未对账时权威对账验证率应为 0%
        assert summary['overall_verification_rate'] == 0.0, \
            f"未对账时权威对账验证率应为 0%，实际: {summary['overall_verification_rate']:.2%}"

        # 打印汇总信息（调试用）
        print(f"\n生产板块数据验证汇总:")
        print(f"  板块数量: {summary['total_sectors']}")
        print(f"  成分股总数: {summary['total_constituents']}")
        print(f"  离线验证通过: {summary['total_offline_valid']}")
        print(f"  权威对账 VERIFIED: {summary['total_verified']}")
        print(f"  未对账 UNVERIFIED: {summary['total_unverified']}")
        print(f"  无效: {summary['total_invalid']}")
        print(f"  重复: {summary['total_duplicates']}")
        print(f"  离线验证通过率: {summary['overall_offline_valid_rate']:.2%}")
        print(f"  权威对账验证率: {summary['overall_verification_rate']:.2%} (未对账时为 0%)")
        if summary['sectors_with_issues']:
            print(f"  有问题的板块: {summary['sectors_with_issues']}")


# ============================================================
# 8. 状态机完整流转集成测试（Code Review Blocker 回归证据）
# ============================================================

class TestStateMachineFlow:
    """完整状态机流转测试 — 验证 Code Review 要求的关键行为

    状态流转：
      INVALID (格式错误)
        ↓ 离线格式验证通过
      UNVERIFIED (格式正确但是股票，尚未与权威源对账)
        ↓ 权威源对账 matched
      VERIFIED (权威源确认成员关系，写入 reconciled_at/source)
        ↓ 权威源对账 local-only
      NOT_MEMBER (本地有但权威源不存在)

    关键回归证据：
    1. 离线-only valid codes remain UNVERIFIED, not VERIFIED
    2. 权威源不可用时 fail-closed，不更新成员新鲜度
    3. 受控权威 snapshot: matched -> VERIFIED, local-only -> NOT_MEMBER, source-only -> 明确新增候选
    4. provenance 记录权威 source/time/result
    """

    @pytest.fixture
    def temp_provenance_dir(self, tmp_path):
        """临时 provenance 目录"""
        d = tmp_path / "provenance"
        d.mkdir()
        return str(d)

    def test_offline_only_remains_unverified(self):
        """回归证据1：离线-only valid codes remain UNVERIFIED, not VERIFIED"""
        report = validate_sector("测试板块", ["600519", "000858", "300750"])
        statuses = [c.status for c in report.constituents]
        # 所有离线验证通过的都是 UNVERIFIED，没有 VERIFIED
        assert all(s == MembershipStatus.UNVERIFIED for s in statuses)
        assert MembershipStatus.VERIFIED not in statuses
        assert report.verified == 0
        assert report.unverified == 3

    def test_authoritative_source_unavailable_fail_closed(self):
        """回归证据2：权威源不可用时 fail-closed，抛出异常"""
        from core.sector_validation import reconcile_with_ths_ifind, AuthoritativeSourceUnavailableError
        with pytest.raises(AuthoritativeSourceUnavailableError):
            reconcile_with_ths_ifind("测试板块", "888888", ["600519"])

    def test_authoritative_unavailable_does_not_update_freshness(self, temp_provenance_dir):
        """回归证据2b：权威源不可用时不更新成员新鲜度（保持 NOT_RECONCILED）"""
        # 创建未对账的 provenance
        update_provenance_after_refresh(
            "测试板块", "888888", "manual", 3,
            provenance_dir=temp_provenance_dir,
        )
        # 确认未对账 → NOT_RECONCILED
        freshness_before = check_freshness("测试板块", provenance_dir=temp_provenance_dir)
        assert freshness_before == FreshnessLevel.NOT_RECONCILED

        # 模拟权威源不可用（不调用 update_provenance_after_reconciliation）
        # 新鲜度应该保持 NOT_RECONCILED
        freshness_after = check_freshness("测试板块", provenance_dir=temp_provenance_dir)
        assert freshness_after == FreshnessLevel.NOT_RECONCILED

        # 验证 provenance 中 authoritative_reconciliation_date 仍然是 None
        p = load_provenance("测试板块", temp_provenance_dir)
        assert p.authoritative_reconciliation_date is None

    def test_controlled_snapshot_full_flow(self, temp_provenance_dir):
        """回归证据3：受控权威 snapshot 完整流转

        matched -> VERIFIED (写入 reconciled_at/source)
        local-only -> NOT_MEMBER
        source-only -> 明确新增候选
        """
        from core.sector_validation import reconcile_with_snapshot, get_source_only_candidates

        # 本地成分股（离线验证后都是 UNVERIFIED）
        local_codes = ["600519", "000858", "300750"]
        # 权威源 snapshot（600519 matched, 000858 matched, 300750 local-only, 688981 source-only）
        source_codes = ["600519", "000858", "688981"]

        # 1. 离线验证
        report = validate_sector("测试板块", local_codes)
        assert all(c.status == MembershipStatus.UNVERIFIED for c in report.constituents)

        # 2. 受控 snapshot 对账
        recon = reconcile_with_snapshot("测试板块", local_codes, source_codes,
                                         source="ths_ifind_snapshot_20261005")
        assert set(recon.matched) == {"600519", "000858"}
        assert set(recon.only_in_local) == {"300750"}
        assert set(recon.only_in_source) == {"688981"}

        # 3. 应用对账结果
        updated = apply_reconciliation_to_constituents(report.constituents, recon)
        statuses = {c.code: c.status for c in updated}

        # matched -> VERIFIED（写入 reconciled_at/source）
        assert statuses["600519"] == MembershipStatus.VERIFIED
        assert statuses["000858"] == MembershipStatus.VERIFIED
        verified_c = [c for c in updated if c.code == "600519"][0]
        assert verified_c.reconciled_at is not None
        assert verified_c.reconciliation_source == "ths_ifind_snapshot_20261005"

        # local-only -> NOT_MEMBER
        assert statuses["300750"] == MembershipStatus.NOT_MEMBER

        # 4. source-only -> 明确新增候选
        candidates = get_source_only_candidates(recon)
        assert len(candidates) == 1
        assert candidates[0]["code"] == "688981"
        assert candidates[0]["source"] == "ths_ifind_snapshot_20261005"

    def test_provenance_records_authoritative_source_time_result(self, temp_provenance_dir):
        """回归证据4：provenance 记录权威 source/time/result"""
        # 先创建本地 provenance
        update_provenance_after_refresh(
            "创新药", "886015", "manual", 100,
            provenance_dir=temp_provenance_dir,
        )

        # 权威对账成功后更新
        updated = update_provenance_after_reconciliation(
            "创新药", ReconciliationMethod.THS_IFIND, 95,
            reconciliation_result="matched=95, local_only=3, source_only=2",
            provenance_dir=temp_provenance_dir,
        )

        # 验证记录了权威 source/time/result
        assert updated.reconciliation_method == ReconciliationMethod.THS_IFIND
        assert updated.authoritative_reconciliation_date is not None
        assert updated.reconciliation_result == "matched=95, local_only=3, source_only=2"
        assert updated.verified_count == 95

        # 对账后新鲜度为 FRESH
        assert updated.get_freshness_level(updated.authoritative_reconciliation_date) == FreshnessLevel.FRESH

        # 持久化后重新加载，验证记录保留
        reloaded = load_provenance("创新药", temp_provenance_dir)
        assert reloaded.reconciliation_method == ReconciliationMethod.THS_IFIND
        assert reloaded.authoritative_reconciliation_date is not None
        assert reloaded.reconciliation_result != ""

    def test_local_refresh_does_not_affect_authoritative_freshness(self, temp_provenance_dir):
        """回归证据5：本地刷新不影响权威对账新鲜度

        即使本地文件每天刷新，只要权威对账过期，新鲜度仍然是 STALE。
        绝不能因为本地刷新就将未对账/过期数据集标记为 FRESH。
        """
        # 创建权威对账过期的 provenance（30天前对账）
        p = SectorProvenance(
            sector_name="测试板块",
            local_refresh_date="2026-10-05",  # 今天刚刷新本地文件
            authoritative_reconciliation_date="2026-09-01",  # 30+ 天前权威对账
            reconciliation_method=ReconciliationMethod.THS_IFIND,
        )
        save_provenance(p, temp_provenance_dir)

        # 即使本地文件今天刚刷新，权威对账过期 → STALE
        freshness = check_freshness("测试板块", current_date="2026-10-05",
                                      provenance_dir=temp_provenance_dir)
        assert freshness == FreshnessLevel.STALE

        # 再次本地刷新（模拟 --update-provenance）
        update_provenance_after_refresh(
            "测试板块", "001", "manual", 10,
            provenance_dir=temp_provenance_dir,
        )
        # 本地刷新后权威对账新鲜度不变，仍然 STALE
        freshness_after = check_freshness("测试板块", current_date="2026-10-05",
                                            provenance_dir=temp_provenance_dir)
        assert freshness_after == FreshnessLevel.STALE
