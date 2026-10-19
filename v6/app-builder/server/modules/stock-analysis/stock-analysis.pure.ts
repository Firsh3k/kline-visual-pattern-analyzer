/**
 * BFF 纯函数（无 Nest 依赖，CI 用 tests/v6/test_bff_pure.js 直接测试）。
 *
 * R67-1：扫描 / 自选股列表的“确认 / 主导”全部透传 Python core 信号层（summary.dominant、
 *        summary.patterns_latest[pid].confirmed），BFF 不再按阶段名或分数另立择优规则。
 * R67-3：asOf 与 Python core.analysis.parse_as_of 同一口径：YYYY-MM-DD、真实存在、不晚于北京时间今天。
 */

export const SIGNAL_RULE_VERSION = 'signals-1';

export class InvalidRequestError extends Error {
  readonly statusCode = 400;
}

/** Asia/Shanghai 当前日期 YYYY-MM-DD（与 core.analysis.today_cn 一致） */
export function todayShanghai(now: Date = new Date()): string {
  const cn = new Date(now.getTime() + 8 * 3600 * 1000);
  return cn.toISOString().slice(0, 10);
}

/** asOf 校验；undefined / 空串 = 最新。非法直接抛 InvalidRequestError（不取行情）。 */
export function assertAsOf(asOf: string | undefined | null, now: Date = new Date()): string | undefined {
  if (asOf === undefined || asOf === null || asOf === '') return undefined;
  if (typeof asOf !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(asOf)) {
    throw new InvalidRequestError(`asOf 必须为 YYYY-MM-DD：${JSON.stringify(asOf)}`);
  }
  const d = new Date(`${asOf}T00:00:00Z`);
  if (Number.isNaN(d.getTime()) || d.toISOString().slice(0, 10) !== asOf) {
    throw new InvalidRequestError(`asOf 日期不存在：${asOf}`);
  }
  const today = todayShanghai(now);
  if (asOf > today) {
    throw new InvalidRequestError(`asOf ${asOf} 晚于今天（${today}，北京时间），不能分析未来日期`);
  }
  return asOf;
}

/**
 * 扫描列表项：字段全部来自 Python core 的 summary（与个股页同一份 doc.signals）。
 * bull_top / bear_top = Python 给出的该方向主导【已确认】形态；没有已确认形态即为 null。
 * summary 缺少信号层或版本不符 → 抛错（该只股票记为失败，不降级展示）。
 */
export function buildBatchItem(doc: Record<string, any>): Record<string, unknown> {
  const summary = doc?.summary;
  if (!summary || typeof summary !== 'object') throw new Error('Python 返回缺少 summary');
  if (summary.signal_rule_version !== SIGNAL_RULE_VERSION) {
    throw new Error(`信号规则版本不符（期望 ${SIGNAL_RULE_VERSION}，实际 ${summary.signal_rule_version ?? '缺失'}）`);
  }
  const latest = summary.patterns_latest;
  const dominant = summary.dominant;
  if (!latest || typeof latest !== 'object' || !dominant || typeof dominant !== 'object') {
    throw new Error('Python summary 缺少 patterns_latest / dominant');
  }

  const patterns: Record<string, any> = {};
  for (const [pid, p] of Object.entries(latest as Record<string, any>)) {
    if (typeof p.confirmed !== 'boolean' || typeof p.active !== 'boolean') {
      throw new Error(`Python summary.patterns_latest.${pid} 缺少 confirmed / active`);
    }
    patterns[pid] = {
      pid,
      name: p.pattern_name,
      direction: p.direction,
      category: p.category,
      stage: p.stage,
      score: p.score,
      confidence: p.confidence,
      active: p.active,
      confirmed: p.confirmed,
      reasons: p.reasons ?? [],
      last_key_date: p.last_key_date,
      bars_since: p.bars_since,
    };
  }

  const topOf = (pid: string | null | undefined) => {
    if (pid === null || pid === undefined) return null;
    const p = patterns[pid];
    if (!p || p.confirmed !== true) throw new Error(`Python dominant 指向的 ${pid} 不是已确认形态`);
    return { pid, name: p.name, stage: p.stage, score: p.score, last_key_date: p.last_key_date, confirmed: true };
  };

  const pct = typeof summary.change_pct === 'number' ? summary.change_pct.toFixed(2) : '0.00';
  return {
    code: String(doc.code ?? summary.code ?? ''),
    name: String(doc.name ?? summary.name ?? ''),
    as_of: String(doc.as_of ?? summary.as_of ?? ''),
    price: summary.close ?? null,
    pct,
    pct_num: summary.change_pct ?? 0,
    patterns,
    bull_top: topOf(dominant.bull),
    bear_top: topOf(dominant.bear),
    dominant: dominant.overall ?? null,
    signal_rule_version: summary.signal_rule_version,
    engine: 'real_core',
    adjustment: doc.input?.adjustment ?? 'qfq',
    stale: doc.calendar?.stale ?? summary.stale ?? false,
    session_state: doc.session_state ?? summary.session_state ?? null,
    data_hash: summary.data_hash ?? null,
    result_sha256: summary.result_sha256 ?? null,
  };
}
