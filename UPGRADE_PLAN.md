# A 股投研系统 全面升级方案书

> 版本 v3.0 | 2026-05-23 | 总计 20 项升级 | 预估 42h

---

## 一、数据中台强化 (8h)

### 1.1 多源数据融合 (2h)

**目标**: 东财 + 同花顺 + 腾讯三源交叉验证

**技术方案**:

```python
# services/data_fusion.py
# 三源数据对比器
class MultiSourceFusion:
    def fetch_all(self, code):
        return {
            "tencent": self._tencent_quote(code),      # 腾讯行情 (已有)
            "eastmoney": self._eastmoney_quote(code),  # 东财 akshare
            "ths": self._tonghuashun_quote(code),      # 同花顺 iFinD
        }
    
    def cross_validate(self, sources):
        """交叉验证，标记异常"""
        prices = [s["price"] for s in sources if s]
        if len(prices) >= 2 and max(prices) - min(prices) > min(prices) * 0.05:
            return {"warning": "价格偏差>5%", "values": prices}
        return {"status": "ok"}
```

**数据库新增**:
```sql
ALTER TABLE stock_daily_quotes ADD COLUMN data_source TEXT DEFAULT 'tencent';
ALTER TABLE stock_daily_quotes ADD COLUMN cross_validated INTEGER DEFAULT 0;
```

**前端**: Dashboard 加「数据质量」小卡片（绿色=三源一致，黄色=偏差<5%，红色=异常）

### 1.2 财务报表解析 (3h)

**目标**: 从 `stock_daily_quotes` 已有数据中提取结构化报表

**数据表**:
```sql
CREATE TABLE financial_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER, report_date TEXT, report_type TEXT, -- 年报/半年报/季报
    revenue REAL, revenue_yoy REAL,           -- 营收 & 同比增长
    net_profit REAL, net_profit_yoy REAL,      -- 净利润 & 同比增长  
    total_assets REAL, total_liabilities REAL, -- 总资产/负债
    operating_cf REAL, investing_cf REAL,     -- 经营/投资现金流
    gross_margin REAL, net_margin REAL,        -- 毛利率/净利率
    roe REAL, roa REAL,                        -- ROE/ROA
    debt_ratio REAL, current_ratio REAL,       -- 负债率/流动比率
    UNIQUE(stock_id, report_date, report_type)
);
```

**数据来源**: akshare `stock_financial_abstract_ths`（同花顺财务摘要）

**前端**: 「财务数据」标签页改为表格形式，对比最近 4 个报告期

### 1.3 宏观指标集成 (3h)

**数据表**:
```sql
CREATE TABLE macro_indicators (
    date TEXT PRIMARY KEY,
    gdp REAL, gdp_yoy REAL,
    cpi REAL, cpi_yoy REAL,
    pmi_manufacturing REAL, pmi_services REAL,
    lpr_1y REAL, lpr_5y REAL,
    m2 REAL, m2_yoy REAL,
    shibor_overnight REAL, shibor_3m REAL
);
```

**数据来源**: akshare `macro_china_gdp`, `macro_china_cpi`, `macro_china_pmi`, `macro_china_lpr`, `macro_china_money_supply`

**集成方式**: 评分引擎新增 `macro_env_score`（宏观环境评分），纳入基本面或政策面

**前端**: Dashboard 新增「宏观仪表盘」卡片，显示核心指标当前值 + 趋势箭头

---

## 二、策略研究引擎 (10h)

### 2.1 因子有效性检验 (3h)

**目标**: IC（信息系统）分析每个因子的预测能力

**技术方案**:
```python
# services/factor_ic.py
def compute_factor_ic(stock_ids, factor_scores, forward_returns, periods=[5,20,60]):
    """
    IC = correl(当期因子分, 未来N日收益)
    IR = mean(IC) / std(IC)  → IR>0.5 有效，IR<0.1 淘汰
    """
    results = {}
    for period in periods:
        ic_series = []
        for date in trade_dates:
            ic = pearsonr(factor_scores[date], forward_returns[date][period])
            ic_series.append(ic)
        results[f"{period}d"] = {
            "mean_ic": mean(ic_series),
            "ir": mean(ic_series) / std(ic_series),
            "ic_positive_ratio": sum(1 for x in ic_series if x > 0) / len(ic_series),
        }
    return results
```

