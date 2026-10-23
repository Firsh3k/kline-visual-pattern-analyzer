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
| `server/modules/stock-analysis/scan-jobs.ts` | 扫描后台任务（FIR-41）：`POST /api/scan/jobs` 提交、`GET /api/scan/jobs/:id` 查进度、`DELETE` 取消；全局最多 3 个分析并发（环境变量 `SCAN_MAX_PARALLEL`），单只超时，单次 ≤200 只；任务存内存，服务重启即清空 |
| `server/modules/stock-analysis/v4-page.controller.ts` | 托管 V4 HTML |
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
