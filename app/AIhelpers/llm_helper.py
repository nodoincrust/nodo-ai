import json
import os
import re
import threading
import time
import logging
from typing import Any, Dict, Iterator, List, Optional

import requests
from sqlalchemy.orm import Session

from app.models import DocumentChunk, DocumentSummary
from .embedding_helper import createEmbeddings

logger = logging.getLogger("ai.llm_helper")

# ---------------------------------------------------------------------------
# Configuration (all overridable via environment)
# ---------------------------------------------------------------------------

OLLAMA_BASE_URL = os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_URL = f"{OLLAMA_BASE_URL}/api/chat"
MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:3b-instruct-q4_0")

# Ollama unloads an idle model after 5 minutes by default, so the next request
# pays a full load from disk. Keeping it resident is the single biggest latency
# win on a CPU-only box.
def _parseKeepAlive(raw: str):
    """
    Ollama accepts a duration string ("30m") or a number of seconds, where -1
    means never unload. A bare "-1" is not a valid duration string, so numeric
    values are sent as numbers.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return raw


KEEP_ALIVE = _parseKeepAlive(os.getenv("OLLAMA_KEEP_ALIVE", "30m"))

REQUEST_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "240"))
# Generation is the expensive half (~5.4 tok/s here), so this is the main
# latency dial: roughly 1 second of wait per 5 tokens allowed.
DEFAULT_NUM_PREDICT = int(os.getenv("OLLAMA_NUM_PREDICT", "256"))
DEFAULT_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.4"))

# Fixed on purpose. Ollama reloads the model whenever num_ctx changes, so a
# per-request value silently costs a full reload every time chat and summary
# alternate. Keep prompts inside this window via CONTEXT_CHAR_BUDGET instead.
NUM_CTX = int(os.getenv("OLLAMA_NUM_CTX", "4096"))

# 0 lets Ollama match the host's core count instead of guessing.
NUM_THREAD = int(os.getenv("OLLAMA_NUM_THREAD", "0"))

# Generation is CPU-bound. Summaries run on background threads and chat runs on
# request threads, so without a cap they thrash each other and every response
# gets slower.
MAX_CONCURRENCY = int(os.getenv("OLLAMA_MAX_CONCURRENCY", "2"))

# Measured on the 4-vCPU host: prompt evaluation runs at ~175 tok/s while
# generation runs at ~5.4 tok/s. Reading context is therefore ~32x cheaper per
# token than writing the answer, so context can be generous and num_predict is
# the setting that actually controls latency. Roughly 5000 chars ~= 1600
# tokens ~= 9s of prompt evaluation.
CONTEXT_CHAR_BUDGET = int(os.getenv("OLLAMA_CONTEXT_CHARS", "5000"))

_slot = threading.BoundedSemaphore(MAX_CONCURRENCY)

# Reusing one session avoids a TCP handshake on every generation.
_session = requests.Session()
_session.mount(
    "http://",
    requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8),
)

_NON_PRINTABLE_RE = re.compile(r"[\x00-\x1F\x7F]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\?\!])\s+")


def cleanInputText(text: str) -> str:
    """Removes non-printable chars."""
    return _NON_PRINTABLE_RE.sub(" ", text)


def splitIntoSentences(text: str) -> List[str]:
    """Splits text into sentences."""
    return [
        s.replace("\n", " ").strip()
        for s in _SENTENCE_SPLIT_RE.split(text.strip())
        if s.strip()
    ]


def optimizeContext(context: str, maxChars: int = CONTEXT_CHAR_BUDGET) -> str:
    """
    Trims context to a character budget, cutting on a sentence boundary.

    This deliberately does NOT rewrite the text. An earlier version truncated
    every sentence at its first comma and dropped everything past the 50th
    sentence, which silently discarded 50-75% of the retrieved document before
    the model ever saw it. Chunks arrive here already ranked by the vector
    search, so keeping the head intact is what preserves relevance.
    """
    context = cleanInputText(context).strip()

    if len(context) <= maxChars:
        return context

    kept: List[str] = []
    used = 0

    for sentence in splitIntoSentences(context):
        # +1 for the newline joining sentences back together.
        if used + len(sentence) + 1 > maxChars:
            break
        kept.append(sentence)
        used += len(sentence) + 1

    if not kept:
        # A single sentence longer than the whole budget: keep a prefix rather
        # than sending nothing.
        return context[:maxChars]

    return "\n".join(kept)


def _estimateTokens(text: str) -> int:
    """Deliberately conservative: English averages ~4 chars/token."""
    return max(1, len(text) // 3)


def _fitsContextWindow(prompt: str, numPredict: int) -> bool:
    """True when the prompt plus the reservation for output fits in NUM_CTX."""
    return _estimateTokens(prompt) + numPredict + 128 <= NUM_CTX


_RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}


def _isRetryable(exc: Exception) -> bool:
    """
    A 404 means the model is not pulled and a 400 means the payload is wrong.
    Retrying either just multiplies the wait before the same failure.
    """
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True

    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return exc.response.status_code in _RETRYABLE_STATUS

    return False


def _describeError(exc: Exception) -> str:
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        if status == 404:
            return (
                f"Model '{MODEL}' is not available on the Ollama server at "
                f"{OLLAMA_BASE_URL}. Run: ollama pull {MODEL}"
            )
        return f"Ollama returned HTTP {status}"

    if isinstance(exc, requests.Timeout):
        return f"Ollama did not respond within {REQUEST_TIMEOUT}s"

    if isinstance(exc, requests.ConnectionError):
        return f"Could not reach the Ollama server at {OLLAMA_BASE_URL}"

    return str(exc)


def askLlm(
    *,
    context: str,
    question: str,
    system_prompt: str,
    retries: int = 3,
    fmt: Optional[Any] = None,
    num_predict: Optional[int] = None,
    temperature: Optional[float] = None,
    context_chars: Optional[int] = None,
) -> Dict[str, Dict[str, str]]:
    """
    Generic LLM caller. Caller MUST explicitly pass system_prompt.

    fmt: pass "json" (or a JSON schema dict) to make Ollama constrain the reply
         to valid JSON instead of hoping the prompt is obeyed.
    context_chars: per-call context budget. Latency-sensitive callers (chat)
         should pass less; background callers (summaries) can afford more.
    """
    numPredict = num_predict if num_predict is not None else DEFAULT_NUM_PREDICT

    optimizedContext = optimizeContext(
        context,
        context_chars if context_chars is not None else CONTEXT_CHAR_BUDGET,
    )

    if len(optimizedContext) < len(context.strip()):
        logger.info(
            "Context trimmed to budget: %d -> %d chars",
            len(context),
            len(optimizedContext),
        )

    prompt = system_prompt + optimizedContext + question

    if not _fitsContextWindow(prompt, numPredict):
        logger.warning(
            "Prompt (~%d tokens) plus %d output tokens exceeds num_ctx=%d; "
            "the model will drop the oldest content. Lower "
            "OLLAMA_CONTEXT_CHARS or raise OLLAMA_NUM_CTX.",
            _estimateTokens(prompt),
            numPredict,
            NUM_CTX,
        )

    options: Dict[str, Any] = {
        "temperature": (
            temperature if temperature is not None else DEFAULT_TEMPERATURE
        ),
        "num_predict": numPredict,
        "num_ctx": NUM_CTX,
        "top_k": 40,
        "top_p": 0.9,
    }

    if NUM_THREAD > 0:
        options["num_thread"] = NUM_THREAD

    payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": [
            # Stable content first so Ollama's prefix cache can be reused
            # across requests that share a system prompt.
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Context:\n{optimizedContext}"},
            {"role": "user", "content": question},
        ],
        "options": options,
        "keep_alive": KEEP_ALIVE,
        "stream": False,
    }

    if fmt is not None:
        payload["format"] = fmt

    attempts = max(1, retries)
    lastError = "LLM call failed"

    for attempt in range(attempts):
        queued = time.monotonic()
        started = queued
        try:
            with _slot:
                # Timed separately: everything before this point is waiting for
                # a free generation slot, not Ollama being slow.
                started = time.monotonic()
                response = _session.post(
                    OLLAMA_URL,
                    json=payload,
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()

            answer = response.json().get("message", {}).get("content", "")
            elapsed = time.monotonic() - started
            waited = started - queued

            logger.info(
                "LLM ok in %.1fs (queued %.1fs, ctx=%d chars, num_ctx=%d, out<=%d)",
                elapsed,
                waited,
                len(optimizedContext),
                options["num_ctx"],
                numPredict,
            )

            return {"status": "success", "data": {"answer": answer}}

        except Exception as exc:
            lastError = _describeError(exc)
            elapsed = time.monotonic() - started
            waited = started - queued

            logger.warning(
                "LLM call failed after %.1fs (queued %.1fs, attempt %d/%d): %s",
                elapsed,
                waited,
                attempt + 1,
                attempts,
                lastError,
            )

            if not _isRetryable(exc) or attempt == attempts - 1:
                return {"status": "error", "data": {"answer": lastError}}

            time.sleep(2**attempt)

    # Unreachable, but never return None to callers that immediately .get().
    return {"status": "error", "data": {"answer": lastError}}


def askLlmStream(
    *,
    context: str,
    question: str,
    system_prompt: str,
    num_predict: Optional[int] = None,
    temperature: Optional[float] = None,
    context_chars: Optional[int] = None,
) -> Iterator[str]:
    """
    Yields answer fragments as Ollama produces them.

    Text only, and deliberately not retried: a stream that fails halfway has
    already sent tokens to the client, so restarting it would duplicate them.
    Callers wanting structured output should use askLlm with fmt="json" —
    partial JSON cannot be parsed, so streaming it buys nothing.

    Raises on failure; the caller decides how to surface that mid-stream.
    """
    numPredict = num_predict if num_predict is not None else DEFAULT_NUM_PREDICT

    optimizedContext = optimizeContext(
        context,
        context_chars if context_chars is not None else CONTEXT_CHAR_BUDGET,
    )

    options: Dict[str, Any] = {
        "temperature": (
            temperature if temperature is not None else DEFAULT_TEMPERATURE
        ),
        "num_predict": numPredict,
        "num_ctx": NUM_CTX,
        "top_k": 40,
        "top_p": 0.9,
    }

    if NUM_THREAD > 0:
        options["num_thread"] = NUM_THREAD

    payload: Dict[str, Any] = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "system", "content": f"Context:\n{optimizedContext}"},
            {"role": "user", "content": question},
        ],
        "options": options,
        "keep_alive": KEEP_ALIVE,
        "stream": True,
    }

    # Deliberately does NOT take _slot. This generator stays alive for the whole
    # HTTP response, so a client that disconnects mid-stream can leave the
    # semaphore held and starve every later request in the process. Ollama
    # serialises generation itself via OLLAMA_NUM_PARALLEL, which is where
    # concurrency for streaming belongs.
    started = time.monotonic()
    firstToken = None
    tokens = 0

    try:
        with _session.post(
            OLLAMA_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
            stream=True,
        ) as response:
            response.raise_for_status()

            for raw in response.iter_lines():
                if not raw:
                    continue

                chunk = json.loads(raw)

                if chunk.get("error"):
                    raise RuntimeError(chunk["error"])

                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    if firstToken is None:
                        firstToken = time.monotonic() - started
                        logger.info(
                            "LLM stream first token in %.1fs (ctx=%d chars)",
                            firstToken,
                            len(optimizedContext),
                        )
                    tokens += 1
                    yield piece

                if chunk.get("done"):
                    break

    except GeneratorExit:
        # Client went away. Close the upstream request rather than leaving it
        # generating into nothing.
        logger.info(
            "LLM stream abandoned by client after %.1fs (%d fragments)",
            time.monotonic() - started,
            tokens,
        )
        raise

    except Exception as exc:
        logger.warning(
            "LLM stream failed after %.1fs (%d fragments): %s",
            time.monotonic() - started,
            tokens,
            _describeError(exc),
        )
        raise

    logger.info(
        "LLM stream ok in %.1fs (first token %.1fs, %d fragments, ctx=%d chars)",
        time.monotonic() - started,
        firstToken if firstToken is not None else -1.0,
        tokens,
        len(optimizedContext),
    )


def warmUpModel() -> None:
    """
    Loads the model so the first real request does not pay for it.

    Safe to call in a daemon thread at startup; failures are logged and ignored.
    """
    # The options must match what real requests send. Ollama reloads the model
    # whenever num_ctx or num_thread changes, so warming it with defaults just
    # makes the first real request pay for a reload instead of a load.
    options: Dict[str, Any] = {"num_ctx": NUM_CTX}
    if NUM_THREAD > 0:
        options["num_thread"] = NUM_THREAD

    try:
        _session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": MODEL,
                "prompt": "",
                "keep_alive": KEEP_ALIVE,
                "options": options,
            },
            timeout=REQUEST_TIMEOUT,
        ).raise_for_status()
        logger.info(
            "Ollama model %s warmed up (num_ctx=%d, num_thread=%s)",
            MODEL,
            NUM_CTX,
            NUM_THREAD or "auto",
        )
    except Exception as exc:
        logger.warning("Ollama warm-up skipped: %s", _describeError(exc))


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

        query_embedding = createEmbeddings([text])[0]

        q = self.db.query(DocumentChunk)

        if ai_document_id:
            q = q.filter(DocumentChunk.ai_document_id == ai_document_id)

        if session_id:
            q = q.filter(DocumentChunk.session_id == session_id)

        q = q.filter(DocumentChunk.embedding.isnot(None))

        results = (
            q.order_by(DocumentChunk.embedding.cosine_distance(query_embedding))
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
