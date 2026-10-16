/* 共用：提示、错误框（显示 BFF 的 message 与 requestId，同 V6）、引擎数据缓存、服务端自选股（ETag / If-Match）。 */
(function () {
  'use strict';
  const { esc } = F;
  const $ = (sel, root = document) => root.querySelector(sel);

  function toast(msg) {
    const t = $('#toast'); t.textContent = msg; t.classList.remove('hidden');
    clearTimeout(toast._t); toast._t = setTimeout(() => { t.classList.add('hidden'); }, 3200);
  }

  function errorBox(e, title = '请求失败') {
    const rid = e && e.requestId ? `<div class="rid">requestId：<code>${esc(e.requestId)}</code></div>` : '';
    const issues = e && e.details && e.details.issues ? `<details><summary>数据问题明细（${e.details.issues.length}）</summary><ul>${
      e.details.issues.map((i) => `<li>第 ${esc(i.index)} 根 ${esc(i.date)} ${esc(i.field)}：${esc(i.reason)}</li>`).join('')}</ul></details>` : '';
    return `<div class="error-msg" role="alert"><b>${esc(title)}</b><div class="err-text">${esc(e ? e.message : '未知错误')}</div>${issues}${rid}</div>`;
  }

  const loading = (text) => `<div class="loading"><div class="spinner"></div>${esc(text)}</div>`;

  // ---------------------------------------------------------------- 引擎数据缓存
  const cache = { market: new Map(), calibration: null, catalog: null };

  async function market(asOf) {
    const k = asOf || '';
    if (!cache.market.has(k)) {
      const p = API.get('/api/v1/market' + (asOf ? `?asOf=${encodeURIComponent(asOf)}` : '')).then((r) => r.data);
      cache.market.set(k, p);
      p.catch(() => cache.market.delete(k));
    }
    return cache.market.get(k);
  }
  async function calibration() {
    if (!cache.calibration) {
      cache.calibration = API.get('/api/v1/calibration').then((r) => r.data);
      cache.calibration.catch(() => { cache.calibration = null; });
    }
    return cache.calibration;
  }
  async function catalog() {
    if (!cache.catalog) {
      cache.catalog = API.catalog();
      cache.catalog.catch(() => { cache.catalog = null; });
    }
    return cache.catalog;
  }
  // 校准表中某形态 全市场 / 全确认状态 的 T+20 统计（与 V6 网页同一取法）
  function calibGroup(cal, pid) {
    return cal.groups.find((x) => x.pattern_type === pid && x.market_regime === 'all' && x.confirmation_state === 'all');
  }

  // ---------------------------------------------------------------- 自选股（服务器端；ETag + If-Match）
  const watch = { codes: [], set: new Set(), etag: null, version: null, names: new Map(), listeners: [] };

  function setWatchState(st, etag) {
    watch.codes = st.watchlist.map((w) => w.code);
    watch.set = new Set(watch.codes);
    watch.version = st.version;
    if (etag) watch.etag = etag;
    watch.listeners.forEach((fn) => { try { fn(st); } catch (e) { /* 忽略 */ } });
  }
  async function loadWatch() {
    const r = await API.get('/api/v1/watchlist');
    setWatchState(r.data, r.etag);
    return r.data;
  }
  // 提交操作；版本不一致（412）时先重新读取最新列表，再按最新版本重提一次（操作带时间戳与 opId，可安全重放）
  async function patchWatch(ops) {
    const send = () => API.patch('/api/v1/watchlist', { ops }, { headers: watch.etag ? { 'If-Match': watch.etag } : {} });
    let r;
    try { r = await send(); } catch (e) {
      if (e.status !== 412) throw e;
      await loadWatch();
      toast('自选股已在其他设备更新，已同步最新列表后重新提交');
      r = await send();
    }
    setWatchState(r.data.state, r.etag);
    const ign = r.data.results.filter((x) => x.status === 'ignored');
    if (ign.length) {
      toast(ign.map((x) => (x.reason === 'deleted_later' ? '该股票已在其他设备删除（删除时间更晚）'
        : x.reason === 'added_later' ? '该股票已在其他设备重新添加' : x.reason === 'watchlist_full' ? '自选股已满' : x.reason)).join('；'));
    }
    return r.data;
  }
  const now = () => new Date().toISOString();
  const watchOps = {
    add: (code) => patchWatch([{ opId: API.opId(), op: 'add', code, at: now() }]),
    addMany: (codes) => patchWatch(codes.map((code) => ({ opId: API.opId(), op: 'add', code, at: now() }))),
    remove: (code) => patchWatch([{ opId: API.opId(), op: 'remove', code, at: now() }]),
    clear: () => patchWatch([{ opId: API.opId(), op: 'clear', at: now() }]),
  };

  const splitCodes = (s) => String(s || '').split(/[\s,，;；、]+/).map((x) => x.trim()).filter(Boolean);

  window.U = { $, toast, errorBox, loading, cache, market, calibration, catalog, calibGroup,
    watch, loadWatch, watchOps, splitCodes };
})();
