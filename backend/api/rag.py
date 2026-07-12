"""财报 PDF RAG API"""
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from config import ENABLE_RAG
from services import report_rag

router = APIRouter(prefix="/api/rag", tags=["rag"])


def _check_enabled():
    if not ENABLE_RAG:
        raise HTTPException(status_code=503, detail="RAG 功能未启用 (AFR_ENABLE_RAG=false)")


@router.get("/stocks/{stock_id}/documents")
def list_docs(stock_id: int):
    _check_enabled()
    return {"documents": report_rag.list_documents(stock_id)}


@router.post("/stocks/{stock_id}/upload")
async def upload_pdf(stock_id: int, file: UploadFile = File(...)):
    _check_enabled()
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="仅支持 PDF 文件")
    raw = await file.read()
    if len(raw) > 25 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件过大（上限 25MB）")
    try:
        result = report_rag.ingest_pdf(stock_id, file.filename, raw)
        return {"ok": True, **result}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


class RagQuestion(BaseModel):
    question: str
    use_llm: bool = True


@router.post("/stocks/{stock_id}/ask")
def ask(stock_id: int, body: RagQuestion):
    _check_enabled()
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    return report_rag.answer_question(stock_id, body.question.strip(), body.use_llm)


@router.get("/stocks/{stock_id}/search")
def search(stock_id: int, q: str, limit: int = 5):
    _check_enabled()
    return {"hits": report_rag.search_chunks(stock_id, q, limit=limit)}
