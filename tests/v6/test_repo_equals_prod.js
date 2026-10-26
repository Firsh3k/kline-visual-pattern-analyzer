/**
 * FIR-41 第 5 项：CI 检查“仓库 = 线上”（依赖、启动方式、运行时文件、页面指纹）。
 *
 * 妙搭的 @lark-apaas/* 私有包在 CI 装不了，所以这里不做完整构建，而是静态校验仓库里的平台快照自洽：
 * 1. package.json 的每个依赖都在 package-lock.json 里，且锁定版本满足声明的范围
 * 2. 服务端运行时导入的每个包：是 dependencies 里的直接依赖，或在 runtime-deps.json 登记且能沿 lock 找到
 * 3. 启动链：run.sh → node server/main.js；build.sh 把 run.sh 拷进 dist；nest sourceRoot=server；prune-smart 入口一致
 * 4. 平台打包后的运行时文件：服务端不再按 cwd 读 .py / json（引擎、板块清单随模块内嵌）；页面按线上 / 开发布局查找；
 *    没有根级 @Controller，页面由 main.ts 的中间件托管
 * 5. /api/health 的 deploy 字段：页面 sha256 = scripts/v6_fingerprint.py 的 page_sha256，引擎包 / 板块清单 sha256 一致；不泄露环境变量
 * 真实构建 + 模拟平台部署 + 启动服务的检查在 tests/v6/test_platform_package.js。
 *
 * 运行：node tests/v6/test_repo_equals_prod.js（需要 typescript、semver 包）
 */
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const { execFileSync } = require('child_process');
const { builtinModules } = require('module');
const ts = require('typescript');
const semver = require('semver');

const ROOT = path.resolve(__dirname, '..', '..');
const APP = path.join(ROOT, 'v6', 'app-builder');
const read = (rel) => fs.readFileSync(path.join(APP, rel), 'utf8');
const pkg = JSON.parse(read('package.json'));
const lock = JSON.parse(read('package-lock.json'));
const runtimeDeps = JSON.parse(read('runtime-deps.json'));
const P = lock.packages || {};

let passed = 0, failed = 0;
const pending = [];
function test(name, fn) {
  const ok = () => { passed++; console.log('  ✓ ' + name); };
  const bad = (e) => { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n      ') : e)); };
  try {
    const r = fn();
    if (r && typeof r.then === 'function') pending.push(r.then(ok, bad));
    else ok();
  } catch (e) { bad(e); }
}

function walk(dir, exts, out = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) { if (ent.name !== 'node_modules' && ent.name !== '__pycache__') walk(p, exts, out); }
    else if (exts.some((e) => ent.name.endsWith(e))) out.push(p);
  }
  return out;
}

/** 文件里的运行时导入（跳过 import type / 相对路径 / Node 内置） */
function runtimeImports(file) {
  const src = fs.readFileSync(file, 'utf8');
  const names = new Set();
  const re = /(?:^|\n)\s*import\s+(type\s+)?[^'"]*?from\s+['"]([^'"]+)['"]|(?:^|\n)\s*import\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)/g;
  let m;
  while ((m = re.exec(src))) {
    if (m[1]) continue;                                  // import type … from
    const spec = m[2] || m[3] || m[4];
    if (!spec || spec.startsWith('.') || spec.startsWith('node:')) continue;
    const name = spec.startsWith('@') ? spec.split('/').slice(0, 2).join('/') : spec.split('/')[0];
    if (builtinModules.includes(name)) continue;
    names.add(name);
  }
  return names;
}

/** 按 node 的解析规则在 lock 里找某个包被 from（lock 路径）依赖时实际用的那一份 */
function resolveInLock(name, from = '') {
  let base = from;
  for (;;) {
    const key = (base ? base + '/' : '') + 'node_modules/' + name;
    if (P[key]) return key;
    if (!base) return null;
    const i = base.lastIndexOf('/node_modules/');
    base = i >= 0 ? base.slice(0, i) : '';
  }
}

