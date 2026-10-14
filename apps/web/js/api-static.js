/* 静态快照版 API（GitHub Pages 等无服务器托管用）：与 api.js 接口相同，但只读 data/ 下由
 * pipeline/daily_snapshot.py 生成的 JSON。这些 JSON 是 Python 分析服务的原样响应；
 * 本文件只做“查文件”和扫描结果的字段过滤（与 services/analysis_api/app/scans.py 的 match() 相同规则），
 * 不做任何形态 / 指标计算。所有写操作返回“只读”错误。 */
(function () {
  'use strict';
  const STAGES = ['forming', 'pre_break', 'breakout_day', 'post_break', 'retest', 'failed_break'];
  const READ_ONLY = '这是每日自动更新的只读版本，不能在网页上修改。要调整自选股或板块，请修改仓库里的 config/daily_snapshot.json，下次自动运行时生效；也可以在 GitHub 的 Actions 页面手动运行“每日快照”。';

  class ApiError extends Error {
    constructor(status, code, message, requestId, details) {
      super(message); this.status = status; this.code = code; this.requestId = requestId; this.details = details || {};
    }
  }

  const cache = {};
  async function file(name, { optional = false } = {}) {
    if (name in cache) return cache[name];
    let r;
    try { r = await fetch(`data/${name}`, { cache: 'no-cache' }); } catch (e) {
      throw new ApiError(0, 'network_error', '网络连接失败，请检查网络后重试', null);
    }
    if (!r.ok) {
      if (optional) return (cache[name] = null);
      throw new ApiError(r.status, 'snapshot_missing', `快照文件缺失：${name}`, null);
    }
    return (cache[name] = await r.json());
  }

  const snapshot = () => file('snapshot.json');

  // 与 scans.py parse_filters / match 相同的规则
  function parseFilters(f) {
    f = f || {};
    return { direction: f.direction || 'any', minScore: f.minScore || 0,
      patterns: f.patterns && f.patterns.length ? f.patterns : null, stages: f.stages && f.stages.length ? f.stages : STAGES,
      maxBarsSince: f.maxBarsSince === undefined ? null : f.maxBarsSince,
      categories: f.categories && f.categories.length ? f.categories : ['price_pattern', 'trend_state'] };
  }
  function match(summary, f) {
    const hits = [];
    for (const [pid, p] of Object.entries(summary.patterns_latest)) {
      if ((f.patterns && !f.patterns.includes(pid)) || !f.stages.includes(p.stage) || !f.categories.includes(p.category)) continue;
      if (f.direction !== 'any' && p.direction !== f.direction) continue;
      if (p.score < f.minScore) continue;
      if (f.maxBarsSince !== null && p.bars_since > f.maxBarsSince) continue;
      hits.push(pid);
    }
    return hits;
  }

  async function scopeResults(sid) {
    const s = (await snapshot()).scopes.find((x) => x.id === sid);
    return s ? (await file(s.file)).results : null;
  }

  const jobs = {};
  async function createScan(body) {
    const scope = body.scope || {};
    if (body.asOf || (body.adjustment && body.adjustment !== 'qfq')) throw new ApiError(422, 'not_in_snapshot', '每日快照只有最新交易日的前复权结果。');
    const f = parseFilters(body.filters);
    const order = []; const byCode = {}; const failures = []; let deduped = 0;
    const add = (it, label) => {
      deduped += 1;
      if (!byCode[it.code]) { byCode[it.code] = { ...it, sectors: [] }; order.push(it.code); }
      if (!byCode[it.code].sectors.includes(label)) byCode[it.code].sectors.push(label);
    };
    const take = async (sid, label) => {
      const res = await scopeResults(sid);
      if (!res) throw new ApiError(409, 'sector_not_scannable', `“${label}”不在每日快照的扫描范围内。${READ_ONLY}`);
      res.items.forEach((it) => add(it, label)); failures.push(...res.failures);
    };
    if (scope.type === 'watchlist') await take('watchlist', '自选股');
    else if (scope.type === 'sectors') {
      const sec = await file('sectors.json');
      const all = [...sec.presets, ...sec.custom, ...sec.dynamic];
      for (const id of scope.ids || []) {
        const s = all.find((x) => x.id === id);
        await take(id, s ? (s.name || s.sectorCode) : id);
      }
    } else if (scope.type === 'codes') {
      const snap = await snapshot(); const known = {};
      for (const s of snap.scopes) (await file(s.file)).results.items.forEach((it) => { known[it.code] = it; });
      for (const c of scope.codes || []) {
        if (known[c]) add({ ...known[c], sectors: undefined }, '自定义股票池');
        else failures.push({ code: c, kind: 'not_in_snapshot', error: '不在每日快照中（把它加入 config/daily_snapshot.json 的自选股）' });
      }
    } else throw new ApiError(422, 'invalid_request', 'scope.type 只能是 sectors / codes / watchlist');
    const items = order.map((c) => { const it = byCode[c]; return { ...it, matchedPatterns: match(it, f) }; });
    const id = 'snap-' + Math.random().toString(16).slice(2, 10);
    const matched = items.filter((i) => i.matchedPatterns.length).length;
    const job = { jobId: id, status: 'completed', total: items.length + failures.length, done: items.length + failures.length,
      succeeded: items.length, failed: failures.length, matched, failures, invalidCodes: [], dedupedFrom: deduped,
      progress: 1, request: { scope, filters: f } };
    jobs[id] = { job, results: { jobId: id, status: 'completed', total: job.total, done: job.done, matched, failures, filters: f, items } };
    return { job };
  }

  async function sectorsView() {
    const sec = JSON.parse(JSON.stringify(await file('sectors.json')));
    const ids = new Set((await snapshot()).scopes.map((s) => s.id));
    const mark = (s) => {
      if (!ids.has(s.id)) Object.assign(s, { scannable: false, status: '不在每日快照', reason: READ_ONLY, verification: undefined });
    };
    sec.presets.forEach(mark); sec.custom.forEach(mark); sec.dynamic.forEach(mark);
    return sec;
  }

  async function route(method, path, body) {
    const [p] = path.split('?');
    if (method === 'GET') {
      const fixed = { '/api/v1/market': 'market.json', '/api/v1/calibration': 'calibration.json',
        '/api/v1/meta/patterns': 'meta_patterns.json', '/api/v1/watchlist': 'watchlist.json' };
      if (fixed[p]) return file(fixed[p]);
      if (p === '/api/v1/sectors') return sectorsView();
      if (p === '/api/v1/version') {
        const v = await file('version.json'); const s = await snapshot();
        return { frontend: { version: '每日快照', commit: '—', releasedAt: s.generated_at, environment: 'GitHub Pages（只读）',
          rollbackBaseline: '—', verified: false }, engine: { ...v.engine, reachable: true }, compatibility: { ok: true } };
      }
      let m = p.match(/^\/api\/v1\/scans\/([^/]+)$/);
      if (m && jobs[m[1]]) return { job: jobs[m[1]].job };
      m = p.match(/^\/api\/v1\/scans\/([^/]+)\/results$/);
      if (m && jobs[m[1]]) return jobs[m[1]].results;
      if (p === '/api/v1/watchlist/export') throw new ApiError(405, 'read_only', READ_ONLY);
      throw new ApiError(404, 'not_found', '接口不存在');
    }
    if (method === 'POST' && p === '/api/v1/analyses') {
      const b = body || {};
      if (b.asOf || (b.adjustment && b.adjustment !== 'qfq')) {
        throw new ApiError(422, 'not_in_snapshot', '每日快照只有最新交易日的前复权结果，不能指定截至日期或不复权。');
      }
      const doc = await file(`analyses/${b.code}.json`, { optional: true });
      if (!doc) {
        throw new ApiError(404, 'not_in_snapshot', `${b.code} 不在每日快照中。把它加进 config/daily_snapshot.json 的自选股，下次自动运行后就能看到；着急的话可以在 GitHub 的 Actions 页面手动运行“每日快照”。`);
      }
      return doc;
    }
    if (method === 'POST' && p === '/api/v1/scans') return createScan(body);
    if (method === 'POST' && p === '/api/v1/sectors/dynamic') {
      const code = body && body.sectorCode; const sec = await sectorsView();
      const d = sec.dynamic.find((x) => x.sectorCode === code);
      if (d && d.scannable) return { sector: d };
      throw new ApiError(409, 'not_in_snapshot', `同花顺板块 ${code} 不在每日快照中。${READ_ONLY}`);
    }
    throw new ApiError(405, 'read_only', READ_ONLY);
  }

  async function request(method, path, body) {
    const data = await route(method, path, body);
    return { data, etag: null, requestId: null };
  }

  function banner() {
    snapshot().then((s) => {
      const el = document.createElement('div');
      el.className = 'notice small'; el.id = 'snapBanner';
      const t = s.generated_at.slice(0, 16).replace('T', ' ');
      el.textContent = `每日快照：生成于 ${t}（北京时间），每个交易日收盘后自动更新，只读。`;
      const main = document.getElementById('main');
      if (main && main.parentNode) main.parentNode.insertBefore(el, main);
    }).catch(() => {});
  }
  document.addEventListener('DOMContentLoaded', () => {
    banner();
    const st = document.createElement('style');   // 只读：隐藏会修改数据的控件
    st.textContent = '#wlAdd,#wlClear,#wlExport,[data-rm],#watchToggle,#dynAdd,#cusForm,[data-deldyn],[data-delcus],[data-editcus],[data-refresh],[data-verify],[data-snap]{display:none!important}';
    document.head.appendChild(st);
    const lo = document.getElementById('logout'); if (lo) lo.remove();
  });

  window.API = {
    ApiError,
    static: true,
    setCsrf() {},
    async session() { return { authenticated: true, csrf: null }; },
    async login() { return { authenticated: true }; },
    async logout() {},
    get: (p) => request('GET', p),
    post: (p, body) => request('POST', p, body),
    put: (p, body) => request('PUT', p, body),
    patch: (p, body) => request('PATCH', p, body),
    del: (p) => request('DELETE', p),
    opId() { return 'w' + Math.random().toString(16).slice(2); },
  };
})();
