# FIR-31 P0 Web Recovery

## 概述
恢复一个可以正常打开的网页，让用户在手机和电脑上输入股票代码，查看K线及Python算法的真实分析结果。

## 架构
```
Frontend (Mobile-first HTML/JS)
    ↓ API
FastAPI Backend
    ↓ 调用
Python Canonical Core (core/swing_pivot.py, core/pivot_patterns.py)
```

**核心原则**：Python是唯一算法Source of Truth，前端不包含任何形态检测算法。

## 项目结构
```
web/
├── main.py              # FastAPI 后端
├── requirements.txt     # Python 依赖
├── start.sh             # 启动脚本
├── watchlist.json       # 自选股数据（运行时生成）
└── static/
    └── index.html       # Mobile-first 前端页面
```

## API 端点
- `GET /api/health` - 健康检查
- `GET /api/analyze/{code}` - 个股形态分析
- `GET /api/market-env` - 大盘环境分析
- `GET /api/watchlist` - 自选股列表
- `POST /api/watchlist/{code}` - 添加自选股
- `DELETE /api/watchlist/{code}` - 删除自选股
- `POST /api/watchlist/analyze` - 批量分析

## 快速开始
```bash
cd web
pip install -r requirements.txt
python main.py
# 访问 http://localhost:8000
```

## 算法说明
- Swing Pivot Engine (FIR-12): left_bars=5, right_bars=5
- 4种形态检测: W底、M头、头肩底、头肩顶
- 推荐度评分: S/A/B/C/D 五级
- 数据来源: 新浪财经API

## 验证结果 (603068 博通集成)
- 数据: 370根K线
- 最新收盘: 43.50
- W底: 8个, 最强100分, 颈线34.70
- 头肩顶: 2个, 最强100分, 颈线43.52
- 推荐度: A级·强烈推荐

## 部署说明
### 本地部署
```bash
cd web && bash start.sh
```

### 公网部署选项
1. **云服务器**: 部署到支持Python的云服务器（阿里云/腾讯云/AWS）
2. **Serverless**: 使用支持Python的Serverless平台（Vercel/Render/Railway）
3. **Node BFF + Python Service**: Node.js Express作为BFF，调用独立Python FastAPI服务

### 注意
- app_builder目前不支持Python后端，如需使用app_builder部署，需采用Node BFF + Python Service架构
- 自选股数据保存在web/watchlist.json，部署时需确保目录可写

## 版本
- v6.0.0-p0 (2026-10-09)
- Core版本: 5.2.0
- 分支: v6/p0-python-web-recovery
