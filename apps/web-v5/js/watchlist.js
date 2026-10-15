/* 自选股池：服务器端自选股（GET / PATCH /api/v1/watchlist，带 ETag / If-Match），
 * “分析全部” = 扫描任务 scope {type:'watchlist'}，结果用 V5 表格展示。 */
(function () {
  'use strict';
  const { esc } = F;
  const { $ } = U;
  let loaded = false;

  function drawTags() {
    const box = $('#watchlistTags');
    $('#wlVer').textContent = U.watch.version !== null ? `（${U.watch.codes.length} 只 · 版本 ${U.watch.version}）` : '';
    if (!U.watch.codes.length) { box.innerHTML = '<span class="hint">暂无自选股，请在上方输入代码添加</span>'; return; }
    box.innerHTML = `<div class="tag-list" data-testid="watchlist">${U.watch.codes.map((code) => {
      const name = U.watch.names.get(code);
      return `<span class="wtag" data-code="${esc(code)}"><button class="wtag-open" type="button" data-open="${esc(code)}" title="查看分析">${name ? `<span class="nm">${esc(name)}</span> ` : ''}<span class="sub">${esc(code)}</span></button>
        <button class="wtag-x" type="button" data-rm="${esc(code)}" title="从所有设备删除" aria-label="移除 ${esc(code)}">×</button></span>`;
    }).join('')}</div>`;
  }

  async function render() {
    if (!loaded) {
      $('#watchlistTags').innerHTML = U.loading('正在读取自选股…');
      try { await U.loadWatch(); loaded = true; } catch (e) { $('#watchlistTags').innerHTML = U.errorBox(e, '读取自选股失败'); return; }
    }
    drawTags();
  }

  async function mutate(fn) {
    try { await fn(); } catch (e) {
      U.toast(e.message + (e.requestId ? `（requestId ${e.requestId}）` : ''));
      try { await U.loadWatch(); } catch (e2) { /* 保留当前显示 */ }
    }
    drawTags();
  }

  function init() {
    U.watch.listeners.push(() => { loaded = true; if (document.getElementById('watchlistTags')) drawTags(); });
    $('#wlAddForm').addEventListener('submit', (ev) => {
      ev.preventDefault();
      const input = $('#addStockInput'); const code = input.value.trim();
      if (!/^\d{6}$/.test(code)) { U.toast('请输入6位股票代码'); return; }
      if (U.watch.set.has(code)) { U.toast('该股票已在自选股池中'); return; }
      input.value = '';
      mutate(() => U.watchOps.add(code));
    });
    $('#watchlistTags').addEventListener('click', (ev) => {
      const rm = ev.target.closest('[data-rm]');
      if (rm) { mutate(() => U.watchOps.remove(rm.dataset.rm)); return; }
      const op = ev.target.closest('[data-open]');
      if (op) Analyze.analyze(op.dataset.open);
    });
    $('#wlClear').addEventListener('click', () => {
      if (!U.watch.codes.length) { U.toast('自选股池为空'); return; }
      if (!confirm('确定清空全部自选股？（删除记录会同步到其他设备）')) return;
      mutate(() => U.watchOps.clear());
    });
    $('#wlAnalyzeAll').addEventListener('click', async () => {
      if (!U.watch.codes.length) { U.toast('自选股池为空，请先添加股票'); return; }
      const res = await Jobs.run($('#wlJob'), { scope: { type: 'watchlist' } }, { onlyMatchedDefault: false, label: '分析' });
      if (res) drawTags();   // 结果里带了股票名称
    });
  }

  window.Watchlist = { init, render };
})();
