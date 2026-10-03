/**
 * Kline Pattern Analyzer - JS Core Module (Minimal Cross-Runtime Parity)
 * 从 kline_analyzer.html 提取的纯算法函数，不依赖 DOM，可在 Node.js 中运行
 * 用于与 Python 端做 cross-runtime parity 对比
 *
 * 覆盖领域（QA 优先级）：
 * 1. Volume / volStatus / relation
 * 2. Bollinger squeeze
 * 3. Fake Signal Risk
 * 4. H&S breakout / breakdown state
 *
 * 完整的 HTML → 独立 JS core module 重构放 V5
 */

'use strict';

// ============ 均线计算 ============
function calcMA(data, n) {
  return data.map((_, i) => {
    if (i < n - 1) return null;
    let sum = 0;
    for (let j = i - n + 1; j <= i; j++) sum += data[j].close;
    return sum / n;
  });
}

// ============ 技术指标计算 ============
function calcIndicators(data) {
  const ma5 = calcMA(data, 5), ma10 = calcMA(data, 10), ma20 = calcMA(data, 20), ma60 = calcMA(data, 60);
  const vma5 = calcMA(data.map(d => ({close: d.volume})), 5);
  const vma20 = calcMA(data.map(d => ({close: d.volume})), 20);
  // MACD
  const ema12 = [], ema26 = [], dif = [], dea = [], macd = [];
  let e12 = data[0].close, e26 = data[0].close;
  for (let i = 0; i < data.length; i++) {
    e12 = i === 0 ? data[0].close : e12 * 11/13 + data[i].close * 2/13;
    e26 = i === 0 ? data[0].close : e26 * 25/27 + data[i].close * 2/27;
    ema12.push(e12); ema26.push(e26);
    dif.push(e12 - e26);
    dea.push(i === 0 ? dif[0] : dea[i-1] * 8/10 + dif[i] * 2/10);
    macd.push((dif[i] - dea[i]) * 2);
  }
  // RSI (14日)
  const rsi = [];
  for (let i = 0; i < data.length; i++) {
    if (i < 14) { rsi.push(50); continue; }
    let gains = 0, losses = 0;
    for (let j = i - 13; j <= i; j++) {
      const change = data[j].close - data[j-1].close;
      if (change > 0) gains += change; else losses -= change;
    }
    const avgGain = gains / 14, avgLoss = losses / 14;
    rsi.push(avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss));
  }
  // BOLL (20, 2)
  const boll_mid = [], boll_up = [], boll_low = [], boll_width = [];
  for (let i = 0; i < data.length; i++) {
    if (i < 19) { boll_mid.push(data[i].close); boll_up.push(data[i].close); boll_low.push(data[i].close); boll_width.push(0); continue; }
    const slice20 = data.slice(i - 19, i + 1).map(d => d.close);
    const mid = slice20.reduce((a,b) => a+b, 0) / 20;
    const variance = slice20.reduce((a,b) => a + (b-mid)**2, 0) / 20;
    const std = Math.sqrt(variance);
    boll_mid.push(mid); boll_up.push(mid + 2*std); boll_low.push(mid - 2*std);
    boll_width.push(mid > 0 ? (boll_up[i] - boll_low[i]) / mid * 100 : 0);
  }
  return { ma5, ma10, ma20, ma60, vma5, vma20, dif, dea, macd, rsi, boll_mid, boll_up, boll_low, boll_width };
}

// ============ 量价分析 ============
function analyzeVolume(data, ind) {
  const n = data.length;
  const last = n - 1;
  const v5 = ind.vma5[last] || 0;
  const v20 = ind.vma20[last] || 0;
  const volRatio = v20 > 0 ? (v5 / v20) : 1;
  const recent5 = data.slice(n - 5);
  const priceUp = recent5[4].close > recent5[0].close;
  const volUp = v5 > (ind.vma5[n-6] || v5);
  let relation = '量价平稳';
  if (priceUp && volUp) relation = '量价齐升（健康上涨）';
  else if (priceUp && !volUp) relation = '价涨量缩（上涨乏力，警惕）';
  else if (!priceUp && volUp) relation = '价跌量增（抛压较重）';
  else if (!priceUp && !volUp) relation = '价跌量缩（正常回调）';
  const todayVol = data[last].volume;
  const volStatus = todayVol > v5 * 1.5 ? '放量' : todayVol < v5 * 0.7 ? '缩量' : '正常';
  return { volRatio: volRatio.toFixed(2), relation, volStatus, v5: Math.round(v5), v20: Math.round(v20) };
}

