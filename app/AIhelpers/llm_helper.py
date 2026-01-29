import requests
import re
from typing import Dict, List
import time
import logging
from sqlalchemy.orm import Session
from app.models import DocumentChunk, DocumentSummary
from .embedding_helper import createEmbeddings

logger = logging.getLogger("ai.llm_helper")

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:3b-instruct-q4_0"

# SYSTEM_PROMPT = """
# You are a document-grounded AI Nodo-ai .

# Rules:
# - Answer ONLY using the provided document context
# - Do not hallucinate or use external knowledge
# - If information is missing, say so clearly

# When generating a summary:
# - Provide a concise factual summary
# - Generate 5–6 short tags (single words or short phrases)
# - Tags must come from the document content
# - Tags must be lowercase
# - No duplicates

# Respond in JSON format exactly like this:
# {
#   "summary": "...",
#   "tags": ["tag1", "tag2", "tag3"]
# }
# """


_NON_PRINTABLE_RE = re.compile(r"[\x00-\x1F\x7F]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\?\!])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")

_STOPWORDS = {
    "the",
    "and",
    "is",
    "in",
    "to",
    "of",
    "a",
    "for",
    "on",
    "with",
    "as",
    "by",
    "that",
    "this",
    "are",
    "was",
    "it",
    "be",
    "or",
    "from",
    "at",
    "an",
    "which",
}


def cleanInputText(text: str) -> str:
    """Removes non-printable chars"""
    return _NON_PRINTABLE_RE.sub(" ", text)


def tokenizeText(text: str) -> List[str]:
    """Extracts meaningful tokens only"""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS]


def splitIntoSentences(text: str) -> List[str]:
    """Splits text into sentences"""
    return [
        s.replace("\n", " ").strip()
        for s in _SENTENCE_SPLIT_RE.split(text.strip())
        if s.strip()
    ]


def compressSentence(sentence: str, maxChars: int = 200) -> str:
    """Compress a sentence to max characters"""
    for sep in (",", ";", " - ", " – ", ":"):
        if sep in sentence:
            sentence = sentence.split(sep)[0]
            break
    sentence = " ".join(sentence.split())
    return (
        sentence
        if len(sentence) <= maxChars
        else sentence[: maxChars - 1].rstrip() + "…"
    )


def optimizeContext(context: str, maxSentences: int = 50) -> str:
    """Optimize context for LLM by keeping relevant sentences"""
    context = cleanInputText(context)
    tokens = set(tokenizeText(context))
    sentences = splitIntoSentences(context)

    filtered = []
    for s in sentences:
        if tokens.intersection(tokenizeText(s)):
            filtered.append(compressSentence(s))
        if len(filtered) >= maxSentences:
            break

    return "\n".join(filtered)


def askLlm(
    *,
    context: str,
    question: str,
    system_prompt: str,
    retries: int = 3,
) -> Dict[str, Dict[str, str]]:
    """
    Generic LLM caller.
    Caller MUST explicitly pass system_prompt.
    """
    logger.info(
        f"askLlm called - context length: {len(context)}, question: {question[:100]}..."
    )

    optimizedContext = optimizeContext(context)
    logger.info(f"Optimized context length: {len(optimizedContext)}")

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Context:\n{optimizedContext}"},
            {"role": "user", "content": question},
        ],
        "options": {
            "temperature": 0.6,
            "num_predict": 700,
            "num_ctx": 16384,
            "top_k": 40,
            "top_p": 0.9,
        },
        "stream": False,
    }

    for attempt in range(retries):
        try:
            response = requests.post(
                OLLAMA_URL,
                json=payload,
                timeout=140,
            )
            response.raise_for_status()

            response_json = response.json()
            answer = response_json.get("message", {}).get("content", "")

            return {
                "status": "success",
                "data": {"answer": answer},
            }

        except Exception as exc:
            logger.exception(f"LLM call failed (attempt {attempt + 1})")
            if attempt == retries - 1:
                return {
                    "status": "error",
                    "data": {"answer": str(exc)},
                }
            time.sleep(2**attempt)


class RAGHelper:
    def __init__(self, db: Session):
        self.db = db

    def query(
        self,
        text: str,
        top_k: int = 5,
        min_similarity: float | None = None,
        session_id=None,
        ai_document_id=None,
    ):

        logger.info("RAG query for: %s", text[:100])

        query_embedding = createEmbeddings(text)

        q = self.db.query(DocumentChunk)

        if ai_document_id:
            q = q.filter(DocumentChunk.ai_document_id == ai_document_id)

        if session_id:
            q = q.filter(DocumentChunk.session_id == session_id)

        q = q.filter(DocumentChunk.embedding.isnot(None))

        results = (
            q.order_by(DocumentChunk.embedding.op("<->")(query_embedding))
            .limit(top_k)
            .all()
        )

        logger.info("RAG returned %d chunks", len(results))

        return [
            {
                "chunk_id": c.id,
                "text": c.chunk_text,
                "page_number": c.page_number,
            }
            for c in results
        ]

    def update_summary_embedding(self, ai_document_id: int, summary: str):
        """
        Update the embedding for a document summary
        """
        try:
            logger.info(f"Updating embedding for ai_document_id={ai_document_id}")
            embedding = createEmbeddings([summary])[0]

            summary_record = (
                self.db.query(DocumentChunk)
                .filter_by(ai_document_id=ai_document_id)
                .first()
            )

            if summary_record:
                summary_record.embedding = embedding
                self.db.commit()
                logger.info(f"Updated embedding for summary {ai_document_id}")
            else:
                logger.warning(
                    f"No summary record to update embedding for {ai_document_id}"
                )
        except Exception as e:
            logger.error(f"Failed to update summary embedding: {e}")
            self.db.rollback()
