from typing import List, Iterable, Dict, Any


def chunkText(
    text: str,
    chunkSize: int = 500,
    overlap: int = 80,
) -> List[str]:
    words = text.split()
    chunks: List[str] = []
    startIndex = 0

    while startIndex < len(words):
        endIndex = startIndex + chunkSize
        chunks.append(" ".join(words[startIndex:endIndex]))
        startIndex += chunkSize - overlap

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