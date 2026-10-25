/**
 * FIR-41 第 5 项：CI 检查“仓库 = 线上”（依赖、启动方式、运行时文件、页面指纹）。
 *
 * 妙搭的 @lark-apaas/* 私有包在 CI 装不了，所以这里不做完整构建，而是静态校验仓库里的平台快照自洽：
 * 1. package.json 的每个依赖都在 package-lock.json 里，且锁定版本满足声明的范围
 * 2. 服务端运行时导入的每个包：是 dependencies 里的直接依赖，或在 runtime-deps.json 登记且能沿 lock 找到
 * 3. 启动链：run.sh → node server/main.js；build.sh 把 run.sh 拷进 dist；nest sourceRoot=server；prune-smart 入口一致
 * 4. 服务按 cwd 找的运行时文件（页面 / python_analyzer.py / core）在仓库里都在，且代码里只有一处路径定义
 * 5. /api/health 的 deploy 字段：页面 sha256 = scripts/v6_fingerprint.py 的 page_sha256；不泄露环境变量
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
function test(name, fn) {
  try { fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 4).join('\n      ') : e)); }
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

// deploy-info.ts（转译后直接加载）
const SRC = path.join(APP, 'server', 'modules', 'stock-analysis', 'deploy-info.ts');
const outJs = ts.transpileModule(fs.readFileSync(SRC, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true }, reportDiagnostics: true,
});
assert.strictEqual((outJs.diagnostics || []).length, 0);
const mod = { exports: {} };
vm.runInThisContext(`(function (module, exports, require) {${outJs.outputText}\n})`)(mod, mod.exports, require);
const D = mod.exports;

console.log('运行时文件');
test('服务按 cwd 找的页面 / python_analyzer.py / core 在仓库里都在', () => {
  for (const [k, rel] of Object.entries(D.RUNTIME_FILES)) assert.ok(fs.existsSync(path.join(APP, rel)), `${k}: ${rel} 不存在`);
});

test('路径只定义一处：页面控制器用 v4PagePath()，health 带 deploy；执行器与 core 路径与 RUNTIME_FILES 一致', () => {
  const page = read('server/modules/stock-analysis/v4-page.controller.ts');
  assert.ok(page.includes('v4PagePath()') && !/'client',\s*'public'/.test(page), '页面控制器又写了一份路径');
  const ctl = read('server/modules/stock-analysis/stock-analysis.controller.ts');
  assert.ok(/deploy:\s*collectDeployInfo\(\)/.test(ctl), '/api/health 没有 deploy 字段');
  const svc = read('server/modules/stock-analysis/stock-analysis.service.ts');
  assert.ok(svc.includes(`path.join(process.cwd(), '${D.RUNTIME_FILES.analyzer}')`), '执行器路径与 RUNTIME_FILES 不一致');
  assert.ok(svc.includes("path.join(process.cwd(), 'core')") && D.RUNTIME_FILES.core === path.join('core', '__init__.py'), 'core 路径与 RUNTIME_FILES 不一致');
});

console.log('页面指纹（/api/health deploy）');
test('deploy.page_sha256 = 文件 sha256 = scripts/v6_fingerprint.py 的 page_sha256；文件都找得到；运行时文件键与指纹脚本一致', () => {
  const info = D.collectDeployInfo(APP);
  const buf = fs.readFileSync(path.join(APP, 'client', 'public', 'v4', 'index.html'));
  assert.strictEqual(info.page_sha256, crypto.createHash('sha256').update(buf).digest('hex'));
  assert.strictEqual(info.page_bytes, buf.length);
  assert.deepStrictEqual(info.files_found, { page: true, analyzer: true, core: true, sector_catalog: true });
  assert.strictEqual(info.cwd, APP);
  assert.strictEqual(info.node_version, process.version);
  const fp = JSON.parse(execFileSync('python3', [path.join(ROOT, 'scripts', 'v6_fingerprint.py')], { encoding: 'utf8' }));
  assert.strictEqual(info.page_sha256, fp.page_sha256, 'health 与指纹脚本对同一页面算出的值不同');
  assert.deepStrictEqual(Object.keys(D.RUNTIME_FILES), fp.runtime_files, 'deploy-info 的运行时文件与指纹脚本列出的不一致');
  const sc = read('server/modules/stock-analysis/sector-catalog.ts');
  assert.ok(/SECTOR_CATALOG_REL = path\.join\('sector_data', 'catalog\.json'\)/.test(sc) && D.RUNTIME_FILES.sector_catalog === path.join('sector_data', 'catalog.json'), '板块清单路径与 sector-catalog.ts 不一致');
});

test('cwd 下缺文件：page_sha256=null，files_found 如实报告', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'deploy-info-'));
  try {
    fs.writeFileSync(path.join(tmp, 'python_analyzer.py'), '');
    const info = D.collectDeployInfo(tmp);
    assert.deepStrictEqual([info.page_sha256, info.page_bytes], [null, null]);
    assert.deepStrictEqual(info.files_found, { page: false, analyzer: true, core: false, sector_catalog: false });
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('deploy 字段只含路径 / 版本 / 哈希，不带环境变量或连接串', () => {
  const before = process.env.SUDA_DATABASE_URL;
  process.env.SUDA_DATABASE_URL = 'postgres://u:SeCrEt@h:5432/d';
  try {
    const info = D.collectDeployInfo(APP);
    assert.deepStrictEqual(Object.keys(info).sort(), ['cwd', 'files_found', 'node_version', 'page_bytes', 'page_sha256']);
    assert.ok(!JSON.stringify(info).includes('SeCrEt'));
  } finally {
    if (before === undefined) delete process.env.SUDA_DATABASE_URL; else process.env.SUDA_DATABASE_URL = before;
  }
});

console.log(`\n${passed} passed, ${failed} failed`);
process.exit(failed ? 1 : 0);
