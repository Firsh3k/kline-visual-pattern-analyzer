#!/bin/bash
# FIR-31 P0 Web Recovery - 启动脚本
# Python Canonical Core + FastAPI + Mobile Web

cd "$(dirname "$0")"

echo "============================================================"
echo "K线形态分析工具 v6.0 P0 (Python Canonical Core)"
echo "============================================================"

# 检查依赖
if ! python3 -c "import fastapi" 2>/dev/null; then
    echo "安装依赖..."
    pip3 install -r requirements.txt
fi

# 启动服务
echo "启动 FastAPI 服务..."
echo "访问地址: http://localhost:8000"
echo "API文档: http://localhost:8000/docs"
echo "============================================================"

python3 main.py
