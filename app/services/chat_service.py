from sqlalchemy.orm import Session
from app.db import SessionLocal
from app.models import (
    AIDocument,
    DocumentSummary,
    DocumentChunk,
    SessionMessage,
)
from app.AIhelpers.llm_helper import askLlm

# Fetches recent messages only
def load_recent_chat_history(
    db: Session,
    session_id: str,
    limit: int = 6,
) -> str:
    messages = (
        db.query(SessionMessage)
        .filter(SessionMessage.session_id == session_id)
        .order_by(SessionMessage.created_at.desc())
        .limit(limit)
        .all()
    )

    messages.reverse()

    return "\n".join(
        f"{m.role.upper()}: {m.content}"
        for m in messages
    )


def chatWithDocument(
    *,
    document_id: int,
    session_id: str,
    query: str,
) -> dict:

    with SessionLocal() as db:

        # --------------------------------------------------
        # 1️⃣ Resolve AI document for this document
        # --------------------------------------------------
        ai_doc = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == document_id)
            .first()
        )

        if not ai_doc:
            return {
                "status": "processing",
                "message": "Document ingestion not completed yet",
            }

        # --------------------------------------------------
        # 2️⃣ Handle recall queries (chat history only)
        # --------------------------------------------------
        if any(x in query.lower() for x in ["last chat", "previous chat", "what we talk"]):
            messages = (
                db.query(SessionMessage)
                .filter(
                    SessionMessage.session_id == session_id,
                    SessionMessage.document_id == document_id,
                )
                .order_by(SessionMessage.created_at.asc())
                .all()
            )

            if not messages:
                answer = "This is the beginning of our conversation."
            else:
                answer = "In our previous chat:\n" + "\n".join(
                    f"{'You asked' if m.role == 'user' else 'I answered'}: {m.content}"
                    for m in messages
                )

            db.add_all([
                SessionMessage(
                    session_id=session_id,
                    document_id=document_id,
                    role="user",
                    content=query,
                ),
                SessionMessage(
                    session_id=session_id,
                    document_id=document_id,
                    role="assistant",
                    content=answer,
                ),
            ])
            db.commit()

            return {
                "status": "success",
                "answer": answer,
                "citations": [],
            }

        # --------------------------------------------------
        # 3️⃣ Load recent chat history
        # --------------------------------------------------
        chat_history = load_recent_chat_history(db, session_id)

        # --------------------------------------------------
        # 4️⃣ Fetch document chunks (AI-owned data)
        # --------------------------------------------------
        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id == ai_doc.id)
            .order_by(DocumentChunk.chunk_index)
            .limit(8)
            .all()
        )

        context_parts = []
        citations = []

        for c in chunks:
            context_parts.append(f"[PAGE {c.page_number}] {c.chunk_text}")
            citations.append({"page_number": c.page_number})

        if not context_parts:
            summary = (
                db.query(DocumentSummary)
                .filter(DocumentSummary.ai_document_id == ai_doc.id)
                .first()
            )
            if summary:
                context_parts.append(summary.summary_text)
                citations = summary.citations or []
            else:
                context_parts.append("No document context available.")

        # --------------------------------------------------
        # 5️⃣ Build final LLM context
        # --------------------------------------------------
        full_context = ""
        if chat_history:
            full_context += f"CHAT HISTORY:\n{chat_history}\n\n"

        full_context += "DOCUMENT:\n" + "\n\n".join(context_parts)

        llm_result = askLlm(
            context=full_context,
            question=query,
        )

        answer = llm_result["data"]["answer"]

        db.add_all([
            SessionMessage(
                session_id=session_id,
                document_id=document_id,
                role="user",
                content=query,
            ),
            SessionMessage(
                session_id=session_id,
                document_id=document_id,
                role="assistant",
                content=answer,
            ),
        ])
        db.commit()

        return {
            "status": "success",
            "answer": answer,
            "citations": citations,
        }