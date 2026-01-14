from typing import Optional, List, Tuple
from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import SessionLocal
from app.models import (
    AIDocument,
    DocumentChunk,
    DocumentSummary,
    ChatSession,
    SessionMessage,
    SessionMemorySummary,
)

def getOrCreateSessionForDocument(documentId: int) -> str:
    db: Session = SessionLocal()
    try:
        ai_doc = (
            db.query(AIDocument)
            .filter(AIDocument.document_id == documentId)
            .first()
        )

        # create if missing
        if not ai_doc:
            ai_doc = AIDocument(document_id=documentId)
            db.add(ai_doc)
            db.flush()

        # return existing session
        if ai_doc.session_id:
            return str(ai_doc.session_id)

        # create session
        session = ChatSession()
        db.add(session)
        db.flush()

        ai_doc.session_id = session.session_id
        db.commit()

        return str(session.session_id)

    finally:
        db.close()


# ======================================================
# DOCUMENT CHUNKS
# ======================================================

def storeDocumentChunk(
    db: Session,
    *,
    documentId: int,
    sessionId: Optional[str],
    chunkText: str,
    embedding: List[float],
    chunkIndex: int,
    pageNumber: Optional[int] = None,
) -> None:
    db.add(
        DocumentChunk(
            id=uuid.uuid4(),
            document_id=documentId,
            session_id=sessionId,
            chunk_text=chunkText,
            embedding=embedding,
            chunk_index=chunkIndex,
            page_number=pageNumber,
        )
    )


def fetchDocumentChunks(
    db: Session,
    *,
    documentId: int,
) -> List[str]:
    rows = (
        db.query(DocumentChunk.chunk_text)
        .filter(DocumentChunk.document_id == documentId)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    return [row.chunk_text for row in rows]


def semanticSearchChunks(
    db: Session,
    *,
    documentId: int,
    queryEmbedding: List[float],
    limit: int = 5,
) -> List[DocumentChunk]:
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == documentId)
        .order_by(DocumentChunk.embedding.l2_distance(queryEmbedding))
        .limit(limit)
        .all()
    )

# ======================================================
# DOCUMENT SUMMARY
# ======================================================

def upsertDocumentSummary(
    db: Session,
    *,
    documentId: int,
    summaryText: str,
) -> None:
    existing = (
        db.query(DocumentSummary)
        .filter(DocumentSummary.document_id == documentId)
        .first()
    )

    if existing:
        existing.summary_text = summaryText
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(
            DocumentSummary(
                document_id=documentId,
                summary_text=summaryText,
            )
        )

    db.commit()


def getDocumentSummary(
    db: Session,
    *,
    documentId: int,
) -> Optional[str]:
    summary = (
        db.query(DocumentSummary)
        .filter(DocumentSummary.document_id == documentId)
        .first()
    )
    return summary.summary_text if summary else None

# ======================================================
# SESSION MESSAGES
# ======================================================

def addSessionMessage(
    db: Session,
    *,
    sessionId: str,
    role: str,
    content: str,
) -> None:
    db.add(
        SessionMessage(
            id=uuid.uuid4(),
            session_id=sessionId,
            role=role,
            content=content,
        )
    )

    db.query(ChatSession).filter_by(session_id=sessionId).update(
        {"last_active": datetime.now(timezone.utc)}
    )


def getRecentMessages(
    db: Session,
    *,
    sessionId: str,
    limit: int = 10,
) -> List[Tuple[str, str]]:
    return (
        db.query(SessionMessage.role, SessionMessage.content)
        .filter_by(session_id=sessionId)
        .order_by(SessionMessage.created_at.asc())
        .limit(limit)
        .all()
    )


def getMessageCount(
    db: Session,
    *,
    sessionId: str,
) -> int:
    return (
        db.query(func.count(SessionMessage.id))
        .filter_by(session_id=sessionId)
        .scalar()
    )

# ======================================================
# SESSION MEMORY
# ======================================================

def upsertSessionMemory(
    db: Session,
    *,
    sessionId: str,
    summaryText: str,
) -> None:
    db.merge(
        SessionMemorySummary(
            session_id=sessionId,
            summary=summaryText,
            updated_at=datetime.now(timezone.utc),
        )
    )
    db.commit()