from typing import List


def chunk_text(text: str, size: int = 500, overlap: int = 80) -> List[str]:
    """
    Split text into overlapping chunks for RAG
    """
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        chunks.append(" ".join(words[start:start + size]))
        start += size - overlap

    return chunks
