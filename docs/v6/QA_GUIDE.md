# V6 QA 验收指南

**先在本地跑一遍，再验 Staging。** 所有判定都以 Python 引擎的输出为准，网页只负责展示。

## 1. 本地一键复现（与 CI 相同）

```bash
pip install -r services/analysis_api/requirements-dev.txt playwright==1.56.0 && python -m playwright install chromium
(cd apps/bff && npm ci)
python -m pytest tests/v5 tests/pipeline -q -k "not Integration"   # core / 管线
(cd services/analysis_api && python -m pytest tests -q)             # API 37 项
(cd apps/bff && npm test)                                           # BFF 7 组
python -m pytest tests/e2e -q                                       # 手机 + PC 真浏览器 11 项，截图写入 docs/v6/evidence/
```

注意：引擎与对照计算需使用同一 CPython 版本（3.10）。不同版本只会导致结果哈希不同，数值仍在容差内。

## 2. Staging 清单（手机 Safari / Android Chrome / PC Chrome 各一遍）

| # | 操作 | 期望 |
|---|---|---|
| 1 | 未登录打开网址 | 显示登录页。直接访问 `/api/v1/watchlist` 返回 401 |
| 2 | 登录后进入“个股”，输入 603068 | 显示真实 K 线和 250 根交易日。顶部显示截至日期、收盘或盘中状态、前复权标识。溯源区显示 dataHash、结果哈希和 requestId |
| 3 | 点选每个形态 | 图上出现关键点、颈线、目标线和止损线；判定证据与阶段说明一致。均线类显示为“趋势状态” |
| 4 | 输入 920001、123、300999 等代码 | 422、404、503 都显示红色错误框和 requestId，不显示任何推测结果 |
| 5 | 自选股：手机添加 A、B；电脑刷新 | 电脑能看到 A、B |
| 6 | 电脑删除 B；手机刷新 | B 不会再出现 |
| 7 | 自选股 → 批量分析全部 | 显示进度，结果卡片可跳转到详情 |
| 8 | 扫描：自定义股票池，含重复代码和无效代码 | 自动去重；失败清单列出原因；结果卡片显示所属板块 |
| 9 | 板块：添加动态板块 886032 | 状态为“待验证 · 禁止扫描”，扫描页中该板块不可勾选；页面给出可复制的验证指令 |
| 10 | 提交权威快照（来源 + 当日日期 + 成分股） | 状态变为“已验证 · 可扫描”，可以发起扫描。提交 8 天前的快照则变为“已过期”，再次禁止扫描 |
| 11 | 图鉴 | 8 个形态。历史统计显示样本量；均线 / 平台显示 N/A；页面上没有“可靠性 xx%” |
| 12 | 版本页 | 网页与引擎各自显示版本和 SHA；元数据缺失时显示 Version Unknown。兼容性显示一致 |
| 13 | 手机横竖屏、缩放图表、长按十字线 | 页面不出现横向滚动；图表可以双指缩放 |
| 14 | `BASE=… ./deploy/smoke.sh` | 输出“全部通过” |

## 3. 逐字段一致性（FIR-31 §10）

E2E 用例 `test_stock_page_parity_with_core` 读取网页 DOM 中的精确值（`data-v`）和显示值，与同一输入直接调用 `core.analysis.analyze` 的结果逐项比较，价格容差为 1e-4。比较字段：

- 形态有无
- 方向
- 阶段
- 分数
- 置信等级
- 关键点：名称、日期、价格
- 颈线
- 突破价
- 目标
- 止损

此外还比较 dataHash 与结果哈希。

Staging 上可以在浏览器开发者工具中调用 `/api/v1/analyses`，与页面显示的值对照。
