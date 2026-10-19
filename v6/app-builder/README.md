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

- **Python 是唯一形态算法来源**；Node/JS 不重新实现形态识别。
- 通用技术指标（MA/RSI/KDJ/MACD/布林）、大盘环境可在前端本地计算；
  但 **W底/M头/头肩底/头肩顶 + 推荐度以 Python Core 为准**。

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
| `server/modules/stock-analysis/v4-page.controller.ts` | 托管 V4 HTML |
| `client/public/v4/FRONTEND_PATCHES.md` | V4 HTML 上 Python Core 集成 + 三道防线的受控改动 |

> 完整 5363 行 `client/public/v4/index.html` 在 App Builder 沙箱；本目录用
> `FRONTEND_PATCHES.md` 完整记录相对 V4 原版（tag `v4.0.0`）的全部受控改动。

## 部署后验收（Production Gate）

每次部署/修改后，必须走真实生产路径（非 mock），全部 PASS 才放行：

1. `/api/health` → 200，`core_analyzer=real_core`
2. `/api/analyze/603068` → 200，`engine.analyzer=real_core`
3. 603068：页面 8 种形态与返回文档 `summary.patterns_latest`（每种形态最新实例）逐项一致；行情每天变化，不写死具体分数
4. 不存在任何静默降级路径（后端 fail-closed + 前端红色错误面板）
5. 页面正常加载（非"页面待开发"、非 404）

算法层的防漂移由仓库根 `tests/test_golden_patterns.py`（CI 黄金测试）锁定。