**API**: `GET /api/factor/ic-analysis` → 返回 8 个因子的 IC/IR 排名

**前端**: 新增「因子分析」页面，柱状图展示各因子 IC，红色=有效，灰色=弱

### 2.2 滚动窗口回测 (3h)

**目标**: 按月滚动，每窗口重算评分 → 交易 → 统计

**技术方案**:
```python
# services/rolling_backtest.py
def rolling_backtest(window_days=60, step_days=20, top_n=5):
    """
    每 20 天为一个窗口：
    1. 用窗口内历史数据重新计算 8 维度评分
    2. 选 Top N 持有
    3. 下一窗口评估收益
    """
    windows = generate_rolling_windows(window_days, step_days)
    results = []
    for w in windows:
        scores = recompute_all_scores(w.start, w.end)
        portfolio = select_top(scores, top_n)
        returns = simulate_returns(portfolio, w.forward_period)
        results.append({
            "period": f"{w.start}~{w.end}",
            "return": returns.total,
            "sharpe": returns.sharpe,
            "max_drawdown": returns.max_dd,
            "win_rate": returns.win_rate,
        })
    return results
```

**API**: `GET /api/backtest/rolling?window=60&step=20&top_n=5`

**前端**: 回测页面新增「滚动窗口」模式，显示收益/夏普/回撤变化曲线

### 2.3 止损/止盈规则 (2h)

**目标**: 回测中加入风控规则

**技术方案**:
```python
# 在回测循环中插入止损/止盈判断
RISK_RULES = {
    "stop_loss": -0.10,    # -10% 止损
    "take_profit": 0.30,   # +30% 止盈
    "trailing_stop": 0.08, # 8% 移动止损
}

def check_risk_rules(holdings, current_dt):
    for code, h in holdings.items():
        current_pnl = (available[code] - h["cost"]) / h["cost"]
        if current_pnl <= RISK_RULES["stop_loss"]:
            sell(code, "止损")
        elif current_pnl >= RISK_RULES["take_profit"]:
            sell(code, "止盈")
        elif h.get("max_price"):
            drawdown = (h["max_price"] - available[code]) / h["max_price"]
            if drawdown >= RISK_RULES["trailing_stop"]:
                sell(code, "移动止损")
```

**API**: 回测参数新增 `stop_loss`, `take_profit`, `trailing_stop`

**前端**: 回测参数面板新增 3 个风控输入框，交易明细显示触发原因

### 2.4 行业轮动策略 (2h)

**目标**: 基于行业平均评分变化，推荐加仓/减仓

**技术方案**:
```python
# services/sector_rotation.py
def compute_rotation_signals():
    """
    1. 计算每个行业的日度平均综合分
    2. 比较今日 vs 5日前的变化
    3. 动量最高/最低的 3 个行业 → 加仓/减仓建议
    """
    today_sectors = get_sector_scores(today)
    prev_sectors = get_sector_scores(today - 5d)
    
    momentum = {}
    for sector in today_sectors:
        momentum[sector] = today_sectors[sector] - prev_sectors.get(sector, 50)
    
    ranked = sorted(momentum.items(), key=lambda x: -x[1])
    return {
        "add": ranked[:3],     # 加仓
        "reduce": ranked[-3:], # 减仓
        "all": ranked,
    }
```

**API**: `GET /api/dashboard/sector-rotation-signals`

**前端**: Dashboard 新增「行业轮动」卡片，显示「建议加仓/减仓」行业

---

## 三、AI 深度分析 (8h)

### 3.1 每日市场综述 (2h)

**目标**: LLM 生成自然语言市场报告

