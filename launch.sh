#!/bin/bash
# ==============================================
# AI 基本面研究员 - 一键启动脚本（带进程保活）
# 用法:
#   ./launch.sh daemon       # 推荐：后台保活 + 生产前端（更稳）
#   ./launch.sh daemon dev   # 开发模式前端（热更新，易崩溃）
#   ./launch.sh stop|status
# ==============================================

# 确保 node/npm/npx/python/lsof 可被 nohup 子进程找到（Tauri/沙箱环境 PATH 不完整;
# /usr/sbin 必须显式加入,否则 lsof 找不到导致保活永远误判服务离线而无限重启）
export PATH="/usr/local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR/backend"
FRONTEND_DIR="$SCRIPT_DIR/frontend"
PID_DIR="$SCRIPT_DIR/.pids"
BACKEND_PID="$PID_DIR/backend.pid"
FRONTEND_PID="$PID_DIR/frontend.pid"
DAEMON_PID="$PID_DIR/daemon.pid"
FRONTEND_MODE_FILE="$PID_DIR/frontend.mode"
BACKEND_PORT=8800
FRONTEND_PORT=3002

mkdir -p "$PID_DIR"

PYTHON="${VENV_QUANT:-$SCRIPT_DIR/venv-quant/bin/python}"
if [ ! -x "$PYTHON" ]; then
    PYTHON="/usr/local/bin/python3"
fi

# 默认生产模式（无 HMR，不易崩溃）
FRONTEND_MODE="${FRONTEND_MODE:-prod}"

port_pids() {
    local port=$1
    [ -n "$port" ] || return 0
    lsof -ti ":$port" 2>/dev/null || true
}

is_backend_up() {
    [ -n "$(port_pids "$BACKEND_PORT")" ]
}

is_frontend_up() {
    [ -n "$(port_pids "$FRONTEND_PORT")" ]
}

start_backend() {
    if is_backend_up; then
        echo "  后端已在运行 (Port: $BACKEND_PORT)"
        port_pids "$BACKEND_PORT" | head -1 > "$BACKEND_PID"
        return 0
    fi
    cd "$BACKEND_DIR"
    unset HTTP_PROXY HTTPS_PROXY http_proxy https_proxy ALL_PROXY all_proxy
    nohup "$PYTHON" -u app.py >> "$PID_DIR/backend.log" 2>&1 &
    echo $! > "$BACKEND_PID"
    disown 2>/dev/null || true
    for _ in $(seq 1 30); do
        sleep 0.5
        if is_backend_up; then
            port_pids "$BACKEND_PORT" | head -1 > "$BACKEND_PID"
            echo "  后端已启动 (PID: $(cat "$BACKEND_PID"), Port: $BACKEND_PORT)"
            return 0
        fi
    done
    echo "  后端启动失败，见 $PID_DIR/backend.log"
    return 1
}

start_frontend_dev() {
    cd "$FRONTEND_DIR"
    ulimit -n 65536 2>/dev/null || ulimit -n 10240 2>/dev/null || true
    export WATCHPACK_POLLING=true
    export CHOKIDAR_USEPOLLING=true
    nohup npx next dev -p "$FRONTEND_PORT" -H 0.0.0.0 </dev/null >> "$PID_DIR/frontend.log" 2>&1 &
    echo $! > "$FRONTEND_PID"
    disown 2>/dev/null || true
    echo "dev" > "$FRONTEND_MODE_FILE"
    for _ in $(seq 1 40); do
        sleep 0.5
        if is_frontend_up; then
            port_pids "$FRONTEND_PORT" | head -1 > "$FRONTEND_PID"
            echo "  前端已启动 [开发] (PID: $(cat "$FRONTEND_PID"), Port: $FRONTEND_PORT)"
            return 0
        fi
    done
    echo "  前端启动失败，见 $PID_DIR/frontend.log"
    return 1
}

start_frontend_prod() {
    cd "$FRONTEND_DIR"
    if [ ! -f ".next/BUILD_ID" ]; then
        echo "  首次构建前端（约 1–3 分钟）..."
        npm run build >> "$PID_DIR/frontend.log" 2>&1 || {
            echo "  前端构建失败，见 $PID_DIR/frontend.log"
            return 1
        }
    fi
    nohup npx next start -p "$FRONTEND_PORT" -H 0.0.0.0 </dev/null >> "$PID_DIR/frontend.log" 2>&1 &
    echo $! > "$FRONTEND_PID"
    disown 2>/dev/null || true
    echo "prod" > "$FRONTEND_MODE_FILE"
    for _ in $(seq 1 30); do
        sleep 0.5
        if is_frontend_up; then
            port_pids "$FRONTEND_PORT" | head -1 > "$FRONTEND_PID"
            echo "  前端已启动 [生产] (PID: $(cat "$FRONTEND_PID"), Port: $FRONTEND_PORT)"
            return 0
        fi
    done
    echo "  前端启动失败，见 $PID_DIR/frontend.log"
    return 1
}

start_frontend() {
    if is_frontend_up; then
        echo "  前端已在运行 (Port: $FRONTEND_PORT)"
        port_pids "$FRONTEND_PORT" | head -1 > "$FRONTEND_PID"
        return 0
    fi
    if [ "$FRONTEND_MODE" = "dev" ]; then
        start_frontend_dev
    else
        start_frontend_prod
    fi
}

