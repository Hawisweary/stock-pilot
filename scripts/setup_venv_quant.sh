#!/usr/bin/env bash
# 创建 Python 3.11 量化子环境（LightGBM / 可选 pyqlib / qars2）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/venv-quant"

PY311=""
for c in /opt/homebrew/bin/python3.11 python3.11 python3.12 python3.14 python3; do
  if command -v "$c" >/dev/null 2>&1; then
    PY311=$c
    [[ "$c" == *3.11* ]] && break
  fi
done
if [[ -z "$PY311" ]]; then
  echo "Need Python 3.11 (brew install python@3.11)" >&2
  exit 1
fi

"$PY311" -m venv "$VENV"
"$VENV/bin/pip" install -U pip wheel
"$VENV/bin/pip" install numpy pandas scipy scikit-learn lightgbm
# 可选（需 Python 3.10–3.12）:
# "$VENV/bin/pip" install pyqlib
# qars3 无 PyPI 包，从 QuantAxis 源码 zip 安装:
#   cp ~/Downloads/quantaxis-MASTER.zip third_party/
#   bash scripts/install_qars_from_quantaxis.sh
echo "venv-quant ready: $VENV/bin/python"
echo "Set AFR_VENV_QUANT_PYTHON=$VENV/bin/python in backend/.env"
echo "QARS: bash scripts/install_qars_from_quantaxis.sh  (needs quantaxis-MASTER.zip)"
