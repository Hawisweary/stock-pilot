"""
财报 PDF 文本提取 + 分块检索 + 简易问答（关键词匹配，可选 LLM 总结）
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from typing import Any

from config import DATA_DIR, DB_PATH, DEEPSEEK_API_KEY, LLM_BASE_URL, LLM_MODEL

REPORTS_DIR = os.path.join(DATA_DIR, "reports")
CHUNK_SIZE = 900
CHUNK_OVERLAP = 120


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def ensure_reports_dir() -> str:
    os.makedirs(REPORTS_DIR, exist_ok=True)
    return REPORTS_DIR


def extract_pdf_text(file_path: str) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("请安装 pypdf: pip install pypdf") from e

    reader = PdfReader(file_path)
    parts = []
    for page in reader.pages:
        t = page.extract_text() or ""
        if t.strip():
            parts.append(t.strip())
    return "\n\n".join(parts)


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def ingest_pdf(stock_id: int, file_name: str, raw: bytes, title: str = "") -> dict[str, Any]:
    ensure_reports_dir()
    content_hash = _content_hash(raw)
    safe_name = re.sub(r"[^\w.\-]", "_", file_name)[:120]
    rel_path = os.path.join("reports", str(stock_id), f"{content_hash[:12]}_{safe_name}")
    abs_path = os.path.join(DATA_DIR, rel_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)

    with open(abs_path, "wb") as f:
        f.write(raw)

    text = extract_pdf_text(abs_path)
    chunks = chunk_text(text)
    if not chunks:
        raise ValueError("未能从 PDF 提取文本")

    db = _connect()
    try:
        existing = db.execute(
            "SELECT id FROM report_documents WHERE stock_id=? AND content_hash=?",
            (stock_id, content_hash),
        ).fetchone()
        if existing:
            doc_id = existing["id"]
            db.execute("DELETE FROM report_chunks WHERE document_id=?", (doc_id,))
        else:
            cur = db.execute(
                """
                INSERT INTO report_documents
                (stock_id, title, file_name, file_path, content_hash, extracted_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    stock_id,
                    title or safe_name,
                    file_name,
                    rel_path,
                    content_hash,
                    text[:500_000],
                ),
            )
            doc_id = cur.lastrowid

        for i, ch in enumerate(chunks):
            cur = db.execute(
                "INSERT INTO report_chunks (document_id, chunk_index, content) VALUES (?, ?, ?)",
                (doc_id, i, ch),
            )
            chunk_id = cur.lastrowid
            try:
                db.execute(
                    """
                    INSERT INTO report_chunks_fts (chunk_id, document_id, stock_id, content)
                    VALUES (?, ?, ?, ?)
                    """,
                    (chunk_id, doc_id, stock_id, ch),
                )
            except sqlite3.OperationalError:
                pass
        db.commit()
        return {
            "document_id": doc_id,
            "stock_id": stock_id,
            "chunks": len(chunks),
            "title": title or safe_name,
            "content_hash": content_hash,
        }
    finally:
        db.close()


def list_documents(stock_id: int) -> list[dict]:
    db = _connect()
    try:
        rows = db.execute(
            """
            SELECT id, title, file_name, created_at,
                   (SELECT COUNT(*) FROM report_chunks c WHERE c.document_id=d.id) as chunk_count
            FROM report_documents d
            WHERE stock_id=?
            ORDER BY created_at DESC
            """,
            (stock_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        db.close()


def _fts_query_escape(q: str) -> str:
    tokens = [t for t in re.split(r"\W+", q) if len(t) >= 2]
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens[:12])


def search_chunks(stock_id: int, query: str, limit: int = 5) -> list[dict]:
    """FTS5 全文检索，失败时回退关键词匹配"""
    fts_q = _fts_query_escape(query)
    db = _connect()
    try:
        if fts_q:
            try:
                rows = db.execute(
                    """
                    SELECT c.id, c.chunk_index, c.content, d.title, d.id as document_id
                    FROM report_chunks_fts f
                    JOIN report_chunks c ON c.id = f.chunk_id
                    JOIN report_documents d ON d.id = c.document_id
                    WHERE f.stock_id = ? AND f MATCH ?
                    ORDER BY rank
                    LIMIT ?
                    """,
                    (stock_id, fts_q, limit),
                ).fetchall()
                if rows:
                    return [dict(r) for r in rows]
            except sqlite3.OperationalError:
                pass

        q_tokens = [t for t in re.split(r"\W+", query.lower()) if len(t) >= 2]
        if not q_tokens:
            return []
        rows = db.execute(
            """
            SELECT c.id, c.chunk_index, c.content, d.title, d.id as document_id
            FROM report_chunks c
            JOIN report_documents d ON d.id = c.document_id
            WHERE d.stock_id=?
            """,
            (stock_id,),
        ).fetchall()
        scored = []
        for r in rows:
            text = (r["content"] or "").lower()
            score = sum(1 for t in q_tokens if t in text)
            if score > 0:
                scored.append((score, dict(r)))
        scored.sort(key=lambda x: -x[0])
        return [item[1] for item in scored[:limit]]
    finally:
        db.close()


def rebuild_fts_index(stock_id: int | None = None) -> int:
    """为已有 chunk 重建 FTS 索引"""
    db = _connect()
    n = 0
    try:
        db.execute("DELETE FROM report_chunks_fts")
        sql = """
            SELECT c.id, c.document_id, d.stock_id, c.content
            FROM report_chunks c
            JOIN report_documents d ON d.id = c.document_id
        """
        params: tuple = ()
        if stock_id is not None:
            sql += " WHERE d.stock_id=?"
            params = (stock_id,)
        for row in db.execute(sql, params).fetchall():
            db.execute(
                """
                INSERT INTO report_chunks_fts (chunk_id, document_id, stock_id, content)
                VALUES (?, ?, ?, ?)
                """,
                (row["id"], row["document_id"], row["stock_id"], row["content"]),
            )
            n += 1
        db.commit()
    finally:
        db.close()
    return n


def answer_question(stock_id: int, question: str, use_llm: bool = True) -> dict[str, Any]:
    hits = search_chunks(stock_id, question, limit=6)
    if not hits:
        return {
            "answer": "未找到相关财报片段，请先上传 PDF 年报/半年报。",
            "sources": [],
            "source": "none",
        }

    context = "\n\n---\n\n".join(
        f"[{h.get('title', '文档')} #{h['chunk_index']}]\n{h['content'][:1200]}"
        for h in hits
    )

    if use_llm and DEEPSEEK_API_KEY:
        try:
            import httpx

            prompt = (
                "你是基本面分析师。根据以下财报摘录回答用户问题，"
                "仅依据摘录内容，不足则说明。用中文简洁回答。\n\n"
                f"摘录：\n{context}\n\n问题：{question}"
            )
            resp = httpx.post(
                f"{LLM_BASE_URL.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
                json={
                    "model": LLM_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
                timeout=60.0,
            )
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"]
            return {"answer": answer, "sources": hits, "source": "llm"}
        except Exception as e:
            print(f"[RAG] LLM 失败: {e}")

    # 规则兜底：返回最相关片段摘要
    best = hits[0]["content"][:800]
    return {
        "answer": f"（摘录摘要）{best}",
        "sources": hits,
        "source": "rules",
    }
