# V4 HTML → Python Core 集成 + 三道防线（受控改动）

基准文件：V4 原版 `kline_analyzer.html`（git tag `v4.0.0`，4731 行）
线上文件：`client/public/v4/index.html`（5363 行，4731 + 下述约 632 行改动）

原则：Python Core 是形态识别 Source of Truth；JS 仅作为用户**主动点击**的降级手段。
所有改动为相对 V4 原版的增量，便于逐行审计。

---

## 一、API 调用层（basePath + CSRF + 不静默降级）

### `_getApiBasePath()`（约 600 行）
从 `window.location.pathname` 提取部署子路径（妙搭 `/app/<appId>/`），拼接 `/api`。
解决 iframe 内根绝对路径丢失 basePath 导致 404。

### `fetchPythonPatterns(code)`（约 605–662 行）
- `credentials: 'include'`：带 cookie
- 从 cookie `suda-csrf-token` 读取并回传 `X-Suda-Csrf-Token`（解决 403）
- `validateApiSchema()`：engine 必须不是 fallback/js，4 形态 + attention 字段齐全
- 失败返回 `{ ok:false, error, httpStatus }`，**不抛假数据、不本地补算**
- 完整 console 日志（`[PyAPI] →/✓/✗`、四形态分数、耗时）

---

## 二、形态覆盖层（Python 为准）

### `convertPythonPatternToV4(pyPattern, isBear)`（约 814–910 行）
把 Python PatternResult 转成 V4 渲染结构；`detected=false` 时返回 0 分空结构，
**关键点**：detected=false 的 0 分结果也必须覆盖本地 JS 假阳性（修复 603068 头肩底误判）。

### `mergePythonPatterns(localMatches, localBearMatches, pythonPatterns)`（约 913–935 行）
四个形态（w_bottom/hs_bottom/m_top/hs_top）**只要 Python 返回了 detected 布尔值就覆盖**，
无论分数高低。推荐度 attention 优先用 Python 的 `attention.score`（修复本地 80 vs Python 42 的方向冲突）。

---

## 三、三道防线

### 第一道：禁止静默降级（约 2784–2802 行）
`doAnalyze` 中 `if (!pythonResult) { ... return; }`：
- 直接 `return` 不渲染任何形态
- 红色错误面板显示错误原因 + HTTP 码
- 两个按钮：`retryAnalyze()`（重试）/ `forceJsAnalyze()`（用户主动降级）

### 第二道：健康检查 `checkEngineHealth()`（约 555–600 行）
页面加载 200ms 后自动 GET `/api/health`；
失败时 `showHealthBanner(true)` 显示红色横幅，引擎徽章变 🔴。

### 第三道：URL 自检 `?selftest=1`（约 4642–4646 行）
`runSelfTest()` 用例：
```js
{ code: '603068', name: '博通集成',
  expected: { bullBest: 'w_bottom', bearBest: 'hs_top', bullScore: 85, bearScore: 100, level: 'bear' } },
{ code: '600519', name: '贵州茅台', expected: { structureOnly: true } },
```

### 引擎徽章（约 3245–3254 行 + CSS 49–55 行）
三态：🟢 `core`（Python Core）/ 🟠 `fallback`（JS降级，带警告条）/ 🔴 `error`。

---

## 四、603068 基准（黄金断言，禁止漂移）

| 指标 | Python Core 正确值 |
|------|------|
| W底 | 85 分 S级，已突破，颈线 41.41，目标 47.81 |
| 头肩底 | 0 分，detected=false（**绝不可显示为头肩底**） |
| M头 | 65 分 A级，形成中 |
| 头肩顶 | 100 分 S级，回踩颈线，颈线 43.57 |
| attention | 42 分，偏空 |

该基准同时固化在仓库根 `tests/test_golden_patterns.py`（CI 自动跑）。
