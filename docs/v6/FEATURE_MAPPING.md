# 旧功能 → V6 对照表

本表以 V5 生产页面 `kline_analyzer.html`（v5.0.1）与 V5 Python `core/` 为恢复基线。

**状态说明**
- **开发完成 · 待 QA**：代码、自动化测试与本地 / CI 证据已齐，等待独立 QA 验收。
- **按决策移除**：PO 已批准不恢复。
- **外部阻塞**：需要外部平台或人工操作。

**证据位置**
- `services/analysis_api/tests/*`：API 契约
- `apps/bff/test/*`：BFF
- `tests/e2e/test_e2e_web.py`：真实浏览器，手机 390×844 触屏与 PC 1366×900 两种尺寸
- `tests/v5/*`、`tests/pipeline/*`：core 与管线
- `docs/v6/evidence/*.png`：截图（CI 也会上传 `v6-e2e-evidence` 产物）

## A. 决策引擎统一（FIR-38 / FIR-32）

| 旧功能 | V6 页面 / API | Issue | 测试证据 | 状态 |
|---|---|---|---|---|
| 网页里的 JS 形态识别（FIR-14 / FIR-30 路线） | 删除。网页与 BFF 不含任何形态、指标或评分代码，全部来自 `POST /api/v1/analyses` → `core.analysis.analyze` | FIR-38、FIR-31 §1 | `test_stock_page_parity_with_core[phone/pc]`：网页每个形态的有无、方向、阶段、分数、置信度、关键点、颈线、突破价、目标、止损，与 core 逐字段一致（误差 ≤1e-4），dataHash 与结果哈希相同 | 开发完成 · 待 QA |
| 8 种形态（W底 / M头 / 头肩底 / 头肩顶 / 均线多头 / 均线空头 / 平台突破 / 平台破位） | 个股页“形态识别”，按 category 分组 | FIR-32 D1 | `test_v4_migration_parity.py`、`test_pattern_category_evidence.py`、API `test_analysis_equals_direct_core_call` | 开发完成 · 待 QA |
| 大盘环境（PR #49 的 `/market-env` 平行评分） | `GET /api/v1/market` → `core.ClassicMarketRegimeAnalyzer`；平行实现不存在 | FIR-38 | `test_market_env_from_core`、管线 `test_market_env_matches_direct_call` | 开发完成 · 待 QA |
| 综合推荐度 / `calc_attention` / 形态-环境适配度 | 不提供 | FIR-32 D4 | `test_summary_is_derived_from_same_doc`（摘要中没有 recommendation / attention 字段） | 按决策移除 |
| 每日管线 | `pipeline/run_daily.py` → `core.analysis.analyze`，与 API 同源 | FIR-38 | `tests/pipeline`，共 30 项 | 开发完成 · 待 QA |
| 个股页与扫描结果一致 | 扫描每只股票走同一个 `analyze_code`，摘要来自同一个 `pattern_summary` | FIR-31 §10 | API `test_scan_dedupe_failures_and_parity_with_single_analysis`、E2E `test_scan_custom_pool` | 开发完成 · 待 QA |

## B. 个股分析（FIR-31 §6A、FIR-12、FIR-24）

| 旧功能 | V6 页面 / API | Issue | 测试证据 | 状态 |
|---|---|---|---|---|
| 代码输入与分析 | 个股页 `#/stock/:code`，可选 asOf 与复权方式（前复权 / 不复权，不混用） | FIR-12 | E2E `test_stock_page_parity_with_core` | 开发完成 · 待 QA |
| 真实 K 线与成交量、均线叠加、摆动点 | lightweight-charts 4.1.3（本地 vendored，CSP 下无外部脚本） | FIR-31 §6A | 截图 `phone_01_stock_002436.png`、`pc_01_stock_002436.png` | 开发完成 · 待 QA |
| 形态标注：关键点、颈线、目标、止损 | 点选形态后在图上标注，详情给出精确值 | FIR-12 | E2E 逐字段断言 | 开发完成 · 待 QA |
| 已有几何防误报规则（弱头肩 <3%、斜颈线 1.5 倍、pending pivot 不参与） | core 原样保留（FIR-24），未改动 | FIR-24 | `tests/v5/test_pivot_patterns.py` 等（CI python-tests） | 开发完成 · 待 QA |
| 阶段（突破前夜 / 突破当日 / 已突破 / 回踩 / 失败） | 中文阶段 + 判定证据表 | FIR-35、FIR-36 | `test_fir35_platform_retest.py`、`test_fir36_breakout_evidence.py` | 开发完成 · 待 QA |
| 放量确认 / 缩量待确认 | `evidence.volume_confirmed`；缩量突破时页面给出提示 | FIR-32 D3 | `test_low_volume_breakout_day_is_flagged_not_confirmed` | 开发完成 · 待 QA |
| 技术指标 | 现行口径与对照值（Wilder RSI / ATR、样本 BOLL），附口径说明 | FIR-37 | `test_fir37_indicator_conventions.py` | 开发完成 · 待 QA |
| 形态知识卡片 | 图鉴页（同一套文字） | — | E2E `test_catalog_and_version` | 开发完成 · 待 QA |
| 数据来源、复权、截至日期、错误状态 | 溯源区显示 source / adjustment / 交易所日历 / 区间 / dataHash / 结果哈希 / requestId；顶部标注收盘或盘中、是否过期 | FIR-23、FIR-25、FIR-27 | `test_core_analysis.py`，共 23 项 | 开发完成 · 待 QA |
| 失败处理 | 无效代码 422、北交所无数据 404、上游脏数据 502、行情不可用 503，均显示 requestId，不展示任何推测结果。若有上次结果，单独提供按钮并明确标注为旧结果 | FIR-31 §3 | E2E `test_stock_errors_fail_closed_with_request_id`、API `test_failure_returns_last_good_explicitly_marked` | 开发完成 · 待 QA |