// ============ 布林带分析 ============
function detectBollinger(data, ind) {
  const n = data.length;
  const last = n - 1;
  const curPrice = data[last].close;
  const up = ind.boll_up[last], low = ind.boll_low[last], mid = ind.boll_mid[last];
  const width = ind.boll_width[last];

  // 计算120日内布林带宽度百分位
  const widths = ind.boll_width.slice(Math.max(0, n-120));
  const sortedWidths = [...widths].sort((a,b) => a-b);
  const widthPercentile = sortedWidths.findIndex(w => w >= width) / sortedWidths.length * 100;
  const isSqueeze = widthPercentile < 25;

  // 突破上轨/下轨
  const breakUp = curPrice > up;
  const breakDown = curPrice < low;

  // 收口后突破（高可靠性信号）
  const squeezeBreakUp = isSqueeze && breakUp;
  const squeezeBreakDown = isSqueeze && breakDown;

  let signal = '正常波动';
  let score = 0;
  if (squeezeBreakUp) { signal = '收口后放量突破上轨（高可靠）'; score = 12; }
  else if (squeezeBreakDown) { signal = '收口后跌破下轨（高可靠）'; score = -12; }
  else if (breakUp) { signal = '突破上轨（强势）'; score = 5; }
  else if (breakDown) { signal = '跌破下轨（弱势）'; score = -5; }
  else if (isSqueeze) { signal = '布林带收口，变盘在即'; score = 0; }

  return {
    up: up.toFixed(2), mid: mid.toFixed(2), low: low.toFixed(2),
    width: width.toFixed(2), widthPercentile: widthPercentile.toFixed(0),
    isSqueeze, breakUp, breakDown, squeezeBreakUp, squeezeBreakDown,
    signal, score
  };
}

// ============ MACD背离检测 ============
function detectMACDDivergence(data, ind) {
  const n = data.length;
  const last = n - 1;
  if (n < 60) return { topDivergence: 0, bottomDivergence: 0, detail: '数据不足' };

  // 找最近60日内的价格高点和MACD高点
  const lookback = Math.min(60, n - 1);
  const startIdx = n - lookback;
  let priceHighIdx = startIdx, macdHighIdx = startIdx;
  let priceLowIdx = startIdx, macdLowIdx = startIdx;

  for (let i = startIdx; i <= last; i++) {
    if (data[i].high > data[priceHighIdx].high) priceHighIdx = i;
    if (ind.macd[i] > ind.macd[macdHighIdx]) macdHighIdx = i;
    if (data[i].low < data[priceLowIdx].low) priceLowIdx = i;
    if (ind.macd[i] < ind.macd[macdLowIdx]) macdLowIdx = i;
  }

  // 顶背离：价格创新高但MACD未创新高
  let topDiv = 0;
  if (priceHighIdx > macdHighIdx && data[last].close > data[priceHighIdx].close * 0.98) {
    const priceDiff = (data[last].close - data[priceHighIdx].close) / data[priceHighIdx].close * 100;
    const macdDiff = (ind.macd[last] - ind.macd[priceHighIdx]) / Math.abs(ind.macd[priceHighIdx] || 1) * 100;
    if (priceDiff > 0 && macdDiff < 0) topDiv = Math.min(100, Math.abs(priceDiff) + Math.abs(macdDiff));
  }

  // 底背离：价格创新低但MACD未创新低
  let bottomDiv = 0;
  if (priceLowIdx > macdLowIdx && data[last].close < data[priceLowIdx].close * 1.02) {
    const priceDiff = (data[last].close - data[priceLowIdx].close) / data[priceLowIdx].close * 100;
    const macdDiff = (ind.macd[last] - ind.macd[priceLowIdx]) / Math.abs(ind.macd[priceLowIdx] || 1) * 100;
    if (priceDiff < 0 && macdDiff > 0) bottomDiv = Math.min(100, Math.abs(priceDiff) + Math.abs(macdDiff));
  }

  return { topDivergence: topDiv, bottomDivergence: bottomDiv };
}

