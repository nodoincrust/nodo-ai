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


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cache_key(text: str) -> str:
    return f"emb:{_hash_text(text)}"


def create_embeddings(texts: List[str]) -> List[List[float]]:

    if not texts:
        return []

    results: List[List[float] | None] = [None] * len(texts)
    missing: List[Dict] = []

    pipe = REDIS.pipeline()
    for t in texts:
        pipe.get(_cache_key(t))
    cached = pipe.execute()

    for i, cached_val in enumerate(cached):
        if cached_val:
            results[i] = json.loads(cached_val)
        else:
            missing.append({"index": i, "text": texts[i]})

    if not missing:
        return results  # type: ignore

    for i in range(0, len(missing), MAX_BATCH_SIZE):
        batch = missing[i:i + MAX_BATCH_SIZE]

        payload = {
            "model": EMBED_MODEL,
            "prompt": [item["text"] for item in batch],
        }

        try:
            res = requests.post(
                EMBED_URL,
                json=payload,
                timeout=EMBED_TIMEOUT,
            )
            res.raise_for_status()
            vectors = res.json()["embeddings"]

        except Exception as e:
            # 🔥 FAIL SAFE: zero-vector fallback
            dim = 768
            vectors = [[0.0] * dim for _ in batch]

        for item, vector in zip(batch, vectors):
            idx = item["index"]
            text = item["text"]

            results[idx] = vector
            REDIS.setex(
                _cache_key(text),
                CACHE_TTL,
                json.dumps(vector),
            )

    return results  # type: ignore


def create_embedding(text: str) -> List[float]:
    return create_embeddings([text])[0]