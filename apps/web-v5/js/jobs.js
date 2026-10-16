/* 扫描任务（自选股“分析全部”与市场扫描共用）：POST /api/v1/scans → 轮询 GET /scans/{id} → GET /scans/{id}/results，
 * 结果用 V5 表格样式展示。每行的形态 / 阶段 / 分数取自引擎的 patterns_latest，与个股分析同源。 */
(function () {
  'use strict';
  const { esc, fmt, sign, cls } = F;
  const { $ } = U;

  function chips(item, ids, dir) {
    const own = ids.filter((pid) => item.patterns_latest[pid] && item.patterns_latest[pid].direction === dir);
    if (!own.length) return '<span class="muted small">—</span>';
    return own.map((pid) => {
      const p = item.patterns_latest[pid];
      return `<span class="pchip ${esc(p.direction)}" data-pid="${esc(pid)}" data-stage="${esc(p.stage)}" data-score="${p.score}">${esc(p.pattern_name)} · ${esc(F.stageText(p))} · <b>${p.score}</b></span>`;
    }).join('');
  }

  function table(items, only, selectable) {
    if (!items.length) return '<div class="empty">暂无符合条件的股票</div>';
    const idsOf = (it) => (only ? it.matchedPatterns : Object.keys(it.patterns_latest));
    const best = (it) => Math.max(-1, ...idsOf(it).map((pid) => it.patterns_latest[pid].score));
    const rows = [...items].sort((a, b) => best(b) - best(a) || a.code.localeCompare(b.code));
    return `<div class="tbl-wrap"><table class="tbl res-tbl" data-testid="results"><thead><tr>
      ${selectable ? '<th class="w30"><input type="checkbox" class="sel-all" aria-label="全选"></th>' : ''}
      <th>股票</th><th>现价</th><th>涨跌幅</th><th class="c-up">上升形态</th><th class="c-down">下降形态</th><th class="n">最高分</th><th>数据截至</th></tr></thead><tbody>
      ${rows.map((it) => { const b = best(it); return `<tr class="res-row" data-code="${esc(it.code)}" title="单击查看详情">
        ${selectable ? `<td class="nopick"><input type="checkbox" class="sel-item" value="${esc(it.code)}" aria-label="选择 ${esc(it.code)}"></td>` : ''}
        <td><b class="nm">${esc(it.name)}</b> <a class="newtab nopick" href="?code=${esc(it.code)}" target="_blank" rel="noopener" title="新标签页打开">↗</a><br><span class="sub">${esc(it.code)}</span>
          ${it.sectors && it.sectors.length ? `<br><span class="sec-tags">${it.sectors.slice(0, 2).map((s) => '🏷️' + esc(s)).join(' ')}${it.sectors.length > 2 ? ' +' + (it.sectors.length - 2) : ''}</span>` : ''}</td>
        <td>${fmt(it.close)}</td>
        <td class="${cls(it.change_pct)}">${sign(Number(it.change_pct).toFixed(2))}%</td>
        <td>${chips(it, idsOf(it), 'bull')}</td>
        <td>${chips(it, idsOf(it), 'bear')}</td>
        <td class="n" data-field="best">${b >= 0 ? b : '—'}</td>
        <td class="small">${esc(it.as_of)}${it.stale ? '<br><span class="c-warn">未更新到最新交易日</span>' : ''}${it.session_state === 'intraday' ? '<br><span class="c-warn">盘中</span>' : ''}</td></tr>`; }).join('')}
      </tbody></table></div>`;
  }

  function failures(f) {
    if (!f.length) return '';
    return `<details class="fail-box" open><summary>失败清单（${f.length}）</summary><div class="tbl-wrap"><table class="tbl" data-testid="failures"><thead><tr><th>代码</th><th>类型</th><th>原因</th></tr></thead><tbody>${
      f.map((x) => `<tr><td class="num">${esc(x.code)}</td><td class="small">${esc(x.kind)}</td><td class="small">${esc(x.error)}</td></tr>`).join('')}</tbody></table></div></details>`;
  }

  async function run(container, body, { onlyMatchedDefault = true, selectable = false, label = '扫描' } = {}) {
    container.innerHTML = U.loading('正在创建任务…');
    let job;
    try { job = (await API.post('/api/v1/scans', body, { timeoutMs: 40000 })).data.job; } catch (e) {
      container.innerHTML = U.errorBox(e, `无法开始${label}`);
      const v = e.details && e.details.verification;
      if (v) container.insertAdjacentHTML('beforeend', `<div class="warn-box">该板块需先完成权威验证：<b>${esc(v.instruction)}</b>（见下方“同花顺动态板块”）</div>`);
      return null;
    }
    container.innerHTML = `<div class="job" data-testid="job">
      <div class="job-head"><span class="job-text" aria-live="polite"></span>
        <button class="btn btn-ghost btn-sm" type="button" data-cancel>取消</button></div>
      <div class="progress"><div class="progress-bar"></div></div>
      <div class="job-res"></div></div>`;
    const text = container.querySelector('.job-text'); const bar = container.querySelector('.progress-bar');
    const cancel = container.querySelector('[data-cancel]');
    cancel.addEventListener('click', async () => { try { await API.post(`/api/v1/scans/${job.jobId}/cancel`, {}); } catch (e) { U.toast(e.message); } });
    const show = (j) => {
      bar.style.width = `${Math.round((j.progress || 0) * 100)}%`;
      text.textContent = `${F.JOB_STATUS[j.status] || j.status} ${j.done}/${j.total}，成功 ${j.succeeded}，失败 ${j.failed}，符合条件 ${j.matched}` +
        `${j.invalidCodes && j.invalidCodes.length ? `，无效代码 ${j.invalidCodes.length}` : ''}${j.dedupedFrom > j.total ? `（去重前 ${j.dedupedFrom}）` : ''}`;
    };
    show(job);
    for (;;) {
      await new Promise((r) => setTimeout(r, 900));
      if (!document.body.contains(text)) return null;   // 已被新的任务替换
      try { job = (await API.get(`/api/v1/scans/${job.jobId}`)).data.job; } catch (e) {
        if (e.status === 401) return null;
        text.textContent = `进度查询失败：${e.message}（稍后自动重试）`; continue;
      }
      show(job);
      if (!['queued', 'running'].includes(job.status)) break;
    }
    cancel.classList.add('hidden');
    let res;
    try { res = (await API.get(`/api/v1/scans/${job.jobId}/results`)).data; } catch (e) {
      container.querySelector('.job-res').innerHTML = U.errorBox(e, '读取结果失败'); return null;
    }
    res.items.forEach((it) => U.watch.names.set(it.code, it.name));
    try {   // 状态行附上大盘环境（引擎结果，非必需）
      const m = (await U.market()).market_env;
      if (m && document.body.contains(text)) text.textContent += `（大盘${m.score}分 ${m.stage}）`;
    } catch (e) { /* 忽略 */ }
    const box = container.querySelector('.job-res');
    const draw = (only) => {
      const items = only ? res.items.filter((i) => i.matchedPatterns.length) : res.items;
      box.innerHTML = `<label class="only-m"><input type="checkbox" class="onlyM"${only ? ' checked' : ''}> 只看符合过滤条件的（${res.matched}）</label>
        ${table(items, only, selectable)}${failures(res.failures)}
        <div class="hint">${selectable ? '勾选后点“加入自选股”批量添加 | ' : ''}单击行查看详情 | ↗ 新标签页打开 | 按引擎形态最高分排序</div>`;
      box.querySelector('.onlyM').addEventListener('change', (e) => draw(e.target.checked));
      const all = box.querySelector('.sel-all');
      if (all) all.addEventListener('change', () => box.querySelectorAll('.sel-item').forEach((c) => { c.checked = all.checked; }));
    };
    draw(onlyMatchedDefault);
    box.addEventListener('click', (e) => {
      if (e.target.closest('.nopick')) return;
      const tr = e.target.closest('tr.res-row'); if (tr) Analyze.analyze(tr.dataset.code);
    });
    return res;
  }

  function selectedCodes(container) {
    return [...container.querySelectorAll('.sel-item:checked')].map((c) => c.value);
  }

  window.Jobs = { run, selectedCodes };
})();
