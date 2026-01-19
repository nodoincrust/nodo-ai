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

    for item in missing:
        idx = item["index"]
        text = item["text"]

        payload = {
            "model": EMBED_MODEL,
            "prompt": text, 
        }

        try:
            response = requests.post(
                EMBED_URL,
                json=payload,
                timeout=EMBED_TIMEOUT,
            )
            response.raise_for_status()
            data = response.json()

            vector = data.get("embedding")
            if not vector or len(vector) != EMBED_DIM:
                raise ValueError("Invalid embedding returned")

        except Exception:
            logger.exception("Embedding failed for text")
            vector = [0.0] * EMBED_DIM

        results[idx] = vector
        REDIS.setex(
            _cache_key(text),
            CACHE_TTL,
            json.dumps(vector),
        )

        logger.info("Embedding sample: %s", vector[:5])

    return results 


def createEmbedding(text: str) -> List[float]:
    return createEmbeddings([text])[0]