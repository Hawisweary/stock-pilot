#!/bin/bash
# 安装 macOS 开机自启 + 崩溃自动重启（launchd）
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.afr.researcher.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.afr.researcher.plist"
UID_NUM="$(id -u)"

mkdir -p "$PROJECT_DIR/.pids"
chmod +x "$PROJECT_DIR/launch.sh"

# 先构建生产前端
if [ ! -f "$PROJECT_DIR/frontend/.next/BUILD_ID" ]; then
  echo "首次构建前端..."
  (cd "$PROJECT_DIR/frontend" && npm run build)
fi

sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PLIST_SRC" > "$PLIST_DST"
echo "已写入 $PLIST_DST"

launchctl bootout "gui/$UID_NUM/com.afr.researcher" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$PLIST_DST"
launchctl kickstart -k "gui/$UID_NUM/com.afr.researcher"

echo ""
echo "已安装并启动。访问: http://localhost:3002"
echo "查看状态: cd $PROJECT_DIR && ./launch.sh status"
echo "卸载: launchctl bootout gui/$UID_NUM/com.afr.researcher && rm $PLIST_DST"
