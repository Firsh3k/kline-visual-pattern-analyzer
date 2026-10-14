/* 路由与启动。hash 路由：#/stock/:code?asOf=&adjustment=、#/watchlist、#/scan、#/sectors、#/catalog、#/about */
(function () {
  'use strict';
  const main = document.getElementById('main');
  const logout = document.getElementById('logout');
  let authed = false;

  function parse() {
    const h = location.hash.replace(/^#/, '') || '/stock';
    const [path, query] = h.split('?');
    const parts = path.split('/').filter(Boolean);
    return { name: parts[0] || 'stock', arg: parts[1] ? decodeURIComponent(parts[1]) : '', query: query || '' };
  }

  async function route() {
    main.onclick = null;
    if (!authed) {
      Views.renderLogin(main, async () => { authed = true; logout.hidden = false; await Views.preloadWatch(); route(); });
      return;
    }
    const r = parse();
    document.querySelectorAll('[data-nav]').forEach((a) => {
      if (a.dataset.nav === r.name) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    const views = { stock: () => Views.renderStock(main, { code: r.arg, query: r.query }), watchlist: () => Views.renderWatchlist(main),
      scan: () => Views.renderScan(main), sectors: () => Views.renderSectors(main), catalog: () => Views.renderCatalog(main),
      about: () => Views.renderAbout(main) };
    const v = views[r.name] || views.stock;
    try { await v(); } catch (e) { main.innerHTML = Views.errorBox(e, '页面加载失败'); }
    main.focus({ preventScroll: true });
  }

  window.addEventListener('hashchange', route);
  window.addEventListener('kl:unauthenticated', () => { authed = false; logout.hidden = true; Views.toast('登录已过期，请重新登录'); route(); });
  logout.addEventListener('click', async () => { try { await API.logout(); } catch (e) { /* 忽略 */ } authed = false; logout.hidden = true; route(); });

  (async function boot() {
    try { const s = await API.session(); authed = s.authenticated; } catch (e) { authed = false; }
    logout.hidden = !authed;
    if (authed) await Views.preloadWatch();
    route();
  })();
})();
