/* 典型图形库：V5 卡片样式，内容来自引擎的 data/pattern_catalog.json（含 engine_rules），
 * 历史统计来自 /api/v1/calibration（与 V6 网页同一取法）。不显示未经校准的“可靠性 / 成功率”。 */
(function () {
  'use strict';
  const { esc } = F;
  const { $ } = U;
  let type = 'bull';
  let loaded = null;

  function stat(cal, pid) {
    if (!cal) return '历史统计：校准数据暂不可用';
    const g = U.calibGroup(cal, pid);
    if (!g) return '无校准数据';
    if (!g.applicable) return `历史统计：N/A（${esc(g.not_applicable_reason)}）`;
    const m = g.metrics_by_horizon['20'];
    if (!m) return '历史统计：N/A';
    return `历史统计（T+20，${m.sample_size} 个信号，${esc(m.confidence_label)}）：方向胜率 ${F.pct(m.win_rate)}` +
      `${m.profit_factor !== null && m.profit_factor !== undefined ? `，盈亏比 ${m.profit_factor.toFixed(2)}` : ''}`;
  }

  function card(p, cal) {
    const catName = (F.CATS.find((c) => c[0] === p.category) || [, p.category])[1];
    const k = p.knowledge;
    return `<article class="pattern-card" id="cat-${esc(p.id)}" data-pid="${esc(p.id)}" tabindex="0" aria-expanded="false">
      <h3>${esc(p.name)} <span class="dir-badge ${esc(p.direction)}">${p.direction === 'bull' ? '看涨' : '看跌'}</span> <span class="cat-badge">${esc(catName)}</span></h3>
      ${p.svg}
      <p>${esc(p.desc)}</p>
      <p class="stat-line" data-testid="calib-stat">${stat(cal, p.id)}</p>
      <div class="pattern-detail">
        <h4>引擎判定规则</h4>
        <ul data-testid="engine-rules">${(p.engine_rules || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
        <h4>形态特征</h4>
        <ul>${p.features.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
        <h4>量价配合</h4>
        <ul><li>${esc(p.volume)}</li></ul>
        <h4>操作参考</h4>
        <ul><li>${esc(p.action)}</li></ul>
        ${k ? `<h4>形态知识</h4><p><b>形态本质：</b>${esc(k.essence)}</p>
          <ul>${k.causes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
          ${k.keyPoints.map((kp) => `<div class="kp"><b>${esc(kp.label)}</b><br>${esc(kp.desc)}</div>`).join('')}
          <p><b>策略：</b>${esc(k.strategy)}</p>${k.traps ? `<p class="trap"><b>陷阱：</b>${esc(k.traps)}</p>` : ''}` : ''}
      </div>
    </article>`;
  }

  async function render() {
    const grid = $('#patternGrid');
    if (!loaded) {
      grid.innerHTML = U.loading('正在读取形态图鉴…');
      loaded = (async () => {
        const cat = await U.catalog();
        let cal = null; let calErr = null;
        try { cal = await U.calibration(); } catch (e) { calErr = e; }
        return { cat, cal, calErr };
      })();
      loaded.catch(() => { loaded = null; });
    }
    let d;
    try { d = await loaded; } catch (e) { grid.innerHTML = U.errorBox(e, '读取形态图鉴失败'); return; }
    $('#catalogNote').textContent = '说明文字沿用 V5 页面，其中与引擎矛盾的说法已按引擎口径更正；每个形态下列出引擎实际使用的判定规则（点击卡片展开）。' +
      '未经校准的“可靠性 / 成功率”百分比已移除，历史统计来自校准数据，证据不足显示 N/A。';
    const items = d.cat.items.filter((p) => p.direction === type);
    grid.innerHTML = items.map((p) => card(p, d.cal)).join('') + (d.calErr ? U.errorBox(d.calErr, '校准数据获取失败') : '');
  }

  function init() {
    document.querySelectorAll('.pattern-tab').forEach((b) => b.addEventListener('click', () => {
      type = b.dataset.ptype;
      document.querySelectorAll('.pattern-tab').forEach((x) => {
        x.classList.toggle('active-bull', x.dataset.ptype === type && type === 'bull');
        x.classList.toggle('active-bear', x.dataset.ptype === type && type === 'bear');
      });
      render();
    }));
    const grid = $('#patternGrid');
    const toggle = (c) => { if (!c) return; c.classList.toggle('expanded'); c.setAttribute('aria-expanded', String(c.classList.contains('expanded'))); };
    grid.addEventListener('click', (e) => toggle(e.target.closest('.pattern-card')));
    grid.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggle(e.target.closest('.pattern-card')); } });
  }

  window.Catalog = { init, render };
})();
