from typing import List
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
import uuid
import re

from app.models import DocumentChunk
from app.AIhelpers.embedding_helper import createEmbeddings


def chunkText(
    text: str,
    chunkSize: int = 1024,  # Increased for efficiency on large docs
    overlap: int = 128,     # Adjusted overlap
) -> List[str]:
    # Efficient sentence-based chunking without NLTK
    sentences = re.split(
        r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', text
    )
    chunks = []
    current_chunk = []
    current_length = 0

    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue

        sentence_length = len(sentence)
        if current_length + sentence_length > chunkSize:
            chunks.append(" ".join(current_chunk))
            current_chunk = current_chunk[-overlap // 50:] if overlap else []
            current_length = len(" ".join(current_chunk))

        current_chunk.append(sentence)
        current_length += sentence_length + 1

    if current_chunk:
        chunks.append(" ".join(current_chunk))

    return chunks
 
 
def chunkTextFromPages(
    pages,
    chunkSize: int = 1024,
    overlap: int = 128,
    includePageNumber: bool = False,
):
    wordBuffer = []
    pageBuffer = []

    for item in pages:
        if includePageNumber:
            if isinstance(item, tuple) and len(item) == 3:
                pageNumber, text, _ = item
            else:
                pageNumber, text = item
        else:
            text = item
            pageNumber = None

        if not text or not text.strip():
            continue

        words = text.split()
        if not words:
            continue

        wordBuffer.extend(words)
        pageBuffer.extend([pageNumber] * len(words))
 
        while len(wordBuffer) >= chunkSize:
            chunk_text = " ".join(wordBuffer[:chunkSize]).strip()
            if chunk_text:
                yield {
                    "text": chunk_text,
                    "page": pageBuffer[0],
                }

            wordBuffer = wordBuffer[chunkSize - overlap :]
            pageBuffer = pageBuffer[chunkSize - overlap :]
 
    if wordBuffer:
        chunk_text = " ".join(wordBuffer).strip()
        if chunk_text:
            yield {
                "text": chunk_text,
                "page": pageBuffer[0],
            }

def createDocumentChunks(
    *,
    db,
    ai_document_id: int,
    session_id: str,
    pages,
    start_index: int = 0,
    chunkSize: int = 1024,
    overlap: int = 128,
    EMBED_BATCH_SIZE: int = 48,
) -> int:

    db_last_index = (
        db.query(func.max(DocumentChunk.chunk_index))
        .filter(DocumentChunk.ai_document_id == ai_document_id)
        .scalar()
    )

    chunk_index = (
        db_last_index + 1
        if db_last_index is not None
        else max(start_index, 0)
    )

    chunks_created = 0
    texts = []
    pages_meta = []

    try:
        for chunk in chunkTextFromPages(
            pages,
            chunkSize=chunkSize,
            overlap=overlap,
            includePageNumber=True,
        ):
            text = (chunk.get("text") or "").strip()
            if not text:
                continue

            texts.append(text)
            pages_meta.append(chunk.get("page"))

            if len(texts) == EMBED_BATCH_SIZE:
                vectors = createEmbeddings(texts)

                for vec, txt, page in zip(vectors, texts, pages_meta):
                    stmt = insert(DocumentChunk).values(
                        id=uuid.uuid4(),
                        ai_document_id=ai_document_id,
                        session_id=session_id,
                        chunk_index=chunk_index,
                        chunk_text=txt,
                        embedding=vec,
                        page_number=page,
                    ).on_conflict_do_nothing(
                        index_elements=["ai_document_id", "chunk_index"]
                    )

                    result = db.execute(stmt)
                    if result.rowcount == 1:
                        chunk_index += 1
                        chunks_created += 1

                texts.clear()
                pages_meta.clear()

        if texts:
            vectors = createEmbeddings(texts)

            for vec, txt, page in zip(vectors, texts, pages_meta):
                stmt = insert(DocumentChunk).values(
                    id=uuid.uuid4(),
                    ai_document_id=ai_document_id,
                    session_id=session_id,
                    chunk_index=chunk_index,
                    chunk_text=txt,
                    embedding=vec,
                    page_number=page,
                ).on_conflict_do_nothing(
                    index_elements=["ai_document_id", "chunk_index"]
                )

                result = db.execute(stmt)
                if result.rowcount == 1:
                    chunk_index += 1
                    chunks_created += 1

        return chunks_created

    except Exception as exc:
        db.rollback()
        print("Chunk creation failed:", exc)
        return 0

def select_top_chunks(
    db: Session,
    chunks: List[DocumentChunk],
    query_text: str,
    top_k: int = 50,
) -> List[DocumentChunk]:

    if not chunks:
        return []

    # Restrict the search to this document; an unscoped vector search would
    # rank chunks belonging to other documents and companies.
    ai_document_ids = {c.ai_document_id for c in chunks if c.ai_document_id}

    try:
        query_emb = createEmbeddings([query_text])[0]
        results = (
            db.query(DocumentChunk)
            .filter(DocumentChunk.ai_document_id.in_(ai_document_ids))
            .filter(DocumentChunk.embedding.isnot(None))
            .order_by(DocumentChunk.embedding.cosine_distance(query_emb))
            .limit(top_k)
            .all()
        )
        return results or chunks[:top_k]
    except Exception:
        return chunks[:top_k]