**技术方案**:
```python
# services/daily_review.py
def generate_daily_review():
    briefing = call_api("/api/dashboard/briefing")
    alerts = call_api("/api/dashboard/alerts")
    rotation = call_api("/api/dashboard/sector-rotation-signals")
    
    prompt = f"""根据以下数据生成每日市场综述：

市场概况：{briefing["summary"]}
Top 5：{briefing["top5"]}
预警：{alerts["alerts"]}
行业轮动：{rotation}

输出结构：
1. 昨日复盘 (150字)
2. 今日关注 (100字) 
3. 风险提示 (80字)
4. 操作建议 (100字)
"""
    return llm(prompt, temperature=0.3, max_tokens=600)
```

**API**: `GET /api/dashboard/review` → 返回 Markdown 格式综述

**自动化**: 08:30 生成，写入 `daily_reviews` 表

**前端**: Dashboard 新增「每日综述」卡片，自然语言显示

### 3.2 个股深度研报 (3h)

**目标**: 用 LLM 生成结构化 8 维度文字分析

**技术方案**: 扩展已有 `debate.py`，生成完整研报：

```python
# services/research_report.py
SECTIONS = {
    "估值": "PE{pe} PB{pb} 处于历史{percentile}分位→{conclusion}",
    "技术": "{multicyc_signal}，RSI{rsi}→{conclusion}",
    "资金": "主力净流入{flow}亿，换手率{turnover}%→{conclusion}",
    "政策": "行业政策倾向{tendency}({policy_score}分)→{conclusion}",
    "情绪": "情绪分{mood}→{mood_level}→{conclusion}",
    "风险": "主要风险：{risks}→{conclusion}",
    "催化剂": "近期催化剂：{catalysts}→{conclusion}",
}
```

**API**: `POST /api/stocks/{id}/research` → 调用 LLM 生成完整文字报告

**前端**: 股票详情页「AI 研报」标签页，显示结构化文字分析

### 3.3 财报电话会分析 (1.5h)

**目标**: 自动爬取业绩说明会纪要

**数据来源**: 巨潮资讯 `cninfo_search_abstract` → 提取 PDF → 文本 → LLM 总结

**技术方案**:
```python
# services/earnings_call_fetcher.py
def fetch_latest_calls(stock_code, limit=3):
    # 1. cninfo 搜索
    abstracts = cninfo_search_abstract(stock_code, keyword="业绩说明会", limit=limit)
    
    # 2. 下载 PDF → 文本
    texts = [pdf_to_text(abstract.pdf_url) for abstract in abstracts]
    
    # 3. LLM 摘要
    for text in texts:
        summary = llm(f"从下文提取关键信息：业绩指引、管理层表态、Q&A要点\n{text[:3000]}")
    
    return summaries
```

**API**: `GET /api/stocks/{id}/earnings-calls`

**前端**: 股票详情页「业绩会」标签页，显示最近 3 次摘要

### 3.4 舆情热点检测 (1.5h)

**目标**: 从新闻 + 社交媒体检测突发舆情

**技术方案**:
```python
# services/sentiment_hotspot.py
def detect_hotspots():
    """分析最近24小时新闻情感突增的股票"""
    news_today = get_news(hours=24)
    
    for code in all_codes:
        recent_news = news_today[code]
        
        # 检测模式
        signals = {
            "sentiment_surge": any(n.sentiment < -0.5 for n in recent_news),  # 负面情绪突增
            "volume_spike": len(recent_news) > normal_volume[code] * 2,       # 新闻量暴增
            "keyword_hits": any(kw in " ".join(n.title for n in recent_news) 
                              for kw in ["立案","减持","重组","定增","回购"]),
        }
        
        if any(signals.values()):
            yield {"code": code, "signals": signals}
```

**API**: `GET /api/dashboard/hotspots` → 返回今曰舆情异常股票

**前端**: Dashboard 「舆情热点」卡片，红点标记异常股票

---

## 四、实盘衔接 (4h)

### 4.1 虚拟交易日志 (2h)

**目标**: 每次模拟交易自动记录

**数据表**:
```sql
CREATE TABLE trade_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio_id INTEGER, stock_code TEXT, action TEXT,  -- BUY/SELL
    trade_date TEXT, price REAL, lots INTEGER,
    reason TEXT,           -- 买入理由(自动提取)
    strategy TEXT,         -- 策略名
    outcome_return REAL,   -- 事后收益(卖出时填写)
    outcome_days INTEGER,  -- 持仓天数
    notes TEXT,
    FOREIGN KEY(portfolio_id) REFERENCES portfolios(id)
);
```

