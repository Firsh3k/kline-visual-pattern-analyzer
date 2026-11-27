# V6 美股个股线验收证据（FIR-47）

- 分支：`v6/us-stock-analysis`，目标分支：`v6/web-recovery-stable`
- 生产基线对照：`45f6e15`（v6-web-1.1.0，PR #81 合并提交）
- 调用链复用：V4 页面 → NestJS BFF → Python Core 5.2.0 → signals-1（形态/评分/确认全部由同一 Core 计算，页面只展示）
- 冻结时间参数：NOW = 2026-11-26 12:00 CN（= 美东 2026-11-25 23:00，已收盘）

## 1. 交易日历复核与修正

`ee33c4f` 误把 2021-06-18 标为休市，已移除。权威依据：

- NASDAQ ETA2021-45：2021-06-18 / 06-21 正常开市（Juneteenth 当年尚未成为 NYSE/NASDAQ 休市日）。
- NASDAQ ETA2022-55：首个 Juneteenth 休市为 2022-06-20。
- NASDAQ 官方日历：Good Friday 休市、感恩节后周五 / 7-3 / 平安夜适逢交易日时 13:00 美东提前收盘。

新增 `_EARLY_CLOSES`（2020–2026 共 14 个 13:00 美东提前收盘日）、`is_early_close()`、`close_time()`（非交易日返回 None，未核实年份 fail-closed）、`REGULAR_CLOSE_ET` / `EARLY_CLOSE_ET` 常量。`core/analysis.py` 的 `_calendar_info_us` 改用 `close_time(today)`，新增 `close_time_et` / `early_close` 字段（仅美股路径，A 股不变）。

四个确定性测试（`tests/v6/test_us_market.py`）：

| 日期 | 预期 | 结果 |
|---|---|---|
| 2021-06-18 | 正常开市 | ✓ |
| 2022-06-20 | Juneteenth 休市 | ✓ |
| 2026-04-03 | Good Friday 休市 | ✓ |
| 2026-11-27 | 美东 13:00 提前收盘，测收盘前/后状态 | ✓ |

提前收盘按 `close_time_et=13:00` 判定收盘前后状态，不统一按 16:00。年度交易日数校验：2021=252、2022=251、2024=252、2025=250。

本轮再修正两处日历错误（整改②）：

| 日期 | 原错误 | 修正 | 权威依据 |
|---|---|---|---|
| 2021-12-31 | 误标休市，拒绝当天有效 K 线 | 正常开市 | NYSE 2021–2023 日历：2022 元旦适逢周六，周五 12-31 正常开市（仅圣诞节适逢周六才周五休市，如 2021-12-24） |
| 2025-01-09 | 误当交易日 | 休市（卡特国葬哀悼日） | Nasdaq 官方公告 closure in honor of National Day of Mourning（James Carter） |

依据：用户提供的 NYSE/ICE 2021–2023 假日与提前收盘日历、Nasdaq ETA2021-45 / ETA2022-55、Nasdaq 卡特哀悼日公告。`core/market_data/us_calendar.py` 与 `v6/app-builder/core/` 镜像同步修正。

## 2. 个股验收（固定行情，Core → API → 页面逐字段）

行情 fixture：开发环境东财三个子域全部不可达（curl 空响应，provider 报"全部主机不可用"），改用腾讯美股前复权抓取并如实记录 `source=tencent`。AAPL / NVDA 各 250 根（2025-11-28 → 2026-11-25）。

| 代码 | data_hash | 主导形态 | 方向 | 确认 | Core↔API signals_sha |
|---|---|---|---|---|---|
| AAPL | 47129be8efa35fcd… | platform_break（平台突破） | bull | 未确认 | 一致（52ca15a0f05f…） |
| NVDA | 2503a3d68c2ed353… | m_top（M头） | bear | 已确认 | 一致（a1f6ae36990d…） |

`tests/v6/test_us_acceptance.js` 17 项全绿：Core↔API `result_sha256` / `signals_sha256` / `data_hash` 一致；8 形态 score / stage / confirmed 逐项一致；页面 `doAnalyze` 渲染与 signals 一致；双向乱序竞态；失败横幅；非 `real_core` 拒绝。

反例验证：`V4_PAGE=/tmp/v4_baseline.html`（基线页面）竞态用例确实失败，证明守卫有效。

## 3. A 股零漂移

`frozen_snapshot.py`（NOW=2026-10-19 12:40 CN）在当前分支与基线 worktree（45f6e15）输出逐字节一致，含 600519_latest（100 根，as_of 2026-09-29，data_hash a204007e5c2610ac…）。同一份行情、同一时间参数，既有结果零漂移。

## 4. 边界与 fail-closed

- 未知代码 ABC123 → `InvalidInputError`"无法识别市场"（exchange_symbol 包装）。
- 格式合法但表外 ZZZZZ → `RuntimeError`"行情获取失败（fail-closed，未降级）"。
- 东财 provider mock `_http_get`：解析 / fqt=1 / 子域轮询（首主机失败 → 13.push2his）/ 全失败 / 空 klines / 非 us 拒绝。
- A 股↔美股快速切换响应乱序：旧响应不得覆盖当前页面（V4 页面 `_analyzeSeq` 守卫）。

## 5. 打包与回归

