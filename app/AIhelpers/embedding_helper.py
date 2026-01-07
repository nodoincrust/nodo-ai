import redis
import requests
import hashlib
import json
import logging
from typing import List

logger = logging.getLogger("ai.embedding")

REDIS = redis.Redis(host="localhost", port=6379, decode_responses=True)

EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
CACHE_TTL = 86400
TIMEOUT = 60


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def hashText(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def getCacheKey(text: str) -> str:
    return f"emb:{hashText(text)}"


# --------------------------------------------------
# SINGLE TEXT EMBEDDING (CORE, OLLAMA SAFE)
# --------------------------------------------------

def createEmbedding(text: str) -> List[float]:
    """
    Generate embedding for a SINGLE text (Ollama compatible).
    """

    if not isinstance(text, str):
        raise TypeError(f"Expected str, got {type(text)}")

    text = text.strip()
    if not text:
        raise ValueError("Empty text passed for embedding")

    cache_key = getCacheKey(text)

    # 1️⃣ Redis cache
    cached = REDIS.get(cache_key)
    if cached:
        vec = json.loads(cached)
        if isinstance(vec, list) and len(vec) == EMBED_DIM:
            return vec
        REDIS.delete(cache_key)

    # 2️⃣ Ollama call (ONE TEXT ONLY)
    payload = {
        "model": EMBED_MODEL,
        "prompt": text,
    }

    response = requests.post(
        EMBED_URL,
        json=payload,
        timeout=TIMEOUT,
    )

    if response.status_code != 200:
        logger.error("Ollama embedding failed: %s", response.text)
        raise RuntimeError("Embedding generation failed")

    data = response.json()
    embedding = data.get("embedding")

    # 3️⃣ Validate
    if not isinstance(embedding, list):
        raise RuntimeError("Invalid embedding format from Ollama")

    if len(embedding) != EMBED_DIM:
        raise RuntimeError(
            f"Invalid embedding dimension {len(embedding)}"
        )

    # 4️⃣ Cache + return
    REDIS.setex(cache_key, CACHE_TTL, json.dumps(embedding))
    return embedding


# --------------------------------------------------
# LIST / BATCH API (SAFE WRAPPER)
# --------------------------------------------------

def createEmbeddings(texts: List[str]) -> List[List[float]]:
    """
    Batch-friendly API.
    Internally calls Ollama ONE TEXT AT A TIME.
    """

    if not isinstance(texts, list):
        raise TypeError("createEmbeddings expects a list of strings")

    embeddings: List[List[float]] = []

    for text in texts:
        embeddings.append(createEmbedding(text))

    return embeddings