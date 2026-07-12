#!/bin/bash
# AI基本面研究员 - 一键安装脚本
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"

echo "=== AI 基本面研究员 - 环境安装 ==="
echo "项目目录: $PROJECT_DIR"

# 1. 安装 Python 依赖
echo ""
echo "[1/4] 安装 Python 依赖..."
cd "$BACKEND_DIR"
python3 -m pip install -r requirements.txt -q 2>&1 | tail -1
python3 -m pip install yfinance -q 2>&1 | tail -1
echo "  Python 依赖安装完成"

# 2. 初始化数据库
echo ""
echo "[2/4] 初始化数据库..."
cd "$BACKEND_DIR"
python3 -c "
from database import init
init()
print('  数据库初始化完成')
"

# 3. 插入种子数据
echo ""
echo "[3/4] 插入种子股票数据..."
cd "$PROJECT_DIR/scripts"
python3 seed_test_stocks.py

# 4. 启动提示
echo ""
echo "[4/4] 安装完成！"
echo ""
echo "启动后端服务："
echo "  cd $BACKEND_DIR && python3 app.py"
echo ""
echo "API 文档地址："
echo "  http://localhost:8800/docs"
echo ""
echo "开始数据抓取："
echo "  curl -X POST http://localhost:8800/api/data/fetch-all"
echo ""
