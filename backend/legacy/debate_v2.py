"""AI 辩论 V2 — 5分析师 + 3风险辩论 + Pydantic结构化输出"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

import config
from database import write_lock

_write_db_lock = threading.Lock()


class AnalystOutput(BaseModel):
    opinion: str = Field(description="分析观点，50-80字")
    score_adjust: float = Field(default=0, description="评分调整 -10到+10")
    key_reason: str = Field(description="核心理由，30字以内")
    confidence: float = Field(default=0.7, ge=0, le=1, description="置信度")


class RiskDebateOutput(BaseModel):
    opinion: str = Field(description="风控观点，50字以内")
    risk_level: str = Field(default="中", description="风险等级：高/中/低")
    key_risk: str = Field(description="核心风险点")


class JudgeOutput(BaseModel):
    verdict: str = Field(description="最终判断，30字")
    final_score: float = Field(description="综合评分 0-100")
    confidence: float = Field(default=0.7, ge=0, le=1)
    risk: str = Field(default="中")
    action: str = Field(default="持有", description="买入/持有/卖出/观望")


def debate_input_hash(
    comp: dict,
    news_titles: list[str],
    tech: dict | None,
) -> str:
    payload = {
        "composite": comp.get("composite_score"),
        "calc_date": comp.get("calc_date"),
        "dims": [
            comp.get(k)
            for k in (
                "fundamental_score",
                "technical_score",
                "sentiment_score",
                "capital_score",
                "policy_score",
                "mood_score",
                "val_score",
            )
        ],
        "news": news_titles[:5],
        "tech_score": (tech or {}).get("score"),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def should_skip_debate(ctx, stock_id: int) -> bool:
    existing = ctx.existing_debate.get(stock_id)
    comp = ctx.comprehensive.get(stock_id)
    if not existing or not comp:
        return False

    news_titles = [n.get("title", "") for n in ctx.news.get(stock_id, [])]
    tech = ctx.tech.get(stock_id)
    current_hash = debate_input_hash(comp, news_titles, tech)

    debate_json = existing.get("debate_json")
    if debate_json:
        try:
            parsed = json.loads(debate_json) if isinstance(debate_json, str) else debate_json
            meta = parsed.get("_meta") or {}
            if meta.get("input_hash") == current_hash:
                return True
        except (json.JSONDecodeError, TypeError):
            pass

    orig = existing.get("original_score")
    comp_score = comp.get("composite_score")
    if orig is not None and comp_score is not None and float(orig) == float(comp_score):
        return True
    return False


def _compute_adjusted_score(comp: dict, debate: dict) -> float:
    adjusts = []
    for role in [
        "fundamental_analyst",
        "technical_analyst",
        "sentiment_analyst",
        "capital_analyst",
        "market_analyst",
    ]:
        if role in debate:
            adjusts.append(debate[role].get("score_adjust", 0))

    judge_score = debate.get("judge", {}).get("final_score")
    orig = comp.get("composite_score")
    if orig is None:
        return max(0, min(100, float(judge_score) if judge_score is not None else 50.0))
    orig = float(orig)
    if judge_score is not None:
        clamped = max(orig - 5, min(orig + 5, round(float(judge_score), 1)))
        adjusted_score = round(orig * 0.8 + clamped * 0.2, 1)
    else:
        avg_adjust = sum(adjusts) / len(adjusts) if adjusts else 0
        adjusted_score = round(orig + avg_adjust * 0.5, 1)
    return max(0, min(100, adjusted_score))


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1] for r in rows}


def _persist_debate_result(
    stock_id: int,
    today: str,
    comp: dict,
    debate: dict,
    adjusted_score: float,
    *,
    write_composite: bool,
    input_hash: str | None = None,
) -> None:
    meta_hash = (debate.get("_meta") or {}).get("input_hash")
    stored_hash = input_hash or meta_hash
    with _write_db_lock:
        conn = sqlite3.connect(config.DB_PATH)
        try:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS debate_v2 (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, stock_id INTEGER, date TEXT,
                    original_score REAL, adjusted_score REAL, debate_json TEXT,
                    input_hash TEXT,
                    UNIQUE(stock_id, date))"""
            )
            cols = _table_columns(conn, "debate_v2")
            if "input_hash" not in cols:
                try:
                    conn.execute("ALTER TABLE debate_v2 ADD COLUMN input_hash TEXT")
                except sqlite3.OperationalError:
                    pass
            if stored_hash:
                conn.execute(
                    """INSERT OR REPLACE INTO debate_v2
                       (stock_id, date, original_score, adjusted_score, debate_json, input_hash)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        stock_id,
                        today,
                        comp["composite_score"],
                        adjusted_score,
                        json.dumps(debate, ensure_ascii=False),
                        stored_hash,
                    ),
                )
            else:
                conn.execute(
                    """INSERT OR REPLACE INTO debate_v2
                       (stock_id, date, original_score, adjusted_score, debate_json)
                       VALUES (?,?,?,?,?)""",
                    (
                        stock_id,
                        today,
                        comp["composite_score"],
                        adjusted_score,
                        json.dumps(debate, ensure_ascii=False),
                    ),
                )
            if write_composite:
                with write_lock:
                    for table in ["comprehensive_scores", "factor_scores"]:
                        date_col = "calc_date"
                        try:
                            conn.execute(
                                f"UPDATE {table} SET composite_score=?, debate_locked=1 "
                                f"WHERE stock_id=? AND {date_col}=?",
                                (adjusted_score, stock_id, today),
                            )
                        except sqlite3.OperationalError:
                            conn.execute(
                                f"UPDATE {table} SET composite_score=? "
                                f"WHERE stock_id=? AND {date_col}=?",
                                (adjusted_score, stock_id, today),
                            )
            conn.commit()
        finally:
            conn.close()


def enhanced_debate_with_context(
    ctx,
    stock_id: int,
    code: str,
    *,
    skip_unchanged: bool | None = None,
    write_composite: bool | None = None,
    use_llm: bool = True,
    tier: str = "full_llm",
) -> dict:
    """使用预加载上下文执行辩论（批量路径）。"""
    skip = config.DEBATE_SKIP_UNCHANGED if skip_unchanged is None else skip_unchanged
    write_back = config.DEBATE_WRITE_COMPOSITE if write_composite is None else write_composite

    stock = ctx.stocks.get(stock_id)
    if not stock:
        return {"error": "股票不存在", "stock_id": stock_id}

    comp = ctx.comprehensive.get(stock_id)
    if not comp:
        return {"error": "暂无评分", "stock_id": stock_id, "code": code}

    if skip and should_skip_debate(ctx, stock_id):
        row = ctx.existing_debate.get(stock_id, {})
        skipped_debate = None
        raw = row.get("debate_json")
        if raw:
            try:
                from services.debate_align import postprocess_debate

                parsed = json.loads(raw) if isinstance(raw, str) else raw
                skipped_debate = postprocess_debate(parsed, comp)
            except (json.JSONDecodeError, TypeError):
                skipped_debate = None
        adj = row.get("adjusted_score")
        if skipped_debate is not None:
            try:
                adj = _compute_adjusted_score(comp, skipped_debate)
            except (KeyError, TypeError, ValueError):
                pass
        return {
            "stock_id": stock_id,
            "code": code,
            "name": stock["name"],
            "date": row.get("date") or ctx.today,
            "skipped": True,
            "reason": "unchanged",
            "tier": tier,
            "original_score": comp["composite_score"],
            "adjusted_score": adj,
            "debate": skipped_debate,
        }

    news = ctx.news.get(stock_id, [])
    news_titles = [n.get("title", "") for n in news]
    tech_signal = ctx.tech.get(stock_id, {})
    input_hash = debate_input_hash(comp, news_titles, tech_signal)

    if not use_llm:
        from services.debate_tiered import light_debate

        debate = light_debate(stock, comp, news, tech_signal, input_hash=input_hash)
        from services.debate_align import postprocess_debate

        debate = postprocess_debate(debate, comp)
        base = float(comp["composite_score"])
        clamped = float(debate["judge"]["final_score"])
        adjusted_score = max(0.0, min(100.0, round(base * 0.9 + clamped * 0.1, 1)))
        result = {
            "stock_id": stock_id,
            "code": code,
            "name": stock["name"],
            "date": ctx.today,
            "tier": tier,
            "method": "light_rules",
            "original_score": comp["composite_score"],
            "adjusted_score": adjusted_score,
            "debate": debate,
        }
        _persist_debate_result(
            stock_id,
            ctx.today,
            comp,
            debate,
            adjusted_score,
            write_composite=write_back,
            input_hash=input_hash,
        )
        return result

    try:
        from services.debate_llm_runner import run_debate_llm

        debate = run_debate_llm(stock, comp, news, tech_signal, ctx.macro_text)
    except Exception as ex:
        return {"error": str(ex), "stock_id": stock_id, "code": code, "tier": tier}

    from services.debate_align import postprocess_debate

    debate = postprocess_debate(debate, comp)
    meta = debate.get("_meta") or {}
    meta.update({"input_hash": input_hash, "skipped_llm": False, "tier": tier, "method": "llm"})
    debate["_meta"] = meta
    adjusted_score = _compute_adjusted_score(comp, debate)

    result = {
        "stock_id": stock_id,
        "code": code,
        "name": stock["name"],
        "date": ctx.today,
        "tier": tier,
        "method": "llm",
        "original_score": comp["composite_score"],
        "adjusted_score": adjusted_score,
        "debate": debate,
    }

    _persist_debate_result(
        stock_id,
        ctx.today,
        comp,
        debate,
        adjusted_score,
        write_composite=write_back,
        input_hash=input_hash,
    )
    return result


def enhanced_debate(stock_id: int, code: str) -> dict:
    """V2: 5分析师 + 3风险辩论 + Pydantic schema（单股 API）。"""
    from services.debate_context import preload_single_stock_context

    ctx = preload_single_stock_context(stock_id)
    # 用户手动触发：不跳过，保证返回完整 debate 并重新对齐
    return enhanced_debate_with_context(
        ctx, stock_id, code, skip_unchanged=False
    )
