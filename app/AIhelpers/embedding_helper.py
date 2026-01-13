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

#Batched Embeddings 
def createEmbeddings(texts: List[str]) -> List[List[float]]:

    if not texts:
        return []

    results: List[List[float] | None] = [None] * len(texts)
    missing: List[Dict] = []

    #Read cache in pipeline
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
        return results  # type: ignore

    #Batch call to Ollama
    for i in range(0, len(missing), MAX_BATCH_SIZE):
        batch = missing[i : i + MAX_BATCH_SIZE]

        payload = {
            "model": EMBED_MODEL,
            "input": [item["text"] for item in batch],
        }

        try:
            response = requests.post(
                EMBED_URL,
                json=payload,
                timeout=EMBED_TIMEOUT,
            )
            response.raise_for_status()
            vectors = [response.json()["embeddings"]]

        except Exception as exc:
            logger.exception("Embedding batch failed: %s", str(exc))
            vectors = [[0.0] * EMBED_DIM for _ in batch]

        for item, vector in zip(batch, vectors):
            idx = item["index"]
            text = item["text"]

            results[idx] = vector
            REDIS.setex(
                _cache_key(text),
                CACHE_TTL,
                json.dumps(vector),
            )

    return results


def createEmbedding(text: str) -> List[float]:
    """Single-text wrapper (still batched internally)"""
    return createEmbeddings([text])[0]