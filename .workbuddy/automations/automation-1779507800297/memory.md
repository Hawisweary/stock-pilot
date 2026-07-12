# 每日综合评分刷新 - 执行记录

## 2026-06-23 16:36
- `/api/scores/batch` 仍是 GET（只读），POST 报 405
- 两步刷新：POST /api/scores/recalculate?benchmark=industry → 99只; POST /api/scores/comprehensive/calculate → 99只
- DB 实际入库 99 只（comprehensive_scores.calc_date=2026-06-23）；GET /api/scores/batch 接口仅返回 8 只（接口层有过滤）
- 非零 score 仅 8 只：招商银行72.25、双环传动62.25、宁德时代52.75、贵州茅台49.75、三花智控21.75、航发科技9.75、汇川技术9.25、铖昌科技7.70
- 91 只 score=0.0，但 7 因子都有非零值（如 600570 恒生电子、300474 景嘉微 等）；说明 composite_v5 合成口径在底层有变化/被重置，需检查 scoring pipeline

## 2026-06-22 16:37
- `/api/scores/batch` 仍是 GET（只读），POST 报 405
- 两步刷新：POST /api/scores/recalculate?benchmark=industry → 99只; POST /api/scores/comprehensive/calculate → 99只
- batch 接口返回 30 只（有 comprehensive_scores 记录的）
- 结果（30只）：均值 24.2 | 最高 62.2(002472) | 最低 0.0
- 分布: A(>=70) 0只 | B(60-70) 2只 | C(40-60) 7只 | D(<40) 21只
- TOP3: 002472(62.2) > 000333(61.5) > 000725(58.5)
- BOTTOM3: 688568/601698/000768 三只均 0.0（多个 0 因子）
- 7因子缺失：capital=9只，val=8只
- 用户询问的 34 只是历史规模，当前 DB 共 99 只，有综合评分 30 只

## 2026-06-02 17:02
- `/api/scores/batch` 端点现为 GET（只读），POST 返回 Method Not Allowed
- 两步刷新：POST /api/scores/recalculate?benchmark=industry → 57只; POST /api/scores/comprehensive/calculate → 57只
- 结果：57只 | 均值51.1 | 最高74.0(000725) | 最低27.6(600118)
- 分布: A(>=70) 1只 | B(60-70) 8只 | C(40-60) 38只 | D(<40) 10只
- TOP5: 000725(74.0) > 601138(69.4) > 300502(68.9) > 000333(67.3) > 300750(67.0)
- BOTTOM5: 600118(27.6) < 600343(30.6) < 601698(30.9) < 002792(32.1) < 301005(33.7)
- ✅ 7因子全部有值

## 2026-05-26 16:35
- 端点 /api/scores/batch 不存在(404)，两步完成综合评分：
  1. POST /api/scores/recalculate?benchmark=industry → factor_scores 54只
  2. POST /api/scores/comprehensive/calculate → comprehensive_scores 54只
- 综合评分结果：53只有效 | 均值53.6 | 最高72.8(002472) | 最低32.2(600118)
- 分布: A(>=70) 2只 | B(60-70) 11只 | C(40-60) 35只 | D(<40) 5只
- TOP5: 002472(72.8) > 000725(71.6) > 601689(65.8) > 300124(64.9) > 300607(64.7)
- BOTTOM5: 600118(32.2) < 601698(35.8) < 000625(36.9) < 688568(37.4) < 301005(38.5)
- ✅ 7因子(fund/tech/news/capital/policy/mood/val)全部有值，批处理对齐已修复

## 2026-05-25 16:35
- 端点 /api/scores/batch 不存在(404)，实际需两步完成综合评分：
  1. POST /api/scores/recalculate?benchmark=industry → 更新 factor_scores（54只）
  2. POST /api/scores/comprehensive/calculate → 更新 comprehensive_scores（54只）
- 综合评分结果：54只 | 均值54.5 | 最高81.0(300476) | 最低30.5(000625)
- 分布: A(>=70) 4只 | B(60-70) 13只 | C(40-60) 31只 | D(30-40) 6只
- TOP5: 300476(81.0) > 688017(74.2) > 300502(70.9) > 002463(70.7) > 300607(66.9)
- BOTTOM5: 000625(30.5) < 601698(37.0) < 600391(37.6) < 688568(38.0) < 000768(39.0)
- ⚠️ 批处理仅3因子(fund/tech/news)，资本/政策/情绪/估值维度在DB中为NULL
- 单股端点(stocks/{id}/comprehensive)支持7因子动态组装，但批处理未对齐

## 2026-05-24 16:35
- 端点 /api/scores/batch 不存在(404)，使用 /api/scores/recalculate?benchmark=industry 替代
- 遇到500错误：factor_engine.py 导入 FACTOR_BENCHMARK_DEFAULT 失败，config.py 缺少该常量
- 修复：在 config.py 添加 `FACTOR_BENCHMARK_DEFAULT = "industry"`
- 重启后端后调用成功：因子评分39只 + 综合评分39只
- 平均分56.4，最高78.9(600862)，最低27.4(603601)
- 分布: >=70分6只 | 60-70分9只 | 40-60分20只 | <40分4只

## 2026-05-23 16:35
- 调用 POST /api/scores/recalculate?benchmark=industry，成功更新39只股票
- 响应: {"updated":39,"status":"done","benchmark_mode":"industry"}
- 注意: /api/scores/batch 端点不存在(404)，正确端点为 /api/scores/recalculate
- 平均分56.4，最高78.9(600862)，最低27.4(603601)
- 分布: >=70分6只 | 60-70分9只 | 40-60分20只 | <40分4只
- 五因子维度: 盈利(profitability)、成长(growth)、安全(safety)、估值(value)、动量(momentum)
