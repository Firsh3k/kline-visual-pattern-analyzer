/**
 * FIR-41 第 4 项：板块扫描范围（服务端统一维护，无 Nest 依赖，tests/v6/test_sector_catalog.js 直接测试）。
 *
 * - 清单来自 sector_data/catalog.json（scripts/build_sector_catalog.py 从仓库 sector_data/ 生成，CI 校验一致）。
 *   服务读的是它的内嵌副本 sector-catalog.data.ts：平台只部署 main.js 沿 import 能追踪到的文件，
 *   按 cwd 读 json 在线上会找不到（2026-10-26 诊断）。sha256 与 catalog.json 字节一致，/api/health 报告。
 * - 同花顺板块都带来源、数据日期；是否“已核对”按权威核对日期与今天（Asia/Shanghai）实时计算：
 *   与 core.sector_validation 的 SectorProvenance.get_freshness_level 同一规则（≤7 天 / ≤30 天 / 更久），
 *   有跨运行时测试。从未核对、核对有差异、核对已过期的，一律不显示“已核对”。
 * - 人工样例没有来源，永远标“人工样例”。
 * - 板块成分只是“扫描哪些股票”，每只股票的结论仍 100% 来自 Python Core（与个股页同源）。
 */
import { createHash } from 'crypto';
import * as fs from 'fs';
import { InvalidRequestError } from './stock-analysis.pure';
import { SECTOR_CATALOG_JSON, SECTOR_CATALOG_SHA256 } from './sector-catalog.data';

export const MAX_SCAN_CODES = 200;          // 与 ScanJobManager 默认 maxCodes 一致

export type SectorStatus = 'verified' | 'unverified' | 'mismatch' | 'stale' | 'sample';

export interface CatalogSector {
  id: string;
  name: string;
  kind: 'ths_concept' | 'curated_sample';
  sector_code: string | null;
  constituents: string[];
  source: string | null;
  source_url: string | null;
  as_of: string | null;
  fetched_at: string | null;
  reconciled_at: string | null;
  reconciliation_method: string;
  reconciliation: { matched: number; local_only: number; source_only: number } | null;
  note: string | null;
}

export interface SectorCatalog {
  schema: string;
  freshness_days: { fresh: number; aging: number };
  sectors: CatalogSector[];
}

export interface SectorView {
  id: string;
  name: string;
  kind: CatalogSector['kind'];
  sector_code: string | null;
  count: number;
  status: SectorStatus;
  verified: boolean;          // 只有 status=verified 才为 true
  label: string;              // 页面直接显示的中文状态
  source: string | null;
  source_label: string;
  as_of: string | null;
  reconciled_at: string | null;
  reconciled_days_ago: number | null;
  scannable: boolean;         // 超过单次扫描上限的不能整板扫描
  note: string | null;
  codes: string[];            // 成分（页面用来算合并后的只数；提交扫描仍只传板块 id，由服务端取成分）
}

const SOURCE_LABELS: Record<string, string> = {
  manual_refresh: '同花顺概念板块（人工导出）',
  local_membership_file: '本地成分文件',
  ths_ifind: '同花顺 iFinD',
  ths_web: '同花顺网页',
};

/** Python FreshnessLevel 的同一规则：fresh / aging / stale / not_reconciled / unknown */
export function freshnessLevel(reconciledAt: string | null, today: string, fresh = 7, aging = 30): string {
  if (!reconciledAt) return 'not_reconciled';
  // 与 Python strptime 一致：格式不对或日期不存在（如 02-30）都算 unknown，不能被 Date 顺延成下个月
  const d = (s: string) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(s)) return NaN;
    const t = Date.parse(`${s}T00:00:00Z`);
    return !Number.isNaN(t) && new Date(t).toISOString().slice(0, 10) === s ? t : NaN;
  };
  const a = d(reconciledAt), b = d(today);
  if (Number.isNaN(a) || Number.isNaN(b)) return 'unknown';
  const days = Math.floor((b - a) / 86400000);
  if (days <= fresh) return 'fresh';
  if (days <= aging) return 'aging';
  return 'stale';
}

/** 今天（Asia/Shanghai）YYYY-MM-DD */
export function shanghaiToday(now: Date = new Date()): string {
  return new Date(now.getTime() + 8 * 3600 * 1000).toISOString().slice(0, 10);
}

