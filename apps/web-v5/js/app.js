/* 启动：引擎地址 → 登录（令牌）→ 四个标签页。支持 ?code=600519 深链接（同 V5）。 */
(function () {
  'use strict';
  const { esc } = F;
  const { $ } = U;
  const TABS = ['patterns', 'analyze', 'watchlist', 'scanner'];
  let current = 'analyze';
  let authed = false;
  const rendered = new Set();

  function switchTab(tab) {
    if (!TABS.includes(tab)) tab = 'analyze';
    current = tab;
    document.querySelectorAll('.tab').forEach((t) => {
      const on = t.dataset.tab === tab;
      t.classList.toggle('active', on);
      if (on) t.setAttribute('aria-current', 'page'); else t.removeAttribute('aria-current');
    });
    TABS.forEach((t) => $(`#page-${t}`).classList.toggle('active', t === tab));
    if (!authed) return;
    if (tab === 'patterns') Catalog.render();
    if (tab === 'watchlist') Watchlist.render();
    if (tab === 'scanner' && !rendered.has('scanner')) { rendered.add('scanner'); Scanner.render(); }
    if (tab === 'analyze') Analyze.redraw();
  }

  // ---------------------------------------------------------------- 引擎地址
  function showEnginePanel(show, msg) {
    $('#enginePanel').classList.toggle('hidden', !show);
    if (show) {
      $('#engineInput').value = API.engine();
      $('#pageOrigin').textContent = location.origin && location.origin !== 'null' ? location.origin : '（本地文件，无法跨域访问；请把本文件放到网站上打开）';
      $('#engineMsg').innerHTML = msg ? `<div class="warn-box">${esc(msg)}</div>` : '';
      $('#engineCancel').classList.toggle('hidden', !API.engine());
    }
  }

  function refreshEngineLabels() {
    const e = API.engine();
    $('#loginEngine').textContent = e || '（未设置）';
    $('#footerEngine').textContent = e || '—';
    $('#loginEngineEdit').classList.toggle('hidden', API.servedByBff());
    $('#engineBtn').classList.toggle('hidden', API.servedByBff());
  }

  // ---------------------------------------------------------------- 登录
  function lock(msg) {
    authed = false;
    document.body.classList.remove('authed');
    $('#logoutBtn').classList.add('hidden');
    refreshEngineLabels();
    if (!API.engine()) { $('#loginPanel').classList.add('hidden'); showEnginePanel(true, msg || '请先填写分析引擎地址。'); return; }
    $('#loginPanel').classList.remove('hidden');
    $('#loginErr').innerHTML = msg ? `<div class="warn-box">${esc(msg)}</div>` : '';
  }

  async function unlock() {
    authed = true;
    document.body.classList.add('authed');
    $('#loginPanel').classList.add('hidden');
    showEnginePanel(false);
    $('#logoutBtn').classList.remove('hidden');
    rendered.clear();
    try { await U.loadWatch(); } catch (e) { /* 自选股页会显示错误 */ }
    loadVersion();
    const code = new URLSearchParams(location.search).get('code');
    if (pendingFirst) {
      pendingFirst = false;
      if (code && /^\d{6}$/.test(code)) { Analyze.analyze(code); return; }
      if (current === 'analyze' && !$('#resultArea').innerHTML.trim()) { $('#codeInput').value = '002436'; switchTab('analyze'); Analyze.run(); return; }
    }
    switchTab(current);
  }
  let pendingFirst = true;

  // ---------------------------------------------------------------- 版本徽章
  async function loadVersion() {
    try {
      const v = (await API.get('/api/v1/version')).data;
      const en = v.engine || {};
      $('#versionTag').textContent = `V5 界面 · V6 引擎 ${en.verified ? en.version : ''}`.trim();
      $('#versionBadge').classList.toggle('verified', !!(en.verified && v.compatibility && v.compatibility.ok));
      $('#versionModalBody').innerHTML = [
        ['引擎地址', API.engine()], ['引擎版本', en.verified ? en.version : 'Version Unknown'], ['引擎提交', en.verified ? en.commit : '—'],
        ['core', en.core_version || '—'], ['接口 / 结果 schema', `${en.api_schema_version || '—'} / ${en.analysis_schema_version || '—'}`],
        ['行情提供者', en.provider || '—'], ['连通', en.reachable ? '正常' : '无法连接'],
        ['兼容性', v.compatibility && v.compatibility.ok ? '网页与引擎接口版本一致' : `不兼容或不可用：${(v.compatibility && v.compatibility.reason) || ''}`],
      ].map(([k, val]) => `<div class="version-info-row"><span class="version-info-label">${esc(k)}</span><span class="version-info-value">${esc(val)}</span></div>`).join('');
    } catch (e) {
      $('#versionModalBody').innerHTML = U.errorBox(e, '读取引擎版本失败');
    }
  }

  function init() {
    Catalog.init(); Analyze.init(); Watchlist.init(); Scanner.init();
    $('#tabs').addEventListener('click', (e) => { const b = e.target.closest('[data-tab]'); if (b) switchTab(b.dataset.tab); });
    $('#versionBadge').addEventListener('click', () => {
      if (!$('#versionModalBody').innerHTML) $('#versionModalBody').innerHTML = `<div class="version-info-row"><span class="version-info-label">引擎地址</span><span class="version-info-value">${esc(API.engine() || '未设置')}</span></div>`;
      $('#versionModal').classList.remove('hidden');
    });
    $('#versionModalClose').addEventListener('click', () => $('#versionModal').classList.add('hidden'));
    $('#versionModal').addEventListener('click', (e) => { if (e.target.id === 'versionModal') $('#versionModal').classList.add('hidden'); });
    $('#engineBtn').addEventListener('click', () => showEnginePanel($('#enginePanel').classList.contains('hidden')));
    $('#loginEngineEdit').addEventListener('click', () => showEnginePanel(true));
    $('#engineCancel').addEventListener('click', () => showEnginePanel(false));
    $('#engineForm').addEventListener('submit', (ev) => {
      ev.preventDefault();
      try {
        const before = API.engine();
        API.setEngine($('#engineInput').value);
        showEnginePanel(false);
        U.cache.market.clear(); U.cache.calibration = null; U.cache.catalog = null;
        if (API.engine() !== before || !API.hasToken()) lock('引擎地址已保存，请登录。'); else refreshEngineLabels();
      } catch (e) { $('#engineMsg').innerHTML = U.errorBox(e, '引擎地址不正确'); }
    });
    $('#loginForm').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const fd = new FormData(ev.target);
      const btn = $('#loginSubmit'); btn.disabled = true; btn.textContent = '登录中…';
      $('#loginErr').innerHTML = '';
      try {
        await API.login(String(fd.get('username')), String(fd.get('password')));
        ev.target.reset();
        await unlock();
      } catch (e) { $('#loginErr').innerHTML = U.errorBox(e, '登录失败'); }
      btn.disabled = false; btn.textContent = '登录';
    });
    $('#logoutBtn').addEventListener('click', () => { API.logout(); lock('已退出登录。'); });
    window.addEventListener('kl:unauthenticated', () => { if (authed) U.toast('登录已过期，请重新登录'); lock('登录已过期或未登录，请重新登录。'); });

    refreshEngineLabels();
    if (API.engine() && API.hasToken()) unlock(); else lock();
  }

  window.App = { switchTab };
  init();
})();