## C. 自选股（FIR-31 §6B、§7；FIR-19、FIR-20、FIR-21）

| 旧功能 | V6 页面 / API | Issue | 测试证据 | 状态 |
|---|---|---|---|---|
| 添加 / 移除 / 清空 | 自选股页；`PATCH /api/v1/watchlist`，ops 带 opId 保证幂等 | FIR-19 | `test_watchlist_ops_tombstones_and_etag` | 开发完成 · 待 QA |
| 删除不复活（tombstones） | 服务端按时间合并：早于删除时间的添加会被忽略 | FIR-20 | API 测试；E2E `test_watchlist_cross_device_no_resurrection`（两台设备 + 离线旧操作） | 开发完成 · 待 QA |
| 跨设备同步（原为 GitHub 文件同步、last-writer-wins） | Python 服务成为唯一写入者，SQLite 原子事务，提供 ETag / If-Match。GitHub 配置只用于迁移来源与导出备份 | FIR-21、FIR-31 §7 | 同上 + `test_users_are_isolated` | 开发完成 · 待 QA |
| 批量分析 | “批量分析全部”，以扫描任务方式执行（scope=watchlist） | FIR-31 §6B | E2E 截图 `phone_04_watchlist_batch.png` | 开发完成 · 待 QA |
| 详情跳转 | 结果卡片 → `#/stock/:code` | — | E2E 点击跳转断言 | 开发完成 · 待 QA |
| 导出 / 导入配置 | 导出：`GET /api/v1/watchlist/export`（V5 v1.2 格式）。导入：一次性迁移脚本（非破坏性） | FIR-31 §7 | `test_export_v12_format`、`test_migrate.py`，共 5 项 | 开发完成 · 待 QA |
| GitHub Token 写入同步 | 不再需要：浏览器不保存 Token，同步由服务端完成 | FIR-31 §7 | — | 按架构替换 |
| 现有 `config/watchlist.json` 迁移 | `python -m app.migrate`：源快照、sha256、数据库备份、逐项对账、失败自动回滚、幂等 | FIR-31 §7 | `test_migrate_real_repo_config`（13 只 + 2 个 tombstone，对账 0 问题） | 开发完成 · 待 QA（Staging 实际执行属外部步骤） |

## D. 市场扫描与板块（FIR-31 §6C-D；FIR-22、FIR-29、FIR-15）

| 旧功能 | V6 页面 / API | Issue | 测试证据 | 状态 |
|---|---|---|---|---|
| 17 个预置板块 | `GET /api/v1/sectors`，从 V5 页面原样提取，逐只一致；同时标注成分股未经权威对账 | FIR-15 | `test_presets_preserved_from_v5_page`、CI `extract_preset_sectors.py --check` | 开发完成 · 待 QA |
| 自定义板块 / 股票池 | 板块页 CRUD（含 tombstone）；扫描页可直接输入代码 | FIR-22 | `test_custom_sector_crud_with_tombstone`、E2E `test_scan_custom_pool` | 开发完成 · 待 QA |
| 同花顺动态板块 | 输入代码后进入 Pending → 复制验证指令 → 提交权威快照（与 FIR-15 使用同一校验）→ 状态为 Verified 且在 7 天内才可扫描。Pending / Stale / Unavailable 时禁止扫描（API 返回 409，页面复选框禁用）；重复添加不会重置状态，避免卡死 | FIR-29、FIR-22 | API `test_dynamic_sector_pending_verify_fresh_scan`、E2E `test_dynamic_sector_flow`、截图 06 / 07 | 开发完成 · 待 QA（真实 iFinD 快照需人工获取） |
| 过滤（最低分 / 方向，以及形态、阶段、距今根数） | 扫描页过滤，仅作用于 Python 返回的字段 | FIR-22 | `test_scan_sector_membership_and_filters` | 开发完成 · 待 QA |
| 进度、结果、失败清单 | 任务方式：进度条、成功 / 失败 / 匹配计数、可取消、失败按类型列出 | FIR-22 | `test_scan_dedupe_failures_and_parity_with_single_analysis`、`test_scan_timeout_is_reported`、截图 05 | 开发完成 · 待 QA |
| 去重与板块归属 | 去重后保留每只股票所属板块列表 | FIR-22 | `test_scan_sector_membership_and_filters`（dedupedFrom=3 → 2，归属为甲 / 乙） | 开发完成 · 待 QA |
| 并发与超时 | 有界并发（默认 4），单只超时（默认 25 秒），单次最多 600 只 | FIR-31 §4 | `test_scan_timeout_is_reported` | 开发完成 · 待 QA |

