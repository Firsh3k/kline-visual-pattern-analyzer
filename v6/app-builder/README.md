# FIR-31 V6 — App Builder 部署层（NestJS BFF + Python Canonical Core）

本目录是**线上 App Builder 应用 `app_17fr2txmwp3` 的真实部署代码**（受控版本），
与早期原型 `v6/node-bff/`（Express + 独立 FastAPI，已废弃）不同。

## 架构

```
手机/PC 浏览器
   │  V4 深色金融终端 UI（client/public/v4/index.html，含三道防线）
   ▼
NestJS BFF（server/，Node 全栈，平台自带 Python 3.10）
   │  child_process spawn python_analyzer.py（不传 --allow-skeleton）
   ▼
Python Canonical Core（仓库根 core/，唯一形态算法 Source of Truth）
   └─ SwingPivotEngine + WBottom/MTop/HSBottom/HSTop Detector + 新浪数据源
```

- **个股一切结论来自 Python**：8 种形态、关键点、阶段、分数、个股页指标序列（`core.indicators.series`）、
  以及信号层 `doc.signals`（`core/signals.py`，规则版本 `signals-1`）：
  - 每种形态的最新实例、是否**已确认**（`confirmed` + 未满足原因 `reasons`）、主导形态（`dominant`）；
  - 可直接绘制的颈线 / 平台线段 `lines`（头肩倾斜颈线从左颈线点到最后一根，必经右颈线点）。
  - Node 扫描 / 自选股（`summary.dominant`、`patterns_latest[*].confirmed`）与个股页消费同一份，不另立择优规则。
- 前端只校验 + 展示：`signals` 缺失、规则版本不符、未知阶段 / 方向、缺分数 → 报错，不套默认文案。
- 大盘环境、假信号风险为**参考**（可由 JS 计算），不参与、不改变个股形态与确认结论。
- asOf：YYYY-MM-DD、真实存在、不晚于北京时间今天，BFF 与 Python 同一口径，在取行情前校验；
  已核实交易日历年份内的休市日 K 线一律拒绝。

## fail-closed（禁止静默降级）

| 层 | 失败行为 |
|----|----------|
| `python_analyzer.py` | core 失败 → 非零退出 + stderr 错误，绝不返回随机/骨架假数据；骨架仅 `--allow-skeleton`（本地开发） |
| `stock-analysis.service.ts` | Python 非零退出 → reject；并双保险拒绝 `engine.analyzer != real_core` 的返回 |
| 前端 | API 失败 → 红色错误面板（不渲染任何形态），用户必须主动点"使用JS降级模式"才走本地 JS |

## 关键文件

