from typing import Optional, List, Tuple
from datetime import datetime, timezone
import uuid

from sqlalchemy.orm import Session
from sqlalchemy import func

from app.db import SessionLocal, engine, Base
from app.models import (
    Document,
    DocuementChunks,
    DocuemntSummery,
    ChatSession,
    SessionMessages,
    SessionMemorySummery,
)


def init_db():
    # Ensure pgvector extension exists
    with engine.connect() as conn:
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
            conn.commit()
        except Exception:
            # Extension may not be available (managed DBs)
            pass

    # Create all tables
    Base.metadata.create_all(bind=engine)


def create_document(
    self,
    document_id: str,
    filename: str,
    file_type: str,
    file_size_mb: float,
    session_id: Optional[str] = None,
) -> Document:
    doc = Document(
        document_id=document_id,
        session_id=session_id,
        filename=filename,
        file_type=file_type,
        file_size_mb=file_size_mb,
    )
    print(session_id)
    self.db.merge(doc)
    self.db.commit()
    return doc


def store_chunk(
    self,
    document_id: str,
    chunk_text: str,
    embedding: List[float],
    chunk_index: int,
    session_id: Optional[str] = None,
):
    chunk = DocuementChunks(
        id=uuid.uuid4(),
        document_id=document_id,
        session_id=session_id,
        chunk_text=chunk_text,
        embedding=embedding,
        chunk_index=chunk_index,
    )
    self.db.add(chunk)


def fetch_chunks(self, document_id: str) -> List[str]:
    rows = (
        self.db.query(DocuementChunks.chunk_text)
        .filter(DocuementChunks.document_id == document_id)
        .order_by(DocuementChunks.chunk_index)
        .all()
    )
    return [r.chunk_text for r in rows]


def semantic_search(
    self,
    document_id: str,
    query_embedding: List[float],
    limit: int = 5,
    session_id: Optional[str] = None,
) -> List[DocuementChunks]:
    q = self.db.query(DocuementChunks).filter(
        DocuementChunks.document_id == document_id
    )

    if session_id:
        q = q.filter(DocuementChunks.session_id == session_id)

    return (
        q.order_by(DocuementChunks.embedding.l2_distance(query_embedding))
        .limit(limit)
        .all()
    )


# DOCUMENT SUMMARY
def upsert_document_summary(self, document_id: str, summary_text: str):
    self.db.merge(
        DocuemntSummery(
            document_id=document_id,
            summery_text=summary_text,
            updated_at=datetime.now(timezone.utc),
        )
    )
    self.db.commit()


def get_document_summary(self, document_id: str) -> Optional[str]:
    res = self.db.query(DocuementSummery).filter_by(document_id=document_id).first()
    return res.summery_text if res else None


# SESSION OPERATIONS


def create_chat_session(db: Session = None) -> str:
    sess = ChatSession()
    db.add(sess)
    db.commit()
    db.refresh(sess)
    return str(sess.session_id)


def session_exists(self, session_id: str) -> bool:
    return (
        self.db.query(ChatSession).filter(ChatSession.session_id == session_id).count()
        > 0
    )


# MESSAGE OPERATIONS
def add_message(self, session_id: str, role: str, content: str):
    self.db.add(
        SessionMessages(
            id=uuid.uuid4(),
            session_id=session_id,
            role=role,
            content=content,
        )
    )
    self.db.query(ChatSession).filter_by(session_id=session_id).update(
        {"last_active": datetime.now(timezone.utc)}
    )


def get_recent_messages(
    self, session_id: str, limit: int = 10
) -> List[Tuple[str, str]]:
    return (
        self.db.query(SessionMessages.role, SessionMessages.content)
        .filter_by(session_id=session_id)
        .order_by(SessionMessages.created_at.asc())
        .limit(limit)
        .all()
    )


def get_message_count(self, session_id: str) -> int:
    return (
        self.db.query(func.count(SessionMessages.id))
        .filter_by(session_id=session_id)
        .scalar()
    )


def get_messages_for_compression(
    self, session_id: str, keep_last: int = 10
) -> Tuple[List[SessionMessages], List[SessionMessages]]:
    msgs = (
        self.db.query(SessionMessages)
        .filter_by(session_id=session_id)
        .order_by(SessionMessages.created_at.asc())
        .all()
    )
    if len(msgs) <= keep_last:
        return [], msgs
    return msgs[:-keep_last], msgs[-keep_last:]


def delete_messages(self, session_id: str, message_ids: List[uuid.UUID]):
    if not message_ids:
        return
    (
        self.db.query(SessionMessages)
        .filter(
            SessionMessages.session_id == session_id,
            SessionMessages.id.in_(message_ids),
        )
        .delete(synchronize_session=False)
    )


def upsert_session_memory_summary(self, session_id: str, summary_text: str):
    self.db.merge(
        SessionMemorySummery(
            session_id=session_id,
            summary=summary_text,
            updated_at=datetime.now(timezone.utc),
        )
    )