**API**: `GET /api/portfolio/{id}/journal` → 交易日志

**前端**: 模拟组合页面新增「交易日志」子页面，统计胜率/平均收益/持仓天数

### 4.2 仓位优化器 (2h)

**目标**: 用 MPT（现代组合理论）计算最优配比

**技术方案**:
```python
# services/portfolio_optimizer.py
import numpy as np
from scipy.optimize import minimize

def optimize_weights(returns_matrix, target_return=None):
    """
    输入: 过去 90 天日收益率矩阵 (34 stocks × 90 days)
    输出: 最优权重向量 (在给定风险水平下最大化收益)
    """
    def portfolio_volatility(weights):
        return np.sqrt(weights.T @ cov_matrix @ weights)
    
    def portfolio_return(weights):
        return np.sum(mean_returns * weights) * 252
    
    # 约束: sum(w)=1, w>=0, 最小占比>=2%, 最大占比<=30%
    constraints = [{'type': 'eq', 'fun': lambda w: np.sum(w) - 1}]
    bounds = [(0.02, 0.30) for _ in range(n_stocks)]
    
    # 优化: 最小化波动率
    result = minimize(portfolio_volatility, initial_weights, 
                     bounds=bounds, constraints=constraints)
    return result.x
```

**API**: `POST /api/portfolio/optimize` → 返回最优权重

**前端**: 模拟组合页面「一键优化」按钮

---

## 五、产品体验 (4h)

### 5.1 评分热力图 (1h)

**目标**: 34×8 维度全屏热力图

**技术方案**:
```tsx
// components/Heatmap.tsx
// 使用 react-heatmap-grid 或手写 Canvas
<Heatmap 
  data={34只股票的8维度评分矩阵}
  xLabels={["基本面","技术面","新闻面","资金面","政策面","情绪面","估值面","综合"]}
  yLabels={股票名称}
  colorRange={["#ef4444","#f59e0b","#22c55e","#16a34a"]} // 红→黄→绿
/>
```

**API**: `GET /api/scores/heatmap` → 返回矩阵

**前端**: 新增「热力图」页面，色块代表评分，悬停显示详细数值

### 5.2 键盘快捷键 (1h)

**方案**:
```tsx
// hooks/useKeyboard.ts
useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
        switch(e.key) {
            case 'j': router.push(`/stocks/${nextStockId}`); break;
            case 'k': router.push(`/stocks/${prevStockId}`); break;
            case '1'..'8': setActiveTab(tabs[e.key-1]); break;
            case '/': document.querySelector('input[type=search]')?.focus(); break;
            case 'd': toggleDarkMode(); break;
        }
    };
    window.addEventListener('keydown', onKeyDown);
}, []);
```

**前端**: 侧边栏底部显示快捷键提示

### 5.3 数据导出 API (2h)

**API 端点**:
```
GET /api/export/scores?format=csv      → 34只×8维度评分CSV
GET /api/export/scores?format=xlsx     → 带格式Excel
GET /api/export/backtest/{id}          → 回测结果CSV
GET /api/export/portfolio/{id}         → 组合交易记录CSV
```

**实现**: 使用 Python `csv` 模块 + `openpyxl`

**前端**: 数据管理页面新增「导出」下拉按钮

---

## 六、基础设施 (8h)

### 6.1 PostgreSQL 迁移 (3h)

**技术方案**:

```sql
-- 1. 安装 PostgreSQL + TimescaleDB 扩展
CREATE EXTENSION timescaledb;

-- 2. 时间序列表转为超表 (自动分区优化)
SELECT create_hypertable('stock_daily_quotes', 'trade_date');
SELECT create_hypertable('comprehensive_scores', 'calc_date');

-- 3. 添加物化视图加速排名查询
CREATE MATERIALIZED VIEW v_latest_scores AS
SELECT DISTINCT ON (stock_id) * FROM comprehensive_scores 
ORDER BY stock_id, calc_date DESC;
```