// ============ 假信号风险评估 ============
function calcFakeSignalRisk(data, ind, bestType, bestScore, marketEnv, macdDiv, vol) {
  const risks = [];
  let riskScore = 0;
  const n = data.length;
  const last = n - 1;
  const curPrice = data[last].close;
  const ma60 = ind.ma60[last] || curPrice;
  const distFromMa60 = Math.abs(curPrice - ma60) / ma60 * 100;

  // 1. 成交量配合度
  const vma5 = ind.vma5 ? ind.vma5[last] : 0;
  const vma20 = ind.vma20 ? ind.vma20[last] : 0;
  const volRatio = vma20 > 0 ? vma5 / vma20 : 1;
  if (bestType === 'bull' && volRatio < 1.0) {
    risks.push({ label: '缩量突破', weight: 20 });
    riskScore += 20;
  }
  if (bestType === 'bear' && volRatio < 1.0) {
    risks.push({ label: '缩量破位', weight: 25 });
    riskScore += 25;
  }

  // 2. 大盘环境适配度
  if (marketEnv) {
    const envScore = marketEnv.score;
    if (bestType === 'bull' && envScore < 45) {
      risks.push({ label: '逆大盘买入', weight: 20 });
      riskScore += 20;
    }
    if (bestType === 'bear' && envScore >= 60) {
      risks.push({ label: '逆大盘卖出', weight: 20 });
      riskScore += 20;
    }
  }

  // 3. MACD背离
  if (bestType === 'bull' && macdDiv && macdDiv.topDivergence > 30) {
    risks.push({ label: 'MACD顶背离', weight: 15 });
    riskScore += 15;
  }
  if (bestType === 'bear' && macdDiv && macdDiv.bottomDivergence > 30) {
    risks.push({ label: 'MACD底背离', weight: 20 });
    riskScore += 20;
  }

  // 4. 远离均线
  if (distFromMa60 > 30) {
    risks.push({ label: '远离MA60', weight: 10 });
    riskScore += 10;
  }

  // 5. 量价背离
  if (vol && vol.relation) {
    if (bestType === 'bull' && vol.relation.includes('价涨量缩')) {
      risks.push({ label: '价涨量缩', weight: 10 });
      riskScore += 10;
    }
    if (bestType === 'bear' && vol.relation.includes('价跌量缩')) {
      risks.push({ label: '价跌量缩', weight: 15 });
      riskScore += 15;
    }
  }

  // 6. 形态匹配度低
  if (bestScore < 50) {
    risks.push({ label: '形态匹配度低', weight: 10 });
    riskScore += 10;
  }

  riskScore = Math.min(100, riskScore);
  let level = '低风险';
  if (riskScore >= 70) level = '高风险';
  else if (riskScore >= 40) level = '中风险';

  return { riskScore, level, risks: risks.map(r => r.label) };
}

