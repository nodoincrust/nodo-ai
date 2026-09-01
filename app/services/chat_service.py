from typing import List
from sqlalchemy.orm import Session

# from sqlalchemy import func
from sqlalchemy.exc import SQLAlchemyError
from app.services.ai_db_service import getOrCreateSessionForDocument
import logging

from app.db import SessionLocal
from app.models import (
    AIDocument,
    DocumentChunk,
    DocumentSummary,
    SessionMessage,
    SessionMemorySummary,DocumentVersion
)
from app.AIhelpers.embedding_helper import createEmbedding
from app.AIhelpers.llm_helper import askLlm
from app.services.background_tasks import submitMemoryUpdate

logger = logging.getLogger("ai.chatHistoryService")

# Chunks are ~1024 words each, so each one is a large slice of prompt. On a
# CPU-only box the model spends longer reading the prompt than writing the
# answer, which makes a high TOP_K the main cause of slow chat replies.
TOP_K = 4  # number of chunks to retrieve
MAX_CHAT_HISTORY = 6  # recent messages only

# Generation costs ~32x more per token than reading context on this hardware,
# so the answer length is the latency dial, not the context size.
CHAT_CONTEXT_CHARS = 5000   # ~1600 tokens, ~9s to read
CHAT_NUM_PREDICT = 160      # ~30s to write, the bulk of the wait

CHAT_SYSTEM_PROMPT = """
You are a document-grounded AI ASSISTANT.
when anyone can request about your information then you can simply return 
My name is NODO-AI ASSISTANT and my work is to help you
 
Rules:
- Answer ONLY using the provided document context
- Do NOT output JSON
- Be concise, factual, and helpful
- Answer in at most 3-4 sentences unless the user asks for more detail
- If the information is not present in the document, say so clearly
"""

def load_recent_chat_history(
    db: Session,
    session_id: str,
    limit: int = MAX_CHAT_HISTORY,
) -> str:
    messages = (
        db.query(SessionMessage)
        .filter(SessionMessage.session_id == session_id)
        .order_by(SessionMessage.created_at.desc())
        .limit(limit)
        .all()
    )

    messages.reverse()

    return "\n".join(f"{m.role.upper()}: {m.content}" for m in messages)


def semantic_search_chunks(
    db: Session,
    *,
    ai_document_id: int,
    query_embedding: List[float],
    top_k: int = TOP_K,
) -> List[DocumentChunk]:
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.ai_document_id == ai_document_id)
        .order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
        .limit(top_k)
        .all()
    )


def semantic_search_chunks(
    db: Session,
    *,
    ai_document_id: int,
    query_embedding: List[float],
    top_k: int = TOP_K,
) -> List[DocumentChunk]:
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.ai_document_id == ai_document_id)
        .order_by(
            DocumentChunk.embedding.cosine_distance(query_embedding)
        )
        .limit(top_k)
        .all()
    )

def chatWithDocument(
    *,
    document_id: int,
    session_id: str,
    query: str,
) -> dict:

    with SessionLocal() as db:

        ai_doc = (
            db.query(AIDocument).filter(AIDocument.document_id == document_id).first()
        )

        if not ai_doc:
            return {
                "status": "processing",
                "message": "Document ingestion not completed yet",
            }

        db.add(
            SessionMessage(
                session_id=session_id,
                document_id=ai_doc.id,
                role="user",
                content=query,
            )
        )
        db.commit()

        chat_history = load_recent_chat_history(db, session_id)

        memory = db.query(SessionMemorySummary).filter_by(session_id=session_id).first()

        query_embedding = createEmbedding(query.strip().lower())

        # Sementic search accross the chunks
        top_chunks = semantic_search_chunks(
            db,
            ai_document_id=ai_doc.id,
            query_embedding=query_embedding,
            top_k=TOP_K,
        )

        context_parts = []
        citations = []

        for c in top_chunks:
            context_parts.append(f"[PAGE {c.page_number}] {c.chunk_text}")
            citations.append({"page_number": c.page_number})

        if not context_parts:
            summary = (
                db.query(DocumentSummary)
                .filter(DocumentSummary.ai_document_id == ai_doc.id)
                .first()
            )

            if summary and summary.summary_text:
                context_parts.append(
                    "High-level document summary (may be incomplete):\n"
                    + summary.summary_text
                )
                citations = summary.citations or []
            else:
                context_parts.append(
                    "The provided document does not contain relevant information."
                )

        final_context_parts = []

        if memory and memory.summary:
            final_context_parts.append(
                f"MEMORY SUMMARY (previous conversation):\n{memory.summary}"
            )

        if chat_history:
            final_context_parts.append(f"RECENT CHAT HISTORY:\n{chat_history}")

        final_context_parts.append("DOCUMENT CONTEXT:\n" + "\n\n".join(context_parts))

        final_context = "\n\n".join(final_context_parts)

        llm_result = askLlm(
            context=final_context,
            question=query,
            system_prompt=CHAT_SYSTEM_PROMPT,
            context_chars=CHAT_CONTEXT_CHARS,
            num_predict=CHAT_NUM_PREDICT,
        )

        if llm_result.get("status") != "success":
            logger.error("LLM failed: %s", llm_result)
            answer = (
                "I’m unable to answer that question based on the document right now."
            )
        else:
            answer = llm_result["data"]["answer"]

        db.add(
            SessionMessage(
                session_id=session_id,
                document_id=ai_doc.id,
                role="assistant",
                content=answer,
            )
        )
        db.commit()

        submitMemoryUpdate(session_id)

        return {
            "status": "success",
            "answer": answer,
            "citations": citations,
        }

 
def fetchChatHistory(
    *,
    documentId: int,
) -> dict:
 
    response = {
        "status": "empty",
        "documentId": documentId,
        "sessionId": None,
        "messages": [],
        "error": None,
    }
 
    db: Session = SessionLocal()
 
    try:
        ai_doc = (
            db.query(AIDocument)
            .filter(
                AIDocument.document_id == documentId,
            )
            .first()
        )
 
        if not ai_doc:
            response["status"] = "error"
            response["error"] = "AI document not found for this version"
            return response
 
        session_id = ai_doc.session_id
        response["sessionId"] = str(session_id)
 
        # 2️⃣ Fetch full chat history (ALL TIME)
        messages = (
            db.query(SessionMessage)
            .filter(SessionMessage.session_id == session_id)
            .order_by(SessionMessage.created_at.asc())
            .all()
        )
 
        response["messages"] = [
            {
                "role": m.role,
                "content": m.content,
                "created_at": m.created_at.isoformat() if m.created_at else None,
            }
            for m in messages
        ]
 
        response["status"] = "success" if messages else "empty"
        return response
 
    except SQLAlchemyError:
        logger.exception("Chat history fetch failed")
        response["status"] = "error"
        response["error"] = "Database error"
        return response
 
    finally:
        db.close()