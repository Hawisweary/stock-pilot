/** 因子库说明文字 — 固定因子精确说明，合成/表达式因子按公式动态生成 */

interface FactorLike {
  factor_id?: unknown;
  name?: unknown;
  category?: unknown;
  formula?: unknown;
}

const FIXED: Record<string, string> = {
  composite_score:
    "V5综合分。十维加权合成(基本面/质量/行业景气/资金/估值/技术/大盘/政策/新闻/情绪)，含短板惩罚与一票否决。作为因子使用时反映系统对个股的总体评价。",
  fundamental_score:
    "基本面维度分。基于财务指标(盈利能力/成长性/偿债/现金流)在全市场的百分位排名合成，季报更新后变化。",
  technical_score:
    "技术面维度分。技术规则引擎(均线/MACD/RSI/量价形态等)对日线与周线的综合打分，每日收盘后重算。",
  sentiment_score:
    "情绪面维度分(波动率30%+动量70%的全市场截面分位)。高分=近期强动量且波动可控。",
  capital_score:
    "资金面维度分。主力净流入/换手/量能/股东户数等信号合成，反映资金关注度。",
  policy_score: "政策面维度分。行业政策事件对个股的利好/利空累计影响。",
  mood_score: "新闻情绪分。个股新闻经LLM/关键词情感分析后的加权聚合。",
  val_score:
    "估值维度分。PE/PB/PS相对行业与自身历史分位的综合，高分=相对便宜。",
  momentum_20d:
    "20日动量: close/close[20日前]-1。追涨因子，正值越大近期涨幅越强。IC为正说明市场处于动量行情。",
  volatility_20d:
    "20日低波动: -std(日收益,20)。取负号后波动越低分越高，低波异象因子，震荡市防御性好。",
  volume_ratio:
    "量比: 5日均量/20日均量。放量因子，>1表示近期成交活跃度上升，常与突破配合。",
  rsi_divergence:
    "RSI(14)反转因子。超卖(低RSI)期望反弹、超买期望回落，均值回归逻辑，与动量因子天然互补。",
  ma_crossover:
    "均线趋势: MA5>MA20记+1否则-1。经典金叉/死叉趋势信号，简单但在趋势市有效。",
  turnover_adj:
    "流动性因子: 当日换手/20日均换手。异常换手往往伴随信息事件，可作事件驱动的辅助信号。",
  debate_final:
    "[已停用] 旧版AI多空辩论最终分，仅保留历史数据供回测对照，不再更新。",
};

const MERGE_METHOD: Record<string, string> = {
  equal: "等权平均",
  ic_ir: "按各因子IC信息比率(IR)加权，历史预测力越稳定权重越高",
  rolling_optimal: "滚动窗口内动态求最优权重组合，自适应市场风格切换",
};

export function factorDescription(f: FactorLike): string {
  const name = String(f.name ?? "");
  const category = String(f.category ?? "");
  const formula = String(f.formula ?? "");

  if (FIXED[name]) return FIXED[name];

  // 合成因子: formula 是 {"method": "...", "inputs": [...]} JSON
  if (category === "合成" || name.startsWith("tech_multi")) {
    try {
      const spec = JSON.parse(formula);
      const method = MERGE_METHOD[String(spec.method)] || String(spec.method);
      const inputs = Array.isArray(spec.inputs) ? spec.inputs.join(" + ") : "";
      return `多因子合成: 由 ${inputs} 按「${method}」方式合成。由因子合成模块生成，随每日流水线更新。`;
    } catch {
      return `多因子合成因子。配置: ${formula.slice(0, 120)}`;
    }
  }

  // GP/表达式因子: formula 是时序表达式
  if (category === "表达式" || name.startsWith("gp_")) {
    const auto = name.startsWith("gp_") ? "遗传规划自动挖掘的" : "自定义";
    return `${auto}时序表达式因子: ${formula}。Mean/Std/Delta/Rank 分别为滚动均值/标准差/差分/截面排名，$adj_close/$volume 为复权价与成交量。`;
  }

  if (formula && formula !== "评分系统") return `${category}因子。公式: ${formula}`;
  return `${category}因子(${name})。`;
}
