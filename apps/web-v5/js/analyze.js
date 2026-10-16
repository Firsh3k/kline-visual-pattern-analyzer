/* 股票分析：V5 版面，数据全部来自 V6 引擎。
 * - POST /api/v1/analyses {code, asOf?}：K 线、均线序列、8 种形态（多 / 空）的阶段 / 分数 / 证据、量价、ATR 止损、交易日历
 * - GET /api/v1/market[?asOf]：大盘环境（字段与 V5 analyzeMarketEnv 同名）
 * - GET /api/v1/calibration：历史统计
 * 回测模式 = asOf；“后续走势”取同一只股票不带 asOf 的引擎行情里截止日之后的 K 线，只做价格加减乘除。 */
(function () {
  'use strict';
  const { esc, fmt, sign, cls, p4 } = F;
  const { $ } = U;
  const st = { doc: null, list: [], sel: null, seq: 0 };

  // ---------------------------------------------------------------- 入口
  function analyze(code) {
    App.switchTab('analyze');
    $('#codeInput').value = code;
    return run();
  }

  async function run() {
    const code = $('#codeInput').value.trim();
    if (!/^\d{6}$/.test(code)) { U.toast('请输入6位股票代码'); return; }
    const bt = $('#backtestMode').checked;
    const asOf = bt ? $('#backtestDate').value : '';
    if (bt && !asOf) { U.toast('请选择回测日期'); return; }
    const seq = ++st.seq;
    const btn = $('#analyzeBtn');
    btn.disabled = true; btn.textContent = '分析中...';
    const area = $('#resultArea');
    area.innerHTML = U.loading('正在由 V6 引擎分析…');
    try {
      const body = { code };
      if (asOf) body.asOf = asOf;
      const reqs = [API.post('/api/v1/analyses', body, { timeoutMs: 40000 })];
      if (asOf) reqs.push(API.post('/api/v1/analyses', { code }, { timeoutMs: 40000 }).then((r) => r, (e) => ({ error: e })));
      let res;
      try { res = await Promise.all(reqs); } catch (e) {
        if (seq !== st.seq) return;
        area.innerHTML = U.errorBox(e, '分析失败（未显示任何推测结果）');
        const lg = e.details && e.details.last_good;
        if (lg) {
          area.insertAdjacentHTML('beforeend', `<div class="warn-box"><p>有一份上次成功的结果（保存于 ${esc(lg.stored_at)}，数据截至 ${esc(lg.result.as_of)}）。${esc(lg.note)}</p>
            <button class="btn btn-ghost" id="showLast" type="button">查看上次结果（明确标记为旧结果）</button></div>`);
          $('#showLast').addEventListener('click', () => render(lg.result, { requestId: e.requestId, lastGood: lg }));
        }
        return;
      }
      if (seq !== st.seq) return;
      const after = asOf ? res[1] : null;
      render(res[0].data.analysis, { requestId: res[0].data.requestId, asOf, after });
    } finally {
      if (seq === st.seq) { btn.disabled = false; btn.textContent = '开始分析'; }
    }
  }

  // ---------------------------------------------------------------- 渲染
  function patternList(doc) {
    const list = [];
    for (const [pid, rs] of Object.entries(doc.core.patterns)) rs.forEach((x, i) => list.push({ ...x, _id: `${pid}#${i}` }));
    const lastKey = (x) => Math.max(...x.key_points.map((k) => k.index));
    list.sort((a, b) => lastKey(b) - lastKey(a));
    return list;
  }

  function backtestPanel(doc, opt) {
    if (!opt.asOf) return '';
    const head = `<div class="bt-head"><span class="bt-title">📅 回测模式 · ${esc(opt.asOf)}</span>
      ${doc.as_of !== opt.asOf ? `<span class="hint">引擎实际截止交易日 ${esc(doc.as_of)}</span>` : ''}</div>`;
    if (!opt.after || opt.after.error) {
      return `<div class="backtest-panel" data-testid="backtest">${head}${U.errorBox(opt.after && opt.after.error, '后续走势获取失败')}</div>`;
    }
    const full = opt.after.data.analysis;
    const later = full.bars.filter((b) => b[0] > doc.as_of);
    if (!later.length) return `<div class="backtest-panel" data-testid="backtest">${head}<p class="hint">回测日之后暂无行情。</p></div>`;
    const btClose = doc.bars[doc.bars.length - 1][4];
    const latest = later[later.length - 1][4];
    const maxP = Math.max(...later.map((b) => b[2])); const minP = Math.min(...later.map((b) => b[3]));
    const ret = (latest - btClose) / btClose * 100; const maxPct = (maxP / btClose - 1) * 100; const minPct = (minP / btClose - 1) * 100;
    const cell = (label, v, c, field) => `<div><div class="bt-k">${label}</div><div class="bt-v ${c}" data-field="${field}">${v}</div></div>`;
    return `<div class="backtest-panel" data-testid="backtest">${head}
      <div class="bt-grid">
        ${cell('回测日收盘', btClose.toFixed(2), '', 'bt_close')}
        ${cell('最新收盘', latest.toFixed(2), '', 'bt_latest')}
        ${cell('至今涨跌', sign(ret.toFixed(2)) + '%', cls(ret), 'bt_return')}
        ${cell('期间最高', sign(maxPct.toFixed(2)) + '%', cls(maxPct), 'bt_max')}
        ${cell('期间最低', sign(minPct.toFixed(2)) + '%', cls(minPct), 'bt_min')}
      </div>
      <div class="hint">后续走势：${esc(later[0][0])} → ${esc(later[later.length - 1][0])}，共 ${later.length} 个交易日（取自引擎不带截止日的行情，${esc(full.as_of)}）。上方形态与大盘均为截至回测日的引擎结果。</div></div>`;
  }

  function matchCard(list, dir) {
    const bull = dir === 'bull';
    const rows = F.TYPES.filter((t) => t[2] === dir).map(([pid, name]) => {
      const rs = list.filter((x) => x.pattern_id === pid);   // list 已按关键点由近到远排序
      if (!rs.length) {
        return `<div class="match-item none" data-pid="${pid}" data-absent="1"><span class="label">${esc(name)}</span>
          <div class="bar-bg"></div><span class="pct muted">—</span></div><div class="match-sub muted">未识别</div>`;
      }
      const x = rs[0];
      const c = x.score >= 60 ? (bull ? 'c-up' : 'c-down') : x.score >= 40 ? 'c-warn' : 'c-muted';
      return `<div class="match-item pick" data-id="${x._id}" data-pid="${pid}" tabindex="0"><span class="label">${esc(x.pattern_name)}</span>
        <div class="bar-bg"><div class="bar ${c}" data-w="${x.score}"></div></div><span class="pct ${c}" data-field="score">${x.score}</span></div>
        <div class="match-sub">${esc(F.stageText(x))} · 等级 ${esc(x.confidence)}${rs.length > 1 ? ` · 共 ${rs.length} 个（显示最近一个）` : ''}</div>`;
    }).join('');
    return `<div class="analysis-card" data-testid="${dir}-match"><h3 class="${bull ? 'c-up' : 'c-down'}">${bull ? '上升形态匹配（找买点）' : '下降形态匹配（风险信号）'}</h3>${rows}
      <p class="hint">分数是引擎的算法信号强度（0–100），不是历史胜率。</p></div>`;
  }

  function patternTable(list) {
    const span = (x) => { const d = x.key_points.map((k) => k.date).sort(); return d[0] === d[d.length - 1] ? d[0] : `${d[0]} → ${d[d.length - 1]}`; };
    const row = (x) => `<tr class="prow" tabindex="0" data-id="${x._id}" data-pid="${esc(x.pattern_id)}"><td><span data-field="name">${esc(x.pattern_name)}</span> <span class="dir ${esc(x.direction)}">${F.dirText(x.direction)}</span></td>
      <td data-field="stage" data-v="${esc(x.stage)}">${esc(F.stageText(x))}</td><td class="n" data-field="score">${x.score}</td><td data-field="confidence">${esc(x.confidence)}</td><td class="num small">${esc(span(x))}</td></tr>`;
    const group = (cat, title, hint) => {
      const rows = F.TYPES.map(([pid, name, dir, basis, staticCat]) => {
        const rs = list.filter((x) => x.pattern_id === pid && x.category === cat);
        if (rs.length) return rs.map(row).join('');
        return (!list.some((x) => x.pattern_id === pid) && staticCat === cat)
          ? `<tr class="none" data-pid="${pid}" data-absent="1"><td>${name} <span class="dir ${dir}">${F.dirText(dir)}</span></td><td colspan="4">未识别（${basis}不成立）</td></tr>` : '';
      }).join('');
      return `<tr class="grp"><td colspan="5"><b>${title}</b>　<span class="muted">${hint}</span></td></tr>${rows}`;
    };
    return `<div class="tbl-wrap"><table class="tbl" data-testid="patterns"><thead><tr><th>形态</th><th>阶段</th><th class="n">分数</th><th>等级</th><th>关键点日期</th></tr></thead>
      <tbody id="pbody">${F.CATS.map(([k, t, h]) => group(k, t, h)).join('')}</tbody></table></div>`;
  }

  function render(doc, opt) {
    st.doc = doc; st.list = patternList(doc); st.sel = st.list[0] ? st.list[0]._id : null;
    const c = doc.core; const L = c.indicators.latest; const bars = doc.bars; const n = bars.length;
    const close = bars[n - 1][4]; const prev = n > 1 ? bars[n - 2][4] : close;
    const chg = close - prev; const chgPct = prev ? chg / prev * 100 : 0;
    const sess = F.SESSION[doc.session_state] || [doc.session_state, 'warn'];
    const cal = doc.calendar; const prov = doc.input.provenance || {};
    const inWatch = U.watch.set.has(doc.code);
    const v = c.indicators.volume; const atr = c.indicators.atr_stop;
    const area = $('#resultArea');

    area.innerHTML = `${opt.lastGood ? `<div class="error-msg" role="alert"><b>这是上次成功的旧结果，不是本次结果</b><div class="err-text">保存于 ${esc(opt.lastGood.stored_at)}；本次行情获取失败。</div></div>` : ''}
    <div class="stock-info" data-testid="quote">
      <span class="name" data-field="name">${esc(doc.name)}</span>
      <span class="code" data-field="code">${esc(doc.code)}</span>
      <span class="price ${cls(chg)}" data-field="close">${close.toFixed(2)}</span>
      <span class="change ${cls(chg)}" data-field="change">${sign(chg.toFixed(2))} (${sign(chgPct.toFixed(2))}%)</span>
      <span class="meta">${esc(doc.as_of)} | 共${doc.input.bar_count}个交易日 · ${esc(F.ADJ[doc.input.adjustment] || doc.input.adjustment)}</span>
      <button class="btn btn-ghost btn-sm" id="watchToggle" type="button" aria-pressed="${inWatch}">${inWatch ? '已在自选' : '加入自选'}</button>
    </div>
    <div class="chips" data-testid="session">
      <span class="chip ${sess[1]}" data-field="session_state" data-v="${esc(doc.session_state)}">${esc(sess[0])}</span>
      ${cal.stale ? `<span class="chip warn" data-field="stale">未更新到最新交易日（应为 ${esc(cal.expected_trade_date)}）</span>` : ''}
      ${cal.status !== 'verified' ? '<span class="chip warn">交易日历未核实</span>' : ''}
      ${opt.asOf ? `<span class="chip bt">回测截至 ${esc(doc.as_of)}</span>` : ''}
    </div>
    ${backtestPanel(doc, opt)}
    <div class="grid-2">
      <div class="env-card" id="marketCard" data-testid="market">${U.loading('正在读取大盘环境…')}</div>
      <div class="env-card" id="stageCard" data-testid="stage"></div>
    </div>
    <div class="chart-section">
      <h3>${n}日全景K线</h3>
      <div class="legend"><span><i class="ma5"></i>MA5</span><span><i class="ma10"></i>MA10</span><span><i class="ma20"></i>MA20</span><span><i class="ma60"></i>MA60</span>
        <span><i class="kup"></i>阳线(涨)</span><span><i class="kdown"></i>阴线(跌)</span><span><i class="neck"></i>颈线 / 平台沿</span><span><i class="tgt"></i>目标</span><span><i class="stp"></i>止损</span></div>
      <canvas id="chartFull" class="kcanvas" data-testid="chart-full"></canvas>
    </div>
    <div class="chart-section">
      <h3>近60日放大K线</h3>
      <canvas id="chart60" class="kcanvas" data-testid="chart-60"></canvas>
      <p class="hint" id="chartNote"></p>
    </div>
    <div class="analysis-grid three">
      ${matchCard(st.list, 'bull')}
      ${matchCard(st.list, 'bear')}
      <div class="analysis-card" data-testid="volume"><h3>量价分析</h3>
        <div class="volume-info">
          <div class="row"><span>今日量能状态</span><span class="${v.volStatus === '放量' ? 'up' : v.volStatus === '缩量' ? 'down' : ''}" data-field="volStatus">${esc(v.volStatus)}</span></div>
          <div class="row"><span>5日均量</span><span>${F.vol(L.vma5)}</span></div>
          <div class="row"><span>20日均量</span><span>${F.vol(L.vma20)}</span></div>
          <div class="row"><span>量比(5日/20日)</span><span data-field="volRatio">${esc(v.volRatio)}</span></div>
          <div class="row"><span>近期量价关系</span><span data-field="relation">${esc(v.relation)}</span></div>
        </div></div>
    </div>
    <div class="analysis-card mt"><h3>形态识别（引擎全部结果 · 点选在图上标注）</h3>${patternTable(st.list)}
      <p class="hint">core ${esc(c.core_version)} · 分数是算法信号强度，不是历史胜率。</p></div>
    <div class="analysis-card mt" id="detail" aria-live="polite"></div>
    <div class="analysis-card mt hidden" id="knowledge"></div>
    <div class="analysis-grid mt">
      <div class="analysis-card" data-testid="indicators"><h3>均线与 ATR 止损（${esc(doc.as_of)}）</h3>
        <div class="volume-info">
          <div class="row"><span><i class="dot ma5"></i>MA5</span><span data-field="ma5">${fmt(L.ma5)}</span></div>
          <div class="row"><span><i class="dot ma10"></i>MA10</span><span data-field="ma10">${fmt(L.ma10)}</span></div>
          <div class="row"><span><i class="dot ma20"></i>MA20</span><span data-field="ma20">${fmt(L.ma20)}</span></div>
          <div class="row"><span><i class="dot ma60"></i>MA60</span><span data-field="ma60">${fmt(L.ma60)}</span></div>
          <div class="row"><span>ATR(14)</span><span>${fmt(atr.atr)}（${fmt(atr.atr_pct)}%）</span></div>
          <div class="row"><span>2×ATR 止损位</span><span class="c-up strong" data-field="atr_stop">${fmt(atr.stop_loss)}</span></div>
        </div>
        <p class="hint">${esc(atr.detail)}</p></div>
      <div class="analysis-card" id="calibCard" data-testid="calibration"><h3>历史统计参考（T+20）</h3>${U.loading('加载中…')}</div>
    </div>
    <details class="analysis-card mt"><summary>数据与计算溯源</summary>
      <div class="volume-info small" data-testid="provenance">
        <div class="row"><span>行情来源</span><span>${esc(prov.source)}（${esc(prov.mode)}）${prov.note ? ' · ' + esc(prov.note) : ''}</span></div>
        <div class="row"><span>交易所 / 日历</span><span>${esc(cal.calendar_id)} · ${cal.status === 'verified' ? '权威交易日历' : '未核实'}${cal.expected_trade_date ? ' · 应有最新交易日 ' + esc(cal.expected_trade_date) : ''}</span></div>
        <div class="row"><span>分析区间</span><span>${esc(doc.input.first_date)} → ${esc(doc.input.last_date)}（${doc.input.bar_count} 根）</span></div>
        <div class="row"><span>输入 dataHash</span><code class="wrap-any" data-field="data_hash">${esc(doc.input.data_hash)}</code></div>
        <div class="row"><span>结果哈希</span><code class="wrap-any" data-field="result_sha256">${esc(c.result_sha256)}</code></div>
        <div class="row"><span>引擎</span><span>core ${esc(doc.engine.core_version)} · <code class="wrap-any">${esc(doc.engine.engine_commit)}</code></span></div>
        <div class="row"><span>requestId</span><code class="wrap-any">${esc(opt.requestId || '—')}</code></div>
      </div></details>`;
    F.applyWidths(area);

    sync();
    bind(doc);
    loadMarket(opt.asOf || null);
    loadCalibration();
  }

  function selected() { return st.list.find((x) => x._id === st.sel) || null; }

  function drawCharts() {
    const doc = st.doc; if (!doc || !$('#chartFull')) return;
    const x = selected(); const mk = KChart.markersFor(x); const n = doc.bars.length;
    KChart.drawKline($('#chartFull'), doc, 0, n, true, mk);
    KChart.drawKline($('#chart60'), doc, Math.max(0, n - 60), n, true, mk);
    const note = $('#chartNote');
    if (note) {
      note.textContent = x ? (x.category === 'trend_state' ? `当前标注：${x.pattern_name}（趋势状态，看均线排列）` :
        `当前标注：${x.pattern_name} · ${F.stageText(x)}（关键点、颈线、目标、止损均来自引擎）`) : '没有识别到形态，图上只显示均线。';
    }
  }

  function stageCard(x) {
    const el = $('#stageCard'); if (!el) return;
    if (!x) { el.innerHTML = '<div class="env-head"><span class="env-title">当前阶段</span></div><div class="muted">引擎没有识别到任何形态。</div>'; return; }
    const bull = x.direction === 'bull'; const parts = (x.notes || '').split('｜');
    el.classList.toggle('bear', !bull); el.classList.toggle('bull', bull);
    el.innerHTML = `<div class="env-head"><span class="env-title">当前阶段 · ${esc(x.pattern_name)}</span>
        <span class="pill ${bull ? 'up' : 'down'}" data-field="direction" data-v="${esc(x.direction)}">${bull ? '看涨形态' : '看跌形态'}</span></div>
      <div class="stage-text ${bull ? 'c-up' : 'c-down'}" data-field="stage" data-v="${esc(x.stage)}">${esc(F.stageText(x))}</div>
      <div class="stage-sub">分数 <b data-field="score">${x.score}</b> · 等级 ${esc(x.confidence)}</div>
      ${parts[0] ? `<div class="stage-sub">${esc(parts[0])}</div>` : ''}${parts[1] ? `<div class="hint">${esc(parts[1])}</div>` : ''}`;
  }

  function detail(x) {
    const d = $('#detail'); if (!d) return;
    if (!x) { d.innerHTML = '<h3>当前形态详细分析</h3><p class="muted">点选上方形态，查看关键点、判定证据并在图上标注。</p>'; return; }
    const bull = x.direction === 'bull';
    const catName = (F.CATS.find((k) => k[0] === x.category) || [, x.category])[1];
    const ev = Object.entries(x.evidence || {});
    d.innerHTML = `<h3 class="${bull ? 'c-up' : 'c-down'}">当前形态详细分析 — ${esc(x.pattern_name)} ${bull ? '（上升 / 看涨）' : '（下降 / 看跌）'} <span class="cat-badge">${esc(catName)}</span></h3>
      <div class="detail-grid">
        <div>
          <div class="k-label">关键点</div>
          <div class="tbl-wrap"><table class="tbl" data-testid="keypoints"><thead><tr><th>关键点</th><th>日期</th><th class="n">价格</th><th>说明</th></tr></thead><tbody>
          ${x.key_points.map((k) => `<tr><td>${esc(k.name)}</td><td class="num">${esc(k.date)}</td><td class="n" data-v="${k.price}">${p4(k.price)}</td><td class="muted small">${esc(k.description)}</td></tr>`).join('')}
          </tbody></table></div>
          <div class="level-list" data-testid="levels">
            <div class="row"><span>颈线 / 突破线</span><span data-field="neckline_price" data-v="${x.neckline_price ?? ''}">${p4(x.neckline_price)}</span></div>
            <div class="row"><span>突破价（突破日收盘）</span><span data-field="breakout_price" data-v="${x.breakout_price ?? ''}">${p4(x.breakout_price)}</span></div>
            <div class="row"><span>目标价</span><span data-field="target_price" data-v="${x.target_price ?? ''}">${p4(x.target_price)}</span></div>
            <div class="row"><span>止损价</span><span data-field="stop_loss" data-v="${x.stop_loss ?? ''}">${p4(x.stop_loss)}</span></div>
          </div>
        </div>
        <div>
          <div class="k-label">判定证据</div>
          ${ev.length ? `<div class="tbl-wrap"><table class="tbl" data-testid="evidence"><tbody>${ev.map(([k, v]) => `<tr><td>${esc(F.EV_LABEL[k] || k)}</td><td class="n" data-k="${esc(k)}">${F.evValue(k, v)}</td></tr>`).join('')}</tbody></table></div>`
    : '<p class="muted">此形态没有结构化证据。</p>'}
          ${x.evidence && x.evidence.back_inside_neckline ? `<div class="warn-box">突破后已连续 ${esc(x.evidence.days_back_inside)} 天收盘回到颈线内（满 ${esc(x.evidence.failed_break_days)} 天才判“失败”）。</div>` : ''}
          ${x.category === 'price_pattern' && x.evidence && x.evidence.volume_confirmed === false && ['breakout_day', 'post_break'].includes(x.stage) ? '<div class="warn-box">缩量突破：放量确认未成立，待确认。</div>' : ''}
        </div>
      </div>`;
  }

  async function knowledge(x) {
    const el = $('#knowledge'); if (!el) return;
    el.classList.add('hidden');
    if (!x) return;
    let cat;
    try { cat = await U.catalog(); } catch (e) { return; }   // 图鉴只是补充说明，读取失败不影响分析
    if (selected() !== x) return;
    const p = cat.items.find((i) => i.id === x.pattern_id); const k = p && p.knowledge;
    if (!k) return;
    const bull = x.direction === 'bull';
    el.innerHTML = `<h3 class="${bull ? 'c-up' : 'c-down'}">形态知识卡片 — ${esc(k.title || p.name)}</h3>
      <div class="detail-grid">
        <div><div class="k-label c-warn">形态本质</div><p>${esc(k.essence)}</p>
          <div class="k-label c-warn">形成原因</div><ul>${k.causes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
          <div class="k-label c-warn">操作策略</div><p class="${bull ? 'c-up' : 'c-down'} strong">${esc(k.strategy)}</p></div>
        <div><div class="k-label c-warn">核心讲究</div>${k.keyPoints.map((kp) => `<div class="kp"><b>${esc(kp.label)}</b><br>${esc(kp.desc)}</div>`).join('')}
          ${k.traps ? `<div class="trap-box"><b>⚠ 常见陷阱</b><br>${esc(k.traps)}</div>` : ''}</div>
      </div>
      <p class="hint">知识卡片取自引擎的形态图鉴（data/pattern_catalog.json）；引擎实际判定规则见“典型图形库”。</p>`;
    el.classList.remove('hidden');
  }

  function sync() {
    const x = selected();
    document.querySelectorAll('#resultArea [data-id]').forEach((e) => e.classList.toggle('sel', e.dataset.id === st.sel));
    stageCard(x); detail(x); knowledge(x); drawCharts();
  }

  function bind(doc) {
    $('#watchToggle').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget; const adding = btn.getAttribute('aria-pressed') !== 'true';
      btn.disabled = true;
      try {
        if (adding) await U.watchOps.add(doc.code); else await U.watchOps.remove(doc.code);
        U.watch.names.set(doc.code, doc.name);
        const now = U.watch.set.has(doc.code);
        btn.setAttribute('aria-pressed', String(now)); btn.textContent = now ? '已在自选' : '加入自选';
        U.toast(now ? '已加入自选股' : '已从自选股移除');
      } catch (e) { U.toast(e.message + (e.requestId ? `（requestId ${e.requestId}）` : '')); }
      btn.disabled = false;
    });
  }

  function scoreClass(s) { return s >= 60 ? 'c-up' : s >= 45 ? 'c-warn' : 'c-down'; }
  const chgSpan = (v) => `<b class="${cls(parseFloat(v))}">${parseFloat(v) >= 0 ? '+' : ''}${esc(v)}%</b>`;
  const trendCls = (t) => (String(t).includes('多头') ? 'c-up' : String(t).includes('空头') ? 'c-down' : 'c-warn');

  async function loadMarket(asOf) {
    const el = $('#marketCard'); if (!el) return;
    const doc = st.doc;
    let d;
    try { d = await U.market(asOf); } catch (e) {
      if ($('#marketCard') === el) el.innerHTML = `<div class="env-head"><span class="env-title">大盘环境（上证指数）</span></div>${U.errorBox(e, '大盘数据获取失败')}`;
      return;
    }
    if ($('#marketCard') !== el || st.doc !== doc) return;
    const m = d.market_env;
    if (!m) { el.innerHTML = '<div class="env-head"><span class="env-title">大盘环境（上证指数）</span><span class="muted">暂无</span></div><div class="muted">引擎没有返回大盘数据。</div>'; return; }
    const periods = [['5日', m.change5], ['20日', m.change20], ['60日', m.change60], ['120日', m.change120], ['250日', m.change250]];
    el.innerHTML = `<div class="env-head"><span class="env-title">大盘环境（上证指数）</span><span class="env-score ${scoreClass(m.score)}" data-field="market_score">${esc(m.score)}分</span></div>
      <div class="env-stage ${m.trend === 'bull' ? 'c-up' : m.trend === 'bear' ? 'c-down' : 'c-warn'}" data-field="market_stage">${esc(m.stage)}${asOf ? ` <span class="bt-badge">回测 ${esc(m.as_of)}</span>` : ''}</div>
      <div class="env-advice">${esc(m.advice)}</div>
      <div class="env-kv"><span>上证: <b>${esc(m.price)}</b></span><span>20日涨跌: ${chgSpan(m.change20)}</span><span>MA20: <b>${esc(m.ma20)}</b></span><span>MA60: <b>${esc(m.ma60)}</b></span></div>
      <div class="period-grid">${periods.map(([k, v]) => `<div><div class="pk">${k}</div><div class="pv ${cls(parseFloat(v))}">${parseFloat(v) >= 0 ? '+' : ''}${esc(v)}%</div></div>`).join('')}</div>
      <div class="wm-grid">
        <div><div class="pk">周线趋势</div><div class="wv ${trendCls(m.weeklyTrend)}">${esc(m.weeklyTrend)}</div><div class="pk2">近4周: ${parseFloat(m.weeklyChange4) >= 0 ? '+' : ''}${esc(m.weeklyChange4)}%</div></div>
        <div><div class="pk">月线趋势</div><div class="wv ${trendCls(m.monthlyTrend)}">${esc(m.monthlyTrend)}</div><div class="pk2">近3月: ${parseFloat(m.monthlyChange3) >= 0 ? '+' : ''}${esc(m.monthlyChange3)}%</div></div>
      </div>
      ${m.resonanceLevel ? `<div class="resonance ${m.resonanceLevel > 0 ? 'pos' : 'neg'}">🔗 多周期共振：${esc(m.resonance)}</div>` : ''}
      <div class="score-bar"><div class="${scoreClass(m.score)}" data-w="${Number(m.score) || 0}"></div></div>
      ${m.breakdown ? `<div class="bd-chips">${m.breakdown.map((b) => `<span class="${String(b.value).startsWith('+') ? 'c-up' : String(b.value).startsWith('-') ? 'c-down' : ''}">${esc(b.label)} ${esc(b.value)}</span>`).join('')}</div>` : ''}
      <div class="pk2">数据: ${esc(m.dataLength)}根K线, 最新: ${esc(m.lastDate)}${d.calendar && d.calendar.stale ? `（未更新到最新交易日 ${esc(d.calendar.expected_trade_date)}）` : ''}</div>`;
    F.applyWidths(el);
  }

  async function loadCalibration() {
    const el = $('#calibCard'); if (!el) return;
    const list = st.list;
    let cal;
    try { cal = await U.calibration(); } catch (e) { if ($('#calibCard') === el) el.innerHTML = `<h3>历史统计参考</h3>${U.errorBox(e, '校准数据获取失败')}`; return; }
    if ($('#calibCard') !== el) return;
    const pids = [...new Set(list.map((x) => x.pattern_id))];
    const rowFor = (pid) => {
      const g = U.calibGroup(cal, pid); const m = g && g.metrics_by_horizon['20'];
      return `<tr><td>${esc(F.typeName(pid))}</td><td class="n">${m ? m.sample_size : 'N/A'}</td><td class="n">${m ? F.pct(m.win_rate) : 'N/A'}</td><td class="n">${m && m.profit_factor !== null ? m.profit_factor.toFixed(2) : 'N/A'}</td><td class="small">${g && !g.applicable ? '口径不适用' : m ? esc(m.confidence_label) : '无数据'}</td></tr>`;
    };
    el.innerHTML = `<h3>历史统计参考（T+20）</h3><p class="hint">${esc(cal.disclaimer)}（${esc(cal.date_range)}，${esc(cal.stock_count)} 只股票）</p>
      ${pids.length ? `<div class="tbl-wrap"><table class="tbl"><thead><tr><th>形态</th><th class="n">样本</th><th class="n">胜率</th><th class="n">盈亏比</th><th>证据等级</th></tr></thead><tbody>${pids.map(rowFor).join('')}</tbody></table></div>` : '<p class="muted">本股当前没有识别到形态。</p>'}`;
  }

  // ---------------------------------------------------------------- 初始化
  function init() {
    $('#analyzeForm').addEventListener('submit', (e) => { e.preventDefault(); run(); });
    $('#quickCodes').addEventListener('click', (e) => { const b = e.target.closest('[data-code]'); if (b) analyze(b.dataset.code); });
    $('#backtestMode').addEventListener('change', () => {
      const on = $('#backtestMode').checked; const d = $('#backtestDate');
      d.classList.toggle('hidden', !on);
      if (on && !d.value) { const t = new Date(); t.setDate(t.getDate() - 30); d.value = t.toISOString().split('T')[0]; }
    });
    const area = $('#resultArea');
    const pick = (el) => { if (!el || !el.dataset.id) return; st.sel = el.dataset.id; sync(); };
    area.addEventListener('click', (e) => pick(e.target.closest('.prow, .match-item.pick')));
    area.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter' && e.key !== ' ') return;
      const el = e.target.closest('.prow, .match-item.pick'); if (!el) return;
      e.preventDefault(); pick(el);
    });
    let rt = null;
    window.addEventListener('resize', () => { clearTimeout(rt); rt = setTimeout(drawCharts, 120); });
  }

  window.Analyze = { init, analyze, run, redraw: drawCharts };
})();
