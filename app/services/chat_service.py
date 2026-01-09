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

    with SessionLocal() as db:  # Opens DB session

        # Handle chat-history recall deterministically
        if "last chat" in query.lower() or "previous chat" in query.lower() or "what we talk" in query.lower():
            messages = (
                db.query(SessionMessage)
                .filter(SessionMessage.session_id == session_id)
                .order_by(SessionMessage.created_at.asc())
                .all()
            )

            if not messages:
                answer = "This is the beginning of our conversation."
            else:
                lines = []
                for m in messages:
                    who = "You asked" if m.role == "user" else "I answered"
                    lines.append(f"{who}: {m.content}")
                answer = "In our previous chat:\n" + "\n".join(lines)

            db.add_all([
                SessionMessage(session_id=session_id, role="user", content=query),
                SessionMessage(session_id=session_id, role="assistant", content=answer),
            ])  # Stores recall interaction
            db.commit()

            return {"status": "success", "answer": answer, "citations": []}

        ai_doc = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == document_id)
            .first()
        )  # Resolves AI document

        if not ai_doc:
            return {
                "status": "processing",
                "message": "Document ingestion not completed yet",
            }  # Guards early chat calls

        chat_history = load_recent_chat_history(db, session_id)  # Loads recent context

        chunks = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id == ai_doc.id)
            .order_by(DocumentChunk.chunk_index)
            .limit(8)
            .all()
        )  # Retrieves top chunks for context

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
                context_parts.append("No document context available.")  # Handles general questions

        full_context_parts = []

        if chat_history:
            full_context_parts.append(f"CHAT HISTORY:\n{chat_history}")

        full_context_parts.append("DOCUMENT:\n" + "\n\n".join(context_parts))

        full_context = "\n\n".join(full_context_parts)  # Builds final LLM context

        llm_result = askLlm(
            context=full_context,
            question=query,
        )

        answer = llm_result["data"]["answer"]

        db.add_all([
            SessionMessage(
                session_id=session_id,
                document_id=ai_doc.id,
                role="user",
                content=query,
            ),
            SessionMessage(
                session_id=session_id,
                document_id=ai_doc.id,
                role="assistant",
                content=answer,
            ),
        ])  # Persists conversation
        db.commit()

        return {
            "status": "success",
            "answer": answer,
            "citations": citations,
        }