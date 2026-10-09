# FIR-31 V6 — App Builder Fullstack 重构

## 架构
```
Frontend (Mobile-first HTML/JS)
    ↓ API
Node.js Express BFF (App Builder)
    ↓ HTTP API
Python FastAPI Analysis Service
    ↓ 调用
Python Canonical Core (core/swing_pivot.py, core/pivot_patterns.py)
```

**核心原则**: Python是唯一算法Source of Truth，Node/JS不实现任何形态识别算法。

## 项目结构
```
v6/
├── python-service/          # Python FastAPI Analysis Service
│   ├── main.py              # FastAPI入口，包含全部API
│   ├── requirements.txt     # Python依赖
│   └── watchlist.json       # 自选股数据（运行时生成）
└── node-bff/                # Node.js Express BFF (App Builder部署)
    ├── server.js            # BFF入口，透传到Python Service
    ├── package.json         # Node依赖
    └── public/
        └── index.html       # Mobile-first前端页面
```

## 功能清单
- ✅ 个股形态分析（Swing Pivot + 4种形态）
- ✅ 大盘环境分析
- ✅ 自选股池管理（增删查）
- ✅ 批量分析自选股
- ✅ 市场扫描（自定义股票列表）
- ✅ 同花顺概念板块（8个内置板块）
- ✅ 多设备同步（GitHub config）
- ✅ 移动端适配（响应式设计）
- ✅ K线图展示（ECharts，标注颈线/头/肩）

## API端点
### Node BFF (端口3000)
- `GET /api/health` - 健康检查
- `GET /api/analyze/{code}` - 个股分析
- `GET /api/market-env` - 大盘环境
- `GET /api/watchlist` - 自选股列表
- `POST /api/watchlist/{code}` - 添加自选股
- `DELETE /api/watchlist/{code}` - 删除自选股
- `POST /api/watchlist/analyze` - 批量分析
- `POST /api/scan` - 市场扫描
- `GET /api/sectors` - 板块列表
- `POST /api/sector/{code}/scan` - 板块扫描
- `GET /api/sync/config` - 同步配置
- `POST /api/sync/pull` - 从GitHub拉取

### Python Service (端口8001)
- `GET /health` - 健康检查
- `GET /analyze/{code}` - 个股分析
- `GET /market-env` - 大盘环境
- `GET /watchlist` - 自选股
- `POST /watchlist/{code}` - 添加
- `DELETE /watchlist/{code}` - 删除
- `POST /watchlist/analyze` - 批量分析
- `POST /scan` - 市场扫描
- `GET /sectors` - 板块列表
- `POST /sector/{code}/scan` - 板块扫描
- `GET /sync/config` - 同步配置
- `POST /sync/pull` - 从GitHub拉取

## 本地开发
```bash
# 1. 启动Python Service
cd v6/python-service
pip install fastapi uvicorn
python main.py  # 端口8001

# 2. 启动Node BFF（另一个终端）
cd v6/node-bff
npm install
node server.js  # 端口3000

# 3. 访问
open http://localhost:3000
```

## 部署
### Python Service部署
需要部署到公网可访问的位置：
- 云服务器（阿里云/腾讯云/AWS）
- Serverless平台（Render/Railway）
- 其他支持Python的平台

部署后设置环境变量：
```bash
export PYTHON_SERVICE_URL=https://your-python-service.com
```

### Node BFF部署 (App Builder)
通过App Builder部署fullstack应用，设置PYTHON_SERVICE_URL环境变量指向Python Service。

## 验证结果 (603068 博通集成)
- 数据: 370根K线
- 最新收盘: 43.50
- W底: 8个, 最强100分, 颈线34.70
- 头肩顶: 2个, 最强100分, 颈线43.52
- 推荐度: A级·强烈推荐
- 引擎: python-canonical-core v5.2.0

## 版本
- V6.0.0 (2026-10-09)
- Core版本: 5.2.0
- 分支: v6/fullstack-app-builder
