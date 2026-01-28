from typing import Optional, List, Tuple
from datetime import datetime, timezone
import uuid
from app.AIhelpers.format_helper import iterateFilePages
from app.AIhelpers.chunk_helper import createDocumentChunks
from app.db import SessionLocal
    
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.models import (
    AIDocument,
    DocumentChunk,
    DocumentSummary,
    ChatSession,
    SessionMessage,
    SessionMemorySummary,
)

def getOrCreateSessionForDocument(
    document_id: int,
    version_id: Optional[int] = None,
) -> str:
    db: Session = SessionLocal()
    try:
        query = db.query(AIDocument).filter(
            AIDocument.document_id == document_id
        )

        if version_id is not None:
            query = query.filter(AIDocument.version_id == version_id)

        ai_doc = query.first()

        if not ai_doc:
            raise RuntimeError(
                f"AIDocument not found for document_id={document_id}, version_id={version_id}. "
                f"Document must be ingested first."
            )

        if ai_doc.session_id:
            return str(ai_doc.session_id)

        # Create ONLY ChatSession
        session = ChatSession()
        db.add(session)
        db.flush()

        ai_doc.session_id = session.session_id
        db.commit()

        return str(session.session_id)

    finally:
        db.close()
        
def createChunksForExistingAIDocument(
    documentId: int,
    versionId: int,
    filePath: str,
    filename: str,
    fileType: str,
    fileSizeMb: float,
) -> dict:
    
    db: Session = SessionLocal()
    ocrUsed = False
    chunksCreated = 0
    lastChunkIndex = 0
    
    try:
        # Get existing AIDocument
        aiDocument = (
            db.query(AIDocument)
            .filter(
                AIDocument.document_id == documentId,
                AIDocument.version_id == versionId,
            )
            .first()
        )
        
        if not aiDocument:
            return {
                "status": "error",
                "message": f"AIDocument not found for documentId={documentId}, versionId={versionId}"
            }
        
        if not aiDocument.session_id:
            return {
                "status": "error",
                "message": f"AIDocument has no session_id for documentId={documentId}, versionId={versionId}"
            }
        
        # Create chunks
        for pageNumber, rawText, usedOcr in iterateFilePages(filePath):
            if not rawText or not rawText.strip():
                continue

            ocrUsed |= usedOcr

            created = createDocumentChunks(
                db=db,
                ai_document_id=aiDocument.id,
                session_id=str(aiDocument.session_id),
                pages=[(pageNumber, rawText)],
                start_index=lastChunkIndex,
            )

            lastChunkIndex += created
            chunksCreated += created

        db.commit()

        return {
            "status": "success",
            "document_id": documentId,
            "chunks": chunksCreated,
            "ocr_used": ocrUsed,
            "file_size_mb": round(fileSizeMb, 2),
        }

    except Exception as e:
        db.rollback()
        return {
            "status": "error",
            "message": str(e)
        }
    finally:
        db.close()
        
def createAIDocumentForVersion(
    document_id: int,
    version_id: int,
    filename: str,
    file_type: str,
    file_size_mb: float,
) -> str:
    """
    Create AIDocument and session for a document version if it doesn't exist.
    Returns the session_id.
    """
    db: Session = SessionLocal()
    try:
        # Check if AIDocument already exists
        ai_doc = db.query(AIDocument).filter(
            AIDocument.document_id == document_id,
            AIDocument.version_id == version_id,
        ).first()

        if ai_doc:
            if ai_doc.session_id:
                return str(ai_doc.session_id)
            # If exists but no session, create session
            session = ChatSession()
            db.add(session)
            db.flush()
            ai_doc.session_id = session.session_id
            db.commit()
            return str(session.session_id)

        # Create new AIDocument with session
        session = ChatSession()
        db.add(session)
        db.flush()

        ai_doc = AIDocument(
            document_id=document_id,
            version_id=version_id,
            session_id=session.session_id,
            filename=filename,
            file_type=file_type,
            file_size_mb=file_size_mb,
        )
        db.add(ai_doc)
        db.commit()

        return str(session.session_id)

    finally:
        db.close()
        
def storeDocumentChunk(
    db: Session,
    *,
    document_id: int,
    sessionId: Optional[str],
    chunkText: str,
    embedding: List[float],
    chunkIndex: int,
    pageNumber: Optional[int] = None,
) -> None:
    db.add(
        DocumentChunk(
            id=uuid.uuid4(),
            document_id=document_id,
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
    document_id: int,
) -> List[str]:
    rows = (
        db.query(DocumentChunk.chunk_text)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )
    return [row.chunk_text for row in rows]


def semanticSearchChunks(
    db: Session,
    *,
    document_id: int,
    queryEmbedding: List[float],
    limit: int = 5,
) -> List[DocumentChunk]:
    return (
        db.query(DocumentChunk)
        .filter(DocumentChunk.document_id == document_id)
        .order_by(DocumentChunk.embedding.l2_distance(queryEmbedding))
        .limit(limit)
        .all()
    )


def upsertDocumentSummary(
    db: Session,
    *,
    document_id: int,
    summaryText: str,
) -> None:
    existing = (
        db.query(DocumentSummary)
        .filter(DocumentSummary.document_id == document_id)
        .first()
    )

    if existing:
        existing.summary_text = summaryText
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(
            DocumentSummary(
                document_id=document_id,
                summary_text=summaryText,
            )
        )

    db.commit()


def getDocumentSummary(
    db: Session,
    *,
    document_id: int,
) -> Optional[str]:
    summary = (
        db.query(DocumentSummary)
        .filter(DocumentSummary.document_id == document_id)
        .first()
    )
    return summary.summary_text if summary else None


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