/** 数据质量告警标签 → 简洁中文说明 */
export const DATA_QUALITY_FLAG_LABELS: Record<string, string> = {
  price_spike: "大涨大跌",
  price_gap: "剧烈震荡",
  volume_burst: "放量异常",
  turnover_burst: "换手激增",
  volume_price_divergence: "量价背离",
  pe_extreme: "PE异常",
  pe_jump: "PE突变",
  pb_extreme: "PB异常",
  fund_flow_divergence: "资金背离",
  fundamental_jump: "财务跳变",
  ex_rights_mismatch: "疑似未复权",
  suspended_with_trades: "停牌有成交",
  valuation_outdated: "估值过期",
  valuation_missing: "缺少估值",
  newly_listed: "新股",
};

export function formatDataQualityFlag(flag: string): string {
  return DATA_QUALITY_FLAG_LABELS[flag] ?? flag;
}
