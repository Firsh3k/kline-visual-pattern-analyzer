/* V5 的 canvas K 线图（drawKline），改为直接画 V6 引擎返回的数据：
 * - K 线 / 成交量：analysis.bars（[日期, 开, 高, 低, 收, 量]）
 * - 均线：analysis.core.indicators.series.ma5/10/20/60（引擎计算，本文件不计算任何指标）
 * - 标注：所选形态的 key_points（关键点）、neckline_price（颈线 / 平台沿）、target_price（目标）、stop_loss（止损）
 * 颜色与布局沿用 V5：红涨绿跌、MA5 橙 / MA10 蓝 / MA20 紫 / MA60 青。 */
(function () {
  'use strict';
  const MA_COLORS = { ma5: '#f39c12', ma10: '#3498db', ma20: '#9b59b6', ma60: '#1abc9c' };
  const UP = '#e74c3c'; const DOWN = '#2ecc71';
  const H = 320;

  // 把引擎形态对象转换成 V5 drawKline 的标注格式（只做坐标映射，不改数值）
  function markersFor(x) {
    if (!x || x.category === 'trend_state') return null;
    const bull = x.direction === 'bull';
    const color = bull ? UP : DOWN;
    const points = x.key_points.map((k) => ({ idx: k.index, price: k.price, label: k.name, color }));
    const lines = [];
    if (x.neckline_price !== null && x.neckline_price !== undefined) {
      const nk = x.key_points.find((k) => /上沿|下沿/.test(k.name));
      lines.push({ price: x.neckline_price, label: nk ? nk.name : '颈线', color: '#f39c12' });
    }
    if (x.target_price !== null && x.target_price !== undefined) lines.push({ price: x.target_price, label: '目标', color: '#5dade2' });
    if (x.stop_loss !== null && x.stop_loss !== undefined) lines.push({ price: x.stop_loss, label: '止损', color: '#e67e22' });
    return { points, lines };
  }

  function drawKline(canvas, doc, startIdx, endIdx, showMA, markers) {
    if (!canvas) return;
    const dpr = window.devicePixelRatio || 1;
    const rect = canvas.getBoundingClientRect();
    if (!rect.width) return;   // 页面未显示时不画（切回时重画）
    canvas.width = Math.round(rect.width * dpr);
    canvas.height = H * dpr;
    const ctx = canvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const W = rect.width;
    const narrow = W < 480;
    const padL = narrow ? 40 : 50; const padR = narrow ? 58 : 70; const padT = 10; const padB = 25;
    const volH = 60;
    const priceH = H - padT - padB - volH - 10;
    const bars = doc.bars;
    const series = doc.core.indicators.series;
    const slice = bars.slice(startIdx, endIdx);
    if (slice.length === 0) return;
    let maxP = Math.max(...slice.map((b) => b[2])); let minP = Math.min(...slice.map((b) => b[3]));
    if (markers && markers.lines) {
      markers.lines.forEach((l) => { if (l.price > maxP) maxP = l.price; if (l.price < minP) minP = l.price; });
    }
    maxP *= 1.02; minP *= 0.98;
    const pRange = maxP - minP || 1;
    const maxV = Math.max(...slice.map((b) => b[5])) || 1;
    const cw = (W - padL - padR) / slice.length;
    const bw = Math.max(1, cw * 0.6);
    const yOf = (p) => padT + (maxP - p) / pRange * priceH;

    ctx.fillStyle = '#1a2332';
    ctx.fillRect(0, 0, W, H);

    // 网格
    ctx.strokeStyle = '#2a3441';
    ctx.lineWidth = 0.5;
    for (let i = 0; i <= 4; i++) {
      const y = padT + priceH * i / 4;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
      const price = maxP - pRange * i / 4;
      ctx.fillStyle = '#556677'; ctx.font = '10px sans-serif'; ctx.textAlign = 'right';
      ctx.fillText(price.toFixed(2), padL - 5, y + 3);
    }

    // K 线 + 成交量
    slice.forEach((b, i) => {
      const [, open, high, low, close, volume] = b;
      const x = padL + i * cw + cw / 2;
      const isUp = close >= open;
      ctx.strokeStyle = isUp ? UP : DOWN;
      ctx.fillStyle = isUp ? UP : DOWN;
      const yO = yOf(open); const yC = yOf(close);
      ctx.beginPath(); ctx.moveTo(x, yOf(high)); ctx.lineTo(x, yOf(low)); ctx.stroke();
      ctx.fillRect(x - bw / 2, Math.min(yO, yC), bw, Math.max(1, Math.abs(yC - yO)));
      const vh = volume / maxV * volH;
      ctx.globalAlpha = 0.6;
      ctx.fillRect(x - bw / 2, padT + priceH + 10 + volH - vh, bw, vh);
      ctx.globalAlpha = 1;
    });

    // 均线（引擎序列）
    if (showMA) {
      ['ma5', 'ma10', 'ma20', 'ma60'].forEach((key) => {
        const ma = series[key] || [];
        ctx.strokeStyle = MA_COLORS[key];
        ctx.lineWidth = 1;
        ctx.beginPath();
        let started = false;
        slice.forEach((b, i) => {
          const v = ma[startIdx + i];
          if (v === null || v === undefined) return;
          const x = padL + i * cw + cw / 2; const y = yOf(v);
          if (!started) { ctx.moveTo(x, y); started = true; } else ctx.lineTo(x, y);
        });
        ctx.stroke();
      });
    }

    // 形态标注
    if (markers) {
      (markers.lines || []).forEach((line) => {
        const y = yOf(line.price);
        if (y < padT || y > padT + priceH) return;
        ctx.strokeStyle = line.color;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([6, 4]);
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = line.color;
        ctx.font = `bold ${narrow ? 9 : 11}px sans-serif`;
        ctx.textAlign = 'left';
        ctx.fillText(`${line.label} ${line.price.toFixed(2)}`, W - padR + 3, y + 4);
      });
      (markers.points || []).forEach((pt) => {
        const li = pt.idx - startIdx;
        if (li < 0 || li >= slice.length) return;
        const x = padL + li * cw + cw / 2; const y = yOf(pt.price);
        if (y < padT - 10 || y > padT + priceH + 10) return;
        ctx.strokeStyle = pt.color; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 6, 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = pt.color;
        ctx.beginPath(); ctx.arc(x, y, 2.5, 0, Math.PI * 2); ctx.fill();
        ctx.font = 'bold 10px sans-serif';
        ctx.textAlign = 'center';
        const tw = ctx.measureText(pt.label).width + 8;
        const labelY = y - 12 < padT + 5 ? y + 18 : y - 10;
        ctx.fillStyle = '#0f1419';
        ctx.fillRect(x - tw / 2, labelY - 9, tw, 13);
        ctx.fillStyle = pt.color;
        ctx.fillText(pt.label, x, labelY);
      });
    }

    // 日期
    ctx.fillStyle = '#556677'; ctx.font = '10px sans-serif'; ctx.textAlign = 'center';
    const step = Math.max(1, Math.floor(slice.length / (narrow ? 4 : 6)));
    for (let i = 0; i < slice.length; i += step) {
      ctx.fillText(slice[i][0].slice(5), padL + i * cw + cw / 2, H - 8);
    }
  }

  window.KChart = { drawKline, markersFor, MA_COLORS };
})();