// ============ 头肩底检测（含完整突破状态机） ============
function detectHSBottom(data, ind) {
  const n = data.length;
  const last = n - 1;
  if (n < 60) return { score: 0, stage: '数据不足', key_points: [] };

  const lookback = Math.min(80, n - 1);
  const seg = data.slice(n - lookback);
  const lows = seg.map(d => d.low);
  const highs = seg.map(d => d.high);
  const idxOffset = n - lookback;

  // 找头部（最低点）
  const headLocalIdx = lows.indexOf(Math.min(...lows));
  const head = lows[headLocalIdx];

  // 找左肩（头部左侧最高点后的最低点）
  let leftShoulder = null, leftShoulderLocalIdx = -1;
  if (headLocalIdx > 10) {
    const leftSeg = lows.slice(0, headLocalIdx - 5);
    if (leftSeg.length > 5) {
      leftShoulderLocalIdx = leftSeg.indexOf(Math.min(...leftSeg));
      leftShoulder = leftSeg[leftShoulderLocalIdx];
    }
  }

  // 找右肩（头部右侧最低点）
  let rightShoulder = null, rightShoulderLocalIdx = -1;
  if (headLocalIdx < lookback - 10) {
    const rightSeg = lows.slice(headLocalIdx + 5);
    if (rightSeg.length > 5) {
      rightShoulderLocalIdx = headLocalIdx + 5 + rightSeg.indexOf(Math.min(...rightSeg));
      rightShoulder = rightSeg[rightShoulderLocalIdx - headLocalIdx - 5];
    }
  }

  // 颈线：左右两个反弹高点连线（支持倾斜颈线）
  let neckLeftPrice = 0, neckRightPrice = 0, neckLeftGlobal = 0, neckRightGlobal = 0;
  if (leftShoulderLocalIdx >= 0 && rightShoulderLocalIdx >= 0) {
    // 左颈线点：左肩到头部之间的最高点
    const leftNeckSeg = highs.slice(leftShoulderLocalIdx, headLocalIdx);
    neckLeftPrice = leftNeckSeg.length > 0 ? Math.max(...leftNeckSeg) : head;
    neckLeftGlobal = idxOffset + leftShoulderLocalIdx + leftNeckSeg.indexOf(neckLeftPrice);
    // 右颈线点：头部到右肩之间的最高点
    const rightNeckSeg = highs.slice(headLocalIdx, rightShoulderLocalIdx);
    neckRightPrice = rightNeckSeg.length > 0 ? Math.max(...rightNeckSeg) : head;
    neckRightGlobal = idxOffset + headLocalIdx + rightNeckSeg.indexOf(neckRightPrice);
  }

  const curPrice = data[last].close;
  // 动态颈线值（基于全局索引的斜率）
  const neckSlope = neckRightGlobal !== neckLeftGlobal ? (neckRightPrice - neckLeftPrice) / (neckRightGlobal - neckLeftGlobal) : 0;
  const neckline = neckLeftPrice + neckSlope * (last - neckLeftGlobal);

  const leftHigher = leftShoulder !== null && leftShoulder > head;
  const rightHigher = rightShoulder !== null && rightShoulder > head;
  const symmetry = leftShoulder && rightShoulder && Math.abs(leftShoulder - rightShoulder) / leftShoulder < 0.2;
  const rightShoulderDone = rightShoulderLocalIdx >= 0 && rightShoulderLocalIdx < lookback - 3;

  // 突破事件检测
  let breakoutEvent = false, breakoutDayIdx = null;
  for (let i = Math.max(1, last - 10); i <= last; i++) {
    const prevNeck = neckLeftPrice + neckSlope * (i - 1 - neckLeftGlobal);
    const curNeck = neckLeftPrice + neckSlope * (i - neckLeftGlobal);
    if (data[i-1].close <= prevNeck && data[i].close > curNeck) {
      breakoutEvent = true;
      breakoutDayIdx = i;
      break;
    }
  }

  const broken = curPrice > neckline * 0.98;

  // 完整突破状态机
  let breakoutState = 'PRE_BREAK';
  if (broken && breakoutEvent) {
    const daysSince = last - breakoutDayIdx;
    if (daysSince === 0) breakoutState = 'BREAKOUT_DAY';
    else {
      const recentLow = Math.min(...data.slice(breakoutDayIdx, last+1).map(d => d.low));
      const isRetest = recentLow <= neckline * 1.02 && curPrice <= neckline * 1.08;
      let belowCount = 0;
      for (let i = breakoutDayIdx+1; i <= last; i++) if (data[i].close < neckline) belowCount++;
      const isFailed = belowCount >= 3 && curPrice < neckline;
      if (isFailed) breakoutState = 'FAILED_BREAK';
      else if (isRetest) breakoutState = 'RETEST';
      else breakoutState = 'POST_BREAK';
    }
  } else if (!broken && breakoutEvent) {
    let belowCount = 0;
    for (let i = breakoutDayIdx+1; i <= last; i++) if (data[i].close < neckline) belowCount++;
    breakoutState = belowCount >= 3 ? 'FAILED_BREAK' : 'RETEST';
  }

  let score = (leftHigher ? 20 : 5) + (rightHigher ? 20 : 5) + (symmetry ? 20 : 5) + (broken ? 40 : 10);
  if (breakoutState === 'FAILED_BREAK') score = Math.max(10, score - 30);

  let stage = '';
  if (!leftHigher) stage = '头肩底不成立：左肩未高于头部';
  else if (!rightShoulderDone) stage = '右肩构建中';
  else if (!rightHigher) stage = '右肩低于头部：头肩底形态可能失败';
  else if (breakoutState === 'PRE_BREAK') stage = '右肩已完成，向颈线推进：突破前夜（PRE_BREAK）';
  else if (breakoutState === 'BREAKOUT_DAY') stage = '头肩底突破颈线当日：形态确认（BREAKOUT_DAY）';
  else if (breakoutState === 'POST_BREAK') stage = `头肩底突破后运行中（第${last - breakoutDayIdx}日）：形态确认（POST_BREAK）`;
  else if (breakoutState === 'RETEST') stage = '头肩底突破后回踩颈线确认中（RETEST）';
  else if (breakoutState === 'FAILED_BREAK') stage = '头肩底突破失败：跌破颈线超过3日，形态失效（FAILED_BREAK）';

  return {
    score, stage, breakoutState,
    key_points: [
      { name: '左肩', price: leftShoulder ? Math.round(leftShoulder * 100) / 100 : null },
      { name: '头部', price: Math.round(head * 100) / 100 },
      { name: '右肩', price: rightShoulder && rightShoulderDone ? Math.round(rightShoulder * 100) / 100 : null },
      { name: '当前颈线', price: Math.round(neckline * 100) / 100 },
      { name: '当前价', price: Math.round(curPrice * 100) / 100 }
    ]
  };
}

