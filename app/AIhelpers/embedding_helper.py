import redis
import requests
import hashlib
import json
from typing import List, Dict

REDIS = redis.Redis(host="localhost", port=6379, decode_responses=True)

EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"

EMBED_TIMEOUT = 30          # seconds
CACHE_TTL = 86400           # 24 hours
MAX_BATCH_SIZE = 16         # HARD LIMIT


def hashText(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def getCacheKey(text: str) -> str:
    return f"emb:{hashText(text)}"


def createEmbeddings(texts: List[str]) -> List[List[float]]:
    """
    Create embeddings with Redis caching and batch safety.
    """
    if not texts:
        return []

    results: List[List[float] | None] = [None] * len(texts)
    missing: List[Dict] = []

    pipe = REDIS.pipeline()
    for text in texts:
        pipe.get(getCacheKey(text))
    cachedResults = pipe.execute()

    for index, cachedValue in enumerate(cachedResults):
        if cachedValue:
            results[index] = json.loads(cachedValue)
        else:
            missing.append({"index": index, "text": texts[index]})

    if not missing:
        return results  # type: ignore

    for i in range(0, len(missing), MAX_BATCH_SIZE):
        batch = missing[i : i + MAX_BATCH_SIZE]

        payload = {
            "model": EMBED_MODEL,
            "prompt": [item["text"] for item in batch],
        }

        try:
            response = requests.post(
                EMBED_URL,
                json=payload,
                timeout=EMBED_TIMEOUT,
            )
            response.raise_for_status()
            vectors = response.json()["embeddings"]

        except Exception:
            # 🔥 FAIL-SAFE: zero-vector fallback
            dimension = 768
            vectors = [[0.0] * dimension for _ in batch]

        for item, vector in zip(batch, vectors):
            index = item["index"]
            text = item["text"]

            results[index] = vector
            REDIS.setex(
                getCacheKey(text),
                CACHE_TTL,
                json.dumps(vector),
            )

    return results  # type: ignore


def createEmbedding(text: str) -> List[float]:
    """
    Convenience wrapper for single-text embedding.
    """
    return createEmbeddings([text])[0]