console.log('依赖：package.json ↔ package-lock.json');
test('lock 与 package.json 对得上（名称、版本、lockfileVersion 3）', () => {
  assert.strictEqual(lock.name, pkg.name);
  assert.strictEqual(lock.version, pkg.version);
  assert.strictEqual(lock.lockfileVersion, 3);
});

test('package.json 的每个依赖都锁定在 lock 里，且锁定版本满足声明的范围', () => {
  const bad = [];
  for (const [name, range] of Object.entries({ ...pkg.dependencies, ...pkg.devDependencies })) {
    const ent = P['node_modules/' + name];
    if (!ent) { bad.push(`${name}：lock 里没有`); continue; }
    const r = (pkg.overrides && pkg.overrides[name]) || range;
    if (!semver.satisfies(ent.version, r, { includePrerelease: true })) bad.push(`${name}：锁定 ${ent.version} 不满足 ${r}`);
  }
  assert.deepStrictEqual(bad, [], bad.join('\n'));
});

test('dependencies 里没有被 lock 标成 dev 的包（否则生产安装会缺）', () => {
  const bad = Object.keys(pkg.dependencies).filter((n) => P['node_modules/' + n] && P['node_modules/' + n].dev);
  assert.deepStrictEqual(bad, []);
});

console.log('运行时导入');
const serverFiles = walk(path.join(APP, 'server'), ['.ts']);
const probeFile = path.join(APP, 'scripts', 'watchlist-probe.js');
const imports = new Map();   // 包名 → 用到它的文件
for (const f of [...serverFiles, probeFile]) {
  for (const n of runtimeImports(f)) {
    if (!imports.has(n)) imports.set(n, []);
    imports.get(n).push(path.relative(APP, f));
  }
}

test('导入扫描有效：能扫到已知的运行时依赖', () => {
  for (const n of ['@nestjs/common', '@nestjs/core', 'postgres', 'hbs']) assert.ok(imports.has(n), `没扫到 ${n}`);
  assert.ok(!imports.has('express') || pkg.dependencies.express, 'express 只能以 import type 出现，或是直接依赖');
});

test('服务端运行时导入的每个包：是直接依赖，或在 runtime-deps.json 登记', () => {
  const bad = [];
  for (const [name, files] of imports) {
    if (pkg.dependencies[name]) continue;
    if (runtimeDeps.transitive[name]) continue;
    bad.push(`${name}（${files.join(', ')}）：不是 dependencies 里的直接依赖，也没在 runtime-deps.json 登记`);
  }
  assert.deepStrictEqual(bad, [], bad.join('\n'));
});

test('runtime-deps.json 登记的间接依赖：沿登记的链路在 lock 里能找到，版本满足上游要求，登记的使用方真的在用', () => {
  const bad = [];
  for (const [name, info] of Object.entries(runtimeDeps.transitive)) {
    const chain = [...info.via, name];
    if (!pkg.dependencies[chain[0]]) { bad.push(`${name}：链路起点 ${chain[0]} 不是直接依赖`); continue; }
    let at = resolveInLock(chain[0]);
    for (let i = 1; i < chain.length && at; i++) {
      const parent = P[at];
      const range = parent.dependencies && parent.dependencies[chain[i]];
      if (!range) { bad.push(`${name}：${chain[i - 1]} 不再依赖 ${chain[i]}`); at = null; break; }
      const next = resolveInLock(chain[i], at);
      if (!next) { bad.push(`${name}：lock 里找不到 ${chain[i]}`); at = null; break; }
      if (!semver.satisfies(P[next].version, range)) { bad.push(`${name}：${chain[i]} 锁定 ${P[next].version} 不满足 ${chain[i - 1]} 要求的 ${range}`); }
      if (P[next].dev) bad.push(`${name}：${chain[i]} 在 lock 里是 dev`);
      at = next;
    }
    const users = imports.get(name) || [];
    for (const u of info.used_by) if (!users.includes(u)) bad.push(`${name}：登记的使用方 ${u} 实际没导入它`);
    if (pkg.dependencies[name]) bad.push(`${name}：已经是直接依赖，请从 runtime-deps.json 删掉`);
  }
  assert.deepStrictEqual(bad, [], bad.join('\n'));
});

