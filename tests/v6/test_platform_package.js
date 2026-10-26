/**
 * FIR-41 平台打包问题的回归测试：用标准构建脚本构建 → 按妙搭的方式部署 → 只用部署产物启动服务 → 核对 /api/health。
 *
 * 2026-10-26 线上诊断：妙搭部署过滤所有 .py；服务端只部署从 dist/server/main.js 沿 require 能追踪到的文件
 * （scripts/prune-smart.js 用的 @vercel/nft 追踪），按 cwd 读的 python_analyzer.py / core / sector_data 都上不了线，
 * 线上 core_analyzer=not_configured、根路径 “V4 page not found”。
 *
 * 验证范围（QA R81-3：区分“nest 编译”与“完整标准构建”）：
 * 1. 构建：原样执行仓库里的 scripts/build.sh（平台生成、不改动）。其中 4 步是平台专用、CI 无法执行的，
 *    用 PATH 前置的 npm / npx 替身跳过并记录：fullstack-cli action-plugin init、gen:openapi、
 *    generate-api-routes / generate-page-routes、vite 前端构建（client/src 是平台模板，不在仓库；本应用页面是
 *    client/public/v4/index.html 静态页，由 build.sh 第 5 步拷贝）。其余步骤都是真实执行：清理 dist、
 *    nest build（swc）、第 5 步产物准备（client/public → dist/dist/client、run.sh → dist）、
 *    第 6 步 prune-smart.js 依赖裁剪（生成 dist/node_modules 与 dist/package.json）。
 * 2. 部署：服务端只放 nft 从 dist/server/main.js 追踪到的文件（参数同 prune-smart.js），再放 build.sh 产出的
 *    dist/dist/client、run.sh、裁剪后的 node_modules 与 package.json（复制，不链接回源码依赖树）；
 *    部署目录及其上级都不能有别的 node_modules，启动时不设 NODE_PATH；部署目录里不允许出现 .py 和 sector_data/。
 * 3. 启动：在部署目录（= 线上 /opt/bytefaas）里执行 run.sh。
 * 4. 核对：/api/health real_core、各项指纹与该版本一致（scripts/v6_verify_deploy.py 的 verify()，只放过自选股
 *    用内存实现这一项）；根路径（含平台子路径前缀）返回仓库页面；/api/sectors 可用。
 * 5. 负例：裁剪产物缺一个运行时依赖 → 服务起不来；部署产物缺页面 → health 报 page=false、核对不通过、根路径 404。
 *
 * 运行：先在 v6/app-builder 里 npm ci，再 NFT_PATH=<@vercel/nft 所在 node_modules> node tests/v6/test_platform_package.js
 */
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const http = require('http');
const net = require('net');
const os = require('os');
const path = require('path');
const { execFileSync, spawn } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');
const APP = path.join(ROOT, 'v6', 'app-builder');
const PY = process.env.PYTHON || 'python3';
if (!process.env.NFT_PATH) throw new Error('需要 NFT_PATH：@vercel/nft 所在的 node_modules（平台构建环境提供，不在 lock 里）');
const NFT_PATH = path.resolve(process.env.NFT_PATH);
const { nodeFileTrace } = require(path.join(NFT_PATH, '@vercel', 'nft'));

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 6).join('\n      ') : e)); }
}
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
function walk(dir, base = '', out = []) {
  for (const ent of fs.readdirSync(path.join(dir, base), { withFileTypes: true })) {
    const rel = base ? `${base}/${ent.name}` : ent.name;
    if (ent.name === 'node_modules') continue;
    if (ent.isDirectory()) walk(dir, rel, out); else out.push(rel);
  }
  return out;
}
function copyTree(src, dst, filter = () => true) {
  for (const rel of walk(src)) {
    if (!filter(rel)) continue;
    fs.mkdirSync(path.dirname(path.join(dst, rel)), { recursive: true });
    fs.copyFileSync(path.join(src, rel), path.join(dst, rel));
  }
}
function which(bin) {
  return execFileSync('bash', ['-c', `command -v ${bin}`], { encoding: 'utf8' }).trim();
}
function freePort() {
  return new Promise((resolve) => { const s = net.createServer().listen(0, '127.0.0.1', () => { const p = s.address().port; s.close(() => resolve(p)); }); });
}
function get(port, url, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.get({ host: '127.0.0.1', port, path: url, headers: { cookie: 'suda-csrf-token=ci', 'x-suda-csrf-token': 'ci', ...headers } }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, body: Buffer.concat(chunks) }));
    });
    req.on('error', reject);
    req.setTimeout(30000, () => req.destroy(new Error(`${url} 超时`)));
  });
}

