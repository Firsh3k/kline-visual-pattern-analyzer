/* 展示用文字与格式化。只做“显示”，不做任何形态 / 指标计算。 */
(function () {
  'use strict';
  const STAGE_BULL = { forming: '构建中', pre_break: '突破前夜', breakout_day: '突破当日', post_break: '已突破',
    retest: '回踩确认', failed_break: '突破失败', complete: '完成' };
  const STAGE_BEAR = { forming: '构建中', pre_break: '跌破前夜', breakout_day: '跌破当日', post_break: '已跌破',
    retest: '反抽确认', failed_break: '跌破失败', complete: '完成' };
  // 名称 / 方向 / 结构依据 —— 仅用于“未识别”占位行；识别结果的名称与类别一律取自后端
  const TYPES = [
    ['w_bottom', 'W底', 'bull', '摆动点结构', 'price_pattern'], ['hs_bottom', '头肩底', 'bull', '摆动点结构', 'price_pattern'],
    ['platform_break', '平台突破', 'bull', '平台区间', 'price_pattern'], ['m_top', 'M头', 'bear', '摆动点结构', 'price_pattern'],
    ['hs_top', '头肩顶', 'bear', '摆动点结构', 'price_pattern'], ['platform_break_down', '平台破位', 'bear', '平台区间', 'price_pattern'],
    ['ma_bull', '均线多头', 'bull', '均线排列', 'trend_state'], ['ma_bear', '均线空头', 'bear', '均线排列', 'trend_state'],
  ];
  const CATS = [['price_pattern', '价格形态', '由价格结构（高低点、平台区间）识别'],
    ['trend_state', '趋势状态', '由均线排列判断，描述当前趋势，不是价格形态']];
  const EV_LABEL = {
    aligned_ma5_ma10: 'MA5 与 MA10 已按方向排列', aligned_ma10_ma20: 'MA10 与 MA20 已按方向排列',
    aligned_ma20_ma60: 'MA20 与 MA60 已按方向排列', fully_aligned: '四条均线完全排列',
    close_beyond_ma5: '收盘在 MA5 外侧（顺方向）', ma5_slope_5d: 'MA5 五日变化', ma5_slope_ok: 'MA5 斜率顺方向',
    range_pct: '平台振幅', platform_start_date: '平台起始', platform_end_date: '平台结束',
    breakout_line: '突破判定线', broke: '收盘已越过判定线', broke_today: '今日首次越过',
    near_line: '接近判定线（3% 内）', had_break_10d: '近 10 日曾越过 2%', had_break_prior_9d: '此前 9 日曾越过 2%',
    retest: '回踩 / 回抽判定', volume_confirmed: '放量确认（否 = 缩量待确认）', ma_converge: 'MA5 与 MA20 粘合（< 6%）',
    v4_stage: 'V4 原顺序的阶段结论',
    close_beyond_breakout_line: '当前收盘在判定线外', breakout_occurred: '已发生突破', breakout_date: '突破日',
    breakout_close: '突破日收盘', bars_since_breakout: '突破后 K 线数', back_inside_neckline: '已回到颈线内',
    days_back_inside: '连续回到颈线内天数', failed_break_days: '判失败所需天数',
  };
  const SESSION = { close_confirmed: ['已收盘确认', 'ok'], intraday: ['盘中数据（最后一根未收盘）', 'warn'],
    unknown: ['交易日历未核实', 'warn'] };
  const ADJ = { qfq: '前复权', none: '不复权', unknown: '复权方式未声明' };

  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined || v === '' ? '—' : (typeof v === 'number' ? v.toFixed(d) : esc(v)));
  const pct = (v, d = 1) => (v === null || v === undefined ? 'N/A' : (v * 100).toFixed(d) + '%');
  const sign = (v) => (Number(v) > 0 ? '+' : '') + v;
  const cls = (v) => (v > 0 ? 'up' : v < 0 ? 'down' : '');
  const stageText = (x) => (x.direction === 'bear' ? STAGE_BEAR : STAGE_BULL)[x.stage] || x.stage;
  const typeName = (pid) => (TYPES.find((t) => t[0] === pid) || [pid, pid])[1];
  const evValue = (k, v) => {
    if (v === null || v === undefined) return '—';
    if (typeof v === 'boolean') return v ? '是' : '否';
    if (k === 'range_pct') return (v * 100).toFixed(2) + '%';
    return esc(v);
  };

  window.F = { STAGE_BULL, STAGE_BEAR, TYPES, CATS, EV_LABEL, SESSION, ADJ, esc, fmt, pct, sign, cls, stageText, typeName, evValue };
})();