stop_pidfile() {
    local name=$1 pidfile=$2
    if [ -f "$pidfile" ] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
        kill "$(cat "$pidfile")" 2>/dev/null || true
        echo "  已停止 $name"
    fi
    rm -f "$pidfile"
}

stop_port() {
    local name=$1 port=$2 pidfile=$3
    local killed=0
    for pid in $(port_pids "$port"); do
        kill "$pid" 2>/dev/null && killed=1 || true
    done
    if [ "$killed" = "1" ]; then
        echo "  已停止 $name"
    fi
    rm -f "$pidfile"
}

keepalive_loop() {
    set +e
    while true; do
        sleep 20
        if ! is_backend_up; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') 后端离线，重启..." >> "$PID_DIR/daemon.log"
            start_backend >> "$PID_DIR/daemon.log" 2>&1
        fi
        if ! is_frontend_up; then
            echo "$(date '+%Y-%m-%d %H:%M:%S') 前端离线，重启..." >> "$PID_DIR/daemon.log"
            start_frontend >> "$PID_DIR/daemon.log" 2>&1
        fi
    done
}

case "${1:-daemon}" in
    start|daemon)
        MODE_ARG="${2:-}"
        if [ "$MODE_ARG" = "dev" ]; then
            FRONTEND_MODE=dev
        elif [ "$MODE_ARG" = "prod" ]; then
            FRONTEND_MODE=prod
        fi
        if [ "$1" = "daemon" ] && [ -f "$DAEMON_PID" ] && kill -0 "$(cat "$DAEMON_PID")" 2>/dev/null; then
            echo "保活已在运行 (PID: $(cat "$DAEMON_PID"))"
            ./launch.sh status
            exit 0
        fi
        echo "=== 启动服务 (前端: $FRONTEND_MODE) ==="
        start_backend || exit 1
        start_frontend || exit 1
        if [ "$1" = "daemon" ]; then
            nohup "$SCRIPT_DIR/launch.sh" _keepalive </dev/null >> "$PID_DIR/daemon.log" 2>&1 &
            echo $! > "$DAEMON_PID"
            disown 2>/dev/null || true
            echo ""
            echo "=== 已就绪（后台保活）==="
            echo "  前端: http://localhost:$FRONTEND_PORT  或  http://127.0.0.1:$FRONTEND_PORT"
            echo "  后端: http://localhost:$BACKEND_PORT"
            echo "  保活 PID: $(cat "$DAEMON_PID")"
        else
            echo ""
            echo "=== 已就绪（前台保活，Ctrl+C 退出）==="
            echo "  前端: http://localhost:$FRONTEND_PORT"
            keepalive_loop
        fi
        ;;

    _keepalive|_launchd)
        FRONTEND_MODE="$(cat "$FRONTEND_MODE_FILE" 2>/dev/null || echo prod)"
        if [ "$1" = "_launchd" ]; then
            start_backend >> "$PID_DIR/daemon.log" 2>&1 || true
            start_frontend >> "$PID_DIR/daemon.log" 2>&1 || true
        fi
        keepalive_loop
        ;;

    stop)
        echo "停止服务..."
        stop_pidfile "保活" "$DAEMON_PID"
        stop_port "后端" "$BACKEND_PORT" "$BACKEND_PID"
        stop_port "前端" "$FRONTEND_PORT" "$FRONTEND_PID"
        rm -f "$FRONTEND_MODE_FILE"
        echo "已全部停止"
        ;;

    status)
        echo "=== 服务状态 ==="
        if is_backend_up; then
            echo "  后端: 运行中 (PID: $(port_pids "$BACKEND_PORT" | head -1), Port: $BACKEND_PORT)"
            curl -s --max-time 3 http://127.0.0.1:$BACKEND_PORT/api/health >/dev/null && echo "    API: OK" || echo "    API: 异常"
        else
            echo "  后端: 未运行"
        fi
        if is_frontend_up; then
            MODE="$(cat "$FRONTEND_MODE_FILE" 2>/dev/null || echo unknown)"
            echo "  前端: 运行中 [$MODE] (PID: $(port_pids "$FRONTEND_PORT" | head -1), Port: $FRONTEND_PORT)"
            curl -s --max-time 3 -o /dev/null -w "    HTTP: %{http_code}\n" http://127.0.0.1:$FRONTEND_PORT 2>/dev/null || echo "    HTTP: 异常"
        else
            echo "  前端: 未运行"
        fi
        if [ -f "$DAEMON_PID" ] && kill -0 "$(cat "$DAEMON_PID")" 2>/dev/null; then
            echo "  保活: 运行中 (PID: $(cat "$DAEMON_PID"))"
        else
            echo "  保活: 未运行 → 请执行: ./launch.sh daemon"
        fi
        ;;

    *)
        echo "用法: ./launch.sh [daemon|daemon dev|daemon prod|start|start prod|stop|status]"
        echo "  或双击 Start.command（macOS）"
        echo "  或 scripts/install-macos-service.sh（开机自启）"
        exit 1
        ;;
esac
