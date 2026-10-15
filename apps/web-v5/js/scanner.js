/* 市场扫描：引擎板块（预置 / 自定义 / 同花顺动态）+ V6 过滤条件（方向 / 最低分 / 形态 / 阶段 / 关键点距今）。
 * 动态板块：POST /sectors/dynamic 自动拉取成分股；POST /sectors/{code}/verification 刷新或提交权威快照；状态与说明照 V6 显示。 */
(function () {
  'use strict';
  const { esc } = F;
  const { $ } = U;
  let sectors = null;
  const now = () => new Date().toISOString();

  function chip(s) {
    const dyn = s.type === 'ths_dynamic';
    const name = s.name || s.sectorCode;
    let extra = '';
    if (dyn) {
      const [label, tone] = F.DYN_STATUS[s.status] || [s.status, 'warn'];
      extra = `<span class="chip ${tone}" data-field="status" data-v="${esc(s.status)}">${esc(label)}</span>
        <button class="mini" type="button" data-refresh="${esc(s.sectorCode)}" title="刷新成分股">↻</button>
        <button class="mini mini-x" type="button" data-deldyn="${esc(s.sectorCode)}" title="移除板块">✕</button>`;
    } else if (s.type === 'custom') {
      extra = `<span class="sub">自定义</span><button class="mini mini-x" type="button" data-delcus="${esc(s.name)}" title="删除自定义板块">✕</button>`;
    } else if (s.membership && s.membership.freshness !== 'fresh') {
      extra = '<span class="sub" title="成分股来自 V5 页面预置列表，尚未经权威对账">未对账</span>';
    }
    return `<label class="sector-chip${dyn ? ' dyn' : ''}${s.scannable ? '' : ' off'}" data-sid="${esc(s.id)}">
      <input type="checkbox" name="sec" value="${esc(s.id)}"${s.scannable ? '' : ' disabled'}>
      <span class="nm">${esc(name)}</span>${dyn && s.name ? ` <span class="sub">${esc(s.sectorCode)}</span>` : ''}
      <span class="sub">(${s.count}只)</span>${extra}</label>`;
  }

  function dynCard(d) {
    const [label, tone] = F.DYN_STATUS[d.status] || [d.status, 'warn'];
    return `<div class="dyn-card" data-dyn="${esc(d.sectorCode)}">
      <div class="dyn-head"><b>${esc(d.name || '（名称待权威验证）')}</b> <span class="sub">${esc(d.sectorCode)}</span>
        <span class="chip ${tone}" data-field="status" data-v="${esc(d.status)}">${esc(label)}</span></div>
      ${d.provenance && d.provenance.authoritative_reconciliation_date ? `<div class="hint">权威来源 ${esc(d.provenance.source)} · 对账日期 ${esc(d.provenance.authoritative_reconciliation_date)} · ${d.count} 只成分股</div>` : ''}
      ${d.reason ? `<div class="hint">${esc(d.reason)}</div>` : ''}
      ${d.verification ? `<div class="warn-box"><p>自动拉取没有成功时，可以点“刷新成分股”重试；也可以手工验证：把下面这句话发给已接入同花顺 iFinD 的助手，得到权威快照 JSON 后粘贴到下面提交。</p>
        <div class="row-wrap"><code class="grow" data-field="instruction">${esc(d.verification.instruction)}</code><button class="btn btn-ghost btn-sm" data-copy="${esc(d.verification.instruction)}" type="button">复制</button></div></div>
        <textarea class="inp snap" data-snap="${esc(d.sectorCode)}" aria-label="权威快照 JSON" placeholder='{"schema_version":"1.0","source":"ths_ifind","as_of_date":"YYYY-MM-DD","sectors":{"板块名":{"sector_code":"${esc(d.sectorCode)}","codes":["600000"]}}}'></textarea>
        <div class="row-wrap"><button class="btn btn-primary btn-sm" data-verify="${esc(d.sectorCode)}" type="button">提交验证</button></div>` : ''}
      <div class="row-wrap"><button class="btn btn-ths btn-sm" data-refresh="${esc(d.sectorCode)}" type="button">刷新成分股</button>
        <button class="btn btn-ghost btn-sm" data-deldyn="${esc(d.sectorCode)}" type="button">删除</button></div>
      <div data-out="${esc(d.sectorCode)}"></div></div>`;
  }

  async function load(force) {
    if (sectors && !force) return sectors;
    sectors = (await API.get('/api/v1/sectors')).data;
    return sectors;
  }

  async function render(force) {
    const list = $('#sectorList');
    if (!sectors || force) list.innerHTML = U.loading('正在读取板块…');
    const checked = new Set([...document.querySelectorAll('#sectorList input[name=sec]:checked')].map((c) => c.value));
    let s;
    try { s = await load(force); } catch (e) { list.innerHTML = U.errorBox(e, '读取板块失败'); return; }
    const all = [...s.presets, ...s.custom, ...s.dynamic];
    list.innerHTML = all.map(chip).join('') +
      (s.dynamic.some((d) => !d.scannable) ? `<div class="hint full">灰色的动态板块尚未通过权威验证或已过期（${s.freshDays} 天内对账才可扫描），暂时不能扫描，可在下方完成验证。</div>` : '');
    list.querySelectorAll('input[name=sec]').forEach((c) => { if (checked.has(c.value) && !c.disabled) c.checked = true; });
    $('#dynList').innerHTML = s.dynamic.length ? `<h4 class="dyn-title">同花顺动态板块</h4><p class="hint">输入 6 位板块指数代码，服务端会自动从同花顺拉取最新成分股并记录来源和日期；扫描时当天没拉过会自动重拉。拉取失败时进入“待验证”，可重试或提交手工快照。</p>
      <div class="dyn-grid" data-testid="dynamic-sectors">${s.dynamic.map(dynCard).join('')}</div>` : '';
  }

  function filtersInit() {
    $('#scanPatterns').innerHTML = F.TYPES.map(([pid, name, dir]) => `<label><input type="checkbox" name="pat" value="${pid}" checked> ${name}<span class="dir ${dir}">${F.dirText(dir)}</span></label>`).join('');
    $('#scanStages').innerHTML = Object.entries(F.STAGE_BULL).filter(([k]) => k !== 'complete')
      .map(([k, v]) => `<label><input type="checkbox" name="stage" value="${k}"${k === 'forming' ? '' : ' checked'}> ${v}${F.STAGE_BEAR[k] !== v ? ` / ${F.STAGE_BEAR[k]}` : ''}</label>`).join('');
  }

  async function addDynamic(code) {
    const msg = $('#thsMsg');
    msg.innerHTML = U.loading(`正在从同花顺拉取板块 ${code} 的成分股…`);
    let r;
    try { r = (await API.post('/api/v1/sectors/dynamic', { sectorCode: code, at: now() }, { timeoutMs: 40000 })).data; } catch (e) {
      msg.innerHTML = U.errorBox(e, '添加板块失败'); return;
    }
    const sec = r.sector;
    if (sec.scannable) {
      msg.innerHTML = `<div class="ok-box" data-testid="ths-msg">${esc(sec.name || code)}：${sec.count} 只成分股，已加入扫描范围。</div>`;
    } else {
      const why = r.autoFetch && !r.autoFetch.ok ? `自动拉取失败：${r.autoFetch.error}` : (sec.reason || '成分股不可用');
      msg.innerHTML = `<div class="warn-box" data-testid="ths-msg">板块 ${esc(code)} 暂时不能扫描：${esc(why)}。可以稍后点“刷新成分股”重试，或在下方提交手工快照。</div>`;
    }
    await render(true);
    const cb = document.querySelector(`#sectorList input[value="${CSS.escape(sec.id)}"]`);
    if (cb && !cb.disabled) cb.checked = true;
  }

  async function onClick(ev) {
    const t = ev.target.closest('button'); if (!t) return;
    const d = t.dataset;
    if (d.copy) {
      ev.preventDefault();
      try { await navigator.clipboard.writeText(d.copy); U.toast('已复制'); } catch (e) { U.toast('复制失败，请手动选择文字'); }
    } else if (d.deldyn) {
      ev.preventDefault();
      if (!confirm(`删除动态板块 ${d.deldyn}？`)) return;
      try { await API.del(`/api/v1/sectors/dynamic/${d.deldyn}?at=${encodeURIComponent(now())}`); render(true); } catch (e) { U.toast(e.message); }
    } else if (d.delcus) {
      ev.preventDefault();
      if (!confirm(`删除自定义板块“${d.delcus}”？`)) return;
      try { await API.del(`/api/v1/sectors/custom/${encodeURIComponent(d.delcus)}?at=${encodeURIComponent(now())}`); render(true); } catch (e) { U.toast(e.message); }
    } else if (d.refresh) {
      ev.preventDefault();
      const code = d.refresh;
      const out = document.querySelector(`[data-out="${code}"]`) || $('#thsMsg');
      out.innerHTML = U.loading('正在从同花顺拉取…');
      try {
        const r = (await API.post(`/api/v1/sectors/${code}/verification`, {}, { timeoutMs: 40000 })).data;
        if (r.autoFetch && r.autoFetch.ok) { U.toast(`已更新：${r.autoFetch.count} 只成分股`); render(true); }
        else {
          const o = document.querySelector(`[data-out="${code}"]`) || $('#thsMsg');
          o.innerHTML = U.errorBox({ message: (r.autoFetch && r.autoFetch.error) || '服务端未开启自动拉取，请提交手工快照' }, '刷新失败');
        }
      } catch (e) { out.innerHTML = U.errorBox(e, '刷新失败'); }
    } else if (d.verify) {
      ev.preventDefault();
      const code = d.verify; const out = document.querySelector(`[data-out="${code}"]`);
      let snap;
      try { snap = JSON.parse(document.querySelector(`[data-snap="${code}"]`).value); } catch (e) { out.innerHTML = U.errorBox({ message: '快照不是合法 JSON。' }, '验证未提交'); return; }
      try {
        const r = (await API.post(`/api/v1/sectors/${code}/verification`, { snapshot: snap })).data;
        U.toast(r.sector.status === 'verified' ? '验证通过，已可扫描' : `已记录，当前状态：${r.sector.status}`);
        render(true);
      } catch (e) { out.innerHTML = U.errorBox(e, '验证未通过'); }
    }
  }

  function init() {
    filtersInit();
    $('#page-scanner').addEventListener('click', onClick);
    $('#thsForm').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const code = $('#thsSectorCode').value.trim();
      if (!/^\d{6}$/.test(code)) { U.toast('板块代码必须是 6 位数字'); return; }
      $('#thsSectorCode').value = '';
      addDynamic(code);
    });
    $('#customSectorForm').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target); const name = String(fd.get('name') || '').trim();
      const codes = U.splitCodes(fd.get('codes'));
      if (!name || !codes.length) { U.toast('请填写板块名称和股票代码'); return; }
      try {
        await API.put(`/api/v1/sectors/custom/${encodeURIComponent(name)}`, { codes, at: now() });
        ev.target.reset(); U.toast(`已保存自定义板块“${name}”`); render(true);
      } catch (e) { U.toast(e.details && e.details.invalid ? `${e.message}：${e.details.invalid.join(' ')}` : e.message); }
    });
    $('#scanForm').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const ids = fd.getAll('sec');
      if (!ids.length) { U.toast('请至少选择一个板块'); return; }
      const filters = { direction: fd.get('direction'), minScore: Number(fd.get('minScore') || 0), patterns: fd.getAll('pat'), stages: fd.getAll('stage') };
      if (fd.get('maxBarsSince') !== '' && fd.get('maxBarsSince') !== null) filters.maxBarsSince = Number(fd.get('maxBarsSince'));
      if (!filters.patterns.length || !filters.stages.length) { U.toast('请至少选择一个形态和一个阶段'); return; }
      Jobs.run($('#scanJob'), { scope: { type: 'sectors', ids }, filters }, { onlyMatchedDefault: true, selectable: true, label: '扫描' });
    });
    $('#scanAddWatch').addEventListener('click', async () => {
      const codes = Jobs.selectedCodes($('#scanJob'));
      if (!codes.length) { U.toast('请先勾选股票'); return; }
      const fresh = codes.filter((c) => !U.watch.set.has(c));
      try {
        if (fresh.length) await U.watchOps.addMany(fresh);
        U.toast(`已添加 ${fresh.length} 只股票到自选股池`);
      } catch (e) { U.toast(e.message); }
    });
  }

  window.Scanner = { init, render };
})();
