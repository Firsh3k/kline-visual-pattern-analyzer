/**
 * FIR-41 平台打包问题：Python 引擎随模块内嵌（engine-bundle.ts）+ 运行时解压（engine.ts）。
 *
 * - 内嵌包与仓库里的 core/**\/*.py、python_analyzer.py 逐字节一致（另有 Python 侧 --check）
 * - 在没有任何 .py 的工作目录里解压后，Python health 报 real_core，core / 执行器指纹与 scripts/v6_fingerprint.py 一致
 * - 已有目录被改过、多出文件、不完整 → 移走重建；父目录不是本用户 0700 的真实目录 → 拒绝
 * - 包本身被改过（sha256 / 路径穿越 / 文件数）→ 拒绝，绝不退回别的引擎
 * - 多个进程同时解压 → 都拿到同一个校验通过的目录
 * - KLINE_ENGINE_SOURCE=files 只认 cwd 下的文件，找不到就报错
 *
 * 运行：node tests/v6/test_engine_bundle.js（需要 typescript 包、python3）
 */
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const zlib = require('zlib');
const { execFileSync, spawn } = require('child_process');
const ts = require('typescript');

const ROOT = path.resolve(__dirname, '..', '..');
const APP = path.join(ROOT, 'v6', 'app-builder');
const DIR = path.join(APP, 'server', 'modules', 'stock-analysis');
const PY = process.env.PYTHON || 'python3';

function compileSource(file, requireMap) {
  const out = ts.transpileModule(fs.readFileSync(path.join(DIR, file), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, strict: true },
    reportDiagnostics: true,
  });
  assert.strictEqual((out.diagnostics || []).length, 0, `${file} TS 诊断非空`);
  return out.outputText;
}
function load(file, requireMap) {
  const mod = { exports: {} };
  const req = (name) => (name in requireMap ? requireMap[name] : require(name));
  vm.runInThisContext(`(function (module, exports, require) {${compileSource(file)}\n})`)(mod, mod.exports, req);
  return mod.exports;
}
const B = load('engine-bundle.ts', {});
const E = load('engine.ts', { './engine-bundle': B });

let passed = 0, failed = 0;
async function test(name, fn) {
  try { await fn(); passed++; console.log('  ✓ ' + name); }
  catch (e) { failed++; console.log('  ✗ ' + name + '\n      ' + (e && e.stack ? e.stack.split('\n').slice(0, 5).join('\n      ') : e)); }
}
const sha = (b) => crypto.createHash('sha256').update(b).digest('hex');
const tmpRoots = [];
function freshRoot() {
  const base = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-test-'));
  tmpRoots.push(base);
  return path.join(base, 'kline-engine');
}
function walk(dir, base = '') {
  const out = [];
  for (const ent of fs.readdirSync(path.join(dir, base), { withFileTypes: true })) {
    const rel = base ? `${base}/${ent.name}` : ent.name;
    if (ent.isDirectory()) { if (ent.name !== '__pycache__') out.push(...walk(dir, rel)); }
    else out.push(rel);
  }
  return out;
}
/** 仓库里应进包的文件（与 scripts/build_runtime_bundles.py 一致） */
function repoEngineFiles() {
  const files = ['python_analyzer.py', ...walk(path.join(APP, 'core')).filter((p) => p.endsWith('.py')).map((p) => `core/${p}`)];
  return files.sort();
}
/** 改写包内容后重新打包（用于构造被篡改的包） */
function repack(mutate, { keepSha = false } = {}) {
  const raw = zlib.gunzipSync(Buffer.from(B.ENGINE_BUNDLE_GZIP_BASE64, 'base64'));
  const doc = JSON.parse(raw.toString('utf8'));
  mutate(doc);
  const newRaw = Buffer.from(JSON.stringify(doc));
  return {
    format: B.ENGINE_BUNDLE_FORMAT,
    sha256: keepSha ? B.ENGINE_BUNDLE_SHA256 : sha(newRaw),
    fileCount: doc.files.length,
    gzipBase64: zlib.gzipSync(newRaw).toString('base64'),
  };
}
const fp = JSON.parse(execFileSync(PY, [path.join(ROOT, 'scripts', 'v6_fingerprint.py')], { encoding: 'utf8' }));

