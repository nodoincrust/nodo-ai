"""
Tag Service (READ-ONLY)

IMPORTANT:
- NO LLM calls here
- Tags are generated during summary creation
- This service only fetches stored tags
"""

import logging
from sqlalchemy.orm import Session
from typing import List

from app.models import DocuemntSummery

logger = logging.getLogger("ai_modul.tag_service")


def get_tags(
    db: Session,
    document_id: str,
) -> List[str]:
    record = (
        db.query(DocuemntSummery)
        .filter_by(document_id=document_id)
        .first()
    )

    if not record or not record.tags:
        logger.info("No tags found for document %s", document_id)
        return []

    return record.tags
