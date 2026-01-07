import logging
from sqlalchemy.orm import Session
from typing import List

from app.models import DocumentSummary

logger = logging.getLogger("ai.tagService")


def getDocumentTags(
    db: Session,
    *,
    document_id: int,
) -> List[str]:
    """
    Fetch stored tags for a document.
    READ-ONLY service.
    """
    record = (
        db.query(DocumentSummary)
        .filter_by(document_id=document_id)
        .first()
    )

    if not record or not record.tags:
        logger.info("No tags found for document %s", document_id)
        return []

    return record.tags
