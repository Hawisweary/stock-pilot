"""投资学习助手 API。DeepSeek 问答,严格约束在\"投教\"范围:只解释概念/指标/策略/软件用法,
不荐股、不预测涨跌、不给个性化投资建议。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api/assistant", tags=["assistant"])

_SYSTEM = """你是"投资学习助手",服务于一个股票研究软件里的新手用户。你的唯一职责是**投资者教育**:
- 用通俗中文解释:交易概念(股票/ETF/指数/复权等)、财务与技术指标(PE/PB/ROE/MACD/KDJ 等)、
  交易策略(价值/成长/趋势/定投/分散)、风控纪律、以及"本软件某个功能怎么用"。
- 回答简洁、面向零基础,可举例,但**不长篇大论**。

**红线(必须遵守)**:
- 绝不推荐买卖任何具体股票/标的,绝不预测价格涨跌或点位,绝不给个性化投资建议。
- 若用户问"我该买X吗 / X会涨吗 / 帮我推荐股票",礼貌拒绝,并转而解释相关的**知识**
  (例如如何自己分析、该看哪些指标),同时提醒"决策需你自己做,投资有风险"。
- 不编造数据或事实;不确定就直说不知道。
- 只聊投资学习相关话题;无关话题礼貌带回。
回答默认用中文。"""

_REFUSAL_HINT = "(注意:不要直接给出买卖建议或涨跌预测。)"


class AskReq(BaseModel):
    question: str
    context: str | None = None  # 可选:用户当前在看的页面/标的,用于结合场景解释


@router.get("/status")
def status():
    from services.llm_client import is_llm_available
    return {"available": is_llm_available()}


@router.post("/ask")
def ask(req: AskReq):
    q = (req.question or "").strip()[:600]
    if not q:
        return {"answer": "请输入你想了解的问题,比如\"什么是市盈率?\"或\"选股筛选怎么用?\""}
    try:
        from services.llm_client import chat_completion, is_llm_available
    except Exception as ex:  # noqa: BLE001
        return {"answer": None, "error": f"助手不可用: {str(ex)[:60]}"}
    if not is_llm_available():
        return {"answer": None, "error": "未配置 LLM,助手暂不可用"}

    ctx = f"\n用户当前正在看:{req.context.strip()[:120]}\n" if req.context else ""
    prompt = f"{ctx}用户问题:{q}\n\n请在投教范围内简洁作答。{_REFUSAL_HINT}"
    try:
        ans = chat_completion(prompt, system_prompt=_SYSTEM, max_tokens=700,
                              temperature=0.5, max_retries=1)
        return {"answer": ans.strip()}
    except Exception as ex:  # noqa: BLE001
        return {"answer": None, "error": f"回答失败: {str(ex)[:80]}"}