console.log('启动方式');
test('启动链一致：run.sh → node server/main.js；build.sh 拷 run.sh 到 dist；nest sourceRoot=server；prune-smart 入口 dist/server/main.js', () => {
  assert.ok(/^\s*NODE_ENV=production node server\/main\.js\s*$/m.test(read('scripts/run.sh')), 'run.sh 启动命令变了');
  assert.ok(read('scripts/build.sh').includes('cp "$ROOT_DIR/scripts/run.sh" "$DIST_DIR/"'), 'build.sh 不再把 run.sh 拷进 dist');
  assert.strictEqual(JSON.parse(read('nest-cli.json')).sourceRoot, 'server');
  const prune = read('scripts/prune-smart.js');
  assert.ok(/DIST_SERVER_DIR\s*=\s*path\.join\(DIST_DIR,\s*'server'\)/.test(prune) && /SERVER_ENTRY\s*=\s*path\.join\(DIST_SERVER_DIR,\s*'main\.js'\)/.test(prune), 'prune-smart 入口变了');
  assert.ok(fs.existsSync(path.join(APP, 'server', 'main.ts')));
  assert.strictEqual(pkg.scripts.build, './scripts/build.sh');
});

// deploy-info.ts / v4-page.ts（转译后直接加载）
function loadTs(file, requireMap = {}) {
  const src = path.join(APP, 'server', 'modules', 'stock-analysis', file);
  const out = ts.transpileModule(fs.readFileSync(src, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true }, reportDiagnostics: true,
  });
  assert.strictEqual((out.diagnostics || []).length, 0, `${file} TS 诊断非空`);
  const mod = { exports: {} };
  vm.runInThisContext(`(function (module, exports, require) {${out.outputText}\n})`)(mod, mod.exports, (n) => (n in requireMap ? requireMap[n] : require(n)));
  return mod.exports;
}
const D = loadTs('deploy-info.ts');
const V = loadTs('v4-page.ts', { './deploy-info': D });
const fp = JSON.parse(execFileSync('python3', [path.join(ROOT, 'scripts', 'v6_fingerprint.py')], { encoding: 'utf8' }));
const ENGINE_OK = { ready: true, source: 'bundle', bundle_sha256: fp.engine_bundle_sha256, file_count: 36, dir: '/tmp/x', error: null };
const CATALOG_OK = { ready: true, sha256: fp.sector_catalog_sha256, error: null };
const PAGE_SRC = path.join(APP, 'client', 'public', 'v4', 'index.html');
const pageSha = crypto.createHash('sha256').update(fs.readFileSync(PAGE_SRC)).digest('hex');
/** 模拟线上运行目录（= 构建产物 dist/）：build.sh 把 client/public 拷到 dist/dist/client，cwd=dist */
function prodLayout() {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'deploy-info-'));
  fs.mkdirSync(path.join(tmp, 'dist', 'client', 'v4'), { recursive: true });
  fs.copyFileSync(PAGE_SRC, path.join(tmp, 'dist', 'client', 'v4', 'index.html'));
  return tmp;
}

console.log('运行时文件（平台打包后：.py 被过滤、只部署 main.js 能追踪到的模块）');
test('build.sh 把 client/public 拷进 dist/dist/client（线上页面 = 仓库页面）；页面候选先线上布局、后开发布局', () => {
  assert.ok(read('scripts/build.sh').includes('cp -R "$ROOT_DIR/client/public/." "$DIST_DIR/dist/client/"'), 'build.sh 不再拷 client/public');
  assert.deepStrictEqual([...D.V4_PAGE_CANDIDATES], [path.join('dist', 'client', 'v4', 'index.html'), path.join('client', 'public', 'v4', 'index.html')]);
  assert.ok(fs.existsSync(PAGE_SRC));
});

