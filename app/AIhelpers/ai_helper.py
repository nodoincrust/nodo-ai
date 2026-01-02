from typing import Generator, Optional
from app.services.chat_service import chat_with_session
from app.services.summary_service import summarize_doc

# from app.models.chat_session import ChatSession

def handle_chat(session_id: str, query: str) -> dict:

    return chat_with_session(session_id, query)


def handle_chat_stream(session_id: str, query: str) -> Generator[str, None, None]:

    return chat_stream(session_id, query)


def handle_chat_with_citation(
    session_id: str, query: str, document_id: Optional[str] = None
) -> dict:

    return chat_with_session(session_id, query, document_id)


def handle_summary(document_id: str) -> dict:

    return summarize_doc(document_id)
