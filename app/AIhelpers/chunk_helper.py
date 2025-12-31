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


def chunk_text_from_pages(pages, size=512, overlap=60, with_page=False):
    buffer = []
    page_buffer = []

    for item in pages:
        if with_page:
            page_no, text = item
        else:
            page_no, text = None, item

        words = text.split()
        buffer.extend(words)
        page_buffer.extend([page_no] * len(words))

        while len(buffer) >= size:
            yield {
                "text": " ".join(buffer[:size]),
                "page": page_buffer[0]
            }
            buffer = buffer[size - overlap:]
            page_buffer = page_buffer[size - overlap:]

    if buffer:
        yield {
            "text": " ".join(buffer),
            "page": page_buffer[0]
        }