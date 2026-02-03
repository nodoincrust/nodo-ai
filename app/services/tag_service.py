# TAG SERVICE
import logging
import json
import re
from sqlalchemy.orm import Session
from typing import List

from app.AIhelpers.embedding_helper import createEmbeddings
from app.models import DocumentSummary, DocumentChunk, AIDocument
from app.AIhelpers.llm_helper import askLlm, RAGHelper

logger = logging.getLogger("ai.tagService")


def generateTagsFromLLM(
    document_id: int, db: Session, use_rag: bool = True
) -> List[str]:
    # Generate tags for a document using LLM based on document chunks.

    # Get AI document
    ai_doc = db.query(AIDocument).filter(AIDocument.document_id == document_id).first()
    if not ai_doc:
        logger.error("AI document not found for document_id %s", document_id)
        return []

    # Get chunks (all for large docs, but limit for context)
    chunks = (
        db.query(DocumentChunk)
        .filter(DocumentChunk.ai_document_id == ai_doc.id)
        .order_by(DocumentChunk.chunk_index)
        .all()
    )

    if not chunks:
        logger.info("No chunks found for document_id %s", document_id)
        return []

    # For large docs, select top relevant chunks using pgvector
    if len(chunks) > 50:
        chunks = select_top_chunks(db, chunks, "relevant tags and themes")

    # Prepare context
    context_parts = [
        f"[PAGE {c.page_number}] {c.chunk_text}" for c in chunks[:50]
    ]  # Limit to 50 for speed
    document_context = "\n\n".join(context_parts)[:30000]  # Increased limit

    # Tag generation prompt
    tag_prompt = """
You are an expert at generating relevant tags for documents.
 
Analyze the provided document excerpts and generate 2-3 concise tags (2-4 words each) that capture the main themes, topics, and entities.
 
Examples of good tags:
- "carbon footprint"
- "environmental impact"
- "product lifecycle data"
 
Return ONLY a JSON array of strings, like: ["tag1", "tag2", "tag3"]
"""

    try:
        if use_rag:
            rag = RAGHelper(db)
            summary = " ".join([c.chunk_text for c in chunks])[
                :2000
            ]  # Temp summary for query
            retrieved = rag.query(summary, top_k=3)  # Reduced for speed
            if retrieved:
                examples = "\n".join(
                    [
                        f"Similar: {r['summary']} -> Tags: {', '.join(r['tags'])}"
                        for r in retrieved
                    ]
                )
                tag_prompt += f"\nExamples from similar documents:\n{examples}"

        # Retry loop for LLM
        tags = []
        for attempt in range(3):
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
                        tags = json.loads(raw_response[start : end + 1])
                    else:
                        raise ValueError("Failed to parse tags")

                if tags:
                    break
            except Exception as e:
                if attempt == 2:
                    logger.error("LLM tags failed after retries: %s", str(e))
                    # Fallback to keywords
                    return _fallback_keywords(document_context)
                continue

        # Validate and clean tags
        cleaned_tags = []
        for tag in tags:
            if isinstance(tag, str):
                cleaned = tag.strip().title()
                if 2 <= len(cleaned) <= 30:
                    cleaned_tags.append(cleaned)

        return cleaned_tags[:4]  # Limit to 10 tags

    except Exception as e:
        logger.exception("Error generating tags from LLM: %s", str(e))
        return _fallback_keywords(document_context)


def _fallback_keywords(text: str) -> List[str]:
    stop_words = {
        "the",
        "and",
        "is",
        "in",
        "to",
        "of",
        "a",
        "for",
        "on",
        "with",
        "as",
        "by",
        "that",
        "this",
        "are",
        "was",
        "it",
        "be",
        "or",
        "from",
        "at",
        "an",
        "which",
    }
    try:
        words = re.findall(r"\b\w+\b", text.lower())
        keywords = [w for w in words if w.isalpha() and w not in stop_words]
        return list(set(keywords))[:4]
    except Exception as e:
        logger.error(f"Keyword fallback failed: {e}")
        return []


def storeDocumentTags(
    db: Session,
    *,
    document_id: int,
    tags: List[str],
) -> bool:
    try:
        # Get AI document
        ai_doc = (
            db.query(AIDocument).filter(AIDocument.document_id == document_id).first()
        )
        if not ai_doc:
            logger.error("AI document not found for document_id %s", document_id)
            return False

        # Get or create summary record
        summary_record = (
            db.query(DocumentSummary).filter_by(ai_document_id=ai_doc.id).first()
        )
        if not summary_record:
            summary_record = DocumentSummary(
                ai_document_id=ai_doc.id,
                summary_text="",  # Will be filled by summary service
                tags=tags,
                citations=[],
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


def select_top_chunks(
    db: Session, chunks: List[DocumentChunk], query_text: str, top_k: int = 50
) -> List[DocumentChunk]:

    try:
        query_emb = createEmbeddings([query_text])[0]
        # Use pgvector cosine distance operator
        results = (
            db.query(DocumentChunk)
            .order_by(
                DocumentChunk.embedding.op("<->")(query_emb)
            )  # Use pgvector cosine distance operator
            .limit(top_k)
            .all()
        )
        return results
    except Exception as e:
        logger.warning(f"Chunk selection failed: {e}; using all chunks")
        return chunks[:top_k]
