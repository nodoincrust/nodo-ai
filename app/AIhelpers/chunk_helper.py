# chunk_helper.py
from typing import List, Iterable, Dict, Any
from sqlalchemy.orm import Session
import uuid
from app.models import DocumentChunk
from app.AIhelpers.embedding_helper import createEmbeddings
import re


def chunkText(
    text: str,
    chunkSize: int = 1024,  # Increased for efficiency on large docs
    overlap: int = 128,     # Adjusted overlap
) -> List[str]:
    # Efficient sentence-based chunking without NLTK
    sentences = re.split(r'(?<!\w\.\w.)(?<![A-Z][a-z]\.)(?<=\.|\?)\s', text)
    chunks = []
    current_chunk = []
    current_length = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        sentence_length = len(sentence)
        if current_length + sentence_length > chunkSize:
            chunks.append(' '.join(current_chunk))
            current_chunk = current_chunk[-overlap // 50:] if overlap else []  # Word approx for overlap
            current_length = len(' '.join(current_chunk))
        current_chunk.append(sentence)
        current_length += sentence_length + 1  # Space
    if current_chunk:
        chunks.append(' '.join(current_chunk))
    return chunks


def chunkTextFromPages(
    pages: Iterable[Any],
    chunkSize: int = 1024,  # Increased
    overlap: int = 128,
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

def createDocumentChunks(
    *,
    db: Session,
    ai_document_id: int,
    session_id: str,
    pages: Iterable[Any],
    start_index: int = 0,
    chunkSize: int = 1024,  # Increased
    overlap: int = 128,
    EMBED_BATCH_SIZE: int = 48,  # Increased for speed
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

        #BATCH FLUSH
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

    #Flush remaining chunks
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