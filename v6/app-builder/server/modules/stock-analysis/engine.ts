/**
 * Python 引擎定位（FIR-41 平台打包问题）。
 *
 * 妙搭部署过滤所有 .py，且服务端只部署 main.js 沿 import 能追踪到的文件，所以按 process.cwd() 找
 * python_analyzer.py / core/ 在线上必然失败（2026-10-26 线上诊断：/opt/bytefaas 下 .py 为 0）。
 * 引擎因此以 engine-bundle.ts 常量随模块部署（scripts/build_runtime_bundles.py 生成，CI 校验与仓库一致），
 * 运行时在这里解压到临时目录后再 spawn python：
 *
 * - 父目录 <tmp>/kline-engine-<uid>/ 必须是本用户的真实目录、权限 0700。
 * - 每个解压好的目录 <bundle_sha256>.<代号>/ 发布后就不再改动、不再移动：先写到临时目录、逐个文件校验，
 *   再 rename 成唯一的代号目录，最后原子替换指针 <bundle_sha256>.current（符号链接）。所以任何进程拿到的
 *   有效目录都不会被别的进程挪走或删除（QA R81-2）；并发修复最多多出一个有效目录，不需要锁。
 * - 每次调用 Python 前都完整校验当前目录：文件集合必须与包完全一致（多一个文件也不行，防止同名模块被
 *   抢先放进去）、每个文件 sha256 一致（QA R81-1：不再只看入口文件是否存在）。不一致 → 删掉这个已损坏的
 *   目录，重新解压发布新代号。校验与 spawn 之间仍有极短窗口，目录只有本用户可写。
 * - spawn 时设 PYTHONDONTWRITEBYTECODE=1，目录里不会长出 __pycache__。
 * - 失败一律抛 EngineUnavailableError（个股分析报错、health 显示 not_configured），绝不退回 JS 计算。
 * - 本地开发要直接跑工作区里的 .py：设 KLINE_ENGINE_SOURCE=files（只认 cwd 下的文件，找不到就报错，不会悄悄改用包）。
 *
 * 无 Nest 依赖，tests/v6/test_engine_bundle.js 直接测试。
 */
import { createHash, randomBytes } from 'crypto';
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import * as zlib from 'zlib';
import {
  ENGINE_BUNDLE_FILE_COUNT, ENGINE_BUNDLE_FORMAT, ENGINE_BUNDLE_GZIP_BASE64, ENGINE_BUNDLE_SHA256,
} from './engine-bundle';

export interface EngineBundle {
  format: string;
  sha256: string;
  fileCount: number;
  gzipBase64: string;
}

export const EMBEDDED_ENGINE_BUNDLE: EngineBundle = {
  format: ENGINE_BUNDLE_FORMAT,
  sha256: ENGINE_BUNDLE_SHA256,
  fileCount: ENGINE_BUNDLE_FILE_COUNT,
  gzipBase64: ENGINE_BUNDLE_GZIP_BASE64,
};

export type EngineSource = 'bundle' | 'files';

export interface EngineLocation {
  source: EngineSource;
  script: string;
  workdir: string;
  bundle_sha256: string | null;
}

export interface EngineStatus {
  ready: boolean;
  source: EngineSource;
  bundle_sha256: string | null;
  file_count: number | null;
  dir: string | null;
  error: string | null;
}

export class EngineUnavailableError extends Error {}

/** 与 scripts/build_runtime_bundles.py 的 ALLOWED_PATH 相同：只允许 python_analyzer.py 与 core/ 下的 .py */
const ALLOWED_PATH = /^(?:python_analyzer\.py|core\/(?:[A-Za-z0-9_]+\/)*[A-Za-z0-9_]+\.py)$/;

export interface BundleFile {
  path: string;
  sha256: string;
  data: Buffer;
}

const sha256 = (buf: Buffer | string) => createHash('sha256').update(buf).digest('hex');

/** 解码并校验整个包（包 sha256、格式、文件数、每个文件的路径与 sha256） */
export function decodeBundle(bundle: EngineBundle): BundleFile[] {
  let raw: Buffer;
  try {
    raw = zlib.gunzipSync(Buffer.from(bundle.gzipBase64, 'base64'));
  } catch (err) {
    throw new EngineUnavailableError(`引擎包解压失败：${err instanceof Error ? err.message : String(err)}`);
  }
  if (sha256(raw) !== bundle.sha256) throw new EngineUnavailableError('引擎包内容与 ENGINE_BUNDLE_SHA256 不符');
  let doc: { format?: unknown; files?: unknown };
  try {
    doc = JSON.parse(raw.toString('utf8'));
  } catch {
    throw new EngineUnavailableError('引擎包不是合法 JSON');
  }
  if (doc.format !== bundle.format || doc.format !== 'kline-engine-bundle-1') throw new EngineUnavailableError(`引擎包格式不对：${String(doc.format)}`);
  if (!Array.isArray(doc.files)) throw new EngineUnavailableError('引擎包缺少文件列表');
  const seen = new Set<string>();
  const out: BundleFile[] = [];
  for (const f of doc.files as Array<Record<string, unknown>>) {
    const p = f && typeof f.path === 'string' ? f.path : '';
    if (!ALLOWED_PATH.test(p) || seen.has(p)) throw new EngineUnavailableError(`引擎包里有不允许的路径：${JSON.stringify(p)}`);
    seen.add(p);
    const data = Buffer.from(String(f.data ?? ''), 'base64');
    if (sha256(data) !== f.sha256) throw new EngineUnavailableError(`引擎包文件校验失败：${p}`);
    out.push({ path: p, sha256: String(f.sha256), data });
  }
  if (out.length !== bundle.fileCount) throw new EngineUnavailableError(`引擎包文件数 ${out.length} ≠ ${bundle.fileCount}`);
  if (!seen.has('python_analyzer.py') || !seen.has('core/__init__.py')) throw new EngineUnavailableError('引擎包缺少 python_analyzer.py 或 core/__init__.py');
  return out;
}

