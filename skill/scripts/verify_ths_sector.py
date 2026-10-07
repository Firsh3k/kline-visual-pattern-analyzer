#!/usr/bin/env python3
"""
FIR-22 — 同花顺板块权威验证脚本（Skill 生产路径）

生产工作流：
1. Agent 调用 iFinD MCP search_securities 工具，查询"同花顺板块代码{code}的所有成分股"
2. 将 MCP 返回的 JSON 通过 stdin 传给本脚本
3. 脚本解析响应，验证成分股，保存 provenance
4. 输出验证结果（板块名称、成分股数量、provenance 路径）

用法：
  echo '<mcp_json>' | python3 verify_ths_sector.py <sector_code>
  python3 verify_ths_sector.py <sector_code> --input mcp_response.json
  python3 verify_ths_sector.py <sector_code> --snapshot '{"sectorName":"...","constituents":[...]}'

Fail-closed 原则：
- 无效板块代码 → 退出码 2
- 空成分股 → 退出码 3
- 缺失权威 source/as-of → 退出码 4
- 验证成功 → 退出码 0
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# 将项目根目录加入 path，以便导入 core 模块
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# skill/scripts/ -> skill/ -> workspace/.user_skills/ -> workspace/
# 项目根目录可能在其他位置，通过环境变量或相对路径查找
PROJECT_ROOT = os.environ.get(
    'KLINE_PROJECT_ROOT',
    os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..', '..', '..', 'Doubao', 'chats', '*'))
)
# 尝试多个可能的项目根目录
CANDIDATE_ROOTS = [
    os.environ.get('KLINE_PROJECT_ROOT', ''),
    '/home/user/Doubao/chats/38444703140272130/kline_pattern_scanner',
    os.path.abspath(os.path.join(SCRIPT_DIR, '..', '..', '..', '..', 'Doubao', 'chats', '38444703140272130', 'kline_pattern_scanner')),
]
for root in CANDIDATE_ROOTS:
    if root and os.path.exists(os.path.join(root, 'core', '__init__.py')):
        sys.path.insert(0, root)
        PROJECT_ROOT = root
        break
else:
    print("ERROR: Cannot find project root with core/ module", file=sys.stderr)
    print("Set KLINE_PROJECT_ROOT environment variable", file=sys.stderr)
    sys.exit(1)

from core.sector_validation import (
    THSSectorResolver,
    IFindMCPProvider,
    SnapshotProvider,
    SectorCodeValidationError,
    ConstituentUnavailableError,
    AuthoritativeSourceUnavailableError,
    validate_ths_sector_code,
)


def parse_ifind_mcp_response(raw_json):
    """
    解析 iFinD MCP search_securities 返回的 JSON。

    支持的格式：
    1. {"securities": [{"code": "...", "name": "..."}, ...]}
    2. {"data": {"securities": [...]}}
    3. {"result": {"securities": [...]}}
    4. [{"code": "...", "name": "..."}, ...]  (直接数组)
    5. {"securities": ["600519", "000858", ...]}  (字符串数组)

    Returns:
        dict: {"constituents": [...], "sector_name": str|None, "as_of": str|None}
    """
    if isinstance(raw_json, str):
        data = json.loads(raw_json)
    else:
        data = raw_json

    constituents = []
    sector_name = None
    as_of = None

    # 提取 securities 列表
    securities = None
    if isinstance(data, list):
        securities = data
    elif isinstance(data, dict):
        # 尝试常见的包装格式
        for key in ['securities', 'data', 'result', 'items', 'list']:
            if key in data:
                val = data[key]
                if isinstance(val, list):
                    securities = val
                    break
                elif isinstance(val, dict):
                    # 可能还有一层包装
                    for sub_key in ['securities', 'items', 'list']:
                        if sub_key in val and isinstance(val[sub_key], list):
                            securities = val[sub_key]
                            break
                    if securities:
                        break

        # 提取元数据
        for key in ['sectorName', 'sector_name', 'name', 'title']:
            if key in data and isinstance(data[key], str):
                sector_name = data[key]
                break
        for key in ['asOf', 'as_of', 'date', 'timestamp', 'updatedAt']:
            if key in data and isinstance(data[key], str):
                as_of = data[key]
                break

    if securities is None:
        raise ValueError("Cannot find securities list in MCP response")

    # 解析成分股
    for item in securities:
        if isinstance(item, str):
            constituents.append(item.strip())
        elif isinstance(item, dict):
            code = item.get('code') or item.get('securityCode') or item.get('stockCode') or item.get('symbol')
            if code:
                constituents.append(str(code).strip())

    # 去重
    constituents = list(dict.fromkeys(constituents))

    return {
        'constituents': constituents,
        'sector_name': sector_name,
        'as_of': as_of,
    }


def main():
    parser = argparse.ArgumentParser(
        description='FIR-22: 同花顺板块权威验证（Skill 生产路径）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  # 从 stdin 读取 iFinD MCP 响应
  echo '{"securities":[{"code":"600519"}]}' | python3 verify_ths_sector.py 886032

  # 从文件读取
  python3 verify_ths_sector.py 886032 --input mcp_response.json

  # 使用 snapshot（测试用）
  python3 verify_ths_sector.py 886032 --snapshot '{"sectorName":"固态电池","constituents":["600519"]}'

退出码：
  0 - 验证成功
  1 - 通用错误
  2 - 无效板块代码
  3 - 空成分股
  4 - 缺失权威 source/as-of
        """
    )
    parser.add_argument('sector_code', help='同花顺板块指数代码（如 886032）')
    parser.add_argument('--input', '-i', help='从文件读取 iFinD MCP JSON 响应（默认从 stdin）')
    parser.add_argument('--snapshot', help='直接提供 snapshot JSON（测试用，格式：{"sectorName":"...","constituents":[...]}）')
    parser.add_argument('--source', default='同花顺 iFinD', help='权威数据源名称（默认：同花顺 iFinD）')
    parser.add_argument('--as-of', help='权威数据有效日期（YYYY-MM-DD），如不提供则使用 MCP 响应中的日期')
    parser.add_argument('--provenance-dir', help='provenance 保存目录（默认：项目 sector_data/provenance/）')
    parser.add_argument('--quiet', '-q', action='store_true', help='静默模式，只输出结果 JSON')

    args = parser.parse_args()

    # 1. 验证板块代码格式
    try:
        if not validate_ths_sector_code(args.sector_code):
            print(f"ERROR: Invalid sector code format: {args.sector_code}", file=sys.stderr)
            sys.exit(2)
    except SectorCodeValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)

    # 2. 获取输入数据
    if args.snapshot:
        # Snapshot 模式（测试用）
        try:
            snapshot_data = json.loads(args.snapshot)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid snapshot JSON: {e}", file=sys.stderr)
            sys.exit(1)

        constituents = snapshot_data.get('constituents', [])
        sector_name = snapshot_data.get('sectorName') or snapshot_data.get('sector_name')
        as_of = args.as_of or snapshot_data.get('asOf') or snapshot_data.get('as_of')
        source = args.source

        # Snapshot 模式必须明确提供 source 和 as_of（fail-closed）
        if not source or source == 'controlled_snapshot':
            print("ERROR: Snapshot mode requires explicit authoritative source (not 'controlled_snapshot')", file=sys.stderr)
            sys.exit(4)
        if not as_of:
            print("ERROR: Snapshot mode requires explicit authoritative as-of date", file=sys.stderr)
            sys.exit(4)

        provider = SnapshotProvider(snapshots={
            args.sector_code: {
                'sectorName': sector_name,
                'constituents': constituents,
                'source': source,
                'asOf': as_of,
            }
        })
    else:
        # 从 stdin 或文件读取 iFinD MCP 响应
        if args.input:
            with open(args.input, 'r') as f:
                raw_input = f.read()
        else:
            raw_input = sys.stdin.read()

        if not raw_input.strip():
            print("ERROR: No input data. Provide iFinD MCP JSON via stdin or --input", file=sys.stderr)
            sys.exit(1)

        try:
            parsed = parse_ifind_mcp_response(raw_input)
        except (json.JSONDecodeError, ValueError) as e:
            print(f"ERROR: Failed to parse MCP response: {e}", file=sys.stderr)
            sys.exit(1)

        constituents = parsed['constituents']
        sector_name = parsed['sector_name']
        as_of = args.as_of or parsed['as_of']
        source = args.source

        # Fail-closed：必须有权威 as-of
        if not as_of:
            print("ERROR: Missing authoritative as-of timestamp in MCP response. Provide --as-of", file=sys.stderr)
            sys.exit(4)

        # 创建一个 mock provider，直接返回解析后的数据
        # （实际的 MCP 调用由 Agent 完成，这里只做验证和持久化）
        def mock_search_fn(query, market='股票'):
            return {
                'securities': [{'code': c} for c in constituents],
                'sectorName': sector_name,
                'asOf': as_of,
            }

        provider = IFindMCPProvider(search_fn=mock_search_fn)

    # 3. 验证成分股非空
    if not constituents:
        print(f"ERROR: Empty constituent list for sector {args.sector_code}", file=sys.stderr)
        sys.exit(3)

    # 4. 设置 provenance 目录
    provenance_dir = args.provenance_dir or os.path.join(PROJECT_ROOT, 'sector_data', 'provenance')
    os.makedirs(provenance_dir, exist_ok=True)

    # 5. 执行验证
    try:
        resolver = THSSectorResolver(provider=provider, provenance_dir=provenance_dir)
        sector = resolver.resolve(args.sector_code)
    except AuthoritativeSourceUnavailableError as e:
        print(f"ERROR: Authoritative source unavailable: {e}", file=sys.stderr)
        sys.exit(4)
    except ConstituentUnavailableError as e:
        print(f"ERROR: Constituent unavailable: {e}", file=sys.stderr)
        sys.exit(3)
    except Exception as e:
        print(f"ERROR: Resolution failed: {e}", file=sys.stderr)
        sys.exit(1)

    # 6. 输出结果
    result = {
        'sectorCode': sector.sector_code,
        'sectorName': sector.sector_name,
        'constituentCount': sector.constituent_count,
        'constituents': sector.constituents,
        'source': sector.source,
        'provenance': {
            'reconciliationDate': sector.provenance.authoritative_reconciliation_date if sector.provenance else None,
            'reconciliationMethod': sector.provenance.reconciliation_method.value if sector.provenance else None,
            'verifiedCount': sector.provenance.verified_count if sector.provenance else 0,
            'provenanceFile': sector.provenance.provenance_file if sector.provenance else None,
        },
        'verified': True,
    }

    if args.quiet:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"✅ 同花顺板块验证成功")
        print(f"   板块代码: {sector.sector_code}")
        print(f"   板块名称: {sector.sector_name}")
        print(f"   成分股数量: {sector.constituent_count}")
        print(f"   权威来源: {sector.source}")
        if sector.provenance:
            print(f"   对账日期: {sector.provenance.authoritative_reconciliation_date}")
            print(f"   对账方式: {sector.provenance.reconciliation_method.value}")
            print(f"   Provenance文件: {sector.provenance.provenance_file}")
        print()
        print("下一步：将验证结果同步到 GitHub 配置")
        print(f"  板块标识: ths_sector_index:{sector.sector_code}")
        print(f"  成分股: {','.join(sector.constituents[:5])}{'...' if sector.constituent_count > 5 else ''}")
        print()
        print("结果 JSON:")
        print(json.dumps(result, ensure_ascii=False, indent=2))

    return 0


if __name__ == '__main__':
    sys.exit(main())