**配置变更**:
```python
# data/database.py
DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/afr")
engine = create_engine(DB_URL, pool_size=10, max_overflow=20)
```

**迁移脚本**: SQLite → PostgreSQL 用 `pgloader` 或自写迁移

### 6.2 Docker 部署 (2h)

**Dockerfile**:
```dockerfile
# Dockerfile
FROM python:3.12-slim AS backend
WORKDIR /app
COPY backend/ /app/
RUN pip install -r requirements.txt
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8800"]

FROM node:22-alpine AS frontend
WORKDIR /app
COPY frontend/ /app/
RUN npm ci && npm run build
CMD ["npm", "start"]
```

**docker-compose.yml**:
```yaml
services:
  backend:
    build: ./backend
    ports: ["8800:8800"]
    depends_on: [postgres]
  frontend:
    build: ./frontend
    ports: ["3000:3000"]
  postgres:
    image: postgres:16
    volumes: [pgdata:/var/lib/postgresql/data]
  redis:
    image: redis:7-alpine  # 缓存评分数据
```

### 6.3 错误监控 (3h)

**方案**: 轻量自建，不需要外部服务

```python
# services/error_tracker.py
ERRORS = []  # 内存队列，最多 1000 条

def track_error(module: str, error: str, context: dict = None):
    ERRORS.append({
        "time": datetime.now().isoformat(),
        "module": module,
        "error": str(error)[:200],
        "context": context,
    })
    if len(ERRORS) > 1000:
        ERRORS.pop(0)
    log_to_db(ERRORS[-1])
```

**数据表**:
```sql
CREATE TABLE error_logs (
    id SERIAL PRIMARY KEY,
    time TIMESTAMP DEFAULT NOW(),
    module TEXT, error TEXT, context JSONB
);
```

**API**: `GET /api/system/errors?limit=50` → 错误列表

**前端**: 新增「错误监控」页面 + Dashboard 右上角错误计数徽章

---

## 七、实施计划

| 阶段 | 项目 | 工时 | 累计 |
|------|------|------|------|
| **第1轮 (P0)** | 多源数据融合 + 宏观指标 | 5h | 5h |
| **第2轮 (P1)** | 因子IC + 滚动回测 + 止损止盈 | 8h | 13h |
| **第3轮 (P1)** | 行业轮动 + 财务报表 | 5h | 18h |
| **第4轮 (P1)** | 每日综述 + 个股研报 + 热力图 | 6h | 24h |
| **第5轮 (P2)** | 舆情热点 + 财报电话会 | 3h | 27h |
| **第6轮 (P2)** | 虚拟交易日志 + 仓位优化 + 导出 | 6h | 33h |
| **第7轮 (P2)** | 键盘快捷键 + Docker + 错误监控 | 6h | 39h |
| **第8轮 (P3)** | PostgreSQL 迁移 | 3h | 42h |

---

## 八、新系统架构

```
                                  ┌─────────────┐
                                  │  用户浏览器   │
                                  │  :3000(:80) │
                                  └──────┬──────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
    ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
    │  Next.js 前端    │     │  FastAPI 后端    │     │  LLM API        │
    │  - 热力图        │     │  - 评分引擎      │     │  - DeepSeek     │
    │  - 回测面板      │     │  - 回测系统      │     │  - 辩论分析     │
    │  - 快捷键        │     │  - 数据融合      │     │  - 每日综述     │
    └─────────────────┘     └────────┬─────────┘     └─────────────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              ▼                      ▼                      ▼
    ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
    │  PostgreSQL     │     │  Redis          │     │  Docker         │
    │  TimescaleDB    │     │  评分缓存       │     │  Compose        │
    └─────────────────┘     └─────────────────┘     └─────────────────┘
              │
    ┌─────────┼─────────┬──────────┐
    ▼         ▼         ▼          ▼
 东财数据   同花顺    腾讯行情    宏观API
```

---

**下一轮建议起点**: 第1轮 — 多源数据融合 + 宏观指标（5h），建立数据中台根基。

从哪个开始？
