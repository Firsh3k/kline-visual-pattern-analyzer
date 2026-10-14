# V6：单一 Python 引擎 + Node BFF + 移动优先网页

| 目录 | 内容 |
|---|---|
| `core/` | 唯一算法实现。`core/analysis.py` 是所有入口共用的分析函数 |
| `services/analysis_api/` | FastAPI 分析服务：分析、大盘、自选股、板块、扫描任务、校准、版本；SQLite；迁移脚本 |
| `apps/bff/` | Express BFF：登录会话、CSRF、限流、超时、转发、静态网页 |
| `apps/web/` | 无框架网页（个股 / 自选股 / 扫描 / 板块 / 图鉴 / 版本） |
| `pipeline/run_daily.py` | 离线每日管线（同一 `core.analysis`） |
| `deploy/` | docker compose、环境变量模板、Staging 冒烟脚本 |
| `tests/e2e/` | 手机 + PC 真浏览器端到端测试 |

相关文档：
- [部署与回滚手册](RUNBOOK.md)
- [旧功能 → V6 对照表](FEATURE_MAPPING.md)
- [QA 验收指南](QA_GUIDE.md)
- 截图证据：`evidence/`

## 本地开发

```bash
# 1. Python 分析服务（fixture 行情，无需外网）
cd services/analysis_api
ANALYSIS_API_TOKEN=$(printf 'd%.0s' {1..40}) MARKET_DATA_PROVIDER=fixture FIXTURE_DIR=$PWD/fixtures \
  ANALYSIS_DATA_DIR=/tmp/kline-data python -m uvicorn --factory app.main:app_factory --port 8765
# 2. BFF + 网页
cd apps/bff && npm ci
NODE_ENV=development PORT=8080 PYTHON_API_BASE_URL=http://127.0.0.1:8765 \
  PYTHON_API_TOKEN=$(printf 'd%.0s' {1..40}) SESSION_SECRET=$(openssl rand -hex 32) LOGIN_USERNAME=owner \
  LOGIN_PASSWORD_HASH="$(printf '%s' 'local-password-123' | node src/hash-password.js)" node src/server.js
# 浏览器打开 http://localhost:8080 ，用 owner / local-password-123 登录；fixture 中可用代码：002436、600001、000002（非法数据）、300999（不可用）
```
