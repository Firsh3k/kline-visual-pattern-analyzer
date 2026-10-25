/**
 * FIR-41 第 5 项：线上“实际在跑的是什么”自报，供 /api/health 与 scripts/v6_verify_deploy.py 比对。
 *
 * - 服务按 process.cwd() 找页面、Python 执行器和 core（见 v4-page.controller.ts、stock-analysis.service.ts），
 *   这里用同一组相对路径，报告实际 cwd、文件是否存在、页面 sha256。
 * - 页面 sha256 每次现算（约 200KB，开销可忽略），反映线上实际返回给浏览器的那个文件。
 * - 只报路径、版本和哈希，不含环境变量或连接串。
 * - 无 Nest 依赖，tests/v6/test_repo_equals_prod.js 直接测试。
 */
import { createHash } from 'crypto';
import * as fs from 'fs';
import * as path from 'path';

/** 相对 process.cwd() 的运行时文件；页面控制器与 health 共用，避免两处路径不一致 */
export const RUNTIME_FILES = {
  page: path.join('client', 'public', 'v4', 'index.html'),
  analyzer: 'python_analyzer.py',
  core: path.join('core', '__init__.py'),
  sector_catalog: path.join('sector_data', 'catalog.json'),   // FIR-41 第 4 项：板块扫描范围（与 sector-catalog.ts 同一路径）
} as const;

export interface DeployInfo {
  cwd: string;
  node_version: string;
  page_sha256: string | null;
  page_bytes: number | null;
  files_found: { page: boolean; analyzer: boolean; core: boolean; sector_catalog: boolean };
}

export function v4PagePath(cwd: string = process.cwd()): string {
  return path.join(cwd, RUNTIME_FILES.page);
}

export function collectDeployInfo(cwd: string = process.cwd()): DeployInfo {
  const found = (rel: string) => {
    try {
      return fs.statSync(path.join(cwd, rel)).isFile();
    } catch {
      return false;
    }
  };
  let pageSha: string | null = null;
  let pageBytes: number | null = null;
  try {
    const buf = fs.readFileSync(v4PagePath(cwd));
    pageSha = createHash('sha256').update(buf).digest('hex');
    pageBytes = buf.length;
  } catch {
    // 页面不存在：保持 null，files_found.page=false
  }
  return {
    cwd,
    node_version: process.version,
    page_sha256: pageSha,
    page_bytes: pageBytes,
    files_found: {
      page: found(RUNTIME_FILES.page), analyzer: found(RUNTIME_FILES.analyzer), core: found(RUNTIME_FILES.core),
      sector_catalog: found(RUNTIME_FILES.sector_catalog),
    },
  };
}