const work = fs.mkdtempSync(path.join(os.tmpdir(), 'platform-pkg-'));
const build = path.join(work, 'build');
const deploy = path.join(work, 'deploy');
const running = [];

/** 在部署目录里按 run.sh 启动；返回 { port, proc, log() }，起不来时 started=false */
async function boot(dir, { waitMs = 90000 } = {}) {
  const port = await freePort();
  const tmpdir = fs.mkdtempSync(path.join(work, 'tmp-'));
  let log = '';
  const proc = spawn('bash', ['run.sh'], {
    cwd: dir,
    env: {
      PATH: process.env.PATH, HOME: process.env.HOME, TMPDIR: tmpdir,   // 不传 NODE_PATH：只能用部署目录里的依赖
      SERVER_HOST: '127.0.0.1', SERVER_PORT: String(port),
      // 平台运行时依赖（鉴权域名、数据服务）在 CI 里没有：给一个不可达地址、关掉 datapaas；自选股 / 自定义板块用内存实现
      FORCE_AUTHN_INNERAPI_DOMAIN: 'http://127.0.0.1:9', FORCE_FRAMEWORK_DISABLE_DATAPASS: 'true', WATCHLIST_STORE: 'memory',
    },
  });
  running.push(proc);
  proc.stdout.on('data', (d) => { log += d; });
  proc.stderr.on('data', (d) => { log += d; });
  const t0 = Date.now();
  while (!/Server running on/.test(log)) {
    if (proc.exitCode !== null) return { started: false, port, proc, tmpdir, log: () => log };
    if (Date.now() - t0 > waitMs) return { started: false, timeout: true, port, proc, tmpdir, log: () => log };
    await new Promise((r) => setTimeout(r, 200));
  }
  return { started: true, port, proc, tmpdir, log: () => log };
}
function stop(s) {
  if (s && s.proc.exitCode === null) s.proc.kill('SIGKILL');
}
function verifyHealth(health) {
  const out = execFileSync(PY, ['-c', `
import importlib.util, json, sys
def load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
FP = load('fp', ${JSON.stringify(path.join(ROOT, 'scripts', 'v6_fingerprint.py'))})
VD = load('vd', ${JSON.stringify(path.join(ROOT, 'scripts', 'v6_verify_deploy.py'))})
rows = VD.verify(json.load(sys.stdin), FP.fingerprint(None), allow_watchlist_down=True)
print(json.dumps({n: r for n, r, _ in rows}))`], { input: JSON.stringify(health), encoding: 'utf8' });
  return JSON.parse(out);
}

