# V6 质量闸门（Quality Gate）— 自动检查与发布信号

> 分支：`v6/web-recovery-stable`（V6 所有更新只在此分支，完善后再合并 main，不直接动生产）
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

## 第二层：部署后 — 生产路径验收（部署后自动执行，给信号）

每次 App Builder 部署/修改后，由助手在沙箱内走**真实生产请求**（非 mock），逐项核对：

- [ ] `/api/health` → 200，`core_analyzer=real_core`
- [ ] `/api/analyze/603068` → 200，`engine.analyzer=real_core`
- [ ] 603068 底部最佳 = W底（w_bottom=85，hs_bottom=0）
- [ ] 603068 顶部最佳 = 头肩顶（hs_top=100）
- [ ] 不存在静默降级（后端 fail-closed + 前端红色错误面板）
- [ ] 页面正常加载（非"页面待开发"、非 404）

全部通过才给 **✅ PRODUCTION GATE PASS**；任一失败给 **❌ FAIL** 并列出阻断项。

> 说明：App Builder 应用所有请求需豆包登录态，外部独立浏览器/curl 无法免登录黑盒探测，
> 因此部署验收在沙箱内走真实 HTTP（basePath + CSRF + credentials）完成。
> 如需独立真实浏览器基线，需负责人在自动化浏览器扫码登录一次（可选）。

## fail-closed 原则（三层一致）

| 层 | 失败行为 |
|----|----------|
| `python_analyzer.py` | core 失败 → 非零退出 + 错误，绝不返回随机/骨架假数据（骨架仅 `--allow-skeleton` 本地开发） |
| NestJS `stock-analysis.service.ts` | Python 非零退出 → reject；并拒绝 `engine.analyzer != real_core` 的返回 |
| 前端 V4 HTML | API 失败 → 红色错误面板，不渲染形态；用户主动点"JS降级"才走本地 JS |

## 603068 基准（禁止漂移）

| 指标 | 正确值 |
|------|------|
| W底 | 85 S级，已突破，颈线 41.41，目标 47.81 |
| 头肩底 | 0，detected=false（绝不可显示） |
| M头 | 65 A级，形成中 |
| 头肩顶 | 100 S级，回踩颈线，颈线 43.57 |
| attention | 42，偏空 |
