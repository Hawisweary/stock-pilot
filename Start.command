#!/bin/bash
# 双击此文件可在 macOS 终端中启动前后端（窗口需保持打开）
cd "$(dirname "$0")"
chmod +x ./launch.sh
./launch.sh stop 2>/dev/null
echo ""
echo "正在启动 AI 基本面研究员..."
echo "关闭此终端窗口将停止服务。"
echo ""
./launch.sh start prod