## E. 数据合同、校准、版本（FIR-23、FIR-25、FIR-27、FIR-13、FIR-28）

| 旧功能 | V6 页面 / API | Issue | 测试证据 | 状态 |
|---|---|---|---|---|
| 数据源与复权策略（qfq 只走腾讯，不静默回退新浪） | `LiveProvider` 使用 core 的 provider，TLS 正常校验；复权不匹配时拒绝 | FIR-23 | `test_data_providers.py`、API adjustment 用例 | 开发完成 · 待 QA |
| 交易所与交易日历 | `calendar.calendar_id`（SSE / SZSE / BSE）。只有权威日历覆盖时才标记为 verified，否则为 unverified / unknown | FIR-25 | `test_unverified_calendar_never_claims_verified`、`test_index_calendar_is_derived_not_defaulted` | 开发完成 · 待 QA |
| 截至日期、收盘 / 盘中状态、dataHash、缺失状态 | `as_of`、`session_state`、`calendar.stale`、`input.data_hash` | FIR-27 | `test_core_analysis.py` | 开发完成 · 待 QA |
| 非法 K 线（OHLC、负量、NaN、重复、乱序） | 整只拒绝，不做修补；管线保留上次结果并标注 `stale_previous` | 审核项 | `test_invalid_values_rejected`、`test_pipeline_rejects_invalid_input_and_keeps_previous_result`（7 种） | 开发完成 · 待 QA |
| 可靠性 / 成功率百分比（图鉴中的 78%~86%） | 不迁移；历史统计来自校准数据，样本不足或口径不适用时显示 N/A | FIR-13 | `test_calibration_marks_inapplicable_and_insufficient_as_null`、E2E 断言图鉴中没有“可靠性” | 开发完成 · 待 QA |
| 版本徽章 | 版本页同时显示网页 + BFF 发布身份与引擎身份；元数据缺失时显示 Version Unknown；RC 版本只在非生产环境有效 | FIR-28 | `test_fir28_release_identity.js`、`test_version_*`、E2E `test_catalog_and_version` | 开发完成 · 待 QA |

## F. 安全与运行（FIR-31 §4、§8；审核项）

| 项 | 实现 | 测试证据 | 状态 |
|---|---|---|---|
| 鉴权 | 登录（scrypt）+ 签名会话 Cookie；Python 服务只接受 Bearer 服务凭证 | `apps/bff/test` 中 auth 用例、E2E `test_unauthenticated_cannot_reach_api` | 开发完成 · 待 QA |
| CSRF、CORS | 双提交 Cookie 与会话绑定 + Origin 校验；同源，不开启 CORS | BFF `auth, csrf, proxy...` | 开发完成 · 待 QA |
| 限流 | 登录、API、分析、创建扫描分别限流 | BFF `rate limits` | 开发完成 · 待 QA |
| 超时与错误码 | 502 / 503 / 504 映射，上游 401 不泄露细节 | BFF `upstream errors...` | 开发完成 · 待 QA |
| TLS | 公网必须 https；拒绝 `NODE_TLS_REJECT_UNAUTHORIZED=0`；Python 端使用默认证书校验 | BFF `config: TLS...` | 开发完成 · 待 QA |
| 接口版本不兼容 | 拒绝服务，不降级 | BFF `schema mismatch` | 开发完成 · 待 QA |
| 镜像与编排 | 两个 Dockerfile + compose，CI 构建镜像并做冒烟 | CI `docker-images` | 开发完成 · 待 QA |
| 真实 HTTPS Staging、真机验证 | 见 RUNBOOK §4 | — | **外部阻塞**：需在 App Builder 与 Python 托管平台部署 |
