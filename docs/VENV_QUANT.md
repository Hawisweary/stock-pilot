# Python 3.11 量化子环境（venv-quant）

Qlib、qars3（QARS2 Rust 核心）等依赖 **不支持 Python 3.14**，需在独立 venv 中运行 worker 子进程。

## 创建

```bash
brew install libomp python@3.11   # LightGBM 需要 libomp
cd ai-fundamental-researcher
bash scripts/setup_venv_quant.sh
venv-quant/bin/pip install pyqlib   # 可选，已验证 0.9.7
```

**后端与 `backend/scripts/` 均应使用 `venv-quant`（Python 3.11）**，不要用 macOS 自带的 Python 3.9。

```bash
./launch.sh start                              # 后端已绑定 venv-quant
bash backend/scripts/run_py.sh scripts/xxx.py  # 脚本包装器
```

详见 README「十、Python 运行环境」。

## QARS2 / qars3 安装（官方文档 vs 当前环境）

官方文档两种安装方式在本机实测结果（2026-05-31）：

| 方式 | 命令 | 结果 |
|------|------|------|
| 方式1 PyPI | `pip install qars3` | ❌ PyPI **无** `qars3` 包 |
| 方式2 源码 | `git clone https://github.com/yutiansut/qars2.git` | ❌ 仓库 **404/私有**，无法克隆 |
| QUANTAXIS zip | `QUANTAXIS-master.zip` | ⚠️ 仅含 Python 桥接层，**不含** qars3 源码 |

**Rust 工具链** 已就绪（`rustc 1.96` / `cargo 1.96`），拿到 qars2 源码后可 `pip install -e .` 编译。

### 验证

```bash
venv-quant/bin/python scripts/verify_qars_support.py
```

或（需先 `pip install -e third_party/quantaxis-src/QUANTAXIS-master`）：

```python
from QUANTAXIS.QARSBridge import has_qars_support
print("✅ QARS2已安装" if has_qars_support() else "❌ QARS2未安装")
```

### 可行路径

1. 从 QuantAxis 社区获取 **qars2 源码 zip**（不是 QUANTAXIS-master.zip）
2. 放到 `third_party/` 后：

```bash
bash scripts/install_qars_from_quantaxis.sh third_party/qars2-master.zip
```

3. 若有 qars2 仓库访问权限，在本机 Terminal：

```bash
cd third_party && git clone https://github.com/yutiansut/qars2.git
venv-quant/bin/pip install -e third_party/qars2
venv-quant/bin/python scripts/verify_qars_support.py
```

未安装 qars3 时，Rust 回测 worker 自动 **fallback** 到 `python_compat`（与 Python 回测一致）。

## 配置 `.env`

```env
AFR_VENV_QUANT_PYTHON=/path/to/ai-fundamental-researcher/venv-quant/bin/python
AFR_QLIB_ENABLED=false
AFR_RUST_BACKTEST_APPROVED=false
```

## 原则

- FastAPI 主进程（3.14）**禁止** `import qlib` / `import qars3`
- 训练/回测通过 `workers/*.py` + `subprocess` 调用
- Worker stdout 最后一行必须是 JSON