| 文件 | 作用 |
|------|------|
| `python_analyzer.py` | Python 执行器：health / analyze，调用 core，fail-closed |
| `server/main.ts` | NestJS 启动（托管 client/dist 下 HTML） |
| `server/app.module.ts` | 根模块 |
| `server/modules/stock-analysis/stock-analysis.controller.ts` | health/analyze/watchlist/scan 等 API |
| `server/modules/stock-analysis/stock-analysis.service.ts` | child_process 调用 Python（核心） |
| `server/modules/stock-analysis/watchlist-store.ts` | 自选股服务端存储（FIR-41）：PostgreSQL 表 `kline_watchlist` + `kline_watchlist_meta`（连接串 `SUDA_DATABASE_URL`；**运行时不建表**，表由平台执行 `watchlist.migration.sql` 创建，应用账号只需两张表的 SELECT/INSERT/UPDATE），全站共用一份；删除记录防止旧设备迁入时复活；未配置数据库 → 接口 503，不在浏览器兜底。接口：`GET/DELETE /api/watchlist`、`POST/DELETE /api/watchlist/:code`、`POST /api/watchlist/import` |
| `server/modules/stock-analysis/sector-catalog.ts` | 板块扫描范围（FIR-41 第 4 项）：读 `sector_data/catalog.json`，`GET /api/sectors` 返回每个板块的来源、数据日期、核对状态（已核对 / 未核对 / 核对有差异 / 已过期 / 人工样例）；`POST /api/scan/jobs` 可传 `sectors`（板块 id），由服务端取成分；清单缺失 → 503，只影响板块扫描 |
| `sector_data/catalog.json` | 部署用板块清单，由仓库根 `scripts/build_sector_catalog.py` 从 `sector_data/`（`new_sectors.js` + `provenance/` + `curated_samples.json`）生成，**不要手改**；**部署时这个目录要一起同步到平台** |
| `server/modules/stock-analysis/custom-sector-store.ts` | 自定义板块服务端存储（FIR-41 第 4 项）：与自选股同一套规则（全站共用、删除不复活、运行时只 DML、版本行锁、fail-closed）。表由平台执行 `custom-sectors.migration.sql` 创建（应用账号只需两张表的 SELECT/INSERT/UPDATE）。接口：`GET /api/custom-sectors`、`PUT/DELETE /api/custom-sectors/:name`、`POST /api/custom-sectors/import`；`/api/health` 带 `custom_sectors_store` |
| `server/modules/stock-analysis/watchlist.migration.sql` | 自选股建表脚本（FIR-42），由平台 migration（有 CREATE 权限的账号）执行，可重复执行；与代码里的 `watchlistMigrationSql()` 一致（CI 校验） |
| `scripts/watchlist-probe.js` | 部署前在平台运行环境执行：检查连接、表与权限、写事务与行锁串行（全部回滚，不改数据），输出 JSON，全部通过 exit 0 |
| `server/modules/stock-analysis/scan-jobs.ts` | 扫描后台任务（FIR-41）：`POST /api/scan/jobs` 提交、`GET /api/scan/jobs/:id` 查进度、`DELETE` 取消；全局最多 3 个分析并发（环境变量 `SCAN_MAX_PARALLEL`），单只超时，单次 ≤200 只；任务存内存，服务重启即清空 |
| `server/modules/stock-analysis/v4-page.controller.ts` | 托管 V4 HTML |
| `server/modules/stock-analysis/deploy-info.ts` | `/api/health` 的 `deploy` 字段（FIR-41 第 5 项）：服务实际 `cwd`、Node 版本、页面 / `python_analyzer.py` / `core` 是否在 `cwd` 下、页面 sha256；部署后用 `scripts/v6_verify_deploy.py` 核对 |
| `runtime-deps.json` | 服务端运行时用到、但 `package.json` 未直接声明的间接依赖登记（目前只有 `postgres`），CI 校验链路与版本 |
| `client/public/v4/FRONTEND_PATCHES.md` | V4 HTML 上 Python Core 集成 + 三道防线的受控改动 |

> 完整 5363 行 `client/public/v4/index.html` 在 App Builder 沙箱；本目录用
> `FRONTEND_PATCHES.md` 完整记录相对 V4 原版（tag `v4.0.0`）的全部受控改动。

## 部署后验收（Production Gate）

每次部署/修改后，必须走真实生产路径（非 mock），全部 PASS 才放行：

1. `/api/health` → 200，`core_analyzer=real_core`；`core_sha256` / `analyzer_sha256` 与 `python3 scripts/v6_fingerprint.py --ref <部署的标签>` 输出一致（线上版本自证）
2. `/api/analyze/603068` → 200，`engine.analyzer=real_core`
3. 603068：页面 8 种形态、已确认 / 未确认标签、主导形态与返回文档 `signals`（及 `summary`）逐项一致；行情每天变化，不写死具体分数
4. 不存在任何静默降级路径（后端 fail-closed + 前端红色错误面板）
5. 页面正常加载（非"页面待开发"、非 404）

算法层的防漂移由仓库根 `tests/test_golden_patterns.py`（CI 黄金测试）锁定。

## 平台依赖与构建/运行环境（FIR-41 同步）

本目录的 `package.json` / `package-lock.json` / `nest-cli.json` / `tsconfig*.json` /
`vite.config.ts` / `scripts/` 是从线上妙搭沙箱 **1:1 导出**的脚手架快照（2026-10-23），
用于让仓库记录平台真实构建环境；**不代表本目录可在本地或 CI 独立构建**。

| 项 | 版本/命令 |
|----|-----------|
| Node | `v22.21.0`（`engines.node >=22.0.0`） |
| npm | `>=10.0.0`（`engines.npm`） |
| Python | `3.10.12`（平台自带，`child_process` 调用，无需额外打包） |
| 依赖锁 | `package-lock.json`，lockfileVersion 3 |
| 构建 | `npm run build` → `scripts/build.sh`：`nest build`（SWC，server）+ `vite build`（client），产物输出 `dist/` |
| 生产启动（平台实际） | `NODE_ENV=production node server/main.js`，cwd 为构建产物目录 `dist/`（**待核实**：服务按 `process.cwd()` 找 `client/public/v4/index.html`、`python_analyzer.py`、`core/`，而 `build.sh` 不会把它们拷进 `dist/`；线上实际 cwd 以 `/api/health` 的 `deploy.cwd` / `deploy.files_found` 为准）（即 `scripts/build.sh` 第 162 行拷贝到 `dist/run.sh` 的脚本；入口产物是 `dist/server/main.js`，与 `scripts/prune-smart.js` 的 `SERVER_ENTRY` 一致） |
| 开发 | `npm run dev` → `scripts/dev.sh`；本地连沙箱用 `node scripts/dev-local.js`（会 env pull 到 `.env.local`） |

