/**
 * FIR-41 平台打包问题的回归测试：真实构建 → 按妙搭的方式裁剪部署 → 真实启动服务 → 核对 /api/health。
 *
 * 2026-10-26 线上诊断：妙搭部署过滤所有 .py；服务端只部署从 dist/server/main.js 沿 require 能追踪到的文件
 * （scripts/prune-smart.js 用的 @vercel/nft 追踪），按 cwd 读的 python_analyzer.py / core / sector_data 都上不了线，
 * 线上 core_analyzer=not_configured、根路径 “V4 page not found”。这里照这个规则模拟：
 *
 * 1. 在临时副本里 NODE_ENV=production nest build（= npm run build:server），再做 build.sh 第 5 步的产物准备
 *    （client/public → dist/dist/client，run.sh → dist/）；
 * 2. 用 @vercel/nft 从 dist/server/main.js 追踪（参数同 prune-smart.js），只把追踪到的 server/ 文件放进部署目录，
 *    再放 dist/client 与 node_modules；部署目录里不允许出现任何 .py 和 sector_data/；
 * 3. 在部署目录（cwd，= 线上 /opt/bytefaas）里按 run.sh 启动 node server/main.js；
 * 4. /api/health：core_analyzer=real_core，core / 执行器指纹、页面、引擎包、板块清单 sha256 与该版本一致，
 *    用 scripts/v6_verify_deploy.py 的 verify() 判定（只放过自选股用内存实现这一项）；
 *    根路径（含平台子路径前缀）返回的页面 = 仓库页面；/api/sectors 可用。
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
const nftPath = process.env.NFT_PATH ? path.join(process.env.NFT_PATH, '@vercel', 'nft') : '@vercel/nft';
const { nodeFileTrace } = require(nftPath);

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
let server = null;
let serverLog = '';

(async () => {
  const fp = JSON.parse(execFileSync(PY, [path.join(ROOT, 'scripts', 'v6_fingerprint.py')], { encoding: 'utf8' }));
  let traced = [];
  let port = 0;

  console.log('构建与按平台规则部署');
  await test('真实 nest build（swc）+ build.sh 第 5 步产物准备', () => {
    copyTree(APP, build, (rel) => !rel.startsWith('dist/') && !rel.includes('__pycache__'));
    fs.symlinkSync(path.join(APP, 'node_modules'), path.join(build, 'node_modules'));
    execFileSync(path.join(APP, 'node_modules', '.bin', 'nest'), ['build'], { cwd: build, env: { ...process.env, NODE_ENV: 'production' }, stdio: 'pipe' });
    fs.mkdirSync(path.join(build, 'dist', 'dist', 'client'), { recursive: true });
    copyTree(path.join(build, 'client', 'public'), path.join(build, 'dist', 'dist', 'client'));
    fs.copyFileSync(path.join(build, 'scripts', 'run.sh'), path.join(build, 'dist', 'run.sh'));
    assert.ok(fs.existsSync(path.join(build, 'dist', 'server', 'main.js')));
  });

  await test('@vercel/nft 从 dist/server/main.js 追踪（同 prune-smart.js 参数）：引擎包、板块清单、页面中间件都在模块图里', async () => {
    const { fileList } = await nodeFileTrace([path.join(build, 'dist', 'server', 'main.js')], {
      base: build, processCwd: build, ts: false, conditions: ['node', 'production'], exportsOnly: true,
      analysis: { emitGlobs: false, computeFileReferences: false, evaluatePureExpressions: false },
    });
    traced = [...fileList].filter((f) => f.startsWith('dist/server/')).sort();
    for (const f of ['engine-bundle.js', 'engine.js', 'sector-catalog.data.js', 'v4-page.js', 'deploy-info.js']) {
      assert.ok(traced.includes(`dist/server/modules/stock-analysis/${f}`), `${f} 不在模块图里（线上不会部署）`);
    }
    // 对照：dist/server 里有、但没被 import 的文件不会部署（这次事故里孤立的 engine-bundle.js 就是这样丢的）
    const orphan = path.join(build, 'dist', 'server', 'orphan-not-imported.js');
    fs.writeFileSync(orphan, 'module.exports = 1;');
    const again = await nodeFileTrace([path.join(build, 'dist', 'server', 'main.js')], { base: build, processCwd: build, ts: false });
    assert.ok(![...again.fileList].includes('dist/server/orphan-not-imported.js'));
  });

  await test('部署目录 = 追踪到的 server/ + dist/client + run.sh + node_modules；没有任何 .py、没有 sector_data/', () => {
    for (const f of traced) {
      const rel = f.slice('dist/'.length);
      fs.mkdirSync(path.dirname(path.join(deploy, rel)), { recursive: true });
      fs.copyFileSync(path.join(build, f), path.join(deploy, rel));
    }
    copyTree(path.join(build, 'dist', 'dist'), path.join(deploy, 'dist'));
    fs.copyFileSync(path.join(build, 'dist', 'run.sh'), path.join(deploy, 'run.sh'));
    fs.copyFileSync(path.join(APP, 'package.json'), path.join(deploy, 'package.json'));
    fs.symlinkSync(path.join(APP, 'node_modules'), path.join(deploy, 'node_modules'));
    const files = walk(deploy);
    assert.deepStrictEqual(files.filter((f) => f.endsWith('.py')), []);
    assert.ok(!files.some((f) => f.startsWith('sector_data/') || f.startsWith('core/')));
    assert.ok(files.includes('dist/client/v4/index.html'));
    assert.ok(/^\s*NODE_ENV=production node server\/main\.js\s*$/m.test(fs.readFileSync(path.join(deploy, 'run.sh'), 'utf8')));
  });

  console.log('启动（cwd = 部署目录，同线上 /opt/bytefaas）');
  await test('node server/main.js 启动成功', async () => {
    port = await freePort();
    const tmpdir = path.join(work, 'tmp');
    fs.mkdirSync(tmpdir);
    server = spawn(process.execPath, ['server/main.js'], {
      cwd: deploy,
      env: {
        PATH: process.env.PATH, HOME: process.env.HOME, TMPDIR: tmpdir,
        NODE_ENV: 'production', SERVER_HOST: '127.0.0.1', SERVER_PORT: String(port),
        // 平台运行时依赖（鉴权域名、数据服务）在 CI 里没有：给一个不可达地址、关掉 datapaas；自选股 / 自定义板块用内存实现
        FORCE_AUTHN_INNERAPI_DOMAIN: 'http://127.0.0.1:9', FORCE_FRAMEWORK_DISABLE_DATAPASS: 'true', WATCHLIST_STORE: 'memory',
      },
    });
    server.stdout.on('data', (d) => { serverLog += d; });
    server.stderr.on('data', (d) => { serverLog += d; });
    const t0 = Date.now();
    while (!/Server running on/.test(serverLog)) {
      if (server.exitCode !== null) throw new Error('服务退出：\n' + serverLog.slice(-2000));
      if (Date.now() - t0 > 90000) throw new Error('启动超时：\n' + serverLog.slice(-2000));
      await new Promise((r) => setTimeout(r, 200));
    }
  });

  let health = null;
  await test('/api/health：core_analyzer=real_core，引擎来自内嵌包并解压到 TMPDIR，core / 执行器 / 页面 / 引擎包 / 板块清单 sha256 都与该版本一致', async () => {
    const r = await get(port, '/api/health');
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
    assert.ok(fs.existsSync(path.join(work, 'tmp', `kline-engine-${process.getuid()}`, fp.engine_bundle_sha256, 'python_analyzer.py')));
  });

  await test('scripts/v6_verify_deploy.py 判定：除自选股（CI 用内存实现，WARN）外全部 PASS', () => {
    const out = execFileSync(PY, ['-c', `
import importlib.util, json, sys
def load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s); s.loader.exec_module(m); return m
FP = load('fp', ${JSON.stringify(path.join(ROOT, 'scripts', 'v6_fingerprint.py'))})
VD = load('vd', ${JSON.stringify(path.join(ROOT, 'scripts', 'v6_verify_deploy.py'))})
rows = VD.verify(json.load(sys.stdin), FP.fingerprint(None), allow_watchlist_down=True)
print(json.dumps({n: r for n, r, _ in rows}))`], { input: JSON.stringify(health), encoding: 'utf8' });
    const res = JSON.parse(out);
    assert.strictEqual(res.watchlist_store, 'WARN');
    delete res.watchlist_store;
    assert.deepStrictEqual(Object.entries(res).filter(([, v]) => v !== 'PASS'), [], out);
    for (const k of ['core_sha256', 'analyzer_sha256', 'page_sha256', 'runtime_files', 'engine_bundle', 'sector_catalog_sha256']) assert.strictEqual(res[k], 'PASS', k);
  });

  await test('根路径返回 V4 页面（含平台子路径前缀 /app/<appId>/）：内容 = 仓库页面；/api/sectors 可用（内嵌清单）', async () => {
    for (const url of ['/', '/v4', '/app/app_17fr2txmwp3/']) {
      const r = await get(port, url, { accept: 'text/html' });
      assert.strictEqual(r.status, 200, `${url}: ${r.status} ${r.body.toString().slice(0, 80)}`);
      assert.strictEqual(sha(r.body), fp.page_sha256, url);
    }
    const s = await get(port, '/api/sectors');
    assert.strictEqual(s.status, 200, s.body.toString());
    const body = JSON.parse(s.body.toString());
    assert.strictEqual(body.sectors.length, JSON.parse(fs.readFileSync(path.join(APP, 'sector_data', 'catalog.json'), 'utf8')).sectors.length);
  });

  if (server && server.exitCode === null) server.kill('SIGKILL');
  if (failed) console.log('\n服务日志（末尾）：\n' + serverLog.slice(-3000));
  fs.rmSync(work, { recursive: true, force: true });
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
