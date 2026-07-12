"""宏观指标自动同步 — 东财 datacenter 主路径，akshare fallback。"""
import sqlite3
import socket
from datetime import date

socket.setdefaulttimeout(8)
from config import DB_PATH


def _ensure_macro_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS macro_indicators (
        date TEXT PRIMARY KEY, gdp REAL, gdp_yoy REAL, cpi REAL, cpi_yoy REAL,
        pmi_manufacturing REAL, pmi_services REAL, lpr_1y REAL, lpr_5y REAL,
        m2 REAL, m2_yoy REAL, shibor_overnight REAL,
        social_financing REAL, social_financing_yoy REAL, social_financing_mom REAL,
        bond_yield_10y REAL, usd_cnh REAL)"""
    )
    for col, ddl in [
        ("social_financing", "social_financing REAL"),
        ("social_financing_yoy", "social_financing_yoy REAL"),
        ("social_financing_mom", "social_financing_mom REAL"),
        ("bond_yield_10y", "bond_yield_10y REAL"),
        ("usd_cnh", "usd_cnh REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE macro_indicators ADD COLUMN {ddl}")
        except sqlite3.OperationalError:
            pass


def _sync_from_tushare() -> dict:
    """统计局官方口径（经 Tushare），比爬 AKShare 更稳定。"""
    from datetime import date as _date, timedelta as _timedelta

    results: dict = {}
    try:
        from services.tushare_adapter import _pro

        pro = _pro()
        today = _date.today()
        start_m = (today - _timedelta(days=400)).strftime("%Y%m")
        end_m = today.strftime("%Y%m")

        try:
            df = pro.cn_cpi(start_m=start_m, end_m=end_m)
            if df is not None and not df.empty:
                last = df.sort_values("month").iloc[-1]
                results["cpi"] = float(last["nt_val"])
                results["cpi_yoy"] = float(last["nt_yoy"])
        except Exception:
            pass

        try:
            df = pro.cn_pmi(start_m=start_m, end_m=end_m, fields="month,pmi010000")
            if df is not None and not df.empty:
                last = df.sort_values("month").iloc[-1]
                results["pmi_manufacturing"] = float(last["pmi010000"])
        except Exception:
            pass

        try:
            df = pro.cn_m(start_m=start_m, end_m=end_m)
            if df is not None and not df.empty:
                last = df.sort_values("month").iloc[-1]
                results["m2"] = float(last["m2"])
                results["m2_yoy"] = float(last["m2_yoy"])
        except Exception:
            pass

        try:
            start_d = (today - _timedelta(days=10)).strftime("%Y%m%d")
            end_d = today.strftime("%Y%m%d")
            df = pro.shibor(start_date=start_d, end_date=end_d)
            if df is not None and not df.empty:
                last = df.sort_values("date").iloc[-1]
                results["shibor_overnight"] = float(last["on"])
        except Exception:
            pass

        try:
            start_q = f"{today.year - 2}Q1"
            end_q = f"{today.year}Q4"
            df = pro.cn_gdp(start_q=start_q, end_q=end_q)
            if df is not None and not df.empty:
                last = df.sort_values("quarter").iloc[-1]
                results["gdp"] = float(last["gdp"])
                results["gdp_yoy"] = float(last["gdp_yoy"])
        except Exception:
            pass

        try:
            df = pro.sf_month(start_m=start_m, end_m=end_m)
            if df is not None and not df.empty:
                last = df.sort_values("month").iloc[-1]
                results["social_financing"] = float(last["inc_month"])
        except Exception:
            pass
    except Exception as e:
        return {"error": str(e)}
    return results


def _sync_from_akshare() -> dict:
    results: dict = {}
    try:
        from services.akshare_lazy import akshare as _ak

        ak = _ak()

        try:
            df_cpi = ak.macro_china_cpi()
            if df_cpi is not None and not df_cpi.empty:
                last = df_cpi.iloc[-1]
                results["cpi"] = float(last["cpi"] if "cpi" in df_cpi.columns else last.iloc[1])
                results["cpi_yoy"] = float(
                    last["cpi_yoy"] if "cpi_yoy" in df_cpi.columns else last.iloc[2]
                )
        except Exception:
            pass

        try:
            df_pmi = ak.macro_china_pmi()
            if df_pmi is not None and not df_pmi.empty:
                last_p = df_pmi.iloc[-1]
                results["pmi_manufacturing"] = float(
                    last_p["制造业"] if "制造业" in df_pmi.columns else last_p.iloc[1]
                )
        except Exception:
            try:
                df_pmi2 = ak.macro_china_manufacturing_pmi()
                if df_pmi2 is not None and not df_pmi2.empty:
                    results["pmi_manufacturing"] = float(df_pmi2.iloc[-1, 1])
            except Exception:
                pass

        try:
            df_lpr = ak.macro_china_lpr()
            if df_lpr is not None and not df_lpr.empty:
                last_l = df_lpr.iloc[-1]
                results["lpr_1y"] = float(
                    last_l["1年期"] if "1年期" in df_lpr.columns else last_l.get("LPR1Y", last_l.iloc[1])
                )
                results["lpr_5y"] = float(
                    last_l["5年期"] if "5年期" in df_lpr.columns else last_l.get("LPR5Y", last_l.iloc[2])
                )
        except Exception:
            pass

        try:
            df_m2 = ak.macro_china_money_supply()
            if df_m2 is not None and not df_m2.empty:
                last_m = df_m2.iloc[-1]
                results["m2_yoy"] = float(last_m["m2"] if "m2" in df_m2.columns else last_m.iloc[3])
        except Exception:
            pass

        try:
            df_sb = ak.rate_interbank(market="上海银行间同业拆放利率", symbol="Shibor")
            if df_sb is not None and not df_sb.empty:
                last_s = df_sb.iloc[-1]
                on_idx = 1 if len(df_sb.columns) > 1 else 0
                results["shibor_overnight"] = float(last_s.iloc[on_idx])
        except Exception:
            pass

        try:
            df_gdp = ak.macro_china_gdp()
            if df_gdp is not None and not df_gdp.empty:
                last_g = df_gdp.iloc[-1]
                results["gdp"] = float(last_g["gdp"] if "gdp" in df_gdp.columns else last_g.iloc[1])
                results["gdp_yoy"] = float(
                    last_g["gdp_yoy"] if "gdp_yoy" in df_gdp.columns else last_g.iloc[2]
                )
        except Exception:
            pass
    except Exception as e:
        return {"error": str(e)}
    return results


def _latest_macro_row(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT * FROM macro_indicators ORDER BY date DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    cols = [d[0] for d in conn.execute("PRAGMA table_info(macro_indicators)")]
    return dict(zip(cols, row))


def backfill_macro_rates(*, days: int = 252) -> dict:
    """回填近 N 个交易日 10Y 国债 / USD-CNH（按交易日对齐）。"""
    from services.eastmoney_macro import fetch_bond_yield_series, fetch_usd_cnh_series

    conn = sqlite3.connect(DB_PATH)
    _ensure_macro_table(conn)
    trade_dates = [
        r[0]
        for r in conn.execute(
            """SELECT DISTINCT trade_date FROM stock_daily_quotes
               WHERE close IS NOT NULL ORDER BY trade_date DESC LIMIT ?""",
            (days,),
        ).fetchall()
    ]
    trade_dates = sorted(trade_dates)
    bonds = fetch_bond_yield_series(days=days)
    fx = fetch_usd_cnh_series(days=days)

    last_bond = last_fx = None
    written = 0
    for dt in trade_dates:
        if dt in bonds:
            last_bond = bonds[dt]
        elif bonds:
            prior = [d for d in bonds if d <= dt]
            if prior:
                last_bond = bonds[max(prior)]
        if dt in fx:
            last_fx = fx[dt]
        elif fx:
            prior = [d for d in fx if d <= dt]
            if prior:
                last_fx = fx[max(prior)]
        if last_bond is None and last_fx is None:
            continue
        prev = conn.execute(
            "SELECT * FROM macro_indicators WHERE date=?", (dt,)
        ).fetchone()
        if prev:
            conn.execute(
                """UPDATE macro_indicators SET
                   bond_yield_10y=COALESCE(?, bond_yield_10y),
                   usd_cnh=COALESCE(?, usd_cnh)
                   WHERE date=?""",
                (last_bond, last_fx, dt),
            )
        else:
            conn.execute(
                """INSERT INTO macro_indicators (date, bond_yield_10y, usd_cnh)
                   VALUES (?,?,?)""",
                (dt, last_bond, last_fx),
            )
        written += 1
    conn.commit()
    conn.close()
    return {"trade_dates": len(trade_dates), "rows_written": written}


def sync_macro_indicators(*, backfill_rates: bool = True) -> dict:
    """同步全部宏观指标到数据库。"""
    today = date.today().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    _ensure_macro_table(conn)

    results: dict = {}
    source = "tushare"
    ts_results = _sync_from_tushare()
    for k, v in ts_results.items():
        if k != "error" and v is not None:
            results[k] = v

    if len(results) < 3:
        source = "eastmoney"
        try:
            from services.eastmoney_macro import fetch_latest_macro

            em = fetch_latest_macro()
            for k, v in em.items():
                if k != "source" and v is not None and results.get(k) is None:
                    results[k] = v
        except Exception as e:
            print(f"[macro] 东财同步失败: {e}")

    if len(results) < 3:
        source = "akshare"
        ak_results = _sync_from_akshare()
        if "error" in ak_results:
            conn.close()
            return ak_results
        for k, v in ak_results.items():
            if v is not None and results.get(k) is None:
                results[k] = v
    elif any(results.get(k) is None for k in ("cpi_yoy", "pmi_manufacturing", "m2_yoy")):
        try:
            from services.eastmoney_macro import fetch_latest_macro

            em_fill = fetch_latest_macro()
            for k, v in em_fill.items():
                if k != "source" and v is not None and results.get(k) is None:
                    results[k] = v
        except Exception:
            pass
        if any(results.get(k) is None for k in ("cpi_yoy", "pmi_manufacturing", "m2_yoy")):
            ak_fill = _sync_from_akshare()
            for k, v in ak_fill.items():
                if v is not None and results.get(k) is None:
                    results[k] = v

    prev = _latest_macro_row(conn) or {}
    bond = results.get("bond_yield_10y") or prev.get("bond_yield_10y")
    usd = results.get("usd_cnh") or prev.get("usd_cnh")
    if bond is not None:
        results["bond_yield_10y"] = bond
    if usd is not None:
        results["usd_cnh"] = usd

    conn.execute(
        """INSERT OR REPLACE INTO macro_indicators
        (date, gdp, gdp_yoy, cpi, cpi_yoy, pmi_manufacturing, pmi_services,
         lpr_1y, lpr_5y, m2, m2_yoy, shibor_overnight,
         social_financing, social_financing_yoy, social_financing_mom,
         bond_yield_10y, usd_cnh)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            today,
            results.get("gdp") or prev.get("gdp"),
            results.get("gdp_yoy") or prev.get("gdp_yoy"),
            results.get("cpi") or prev.get("cpi"),
            results.get("cpi_yoy") or prev.get("cpi_yoy"),
            results.get("pmi_manufacturing") or prev.get("pmi_manufacturing"),
            results.get("pmi_services") or prev.get("pmi_services"),
            results.get("lpr_1y") or prev.get("lpr_1y"),
            results.get("lpr_5y") or prev.get("lpr_5y"),
            results.get("m2") or prev.get("m2"),
            results.get("m2_yoy") or prev.get("m2_yoy"),
            results.get("shibor_overnight") or prev.get("shibor_overnight"),
            results.get("social_financing") or prev.get("social_financing"),
            results.get("social_financing_yoy") or prev.get("social_financing_yoy"),
            results.get("social_financing_mom") or prev.get("social_financing_mom"),
            bond,
            usd,
        ),
    )
    conn.commit()
    conn.close()

    backfill_result = None
    if backfill_rates:
        try:
            backfill_result = backfill_macro_rates(days=252)
        except Exception as e:
            backfill_result = {"error": str(e)}

    return {
        "date": today,
        "indicators": {k: v for k, v in results.items() if v is not None},
        "source": source,
        "backfill": backfill_result,
    }


def get_macro_score() -> dict:
    """计算宏观环境评分 (0-100)"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM macro_indicators ORDER BY date DESC LIMIT 1").fetchone()
    conn.close()

    if not row:
        return {"score": 50, "label": "无数据"}

    row = dict(row)
    score = 50

    pmi = row.get("pmi_manufacturing")
    if pmi:
        score += min((pmi - 50) * 2, 10)

    cpi = row.get("cpi_yoy")
    if cpi:
        if cpi > 5:
            score -= 10
        elif cpi < 0:
            score -= 10

    lpr = row.get("lpr_1y")
    if lpr and lpr < 3.5:
        score += 5

    sf_yoy = row.get("social_financing_yoy")
    if sf_yoy is not None:
        if sf_yoy > 10:
            score += 3
        elif sf_yoy < -10:
            score -= 5

    bond_10y = row.get("bond_yield_10y")
    if bond_10y is not None and bond_10y > 2.8:
        score -= 3

    usd_cnh = row.get("usd_cnh")
    if usd_cnh is not None and usd_cnh > 7.3:
        score -= 2

    score = max(0, min(100, score))

    label = "偏松" if score >= 60 else ("偏紧" if score < 40 else "中性")
    return {"score": round(score, 1), "label": label, "indicators": row}