export function sectorView(s: CatalogSector, today: string, fd = { fresh: 7, aging: 30 }): SectorView {
  let status: SectorStatus;
  let label: string;
  let daysAgo: number | null = null;
  if (s.kind === 'curated_sample') {
    status = 'sample';
    label = '人工样例 · 无来源';
  } else {
    const level = freshnessLevel(s.reconciled_at, today, fd.fresh, fd.aging);
    if (s.reconciled_at && level !== 'unknown') {
      daysAgo = Math.floor((Date.parse(`${today}T00:00:00Z`) - Date.parse(`${s.reconciled_at}T00:00:00Z`)) / 86400000);
    }
    const r = s.reconciliation;
    if (level === 'not_reconciled' || level === 'unknown') {
      status = 'unverified';
      label = `未核对 · 数据日期 ${s.as_of}`;
    } else if (daysAgo !== null && daysAgo < 0) {
      // QA R79-1：核对日期晚于今天（上海）= 还没发生的核对，多半是录入错误，按未核对处理。
      // freshnessLevel 保持与 Python 一致（Python 对未来日期也返回 fresh），保护放在状态判定这一层。
      status = 'unverified';
      label = `未核对 · 核对日期 ${s.reconciled_at} 晚于今天，视为无效 · 数据日期 ${s.as_of}`;
      daysAgo = null;
    } else if (!r || r.local_only > 0 || r.source_only > 0) {
      status = 'mismatch';
      label = r ? `核对有差异（本地多 ${r.local_only}、权威源多 ${r.source_only}）· ${s.reconciled_at}` : `核对结果缺失 · ${s.reconciled_at}`;
    } else if (level === 'stale') {
      status = 'stale';
      label = `核对已过期（${s.reconciled_at}，超过 ${fd.aging} 天）`;
    } else {
      status = 'verified';
      label = level === 'fresh' ? `已核对 · ${s.reconciled_at}` : `已核对 · ${s.reconciled_at}（${daysAgo} 天前）`;
    }
  }
  return {
    id: s.id,
    name: s.name,
    kind: s.kind,
    sector_code: s.sector_code,
    count: s.constituents.length,
    status,
    verified: status === 'verified',
    label,
    source: s.source,
    source_label: s.source ? (SOURCE_LABELS[s.source] || s.source) : '无来源',
    as_of: s.as_of,
    reconciled_at: s.reconciled_at,
    reconciled_days_ago: daysAgo,
    scannable: s.constituents.length > 0 && s.constituents.length <= MAX_SCAN_CODES,
    note: s.note,
    codes: s.constituents.slice(),
  };
}

/** 清单原文：内嵌副本（线上）或指定文件（测试） */
export interface SectorCatalogSource {
  json: string;
  sha256: string;
}

export const EMBEDDED_SECTOR_CATALOG: SectorCatalogSource = { json: SECTOR_CATALOG_JSON, sha256: SECTOR_CATALOG_SHA256 };

export interface SectorCatalogStatus {
  ready: boolean;
  sha256: string | null;
  error: string | null;
}

export class SectorCatalogStore {
  private catalog: SectorCatalog | null = null;
  private error: string | null = null;
  private sha: string | null = null;

  /** source：默认内嵌副本；传文件路径时读该文件（测试用） */
  constructor(private readonly source: SectorCatalogSource | string = EMBEDDED_SECTOR_CATALOG) {}

  /** 读取并校验；失败只影响板块扫描（fail-closed），不影响服务启动 */
  load(): void {
    try {
      let text: string;
      if (typeof this.source === 'string') {
        text = fs.readFileSync(this.source, 'utf8');
      } else {
        text = this.source.json;
        if (createHash('sha256').update(text, 'utf8').digest('hex') !== this.source.sha256) throw new Error('内嵌清单与 SECTOR_CATALOG_SHA256 不符');
      }
      this.sha = createHash('sha256').update(text, 'utf8').digest('hex');
      const data = JSON.parse(text) as SectorCatalog;
      if (data.schema !== 'sector-catalog-1' || !Array.isArray(data.sectors)) throw new Error('板块清单格式不对');
      for (const s of data.sectors) {
        if (s.kind === 'ths_concept' && (!s.source || !s.as_of)) throw new Error(`同花顺板块 ${s.name} 缺少来源或数据日期`);
        if (!s.constituents.every((c) => /^\d{6}$/.test(c))) throw new Error(`板块 ${s.name} 有非法代码`);
      }
      this.catalog = data;
      this.error = null;
    } catch (err) {
      this.catalog = null;
      this.sha = null;
      this.error = `板块清单不可用：${err instanceof Error ? err.message : String(err)}`;
    }
  }

  /** /api/health 用：清单能否使用、内容 sha256（与仓库 sector_data/catalog.json 比对） */
  status(): SectorCatalogStatus {
    if (!this.catalog) this.load();
    return { ready: !!this.catalog, sha256: this.catalog ? this.sha : null, error: this.catalog ? null : this.error };
  }

  private need(): SectorCatalog {
    if (!this.catalog) this.load();
    if (!this.catalog) throw new SectorCatalogUnavailableError(this.error || '板块清单不可用');
    return this.catalog;
  }

  list(today: string = shanghaiToday()): { today: string; max_scan_codes: number; sectors: SectorView[] } {
    const c = this.need();
    return { today, max_scan_codes: MAX_SCAN_CODES, sectors: c.sectors.map((s) => sectorView(s, today, c.freshness_days)) };
  }

  /** 按板块 id 取成分（去重保序）+ 扫描范围说明；未知 id → 400 */
  resolve(ids: unknown, today: string = shanghaiToday()): { codes: string[]; scope: Array<Omit<SectorView, 'codes'>> } {
    if (ids === undefined || ids === null) return { codes: [], scope: [] };
    if (!Array.isArray(ids)) throw new InvalidRequestError('sectors 必须是板块 id 数组');
    const c = this.need();
    const byId = new Map(c.sectors.map((s) => [s.id, s]));
    const unknown = ids.filter((id) => !byId.has(String(id)));
    if (unknown.length) throw new InvalidRequestError(`未知的板块：${unknown.slice(0, 5).join(', ')}`);
    const seen = new Set<string>();
    const codes: string[] = [];
    const scope: Array<Omit<SectorView, 'codes'>> = [];
    for (const id of [...new Set(ids.map(String))]) {
      const s = byId.get(id)!;
      const { codes: _codes, ...meta } = sectorView(s, today, c.freshness_days);
      scope.push(meta);
      for (const code of s.constituents) if (!seen.has(code)) { seen.add(code); codes.push(code); }
    }
    return { codes, scope };
  }
}

export class SectorCatalogUnavailableError extends Error {
  readonly statusCode = 503;
}
