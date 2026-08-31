import redis
import requests
import hashlib
import json
import logging
from typing import List, Dict

logger = logging.getLogger("ai.embedding")

REDIS = redis.Redis(host="localhost", port=6379, decode_responses=True)
 
EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
CACHE_TTL = 86400          # 24 hours
EMBED_TIMEOUT = 120
MAX_BATCH_SIZE = 48        # Increased for faster batching on powerful system

def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_key(text: str) -> str:
    return f"emb:{_hash(text)}"

def createEmbeddings(texts: List[str]) -> List[List[float]]:
    if not texts:
        return []
 
    results: List[List[float] | None] = [None] * len(texts)
    missing: List[Dict] = []
 
    pipe = REDIS.pipeline()
    for t in texts:
        pipe.get(_cache_key(t))
    cached = pipe.execute()
    for i, value in enumerate(cached):
        if value:
            results[i] = json.loads(value)
        else:
            missing.append({"index": i, "text": texts[i]})

    if not missing:
        return results

    pending: List[Dict] = []

    for item in missing:
        idx = item["index"]
        raw_text = (item["text"] or "").strip()

        if not raw_text:
            vector = [0.0] * EMBED_DIM
            results[idx] = vector
            REDIS.setex(_cache_key(raw_text), CACHE_TTL, json.dumps(vector))
            continue

        pending.append(
            {"index": idx, "raw_text": raw_text, "text": _normalizeForEmbedding(raw_text)}
        )

    # Ollama accepts a list of inputs per call, so send them in batches
    # instead of one HTTP round trip per chunk.
    for start in range(0, len(pending), MAX_BATCH_SIZE):
        batch = pending[start : start + MAX_BATCH_SIZE]
        vectors = _embedBatch([item["text"] for item in batch])

        cachePipe = REDIS.pipeline()
        for item, vector in zip(batch, vectors):
            results[item["index"]] = vector
            cachePipe.setex(
                _cache_key(item["raw_text"]),
                CACHE_TTL,
                json.dumps(vector),
            )
        cachePipe.execute()

    return results


def _normalizeForEmbedding(raw_text: str) -> str:
    alpha_count = sum(ch.isalpha() for ch in raw_text)
    digit_count = sum(ch.isdigit() for ch in raw_text)

    text = raw_text

    # Numeric / table heavy → normalize to semantic text
    if digit_count > alpha_count:
        lines = raw_text.splitlines()[:20]

        semantic_lines = []
        for line in lines:
            clean = line.replace("|", " ").replace(",", " ").strip()
            if clean:
                semantic_lines.append(f"Data row containing values: {clean}")

        text = (
            "The following section contains structured numeric or tabular data. "
            "It describes measurements, quantities, identifiers, or metrics.\n"
            + "\n".join(semantic_lines)
        )

    return text[:4000]


def _embedBatch(texts: List[str]) -> List[List[float]]:
    """Embeds a batch in one call, falling back to per-text calls on failure."""
    try:
        response = requests.post(
            EMBED_URL,
            json={"model": EMBED_MODEL, "input": texts},
            timeout=EMBED_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        vectors = data.get("embeddings") or []
        if len(vectors) == len(texts) and all(
            v and len(v) == EMBED_DIM for v in vectors
        ):
            return vectors

        raise ValueError("Invalid batch embedding response")

    except Exception:
        logger.warning(
            "Batch embedding failed for %d texts; falling back to single calls",
            len(texts),
        )
        return [_embedSingle(text) for text in texts]


def _embedSingle(text: str) -> List[float]:
    try:
        response = requests.post(
            EMBED_URL,
            json={"model": EMBED_MODEL, "prompt": text},
            timeout=EMBED_TIMEOUT,
        )
        response.raise_for_status()
        vector = response.json().get("embedding")

        if not vector or len(vector) != EMBED_DIM:
            raise ValueError("Invalid embedding returned")

        return vector

    except Exception:
        logger.exception("Embedding failed for text")
        return [0.0] * EMBED_DIM

def createEmbedding(text: str) -> List[float]:
    return createEmbeddings([text])[0]