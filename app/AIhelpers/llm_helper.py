import requests
import re
from typing import Dict, List
import time
import logging
from sqlalchemy.orm import Session
from app.models import DocumentSummary
from .embedding_helper import createEmbeddings 

logger = logging.getLogger("ai.llm_helper")

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "qwen2.5:3b-instruct-q4_0"

SYSTEM_PROMPT = """
You are a document-grounded AI assistant.

Rules:
• Answer only from the provided document context and session memory
• Do not hallucinate, guess, or use external knowledge
• If the answer is not present, reply: "The provided document does not contain this information"

Behavior:
• Be concise and structured when useful
• Use session memory and chat history when provided and do not deny topics present there
• Ask for clarification if the question is ambiguous

Safety & Accuracy:
• Acknowledge uncertainty explicitly
• Refuse unsafe or off-topic requests

All responses must be grounded, accurate, and trustworthy.
"""

_NON_PRINTABLE_RE = re.compile(r"[\x00-\x1F\x7F]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\?\!])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")

_STOPWORDS = {
    "the", "and", "is", "in", "to", "of", "a", "for", "on", "with", "as", "by",
    "that", "this", "are", "was", "it", "be", "or", "from", "at", "an", "which",
}


def cleanInputText(text: str) -> str:
    """Removes non-printable chars"""
    return _NON_PRINTABLE_RE.sub(" ", text)  


def tokenizeText(text: str) -> List[str]:
    """Extracts meaningful tokens only"""
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS
    ]  


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
        sentence if len(sentence) <= maxChars
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


def askLlm(*, context: str, question: str, retries: int = 3, system_prompt: str = SYSTEM_PROMPT) -> Dict[str, Dict[str, str]]:
    """
    Call the LLM with optimized context and question.
    Returns: {"status": "success"/"error", "data": {"answer": "..."}}
    """
    logger.info(f"askLlm called - context length: {len(context)}, question: {question[:100]}...")
    
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
            "temperature": 0.4,
            "num_predict": 1000,
            "num_ctx": 16384,
            "top_k": 40,
            "top_p": 0.9        
        },
        "stream": False,
    }

    logger.info(f"Sending request to Ollama at {OLLAMA_URL}")

    for attempt in range(retries):
        try:
            logger.info(f"Attempt {attempt + 1}/{retries}")
            
            response = requests.post(
                OLLAMA_URL,
                json=payload,
                timeout=140,
            )
            
            logger.info(f"Response status code: {response.status_code}")
            response.raise_for_status()

            response_json = response.json()
            logger.info(f"Response received from LLM")
            
            answer = response_json.get("message", {}).get("content", "")
            logger.info(f"LLM answer length: {len(answer)}")

            return {
                "status": "success",
                "data": {
                    "answer": answer
                },
            }
            
        except requests.exceptions.ConnectionError as conn_err:
            logger.error(f"Connection error to Ollama: {conn_err}")
            if attempt == retries - 1:
                return {
                    "status": "error",
                    "data": {
                        "answer": f"Cannot connect to Ollama at {OLLAMA_URL}. Is Ollama running?"
                    },
                }
            time.sleep(2 ** attempt)
            
        except requests.exceptions.Timeout as timeout_err:
            logger.error(f"Timeout calling Ollama: {timeout_err}")
            if attempt == retries - 1:
                return {
                    "status": "error",
                    "data": {
                        "answer": "Request to LLM timed out"
                    },
                }
            time.sleep(2 ** attempt)
            
        except Exception as exc:
            logger.exception(f"LLM call failed on attempt {attempt + 1}")
            if attempt == retries - 1:
                return {
                    "status": "error",
                    "data": {
                        "answer": str(exc)
                    },
                }
            time.sleep(2 ** attempt)


class RAGHelper:
    """Helper class for RAG (Retrieval Augmented Generation)"""
    
    def __init__(self, db: Session):
        self.db = db

    def query(self, text: str, top_k: int = 3, min_similarity: float = 0.7) -> List[Dict[str, any]]:
        """
        Query similar documents using embeddings
        Returns list of similar document summaries
        """
        try:
            logger.info(f"RAG query for text: {text[:100]}...")
            embedding = createEmbeddings([text])[0]
            
            # Use pgvector cosine distance operator '<->' (smaller distance = more similar)
            results = (
                self.db.query(DocumentSummary)
                .order_by(DocumentSummary.embedding.op('<->')(embedding))
                .limit(top_k)
                .all()
            )
            
            logger.info(f"RAG found {len(results)} similar documents")
            
            return [
                {
                    'id': r.ai_document_id, 
                    'summary': r.summary_text, 
                    'tags': r.tags or []
                } 
                for r in results
            ]
        except Exception as e:
            logger.error(f"RAG query failed: {e}")
            return []

    def update_summary_embedding(self, ai_document_id: int, summary: str):
        """
        Update the embedding for a document summary
        """
        try:
            logger.info(f"Updating embedding for ai_document_id={ai_document_id}")
            embedding = createEmbeddings([summary])[0]
            
            summary_record = self.db.query(DocumentSummary).filter_by(
                ai_document_id=ai_document_id
            ).first()
            
            if summary_record:
                summary_record.embedding = embedding
                self.db.commit()
                logger.info(f"Updated embedding for summary {ai_document_id}")
            else:
                logger.warning(f"No summary record to update embedding for {ai_document_id}")
        except Exception as e:
            logger.error(f"Failed to update summary embedding: {e}")
            self.db.rollback()