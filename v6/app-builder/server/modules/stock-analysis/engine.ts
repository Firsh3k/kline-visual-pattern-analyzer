/**
 * Python 引擎定位（FIR-41 平台打包问题）。
 *
 * 妙搭部署过滤所有 .py，且服务端只部署 main.js 沿 import 能追踪到的文件，所以按 process.cwd() 找
 * python_analyzer.py / core/ 在线上必然失败（2026-10-26 线上诊断：/opt/bytefaas 下 .py 为 0）。
 * 引擎因此以 engine-bundle.ts 常量随模块部署（scripts/build_runtime_bundles.py 生成，CI 校验与仓库一致），
 * 运行时在这里解压到临时目录后再 spawn python：
 *
 * - 目录 <tmp>/kline-engine-<uid>/<bundle_sha256>/：先写到同级临时目录、逐个文件校验 sha256，
 *   再整体 rename；多个进程同时解压时只有一个 rename 成功，其余校验已有目录后直接用。
 * - 已有目录也要校验：文件集合必须与包完全一致（多一个文件也不行，防止同名模块被抢先放进去）、
 *   每个文件 sha256 一致；不一致的目录移走重建。父目录必须是本用户的、权限 0700、不是符号链接。
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

/** 把包解压成 <root>/<bundle_sha256>/，返回目录；已有且一致就直接用 */
export function materializeBundle(bundle: EngineBundle, root: string = defaultEngineRoot()): string {
  const files = decodeBundle(bundle);
  ensurePrivateRoot(root);
  const final = path.join(root, bundle.sha256);
  if (fs.existsSync(final)) {
    const bad = verifyEngineDir(final, files);
    if (!bad) return final;
    // 被改过或不完整：移走后重建（rename 是原子的，不会删到别人正在用的新目录）
    const aside = path.join(root, `.bad-${bundle.sha256.slice(0, 12)}-${process.pid}-${randomBytes(4).toString('hex')}`);
    try {
      fs.renameSync(final, aside);
      fs.rmSync(aside, { recursive: true, force: true });
    } catch {
      // 别的进程先处理了；下面重建后再校验
    }
  }
  const tmp = path.join(root, `.tmp-${bundle.sha256.slice(0, 12)}-${process.pid}-${randomBytes(4).toString('hex')}`);
  fs.mkdirSync(tmp, { mode: 0o700 });
  try {
    for (const f of files) {
      const full = path.join(tmp, f.path);
      fs.mkdirSync(path.dirname(full), { recursive: true, mode: 0o700 });
      fs.writeFileSync(full, f.data, { mode: 0o600, flag: 'wx' });
    }
    const bad = verifyEngineDir(tmp, files);
    if (bad) throw new EngineUnavailableError(`解压后校验失败：${bad}`);
    try {
      fs.renameSync(tmp, final);
    } catch (err) {
      const code = (err as NodeJS.ErrnoException).code;
      if (code !== 'EEXIST' && code !== 'ENOTEMPTY') throw err;
      // 并发：别的进程已经放好了，校验它
    }
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
  const bad = verifyEngineDir(final, files);
  if (bad) throw new EngineUnavailableError(`引擎目录校验失败：${bad}`);
  return final;
}

export interface EngineResolverOptions {
  bundle?: EngineBundle;
  env?: Record<string, string | undefined>;
  cwd?: string;
  root?: string;
}

/** 每次调用 Python 前取引擎位置；包模式解压一次后缓存，目录被清掉（如 /tmp 清理）会自动重建 */
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
    const c = this.cached;
    if (c && fs.existsSync(c.script) && fs.existsSync(path.join(c.workdir, 'core', '__init__.py'))) return c;
    const dir = materializeBundle(this.bundle, this.root);
    this.cached = { source: 'bundle', script: path.join(dir, 'python_analyzer.py'), workdir: dir, bundle_sha256: this.bundle.sha256 };
    return this.cached;
  }
}

/** spawn Python 用的环境：不写字节码（引擎目录保持与包完全一致） */
export function engineEnv(base: Record<string, string | undefined> = process.env): Record<string, string | undefined> {
  return { ...base, PYTHONDONTWRITEBYTECODE: '1' };
}