test('服务端代码不再按 cwd 读 .py / json：引擎与板块清单都静态 import 内嵌模块', () => {
  const offenders = serverFiles.filter((f) => {
    const src = fs.readFileSync(f, 'utf8');
    return /process\.cwd\(\)[^;\n]*['"](?:python_analyzer\.py|core|sector_data)['"]/.test(src) || /['"]sector_data['"]/.test(src);
  }).map((f) => path.relative(APP, f));
  assert.deepStrictEqual(offenders, []);
  const svc = read('server/modules/stock-analysis/stock-analysis.service.ts');
  assert.ok(/new EngineResolver\(\)/.test(svc) && /new SectorCatalogStore\(\)/.test(svc));
  assert.ok(/from '\.\/sector-catalog\.data'/.test(read('server/modules/stock-analysis/sector-catalog.ts')));
});

test('页面托管：没有根级 @Controller（平台要求 api/ 或 openapi/ 开头）；main.ts 注册页面中间件，与 health 用同一个 v4PagePath', () => {
  const bad = [];
  for (const f of serverFiles) {
    for (const m of fs.readFileSync(f, 'utf8').matchAll(/@Controller\(\s*(?:'([^']*)'|"([^"]*)")?\s*\)/g)) {
      const p = (m[1] ?? m[2] ?? '').replace(/^\//, '');
      if (!/^(api|openapi)(\/|$)/.test(p)) bad.push(`${path.relative(APP, f)}: @Controller(${JSON.stringify(p)})`);
    }
  }
  assert.deepStrictEqual(bad, []);
  assert.ok(/app\.use\(createV4PageMiddleware\(\)\)/.test(read('server/main.ts')));
  const page = read('server/modules/stock-analysis/v4-page.ts');
  assert.ok(page.includes('v4PagePath()') && !/'client',\s*'public'/.test(page), '页面中间件又写了一份路径');
  const ctl = read('server/modules/stock-analysis/stock-analysis.controller.ts');
  assert.ok(/deploy:\s*collectDeployInfo\(process\.cwd\(\), this\.service\.getEngineStatus\(\), this\.service\.getSectorCatalogStatus\(\)\)/.test(ctl), '/api/health 的 deploy 没带引擎 / 板块清单状态');
});

test('页面路由：/、/v4、/v4/* 且 Accept 含 text/html；平台子路径前缀 /app/<appId> 先去掉；/api 与非 GET 一律放行', () => {
  const html = 'text/html,application/xhtml+xml';
  for (const u of ['/', '/?code=603068', '/v4', '/v4/', '/v4/x', '/app/app_17fr2txmwp3', '/app/app_17fr2txmwp3/', '/app/app_17fr2txmwp3/v4?x=1']) assert.ok(V.isV4PageRequest('GET', u, html, undefined), u);
  for (const u of ['/api/health', '/app/a/api/health', '/v40', '/favicon.ico', '/app/a/assets/x.js']) assert.ok(!V.isV4PageRequest('GET', u, html, undefined), u);
  assert.ok(!V.isV4PageRequest('POST', '/', html, undefined));
  assert.ok(!V.isV4PageRequest('GET', '/', 'application/json', undefined));
  assert.strictEqual(V.pageRoute('/base/v4?x', '/base/'), '/v4');
  assert.strictEqual(V.pageRoute('/base', '/base'), '/');
});

test('页面中间件：返回 v4PagePath 找到的文件（与 deploy.page_sha256 同一个）；找不到 → 404；非页面请求 next()', async () => {
  const run = (url, accept, resolvePage) => new Promise((resolve) => {
    const chunks = [];
    const { Writable } = require('stream');
    const res = new Writable({ write(c, _e, cb) { chunks.push(c); cb(); } });
    res.headers = {}; res.statusCode = 200;
    res.setHeader = (k, v) => { res.headers[k.toLowerCase()] = v; };
    res.status = (c) => { res.statusCode = c; return res; };
    res.send = (b) => resolve({ status: res.statusCode, body: String(b), headers: res.headers });
    res.on('finish', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks), headers: res.headers }));
    V.createV4PageMiddleware(resolvePage)({ method: 'GET', url, headers: { accept } }, res, () => resolve('next'));
  });
  const tmp = prodLayout();
  try {
    const r = await run('/app/app_x/', 'text/html', () => D.v4PagePath(tmp));
    assert.strictEqual(r.status, 200);
    assert.strictEqual(crypto.createHash('sha256').update(r.body).digest('hex'), pageSha);
    assert.strictEqual(r.headers['content-type'], 'text/html; charset=utf-8');
    const miss = await run('/', 'text/html', () => null);
    assert.deepStrictEqual([miss.status, miss.body], [404, 'V4 page not found']);
    assert.strictEqual(await run('/api/health', 'text/html', () => D.v4PagePath(tmp)), 'next');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

console.log('/api/health deploy');
test('线上布局（cwd=dist 产物）：page_path=dist/client/v4/index.html，page_sha256 = 指纹脚本；files_found 的键 = 指纹脚本的 runtime_files', () => {
  const tmp = prodLayout();
  try {
    const info = D.collectDeployInfo(tmp, ENGINE_OK, CATALOG_OK);
    assert.strictEqual(info.page_path, 'dist/client/v4/index.html');
    assert.strictEqual(info.page_sha256, pageSha);
    assert.strictEqual(info.page_sha256, fp.page_sha256, 'health 与指纹脚本对同一页面算出的值不同');
    assert.strictEqual(info.page_bytes, fs.statSync(PAGE_SRC).size);
    assert.deepStrictEqual(info.files_found, { page: true, engine: true, sector_catalog: true });
    assert.deepStrictEqual(Object.keys(info.files_found), fp.runtime_files);
    assert.deepStrictEqual(info.engine, { source: 'bundle', bundle_sha256: fp.engine_bundle_sha256, file_count: 36, error: null });
    assert.strictEqual(info.sector_catalog_sha256, fp.sector_catalog_sha256);
    assert.deepStrictEqual([info.cwd, info.node_version], [tmp, process.version]);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('开发布局（cwd=项目根）用 client/public/v4/index.html；两种布局都在时用线上布局', () => {
  assert.strictEqual(D.collectDeployInfo(APP, ENGINE_OK, CATALOG_OK).page_path, 'client/public/v4/index.html');
  const tmp = prodLayout();
  try {
    fs.mkdirSync(path.join(tmp, 'client', 'public', 'v4'), { recursive: true });
    fs.writeFileSync(path.join(tmp, 'client', 'public', 'v4', 'index.html'), 'dev');
    assert.strictEqual(D.collectDeployInfo(tmp, ENGINE_OK, CATALOG_OK).page_path, 'dist/client/v4/index.html');
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('页面缺失 / 引擎不可用 / 板块清单不可用：如实报告 false 与原因，不报 sha256', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'deploy-info-'));
  try {
    const info = D.collectDeployInfo(tmp, { ...ENGINE_OK, ready: false, dir: null, error: '引擎包解压失败' }, { ready: false, sha256: null, error: 'x' });
    assert.deepStrictEqual([info.page_path, info.page_sha256, info.page_bytes], [null, null, null]);
    assert.deepStrictEqual(info.files_found, { page: false, engine: false, sector_catalog: false });
    assert.strictEqual(info.engine.error, '引擎包解压失败');
    assert.strictEqual(info.sector_catalog_sha256, null);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('deploy 字段只含路径 / 版本 / 哈希，不带环境变量或连接串', () => {
  const before = process.env.SUDA_DATABASE_URL;
  process.env.SUDA_DATABASE_URL = 'postgres://u:SeCrEt@h:5432/d';
  try {
    const info = D.collectDeployInfo(APP, ENGINE_OK, CATALOG_OK);
    assert.deepStrictEqual(Object.keys(info).sort(), ['cwd', 'engine', 'files_found', 'node_version', 'page_bytes', 'page_path', 'page_sha256', 'sector_catalog_sha256']);
    assert.ok(!JSON.stringify(info).includes('SeCrEt'));
  } finally {
    if (before === undefined) delete process.env.SUDA_DATABASE_URL; else process.env.SUDA_DATABASE_URL = before;
  }
});

Promise.all(pending).then(() => {
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
});
