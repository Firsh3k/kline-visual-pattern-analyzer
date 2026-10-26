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
  const FILES = E.decodeBundle(E.EMBEDDED_ENGINE_BUNDLE);
  const SHA = B.ENGINE_BUNDLE_SHA256;
  const GEN_RE = new RegExp(`^${SHA}\\.[0-9a-z]+-[0-9]+-[0-9a-f]{8}$`);
  /** 父目录里的代号目录（不含指针、临时文件） */
  const gens = (root) => fs.readdirSync(root).filter((n) => GEN_RE.test(n)).sort();
  const pointer = (root) => path.join(root, fs.readlinkSync(path.join(root, `${SHA}.current`)));
  const pyHealth = (loc) => JSON.parse(execFileSync(PY, [loc.script, '--mode', 'health'], { cwd: loc.workdir, env: E.engineEnv(process.env), encoding: 'utf8' }));
  // 编译后的两个模块写到临时目录，子进程直接 require（与线上一样是普通 CommonJS 模块）
  const modDir = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-mod-'));
  tmpRoots.push(modDir);
  fs.writeFileSync(path.join(modDir, 'engine-bundle.js'), compileSource('engine-bundle.ts'));
  fs.writeFileSync(path.join(modDir, 'engine.js'), compileSource('engine.ts'));
  const ENGINE_JS = JSON.stringify(path.join(modDir, 'engine.js'));
  const childRun = (script) => new Promise((resolve) => {
    const p = spawn(process.execPath, ['-e', script]);
    let out = '', err = '';
    p.stdout.on('data', (d) => { out += d; });
    p.stderr.on('data', (d) => { err += d; });
    p.on('close', (code) => resolve({ code, out, err }));
  });
  const sleepSync = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);

  await test('在没有任何 .py 的 cwd 下：解压到 <root>/<sha256>.<代号>/ 并由指针指向它，Python health = real_core，core / 执行器指纹与指纹脚本一致', () => {
    const root = freshRoot();
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-cwd-'));
    tmpRoots.push(cwd);
    const r = new E.EngineResolver({ cwd, root, env: {} });
    const loc = r.resolve();
    assert.strictEqual(loc.source, 'bundle');
    assert.ok(GEN_RE.test(path.basename(loc.workdir)) && path.dirname(loc.workdir) === root, loc.workdir);
    assert.strictEqual(pointer(root), loc.workdir);
    assert.strictEqual(loc.bundle_sha256, SHA);
    assert.strictEqual(fs.statSync(root).mode & 0o777, 0o700);
    const out = pyHealth(loc);
    assert.strictEqual(out.analyzer, 'real_core');
    assert.strictEqual(out.core_sha256, fp.core_sha256);
    assert.strictEqual(out.analyzer_sha256, fp.analyzer_sha256);
    // PYTHONDONTWRITEBYTECODE：跑过之后目录仍与包完全一致（没有 __pycache__）
    assert.strictEqual(E.verifyEngineDir(loc.workdir, FILES), null);
    const st = r.status();
    assert.deepStrictEqual([st.ready, st.source, st.bundle_sha256, st.file_count, st.error], [true, 'bundle', SHA, B.ENGINE_BUNDLE_FILE_COUNT, null]);
  });

  await test('同一 resolver 再次调用：目录完好 → 同一目录、不重写；目录被清掉（/tmp 清理）→ 自动重建', () => {
    const root = freshRoot();
    const r = new E.EngineResolver({ root, env: {} });
    const a = r.resolve();
    const mtime = fs.statSync(a.script).mtimeMs;
    assert.strictEqual(r.resolve().workdir, a.workdir);
    assert.strictEqual(fs.statSync(a.script).mtimeMs, mtime);
    fs.rmSync(a.workdir, { recursive: true, force: true });
    const b = r.resolve();
    assert.strictEqual(E.verifyEngineDir(b.workdir, FILES), null);
    assert.strictEqual(pointer(root), b.workdir);
  });

  await test('QA R81-1：同一 resolver 第二次调用前目录被改（改内容 / 多出同名模块 json.py / 缺模块 / __pycache__ / 换成符号链接）→ 不会用被动过的目录：重建并返回校验通过的新目录', () => {
    const root = freshRoot();
    const r = new E.EngineResolver({ root, env: {} });
    let loc = r.resolve();
    const tamper = [
      (d) => fs.appendFileSync(path.join(d, 'core', 'analysis.py'), '\n# x'),
      (d) => fs.writeFileSync(path.join(d, 'json.py'), 'raise SystemExit("QA_UNVERIFIED_MODULE_EXECUTED")'),
      (d) => fs.unlinkSync(path.join(d, 'core', 'signals.py')),
      (d) => { fs.mkdirSync(path.join(d, 'core', '__pycache__')); fs.writeFileSync(path.join(d, 'core', '__pycache__', 'x.pyc'), 'x'); },
      (d) => { fs.unlinkSync(path.join(d, 'python_analyzer.py')); fs.symlinkSync('/etc/hostname', path.join(d, 'python_analyzer.py')); },
    ];
    for (const t of tamper) {
      const before = loc.workdir;
      t(before);
      assert.notStrictEqual(E.verifyEngineDir(before, FILES), null);
      loc = r.resolve();                      // 与线上一样：同一个长期存在的 resolver
      assert.notStrictEqual(loc.workdir, before);
      assert.strictEqual(E.verifyEngineDir(loc.workdir, FILES), null);
      assert.strictEqual(r.status().ready, true);
      assert.ok(!fs.existsSync(before), '被改过的目录应删除');
      const out = pyHealth(loc);               // json.py 不会被执行
      assert.strictEqual(out.analyzer, 'real_core');
      assert.strictEqual(out.core_sha256, fp.core_sha256);
    }
    // 只留下一个有效目录和指针，临时目录都清理了
    assert.deepStrictEqual(gens(root), [path.basename(loc.workdir)]);
    assert.deepStrictEqual(fs.readdirSync(root).filter((n) => n.startsWith('.tmp-')), []);
  });

  await test('QA R81-2（确定性交错）：A 判定目录损坏后暂停 → 子进程 B 修好、发布并启动 Python → A 继续：A 不会挪走 / 删除 B 发布的有效目录，B 的消费者正常运行', () => {
    const root = freshRoot();
    const first = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root);
    fs.writeFileSync(path.join(first, 'json.py'), 'raise SystemExit(1)');   // 已有坏目录
    let bPath = null;
    const aPath = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root, {
      afterVerifyFailed: (dir, reason) => {
        assert.strictEqual(dir, first);
        assert.ok(/多出文件/.test(reason), reason);
        // A 暂停在这里：真实子进程 B 完成修复并发布，然后按返回的路径启动 Python（消费者）
        const outB = execFileSync(process.execPath, ['-e', `
          const { execFileSync } = require('child_process');
          const E = require(${ENGINE_JS});
          const dir = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, ${JSON.stringify(root)});
          process.stdout.write(dir);`], { encoding: 'utf8' });
        bPath = outB;
        assert.notStrictEqual(bPath, first);
        assert.strictEqual(E.verifyEngineDir(bPath, FILES), null);
      },
    });
    // A 恢复后：看到 B 已发布的有效目录，直接用它，不另建、不移动
    assert.strictEqual(aPath, bPath);
    assert.strictEqual(pointer(root), bPath);
    assert.strictEqual(E.verifyEngineDir(bPath, FILES), null);
    assert.ok(!fs.existsSync(first));
    const out = pyHealth({ script: path.join(bPath, 'python_analyzer.py'), workdir: bPath });
    assert.strictEqual(out.analyzer, 'real_core');
    assert.deepStrictEqual(gens(root), [path.basename(bPath)]);
  });

  await test('QA R81-2（真实并发）：坏目录上 B 暂停在“判定损坏之后”，A 修好并启动 Python；B 继续 → 用 A 的目录；A 的消费者全程不受影响', async () => {
    const root = freshRoot();
    const first = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root);
    fs.appendFileSync(path.join(first, 'core', 'signals.py'), '\n# tampered');
    const marker = path.join(path.dirname(root), 'b-paused');
    const go = path.join(path.dirname(root), 'b-go');
    const pB = childRun(`
      const fs = require('fs');
      const E = require(${ENGINE_JS});
      const sleep = (ms) => Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
      const dir = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, ${JSON.stringify(root)}, {
        afterVerifyFailed: () => { fs.writeFileSync(${JSON.stringify(marker)}, '1'); for (let i = 0; i < 400 && !fs.existsSync(${JSON.stringify(go)}); i++) sleep(25); },
      });
      process.stdout.write(dir);`);
    for (let i = 0; i < 400 && !fs.existsSync(marker); i++) sleepSync(25);
    assert.ok(fs.existsSync(marker), 'B 没有走到修复分支');
    const aPath = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root);       // A 修复并发布
    const consumer = spawn(PY, [path.join(aPath, 'python_analyzer.py'), '--mode', 'health'], { cwd: aPath, env: E.engineEnv(process.env) });
    let cOut = '';
    consumer.stdout.on('data', (d) => { cOut += d; });
    const cDone = new Promise((r) => consumer.on('close', r));
    fs.writeFileSync(go, '1');                                               // B 继续
    const b = await pB;
    assert.strictEqual(b.code, 0, b.err);
    assert.strictEqual(b.out, aPath, 'B 应直接用 A 已发布的有效目录');
    assert.strictEqual(await cDone, 0);
    assert.strictEqual(JSON.parse(cOut).analyzer, 'real_core');
    assert.strictEqual(E.verifyEngineDir(aPath, FILES), null);
    assert.deepStrictEqual(gens(root), [path.basename(aPath)]);
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

  await test('指针被改成指向父目录以外 / 别的名字 → 不跟随，重新发布', () => {
    const root = freshRoot();
    const outside = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, freshRoot());   // 一个内容有效但不在本父目录的目录
    E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root);
    fs.unlinkSync(path.join(root, `${SHA}.current`));
    fs.symlinkSync(outside, path.join(root, `${SHA}.current`));
    const dir = E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, root);
    assert.strictEqual(path.dirname(dir), root);
    assert.strictEqual(pointer(root), dir);
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
  await test('冷启动 4 个进程同时解压：都成功，各自返回的目录都存在且校验通过，指针指向其中之一', async () => {
    const root = freshRoot();
    const script = `const E = require(${ENGINE_JS});
      process.stdout.write(E.materializeBundle(E.EMBEDDED_ENGINE_BUNDLE, ${JSON.stringify(root)}));`;
    const runs = await Promise.all([0, 1, 2, 3].map(() => childRun(script)));
    for (const r of runs) assert.strictEqual(r.code, 0, r.err);
    for (const r of runs) assert.strictEqual(E.verifyEngineDir(r.out, FILES), null, r.out);
    assert.ok(runs.some((r) => r.out === pointer(root)));
    assert.ok(gens(root).length >= 1 && gens(root).length <= 4);
    assert.deepStrictEqual(fs.readdirSync(root).filter((n) => n.startsWith('.tmp-') || n.startsWith('.ptr-')), []);
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
