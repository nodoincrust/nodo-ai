from typing import Generator, Optional

from app.services.chat_service import chatWithDocument
from app.services.summary_service import summarizeDocument
from app.services.ai_DBservice import getOrCreateSessionForDocument


def handleChat(*, document_id: int, query: str) -> dict:
    """
    Handle a chat request for a document.
    """
    sessionId = getOrCreateSessionForDocument(document_id)

    return chatWithDocument(
        document_id=document_id,
        sessionId=sessionId,
        query=query,
    )


def handleChatStream(*, document_id: int, query: str) -> Generator[str, None, None]:
    
    sessionId = getOrCreateSessionForDocument(document_id)

    # Placeholder: implement streaming version later if needed
    raise NotImplementedError("Streaming chat is not implemented yet")


def handleChatWithCitation(
    *,
    document_id: int,
    query: str,
) -> dict:
    sessionId = getOrCreateSessionForDocument(document_id)

    return chatWithDocument(
        document_id=document_id,
        sessionId=sessionId,
        query=query,
    )


def handleSummary(*, document_id: int) -> dict:
    return summarizeDocument(document_id)
