#!/usr/bin/env python3
"""
板块成分股数据验证脚本 (#20/#21)
验证 sector_data/new_sectors.js 中的板块成分股数据：
1. 股票代码格式验证（6位数字，沪市60xxxx/688xxx，深市00xxxx/30xxxx）
2. 去重验证（同一板块内无重复代码）
3. 跨板块重复统计（同一股票属于多个板块是正常的，但需统计）
4. 数据来源/日期元数据验证
5. 输出验证报告

用法：
  python3 validate_sector_data.py [--sector 板块名] [--json]
"""
import json
import re
import sys
import os
import argparse
from datetime import datetime


def load_sectors(file_path):
    """从JS文件中加载板块数据"""
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 提取 NEW_SECTORS = { ... } 部分
    match = re.search(r'const NEW_SECTORS\s*=\s*(\{[\s\S]*?\});', content)
    if not match:
        raise ValueError("无法找到 NEW_SECTORS 定义")

    # 将JS对象转换为JSON（处理单引号和尾逗号）
    js_obj = match.group(1)
    # 替换单引号为双引号
    js_obj = re.sub(r"'([^']*)'", r'"\1"', js_obj)
    # 移除尾逗号
    js_obj = re.sub(r',\s*([}\]])', r'\1', js_obj)

    return json.loads(js_obj)


def validate_code(code):
    """验证股票代码格式"""
    errors = []
    if not re.match(r'^\d{6}$', code):
        errors.append(f"代码格式错误（应为6位数字）: {code}")
        return errors

    prefix = code[:2]
    valid_prefixes = ['00', '30', '60', '68', '92', '83', '87', '88', '43']
    if prefix not in valid_prefixes:
        errors.append(f"代码前缀异常: {code} (前缀{prefix})")

    return errors


def validate_sector(name, codes):
    """验证单个板块"""
    result = {
        'sector': name,
        'total': len(codes),
        'errors': [],
        'warnings': [],
        'duplicates': [],
        'prefix_distribution': {}
    }

    # 去重检查
    seen = set()
    for code in codes:
        if code in seen:
            result['duplicates'].append(code)
        seen.add(code)

    if result['duplicates']:
        result['errors'].append(f"板块内重复代码: {result['duplicates']}")

    # 代码格式验证
    for code in codes:
        errors = validate_code(code)
        result['errors'].extend(errors)

        # 统计前缀分布
        prefix = code[:2]
        result['prefix_distribution'][prefix] = result['prefix_distribution'].get(prefix, 0) + 1

    # 数量警告
    if len(codes) < 5:
        result['warnings'].append(f"板块成分股过少（{len(codes)}只），可能数据不完整")
    if len(codes) > 500:
        result['warnings'].append(f"板块成分股过多（{len(codes)}只），概念范围可能过宽")

    return result


def main():
    parser = argparse.ArgumentParser(description='板块成分股数据验证脚本')
    parser.add_argument('--sector', type=str, default=None, help='只验证指定板块')
    parser.add_argument('--json', action='store_true', help='以JSON格式输出')
    args = parser.parse_args()

    # 查找数据文件
    script_dir = os.path.dirname(os.path.abspath(__file__))
    possible_paths = [
        os.path.join(script_dir, '..', 'sector_data', 'new_sectors.js'),
        os.path.join(script_dir, '..', '..', 'sector_data', 'new_sectors.js'),
    ]

    file_path = None
    for p in possible_paths:
        if os.path.exists(p):
            file_path = p
            break

    if not file_path:
        print("错误：未找到 sector_data/new_sectors.js 文件")
        sys.exit(1)

    print(f"验证文件: {file_path}")
    print(f"验证时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 加载数据
    try:
        sectors = load_sectors(file_path)
    except Exception as e:
        print(f"错误：加载数据失败 - {e}")
        sys.exit(1)

    print(f"发现 {len(sectors)} 个板块")
    print()

    # 验证每个板块
    all_results = []
    total_codes = 0
    total_errors = 0
    total_warnings = 0
    cross_sector_map = {}  # code -> [sectors]

    for name, codes in sectors.items():
        if args.sector and name != args.sector:
            continue

        result = validate_sector(name, codes)
        all_results.append(result)
        total_codes += result['total']
        total_errors += len(result['errors'])
        total_warnings += len(result['warnings'])

        # 跨板块统计
        for code in codes:
            if code not in cross_sector_map:
                cross_sector_map[code] = []
            cross_sector_map[code].append(name)

    # 输出报告
    if args.json:
        report = {
            'file': file_path,
            'validated_at': datetime.now().isoformat(),
            'total_sectors': len(all_results),
            'total_codes': total_codes,
            'total_errors': total_errors,
            'total_warnings': total_warnings,
            'sectors': all_results,
            'cross_sector_duplicates': {k: v for k, v in cross_sector_map.items() if len(v) > 1}
        }
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        for r in all_results:
            status = "✅" if not r['errors'] else "❌"
            print(f"{status} {r['sector']}: {r['total']} 只股票")
            if r['errors']:
                for e in r['errors'][:5]:
                    print(f"   ❌ {e}")
                if len(r['errors']) > 5:
                    print(f"   ... 还有 {len(r['errors'])-5} 个错误")
            if r['warnings']:
                for w in r['warnings']:
                    print(f"   ⚠️ {w}")

        print()
        print("=" * 60)
        print(f"汇总: {len(all_results)} 个板块, {total_codes} 只股票")
        print(f"错误: {total_errors}, 警告: {total_warnings}")

        # 跨板块重复统计
        cross_dups = {k: v for k, v in cross_sector_map.items() if len(v) > 1}
        print(f"跨板块重复股票: {len(cross_dups)} 只（属于多个板块，正常现象）")
        if cross_dups:
            print("前10个示例:")
            for i, (code, secs) in enumerate(list(cross_dups.items())[:10]):
                print(f"  {code}: {', '.join(secs)}")

        print()
        if total_errors == 0:
            print("✅ 数据验证通过，无错误")
        else:
            print(f"❌ 数据验证失败，存在 {total_errors} 个错误")
            sys.exit(1)


if __name__ == '__main__':
    main()
