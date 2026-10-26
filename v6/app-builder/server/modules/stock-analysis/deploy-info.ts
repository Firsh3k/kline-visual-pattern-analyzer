/**
 * FIR-41 第 5 项：线上“实际在跑的是什么”自报，供 /api/health 与 scripts/v6_verify_deploy.py 比对。
 *
 * 2026-10-26 平台打包问题之后（妙搭过滤 .py、服务端只部署 main.js 沿 import 能追踪到的文件）：
 * - 页面：按 V4_PAGE_CANDIDATES 顺序找第一个存在的文件。线上 cwd=/opt/bytefaas（= 构建产物 dist/），
 *   build.sh 把 client/public 拷到 dist/dist/client，所以线上是 dist/client/v4/index.html；
 *   本地开发（cwd=项目根）是 client/public/v4/index.html。两份内容相同，sha256 与仓库文件一致。
 * - Python 引擎、板块清单随模块内嵌（engine-bundle.ts、sector-catalog.data.ts），不再按 cwd 找文件；
 *   这里报告它们是否可用和内容 sha256，由调用方传入（见 stock-analysis.service.ts）。
 * - 页面 sha256 每次现算（约 200KB，开销可忽略），反映线上实际返回给浏览器的那个文件。
 * - 只报路径、版本和哈希，不含环境变量或连接串。
 * - 无 Nest 依赖，tests/v6/test_repo_equals_prod.js 直接测试。
 */
import { createHash } from 'crypto';
import * as fs from 'fs';
import * as path from 'path';
import type { EngineStatus } from './engine';
import type { SectorCatalogStatus } from './sector-catalog';

/** 相对 process.cwd() 的页面位置，先线上布局、后本地开发布局；页面中间件与 health 共用 */
export const V4_PAGE_CANDIDATES = [
  path.join('dist', 'client', 'v4', 'index.html'),
  path.join('client', 'public', 'v4', 'index.html'),
] as const;

export interface DeployInfo {
  cwd: string;
  node_version: string;
  page_path: string | null;
  page_sha256: string | null;
  page_bytes: number | null;
  engine: Pick<EngineStatus, 'source' | 'bundle_sha256' | 'file_count' | 'error'>;
  sector_catalog_sha256: string | null;
  files_found: { page: boolean; engine: boolean; sector_catalog: boolean };
}

/** 实际会返回给浏览器的页面文件（绝对路径）；都不存在返回 null */
export function v4PagePath(cwd: string = process.cwd()): string | null {
  for (const rel of V4_PAGE_CANDIDATES) {
    const full = path.join(cwd, rel);
    try {
      if (fs.statSync(full).isFile()) return full;
    } catch {
      // 下一个
    }
  }
  return null;
}

export function collectDeployInfo(
  cwd: string,
  engine: EngineStatus,
  catalog: SectorCatalogStatus,
): DeployInfo {
  const page = v4PagePath(cwd);
  let pageSha: string | null = null;
  let pageBytes: number | null = null;
  if (page) {
    try {
      const buf = fs.readFileSync(page);
      pageSha = createHash('sha256').update(buf).digest('hex');
      pageBytes = buf.length;
    } catch {
      // 读失败按不存在报告
    }
  }
  return {
    cwd,
    node_version: process.version,
    page_path: page && pageSha ? path.relative(cwd, page).split(path.sep).join('/') : null,
    page_sha256: pageSha,
    page_bytes: pageBytes,
    engine: { source: engine.source, bundle_sha256: engine.bundle_sha256, file_count: engine.file_count, error: engine.error },
    sector_catalog_sha256: catalog.ready ? catalog.sha256 : null,
    files_found: { page: pageSha !== null, engine: engine.ready, sector_catalog: catalog.ready },
  };
}
