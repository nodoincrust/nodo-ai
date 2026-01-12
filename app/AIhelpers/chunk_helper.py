from typing import List, Iterable, Dict, Any
from sqlalchemy.orm import Session
import uuid
from app.models import DocumentChunk
from app.AIhelpers.embedding_helper import createEmbeddings


def chunkText(
    text: str,
    chunkSize: int = 500,
    overlap: int = 80,
) -> List[str]:
    words = text.split()                          # Tokenizes text by whitespace
    chunks: List[str] = []
    startIndex = 0

    while startIndex < len(words):
        endIndex = startIndex + chunkSize
        chunks.append(" ".join(words[startIndex:endIndex]))
        startIndex += chunkSize - overlap          # Applies overlap for context continuity

    return chunks


def chunkTextFromPages(
    pages: Iterable[Any],
    chunkSize: int = 512,
    overlap: int = 60,
    includePageNumber: bool = False,
) -> Iterable[Dict[str, Any]]:

    wordBuffer: List[str] = []
    pageBuffer: List[int] = []

    for item in pages:
        if includePageNumber:
            pageNumber, text = item
        else:
            pageNumber, text = None, item

        words = text.split()
        wordBuffer.extend(words)
        pageBuffer.extend([pageNumber] * len(words))

        while len(wordBuffer) >= chunkSize:
            yield {
                "text": " ".join(wordBuffer[:chunkSize]),
                "page": pageBuffer[0],
            }
            wordBuffer = wordBuffer[chunkSize - overlap :]
            pageBuffer = pageBuffer[chunkSize - overlap :]

    if wordBuffer:
        yield {
            "text": " ".join(wordBuffer),
            "page": pageBuffer[0],
        }


# =========================
# DOCUMENT INGESTION (BATCHED)
# =========================
def createDocumentChunks(
    *,
    db: Session,
    ai_document_id: int,
    session_id: str,
    pages: Iterable[Any],
    start_index: int = 0,
    chunkSize: int = 512,
    overlap: int = 60,
    EMBED_BATCH_SIZE: int = 16,   # ✅ MUST MATCH embedding helper
) -> int:

    chunk_index = start_index
    chunks_created = 0

    texts: List[str] = []
    pages_meta: List[int | None] = []

    for chunk in chunkTextFromPages(
        pages,
        chunkSize=chunkSize,
        overlap=overlap,
        includePageNumber=True,
    ):
        text = chunk["text"].strip()
        if not text:
            continue

        texts.append(text)
        pages_meta.append(chunk["page"])

        # 🔥 BATCH FLUSH
        if len(texts) == EMBED_BATCH_SIZE:
            vectors = createEmbeddings(texts)

            for vec, txt, page in zip(vectors, texts, pages_meta):
                db.add(
                    DocumentChunk(
                        id=uuid.uuid4(),
                        ai_document_id=ai_document_id,
                        session_id=session_id,
                        chunk_index=chunk_index,
                        chunk_text=txt,
                        embedding=vec,
                        page_number=page,
                    )
                )
                chunk_index += 1
                chunks_created += 1

            texts.clear()
            pages_meta.clear()

    # 🔁 Flush remaining chunks
    if texts:
        vectors = createEmbeddings(texts)

        for vec, txt, page in zip(vectors, texts, pages_meta):
            db.add(
                DocumentChunk(
                    id=uuid.uuid4(),
                    ai_document_id=ai_document_id,
                    session_id=session_id,
                    chunk_index=chunk_index,
                    chunk_text=txt,
                    embedding=vec,
                    page_number=page,
                )
            )
            chunk_index += 1
            chunks_created += 1

    return chunks_created