// ============ 头肩顶检测（含完整破位状态机） ============
function detectHSTop(data, ind) {
  const n = data.length;
  const last = n - 1;
  if (n < 60) return { score: 0, stage: '数据不足', key_points: [] };

  const lookback = Math.min(80, n - 1);
  const seg = data.slice(n - lookback);
  const lows = seg.map(d => d.low);
  const highs = seg.map(d => d.high);
  const idxOffset = n - lookback;

  // 找头部（最高点）
  const headLocalIdx = highs.indexOf(Math.max(...highs));
  const head = highs[headLocalIdx];

  // 找左肩（头部左侧最低点后的最高点）
  let leftShoulder = null, leftShoulderLocalIdx = -1;
  if (headLocalIdx > 10) {
    const leftSeg = highs.slice(0, headLocalIdx - 5);
    if (leftSeg.length > 5) {
      leftShoulderLocalIdx = leftSeg.indexOf(Math.max(...leftSeg));
      leftShoulder = leftSeg[leftShoulderLocalIdx];
    }
  }

  // 找右肩（头部右侧最高点）
  let rightShoulder = null, rightShoulderLocalIdx = -1;
  if (headLocalIdx < lookback - 10) {
    const rightSeg = highs.slice(headLocalIdx + 5);
    if (rightSeg.length > 5) {
      rightShoulderLocalIdx = headLocalIdx + 5 + rightSeg.indexOf(Math.max(...rightSeg));
      rightShoulder = rightSeg[rightShoulderLocalIdx - headLocalIdx - 5];
    }
  }

  // 颈线：左右两个回调低点连线（支持倾斜颈线）
  let neckLeftPrice = 0, neckRightPrice = 0, neckLeftGlobal = 0, neckRightGlobal = 0;
  if (leftShoulderLocalIdx >= 0 && rightShoulderLocalIdx >= 0) {
    const leftNeckSeg = lows.slice(leftShoulderLocalIdx, headLocalIdx);
    neckLeftPrice = leftNeckSeg.length > 0 ? Math.min(...leftNeckSeg) : head;
    neckLeftGlobal = idxOffset + leftShoulderLocalIdx + leftNeckSeg.indexOf(neckLeftPrice);
    const rightNeckSeg = lows.slice(headLocalIdx, rightShoulderLocalIdx);
    neckRightPrice = rightNeckSeg.length > 0 ? Math.min(...rightNeckSeg) : head;
    neckRightGlobal = idxOffset + headLocalIdx + rightNeckSeg.indexOf(neckRightPrice);
  }

  const curPrice = data[last].close;
  const neckSlope = neckRightGlobal !== neckLeftGlobal ? (neckRightPrice - neckLeftPrice) / (neckRightGlobal - neckLeftGlobal) : 0;
  const neckline = neckLeftPrice + neckSlope * (last - neckLeftGlobal);

  const leftLower = leftShoulder !== null && leftShoulder < head;
  const rightLower = rightShoulder !== null && rightShoulder < head;
  const symmetry = leftShoulder && rightShoulder && Math.abs(leftShoulder - rightShoulder) / leftShoulder < 0.2;
  const rightShoulderDone = rightShoulderLocalIdx >= 0 && rightShoulderLocalIdx < lookback - 3;

  // 破位事件检测
  let breakdownEvent = false, breakdownDayIdx = null;
  for (let i = Math.max(1, last - 10); i <= last; i++) {
    const prevNeck = neckLeftPrice + neckSlope * (i - 1 - neckLeftGlobal);
    const curNeck = neckLeftPrice + neckSlope * (i - neckLeftGlobal);
    if (data[i-1].close >= prevNeck && data[i].close < curNeck) {
      breakdownEvent = true;
      breakdownDayIdx = i;
      break;
    }
  }

  const broken = curPrice < neckline * 1.02;

  // 完整破位状态机
  let breakdownState = 'PRE_BREAKDOWN';
  if (broken && breakdownEvent) {
    const daysSince = last - breakdownDayIdx;
    if (daysSince === 0) breakdownState = 'BREAKDOWN_DAY';
    else {
      const recentHigh = Math.max(...data.slice(breakdownDayIdx, last+1).map(d => d.high));
      const isRetest = recentHigh >= neckline * 0.98 && curPrice >= neckline * 0.92;
      let aboveCount = 0;
      for (let i = breakdownDayIdx+1; i <= last; i++) if (data[i].close > neckline) aboveCount++;
      const isFailed = aboveCount >= 3 && curPrice > neckline;
      if (isFailed) breakdownState = 'FAILED_BREAKDOWN';
      else if (isRetest) breakdownState = 'RETEST';
      else breakdownState = 'POST_BREAKDOWN';
    }
  } else if (!broken && breakdownEvent) {
    let aboveCount = 0;
    for (let i = breakdownDayIdx+1; i <= last; i++) if (data[i].close > neckline) aboveCount++;
    breakdownState = aboveCount >= 3 ? 'FAILED_BREAKDOWN' : 'RETEST';
  }

  let score = (leftLower ? 20 : 5) + (rightLower ? 20 : 5) + (symmetry ? 20 : 5) + (broken ? 40 : 10);
  if (breakdownState === 'FAILED_BREAKDOWN') score = Math.max(10, score - 30);

  let stage = '';
  if (!leftLower) stage = '头肩顶不成立：左肩未低于头部';
  else if (!rightShoulderDone) stage = '右肩构建中';
  else if (!rightLower) stage = '右肩高于头部：头肩顶形态可能失败';
  else if (breakdownState === 'PRE_BREAKDOWN') stage = '右肩已完成，向颈线下行：破位前夜（PRE_BREAKDOWN）';
  else if (breakdownState === 'BREAKDOWN_DAY') stage = '头肩顶跌破颈线当日：顶部确认（BREAKDOWN_DAY）';
  else if (breakdownState === 'POST_BREAKDOWN') stage = `头肩顶破位后运行中（第${last - breakdownDayIdx}日）：顶部确认（POST_BREAKDOWN）`;
  else if (breakdownState === 'RETEST') stage = '头肩顶破位后回抽颈线确认中（RETEST）';
  else if (breakdownState === 'FAILED_BREAKDOWN') stage = '头肩顶破位失败：涨回颈线上方超过3日，形态失效（FAILED_BREAKDOWN）';

  return {
    score, stage, breakdownState,
    key_points: [
      { name: '左肩', price: leftShoulder ? Math.round(leftShoulder * 100) / 100 : null },
      { name: '头部', price: Math.round(head * 100) / 100 },
      { name: '右肩', price: rightShoulder && rightShoulderDone ? Math.round(rightShoulder * 100) / 100 : null },
      { name: '当前颈线', price: Math.round(neckline * 100) / 100 },
      { name: '当前价', price: Math.round(curPrice * 100) / 100 }
    ]
  };
}

