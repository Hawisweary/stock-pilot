"""
AI 分析器 - DeepSeek / OpenAI / Claude 生成基本面分析与趋势检测
趋势告警持久化缓存；基本面分析优先单次 LLM 调用
"""
import json
import hashlib

from services.llm_client import chat_completion, is_llm_available, parse_json_from_response


class AiAnalyzer:
    """AI 基本面分析器"""

    def __init__(self):
        self.use_simulation = not is_llm_available()
        if self.use_simulation:
            print("[AiAnalyzer] 未配置 LLM API Key，使用规则引擎 fallback")

    def analyze(self, stock_id: int, code: str, name: str) -> dict:
        """分块分析: MD&A → Risk → Financials → 汇总（固定JSON输出）"""
        financial_data = self._get_financial_summary(stock_id)
        insights = self._get_latest_insights(stock_id)

        # 无财务数据时返回空结构（避免500）
        if not financial_data.get("indicators") and not financial_data.get("recent_years"):
            return self._empty_analysis(code, name)

        if self.use_simulation:
            return self._simulate_analysis_v2(code, name, financial_data, insights)

        try:
            prompt = self._build_unified_prompt(code, name, financial_data, insights)
            text = chat_completion(
                prompt,
                system_prompt="你是 A 股基本面研究员。只返回要求的 JSON，不得编造未提供的数据。",
                max_tokens=1200,
                temperature=0.3,
            )
            parsed = parse_json_from_response(text)
            if isinstance(parsed, dict):
                parsed["source"] = "llm"
                return self._normalize_analysis(parsed)
        except Exception as e:
            print(f"[AiAnalyzer] 单次 LLM 分析失败，回退分块: {e}")

        blocks = {}
        for block_key, prompt in [
            ("mda", self._build_mda_prompt(code, name, financial_data)),
            ("risk", self._build_risk_prompt(code, name, financial_data)),
            ("financials", self._build_financials_prompt(code, name, financial_data)),
        ]:
            try:
                text = chat_completion(
                    prompt,
                    system_prompt="你是 A 股基本面研究员。只返回要求的 JSON，不编造数据。",
                    max_tokens=400,
                    temperature=0.3,
                )
                blocks[block_key] = parse_json_from_response(text)
            except Exception as err:
                blocks[block_key] = {"error": str(err)[:100]}

        summary = {}
        try:
            text = chat_completion(
                self._build_summary_prompt(code, name, blocks, insights),
                system_prompt="你是 A 股基本面研究员。基于已有分析块做总结。只返回要求的 JSON。",
                max_tokens=500,
                temperature=0.3,
            )
            summary = parse_json_from_response(text)
        except Exception:
            summary = {"overall": "分析汇总暂不可用"}

        result = {**blocks, **summary, "source": "llm"}
        return self._normalize_analysis(result)

    def financial_input_hash(self, stock_id: int) -> str:
        data = self._get_financial_summary(stock_id)
        payload = json.dumps(data, sort_keys=True, default=str)
        return hashlib.md5(payload.encode()).hexdigest()

    def detect_trend_alerts(
        self,
        code: str,
        name: str,
        periods: list[dict],
        data_granularity: str = "annual",
    ) -> list[dict]:
        """LLM 趋势检测；数据未变则复用缓存，避免重复调用LLM"""
        if len(periods) < 2:
            return []

        data_hash = hashlib.md5(json.dumps(periods[:10], sort_keys=True, default=str).encode()).hexdigest()
        cached = self._load_trend_cache_db(code, data_hash)
        if cached is not None:
            return cached

        if self.use_simulation:
            from services.trend_rules import detect_changes_rules
            alerts = detect_changes_rules(periods)
            for a in alerts:
                a["source"] = "rules"
            self._save_trend_cache_db(code, data_hash, alerts, "rules")
            return alerts

        prompt = self._build_trend_prompt(code, name, periods, data_granularity)
        try:
            text = chat_completion(
                prompt,
                system_prompt=(
                    "你是 A 股财务分析专家。根据财报趋势识别风险与亮点。"
                    "只返回 JSON 数组，不要 markdown 说明。"
                ),
                max_tokens=800,
                temperature=0.2,
            )
            alerts = parse_json_from_response(text)
            if not isinstance(alerts, list):
                alerts = alerts.get("alerts", []) if isinstance(alerts, dict) else []
            normalized = [self._normalize_alert(a) for a in alerts if isinstance(a, dict)]
            for a in normalized:
                a["source"] = "llm"
            result = normalized[:5]
            self._save_trend_cache_db(code, data_hash, result, "llm")
            return result
        except Exception as e:
            print(f"[AiAnalyzer] LLM 趋势检测失败，回退规则: {e}")
            from services.trend_rules import detect_changes_rules
            alerts = detect_changes_rules(periods)
            for a in alerts:
                a["source"] = "rules"
            self._save_trend_cache_db(code, data_hash, alerts, "rules")
            return alerts

    def _load_trend_cache_db(self, code: str, data_hash: str) -> list | None:
        from database import get
        row = get().execute(
            """SELECT alerts_json FROM trend_alerts_cache
               WHERE stock_id=(SELECT id FROM stocks WHERE code=? LIMIT 1)
                 AND data_hash=? AND created_at > datetime('now', '-1 hour')
               ORDER BY created_at DESC LIMIT 1""",
            (code, data_hash),
        ).fetchone()
        if not row:
            return None
        try:
            return json.loads(row["alerts_json"])
        except json.JSONDecodeError:
            return None

    def _save_trend_cache_db(self, code: str, data_hash: str, alerts: list, source: str):
        from database import get, write_lock
        conn = get()
        stock = conn.execute("SELECT id FROM stocks WHERE code=? LIMIT 1", (code,)).fetchone()
        if not stock:
            return
        with write_lock:
            conn.execute(
                """INSERT INTO trend_alerts_cache (stock_id, data_hash, alerts_json, source)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(stock_id, data_hash) DO UPDATE SET
                     alerts_json=excluded.alerts_json, source=excluded.source, created_at=datetime('now')""",
                (stock["id"], data_hash, json.dumps(alerts, ensure_ascii=False), source),
            )
            conn.commit()

    def _build_unified_prompt(self, code: str, name: str, data: dict, insights: list[dict]) -> str:
        ind = data.get("indicators", {})
        facts = "\n".join(f"- {i.get('fact','')}" for i in insights[:6]) or "无"
        return f"""股票: {name}({code})
行业: {data.get('industry','')}
指标(仅可使用下列数字): ROE={ind.get('roe')} 毛利率={ind.get('gross_margin')} 净利率={ind.get('net_margin')}
PE={ind.get('pe_ttm')} PB={ind.get('pb')} 负债率={ind.get('debt_to_equity')}
Quant事实: {facts}

返回一个 JSON 对象:
{{
  "summary": "一段话",
  "strengths": ["..."],
  "weaknesses": ["..."],
  "factor_commentary": {{"quality":"","growth":"","value":"","momentum":"","risk":""}},
  "valuation_view": "",
  "overall_rating": "推荐|中性|谨慎",
  "mda": {{"strategy":"","growth_drivers":[],"tone":""}},
  "risk": {{"top_risk":"","financial":""}},
  "financials": {{"margin_trend":"","cashflow_quality":""}}
}}"""

    def _get_latest_insights(self, stock_id: int) -> list[dict]:
        from database import get
        row = get().execute(
            "SELECT score_detail_json FROM factor_scores WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1",
            (stock_id,)).fetchone()
        if row and row["score_detail_json"]:
            detail = json.loads(row["score_detail_json"])
            return detail.get("_insights", [])
        return []

    def _empty_analysis(self, code: str, name: str) -> dict:
        return {
            "summary": f"{name}({code})暂无财务数据，无法生成AI分析。请先执行数据抓取。",
            "strengths": [], "weaknesses": [],
            "factor_commentary": {}, "valuation_view": "数据不足",
            "overall_rating": "数据不足", "source": "rules",
        }

    def _build_explanation_prompt(self, code: str, name: str, data: dict,
                                   insights: list[dict]) -> str:
        """构建AI解释提示词（只解释Quant发现的事实）"""
        facts_text = "\n".join(
            f"- [{i['type']}] {i['fact']} ({i.get('signal','')})"
            for i in insights[:8]
        ) if insights else "（无显著异常）"
        return f"""股票: {name}({code})
行业: {data.get('industry','')}
综合评分: {data.get('composite_score','?')}

=== 规则引擎发现的事实 ===
{facts_text}

=== 你的任务 ===
用简洁的专业语言解释以上事实背后的可能原因。
禁止编造数据，禁止给出买卖建议。

返回JSON:
{{
  "summary": "一段话总结",
  "strengths": ["优势1","优势2"],
  "weaknesses": ["风险1","风险2"],
  "factor_commentary": {{"quality":"解释","growth":"解释","value":"解释","momentum":"解释","risk":"解释"}},
  "valuation_view": "估值观点",
  "overall_rating": "基于事实的定性评级"
}}"""

    def _simulate_analysis_v2(self, code: str, name: str, data: dict,
                               insights: list[dict]) -> dict:
        """规则引擎模拟（无LLM时）"""
        strengths = ["ROE表现优异"] if data.get("roe") and data["roe"] > 15 else []
        if data.get("gross_margin") and data["gross_margin"] > 50:
            strengths.append("高毛利率，产品有定价权")
        weaknesses = []
        if data.get("debt_to_equity") and data["debt_to_equity"] > 2:
            weaknesses.append("高杠杆运营")
        if data.get("pe_ttm") and data["pe_ttm"] > 60:
            weaknesses.append("高估值水平")
        score = data.get("composite_score", 50)
        rating = "优秀" if score >= 75 else "良好" if score >= 60 else "一般" if score >= 40 else "需关注"
        return {
            "summary": f"综合评分{score:.0f}分，属于{rating}水平。" +
                       f"{len(strengths)}项优势，{len(weaknesses)}项关注点。",
            "strengths": strengths or ["基本面均衡"],
            "weaknesses": weaknesses or ["无明显弱点"],
            "factor_commentary": {
                "quality": "ROE" + ("优秀" if data.get("roe",0) > 15 else "一般"),
                "growth": "增长" + ("强劲" if data.get("rev_growth_3y",0) > 0.1 else "平稳"),
                "value": "估值" + ("偏高" if data.get("pe_ttm",0) > 50 else "合理"),
                "momentum": "动量" + ("强劲" if data.get("composite_score",50) > 65 else "中性"),
                "risk": "风险" + ("可控" if data.get("debt_to_equity",0) < 1.5 else "偏高")
            },
            "valuation_view": "PE_TTM=" + str(data.get("pe_ttm","?")) + "，处于" +
                               ("较高" if data.get("pe_ttm",0) > 50 else "合理") + "水平",
            "overall_rating": rating,
            "source": "rules",
        }

    def _build_mda_prompt(self, code: str, name: str, data: dict) -> str:
        """管理层讨论与分析块"""
        ind = data.get("indicators", {})
        recent = data.get("recent_years", [])
        rev_str = " ".join(f"{r.get('period_end_date','')[:4]}:{r.get('revenue',0)/1e8:.0f}亿" for r in recent[:3])
        return f"""{name}({code}) 管理层讨论分析
指标: ROE={ind.get('roe','?')}% 毛利率={ind.get('gross_margin','?')}% 净利率={ind.get('net_margin','?')}%
近3年营收: {rev_str}

分析要点:
1. 管理层战略方向与资本配置
2. 增长驱动力
3. 管理层语气

返回JSON: {{"strategy":"","growth_drivers":[""],"capital_allocation":"","tone":"neutral/optimistic/cautious"}}"""

    def _build_risk_prompt(self, code: str, name: str, data: dict) -> str:
        """风险因素块"""
        ind = data.get("indicators", {})
        return f"""{name}({code}) 风险评估
负债率={ind.get('debt_to_equity','?')} 流动比率={ind.get('current_ratio','?')}
经营现金流/净利润={ind.get('fcf_margin','?')}

分析: 供应链风险, 监管风险, 竞争风险, 财务风险

返回JSON: {{"supply_chain":"","regulatory":"","competition":"","financial":"","top_risk":""}}"""

    def _build_financials_prompt(self, code: str, name: str, data: dict) -> str:
        """财务报表块"""
        ind = data.get("indicators", {})
        recent = data.get("recent_years", [])
        cf_str = " ".join(f"{r.get('period_end_date','')[:4]}:{r.get('operating_cf',0)/1e8:.0f}亿" for r in recent[:3])
        return f"""{name}({code}) 财务报表分析
PE={ind.get('pe_ttm','?')} PB={ind.get('pb','?')} EPS={ind.get('eps_growth_3y','?')}
近3年经营现金流: {cf_str}

分析: 利润率变化趋势, 现金流质量, CapEx, 资产效率

返回JSON: {{"margin_trend":"","cashflow_quality":"","capex_view":"","asset_efficiency":"","balance_sheet_health":""}}"""

    def _build_summary_prompt(self, code: str, name: str, blocks: dict,
                               insights: list[dict]) -> str:
        """汇总块"""
        mda = json.dumps(blocks.get("mda", {}), ensure_ascii=False)
        risk = json.dumps(blocks.get("risk", {}), ensure_ascii=False)
        fin = json.dumps(blocks.get("financials", {}), ensure_ascii=False)
        facts = "\n".join(f"- {i.get('fact','')}" for i in insights[:4]) or "无"
        return f"""{name}({code}) 综合分析汇总
MD&A: {mda}
风险: {risk}
财务: {fin}
Quant发现: {facts}

返回JSON: {{"summary":"一段话总结(80字内)","strengths":[""],"weaknesses":[""],
"factor_commentary":{{"quality":"","growth":"","value":"","momentum":"","risk":""}},
"valuation_view":"估值观点","overall_rating":"优秀/良好/一般/需关注"}}"""

    def _get_financial_summary(self, stock_id: int) -> dict:
        from database import get

        conn = get()
        cur = conn.cursor()

        latest = cur.execute(
            """SELECT * FROM financial_reports
               WHERE stock_id=? AND report_type='annual'
               ORDER BY period_end_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()

        recent = cur.execute(
            """SELECT period_end_date, revenue, net_profit, eps, total_assets,
                      total_equity, total_liabilities, operating_cf
               FROM financial_reports
               WHERE stock_id=? AND report_type='annual'
               ORDER BY period_end_date DESC LIMIT 3""",
            (stock_id,),
        ).fetchall()

        indicators = cur.execute(
            """SELECT * FROM financial_indicators
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()

        valuation = None
        try:
            valuation = cur.execute(
                """SELECT pe_ttm, pb, market_cap, dividend_yield, as_of_date
                   FROM valuation_snapshots WHERE stock_id=?
                   ORDER BY as_of_date DESC LIMIT 1""",
                (stock_id,),
            ).fetchone()
        except Exception as e:
            import traceback; print(f"[AI分析] 失败: {e}\n{traceback.format_exc()[:300]}")

        scores = cur.execute(
            """SELECT * FROM factor_scores
               WHERE stock_id=? ORDER BY calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()

        stock = cur.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()

        ind_dict = dict(indicators) if indicators else {}
        if valuation:
            for k in ("pe_ttm", "pb", "market_cap", "dividend_yield"):
                if valuation[k] is not None:
                    ind_dict[k] = valuation[k]
            ind_dict["valuation_as_of"] = valuation["as_of_date"]

        return {
            "stock": dict(stock) if stock else {},
            "latest_annual": dict(latest) if latest else {},
            "recent_years": [dict(r) for r in recent],
            "indicators": ind_dict,
            "scores": dict(scores) if scores else {},
            "industry": dict(stock).get("industry", "") if stock else "",
            "composite_score": dict(scores).get("composite_score", 50) if scores else 50,
            "roe": ind_dict.get("roe"),
            "gross_margin": ind_dict.get("gross_margin"),
            "debt_to_equity": ind_dict.get("debt_to_equity"),
            "pe_ttm": ind_dict.get("pe_ttm"),
            "rev_growth_3y": ind_dict.get("rev_growth_3y"),
        }

    def _build_analysis_prompt(self, code: str, name: str, data: dict) -> str:
        indicators = data["indicators"]
        scores = data["scores"]

        recent_str = ""
        for y in data["recent_years"]:
            rev = y.get("revenue", 0) or 0
            profit = y.get("net_profit", 0) or 0
            recent_str += (
                f"  {y.get('period_end_date', '')[:4]}年: "
                f"营收{rev/1e8:.2f}亿, 净利润{profit/1e8:.2f}亿, "
                f"EPS{y.get('eps', 0) or 0:.2f}, "
                f"经营现金流{(y.get('operating_cf', 0) or 0)/1e8:.2f}亿\n"
            )

        score_str = (
            f"盈利能力:{scores.get('profitability_score', 0):.0f} "
            f"成长性:{scores.get('growth_score', 0):.0f} "
            f"安全性:{scores.get('safety_score', 0):.0f} "
            f"估值:{scores.get('value_score', 0):.0f} "
            f"综合:{scores.get('composite_score', 0):.0f}"
        )

        return f"""请根据以下财务数据，对 {name}（{code}）进行简明的基本面分析。

## 财务数据
{recent_str}

## 关键指标
ROE: {indicators.get('roe', 'N/A')}%
净利率: {indicators.get('net_margin', 'N/A')}%
毛利率: {indicators.get('gross_margin', 'N/A')}%
资产负债率(D/E): {indicators.get('debt_to_equity', 'N/A')}
流动比率: {indicators.get('current_ratio', 'N/A')}
PE(TTM): {indicators.get('pe_ttm', 'N/A')}
PB: {indicators.get('pb', 'N/A')}

## 因子评分（0-100，与跟踪股票横向对比）
{score_str}

请返回 JSON：
{{
  "summary": "一句话总结（30字以内）",
  "strengths": ["优势1", "优势2", "优势3"],
  "weaknesses": ["风险1", "风险2", "风险3"],
  "factor_commentary": {{
    "profitability": "盈利能力点评（15字以内）",
    "growth": "成长性点评（15字以内）",
    "safety": "安全性点评（15字以内）",
    "value": "估值点评（15字以内）"
  }},
  "valuation_view": "估值观点（30字以内）",
  "overall_rating": "推荐或中性或谨慎"
}}"""

    def _build_trend_prompt(
        self, code: str, name: str, periods: list[dict], data_granularity: str
    ) -> str:
        lines = []
        for p in periods[:8]:
            rev = (p.get("revenue") or 0) / 1e8
            profit = (p.get("net_profit") or 0) / 1e8
            cf = (p.get("operating_cf") or 0) / 1e8
            yoy_r = p.get("revenue_yoy")
            yoy_p = p.get("profit_yoy")
            qoq_r = p.get("revenue_qoq")
            lines.append(
                f"- {p.get('period_end_date')} [{p.get('report_type')}]: "
                f"营收{rev:.2f}亿 YoY{yoy_r if yoy_r is not None else 'N/A'}% "
                f"QoQ{qoq_r if qoq_r is not None else 'N/A'}%, "
                f"净利{profit:.2f}亿 YoY{yoy_p if yoy_p is not None else 'N/A'}%, "
                f"经营现金流{cf:.2f}亿"
            )

        gran_note = "年报序列" if data_granularity == "annual" else "季度序列"
        return f"""分析 {name}（{code}）的财务趋势（{gran_note}），识别最多 5 条关键变化。

数据（新到旧）：
{chr(10).join(lines)}

返回 JSON 数组，每项格式：
{{
  "type": "warning 或 positive",
  "title": "简短标题",
  "detail": "具体说明，含数字",
  "severity": "high 或 medium 或 low"
}}

关注：营收/利润同比趋势、毛利率变化、现金流、估值风险。不要编造没有的数据。"""

    def _simulate_analysis(self, stock_id: int, code: str, name: str) -> str:
        from database import get

        conn = get()
        row = conn.execute(
            """SELECT s.code, s.name, fs.*
               FROM factor_scores fs
               JOIN stocks s ON fs.stock_id = s.id
               WHERE fs.stock_id = ?
               ORDER BY fs.calc_date DESC LIMIT 1""",
            (stock_id,),
        ).fetchone()

        if not row:
            return json.dumps({
                "summary": f"{name}暂无足够数据进行分析",
                "strengths": ["需要先抓取财务数据"],
                "weaknesses": ["数据不足"],
                "factor_commentary": {},
                "valuation_view": "数据不足",
                "overall_rating": "中性",
            }, ensure_ascii=False)

        scores = dict(row)
        comp = scores.get("composite_score", 50)
        prof = scores.get("profitability_score", 50)
        growth = scores.get("growth_score", 50)
        safety = scores.get("safety_score", 50)
        value = scores.get("value_score", 50)

        strengths, weaknesses = [], []
        if prof >= 60:
            strengths.append("盈利能力优秀，ROE和利润率处于行业领先水平")
        else:
            weaknesses.append("盈利能力有待提升，利润率承压")
        if growth >= 60:
            strengths.append("成长性良好，营收和利润保持稳健增长")
        else:
            weaknesses.append("营收增长放缓，需要关注未来成长动力")
        if safety >= 60:
            strengths.append("财务结构稳健，负债水平合理")
        else:
            weaknesses.append("负债率偏高，财务风险需警惕")
        if value >= 60:
            strengths.append("估值具有吸引力，当前PE/PB低于行业平均")
        else:
            weaknesses.append("估值偏高，需耐心等待更好的入场时机")

        rating = "推荐" if comp >= 70 else ("中性" if comp >= 40 else "谨慎")

        return json.dumps({
            "summary": f"{name}综合评分{comp:.0f}分，基本面{'优良' if comp >= 60 else '一般' if comp >= 40 else '偏弱'}。",
            "strengths": strengths[:3],
            "weaknesses": weaknesses[:3],
            "factor_commentary": {
                "profitability": f"盈利能力得分{prof:.0f}分",
                "growth": f"成长性得分{growth:.0f}分",
                "safety": f"安全性得分{safety:.0f}分",
                "value": f"估值得分{value:.0f}分",
            },
            "valuation_view": f"{name}当前综合评分{comp:.0f}，{'估值合理' if value >= 50 else '估值偏高'}",
            "overall_rating": rating,
        }, ensure_ascii=False)

    def _normalize_analysis(self, result: dict) -> dict:
        rating = str(result.get("overall_rating", "中性"))
        if rating not in ("推荐", "中性", "谨慎"):
            if "推荐" in rating or "买入" in rating:
                rating = "推荐"
            elif "谨慎" in rating or "回避" in rating:
                rating = "谨慎"
            else:
                rating = "中性"
        return {
            "summary": str(result.get("summary", "")),
            "strengths": list(result.get("strengths") or [])[:5],
            "weaknesses": list(result.get("weaknesses") or [])[:5],
            "factor_commentary": dict(result.get("factor_commentary") or {}),
            "valuation_view": str(result.get("valuation_view", "")),
            "overall_rating": rating,
            "source": result.get("source", "llm"),
        }

    def _normalize_alert(self, alert: dict) -> dict:
        severity = str(alert.get("severity", "medium"))
        if severity not in ("high", "medium", "low"):
            severity = "medium"
        atype = str(alert.get("type", "warning"))
        if atype not in ("warning", "positive"):
            atype = "warning" if "风险" in alert.get("title", "") else "positive"
        return {
            "type": atype,
            "title": str(alert.get("title", "")),
            "detail": str(alert.get("detail", "")),
            "severity": severity,
        }
