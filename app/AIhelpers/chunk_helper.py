from typing import List, Iterable, Dict, Any
from sqlalchemy.orm import Session

from app.models import DocumentChunk
from app.AIhelpers.embedding_helper import createEmbedding


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


def _flat(vec):
    return vec[0] if isinstance(vec, list) and vec and isinstance(vec[0], list) else vec  # Normalizes embedding shape


def createDocumentChunks(
    *,
    db: Session,
    ai_document_id: int,
    session_id: str,
    pages: Iterable[Any],
    chunkSize: int = 512,
    overlap: int = 60,
) -> int:

    chunk_index = 0

    for chunk in chunkTextFromPages(
        pages,
        chunkSize=chunkSize,
        overlap=overlap,
        includePageNumber=True,
    ):
        text = chunk["text"].strip()
        page_number = chunk["page"]

        if not text:
            continue                               # Skips empty chunks

        embedding = _flat(createEmbedding(text))   # Generates vector embedding

        db.add(
            DocumentChunk(
                ai_document_id=ai_document_id,
                session_id=session_id,
                chunk_index=chunk_index,
                chunk_text=text,
                embedding=embedding,
                page_number=page_number,
            )
        )

        chunk_index += 1

    db.commit()                                    # Persists all chunks atomically
    return chunk_index