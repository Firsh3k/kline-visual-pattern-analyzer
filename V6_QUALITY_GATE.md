# V6 质量闸门（Quality Gate）— 自动检查与发布信号

> 分支：`v6/web-recovery-stable`（V6 主线；第一阶段发布标签 `v6-web-1.0.0`）
> 目标：代码提交和部署后都自动检查，给负责人明确的 ✅/❌ 信号，无需人工逐项核对。

## 第一层：代码提交 — CI 黄金形态测试（全自动，合并前硬闸门）

- 触发：push / PR 到 `v6/web-recovery-stable`（GitHub Actions）
- 用例：`tests/test_golden_patterns.py`（确定性，无网络/无时间依赖）
  1. W底 不被误判为 头肩底（603068 类偏差的直接防线）
  2. M头 不被误判为 头肩顶
  3. 无未来函数：修改右侧未确认 K 线，不改变已确认历史 pivot
  4. Pivot 确认边界：right_bars-1=pending / right_bars=confirmed
  5. 输出 schema 完整性
- 判定：CI 全绿才允许合并；任一形态漂移即红，阻断合并。
- 位置：CI 步骤 "Run golden pattern tests (algorithm drift gate)"，位于稳定的 core 测试段。

### 硬闸门只跑确定性测试（网络测试不阻断）
- `tests/v5/` 硬闸门步骤设 `SKIP_NETWORK_TESTS=1`，排除真实访问新浪/腾讯的 `test_data_providers.py`，
  保证信号只由确定性算法/契约测试决定，不被外部数据源 SSL 超时/限频抖动拖红（run #246 教训）。
- 真实数据源连通性单独跑一个 `continue-on-error` 的 non-blocking 步骤，仅作信息参考，不阻断合并。
- 黄金测试（`test_golden_patterns.py`）为纯合成数据、无网络、无时间依赖，是算法漂移的最终硬闸门。

### 冻结历史快照（FIR-40 问题 11）
- 用例：`tests/v5/test_v6_frozen_snapshot.py`，期望值在 `tests/v6/snapshots/stage1_frozen.json`。
- 用仓库内冻结的真实行情（002436 最新 / 002436 asOf=2026-09-30 / 002594 / 600519 / 上证指数），
  固定“当前时间”，逐字段比对 data_hash、阶段、分数、确认、主导形态、线段、summary 与大盘环境。
- 行情每天变也不影响这个基准；不再写死任何股票“当天”的结果。
- 有意的算法变更：经 QA 同意后用 Python 3.10 运行 `python3.10 tests/v6/frozen_snapshot.py --write`，PR 中说明差异。
- `core.result_sha256` 依赖 Python 小版本（3.12 起 `sum()` 改为补偿求和），快照按线上 Python 3.10 生成；
  其它版本只跳过这一项。

### 实际部署页面与 BFF（CI 的 JS job）
- `tests/v6/test_v4_adapter_r67.js`：加载实际部署的 V4 页面脚本，验证确认 / 主导只来自 Python、斜颈线绘制、坏数据报错。
- `tests/v6/test_v4_no_local_engine.js`：本地 JS 形态引擎 / 推荐度 / 降级入口不得重新出现。
- `tests/v6/test_bff_pure.js`：asOf 校验与扫描列表透传 Python 确认。
- `tests/v6/test_repo_equals_prod.js`（FIR-41 第 5 项）：仓库里的平台快照自洽——依赖与 lock 版本一致；服务端运行时导入
  都是直接依赖或在 `runtime-deps.json` 登记的间接依赖；启动链（`run.sh` / `build.sh` / nest / prune-smart）一致；
  服务端不再按 cwd 读 .py / json；没有根级 `@Controller`；`/api/health` 的页面 / 引擎包 / 板块清单指纹算法与 `scripts/v6_fingerprint.py` 一致。
- `tests/v6/test_engine_bundle.js` + `tests/v5/test_runtime_bundles.py`（平台打包问题）：内嵌引擎包与 `core/**/*.py`、`python_analyzer.py`
  逐字节一致（`scripts/build_runtime_bundles.py --check`，改了 core 忘记重新生成即红）；在没有任何 .py 的目录里解压后 Python health 为
  `real_core`、指纹与仓库一致；目录被改 / 多文件 / 缺文件会重建，包被篡改 / 路径穿越一律拒绝，并发解压安全。

### 平台打包（CI 的 platform-package job，2026-10-26 事故的回归）
- 按 `package-lock.json` `npm ci` 装全套平台依赖。
- 平台同款 ESLint：`@lark-apaas/fullstack-presets` 的 `eslintPresetsOfSimple.server`（`tests/v6/platform-eslint.config.cjs`），0 错误 0 警告；
  平台 commit_task 会拦的提交在这里先拦住。`tsc` 类型检查 server。