- 引擎包：`scripts/build_runtime_bundles.py` 重建 39 文件，`bundle_sha256=393532404b48305b…`，`--check` 通过；`test_engine_bundle.js` 14 项。
- 类型检查：服务端 `tsc --noEmit -p tsconfig.node.json` 通过；`nest build` 成功（18 文件）。客户端无 TS 源码（纯静态 V4 HTML），`tsconfig.app.json` TS18003 为基线既有状态。
- 平台构建：`test_platform_package.js` 9 项（含部署产物缺依赖/缺页面负例）；`test_repo_equals_prod.js` 16 项。
- Python 回归：`tests/v5 + tests/v6` = 1009 passed, 1 skipped；`test_us_market.py` 39 项。
- 其余 JS：bff_pure 9 / custom_sectors 18 / sector_catalog 18 / scan_jobs 15 / watchlist_page 11 / watchlist_store 18 / v4_no_local_engine 14 / v4_adapter_r67 17。

## 6. 环境限制与待线上验证

- `fullstack-cli`（平台全局工具，仅 postinstall `action-plugin init` 用）VM 缺失，依赖本体已装好，不影响 tsc/nest 构建。
- `test_mobile_layout.js` 浏览器段需 `playwright-core`（CI 提供），静态源码约束已通过。
- **东财生产出口可达性 = 部署后冒烟项**：开发环境通过不能代替生产验证。
## 7. 本轮整改（FIR-47 四项工单，复核基准）

### ① 两项 CI 失败修复（run 45650800048）

- **JS DataSourceType 枚举同步**：Python 新增 `eastmoney`，JS 镜像 `tests/v5/canonical_schema.js` 缺。补 `EASTMONEY:'eastmoney'`（顺序 sina/tencent/ifind/tongdaxin/eastmoney/manual）。`run_canonical_parity.py` 100% 通过。
- **Playwright runner 兼容**：`ubuntu-latest` 已迁 ubuntu-26.04，playwright@1.56.1 报 "does not support chromium on ubuntu26.04-x64"。`js-syntax-check` job 固定 `runs-on: ubuntu-24.04`（受支持且兼容）。本地 VM chromium 移动布局 29/29 通过。

### ② 美股日历 + 美东日期校验

- 日历修正见 §1（2021-12-31 开市、2025-01-09 休市），3 个日历回归用例。
- **美东日期校验**：`core/analysis.py` 新增 `today_us()`（ZoneInfo America/New_York，fail-closed），`parse_as_of(market=)` 与 `analyze` 最后一根 K 线按市场选时区——美股用美东今天，A 股仍用北京时间。JS `stock-analysis.pure.ts` 加 `todayNewYork`（Intl en-CA timeZone）/`marketOfCode`，`assertAsOf(market=)`；controller/service 传 `marketOfCode(checkedCode)`，A 股指数路径保持 cn。
- 关键场景：北京 2026-11-25 00:30 = 美东 2026-11-24 11:30（EST），北京已跨日、美东未跨日时拒绝美东未来日期。`TestUSDateValidation` 4 用例 + `test_bff_pure.js` 3 个美东用例。

### ③ 页面收回"美股个股分析"范围

- 自选股 `addStock` / 自定义板块 `addCustomSector` / `addSelectedToWatchlist` 恢复 A 股六位数字输入限制（`isCNCode` 守卫，非 CN 代码点名拒绝）。
- 美股个股页隐藏依赖上证指数的内容：相对强度（RS）卡片、逆大盘风险知识项、"大盘环境适配"评估维度、header"含大盘参考数据"。`doAnalyze` 对美股跳过 `sh000001` 大盘抓取。A 股页面保持原样（RS 卡片仍渲染）。
- `?code=` 个股入口保留美股支持。`test_us_acceptance.js` section 五 5 用例。

### ④ 可重复验收接入 CI + 真实 Nest + 零漂移

- **CI 接入**：`python-tests` job 跑 `tests/v6/test_us_market.py`（46 项）；`js-syntax-check` job 跑 `tests/v6/test_us_acceptance.js`（22 项）；`platform-package` job（已 npm ci 全量依赖）跑 `tests/v6/test_us_nest_e2e.js`（5 项）。
- **真实 Nest 端到端**（`test_us_nest_e2e.js` + `us_nest_boot.js`）：启动真实 `StockAnalysisController → StockAnalysisService → EngineResolver → Python subprocess → Canonical Core`，经 `KLINE_FIXTURE_DIR` 测试接缝注入冻结行情（`python_analyzer._fixture_series`，仅美股、仅 env 启用，生产不设此 env 行为不变），走真实 HTTP `/api/analyze/AAPL|NVDA`。核对：真实 API `result_sha256`/`signals_sha256`/`data_hash`/8 形态 score·stage·confirmed == 冻结 Core；页面 `doAnalyze` 渲染主导形态/分数/确认 == 真实 API；美股页隐藏 RS。5/5 通过。
- **600519 零漂移复跑**：`frozen_snapshot.py`（NOW=2026-10-19 12:40 CN）当前分支 vs 基线 worktree（45f6e15）输出**逐字节一致**（含 600519_latest data_hash a204007e5c2610ac…、002436、002594、大盘）。A 股路径零漂移。
- 东财真实取数留部署后冒烟，无需 Staging。

### 本轮测试汇总（本地全绿）

| 套件 | 结果 |
|---|---|
| `test_us_market.py` | 46 passed |
| `test_us_acceptance.js` | 22 passed |
| `test_us_nest_e2e.js`（真实 Nest） | 5 passed |
| `test_bff_pure.js` | 12 passed |
| `run_canonical_parity.py` | 100% |
| `test_mobile_layout.js` | 29 passed |
| 服务端 `tsc --noEmit` | 0 错误 |
| 平台 ESLint server | 0 警告 |
| `build_runtime_bundles.py --check` | 一致 |
| 600519 零漂移（vs 45f6e15） | 逐字节一致 |

引擎包重建 39 文件，`bundle_sha256=9da72bc2581f6e5126eb3ebf1939090efc8d678438db590e9036276d15e96a09`（含 `python_analyzer.py` fixture 接缝 + core 美东校验）。
