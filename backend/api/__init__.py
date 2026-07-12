"""
API 路由注册模块
"""
from fastapi import FastAPI

from config import (
    ENABLE_ADVANCED,
    ENABLE_BACKTEST,
    ENABLE_FACTOR_LAB,
    ENABLE_PORTFOLIO,
    ENABLE_PREMIUM,
    ENABLE_RAG,
)


def register_blueprints(app: FastAPI):
    """注册所有 API 路由（核心 + 可开关实验模块）"""
    from api.stocks import router as stocks_router
    from api.data import router as data_router
    from api.financials import router as financials_router
    from api.scores import router as scores_router
    from api.dashboard import router as dashboard_router
    from api.ai import router as ai_router
    from api.analysis import router as analysis_router
    from api.technical import router as technical_router
    from api.groups import router as groups_router
    from api.report import router as report_router
    from api.news import router as news_router
    from api.capital import router as capital_router
    from api.policy import router as policy_router
    from api.sentiment import router as sentiment_router
    from api.system import router as system_router
    from api.signals import router as signals_router
    from api.risk import router as risk_router
    from api.v5_data import router as v5_data_router
    from api.alerts import router as alerts_router
    from api.screener import router as screener_router
    from api.calendar import router as calendar_router

    # 核心
    app.include_router(risk_router)
    app.include_router(v5_data_router)
    app.include_router(alerts_router)
    app.include_router(screener_router)
    app.include_router(calendar_router)
    app.include_router(stocks_router)
    app.include_router(data_router)
    app.include_router(financials_router)
    app.include_router(scores_router)
    app.include_router(dashboard_router)
    app.include_router(ai_router)
    app.include_router(analysis_router)
    app.include_router(technical_router)
    app.include_router(groups_router)
    app.include_router(report_router)
    app.include_router(news_router)
    app.include_router(capital_router)
    app.include_router(policy_router)
    app.include_router(sentiment_router)
    app.include_router(system_router)
    app.include_router(signals_router)

    if ENABLE_RAG:
        from api.rag import router as rag_router
        app.include_router(rag_router)

    if ENABLE_BACKTEST:
        from api.backtest import router as backtest_router
        app.include_router(backtest_router)

    if ENABLE_BACKTEST or ENABLE_FACTOR_LAB or ENABLE_ADVANCED:
        from api.qlib import router as qlib_router
        app.include_router(qlib_router)

    if ENABLE_PORTFOLIO:
        from api.portfolio import router as portfolio_router
        app.include_router(portfolio_router)

    if ENABLE_FACTOR_LAB or ENABLE_ADVANCED:
        from api.advanced import router as advanced_router
        from api.strategy import router as strategy_router
        app.include_router(advanced_router)
        app.include_router(strategy_router)

    if ENABLE_PREMIUM:
        from api.premium import router as premium_router
        app.include_router(premium_router)

    from api.realtime import router as realtime_router
    app.include_router(realtime_router)
    from api.market import router as market_router
    app.include_router(market_router)
