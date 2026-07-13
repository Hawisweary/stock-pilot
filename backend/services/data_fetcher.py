"""
数据抓取服务 - 多源数据整合
行情: 腾讯/yfinance | 财报: 东财直连 + akshare fallback | PE/PB: 腾讯财经
"""
import os, signal, time, sqlite3
from datetime import datetime, timedelta

import config
import yfinance as yf
import pandas as pd

from services.data_processor import (
    normalize_code, to_exchange_code, to_yfinance_code, safe_float,
    transform_yfinance_quotes, transform_financial_indicators,
    transform_financial_reports, _transform_financial_abstract
)
from services.data_sources import (
    tencent_quote, eastmoney_stock_info
)
from services import eastmoney_finance as em_fin
from services.akshare_lazy import akshare as _ak
from database import write_lock
from services.industry_normalize import normalize_industry


class DataFetcher:
    """股票数据抓取器（多源整合版）"""

    def __init__(self, conn: sqlite3.Connection, *, batch_commit: bool = False):
        self.conn = conn
        self._batch_commit = batch_commit
        self._log_buffer: list[tuple] = []
        self._step_status_buffer: list[tuple] = []

    def _commit(self) -> None:
        if not self._batch_commit:
            with write_lock:
                self.conn.commit()

    def _flush_logs(self) -> None:
        if not self._log_buffer:
            return
        # data_fetch_log 已迁移到缓存库 cache.db，不再与主库争写锁
        from database import cache_connect

        cconn = cache_connect()
        try:
            cconn.executemany(
                """INSERT INTO data_fetch_log
                   (stock_id, data_type, status, records_count, error_message, duration_ms, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                self._log_buffer,
            )
            cconn.commit()
            self._log_buffer.clear()
        finally:
            cconn.close()

    def _flush_step_status(self) -> None:
        if not self._step_status_buffer:
            return
        sql = """
            INSERT INTO fetch_step_status (stock_id, step, status, message, updated_at)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(stock_id, step) DO UPDATE SET
                status=excluded.status,
                message=excluded.message,
                updated_at=datetime('now')
        """
        with write_lock:
            self.conn.executemany(sql, self._step_status_buffer)
            self._step_status_buffer.clear()

    def _finalize_batch(self) -> None:
        if self._batch_commit:
            self._flush_logs()
            self._flush_step_status()
            with write_lock:
                self.conn.commit()

    def _log(
        self,
        stock_id: int | None,
        data_type: str,
        status: str,
        records: int = 0,
        error: str = "",
        duration_ms: int = 0,
        *,
        source: str = "",
    ):
        row = (
            stock_id,
            data_type,
            status,
            records,
            str(error)[:500],
            duration_ms,
            source or "",
        )
        if self._batch_commit:
            self._log_buffer.append(row)
            return
        from database import cache_connect

        cconn = cache_connect()
        try:
            cconn.execute(
                """INSERT INTO data_fetch_log
                   (stock_id, data_type, status, records_count, error_message, duration_ms, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                row,
            )
            cconn.commit()
        finally:
            cconn.close()

    # ===== 公开方法 =====

    def fetch_all_for_stock(
        self,
        stock_id: int,
        code: str,
        market: str = "A",
        *,
        finance_fast: bool | None = None,
        plan=None,
    ) -> dict:
        from services.fetch_planner import StockFetchPlan, quote_bars_for_stock

        code = normalize_code(code, market=market or "A")
        if plan is not None and not isinstance(plan, StockFetchPlan):
            plan = None

        use_fast = (
            plan.finance_fast
            if plan is not None
            else (config.FINANCE_FAST_PATH if finance_fast is None else finance_fast)
        )
        result: dict = {
            "quotes_count": 0,
            "financials_count": 0,
            "indicators_count": 0,
            "announcements_count": 0,
            "ex_rights_count": 0,
            "status": "success",
            "errors": [],
            "finance_mode": "fast" if use_fast else "full",
            "fetch_plan": plan.to_dict() if plan else None,
        }

        try:
            if plan is None or plan.fetch_info:
                self._fetch_stock_info(stock_id, code, market)
            elif plan:
                self._record_skipped_step(stock_id, "info", plan.skipped_steps)
        except Exception as e:
            result["errors"].append({"step": "info", "message": str(e)})

        try:
            if plan is None or plan.fetch_quotes:
                if plan and plan.quote_max_bars is not None:
                    quote_days = quote_bars_for_stock(
                        self.conn,
                        stock_id,
                        max_bars=plan.quote_max_bars,
                        incremental=plan.quote_incremental,
                    )
                else:
                    quote_days = config.FINANCE_FAST_QUOTE_DAYS if use_fast else config.DATA_FETCH_DAYS
                result["quotes_count"] = self._fetch_daily_quotes(
                    stock_id, code, market=market or "A", max_bars=quote_days
                )
            elif plan:
                self._record_skipped_step(stock_id, "quotes", plan.skipped_steps)
        except Exception as e:
            result["errors"].append({"step": "quotes", "message": str(e)})

        try:
            if plan is None or plan.fetch_financials:
                if use_fast:
                    fin, ind = self._fetch_financial_fast(stock_id, code)
                    result["financials_count"] = fin
                    if plan is None or plan.fetch_indicators:
                        result["indicators_count"] = ind
                else:
                    result["financials_count"] = self._fetch_financial_reports(stock_id, code)
            elif plan:
                self._record_skipped_step(stock_id, "financials", plan.skipped_steps)
        except Exception as e:
            result["errors"].append({"step": "financials", "message": str(e)})

        if not use_fast and (plan is None or plan.fetch_indicators):
            try:
                result["indicators_count"] = self._fetch_financial_indicators(stock_id, code)
            except Exception as e:
                result["errors"].append({"step": "indicators", "message": str(e)})
        elif plan and not plan.fetch_indicators:
            self._record_skipped_step(stock_id, "indicators", plan.skipped_steps)

        try:
            if plan is None or plan.fetch_valuation:
                self._fetch_valuation_indicators(stock_id, code)
            elif plan:
                self._record_skipped_step(stock_id, "valuation", plan.skipped_steps)
        except Exception as e:
            result["errors"].append({"step": "valuation", "message": str(e)})

        try:
            if plan is None or plan.fetch_announcements:
                ann_limit = plan.announcement_limit if plan else 30
                result["announcements_count"] = self._fetch_announcements(
                    stock_id, code, limit=ann_limit
                )
        except Exception as e:
            result["errors"].append({"step": "announcements", "message": str(e)})

        self._flush_logs()
        log_errors = self._recent_fetch_errors(stock_id)
        seen = {e["step"] for e in result["errors"]}
        for le in log_errors:
            if le["step"] not in seen:
                result["errors"].append(le)

        skip_factor = plan.skip_factor if plan else False
        if not skip_factor:
            try:
                from services.factor_engine import FactorEngine

                FactorEngine(self.conn).calculate_all([stock_id])
            except Exception as e:
                print(f"[Fetcher] 因子评分失败 {code}: {e}")
                result["errors"].append({"step": "factor_scores", "message": str(e)})

        ok_parts = (
            result["quotes_count"] > 0
            or result["financials_count"] > 0
            or result["indicators_count"] > 0
        )
        if result["errors"]:
            result["status"] = "partial" if ok_parts else "error"
        else:
            result["status"] = "success"

        self._finalize_batch()
        return result

    def _record_skipped_step(self, stock_id: int, step: str, skipped: list[str]) -> None:
        reason = "skipped_by_plan"
        if f"{step}_circuit" in skipped or "financials_circuit" in skipped:
            reason = "circuit_breaker"
        if self._batch_commit:
            self._step_status_buffer.append((stock_id, step, "skipped", reason))
            return
        from services.fetch_step_status import record_step

        record_step(stock_id, step, "skipped", reason, conn=self.conn)

    def _recent_fetch_errors(self, stock_id: int, limit: int = 8) -> list[dict]:
        from database import cache_connect

        cconn = cache_connect()
        try:
            rows = cconn.execute(
                """
                SELECT data_type, error_message FROM data_fetch_log
                WHERE stock_id=? AND status='error'
                ORDER BY id DESC LIMIT ?
                """,
                (stock_id, limit),
            ).fetchall()
        finally:
            cconn.close()
        out = []
        for r in rows:
            msg = (r["error_message"] or "").strip()
            if msg:
                out.append({"step": r["data_type"], "message": msg})
        return out

    def _fetch_stock_info(self, stock_id: int, code: str, market: str):
        """获取公司基本信息（保留已有中文名，不覆盖）"""
        start = time.time()

        # 检查是否已有中文名
        existing = self.conn.execute(
            "SELECT name FROM stocks WHERE id=?", (stock_id,)
        ).fetchone()
        current_name = str(existing["name"]) if existing else ""

        # 如果已有中文名但不是纯数字/代码，仍然尝试获取行业
        if current_name and current_name != code and not current_name.isascii():
            # 已有中文名，检查行业是否缺失
            existing_ind = self.conn.execute(
                "SELECT industry, industry_sw FROM stocks WHERE id=?", (stock_id,)
            ).fetchone()
            need_industry = not existing_ind or not existing_ind["industry"] or not existing_ind["industry_sw"]
            if not need_industry:
                self._log(stock_id, "info", "success", 0, duration_ms=0)
                return
            # 行业缺失，尝试获取
            info = {}
            try:
                from services.adata_adapter import get_industry_sw
                sw = get_industry_sw(code)
                if sw.get("industry"):
                    sw_norm = normalize_industry(sw["industry"], self.conn)
                    with write_lock:
                        self.conn.execute(
                            "UPDATE stocks SET industry=?, industry_sw=? WHERE id=?",
                            (sw["industry"], sw_norm, stock_id),
                        )
                        self._commit()
                    self._log(stock_id, "info", "success")
                    return
            except Exception:
                pass
            # ADATA失败则继续下面的流程获取行业

        info = {}

        # 方案0: ADATA申万行业（最精准中文行业）
        try:
            from services.adata_adapter import get_industry_sw
            sw = get_industry_sw(code)
            if sw.get("industry"): info["industry"] = sw["industry"]
        except Exception:
            pass

        # 方案1: 腾讯财经行情（可能含 XD/XR/DR，且偶尔截断）
        try:
            from services.data_sources import tencent_quote
            q = tencent_quote([code])
            if code in q and q[code].get("name"):
                raw = q[code]["name"]
                # 去掉 XD/XR/DR 后如果长度≤3 可能是截断的，不采用
                clean = raw
                for p in ("XD", "XR", "DR"):
                    if clean.startswith(p): clean = clean[len(p):]; break
                if len(clean) >= 4 and clean != code:
                    info["name"] = raw  # 保留原始含 XD 的名称
        except Exception:
            pass

        # 方案2: 东财 stock info（全名，不受除权影响）
        if not info.get("name") or len(info.get("name","").replace("XD","").replace("XR","").replace("DR","")) < 4:
            try:
                from services.data_sources import eastmoney_stock_info
                ei = eastmoney_stock_info(code)
                if ei.get("name") and ei["name"] != code:
                    info["name"] = ei["name"]
                if ei.get("industry"): info["industry"] = ei["industry"]
            except Exception:
                pass

        # 方案3: akshare
        if not info.get("name") or not info.get("industry"):
            try:
                df = _ak().stock_individual_info_em(symbol=code)
                for _, row in df.iterrows():
                    key = str(row["item"]).strip()
                    val = str(row["value"]).strip()
                    if "股票简称" in key: info["name"] = val
                    elif "行业" in key: info["industry"] = val
                    elif "上市时间" in key: info["list_date"] = val
            except Exception:
                pass

        # 方案4: yfinance (兜底行业)
        if not info.get("industry"):
            try:
                import yfinance as yf
                t = yf.Ticker(to_yfinance_code(code))
                ti = t.info or {}
                ind = ti.get("industry") or ti.get("sector") or ""
                if ind: info["industry"] = ind
            except Exception:
                pass

        raw_industry = info.get("industry") or ""
        if raw_industry:
            info["industry_sw"] = normalize_industry(raw_industry, self.conn)

        # 更新数据库
        cols = []
        values = []
        if info.get("name") and not info["name"].isascii():
            cols.append("name=?")
            values.append(info["name"])
        if info.get("industry"):
            cols.append("industry=?")
            values.append(info["industry"])
        if info.get("industry_sw"):
            cols.append("industry_sw=?")
            values.append(info["industry_sw"])
        if info.get("list_date"):
            cols.append("list_date=?")
            values.append(info["list_date"])

        if cols:
            values.append(stock_id)
            with write_lock:
                self.conn.execute(
                    f"UPDATE stocks SET {', '.join(cols)}, updated_at=datetime('now') WHERE id=?",
                    values
                )
                self._commit()
            self._log(stock_id, "info", "success", duration_ms=int((time.time()-start)*1000))
        else:
            self._log(stock_id, "info", "error", error="无法获取基本信息", duration_ms=int((time.time()-start)*1000))

    def _apply_adj_after_quotes(self, stock_id: int, code: str, quote_source: str) -> int:
        """同步除权除息并写入 adj_close。"""
        from services.adjust_factor_sync import apply_forward_adj, sync_ex_rights

        n = sync_ex_rights(stock_id, code, conn=self.conn)
        apply_forward_adj(stock_id, quote_source=quote_source, conn=self.conn)
        self._log(stock_id, "ex_rights", "success", n, source="eastmoney")
        return n

    def _enrich_quote_extras(self, stock_id: int, code: str, *, max_bars: int = 500) -> int:
        try:
            from services.quote_extras_sync import enrich_stock_quote_extras

            return enrich_stock_quote_extras(
                stock_id, code, max_bars=max_bars, conn=self.conn
            )
        except Exception as e:
            print(f"[Fetcher] 成交额/换手率补全失败 {code}: {e}")
            return 0

    def _fetch_daily_quotes(
        self, stock_id: int, code: str, *, market: str = "A", max_bars: int = 2000
    ) -> int:
        """使用腾讯财经获取每日行情（yfinance 为备用源）"""
        start = time.time()

        # 主源: 腾讯财经 HTTP API（兼容代理，稳定快速）
        try:
            from services.tencent_adapter import fetch_daily_quotes, transform_to_db_rows
            df = fetch_daily_quotes(code, market=market, count=max_bars)
            if df is not None and not df.empty:
                rows = transform_to_db_rows(df, stock_id)
                count = self._upsert_batch("stock_daily_quotes", rows, ["stock_id", "trade_date"])
                self._apply_adj_after_quotes(stock_id, code, quote_source="qfq")
                extras_n = self._enrich_quote_extras(stock_id, code, max_bars=max_bars)
                self._log(
                    stock_id,
                    "quotes",
                    "success",
                    count,
                    duration_ms=int((time.time() - start) * 1000),
                    source="tencent+eastmoney" if extras_n else "tencent",
                )
                return count
            print(f"[Fetcher] 腾讯API空数据 {code}，尝试yfinance备用源")
        except Exception as e:
            print(f"[Fetcher] 腾讯API失败 {code}: {e}，尝试yfinance备用源")

        # 备用源: yfinance
        import yfinance.exceptions
        yf_period = "1y" if max_bars <= 150 else "max"
        for attempt in range(4):
            try:
                yf_code = to_yfinance_code(code, market=market)
                ticker = yf.Ticker(yf_code)
                df = ticker.history(period=yf_period)

                if df is None or df.empty:
                    if attempt < 3:
                        time.sleep(0.5 * (attempt + 1))
                        continue
                    self._log(stock_id, "quotes", "success", 0, duration_ms=int((time.time()-start)*1000))
                    return 0

                rows = transform_yfinance_quotes(df, stock_id)
                count = self._upsert_batch("stock_daily_quotes", rows, ["stock_id", "trade_date"])
                self._apply_adj_after_quotes(stock_id, code, quote_source="raw")
                self._log(stock_id, "quotes", "success", count, duration_ms=int((time.time()-start)*1000), source="yfinance")
                return count
            except yfinance.exceptions.YFRateLimitError:
                wait = 2 ** attempt
                print(f"[Fetcher] yfinance限流 stock={code}，{wait}s后重试...")
                time.sleep(wait)
            except Exception as e:
                if attempt < 3:
                    time.sleep(1)
                    continue
                self._log(stock_id, "quotes", "error", error=str(e), duration_ms=int((time.time()-start)*1000), source="yfinance")
                print(f"[Fetcher] 行情获取失败 {code}: {e}")
                return 0
        self._log(stock_id, "quotes", "error", error="yfinance rate limited after 4 retries", duration_ms=int((time.time()-start)*1000), source="yfinance")
        return 0

    def _fetch_announcements(self, stock_id: int, code: str, limit: int = 30) -> int:
        from services.announcement_fetch import sync_announcements

        start = time.time()
        try:
            n = sync_announcements(stock_id, code, limit=limit, conn=self.conn)
            self._log(
                stock_id,
                "announcements",
                "success" if n else "success",
                n,
                duration_ms=int((time.time() - start) * 1000),
                source="eastmoney",
            )
            return n
        except Exception as e:
            self._log(
                stock_id,
                "announcements",
                "error",
                error=str(e),
                duration_ms=int((time.time() - start) * 1000),
                source="eastmoney",
            )
            return 0

    def _ak_sleep(self) -> None:
        from services.rate_limiter import wait_host

        wait_host("akshare")

    def _fetch_financial_fast(self, stock_id: int, code: str) -> tuple[int, int]:
        """快路径：ADATA 核心指标 + mootdx 近 4 季 → indicators + financial_reports"""
        start = time.time()
        fin_count = 0
        ind_count = 0

        try:
            from services.adata_adapter import get_core_finance

            core = get_core_finance(code, count=4)
            if core:
                ind_rows = []
                rep_rows = []
                for c in core:
                    calc_date = str(c.get("date", ""))[:10]
                    if not calc_date:
                        continue
                    debt = c.get("debt_ratio")
                    ind_rows.append(
                        {
                            "stock_id": stock_id,
                            "calc_date": calc_date,
                            "roe": c.get("roe"),
                            "roa": c.get("roa"),
                            "gross_margin": c.get("gross_margin"),
                            "net_margin": c.get("net_margin"),
                            "debt_to_equity": round(debt / (100 - debt), 4)
                            if debt is not None and debt < 100
                            else None,
                        }
                    )
                    month = calc_date[5:7] if len(calc_date) >= 7 else "12"
                    rep_rows.append(
                        {
                            "stock_id": stock_id,
                            "report_date": calc_date,
                            "period_end_date": calc_date,
                            "report_type": "annual" if month == "12" else "quarterly",
                            "eps": c.get("eps"),
                            "net_profit_parent": None,
                            "revenue": None,
                        }
                    )
                if ind_rows:
                    ind_count = self._upsert_batch(
                        "financial_indicators", ind_rows, ["stock_id", "calc_date"]
                    )
                if rep_rows:
                    fin_count = self._upsert_batch(
                        "financial_reports", rep_rows, ["stock_id", "period_end_date", "report_type"]
                    )
                self._log(stock_id, "financials_fast", "success", fin_count, duration_ms=int((time.time()-start)*1000), source="adata")
        except Exception as e:
            self._log(stock_id, "financials_fast", "error", error=str(e), source="adata")

        try:
            from services.astock_data import sync_mootdx_financials

            moot = sync_mootdx_financials(code, stock_id=stock_id)
            added = int(moot.get("reports_count") or moot.get("records") or 0)
            if added > fin_count:
                fin_count = added
        except Exception as e:
            print(f"[Fetcher] mootdx fast {code}: {e}")

        if ind_count == 0:
            try:
                ind_count = self._fetch_financial_indicators(stock_id, code)
            except Exception:
                pass

        if fin_count == 0:
            df, src = self._fetch_sheet_em_or_ak(code, "profit", "yearly", "快路径年度利润表")
            if df is not None and not df.empty:
                records = transform_financial_reports(df, None, None, stock_id)
                fin_count = self._upsert_batch(
                    "financial_reports", records, ["stock_id", "period_end_date", "report_type"]
                )
                self._log(stock_id, "financials", "success", fin_count, source=src)

        return fin_count, ind_count

    def _fetch_sheet_em_or_ak(
        self, code: str, sheet: str, period: str, label: str,
    ) -> tuple:
        """东财直连优先，akshare 作 fallback。返回 (DataFrame|None, source)"""
        fetchers = {
            ("profit", "yearly"): em_fin.fetch_profit_sheet,
            ("profit", "quarterly"): em_fin.fetch_profit_sheet,
            ("balance", "yearly"): em_fin.fetch_balance_sheet,
            ("balance", "quarterly"): em_fin.fetch_balance_sheet,
            ("cashflow", "yearly"): em_fin.fetch_cashflow_sheet,
            ("cashflow", "quarterly"): em_fin.fetch_cashflow_sheet,
        }
        ak_map = {
            ("profit", "yearly"): lambda ex: _ak().stock_profit_sheet_by_yearly_em(symbol=ex),
            ("profit", "quarterly"): lambda ex: _ak().stock_profit_sheet_by_quarterly_em(symbol=ex),
            ("balance", "yearly"): lambda ex: _ak().stock_balance_sheet_by_yearly_em(symbol=ex),
            ("balance", "quarterly"): lambda ex: _ak().stock_balance_sheet_by_report_em(symbol=ex),
            ("cashflow", "yearly"): lambda ex: _ak().stock_cash_flow_sheet_by_yearly_em(symbol=ex),
            ("cashflow", "quarterly"): lambda ex: _ak().stock_cash_flow_sheet_by_quarterly_em(symbol=ex),
        }
        from services.rate_limiter import wait_host

        key = (sheet, period)
        wait_host("eastmoney")
        df = self._retry_fetch(
            lambda: fetchers[key](code, period),
            f"{label} {code} [eastmoney]",
        )
        if df is not None and not df.empty:
            return df, "eastmoney"
        exchange_code = to_exchange_code(code)
        self._ak_sleep()
        df = self._retry_fetch(
            lambda: ak_map[key](exchange_code),
            f"{label} {code} [akshare]",
        )
        if df is not None and not df.empty:
            return df, "akshare"
        return df, "eastmoney"

    def _fetch_financial_reports(self, stock_id: int, code: str) -> int:
        """获取年度+季度财报并合并（东财直连 + akshare fallback）"""
        import time as _t

        from services.rate_limiter import wait_host

        df_income, src_i = self._fetch_sheet_em_or_ak(code, "profit", "yearly", "年度利润表")
        wait_host("eastmoney")
        df_balance, src_b = self._fetch_sheet_em_or_ak(code, "balance", "yearly", "年度资产负债表")
        wait_host("eastmoney")
        df_cf, src_c = self._fetch_sheet_em_or_ak(code, "cashflow", "yearly", "年度现金流")
        fin_source = "eastmoney" if "eastmoney" in (src_i, src_b, src_c) else "akshare"

        df_income_q = df_balance_q = df_cf_q = None
        if not config.FINANCE_FAST_PATH:
            wait_host("eastmoney")
            df_income_q, _ = self._fetch_sheet_em_or_ak(code, "profit", "quarterly", "季度利润表")
            wait_host("eastmoney")
            df_balance_q, _ = self._fetch_sheet_em_or_ak(code, "balance", "quarterly", "季度资产负债表")
            wait_host("eastmoney")
            df_cf_q, _ = self._fetch_sheet_em_or_ak(code, "cashflow", "quarterly", "季度现金流")

        # 合并年度+季度数据（季度失败时仍保留年度）
        import pandas as pd

        def _merge(annual, quarterly):
            if annual is not None and not annual.empty and quarterly is not None and not quarterly.empty:
                return pd.concat([annual, quarterly]).drop_duplicates(
                    subset=["REPORT_DATE", "REPORT_TYPE"], keep="last"
                )
            if quarterly is not None and not quarterly.empty:
                return quarterly
            return annual

        all_income = _merge(df_income, df_income_q)
        all_balance = _merge(df_balance, df_balance_q)
        all_cf = _merge(df_cf, df_cf_q)

        if all_income is None or (hasattr(all_income, "empty") and all_income.empty):
            self._log(stock_id, "financials", "error", error="年度与季度利润表均为空", source=fin_source)
            return 0

        records = transform_financial_reports(all_income, all_balance, all_cf, stock_id)
        count = self._upsert_batch(
            "financial_reports", records, ["stock_id", "period_end_date", "report_type"]
        )
        from services.data_processor import is_quarterly_report_type

        q_count = sum(
            1 for r in records if is_quarterly_report_type(r.get("report_type", ""), r.get("period_end_date", ""))
        )
        a_count = count - q_count
        self._log(
            stock_id,
            "financials",
            "success" if count else "error",
            count,
            error="" if count else "无财报记录",
            source=fin_source,
        )
        if q_count == 0 and count > 0:
            self._log(stock_id, "financials_quarterly", "error", 0, error="未获取到季度报表", source=fin_source)
        else:
            self._log(stock_id, "financials_quarterly", "success", q_count, source=fin_source)
        self._log(stock_id, "financials_annual", "success", a_count, source=fin_source)
        return count

    def _fetch_financial_indicators(self, stock_id: int, code: str) -> int:
        """财务分析指标：东财 datacenter → 新浪 abstract → akshare"""
        start = time.time()
        exchange_code = to_exchange_code(code)

        try:
            df = em_fin.fetch_financial_indicators_em(code)
            if df is not None and not df.empty:
                rows = transform_financial_indicators(df, stock_id)
                count = self._upsert_batch("financial_indicators", rows, ["stock_id", "calc_date"])
                self._log(stock_id, "indicators", "success", count, duration_ms=int((time.time()-start)*1000), source="eastmoney")
                return count
        except Exception:
            pass

        try:
            df = em_fin.fetch_financial_abstract_sina(code)
            if df is not None and not df.empty:
                rows = _transform_financial_abstract(df, stock_id)
                count = self._upsert_batch("financial_indicators", rows, ["stock_id", "calc_date"])
                self._log(stock_id, "indicators", "success", count, duration_ms=int((time.time()-start)*1000), source="sina")
                return count
        except Exception:
            pass

        try:
            current_year = datetime.now().year
            df = _ak().stock_financial_analysis_indicator(
                symbol=exchange_code, start_year=str(current_year - 5)
            )
            if df is not None and not df.empty:
                rows = transform_financial_indicators(df, stock_id)
                count = self._upsert_batch("financial_indicators", rows, ["stock_id", "calc_date"])
                self._log(stock_id, "indicators", "success", count, duration_ms=int((time.time()-start)*1000), source="akshare")
                return count
        except Exception:
            pass

        try:
            self._ak_sleep()
            df = _ak().stock_financial_abstract(symbol=exchange_code)
            if df is not None and not df.empty:
                rows = _transform_financial_abstract(df, stock_id)
                count = self._upsert_batch("financial_indicators", rows, ["stock_id", "calc_date"])
                self._log(stock_id, "indicators", "success", count, duration_ms=int((time.time()-start)*1000), source="akshare")
                return count
        except Exception as e:
            self._log(stock_id, "indicators", "error", error=str(e), duration_ms=int((time.time()-start)*1000), source="akshare")
            return 0

        self._log(stock_id, "indicators", "success", 0, duration_ms=int((time.time()-start)*1000), source="")
        return 0

    def _fetch_valuation_indicators(self, stock_id: int, code: str) -> int:
        """腾讯财经实时 PE/PB/市值 -> valuation_snapshots（与历史财报指标分离）"""
        start = time.time()
        try:
            quotes = tencent_quote([code])
            if code not in quotes:
                return 0

            q = quotes[code]
            div_yield = self._calc_dividend_yield(code, q["price"])
            as_of = datetime.now().strftime("%Y-%m-%d")

            with write_lock:
                self.conn.execute(
                    """INSERT INTO valuation_snapshots
                       (stock_id, as_of_date, pe_ttm, pb, market_cap, dividend_yield, source)
                       VALUES (?, ?, ?, ?, ?, ?, 'tencent')
                       ON CONFLICT(stock_id, as_of_date) DO UPDATE SET
                         pe_ttm=excluded.pe_ttm, pb=excluded.pb,
                         market_cap=excluded.market_cap, dividend_yield=excluded.dividend_yield""",
                    (
                        stock_id,
                        as_of,
                        q["pe_ttm"] or None,
                        q["pb"] or None,
                        q["mcap_yi"] or None,
                        div_yield,
                    ),
                )
                self._commit()
            self._log(stock_id, "valuation", "success", 1, duration_ms=int((time.time()-start)*1000), source="tencent")
            return 1
        except Exception as e:
            self._log(stock_id, "valuation", "error", error=str(e), duration_ms=int((time.time()-start)*1000), source="tencent")
            return 0

    def _calc_dividend_yield(self, code: str, price: float) -> float | None:
        """从分红历史计算股息率"""
        try:
            from services.data_sources import dividend_history
            divs = dividend_history(code, page_size=1)
            if divs and price > 0:
                return round(divs[0]["bonus_rmb"] / price * 100, 2)
        except Exception:
            pass
        return None

    def _upsert_batch(self, table: str, rows: list[dict], unique_keys: list[str]) -> int:
        if not rows:
            return 0
        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_names = ", ".join(columns)
        conflict_cols = ", ".join(unique_keys)
        update_cols = [c for c in columns if c not in unique_keys]
        if update_cols:
            update_clause = ", ".join(f"{c}=excluded.{c}" for c in update_cols)
            sql = (
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_cols}) DO UPDATE SET {update_clause}"
            )
        else:
            sql = (
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT({conflict_cols}) DO NOTHING"
            )
        values_list = [[row.get(c) for c in columns] for row in rows]
        count = 0
        for attempt in range(4):
            try:
                with write_lock:
                    for values in values_list:
                        cur = self.conn.execute(sql, values)
                        count += max(cur.rowcount, 0)
                    self._commit()
                return count
            except sqlite3.OperationalError as e:
                if "locked" not in str(e).lower() or attempt >= 3:
                    print(f"[Fetcher] 插入失败 {table}: {e}")
                    return count
                time.sleep(0.2 * (attempt + 1))
        return count

    def _retry_fetch(self, fn, label: str, max_retries: int = 3, base_delay: float = 2.0):
        """带 Exponential Backoff 的重试抓取
        Args:
            fn: 抓取函数
            label: 日志标签
            max_retries: 最大重试次数（默认3）
            base_delay: 基础延迟秒数（每次重试乘以 2^(attempt-1)）
        """
        import time as _t
        import os
        max_retries = int(os.environ.get("MAX_RETRIES", str(max_retries)))
        base_delay = float(os.environ.get("CRAWL_DELAY_SEC", str(base_delay)))

        for attempt in range(max_retries + 1):
            try:
                return fn()
            except Exception as e:
                if attempt < max_retries:
                    wait = base_delay * (2 ** attempt)  # 2s → 4s → 8s
                    print(f"[Fetcher] {label} 失败(重试 {attempt+1}/{max_retries}): {str(e)[:60]}, {wait:.0f}s后重试")
                    _t.sleep(wait)
                else:
                    print(f"[Fetcher] {label} 最终失败({attempt}次重试后): {str(e)[:80]}")
        return None
