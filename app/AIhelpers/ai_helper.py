from typing import Generator, Optional

from app.services.chat_service import chatWithDocument
from app.services.summary_service import summarizeDocument
from app.services.ai_db_service import getOrCreateSessionForDocument


def handleChat(*, documentId: int, query: str) -> dict:
    """
    Handle a chat request for a document.
    """
    sessionId = getOrCreateSessionForDocument(documentId)

    return chatWithDocument(
        documentId=documentId,
        sessionId=sessionId,
        query=query,
    )


def handleChatStream(*, documentId: int, query: str) -> Generator[str, None, None]:

    sessionId = getOrCreateSessionForDocument(documentId)

    # Placeholder: implement streaming version later if needed
    raise NotImplementedError("Streaming chat is not implemented yet")


def handleChatWithCitation(
    *,
    documentId: int,
    query: str,
) -> dict:
    sessionId = getOrCreateSessionForDocument(documentId)

    return chatWithDocument(
        documentId=documentId,
        sessionId=sessionId,
        query=query,
    )


def handleSummary(*, documentId: int) -> dict:
    return summarizeDocument(documentId)