/** 目录里的全部条目（相对路径）；遇到符号链接或非普通文件直接判不一致 */
function listTree(dir: string, base = ''): string[] | null {
  const out: string[] = [];
  for (const ent of fs.readdirSync(path.join(dir, base), { withFileTypes: true })) {
    const rel = base ? `${base}/${ent.name}` : ent.name;
    if (ent.isDirectory()) {
      const sub = listTree(dir, rel);
      if (!sub) return null;
      out.push(...sub);
    } else if (ent.isFile()) {
      out.push(rel);
    } else {
      return null;
    }
  }
  return out;
}

/** 目录与包完全一致：文件集合相同（不多不少）、每个文件 sha256 相同 */
export function verifyEngineDir(dir: string, files: BundleFile[]): string | null {
  try {
    const st = fs.lstatSync(dir);
    if (!st.isDirectory()) return '不是目录';
    const tree = listTree(dir);
    if (!tree) return '含符号链接或特殊文件';
    const want = new Map(files.map((f) => [f.path, f.sha256]));
    const extra = tree.filter((p) => !want.has(p));
    if (extra.length) return `多出文件：${extra.slice(0, 3).join(', ')}`;
    for (const [p, h] of want) {
      const full = path.join(dir, p);
      if (!fs.existsSync(full)) return `缺少文件：${p}`;
      if (sha256(fs.readFileSync(full)) !== h) return `文件内容不符：${p}`;
    }
    return null;
  } catch (err) {
    return err instanceof Error ? err.message : String(err);
  }
}

/** 本用户专用的父目录：不存在就以 0700 创建；存在则必须是本用户的真实目录且权限 0700 */
function ensurePrivateRoot(root: string): void {
  try {
    fs.mkdirSync(root, { recursive: false, mode: 0o700 });
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'EEXIST') throw err;
  }
  const st = fs.lstatSync(root);
  if (st.isSymbolicLink() || !st.isDirectory()) throw new EngineUnavailableError(`引擎目录 ${root} 不是普通目录`);
  const uid = typeof process.getuid === 'function' ? process.getuid() : null;
  if (uid !== null && st.uid !== uid) throw new EngineUnavailableError(`引擎目录 ${root} 不属于当前用户`);
  if ((st.mode & 0o077) !== 0) throw new EngineUnavailableError(`引擎目录 ${root} 权限过宽（${(st.mode & 0o777).toString(8)}），应为 700`);
}

export function defaultEngineRoot(): string {
  const uid = typeof process.getuid === 'function' ? process.getuid() : 'u';
  return path.join(os.tmpdir(), `kline-engine-${uid}`);
}

const GEN_RE = /^[0-9a-f]{64}\.[0-9a-z]+-[0-9]+-[0-9a-f]{8}$/;

function pointerPath(root: string, sha: string): string {
  return path.join(root, `${sha}.current`);
}

/** 指针当前指向的代号目录（只接受同一父目录下、本包 sha 的代号名）；没有 / 不合法返回 null */
function readPointer(root: string, sha: string): string | null {
  try {
    const target = fs.readlinkSync(pointerPath(root, sha));
    if (!GEN_RE.test(target) || !target.startsWith(`${sha}.`)) return null;
    return path.join(root, target);
  } catch {
    return null;
  }
}

/** 测试用的插入点：在“发现目录损坏之后、开始修复之前”暂停，构造与别的进程交错的场景 */
export interface MaterializeHooks {
  afterVerifyFailed?: (dir: string | null, reason: string) => void;
}

/**
 * 返回一个已完整校验、内容与包完全一致的引擎目录。
 * 当前目录有效 → 直接返回；无效（缺失、被改、多文件）→ 解压成新的代号目录并替换指针。
 * 已发布的有效目录不会被任何进程移动或删除；只删除自己刚校验为损坏的目录。
 */