说明：

- 依赖中含大量 `@lark-apaas/*` 私有包（由妙搭平台提供，私有 registry），本地 / CI 无法 `npm install`；
  构建与部署仍以妙搭平台为准。`package.json` 中的 React / Vite / Radix 等前端依赖是平台模板
  （`fullstack-nestjs-template@2.3.0`）自带；本应用实际渲染的是 `client/public/v4/index.html` 静态页。
- **生产启动入口以 `scripts/run.sh` 为准，不是 `package.json` 的 `start`。** `package.json` 里的
  `"start": "NODE_ENV=production node main.js"` 是平台脚手架的 1:1 原样快照：源码根并不存在 `main.js`
  （`nest build` 的 server 产物在 `dist/server/main.js`），而 `scripts/prune-smart.js` 智能裁剪后生成的
  `dist/package.json` 只保留 `name/version/private/dependencies/engines`、**不含 `scripts`**。因此该 `start`
  不能作为本仓库的启动说明，在源码根或本地直接执行会 `MODULE_NOT_FOUND`；平台实际是在构建后于 `dist/`
  目录执行 `run.sh`（`node server/main.js`）。为保持脚手架快照 1:1，未为本地便利擅改平台 `package.json`/脚本。
- **平台构建期注入、不在 lock 中的工具**：`fullstack-cli`（`postinstall` 的 `fullstack-cli action-plugin init`、
  `build.sh` 的 `npx fullstack-cli action-plugin init`、`dev.js` 中调用）与 `@vercel/nft`
  （`scripts/prune-smart.js` 顶部 `require('@vercel/nft')`）均**不在 `package-lock.json`**，由妙搭平台构建镜像 /
  CLI 环境提供（`build.sh` 以 `npx fullstack-cli …` 临时解析，`@vercel/nft` 由平台构建环境的 `node_modules` 提供）。
  本地 / CI 没有这些工具，无法据此快照独立完成完整 `npm run build` 与智能依赖裁剪；本目录是**平台环境快照，不是完整的独立构建配方**。
  验证方式：以妙搭沙箱内实际构建/发布结果为准。
- 仓库仅同步了受我们管控的 `server/`、`client/public/v4/`、`python_analyzer.py`、`core/`；
  平台模板生成的 `client/src/`、`shared/`、`capabilities/` 等前端源不在仓库内。
- 沙箱实测运行规格：**2 vCPU / 8 GB**；`_FAAS_MAX_CONCURRENCY=10`、`_FAAS_FUNC_TIMEOUT=3600s`（函数级，非网关）。
- 平台可用持久存储（FIR-41 A）：托管 PostgreSQL（`@lark-apaas/nestjs-datapaas` + `SUDA_DATABASE_URL`）、
  Redis over TLS（`MIAODA_CACHE_URL`）、TOS 对象存储（`TOS_BUCKET`），以及持久文件卷 `/home/workspace`
  （跨部署保留；代码目录 `/home/gem/workspace/code` 在 overlay 临时层，重部署会重置）。

## 板块核对流程（FIR-41 第 4 项）

板块要从“未核对”变成“已核对”，必须与权威源（如同花顺 iFinD）的带日期快照完全一致：

1. 用 iFinD 导出快照 JSON：`{"schema_version": "1.0", "source": "ths_ifind", "as_of_date": "YYYY-MM-DD", "sectors": {"创新药": {"sector_code": "886015", "codes": [...]}}}`
2. 在仓库根目录执行：
   - `python3 scripts/build_sector_catalog.py --adopt-snapshot snap.json`（把快照成分写回 `sector_data/new_sectors.js`）
   - `python3 scripts/refresh_sector_membership.py --reconcile-with-snapshot snap.json`（写入权威核对日期与结果，留存证据）
   - `python3 scripts/build_sector_catalog.py`（重新生成 `v6/app-builder/sector_data/catalog.json`）
3. 提 PR → QA → 合并 → 部署。核对日期超过 30 天自动显示“核对已过期”，需要重新走一遍。
