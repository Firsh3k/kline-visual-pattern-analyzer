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

提前收盘按 `close_time_et=13:00` 判定收盘前后状态，不统一按 16:00。年度交易日数校验：2021=251、2022=251、2024=252。

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