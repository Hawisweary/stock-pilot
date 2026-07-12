#!/usr/bin/env bash
# 从 quantaxis-MASTER.zip 安装 qars3（QARS2 Rust 核心）
# 用法:
#   cp ~/Downloads/quantaxis-MASTER.zip third_party/
#   bash scripts/install_qars_from_quantaxis.sh
# 或:
#   bash scripts/install_qars_from_quantaxis.sh ~/Downloads/quantaxis-MASTER.zip
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="${VENV_QUANT:-$ROOT/venv-quant}"
PIP="$VENV/bin/pip"
PY="$VENV/bin/python"

resolve_zip() {
  if [[ $# -ge 1 && -f "$1" ]]; then
    echo "$1"
    return
  fi
  for candidate in \
    "$ROOT/third_party/QUANTAXIS-master.zip" \
    "$ROOT/third_party/quantaxis-MASTER.zip" \
    "$HOME/Downloads/QUANTAXIS-master.zip" \
    "$HOME/Downloads/quantaxis-MASTER.zip"; do
    if [[ -f "$candidate" ]]; then
      echo "$candidate"
      return
    fi
  done
  echo ""
}

ZIP="$(resolve_zip "${1:-}")"
if [[ -z "$ZIP" ]]; then
  echo "找不到 quantaxis / QUANTAXIS zip" >&2
  echo "请复制到: $ROOT/third_party/QUANTAXIS-master.zip" >&2
  echo "  cp ~/Downloads/QUANTAXIS-master.zip $ROOT/third_party/" >&2
  exit 1
fi

if [[ ! -x "$PY" ]]; then
  echo "venv-quant 不存在，先运行: bash scripts/setup_venv_quant.sh" >&2
  exit 1
fi

EXTRACT="$ROOT/third_party/quantaxis-src"
rm -rf "$EXTRACT"
mkdir -p "$EXTRACT"
echo "解压: $ZIP -> $EXTRACT"
unzip -q "$ZIP" -d "$EXTRACT"

find_qars_dir() {
  local base="$1"
  local d
  for d in qars2 qars3 QARS2 QARS3; do
    if [[ -f "$base/$d/pyproject.toml" || -f "$base/$d/Cargo.toml" ]]; then
      echo "$base/$d"
      return 0
    fi
  done
  # zip 可能带一层 quantaxis-MASTER/ 前缀
  for top in "$base"/*; do
    [[ -d "$top" ]] || continue
    for d in qars2 qars3 QARS2 QARS3; do
      if [[ -f "$top/$d/pyproject.toml" || -f "$top/$d/Cargo.toml" ]]; then
        echo "$top/$d"
        return 0
      fi
    done
  done
  # 按 pyproject 包名 qars3 搜索
  while IFS= read -r f; do
    if grep -q 'name\s*=\s*"qars3"' "$f" 2>/dev/null; then
      dirname "$f"
      return 0
    fi
  done < <(find "$base" -name pyproject.toml 2>/dev/null)
  return 1
}

QARS_DIR=""
if ! QARS_DIR="$(find_qars_dir "$EXTRACT")"; then
  QA_ROOT=""
  for candidate in "$EXTRACT"/QUANTAXIS-master "$EXTRACT"/QUANTAXIS "$EXTRACT"/*; do
    [[ -f "$candidate/setup.py" && -d "$candidate/QUANTAXIS/QARSBridge" ]] && QA_ROOT="$candidate" && break
  done
  echo "" >&2
  echo "========================================" >&2
  echo "此 zip 仅含 QUANTAXIS Python 桥接层，不含 qars3 Rust 源码。" >&2
  echo "QARSBridge 依赖独立包 qars3（PyPI 暂无，需 qars2 源码编译）。" >&2
  echo "" >&2
  echo "请另提供 qars2 / qars3 源码 zip，例如:" >&2
  echo "  ~/Downloads/qars2-master.zip" >&2
  echo "  或从 QuantAxis 社区获取后放到 third_party/" >&2
  echo "" >&2
  echo "然后运行:" >&2
  echo "  bash scripts/install_qars_from_quantaxis.sh third_party/qars2-master.zip" >&2
  echo "========================================" >&2
  if [[ -n "$QA_ROOT" ]]; then
    echo "已解压 QUANTAXIS 到: $QA_ROOT（供参考，非 Rust 核心）" >&2
  fi
  echo "zip 顶层:" >&2
  ls -la "$EXTRACT" >&2
  exit 1
fi
echo "QARS 源码: $QARS_DIR"

ensure_rust() {
  if command -v cargo >/dev/null 2>&1 && cargo --version >/dev/null 2>&1; then
    echo "Rust: $(cargo --version)"
    return
  fi
  echo "未检测到可用 Rust，尝试安装 rustup..."
  if ! command -v rustup >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable
  fi
  # shellcheck disable=SC1091
  source "$HOME/.cargo/env" 2>/dev/null || true
  rustup default stable 2>/dev/null || true
  if ! cargo --version >/dev/null 2>&1; then
    echo "Rust 安装失败，请手动: curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh" >&2
    exit 1
  fi
  echo "Rust: $(cargo --version)"
}

ensure_rust

"$PIP" install -U pip wheel maturin
echo "编译安装 qars3..."
(cd "$QARS_DIR" && "$PIP" install -e .)

# 可选: qadataswap
DATASWAP=""
for candidate in \
  "$QARS_DIR/libs/qadataswap" \
  "$QARS_DIR/../qadataswap" \
  "$(dirname "$QARS_DIR")/qadataswap"; do
  if [[ -f "$candidate/pyproject.toml" ]]; then
    DATASWAP="$candidate"
    break
  fi
done
if [[ -n "$DATASWAP" ]]; then
  echo "安装 qadataswap: $DATASWAP"
  (cd "$DATASWAP" && "$PIP" install -e .) || echo "qadataswap 安装跳过（非必需）"
fi

echo ""
echo "验证 qars3..."
"$PY" - <<'PY'
import json
try:
    import qars3
    print(json.dumps({
        "ok": True,
        "qars3_version": getattr(qars3, "__version__", "unknown"),
        "has_QA_QIFIAccount": hasattr(qars3, "QA_QIFIAccount"),
        "has_Backtest": hasattr(qars3, "Backtest"),
    }, ensure_ascii=False))
except ImportError as e:
    print(json.dumps({"ok": False, "error": str(e)}))
    raise SystemExit(1)
PY

echo ""
echo "完成。请确认 backend/.env 中:"
echo "  AFR_VENV_QUANT_PYTHON=$PY"
echo "  AFR_RUST_BACKTEST_APPROVED=true"
