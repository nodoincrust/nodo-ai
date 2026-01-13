import requests
import re
from typing import Dict, List, Iterable
import time
import logging
from sqlalchemy.orm import Session
from app.models import DocumentSummary
from .embedding_helper import createEmbeddings 

logger = logging.getLogger("ai.llm_helper")

OLLAMA_URL = "http://localhost:11434/api/chat"   # Ollama endpoint
MODEL = "llama3.1:8b-instruct-q4_0"                 # Optimized local model


SYSTEM_PROMPT = """
You are an enterprise-grade AI assistant operating inside a document-centric,
memory-aware question answering system.
CORE RULES:
• Answer only from provided document context
• Do NOT hallucinate or invent facts
• If information is missing, say: "The provided document does not contain this information"
• Do NOT use external knowledge unless explicitly allowed

RESPONSE STYLE:
• Be direct and concise
• Use structured format (lists, steps) when helpful
• Cite aligned text from documents
• Ask for clarification if the question is ambiguous

MEMORY & CONTEXT:
• Use session memory only for relevant context
• Do NOT contradict previous information
• Synthesize multiple document chunks logically

ERROR HANDLING:
• Never guess or assume
• Acknowledge uncertainty explicitly
• Refuse unsafe or off-topic requests

Every response must be: Grounded, Accurate, and Trustworthy.
"""

_NON_PRINTABLE_RE = re.compile(r"[\x00-\x1F\x7F]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\?\!])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")

_STOPWORDS = {
    "the", "and", "is", "in", "to", "of", "a", "for", "on", "with", "as", "by",
    "that", "this", "are", "was", "it", "be", "or", "from", "at", "an", "which",
}

# Removes non-printable chars
def cleanInputText(text: str) -> str:
    return _NON_PRINTABLE_RE.sub(" ", text)  

# Extracts meaningful tokens only
def tokenizeText(text: str) -> List[str]:
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS
    ]  

 # Splits text into sentences
def splitIntoSentences(text: str) -> List[str]:
    return [
        s.replace("\n", " ").strip()
        for s in _SENTENCE_SPLIT_RE.split(text.strip())
        if s.strip()
    ] 


def compressSentence(sentence: str, maxChars: int = 200) -> str:
    for sep in (",", ";", " - ", " — ", ":"):
        if sep in sentence:
            sentence = sentence.split(sep)[0]
            break
    sentence = " ".join(sentence.split())
    return (
        sentence if len(sentence) <= maxChars
        else sentence[: maxChars - 1].rstrip() + "…"
    )

def optimizeContext(context: str, maxSentences: int = 50) -> str:  # Increased for large docs
    context = cleanInputText(context)                     # Sanitizes input
    tokens = set(tokenizeText(context))                   # Token set for relevance
    sentences = splitIntoSentences(context)               # Sentence segmentation

    filtered = []
    for s in sentences:
        if tokens.intersection(tokenizeText(s)):
            filtered.append(compressSentence(s))          # Keeps only relevant sentences
        if len(filtered) >= maxSentences:
            break

    return "\n".join(filtered)                             # Returns compact context


def askLlm(*, context: str, question: str, retries: int = 3, system_prompt: str = SYSTEM_PROMPT) -> Dict[str, Dict[str, str]]:
    optimizedContext = optimizeContext(context)            # Shrinks context before LLM

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Context:\n{optimizedContext}"},
            {"role": "user", "content": question},
        ],
        "options": {
            "temperature": 0.6,                            # Faster, more deterministic
            "num_predict": 1500,                           # Increased for richer outputs
            "num_ctx": 16384,                              # Increased for large context
            "top_k": 40,
            "top_p": 0.9        
        },
        "stream": False,
    }

    for attempt in range(retries):
        try:
            response = requests.post(
                OLLAMA_URL,
                json=payload,
                timeout=140,                                # Increased timeout for large
            )
            response.raise_for_status()

            return {
                "status": "success",
                "data": {
                    "answer": response.json()["message"]["content"]
                },
            }
        except Exception as exc:
            if attempt == retries - 1:
                logger.error(f"LLM call failed after {retries} attempts: {exc}")
                return {
                    "status": "error",
                    "data": {
                        "answer": str(exc)
                    },
                }
            time.sleep(2 ** attempt)  # Exponential backoff


class RAGHelper:
    def __init__(self, db: Session):
        self.db = db

    def query(self, text: str, top_k: int = 3, min_similarity: float = 0.7) -> List[Dict[str, any]]:

        try:
            embedding = createEmbeddings([text])[0]
            # Use pgvector cosine distance operator '<->' (smaller distance = more similar)
            results = (
                self.db.query(DocumentSummary)
                .order_by(DocumentSummary.embedding.op('<->')(embedding))
                .limit(top_k)
                .all()
            )
            return [{'id': r.ai_document_id, 'summary': r.summary_text, 'tags': r.tags} for r in results]
        except Exception as e:
            logger.error(f"RAG query failed: {e}")
            return []  # Fallback: Empty

    def update_summary_embedding(self, ai_document_id: int, summary: str):
        try:
            embedding = createEmbeddings([summary])[0]
            summary_record = self.db.query(DocumentSummary).filter_by(ai_document_id=ai_document_id).first()
            if summary_record:
                summary_record.embedding = embedding  # Assuming column exists
                self.db.commit()
                logger.info(f"Updated embedding for summary {ai_document_id}")
            else:
                logger.warning(f"No summary record to update embedding for {ai_document_id}")
        except Exception as e:
            logger.error(f"Failed to update summary embedding: {e}")
            self.db.rollback()