// ============ 统一分析入口 ============
function analyze(data, marketEnv) {
  const ind = calcIndicators(data);
  const vol = analyzeVolume(data, ind);
  const boll = detectBollinger(data, ind);
  const macdDiv = detectMACDDivergence(data, ind);
  const hsBottom = detectHSBottom(data, ind);
  const hsTop = detectHSTop(data, ind);

  // 假信号风险（取最佳形态）
  const bestBull = hsBottom.score > 0 ? { type: 'bull', score: hsBottom.score } : { type: 'bull', score: 0 };
  const bestBear = hsTop.score > 0 ? { type: 'bear', score: hsTop.score } : { type: 'bear', score: 0 };
  const fakeRiskBull = calcFakeSignalRisk(data, ind, 'bull', bestBull.score, marketEnv, macdDiv, vol);
  const fakeRiskBear = calcFakeSignalRisk(data, ind, 'bear', bestBear.score, marketEnv, macdDiv, vol);

  return {
    indicators: {
      ma5: ind.ma5[ind.ma5.length - 1],
      ma20: ind.ma20[ind.ma20.length - 1],
      ma60: ind.ma60[ind.ma60.length - 1],
      rsi: ind.rsi[ind.rsi.length - 1],
      macd: ind.macd[ind.macd.length - 1]
    },
    volume: vol,
    bollinger: boll,
    macd_divergence: macdDiv,
    hs_bottom: hsBottom,
    hs_top: hsTop,
    fake_signal_risk_bull: fakeRiskBull,
    fake_signal_risk_bear: fakeRiskBear
  };
}

// Node.js 导出
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    calcMA,
    calcIndicators,
    analyzeVolume,
    detectBollinger,
    detectMACDDivergence,
    calcFakeSignalRisk,
    detectHSBottom,
    detectHSTop,
    analyze
  };
}
