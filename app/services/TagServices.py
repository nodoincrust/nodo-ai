import logging
import json
import re
from sqlalchemy.orm import Session
from typing import List

from app.models import DocumentSummary, DocumentChunk, AIDocument
from app.AIhelpers.llm_helper import askLlm

logger = logging.getLogger("ai.tagService")


def generateTagsFromLLM(document_id: int, db: Session) -> List[str]:
    """
    Generate tags for a document using LLM based on document chunks.
    """
    # Get AI document
    ai_doc = db.query(AIDocument).filter(AIDocument.document_id == document_id).first()
    if not ai_doc:
        logger.error("AI document not found for document_id %s", document_id)
        return []

    # Get chunks
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.ai_document_id == ai_doc.id)
        .order_by(DocumentChunk.chunk_index)
        .limit(12)  # Use top chunks for tag generation
        .all()
    )

    if not chunks:
        logger.info("No chunks found for document_id %s", document_id)
        return []

    # Prepare context
    context_parts = [f"[PAGE {c.page_number}] {c.chunk_text}" for c in chunks]
    document_context = "\n\n".join(context_parts)[:7000]  # Limit context

    # Tag generation prompt
    tag_prompt = """
You are an expert at generating relevant tags for documents.

Analyze the provided document excerpts and generate 5-10 concise tags (2-4 words each) that capture the main themes, topics, and entities.

Examples of good tags:
- "carbon footprint"
- "environmental impact"
- "product lifecycle data"
- "kgCO2eq metrics"
- "furniture products"
- "supplier emissions"

Return ONLY a JSON array of strings, like: ["tag1", "tag2", "tag3"]
"""

    try:
        llm_result = askLlm(
            context=f"{tag_prompt}\n\nDOCUMENT EXCERPTS:\n{document_context}",
            question="Generate relevant tags for this document.",
        )

        raw_response = llm_result["data"]["answer"].strip()

        # Parse JSON array
        if raw_response.startswith("[") and raw_response.endswith("]"):
            tags = json.loads(raw_response)
        else:
            # Try to extract from response
            start = raw_response.find("[")
            end = raw_response.rfind("]")
            if start != -1 and end != -1:
                tags = json.loads(raw_response[start:end+1])
            else:
                logger.error("Failed to parse tags from LLM response: %s", raw_response)
                return []

        # Validate and clean tags
        cleaned_tags = []
        for tag in tags:
            if isinstance(tag, str):
                cleaned = tag.strip().title()
                if 2 <= len(cleaned) <= 30:
                    cleaned_tags.append(cleaned)

        return cleaned_tags[:10]  # Limit to 10 tags

    except Exception as e:
        logger.exception("Error generating tags from LLM: %s", str(e))
        return []


def storeDocumentTags(
    db: Session,
    *,
    document_id: int,
    tags: List[str],
) -> bool:
    """
    Store tags for a document in the database.
    """
    try:
        # Get AI document
        ai_doc = db.query(AIDocument).filter(AIDocument.document_id == document_id).first()
        if not ai_doc:
            logger.error("AI document not found for document_id %s", document_id)
            return False

        # Get or create summary record
        summary_record = db.query(DocumentSummary).filter_by(ai_document_id=ai_doc.id).first()
        if not summary_record:
            summary_record = DocumentSummary(
                ai_document_id=ai_doc.id,
                summary_text="",  # Will be filled by summary service
                tags=tags,
                citations=[]
            )
            db.add(summary_record)
        else:
            summary_record.tags = tags
            summary_record.updated_at = db.func.now()

        db.commit()
        logger.info("Stored %d tags for document %s", len(tags), document_id)
        return True

    except Exception as e:
        db.rollback()
        logger.exception("Error storing tags for document %s: %s", document_id, str(e))
        return False


def generateAndStoreTags(
    db: Session,
    *,
    document_id: int,
) -> List[str]:
    """
    Generate tags from LLM and store them in the database.
    Returns the generated tags.
    """
    tags = generateTagsFromLLM(document_id, db)
    if tags:
        success = storeDocumentTags(db=db, document_id=document_id, tags=tags)
        if success:
            return tags
        else:
            logger.error("Failed to store tags for document %s", document_id)
            return []
    else:
        logger.warning("No tags generated for document %s", document_id)
        return []


def getDocumentTags(
    db: Session,
    *,
    document_id: int,
) -> List[str]:
    """
    Fetch stored tags for a document.
    READ-ONLY service.
    """
    record = db.query(DocumentSummary).filter_by(document_id=document_id).first()

    if not record or not record.tags:
        logger.info("No tags found for document %s", document_id)
        return []

    return record.tags