(async () => {
  const fp = JSON.parse(execFileSync(PY, [path.join(ROOT, 'scripts', 'v6_fingerprint.py')], { encoding: 'utf8' }));
  let traced = [];
  let skipped = [];
  let main = null;

  console.log('标准构建（scripts/build.sh 原样执行，平台专用的 4 步用替身跳过）');
  await test('build.sh 成功：nest build、产物准备、prune-smart 依赖裁剪都是真实执行；跳过的只有平台专用步骤', () => {
    copyTree(APP, build, (rel) => !rel.startsWith('dist/') && !rel.includes('__pycache__'));
    // 构建环境的完整依赖（= 平台 npm ci 之后）。必须是真实目录：prune-smart 按 nft 追踪到的路径从这里挑包，
    // 用符号链接时 nft 会解析到构建目录以外，裁剪结果就不对了。硬链接复制，跨分区时退回普通复制
    const srcModules = fs.realpathSync(path.join(APP, 'node_modules'));
    try { execFileSync('cp', ['-al', srcModules + '/.', path.join(build, 'node_modules')]); }
    catch { execFileSync('cp', ['-a', srcModules + '/.', path.join(build, 'node_modules')]); }
    const shim = path.join(work, 'shim');
    const skipLog = path.join(work, 'skipped.log');
    fs.mkdirSync(shim);
    const realNpm = which('npm'), realNpx = which('npx');
    fs.writeFileSync(path.join(shim, 'npx'), `#!/usr/bin/env bash
case "$1" in
  fullstack-cli|generate-api-routes|generate-page-routes) echo "npx $*" >> ${JSON.stringify(skipLog)}; exit 0 ;;
esac
exec ${JSON.stringify(realNpx)} "$@"
`, { mode: 0o755 });
    fs.writeFileSync(path.join(shim, 'npm'), `#!/usr/bin/env bash
if [ "$1" = run ] && [ "$2" = gen:openapi ]; then echo "npm $*" >> ${JSON.stringify(skipLog)}; exit 0; fi
if [ "$1" = run ] && [ "$2" = build:client ]; then echo "npm $*" >> ${JSON.stringify(skipLog)}; mkdir -p dist/client; exit 0; fi
exec ${JSON.stringify(realNpm)} "$@"
`, { mode: 0o755 });
    const before = fs.readFileSync(path.join(APP, 'scripts', 'build.sh'));
    try {
      execFileSync('bash', ['scripts/build.sh'], {
        cwd: build, stdio: 'pipe', encoding: 'utf8',
        env: { ...process.env, PATH: `${shim}:${process.env.PATH}`, NODE_PATH: NFT_PATH },   // NODE_PATH 只给构建期的 prune-smart 找 @vercel/nft
      });
    } catch (e) {
      throw new Error('build.sh 失败：\n' + String(e.stdout || '').slice(-3000) + String(e.stderr || '').slice(-2000));
    }
    assert.ok(fs.readFileSync(path.join(build, 'scripts', 'build.sh')).equals(before), 'build.sh 被改过');
    skipped = fs.readFileSync(skipLog, 'utf8').trim().split('\n').sort();
    assert.deepStrictEqual(skipped, [
      'npm run build:client', 'npm run gen:openapi',
      'npx fullstack-cli action-plugin init',
      'npx generate-api-routes --server-dir ./server --out-dir ./dist',
      'npx generate-page-routes --app-path ./client/src/app.tsx --out-dir ./dist',
    ].sort());
    for (const f of ['server/main.js', 'run.sh', 'package.json', 'dist/client/v4/index.html', 'node_modules/@nestjs/core/package.json']) {
      assert.ok(fs.existsSync(path.join(build, 'dist', f)), `构建产物缺 dist/${f}`);
    }
    const pruned = JSON.parse(fs.readFileSync(path.join(build, 'dist', 'package.json'), 'utf8'));
    assert.ok(!pruned.scripts && pruned.dependencies['@nestjs/core'], 'dist/package.json 不是 prune-smart 的输出');
    assert.ok(!fs.lstatSync(path.join(build, 'dist', 'node_modules')).isSymbolicLink());
  });

  await test('@vercel/nft 从 dist/server/main.js 追踪（同 prune-smart.js 参数）：引擎包、板块清单、页面中间件都在模块图里；没被 import 的文件不在', async () => {
    const opts = {
      base: build, processCwd: build, ts: false, conditions: ['node', 'production'], exportsOnly: true,
      analysis: { emitGlobs: false, computeFileReferences: false, evaluatePureExpressions: false },
    };
    const orphan = path.join(build, 'dist', 'server', 'orphan-not-imported.js');
    fs.writeFileSync(orphan, 'module.exports = 1;');   // 对照：这次事故里孤立的 engine-bundle.js 就是这样丢的
    const { fileList } = await nodeFileTrace([path.join(build, 'dist', 'server', 'main.js')], opts);
    traced = [...fileList].filter((f) => f.startsWith('dist/server/')).sort();
    for (const f of ['engine-bundle.js', 'engine.js', 'sector-catalog.data.js', 'v4-page.js', 'deploy-info.js']) {
      assert.ok(traced.includes(`dist/server/modules/stock-analysis/${f}`), `${f} 不在模块图里（线上不会部署）`);
    }
    assert.ok(!traced.includes('dist/server/orphan-not-imported.js'));
    fs.unlinkSync(orphan);
  });

  await test('部署目录 = 追踪到的 server/ + build.sh 产出的 dist/client、run.sh + 裁剪后的 node_modules / package.json；不链接源码依赖，没有 .py / sector_data/', () => {
    for (const f of traced) {
      const rel = f.slice('dist/'.length);
      fs.mkdirSync(path.dirname(path.join(deploy, rel)), { recursive: true });
      fs.copyFileSync(path.join(build, f), path.join(deploy, rel));
    }
    copyTree(path.join(build, 'dist', 'dist'), path.join(deploy, 'dist'));
    for (const f of ['run.sh', 'package.json']) fs.copyFileSync(path.join(build, 'dist', f), path.join(deploy, f));
    fs.cpSync(path.join(build, 'dist', 'node_modules'), path.join(deploy, 'node_modules'), { recursive: true, verbatimSymlinks: true });
    assert.ok(!fs.lstatSync(path.join(deploy, 'node_modules')).isSymbolicLink());
    for (let d = path.dirname(deploy); ; d = path.dirname(d)) {          // 上级目录不能有 node_modules（否则 Node 会往上找到）
      assert.ok(!fs.existsSync(path.join(d, 'node_modules')), `${d} 下有 node_modules`);
      if (d === path.dirname(d)) break;
    }
    const files = walk(deploy);
    assert.deepStrictEqual(files.filter((f) => f.endsWith('.py')), []);
    assert.ok(!files.some((f) => f.startsWith('sector_data/') || f.startsWith('core/')));
    assert.ok(files.includes('dist/client/v4/index.html'));
    assert.ok(/^\s*NODE_ENV=production node server\/main\.js\s*$/m.test(fs.readFileSync(path.join(deploy, 'run.sh'), 'utf8')));
  });

  console.log('只用部署产物启动（cwd = 部署目录，同线上 /opt/bytefaas）');
  await test('bash run.sh 启动成功', async () => {
    main = await boot(deploy);
    assert.ok(main.started, '服务没起来：\n' + main.log().slice(-2500));
  });

  let health = null;
  await test('/api/health：core_analyzer=real_core，引擎来自内嵌包并解压到 TMPDIR，core / 执行器 / 页面 / 引擎包 / 板块清单 sha256 都与该版本一致', async () => {
    const r = await get(main.port, '/api/health');
    assert.strictEqual(r.status, 200, r.body.toString());
    health = JSON.parse(r.body.toString());
    assert.strictEqual(health.core_analyzer, 'real_core', JSON.stringify(health));
    assert.strictEqual(health.core_installed, true);
    assert.strictEqual(health.core_sha256, fp.core_sha256);
    assert.strictEqual(health.analyzer_sha256, fp.analyzer_sha256);
    const d = health.deploy;
    assert.strictEqual(d.cwd, deploy);
    assert.strictEqual(d.page_path, 'dist/client/v4/index.html');
    assert.strictEqual(d.page_sha256, fp.page_sha256);
    assert.deepStrictEqual(d.files_found, { page: true, engine: true, sector_catalog: true });
    assert.deepStrictEqual([d.engine.source, d.engine.bundle_sha256, d.engine.error], ['bundle', fp.engine_bundle_sha256, null]);
    assert.strictEqual(d.sector_catalog_sha256, fp.sector_catalog_sha256);
    const root = path.join(main.tmpdir, `kline-engine-${process.getuid()}`);
    const gen = fs.readlinkSync(path.join(root, `${fp.engine_bundle_sha256}.current`));
    assert.ok(fs.existsSync(path.join(root, gen, 'python_analyzer.py')));
  });

  await test('scripts/v6_verify_deploy.py 判定：除自选股（CI 用内存实现，WARN）外全部 PASS', () => {
    const res = verifyHealth(health);
    assert.strictEqual(res.watchlist_store, 'WARN');
    delete res.watchlist_store;
    assert.deepStrictEqual(Object.entries(res).filter(([, v]) => v !== 'PASS'), [], JSON.stringify(res));
    for (const k of ['core_sha256', 'analyzer_sha256', 'page_sha256', 'runtime_files', 'engine_bundle', 'sector_catalog_sha256']) assert.strictEqual(res[k], 'PASS', k);
  });

  await test('根路径返回 V4 页面（含平台子路径前缀 /app/<appId>/）：内容 = 仓库页面；/api/sectors 可用（内嵌清单）', async () => {
    for (const url of ['/', '/v4', '/app/app_17fr2txmwp3/']) {
      const r = await get(main.port, url, { accept: 'text/html' });
      assert.strictEqual(r.status, 200, `${url}: ${r.status} ${r.body.toString().slice(0, 80)}`);
      assert.strictEqual(sha(r.body), fp.page_sha256, url);
    }
    const s = await get(main.port, '/api/sectors');
    assert.strictEqual(s.status, 200, s.body.toString());
    const body = JSON.parse(s.body.toString());
    assert.strictEqual(body.sectors.length, JSON.parse(fs.readFileSync(path.join(APP, 'sector_data', 'catalog.json'), 'utf8')).sectors.length);
  });
  stop(main);

  console.log('负例：部署产物缺东西就必须失败');
  await test('裁剪后的 node_modules 缺一个运行时依赖（hbs）→ 服务起不来，日志指明缺的模块', async () => {
    const pkg = path.join(deploy, 'node_modules', 'hbs');
    assert.ok(fs.existsSync(pkg), '裁剪产物里本应有 hbs（main.ts 直接 import）');
    fs.renameSync(pkg, pkg + '.removed');
    try {
      const s = await boot(deploy, { waitMs: 60000 });
      stop(s);
      assert.ok(!s.started, '缺依赖时服务不应启动成功');
      assert.ok(/Cannot find module 'hbs'/.test(s.log()), s.log().slice(-1500));
    } finally {
      fs.renameSync(pkg + '.removed', pkg);
    }
  });

  await test('部署产物缺页面 → health 报 files_found.page=false、page_sha256=null，核对 page_sha256 / runtime_files 判 FAIL，根路径 404', async () => {
    const page = path.join(deploy, 'dist', 'client', 'v4', 'index.html');
    fs.renameSync(page, page + '.removed');
    let s = null;
    try {
      s = await boot(deploy);
      assert.ok(s.started, s.log().slice(-1500));
      const h = JSON.parse((await get(s.port, '/api/health')).body.toString());
      assert.strictEqual(h.deploy.files_found.page, false);
      assert.strictEqual(h.deploy.page_sha256, null);
      const res = verifyHealth(h);
      assert.strictEqual(res.page_sha256, 'FAIL');
      assert.strictEqual(res.runtime_files, 'FAIL');
      const r = await get(s.port, '/', { accept: 'text/html' });
      assert.deepStrictEqual([r.status, r.body.toString()], [404, 'V4 page not found']);
    } finally {
      stop(s);
      fs.renameSync(page + '.removed', page);
    }
  });

  for (const p of running) if (p.exitCode === null) p.kill('SIGKILL');
  if (failed && main) console.log('\n服务日志（末尾）：\n' + main.log().slice(-3000));
  fs.rmSync(work, { recursive: true, force: true });
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
