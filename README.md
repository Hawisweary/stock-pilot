# Stock Pilot

面向 A 股的个人**量化投研 + 模拟交易平台**：从多源数据采集、十维综合评分、因子挖掘与回测，到策略化模拟盘与自动调度，构成一个完整闭环。

> 桌面端（Tauri）对外显示名为 **Stock Pilot**；代码 / 数据库等底层标识符沿用历史代号 `afr`。

---

## 核心能力

### 📊 V5 十维评分体系
对全市场个股做十个维度的档位打分（−2…+2），加权合成为 `composite_v5`，并带**短板惩罚**与**一票否决**：

| 维度 | 说明 |
|------|------|
| 基本面 | 盈利 / 成长 / 偿债 / 现金流的全市场百分位 |
| 质量因子 | 应计、现金质量、ROE 稳定性等 |
| 行业景气 | 行业资金流与相对强度 |
| 资金面 | 主力净流入、换手、量能、股东户数 |
| 估值 | PE/PB/PS 相对行业与自身历史分位 |
| 技术面 | 均线 / MACD / RSI / 量价形态规则引擎 |
| 大盘环境 | 指数趋势与市场广度 |
| 政策面 | 行业政策事件影响 |
| 新闻面 | 个股新闻情绪（LLM + 关键词） |
| 情绪面 | 波动率 + 动量的横截面分位 |

### 🧪 因子实验室
- 70+ 因子：评分维度因子、技术因子（动量 / 低波 / 量价 / 反转 / 趋势）、合成因子、GP 遗传规划挖掘、自定义时序表达式
- IC / IR 分析、分层单调性、多空净值、IC 衰减、相关矩阵
- 因子合成（等权 / IC_IR 加权 / 滚动最优）
- ML / Qlib 预测接入

### 📈 回测引擎
T+1 开盘价成交模型、可配置滑点与费率、涨跌停约束、交易日历调仓；输出累计收益、最大回撤、Sharpe（含无风险利率）、Information Ratio、月度热力图、持仓分布。

### 💼 模拟交易（模拟盘）
多策略组合（综合 / 动量 / 技术 / 估值 / 质量 / 资金 / 行业轮动 / 海龟…），自动调仓、海龟通道出场、实时 / EOD 定价、滑点与手续费、FIFO 已实现盈亏。

### 🔬 Alpha 因子 v1
盈余惊喜、三方资金共振（L2 大单 + 龙虎榜 + 沪深股通 ≥2 路同向）、行业中性估值——作为独立高确信信号，不并入 V5 合成分。

---

## 数据源

- **Tushare Pro**（主）：日线行情、财务、估值、资金流、龙虎榜、指数、宏观、停复牌、行业分类
- **AKShare**（补充）：筹码分布、创新高新低统计、龙虎榜多周期、市场总貌等 Tushare 该额度未覆盖的数据
- **DeepSeek LLM**：新闻情绪分析、公告事件分类

行情主源为 Tushare 按日批量拉取（快、稳、不受 eastmoney 反爬影响），失败自动回退 tencent / eastmoney 逐股路径。

---

## 技术架构

```
┌─ 桌面壳 (Tauri / Rust)  →  加载 http://127.0.0.1:3002
├─ 前端  (Next.js 16, :3002)  →  /api/* 反向代理到后端
└─ 后端  (FastAPI, :8800, venv-quant / Python 3.11)
     ├─ SQLite 主库 afr.db（WAL）+ 缓存库 cache.db（日志 / 健康类高频小写入解耦）
     ├─ 调度器：日(15:30) / 夜间(02:00) / 周度 自动流水线
     │    · 状态持久化 + 错过窗口自动补跑
     │    · 重任务子进程隔离，不阻塞 API
     │    · WAL 看门狗防止无限膨胀
     └─ launch.sh 保活守护（keep-alive daemon）
```

---

## 快速开始

### 依赖
- Python 3.11（项目自带 `venv-quant`）
- Node.js（前端）
- Tushare Pro token、DeepSeek API key

### 配置密钥
复制 `.env.example` 为 `.env` 并填入（**切勿提交 `.env` / `.envrc`，已 gitignore**）：
```bash
DEEPSEEK_API_KEY=sk-...
TUSHARE_TOKEN=...
```

### 启动
```bash
# 一键启动后端 + 前端 + 保活守护（推荐）
./launch.sh daemon

# 查看状态 / 停止
./launch.sh status
./launch.sh stop
```
- 前端：http://localhost:3002
- 后端 API 文档：http://localhost:8800/docs

桌面端：构建后运行 `/Applications/Stock Pilot.app`（Tauri 会自动拉起 `launch.sh daemon`，并在前端就绪后跳转）。

---

## 目录结构

```
backend/      FastAPI 后端（api/ 路由、services/ 业务、scripts/ 批处理、migrations.py）
frontend/     Next.js 前端（app/ 页面、components/、lib/api.ts）
src-tauri/    Tauri 桌面壳（Rust）
data/         SQLite 数据库与备份（gitignored）
launch.sh     一键启动 / 保活脚本
```

---

## 说明

个人研究用途，**非投资建议**。数据依赖第三方接口的可用性与额度。