- `tests/v6/test_platform_package.js`：真实 `nest build` → 用 `@vercel/nft` 从 `dist/server/main.js` 追踪（同 `prune-smart.js`），
  只把追踪到的文件放进部署目录、不放任何 .py → 在该目录启动服务 → `/api/health` 必须 `real_core`，
  `scripts/v6_verify_deploy.py` 判定全部 PASS（自选股用内存实现，只此一项 WARN）；根路径（含平台子路径前缀）返回仓库页面，`/api/sectors` 可用。
  在 6a41532 上跑这个测试会复现线上的 `core_analyzer=not_configured` 与 “V4 page not found”。
- `tests/v6/test_sector_catalog.js` + `tests/v5/test_fir41_sector_catalog.py`（FIR-41 第 4 项）：板块扫描范围来自服务端清单 `sector_data/catalog.json`（与仓库 `sector_data/` 一致）；同花顺板块必须有来源和数据日期；只有与权威快照完全一致、核对日期在 30 天内的才显示“已核对”（新鲜度规则与 Python `core.sector_validation` 逐日比对）；人工样例永远标“人工样例”；页面不写死板块成分，清单不可用时不兜底。
- `tests/v6/test_custom_sectors.js`（FIR-41 第 4 项）：自定义板块存服务端——内存与真实 PostgreSQL 同一组用例；删除不复活、迁入只加服务器没有的；运行时不建表；两连接并发写 rev 连续；页面一次性迁入旧数据、多端同步、503 不在本机兜底、迟到旧响应不回退。
- `tests/v6/test_mobile_layout.js`（FIR-41 第 6 项）：真实 Chrome 在 320 / 390 / 768 / 1280px 打开实际页面（接口用真实 Core doc 打桩），首页、个股分析、自选股、市场扫描都不能横向滚动；多列卡片网格必须带 `m-stack`。

## 第二层：部署后 — 线上验收

流程：PR → QA → 合并 → 合并提交 CI 绿 → 记录回退点 → 部署 → 线上冒烟 → 回写 FIR。不先上线后审核。

**版本自证（替代人工逐个文件算哈希）**
- [ ] 在已登录的浏览器打开线上 `/api/health`，把返回的 JSON 存成 `health.json`
- [ ] 运行 `python3 scripts/v6_verify_deploy.py --ref <本次部署的标签或提交> --health health.json`，结论必须是“通过”：
  `core_sha256` / `analyzer_sha256`（Python Core 与执行器）、`deploy.page_sha256`（浏览器实际拿到的页面）与该版本一致，
  `core_analyzer=real_core`，`deploy.files_found` 里该版本应有的项都为 true（内嵌引擎包之后：page / engine / sector_catalog；
  之前的版本：page / analyzer / core 在 `cwd` 下），内嵌引擎包之后的版本还要 `deploy.engine.source=bundle` 且 `bundle_sha256`、
  `deploy.sector_catalog_sha256` 与该版本一致，`watchlist_store` 为 `postgres` 且 `ready=true`
  （FIR-41 第 5 项；只想看指纹时仍可用 `python3 scripts/v6_fingerprint.py --ref <版本>`）
  退出码：0 通过；1 不通过（字段缺失、类型不对也算）；3 部分核对——只在部署的确是第 5 项之前的版本、显式加
  `--allow-legacy-health` 时出现，页面与运行时文件未核对，**不能当作 PRODUCTION GATE PASS**

**个股冒烟**
- [ ] 任选一只股票：`/api/analyze/<code>` → 200，`engine.analyzer=real_core`，250 根前复权，有 `signals`，无推荐度
- [ ] 页面 8 种形态的阶段 / 分数 /“已确认·未确认”标签、主导形态与返回文档 `signals` 一致（行情每天变，不写死数值）
- [ ] 回测：`asOf=<一个月前的交易日>` → `as_of` 等于该日或之前最近的交易日，250 根
- [ ] 未来日期：`asOf=2099-01-01` → 400，“晚于今天”
- [ ] 有头肩形态的股票：颈线按斜率绘制

**列表链路**
- [ ] 自选股批量分析 / 市场扫描：每行的“买入 / 卖出信号”只来自 Python 已确认的主导形态；无效代码计入失败清单

全部通过才给 **✅ PRODUCTION GATE PASS**；任一失败给 **❌ FAIL** 并列出阻断项。

> App Builder 应用所有请求需豆包登录态，外部 curl 访问会被 302 跳到登录页；线上验收需登录后进行。

## fail-closed 原则（三层一致）

| 层 | 失败行为 |
|----|----------|
| `python_analyzer.py` | core 失败 / asOf 非法 → 非零退出 + 错误，绝不返回骨架或降级结果 |
| NestJS `stock-analysis.service.ts` | Python 非零退出 → reject；拒绝 `engine.analyzer != real_core`；扫描列表缺信号层的股票计入失败 |
| 前端 V4 HTML | API 失败或数据合同不符（未知阶段 / 方向、缺分数、缺 signals）→ 红色错误面板，不渲染形态；页面没有本地 JS 形态引擎 |