(async () => {
  console.log('内嵌包 = 仓库源码');
  await test('包里正好是 core/**/*.py + python_analyzer.py，逐字节一致；ENGINE_BUNDLE_SHA256 = 指纹脚本按源码算出的值', () => {
    const files = E.decodeBundle(E.EMBEDDED_ENGINE_BUNDLE);
    assert.deepStrictEqual(files.map((f) => f.path).sort(), repoEngineFiles());
    for (const f of files) assert.ok(f.data.equals(fs.readFileSync(path.join(APP, f.path))), `${f.path} 与仓库不一致`);
    assert.strictEqual(B.ENGINE_BUNDLE_FILE_COUNT, files.length);
    assert.strictEqual(B.ENGINE_BUNDLE_SHA256, fp.engine_bundle_sha256);
  });

  console.log('解压与校验');
  await test('在没有任何 .py 的 cwd 下：解压到 <root>/<sha256>/，Python health = real_core，core / 执行器指纹与指纹脚本一致', () => {
    const root = freshRoot();
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-cwd-'));
    tmpRoots.push(cwd);
    const r = new E.EngineResolver({ cwd, root, env: {} });
    const loc = r.resolve();
    assert.strictEqual(loc.source, 'bundle');
    assert.strictEqual(loc.workdir, path.join(root, B.ENGINE_BUNDLE_SHA256));
    assert.strictEqual(loc.bundle_sha256, B.ENGINE_BUNDLE_SHA256);
    assert.strictEqual(fs.statSync(root).mode & 0o777, 0o700);
    const out = JSON.parse(execFileSync(PY, [loc.script, '--mode', 'health'], { cwd: loc.workdir, env: E.engineEnv(process.env), encoding: 'utf8' }));
    assert.strictEqual(out.analyzer, 'real_core');
    assert.strictEqual(out.core_sha256, fp.core_sha256);
    assert.strictEqual(out.analyzer_sha256, fp.analyzer_sha256);
    // PYTHONDONTWRITEBYTECODE：跑过之后目录仍与包完全一致（没有 __pycache__）
    assert.strictEqual(E.verifyEngineDir(loc.workdir, E.decodeBundle(E.EMBEDDED_ENGINE_BUNDLE)), null);
    const st = r.status();
    assert.deepStrictEqual([st.ready, st.source, st.bundle_sha256, st.file_count, st.error], [true, 'bundle', B.ENGINE_BUNDLE_SHA256, B.ENGINE_BUNDLE_FILE_COUNT, null]);
  });

  await test('第二次直接用缓存；目录被清掉（/tmp 清理）后下次调用自动重建', () => {
    const root = freshRoot();
    const r = new E.EngineResolver({ root, env: {} });
    const a = r.resolve();
    const mtime = fs.statSync(a.script).mtimeMs;
    assert.strictEqual(r.resolve().script, a.script);
    assert.strictEqual(fs.statSync(a.script).mtimeMs, mtime);
    fs.rmSync(a.workdir, { recursive: true, force: true });
    const b = r.resolve();
    assert.strictEqual(b.workdir, a.workdir);
    assert.ok(fs.existsSync(b.script));
  });

  await test('已有目录被改过 / 多出同名模块 / 缺文件 → 校验不过，移走重建（不会用被动过的文件）', () => {
    const root = freshRoot();
    const dir = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root);
    const files = E.decodeBundle(E.EMBEDDED_ENGINE_BUNDLE);
    const tamper = [
      () => fs.appendFileSync(path.join(dir, 'core', 'analysis.py'), '\n# x'),
      () => fs.writeFileSync(path.join(dir, 'json.py'), 'raise SystemExit("hijack")'),
      () => fs.unlinkSync(path.join(dir, 'core', 'signals.py')),
      () => { fs.mkdirSync(path.join(dir, 'core', '__pycache__')); fs.writeFileSync(path.join(dir, 'core', '__pycache__', 'x.pyc'), 'x'); },
      () => { fs.unlinkSync(path.join(dir, 'python_analyzer.py')); fs.symlinkSync('/etc/hostname', path.join(dir, 'python_analyzer.py')); },
    ];
    for (const t of tamper) {
      t();
      assert.notStrictEqual(E.verifyEngineDir(dir, files), null);
      assert.strictEqual(E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root), dir);
      assert.strictEqual(E.verifyEngineDir(dir, files), null);
      assert.ok(!fs.existsSync(path.join(dir, 'json.py')));
    }
    // 只留下正式目录，临时 / 移走的目录都清理了
    assert.deepStrictEqual(fs.readdirSync(root), [B.ENGINE_BUNDLE_SHA256]);
  });

  await test('父目录是符号链接 / 别人可写（权限不是 700）→ 拒绝，不往里解压', () => {
    const base = path.dirname(freshRoot());
    const real = path.join(base, 'real');
    fs.mkdirSync(real, { mode: 0o700 });
    const link = path.join(base, 'link');
    fs.symlinkSync(real, link);
    assert.throws(() => E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, link), (e) => e instanceof E.EngineUnavailableError && /不是普通目录/.test(e.message));
    const wide = path.join(base, 'wide');
    fs.mkdirSync(wide);
    fs.chmodSync(wide, 0o777);
    assert.throws(() => E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, wide), (e) => e instanceof E.EngineUnavailableError && /权限过宽/.test(e.message));
    assert.deepStrictEqual(fs.readdirSync(real), []);
    assert.deepStrictEqual(fs.readdirSync(wide), []);
  });

  console.log('包本身被改过 → 拒绝');
  await test('内容与 ENGINE_BUNDLE_SHA256 不符 / 单个文件 sha256 不符 / 路径穿越 / 绝对路径 / 非 .py / 重复路径 / 文件数不符 / 缺 core → 一律拒绝', () => {
    const cases = {
      'sha256 不符': repack((d) => { d.files[0].data = Buffer.from('x').toString('base64'); }, { keepSha: true }),
      '文件 sha256 不符': repack((d) => { d.files[0].data = Buffer.from('x').toString('base64'); }),
      '路径穿越': repack((d) => { d.files[0].path = 'core/../../evil.py'; }),
      '绝对路径': repack((d) => { d.files[0].path = '/tmp/evil.py'; }),
      '非 .py': repack((d) => { d.files[0].path = 'core/run.sh'; }),
      '重复路径': repack((d) => { d.files.push({ ...d.files[0] }); }),
      '缺 core/__init__.py': repack((d) => { d.files = d.files.filter((f) => f.path !== 'core/__init__.py'); }),
      '格式不对': repack((d) => { d.format = 'other'; }),
    };
    for (const [label, bundle] of Object.entries(cases)) {
      const root = freshRoot();
      assert.throws(() => E.materializeBundle(bundle, root), (e) => e instanceof E.EngineUnavailableError, label);
      assert.ok(!fs.existsSync(root) || fs.readdirSync(root).length === 0, `${label}：不应写出任何文件`);
    }
    const n = { ...E.EMBEDDED_ENGINE_BUNDLE, fileCount: E.EMBEDDED_ENGINE_BUNDLE.fileCount + 1 };
    assert.throws(() => E.decodeBundle(n), /文件数/);
    assert.throws(() => E.decodeBundle({ ...E.EMBEDDED_ENGINE_BUNDLE, gzipBase64: 'not-gzip' }), /解压失败/);
  });

  await test('包不可用时 resolve 抛 EngineUnavailableError，status 报 ready=false 和原因（health 据此显示 not_configured）', () => {
    const bad = repack((d) => { d.files[0].path = '../x.py'; });
    const r = new E.EngineResolver({ bundle: bad, root: freshRoot(), env: {} });
    assert.throws(() => r.resolve(), E.EngineUnavailableError);
    const st = r.status();
    assert.strictEqual(st.ready, false);
    assert.strictEqual(st.dir, null);
    assert.ok(/不允许的路径/.test(st.error), st.error);
  });

  console.log('并发与来源');
  await test('4 个进程同时解压同一个包：都成功，拿到同一个目录，目录校验通过', async () => {
    const root = freshRoot();
    // 编译后的两个模块写到临时目录，子进程直接 require（与线上一样是普通 CommonJS 模块）
    const modDir = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-mod-'));
    tmpRoots.push(modDir);
    fs.writeFileSync(path.join(modDir, 'engine-bundle.js'), compileSource('engine-bundle.ts'));
    fs.writeFileSync(path.join(modDir, 'engine.js'), compileSource('engine.ts'));
    const script = `const E = require(${JSON.stringify(path.join(modDir, 'engine.js'))});
      process.stdout.write(E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, ${JSON.stringify(root)}));`;
    const runs = await Promise.all([0, 1, 2, 3].map(() => new Promise((resolve) => {
      const p = spawn(process.execPath, ['-e', script]);
      let out = '', err = '';
      p.stdout.on('data', (d) => { out += d; });
      p.stderr.on('data', (d) => { err += d; });
      p.on('close', (code) => resolve({ code, out, err }));
    })));
    for (const r of runs) assert.strictEqual(r.code, 0, r.err);
    assert.deepStrictEqual([...new Set(runs.map((r) => r.out))], [path.join(root, B.ENGINE_BUNDLE_SHA256)]);
    assert.strictEqual(E.verifyEngineDir(runs[0].out, E.decodeBundle(E.EMBEDDED_ENGINE_BUNDLE)), null);
    assert.deepStrictEqual(fs.readdirSync(root), [B.ENGINE_BUNDLE_SHA256]);
  });

  await test('KLINE_ENGINE_SOURCE=files：只用 cwd 下的文件（本地开发）；cwd 下没有就报错，不会悄悄改用内嵌包', () => {
    const r = new E.EngineResolver({ cwd: APP, root: freshRoot(), env: { KLINE_ENGINE_SOURCE: 'files' } });
    const loc = r.resolve();
    assert.deepStrictEqual([loc.source, loc.workdir, loc.bundle_sha256], ['files', APP, null]);
    const empty = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-empty-'));
    tmpRoots.push(empty);
    const r2 = new E.EngineResolver({ cwd: empty, root: freshRoot(), env: { KLINE_ENGINE_SOURCE: 'files' } });
    assert.throws(() => r2.resolve(), (e) => e instanceof E.EngineUnavailableError && /KLINE_ENGINE_SOURCE=files/.test(e.message));
    assert.strictEqual(r2.status().source, 'files');
  });

  await test('默认（线上）一律用内嵌包，即使 cwd 下恰好有 .py 也不用（线上行为与 CI 测的一致）', () => {
    const r = new E.EngineResolver({ cwd: APP, root: freshRoot(), env: {} });
    assert.strictEqual(r.resolve().source, 'bundle');
  });

  console.log('服务接线');
  await test('service 里不再按 process.cwd() 找 python_analyzer.py / core，Python 调用都经 EngineResolver；engine-bundle 被静态 import', () => {
    const svc = fs.readFileSync(path.join(DIR, 'stock-analysis.service.ts'), 'utf8');
    assert.ok(!/python_analyzer\.py/.test(svc) && !/process\.cwd\(\)/.test(svc), 'service 又按 cwd 找文件了');
    assert.ok(/from '\.\/engine'/.test(svc) && /new EngineResolver\(\)/.test(svc));
    assert.strictEqual((svc.match(/this\.engine\.resolve\(\)/g) || []).length, 3, 'health / 个股 / 大盘三处都要经 resolve');
    assert.strictEqual((svc.match(/engineEnv\(/g) || []).length, 3, '三处 spawn 都要用 engineEnv');
    const eng = fs.readFileSync(path.join(DIR, 'engine.ts'), 'utf8');
    assert.ok(/^import \{[^}]*ENGINE_BUNDLE_GZIP_BASE64[^}]*\} from '\.\/engine-bundle';/m.test(eng), 'engine-bundle 必须静态 import（才会进模块图被部署）');
  });

  for (const d of tmpRoots) fs.rmSync(d, { recursive: true, force: true });
  console.log(`\n${passed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
})();