export function materializeBundle(bundle: EngineBundle, root: string = defaultEngineRoot(), hooks: MaterializeHooks = {}): string {
  const files = decodeBundle(bundle);
  ensurePrivateRoot(root);
  const current = readPointer(root, bundle.sha256);
  const bad = current ? verifyEngineDir(current, files) : '尚未解压';
  if (current && bad === null) return current;
  if (hooks.afterVerifyFailed) hooks.afterVerifyFailed(current, bad || '');

  // 别的进程可能已经修好并换了指针：再看一次，指针变了且新目录有效就直接用
  const latest = readPointer(root, bundle.sha256);
  if (latest && latest !== current && verifyEngineDir(latest, files) === null) return latest;

  const tag = `${Date.now().toString(36)}-${process.pid}-${randomBytes(4).toString('hex')}`;
  const tmp = path.join(root, `.tmp-${bundle.sha256.slice(0, 12)}-${tag}`);
  const gen = path.join(root, `${bundle.sha256}.${tag}`);
  fs.mkdirSync(tmp, { mode: 0o700 });
  try {
    for (const f of files) {
      const full = path.join(tmp, f.path);
      fs.mkdirSync(path.dirname(full), { recursive: true, mode: 0o700 });
      fs.writeFileSync(full, f.data, { mode: 0o600, flag: 'wx' });
    }
    const badTmp = verifyEngineDir(tmp, files);
    if (badTmp) throw new EngineUnavailableError(`解压后校验失败：${badTmp}`);
    fs.renameSync(tmp, gen);   // 代号唯一，不会与别人的目录冲突
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  // 原子替换指针：先建临时符号链接再 rename 覆盖
  const link = path.join(root, `.ptr-${tag}`);
  fs.symlinkSync(path.basename(gen), link);
  fs.renameSync(link, pointerPath(root, bundle.sha256));
  // 只删除自己校验过、确认已损坏的旧目录（有效目录从不删除）
  if (current && current !== gen && verifyEngineDir(current, files) !== null) {
    fs.rmSync(current, { recursive: true, force: true });
  }
  const badGen = verifyEngineDir(gen, files);
  if (badGen) throw new EngineUnavailableError(`引擎目录校验失败：${badGen}`);
  return gen;
}

export interface EngineResolverOptions {
  bundle?: EngineBundle;
  env?: Record<string, string | undefined>;
  cwd?: string;
  root?: string;
}

/** 每次调用 Python 前取引擎位置；包模式每次都完整校验目录（QA R81-1），损坏或被清掉会自动重建 */
export class EngineResolver {
  private readonly bundle: EngineBundle;
  private readonly source: EngineSource;
  private readonly cwd: string;
  private readonly root: string;
  private cached: EngineLocation | null = null;
  private lastError: string | null = null;

  constructor(opts: EngineResolverOptions = {}) {
    this.bundle = opts.bundle ?? EMBEDDED_ENGINE_BUNDLE;
    const env = opts.env ?? process.env;
    this.source = env.KLINE_ENGINE_SOURCE === 'files' ? 'files' : 'bundle';
    this.cwd = opts.cwd ?? process.cwd();
    this.root = opts.root ?? defaultEngineRoot();
  }

  resolve(): EngineLocation {
    try {
      const loc = this.source === 'files' ? this.fromFiles() : this.fromBundle();
      this.lastError = null;
      return loc;
    } catch (err) {
      this.cached = null;
      this.lastError = err instanceof Error ? err.message : String(err);
      throw err instanceof EngineUnavailableError ? err : new EngineUnavailableError(`Python 引擎不可用：${this.lastError}`);
    }
  }

  status(): EngineStatus {
    let ready = false;
    try {
      this.resolve();
      ready = true;
    } catch {
      // lastError 已记录
    }
    return {
      ready,
      source: this.source,
      bundle_sha256: this.source === 'bundle' ? this.bundle.sha256 : null,
      file_count: this.source === 'bundle' ? this.bundle.fileCount : null,
      dir: this.cached ? this.cached.workdir : null,
      error: this.lastError,
    };
  }

  private fromFiles(): EngineLocation {
    const script = path.join(this.cwd, 'python_analyzer.py');
    if (!fs.existsSync(script) || !fs.existsSync(path.join(this.cwd, 'core', '__init__.py'))) {
      throw new EngineUnavailableError(`KLINE_ENGINE_SOURCE=files，但 ${this.cwd} 下没有 python_analyzer.py 或 core/`);
    }
    this.cached = { source: 'files', script, workdir: this.cwd, bundle_sha256: null };
    return this.cached;
  }

  private fromBundle(): EngineLocation {
    // 不走“入口文件还在就直接用”的捷径：每次都完整校验文件集合与内容（约 36 个文件、几百 KB，毫秒级）
    const dir = materializeBundle(this.bundle, this.root);
    this.cached = { source: 'bundle', script: path.join(dir, 'python_analyzer.py'), workdir: dir, bundle_sha256: this.bundle.sha256 };
    return this.cached;
  }
}

/** spawn Python 用的环境：不写字节码（引擎目录保持与包完全一致） */
export function engineEnv(base: Record<string, string | undefined> = process.env): Record<string, string | undefined> {
  return { ...base, PYTHONDONTWRITEBYTECODE: '1' };
}
