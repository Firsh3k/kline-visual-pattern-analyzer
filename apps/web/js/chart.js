/* K 线 + 成交量 + 均线 + 形态关键点 / 颈线 / 目标 / 止损叠加。数据全部来自后端分析结果。 */
(function () {
  'use strict';
  const MA_KEYS = [['ma5', 'MA5', '--ma5'], ['ma10', 'MA10', '--ma10'], ['ma20', 'MA20', '--ma20'], ['ma60', 'MA60', '--ma60']];
  const tok = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();

  function create(el, legend, doc) {
    const bars = doc.bars;
    const byDate = new Map(bars.map((b) => [b[0], b]));
    const fmtBar = (b, i) => {
      const p = i > 0 ? bars[i - 1][4] : null; const ch = p ? ((b[4] - p) / p) * 100 : null;
      return `${b[0]}　开 ${b[1].toFixed(2)}　高 ${b[2].toFixed(2)}　低 ${b[3].toFixed(2)}　收 <span class="${F.cls(ch)}">${b[4].toFixed(2)}` +
        `${ch !== null ? '（' + F.sign(ch.toFixed(2)) + '%）' : ''}</span>　量 ${Math.round(b[5]).toLocaleString('zh-CN')}`;
    };
    legend.innerHTML = fmtBar(bars[bars.length - 1], bars.length - 1);
    if (!window.LightweightCharts) {
      el.innerHTML = '<div class="notice">图表库未能加载，形态与指标数值仍完整显示在下方。</div>';
      return { setSelection() {}, setOverlay() {}, destroy() {}, focus() {} };
    }
    const chart = LightweightCharts.createChart(el, {
      autoSize: true, localization: { locale: 'zh-CN', dateFormat: 'yyyy-MM-dd' }, crosshair: { mode: 0 },
      timeScale: { rightOffset: 4 }, rightPriceScale: { scaleMargins: { top: 0.06, bottom: 0.24 } },
      handleScroll: { vertTouchDrag: false }, handleScale: { axisPressedMouseMove: true, pinch: true },
    });
    const candles = chart.addCandlestickSeries({ priceFormat: { type: 'price', precision: 2, minMove: 0.01 }, priceLineVisible: false });
    const vol = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    candles.setData(bars.map((b) => ({ time: b[0], open: b[1], high: b[2], low: b[3], close: b[4] })));
    const neck = chart.addLineSeries({ lineWidth: 1, lineStyle: 2, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false });
    const maSeries = {};
    MA_KEYS.forEach(([k]) => { maSeries[k] = chart.addLineSeries({ lineWidth: 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false }); });
    chart.subscribeCrosshairMove((p) => {
      const b = p.time ? byDate.get(p.time) : bars[bars.length - 1];
      if (b) legend.innerHTML = fmtBar(b, bars.indexOf(b));
    });
    chart.timeScale().fitContent();
    let priceLines = [];
    const state = { sel: null, ma: false, piv: false };

    function overlays() {
      const x = state.sel; const isMA = x && x.category === 'trend_state';
      priceLines.forEach((l) => candles.removePriceLine(l)); priceLines = [];
      const showMA = state.ma || isMA;
      MA_KEYS.forEach(([k]) => maSeries[k].setData(showMA ? doc.core.indicators.series[k]
        .map((v, i) => (v === null ? null : { time: bars[i][0], value: v })).filter(Boolean) : []));
      const markers = []; const mk = tok('--mark');
      if (x && !isMA) {
        x.key_points.forEach((k) => {
          const b = byDate.get(k.date); const below = b && Math.abs(k.price - b[3]) < 1e-9;
          markers.push({ time: k.date, position: below ? 'belowBar' : 'aboveBar', color: mk, shape: below ? 'arrowUp' : 'arrowDown',
            text: `${k.name} ${k.price.toFixed(2)}` });
        });
        if (x.neckline_price !== null && x.neckline_price !== undefined) {
          const nk = x.key_points.find((k) => /颈线|上沿|下沿/.test(k.name)) || x.key_points[0];
          neck.setData([{ time: nk.date, value: x.neckline_price }, { time: bars[bars.length - 1][0], value: x.neckline_price }]);
        } else neck.setData([]);
        [['目标', x.target_price], ['止损', x.stop_loss]].forEach(([t, p]) => {
          if (p !== null && p !== undefined) priceLines.push(candles.createPriceLine({ price: p, color: mk, lineWidth: 1, lineStyle: 3, axisLabelVisible: true, title: t }));
        });
      } else neck.setData([]);
      if (state.piv) doc.core.pivots.forEach((p) => markers.push({ time: p.date, position: p.pivot_type === 'swing_high' ? 'aboveBar' : 'belowBar', color: tok('--muted'), shape: 'circle', size: 0.5 }));
      markers.sort((a, b) => (a.time < b.time ? -1 : a.time > b.time ? 1 : 0));
      candles.setMarkers(markers);
    }

    function theme() {
      const up = tok('--up'); const dn = tok('--down');
      chart.applyOptions({ layout: { background: { type: 'solid', color: tok('--panel') }, textColor: tok('--muted') },
        grid: { vertLines: { color: tok('--soft') }, horzLines: { color: tok('--soft') } },
        rightPriceScale: { borderColor: tok('--line') }, timeScale: { borderColor: tok('--line') } });
      candles.applyOptions({ upColor: up, downColor: dn, borderUpColor: up, borderDownColor: dn, wickUpColor: up, wickDownColor: dn });
      vol.setData(bars.map((b) => ({ time: b[0], value: b[5], color: (b[4] >= b[1] ? up : dn) + '66' })));
      neck.applyOptions({ color: tok('--mark') });
      MA_KEYS.forEach(([k, , t]) => maSeries[k].applyOptions({ color: tok(t) }));
      overlays();
    }
    theme();
    const mq = matchMedia('(prefers-color-scheme: dark)'); mq.addEventListener('change', theme);

    return {
      setSelection(x) { state.sel = x; overlays(); },
      setOverlay(k, v) { state[k] = v; overlays(); },
      focus(x) {
        if (x) { const ix = x.key_points.map((k) => k.index); chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, Math.min(...ix) - 25), to: bars.length + 3 }); } else chart.timeScale().fitContent();
      },
      destroy() { mq.removeEventListener('change', theme); chart.remove(); },
    };
  }
  window.KChart = { create, MA_KEYS };
})();
