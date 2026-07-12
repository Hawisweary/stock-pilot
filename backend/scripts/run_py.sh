#!/usr/bin/env bash
# 使用项目 venv-quant 运行 backend 脚本（避免误用 macOS 自带 Python 3.9）
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON="${VENV_QUANT:-$ROOT/venv-quant/bin/python}"

if [[ ! -x "$PYTHON" ]]; then
  echo "错误: 未找到 venv-quant ($PYTHON)" >&2
  echo "请先执行: bash scripts/setup_venv_quant.sh" >&2
  echo "并安装依赖: venv-quant/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi

cd "$ROOT/backend"
exec "$PYTHON" "$@"
