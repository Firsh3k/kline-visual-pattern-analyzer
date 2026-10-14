/* 页面视图。所有形态 / 指标 / 大盘 / 校准数值均原样来自 Python 分析服务；本文件只负责展示与交互。 */
(function () {
  'use strict';
  const { esc, fmt, sign, cls } = F;
  const $ = (sel, root = document) => root.querySelector(sel);
  const p4 = (v) => (v === null || v === undefined ? '—' : String(Number(Number(v).toFixed(4))));

  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.hidden = true; }, 3200);
  }

  function errorBox(e, title = '请求失败') {
    const rid = e && e.requestId ? `<div class="rid">requestId：${esc(e.requestId)}</div>` : '';
    const issues = e && e.details && e.details.issues ? `<details><summary>数据问题明细（${e.details.issues.length}）</summary><ul>${
      e.details.issues.map((i) => `<li class="small">第 ${esc(i.index)} 根 ${esc(i.date)} ${esc(i.field)}：${esc(i.reason)}</li>`).join('')}</ul></details>` : '';
    return `<div class="alert bad" role="alert"><h3>${esc(title)}</h3><p>${esc(e ? e.message : '未知错误')}</p>${issues}${rid}</div>`;
  }

  // ================================================================ 登录
  function renderLogin(main, onDone) {
    main.innerHTML = `<form class="panel login" id="loginForm" autocomplete="on">
      <h1>登录</h1><p class="muted small">私人内部系统，请使用部署时配置的账号。</p>
      <label class="field">用户名<input name="username" autocomplete="username" required></label>
      <label class="field">密码<input name="password" type="password" autocomplete="current-password" required></label>
      <button class="tbtn primary" type="submit">登录</button><div id="loginErr"></div></form>`;
    $('#loginForm').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      try { await API.login(fd.get('username'), fd.get('password')); onDone(); } catch (e) { $('#loginErr').innerHTML = errorBox(e, '登录失败'); }
    });
  }

  // ================================================================ 个股分析
  let chartRef = null;
  const stockCache = { market: null, calibration: null, watch: null };

  async function renderStock(main, params) {
    const code = params.code || '';
    const q = new URLSearchParams(params.query || '');
    main.innerHTML = `<section class="panel">
      <form class="row" id="stockForm" role="search">
        <label class="grow field"><span class="label">股票代码（6 位）</span>
          <input name="code" inputmode="numeric" pattern="\\d{6}" maxlength="6" value="${esc(code)}" placeholder="例如 603068" required></label>
        <label class="field"><span class="label">截至日期（可选）</span><input name="asOf" type="date" value="${esc(q.get('asOf') || '')}"></label>
        <label class="field"><span class="label">复权</span><select name="adjustment">
          <option value="qfq"${q.get('adjustment') !== 'none' ? ' selected' : ''}>前复权</option>
          <option value="none"${q.get('adjustment') === 'none' ? ' selected' : ''}>不复权</option></select></label>
        <button class="tbtn primary" type="submit">分析</button>
      </form>
      <p class="small muted">固定使用最近 250 根交易日 K 线；所有形态与指标由 Python 引擎计算，本页只展示。</p>
    </section><div id="stockOut">${code ? '<div class="notice"><span class="spinner"></span> 正在分析…</div>' : '<div class="notice">输入股票代码开始分析。</div>'}</div>`;
    $('#stockForm').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target); const c = String(fd.get('code')).trim();
      if (!/^\d{6}$/.test(c)) { toast('股票代码必须是 6 位数字'); return; }
      const qs = new URLSearchParams();
      if (fd.get('asOf')) qs.set('asOf', fd.get('asOf'));
      if (fd.get('adjustment') === 'none') qs.set('adjustment', 'none');
      location.hash = `#/stock/${c}${qs.toString() ? '?' + qs : ''}`;
    });
    if (!code) return;
    const out = $('#stockOut');
    const body = { code, adjustment: q.get('adjustment') === 'none' ? 'none' : 'qfq' };
    if (q.get('asOf')) body.asOf = q.get('asOf');
    let res;
    try {
      res = await API.post('/api/v1/analyses', body, { timeoutMs: 40000 });
    } catch (e) {
      out.innerHTML = errorBox(e, '分析失败（未显示任何推测结果）');
      const lg = e.details && e.details.last_good;
      if (lg) {
        out.insertAdjacentHTML('beforeend', `<div class="alert warn"><p>有一份上次成功的结果（保存于 <span class="num">${esc(lg.stored_at)}</span>，数据截至 ${esc(lg.result.as_of)}）。${esc(lg.note)}</p>
          <button class="tbtn" id="showLast" type="button">查看上次结果（明确标记为旧结果）</button></div>`);
        $('#showLast').addEventListener('click', () => { out.innerHTML = ''; drawAnalysis(out, lg.result, e.requestId, lg); });
      }
      return;
    }
    out.innerHTML = '';
    drawAnalysis(out, res.data.analysis, res.data.requestId, null);
  }

  function drawAnalysis(out, doc, requestId, lastGood) {
    const c = doc.core; const L = c.indicators.latest; const bars = doc.bars;
    const close = bars[bars.length - 1][4]; const prev = bars[bars.length - 2][4];
    const chg = close - prev; const chgPct = (chg / prev) * 100;
    const list = [];
    for (const [pid, rs] of Object.entries(c.patterns)) rs.forEach((x, i) => list.push({ ...x, _id: `${pid}#${i}` }));
    const lastKey = (x) => Math.max(...x.key_points.map((k) => k.index));
    list.sort((a, b) => lastKey(b) - lastKey(a));
    const span = (x) => { const d = x.key_points.map((k) => k.date).sort(); return d[0] === d[d.length - 1] ? d[0] : `${d[0]} → ${d[d.length - 1]}`; };
    const row = (x) => `<tr class="prow" tabindex="0" data-id="${x._id}" data-pid="${esc(x.pattern_id)}"><td>${esc(x.pattern_name)} <span class="dir ${esc(x.direction)}">${x.direction === 'bull' ? '涨' : '跌'}</span></td>
      <td data-field="stage" data-v="${esc(x.stage)}">${esc(F.stageText(x))}</td><td class="n" data-field="score">${x.score}</td><td data-field="confidence">${esc(x.confidence)}</td><td class="num small">${esc(span(x))}</td></tr>`;
    const group = (cat, title, hint) => {
      const rows = F.TYPES.map(([pid, name, dir, basis, staticCat]) => {
        const rs = list.filter((x) => x.pattern_id === pid && x.category === cat);
        if (rs.length) return rs.map(row).join('');
        return (!list.some((x) => x.pattern_id === pid) && staticCat === cat)
          ? `<tr class="none" data-pid="${pid}" data-absent="1"><td>${name} <span class="dir ${dir}">${dir === 'bull' ? '涨' : '跌'}</span></td><td colspan="4">未识别（${basis}不成立）</td></tr>` : '';
      }).join('');
      return `<tr class="grp"><td colspan="5"><b>${title}</b>　<span class="muted">${hint}</span></td></tr>${rows}`;
    };
    const sess = F.SESSION[doc.session_state] || [doc.session_state, 'warn'];
    const cal = doc.calendar; const prov = doc.input.provenance || {};
    const inWatch = stockCache.watch && stockCache.watch.has(doc.code);

    out.innerHTML = `${lastGood ? `<div class="alert warn" role="alert"><h3>这是上次成功的旧结果，不是本次结果</h3><p>保存于 ${esc(lastGood.stored_at)}；本次行情获取失败。</p></div>` : ''}
    <header class="panel quote" data-testid="quote">
      <div><h1>${esc(doc.name)}<span class="code">${esc(doc.code)}</span></h1>
        <div class="meta"><span>数据截至 <b class="num" data-field="as_of">${esc(doc.as_of)}</b></span>
          <span>${esc(F.ADJ[doc.input.adjustment] || doc.input.adjustment)} · <span class="num">${doc.input.bar_count}</span> 根交易日</span>
          <span class="chip ${sess[1]}" data-field="session_state" data-v="${esc(doc.session_state)}">${esc(sess[0])}</span>
          ${cal.stale ? `<span class="chip warn">未更新到最新交易日（应为 ${esc(cal.expected_trade_date)}）</span>` : ''}
          ${cal.status !== 'verified' ? `<span class="chip warn">交易日历未核实</span>` : ''}</div></div>
      <div><div class="px ${cls(chg)}">${close.toFixed(2)}</div><div class="chg ${cls(chg)}">${sign(chg.toFixed(2))}　${sign(chgPct.toFixed(2))}%</div></div>
      <div class="row"><button class="tbtn" id="watchToggle" type="button" aria-pressed="${inWatch ? 'true' : 'false'}">${inWatch ? '已在自选' : '加入自选'}</button></div>
    </header>
    <section class="panel" aria-label="K线图">
      <div class="head"><h2>日K线与形态标注</h2><span class="label">点选下方形态在图上标注关键点、颈线、目标与止损</span></div>
      <div class="toolbar" id="tb"><span class="label">叠加</span>
        <button class="tbtn" data-t="ma" aria-pressed="false" type="button"><span class="sw ma5"></span><span class="sw ma10"></span><span class="sw ma20"></span><span class="sw ma60"></span>均线</button>
        <button class="tbtn" data-t="piv" aria-pressed="false" type="button"><span class="sw piv"></span>摆动点（${c.pivots.length}）</button>
        <span class="label"><span class="sw mark"></span> 关键点与颈线</span></div>
      <div class="legend" id="legend"></div><div class="chart" id="chart"></div>
    </section>
    <div class="grid2">
      <section class="panel"><div class="head"><h2>形态识别</h2><span class="label num">core ${esc(c.core_version)}</span></div>
        <div class="tbl-wrap"><table data-testid="patterns"><thead><tr><th>形态</th><th>阶段</th><th class="n">分数</th><th>等级</th><th>关键点日期</th></tr></thead>
        <tbody id="pbody">${F.CATS.map(([k, t, h]) => group(k, t, h)).join('')}</tbody></table></div>
        <p class="small muted">分数是算法信号强度，不是历史胜率。</p></section>
      <section class="panel" id="detail" aria-live="polite"></section>
    </div>
    <div class="grid3">
      <section class="panel"><h2>技术指标（${esc(doc.as_of)}）</h2>
        <dl class="kv" data-testid="indicators">
          <dt>MA5 / 10 / 20 / 60</dt><dd>${['ma5', 'ma10', 'ma20', 'ma60'].map((k) => fmt(L[k])).join(' / ')}</dd>
          <dt>RSI14</dt><dd>${fmt(L.rsi, 2)} <span class="muted small">（Wilder 对照 ${fmt(c.indicators.reference.latest.rsi_wilder14, 2)}）</span></dd>
          <dt>KDJ K / D / J</dt><dd>${fmt(L.kdj_k, 1)} / ${fmt(L.kdj_d, 1)} / ${fmt(L.kdj_j, 1)}</dd>
          <dt>MACD DIF / DEA</dt><dd>${fmt(L.dif, 3)} / ${fmt(L.dea, 3)}</dd>
          <dt>布林 上 / 中 / 下</dt><dd>${fmt(L.boll_up)} / ${fmt(L.boll_mid)} / ${fmt(L.boll_low)}<br><span class="muted small">样本标准差对照 ${fmt(c.indicators.reference.latest.boll_up_sample20)} / ${fmt(c.indicators.reference.latest.boll_low_sample20)}</span></dd>
          <dt>ATR14</dt><dd>${fmt(c.indicators.atr_stop.atr)}（${fmt(c.indicators.atr_stop.atr_pct)}%）<span class="muted small"> Wilder 对照 ${fmt(c.indicators.reference.latest.atr_wilder14)}</span></dd>
          <dt>2×ATR 止损位</dt><dd>${fmt(c.indicators.atr_stop.stop_loss)}</dd>
          <dt>量价</dt><dd class="txt">${esc(c.indicators.volume.volStatus)} · ${esc(c.indicators.volume.relation)} · 量比 <span class="num">${esc(c.indicators.volume.volRatio)}</span></dd>
        </dl>
        <details><summary class="small">指标口径说明</summary><dl class="kv small">${Object.values(c.indicators.conventions).map((v) => `<dt>${esc(v.label)}</dt><dd class="txt">${esc(v.convention)}</dd>`).join('')}</dl></details>
      </section>
      <section class="panel" id="marketPanel"><h2>大盘环境（上证指数）</h2><p class="muted"><span class="spinner"></span> 加载中…</p></section>
      <section class="panel" id="calibPanel"><h2>历史统计参考</h2><p class="muted"><span class="spinner"></span> 加载中…</p></section>
    </div>
    <section class="panel"><h2>数据与计算溯源</h2>
      <dl class="kv small" data-testid="provenance">
        <dt>行情来源</dt><dd>${esc(prov.source)}（${esc(prov.mode)}）${prov.note ? ' · ' + esc(prov.note) : ''}</dd>
        <dt>交易所 / 日历</dt><dd>${esc(cal.calendar_id)} · ${cal.status === 'verified' ? '权威交易日历' : '未核实'}${cal.expected_trade_date ? ' · 应有最新交易日 ' + esc(cal.expected_trade_date) : ''}</dd>
        <dt>分析区间</dt><dd>${esc(doc.input.first_date)} → ${esc(doc.input.last_date)}（${doc.input.bar_count} 根）</dd>
        <dt>获取时间</dt><dd>${esc(prov.fetched_at || '—')}</dd>
        <dt>输入 dataHash</dt><dd><code data-field="data_hash">${esc(doc.input.data_hash)}</code></dd>
        <dt>结果哈希</dt><dd><code data-field="result_sha256">${esc(c.result_sha256)}</code></dd>
        <dt>引擎</dt><dd>core ${esc(doc.engine.core_version)} · ${esc(doc.engine.engine_commit)}</dd>
        <dt>requestId</dt><dd><code>${esc(requestId || '—')}</code></dd>
      </dl></section>
    <p class="foot">本页只展示 Python 引擎的计算结果，不做任何形态判断或评分；形态分析是技术面参考，不构成投资建议。</p>`;

    if (chartRef) { chartRef.destroy(); chartRef = null; }
    chartRef = KChart.create($('#chart'), $('#legend'), doc);
    const state = { sel: null };
    const detail = (x) => {
      const d = $('#detail');
      if (!x) { d.innerHTML = '<h2>形态详情</h2><p class="muted">点选左侧形态，查看关键点、判定证据并在图上标注。</p>'; return; }
      const parts = (x.notes || '').split('｜');
      const catName = (F.CATS.find((k) => k[0] === x.category) || [, x.category])[1];
      const ev = Object.entries(x.evidence || {});
      d.innerHTML = `<div class="head"><h2>${esc(x.pattern_name)}</h2><span><span class="chip">${esc(catName)}</span> <span class="dir ${esc(x.direction)}" data-field="direction" data-v="${esc(x.direction)}">${x.direction === 'bull' ? '看涨' : '看跌'}</span></span></div>
        <div class="stagebox"><b>${esc(F.stageText(x))}</b> · 分数 <span class="num">${x.score}</span> · 等级 ${esc(x.confidence)}<br>${esc(parts[0])}${parts[1] ? `<br><span class="muted small">${esc(parts[1])}</span>` : ''}</div>
        <div class="tbl-wrap"><table data-testid="keypoints"><thead><tr><th>关键点</th><th>日期</th><th class="n">价格</th><th>说明</th></tr></thead><tbody>
        ${x.key_points.map((k) => `<tr><td>${esc(k.name)}</td><td class="num">${esc(k.date)}</td><td class="n" data-v="${k.price}">${p4(k.price)}</td><td class="muted small">${esc(k.description)}</td></tr>`).join('')}
        </tbody></table></div>
        <dl class="kv" data-testid="levels">
          <dt>颈线 / 突破线</dt><dd data-field="neckline_price" data-v="${x.neckline_price ?? ''}">${p4(x.neckline_price)}</dd>
          <dt>突破价（突破日收盘）</dt><dd data-field="breakout_price" data-v="${x.breakout_price ?? ''}">${p4(x.breakout_price)}</dd>
          <dt>目标价</dt><dd data-field="target_price" data-v="${x.target_price ?? ''}">${p4(x.target_price)}</dd>
          <dt>止损价</dt><dd data-field="stop_loss" data-v="${x.stop_loss ?? ''}">${p4(x.stop_loss)}</dd></dl>
        <h3>判定证据</h3>
        ${ev.length ? `<div class="tbl-wrap"><table data-testid="evidence"><tbody>${ev.map(([k, v]) => `<tr><td>${esc(F.EV_LABEL[k] || k)}</td><td class="n" data-k="${esc(k)}">${F.evValue(k, v)}</td></tr>`).join('')}</tbody></table></div>`
    : '<p class="muted small">此形态没有结构化证据。</p>'}
        ${x.evidence && x.evidence.back_inside_neckline ? `<div class="alert warn small">突破后已连续 ${x.evidence.days_back_inside} 天收盘回到颈线内（满 ${x.evidence.failed_break_days} 天才判“失败”）。</div>` : ''}
        ${x.category === 'price_pattern' && x.evidence && x.evidence.volume_confirmed === false && ['breakout_day', 'post_break'].includes(x.stage) ? '<div class="alert warn small">缩量突破：放量确认未成立，待确认。</div>' : ''}`;
    };
    const sync = () => {
      const x = list.find((v) => v._id === state.sel) || null;
      chartRef.setSelection(x); detail(x);
      document.querySelectorAll('.prow').forEach((tr) => tr.classList.toggle('sel', tr.dataset.id === state.sel));
      return x;
    };
    $('#tb').addEventListener('click', (e) => {
      const b = e.target.closest('[data-t]'); if (!b) return;
      const v = b.getAttribute('aria-pressed') !== 'true'; b.setAttribute('aria-pressed', String(v)); chartRef.setOverlay(b.dataset.t, v);
    });
    const pick = (tr) => {
      if (!tr) return; state.sel = state.sel === tr.dataset.id ? null : tr.dataset.id;
      const x = sync(); chartRef.focus(x);
      $('#chart').scrollIntoView({ behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth', block: 'center' });
    };
    $('#pbody').addEventListener('click', (e) => pick(e.target.closest('.prow')));
    $('#pbody').addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(e.target.closest('.prow')); } });
    state.sel = list[0] ? list[0]._id : null; sync();

    $('#watchToggle').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget; const adding = btn.getAttribute('aria-pressed') !== 'true';
      try {
        const r = await API.patch('/api/v1/watchlist', { ops: [{ opId: API.opId(), op: adding ? 'add' : 'remove', code: doc.code, at: new Date().toISOString() }] });
        stockCache.watch = new Set(r.data.state.watchlist.map((w) => w.code));
        const now = stockCache.watch.has(doc.code);
        btn.setAttribute('aria-pressed', String(now)); btn.textContent = now ? '已在自选' : '加入自选';
        toast(now ? '已加入自选股' : '已从自选股移除');
      } catch (e) { toast(e.message); }
    });
    loadMarket(); loadCalibration(list);
  }

  async function loadMarket() {
    const el = $('#marketPanel'); if (!el) return;
    try {
      if (!stockCache.market) stockCache.market = (await API.get('/api/v1/market')).data;
      const m = stockCache.market.market_env;
      el.innerHTML = `<h2>大盘环境（上证指数）</h2>${m ? `<div class="stagebox"><b>${esc(m.stage)}</b>　<span class="num">${m.score}</span> 分<br>${esc(m.advice)}</div>
        <dl class="kv small"><dt>截至</dt><dd>${esc(m.as_of)}</dd><dt>评分构成</dt><dd class="txt">${(m.breakdown || []).map((b) => esc(b.label) + ' ' + esc(b.value)).join('，')}</dd>
        <dt>5 / 20 / 60 日</dt><dd>${esc(m.change5)}% / ${esc(m.change20)}% / ${esc(m.change60)}%</dd>
        <dt>周线 / 月线</dt><dd class="txt">${esc(m.weeklyTrend)} / ${esc(m.monthlyTrend)}（${esc(m.resonance)}）</dd></dl>` : '<p class="muted">没有大盘数据。</p>'}`;
    } catch (e) { el.innerHTML = `<h2>大盘环境</h2>${errorBox(e, '大盘数据获取失败')}`; }
  }

  async function loadCalibration(list) {
    const el = $('#calibPanel'); if (!el) return;
    try {
      if (!stockCache.calibration) stockCache.calibration = (await API.get('/api/v1/calibration')).data;
      const cal = stockCache.calibration;
      const pids = [...new Set(list.map((x) => x.pattern_id))];
      const rowFor = (pid) => {
        const g = cal.groups.find((x) => x.pattern_type === pid && x.market_regime === 'all' && x.confirmation_state === 'all');
        const m = g && g.metrics_by_horizon['20'];
        return `<tr><td>${esc(F.typeName(pid))}</td><td class="n">${m ? m.sample_size : 'N/A'}</td><td class="n">${m ? F.pct(m.win_rate) : 'N/A'}</td><td class="n">${m && m.profit_factor !== null ? m.profit_factor.toFixed(2) : 'N/A'}</td><td class="small">${g && !g.applicable ? '口径不适用' : m ? esc(m.confidence_label) : '无数据'}</td></tr>`;
      };
      el.innerHTML = `<h2>历史统计参考（T+20）</h2><p class="small muted">${esc(cal.disclaimer)}（${esc(cal.date_range)}，${cal.stock_count} 只股票）</p>
        ${pids.length ? `<div class="tbl-wrap"><table><thead><tr><th>形态</th><th class="n">样本</th><th class="n">胜率</th><th class="n">盈亏比</th><th>证据等级</th></tr></thead><tbody>${pids.map(rowFor).join('')}</tbody></table></div>` : '<p class="muted">本股当前没有识别到形态。</p>'}`;
    } catch (e) { el.innerHTML = `<h2>历史统计参考</h2>${errorBox(e, '校准数据获取失败')}`; }
  }

  // ================================================================ 扫描结果渲染（自选股批量 / 市场扫描共用）
  function patternChips(item, onlyMatched) {
    const ids = onlyMatched ? item.matchedPatterns : Object.keys(item.patterns_latest);
    if (!ids.length) return '<span class="muted small">无形态</span>';
    return ids.map((pid) => {
      const p = item.patterns_latest[pid];
      return `<span class="chip" data-pid="${esc(pid)}"><span class="dir ${esc(p.direction)}">${p.direction === 'bull' ? '涨' : '跌'}</span>${esc(p.pattern_name)} · ${esc(F.stageText(p))} · <span class="num">${p.score}</span></span>`;
    }).join('');
  }

  function resultCards(items, onlyMatched) {
    if (!items.length) return '<p class="muted">没有结果。</p>';
    return `<div class="cards" data-testid="results">${items.map((it) => `<div class="card" data-code="${esc(it.code)}">
      <a class="t" href="#/stock/${esc(it.code)}">${esc(it.name)} <span class="num muted">${esc(it.code)}</span></a>
      <span class="num ${cls(it.change)}">${fmt(it.close)} ${F.sign(it.change_pct.toFixed(2))}%</span>
      <div class="small muted">截至 ${esc(it.as_of)}${it.stale ? ' · <b>未更新到最新交易日</b>' : ''}${it.session_state === 'intraday' ? ' · 盘中' : ''}${it.sectors ? ' · ' + it.sectors.map(esc).join(' / ') : ''}</div>
      <div class="pats">${patternChips(it, onlyMatched)}</div></div>`).join('')}</div>`;
  }

  function failuresTable(f) {
    if (!f.length) return '';
    return `<details open><summary>失败清单（${f.length}）</summary><div class="tbl-wrap"><table data-testid="failures"><thead><tr><th>代码</th><th>类型</th><th>原因</th></tr></thead><tbody>${
      f.map((x) => `<tr><td class="num">${esc(x.code)}</td><td class="small">${esc(x.kind)}</td><td class="small">${esc(x.error)}</td></tr>`).join('')}</tbody></table></div></details>`;
  }

  async function runJob(container, body, { onlyMatchedDefault = true } = {}) {
    container.innerHTML = '<div class="notice"><span class="spinner"></span> 正在创建扫描任务…</div>';
    let job;
    try { job = (await API.post('/api/v1/scans', body)).data.job; } catch (e) {
      container.innerHTML = errorBox(e, '无法开始扫描');
      const v = e.details && e.details.verification;
      if (v) container.insertAdjacentHTML('beforeend', `<div class="alert warn"><p>该板块需先完成权威验证：<b>${esc(v.instruction)}</b></p><a href="#/sectors">前往板块页验证</a></div>`);
      return;
    }
    container.innerHTML = `<section class="panel" data-testid="job"><div class="head"><h2>扫描进度</h2><span class="label num">任务 ${esc(job.jobId.slice(0, 8))}</span></div>
      <div class="progress" aria-hidden="true"><i id="jobBar"></i></div>
      <p id="jobText" class="small" aria-live="polite"></p><div class="row"><button class="tbtn danger" id="jobCancel" type="button">取消</button></div>
      <div id="jobRes"></div></section>`;
    const bar = $('#jobBar'); const text = $('#jobText'); const cancel = $('#jobCancel');
    cancel.addEventListener('click', async () => { try { await API.post(`/api/v1/scans/${job.jobId}/cancel`, {}); } catch (e) { toast(e.message); } });
    const show = (j) => {
      bar.style.width = `${Math.round(j.progress * 100)}%`;
      text.textContent = `${j.status === 'running' ? '进行中' : j.status === 'completed' ? '已完成' : j.status === 'cancelled' ? '已取消' : j.status} · ${j.done}/${j.total} · 成功 ${j.succeeded} · 失败 ${j.failed} · 匹配 ${j.matched}` +
        `${j.invalidCodes && j.invalidCodes.length ? ` · 无效代码 ${j.invalidCodes.length}` : ''}${j.dedupedFrom > j.total ? ` · 去重前 ${j.dedupedFrom}` : ''}`;
    };
    show(job);
    for (;;) {
      await new Promise((r) => setTimeout(r, 900));
      if (!document.body.contains(text)) return;   // 已离开页面
      try { job = (await API.get(`/api/v1/scans/${job.jobId}`)).data.job; } catch (e) { text.textContent = `进度查询失败：${e.message}（稍后自动重试）`; continue; }
      show(job);
      if (!['queued', 'running'].includes(job.status)) break;
    }
    cancel.hidden = true;
    const res = (await API.get(`/api/v1/scans/${job.jobId}/results`)).data;
    const box = $('#jobRes');
    const draw = (only) => {
      const items = only ? res.items.filter((i) => i.matchedPatterns.length) : res.items;
      box.innerHTML = `<div class="row"><label class="checks"><label><input type="checkbox" id="onlyM"${only ? ' checked' : ''}> 只看符合过滤条件的（${res.matched}）</label></label></div>
        ${resultCards(items, only)}${failuresTable(res.failures)}`;
      $('#onlyM').addEventListener('change', (e) => draw(e.target.checked));
    };
    draw(onlyMatchedDefault);
  }

  // ================================================================ 自选股
  const PENDING_KEY = 'kl_pending_ops_v1';
  const pending = {
    load() { try { return JSON.parse(localStorage.getItem(PENDING_KEY) || '[]'); } catch (e) { return []; } },
    save(v) { try { localStorage.setItem(PENDING_KEY, JSON.stringify(v)); } catch (e) { /* 忽略 */ } },
  };

  async function sendOps(ops) {
    const all = pending.load().concat(ops);
    pending.save(all);
    const r = await API.patch('/api/v1/watchlist', { ops: all });
    pending.save([]);
    return r;
  }

  async function renderWatchlist(main) {
    main.innerHTML = `<section class="panel"><div class="head"><h2>自选股</h2><span class="label" id="wlVer"></span></div>
      <form class="search" id="wlAdd"><input class="inp" name="code" inputmode="numeric" maxlength="6" placeholder="输入 6 位代码添加" aria-label="股票代码"><button class="tbtn primary" type="submit">添加</button></form>
      <div class="row" id="wlActs"><button class="tbtn" id="wlScan" type="button">批量分析全部</button><button class="tbtn" id="wlExport" type="button">导出备份（V5 格式）</button>
        <button class="tbtn danger" id="wlClear" type="button">清空</button></div>
      <p class="small muted">保存在服务器，手机和电脑自动同步；删除记录会保留，旧设备离线时的添加不会让已删除的股票复活。</p>
      <div id="wlPending"></div><div id="wlList"><span class="spinner"></span></div></section><div id="wlJob"></div>`;
    const draw = (st) => {
      stockCache.watch = new Set(st.watchlist.map((w) => w.code));
      $('#wlVer').textContent = `版本 ${st.version}`;
      $('#wlList').innerHTML = st.watchlist.length ? `<div class="cards" data-testid="watchlist">${st.watchlist.map((w) => `<div class="card" data-code="${esc(w.code)}">
        <a class="t num" href="#/stock/${esc(w.code)}">${esc(w.code)}</a><div class="acts"><button class="tbtn danger" data-rm="${esc(w.code)}" type="button" aria-label="移除 ${esc(w.code)}">移除</button></div>
        <div class="small muted">加入于 ${esc(w.addedAt.slice(0, 16).replace('T', ' '))} UTC</div></div>`).join('')}</div>` : '<p class="muted">还没有自选股。</p>';
      const p = pending.load();
      $('#wlPending').innerHTML = p.length ? `<div class="alert warn">有 ${p.length} 个操作尚未同步到服务器。<button class="tbtn" id="wlRetry" type="button">重试同步</button></div>` : '';
      if (p.length) $('#wlRetry').addEventListener('click', () => mutate([]));
    };
    const mutate = async (ops) => {
      try { const r = await sendOps(ops); draw(r.data.state); const ign = r.data.results.filter((x) => x.status === 'ignored');
        if (ign.length) toast(ign.map((x) => x.reason === 'deleted_later' ? '该股票已在其他设备删除' : x.reason).join('；'));
      } catch (e) {
        if (e.status === 422) pending.save([]);   // 非法操作不保留
        toast(e.message); try { draw((await API.get('/api/v1/watchlist')).data); } catch (e2) { /* 保留 */ }
      }
    };
    try { draw((await API.get('/api/v1/watchlist')).data); } catch (e) { $('#wlList').innerHTML = errorBox(e, '读取自选股失败'); }
    $('#wlAdd').addEventListener('submit', (ev) => {
      ev.preventDefault(); const c = String(new FormData(ev.target).get('code')).trim();
      if (!/^\d{6}$/.test(c)) { toast('股票代码必须是 6 位数字'); return; }
      ev.target.reset(); mutate([{ opId: API.opId(), op: 'add', code: c, at: new Date().toISOString() }]);
    });
    $('#wlList').addEventListener('click', (ev) => {
      const b = ev.target.closest('[data-rm]'); if (!b) return;
      mutate([{ opId: API.opId(), op: 'remove', code: b.dataset.rm, at: new Date().toISOString() }]);
    });
    $('#wlClear').addEventListener('click', () => {
      if (!confirm('确定清空全部自选股？（删除记录会同步到其他设备）')) return;
      mutate([{ opId: API.opId(), op: 'clear', at: new Date().toISOString() }]);
    });
    $('#wlScan').addEventListener('click', () => runJob($('#wlJob'), { scope: { type: 'watchlist' } }, { onlyMatchedDefault: false }));
    $('#wlExport').addEventListener('click', async () => {
      try {
        const e = (await API.get('/api/v1/watchlist/export')).data.export;
        const url = URL.createObjectURL(new Blob([JSON.stringify(e, null, 2)], { type: 'application/json' }));
        const a = document.createElement('a'); a.href = url; a.download = `watchlist_${e.exportDate}.json`; a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      } catch (err) { toast(err.message); }
    });
  }

  // ================================================================ 市场扫描
  async function renderScan(main) {
    main.innerHTML = '<div class="notice"><span class="spinner"></span> 加载板块…</div>';
    let sectors;
    try { sectors = (await API.get('/api/v1/sectors')).data; } catch (e) { main.innerHTML = errorBox(e, '读取板块失败'); return; }
    const all = [...sectors.presets, ...sectors.custom, ...sectors.dynamic];
    const opt = (s) => `<label class="${s.scannable ? '' : 'muted'}"><input type="checkbox" name="sec" value="${esc(s.id)}"${s.scannable ? '' : ' disabled'}> ${esc(s.name || s.sectorCode)}${s.type === 'ths_dynamic' ? '（动态）' : ''} <span class="small muted">${s.scannable ? s.count + ' 只' : esc(s.status === 'pending' ? '待验证' : s.status === 'stale' ? '已过期' : s.status)}</span></label>`;
    main.innerHTML = `<form class="panel" id="scanForm"><div class="head"><h2>市场扫描</h2><a class="small" href="#/sectors">管理板块</a></div>
      <fieldset class="field"><legend class="label">扫描范围</legend><div class="checks">
        <label><input type="radio" name="scope" value="sectors" checked> 板块</label><label><input type="radio" name="scope" value="codes"> 自定义股票池</label><label><input type="radio" name="scope" value="watchlist"> 自选股</label></div></fieldset>
      <div id="scopeSectors" class="checks">${all.map(opt).join('')}</div>
      ${sectors.dynamic.some((s) => !s.scannable) ? '<p class="small muted">灰色的动态板块尚未通过权威验证或已过期，暂时不能扫描（可在板块页完成验证）。</p>' : ''}
      <label class="field hidden" id="scopeCodes">股票代码（空格、逗号或换行分隔）<textarea name="codes" placeholder="600519 000858 002436"></textarea></label>
      <div class="grid3"><label class="field">方向<select name="direction"><option value="any">全部</option><option value="bull">看涨</option><option value="bear">看跌</option></select></label>
        <label class="field">最低分数<input name="minScore" type="number" min="0" max="100" value="60"></label>
        <label class="field">关键点距今不超过（根）<input name="maxBarsSince" type="number" min="0" max="250" placeholder="不限"></label></div>
      <fieldset class="field"><legend class="label">形态</legend><div class="checks">${F.TYPES.map(([pid, name]) => `<label><input type="checkbox" name="pat" value="${pid}" checked> ${name}</label>`).join('')}</div></fieldset>
      <fieldset class="field"><legend class="label">阶段</legend><div class="checks">${Object.entries(F.STAGE_BULL).filter(([k]) => k !== 'complete').map(([k, v]) => `<label><input type="checkbox" name="stage" value="${k}"${k === 'forming' ? '' : ' checked'}> ${v}</label>`).join('')}</div></fieldset>
      <button class="tbtn primary" type="submit">开始扫描</button></form><div id="scanJob"></div>`;
    const form = $('#scanForm');
    form.addEventListener('change', () => {
      const s = form.scope.value;
      $('#scopeSectors').classList.toggle('hidden', s !== 'sectors'); $('#scopeCodes').classList.toggle('hidden', s !== 'codes');
    });
    form.addEventListener('submit', (ev) => {
      ev.preventDefault(); const fd = new FormData(form); const s = fd.get('scope');
      let scope;
      if (s === 'sectors') { const ids = fd.getAll('sec'); if (!ids.length) { toast('请至少选择一个板块'); return; } scope = { type: 'sectors', ids }; }
      else if (s === 'codes') { const codes = String(fd.get('codes')).split(/[\s,，;；]+/).filter(Boolean); if (!codes.length) { toast('请输入股票代码'); return; } scope = { type: 'codes', codes }; }
      else scope = { type: 'watchlist' };
      const filters = { direction: fd.get('direction'), minScore: Number(fd.get('minScore') || 0), patterns: fd.getAll('pat'), stages: fd.getAll('stage') };
      if (fd.get('maxBarsSince') !== '') filters.maxBarsSince = Number(fd.get('maxBarsSince'));
      if (!filters.patterns.length || !filters.stages.length) { toast('请至少选择一个形态和一个阶段'); return; }
      runJob($('#scanJob'), { scope, filters });
    });
  }

  // ================================================================ 板块
  async function renderSectors(main) {
    main.innerHTML = '<div class="notice"><span class="spinner"></span> 加载板块…</div>';
    let s;
    try { s = (await API.get('/api/v1/sectors')).data; } catch (e) { main.innerHTML = errorBox(e, '读取板块失败'); return; }
    const dynCard = (d) => {
      const chip = d.status === 'verified' ? 'ok' : d.status === 'pending' ? 'warn' : 'bad';
      const label = { verified: '已验证 · 可扫描', pending: '待验证 · 禁止扫描', stale: '已过期 · 禁止扫描', unavailable: '不可用 · 禁止扫描' }[d.status] || d.status;
      return `<div class="panel" data-dyn="${esc(d.sectorCode)}"><div class="head"><h3>${esc(d.name || '（名称待权威验证）')} <span class="num muted">${esc(d.sectorCode)}</span></h3><span class="chip ${chip}" data-field="status" data-v="${esc(d.status)}">${label}</span></div>
        ${d.provenance && d.provenance.authoritative_reconciliation_date ? `<p class="small">权威来源 ${esc(d.provenance.source)} · 对账日期 <span class="num">${esc(d.provenance.authoritative_reconciliation_date)}</span> · ${d.count} 只成分股</p>` : ''}
        ${d.reason ? `<p class="small muted">${esc(d.reason)}</p>` : ''}
        ${d.verification ? `<div class="alert warn small"><p>验证方法：把下面这句话发给已接入同花顺 iFinD 的助手，得到权威快照 JSON 后粘贴到下面提交。</p>
          <div class="row"><code class="grow" data-field="instruction">${esc(d.verification.instruction)}</code><button class="tbtn" data-copy="${esc(d.verification.instruction)}" type="button">复制</button></div></div>
          <label class="field">权威快照 JSON<textarea data-snap="${esc(d.sectorCode)}" placeholder='{"schema_version":"1.0","source":"ths_ifind","as_of_date":"YYYY-MM-DD","sectors":{"板块名":{"sector_code":"${esc(d.sectorCode)}","codes":["600000"]}}}'></textarea></label>
          <div class="row"><button class="tbtn primary" data-verify="${esc(d.sectorCode)}" type="button">提交验证</button></div>` : ''}
        <div class="row"><button class="tbtn danger" data-deldyn="${esc(d.sectorCode)}" type="button">删除</button></div><div data-out="${esc(d.sectorCode)}"></div></div>`;
    };
    main.innerHTML = `<section class="panel"><h2>同花顺动态板块</h2>
      <p class="small muted">输入 6 位板块指数代码，只做格式检查并进入“待验证”。必须提交权威快照（来源 + 日期 + 成分股）完成验证，并且在 ${s.freshDays} 天内才可扫描。</p>
      <form class="search" id="dynAdd"><input class="inp" name="code" inputmode="numeric" maxlength="6" placeholder="例如 886032" aria-label="板块代码"><button class="tbtn primary" type="submit">添加</button></form></section>
      <div class="grid2" id="dynList">${s.dynamic.map(dynCard).join('') || '<p class="muted">还没有动态板块。</p>'}</div>
      <section class="panel"><h2>自定义股票池</h2>
        <form id="cusForm" class="grid2"><label class="field">名称<input name="name" maxlength="20" required></label>
          <label class="field">股票代码（空格 / 逗号 / 换行分隔）<textarea name="codes" required></textarea></label>
          <div class="row"><button class="tbtn primary" type="submit">保存</button></div></form>
        <div class="cards">${s.custom.map((c) => `<div class="card"><span class="t">${esc(c.name)} <span class="muted small">${c.count} 只</span></span>
          <div class="acts"><button class="tbtn" data-editcus="${esc(c.name)}" type="button">编辑</button><button class="tbtn danger" data-delcus="${esc(c.name)}" type="button">删除</button></div>
          <div class="small muted num">${c.codes.map(esc).join(' ')}</div></div>`).join('') || '<p class="muted">还没有自定义股票池。</p>'}</div></section>
      <section class="panel"><h2>预置板块（${s.presets.length}）</h2><p class="small muted">与 V5 页面预置列表逐只一致。</p>
        ${s.presets.map((p) => `<details><summary>${esc(p.name)}${p.sectorCode ? ` <span class="num muted small">${esc(p.sectorCode)}</span>` : ''} · ${p.count} 只 <span class="chip ${p.membership.freshness === 'fresh' ? 'ok' : 'warn'}">${p.membership.freshness === 'fresh' ? '已对账' : '未经权威对账'}</span></summary>
          <p class="small muted">${esc(p.membership.note)}</p><p class="small num">${p.codes.map(esc).join(' ')}</p></details>`).join('')}</section>`;
    const reload = () => renderSectors(main);
    $('#dynAdd').addEventListener('submit', async (ev) => {
      ev.preventDefault(); const c = String(new FormData(ev.target).get('code')).trim();
      if (!/^\d{6}$/.test(c)) { toast('板块代码必须是 6 位数字'); return; }
      try { await API.post('/api/v1/sectors/dynamic', { sectorCode: c, at: new Date().toISOString() }); reload(); } catch (e) { toast(e.message); }
    });
    $('#cusForm').addEventListener('submit', async (ev) => {
      ev.preventDefault(); const fd = new FormData(ev.target);
      const codes = String(fd.get('codes')).split(/[\s,，;；]+/).filter(Boolean);
      try { await API.put(`/api/v1/sectors/custom/${encodeURIComponent(String(fd.get('name')).trim())}`, { codes, at: new Date().toISOString() }); reload(); }
      catch (e) { toast(e.details && e.details.invalid ? `${e.message}：${e.details.invalid.join(' ')}` : e.message); }
    });
    main.onclick = async (ev) => {
      const t = ev.target;
      if (t.dataset.copy) { try { await navigator.clipboard.writeText(t.dataset.copy); toast('已复制'); } catch (e) { toast('复制失败，请手动选择文字'); } }
      if (t.dataset.deldyn && confirm(`删除动态板块 ${t.dataset.deldyn}？`)) {
        try { await API.del(`/api/v1/sectors/dynamic/${t.dataset.deldyn}?at=${encodeURIComponent(new Date().toISOString())}`); reload(); } catch (e) { toast(e.message); }
      }
      if (t.dataset.delcus && confirm(`删除自定义股票池“${t.dataset.delcus}”？`)) {
        try { await API.del(`/api/v1/sectors/custom/${encodeURIComponent(t.dataset.delcus)}?at=${encodeURIComponent(new Date().toISOString())}`); reload(); } catch (e) { toast(e.message); }
      }
      if (t.dataset.editcus) {
        const c = s.custom.find((x) => x.name === t.dataset.editcus); const f = $('#cusForm');
        f.name.value = c.name; f.codes.value = c.codes.join(' '); f.scrollIntoView({ block: 'center' });
      }
      if (t.dataset.verify) {
        const code = t.dataset.verify; const out = main.querySelector(`[data-out="${code}"]`);
        let snap;
        try { snap = JSON.parse(main.querySelector(`[data-snap="${code}"]`).value); } catch (e) { out.innerHTML = '<div class="alert bad small">快照不是合法 JSON。</div>'; return; }
        try {
          const r = (await API.post(`/api/v1/sectors/${code}/verification`, { snapshot: snap })).data;
          toast(r.sector.status === 'verified' ? '验证通过，已可扫描' : `已记录，当前状态：${r.sector.status}`); reload();
        } catch (e) { out.innerHTML = errorBox(e, '验证未通过'); }
      }
    };
  }

  // ================================================================ 图鉴
  let catalogCache = null;
  async function renderCatalog(main) {
    main.innerHTML = '<div class="notice"><span class="spinner"></span></div>';
    try {
      if (!catalogCache) catalogCache = await (await fetch('data/pattern_catalog.json', { cache: 'no-cache' })).json();
      if (!stockCache.calibration) stockCache.calibration = (await API.get('/api/v1/calibration')).data;
    } catch (e) { main.innerHTML = errorBox(e, '读取图鉴失败'); return; }
    const cal = stockCache.calibration;
    const stat = (pid) => {
      const g = cal.groups.find((x) => x.pattern_type === pid && x.market_regime === 'all' && x.confirmation_state === 'all');
      if (!g) return '无校准数据';
      if (!g.applicable) return `历史统计：N/A（${esc(g.not_applicable_reason)}）`;
      const m = g.metrics_by_horizon['20'];
      return `历史统计（T+20，${m.sample_size} 个信号，${esc(m.confidence_label)}）：方向胜率 ${F.pct(m.win_rate)}`;
    };
    main.innerHTML = `<section class="panel"><h2>形态图鉴</h2><p class="small muted">说明文字沿用 V5 页面，其中与引擎矛盾的说法已按引擎口径更正；每个形态下列出引擎实际使用的判定规则。未经校准的“可靠性 / 成功率”百分比已移除，历史统计来自校准数据，证据不足显示 N/A。</p></section>
      <div class="catalog">${catalogCache.items.map((p) => `<article class="panel" id="cat-${esc(p.id)}"><div class="head"><h2>${esc(p.name)}</h2><span class="dir ${esc(p.direction)}">${p.direction === 'bull' ? '看涨' : '看跌'}</span></div>
        ${p.svg}<p>${esc(p.desc)}</p><p class="small muted">${stat(p.id)}</p>
        <h3>引擎判定规则</h3><ul class="small" data-testid="engine-rules">${(p.engine_rules || []).map((r) => `<li>${esc(r)}</li>`).join('')}</ul>
        <h3>形态特征</h3><ul>${p.features.map((f) => `<li>${esc(f)}</li>`).join('')}</ul>
        <h3>量价配合</h3><p>${esc(p.volume)}</p><h3>操作参考</h3><p>${esc(p.action)}</p>
        ${p.knowledge ? `<details><summary>形态知识卡片</summary><p><b>形态本质：</b>${esc(p.knowledge.essence)}</p><ul>${p.knowledge.causes.map((c) => `<li>${esc(c)}</li>`).join('')}</ul>
          ${p.knowledge.keyPoints.map((k) => `<div class="kp"><b>${esc(k.label)}</b><br><span class="small">${esc(k.desc)}</span></div>`).join('')}
          <p><b>策略：</b>${esc(p.knowledge.strategy)}</p>${p.knowledge.traps ? `<p class="small"><b>陷阱：</b>${esc(p.knowledge.traps)}</p>` : ''}</details>` : ''}
      </article>`).join('')}</div>`;
  }

  // ================================================================ 版本
  async function renderAbout(main) {
    main.innerHTML = '<div class="notice"><span class="spinner"></span></div>';
    let v;
    try { v = (await API.get('/api/v1/version')).data; } catch (e) { main.innerHTML = errorBox(e, '读取版本失败'); return; }
    const fe = v.frontend; const en = v.engine;
    main.innerHTML = `<div class="grid2">
      <section class="panel" data-testid="frontend-version"><h2>网页 + BFF</h2><dl class="kv">
        <dt>版本</dt><dd data-field="fe_version">${esc(fe.version)}</dd><dt>提交</dt><dd>${esc(fe.commit)}</dd>
        <dt>发布日期</dt><dd>${esc(fe.releasedAt)}</dd><dt>环境</dt><dd>${esc(fe.environment)}</dd>
        <dt>回滚基线</dt><dd>${esc(fe.rollbackBaseline)}</dd></dl>
        ${fe.verified ? `<p class="small"><a href="${esc(fe.releaseUrl)}" rel="noopener">查看发布记录</a></p>` : '<p class="small muted">发布元数据缺失或不合法，显示为 Version Unknown（FIR-28）。</p>'}</section>
      <section class="panel" data-testid="engine-version"><h2>Python 分析引擎</h2><dl class="kv">
        <dt>版本</dt><dd data-field="en_version">${en.verified ? esc(en.version) : 'Version Unknown'}</dd>
        <dt>提交</dt><dd>${en.verified ? esc(en.commit) : '—'}</dd><dt>构建时间</dt><dd>${esc(en.build_time || '—')}</dd>
        <dt>环境</dt><dd>${esc(en.environment || '—')}</dd><dt>core</dt><dd>${esc(en.core_version || '—')}</dd>
        <dt>接口 / 结果 schema</dt><dd>${esc(en.api_schema_version || '—')} / ${esc(en.analysis_schema_version || '—')}</dd>
        <dt>行情提供者</dt><dd>${esc(en.provider || '—')}</dd><dt>连通</dt><dd>${en.reachable ? '正常' : '无法连接'}</dd></dl></section></div>
      <section class="panel"><h2>兼容性</h2><p>${v.compatibility.ok ? '<span class="chip ok">网页与引擎接口版本一致</span>' : `<span class="chip bad">不兼容或不可用：${esc(v.compatibility.reason)}</span>（此时所有分析请求会被拒绝，不会降级到其他算法）`}</p></section>`;
  }

  window.Views = { renderLogin, renderStock, renderWatchlist, renderScan, renderSectors, renderCatalog, renderAbout, toast, errorBox,
    preloadWatch: async () => { try { stockCache.watch = new Set((await API.get('/api/v1/watchlist')).data.watchlist.map((w) => w.code)); } catch (e) { /* 忽略 */ } } };
})();
