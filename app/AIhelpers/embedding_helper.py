import redis
import requests
import hashlib
import json
from typing import List
import numpy as np

REDIS = redis.Redis(host="localhost", port=6379, decode_responses=True)

EMBED_URL = "http://localhost:11434/api/embeddings"
EMBED_MODEL = "nomic-embed-text"


def create_embedding(text: str) -> List[float]:
    """
    Create or fetch cached embedding for text
    """
    key = "emb:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    cached = REDIS.get(key)
    if cached:
        return json.loads(cached)

    payload = {
        "model": EMBED_MODEL,
        "prompt": text
    }

    res = requests.post(EMBED_URL, json=payload) # timeout=60
    res.raise_for_status()

    vector = res.json()["embedding"]
    REDIS.setex(key, 86400, json.dumps(vector))  # 24h cache

    return vector


def create_embeddings(texts: List[str]) -> List[List[float]]:
    """
    Create or fetch cached embeddings for a list of texts.
    Returns embeddings in the same order as `texts`.
    """
    results = [None] * len(texts)
    to_request = []  # tuples of (index, text)

    pipe = REDIS.pipeline()
    for i, t in enumerate(texts):
        key = "emb:" + hashlib.sha256(t.encode("utf-8")).hexdigest()
        pipe.get(key)
    cached = pipe.execute()

    for i, v in enumerate(cached):
        if v:
            results[i] = json.loads(v)
        else:
            to_request.append((i, texts[i]))

    # Request uncached embeddings sequentially to avoid overloading embedding service.
    # If the embedding API supports batching, this can be replaced with a single batch call.
    for idx, txt in to_request:
        payload = {
            "model": EMBED_MODEL,
            "prompt": txt
        }
        res = requests.post(EMBED_URL, json=payload)  # consider timeout
        res.raise_for_status()
        vector = res.json()["embedding"]
        key = "emb:" + hashlib.sha256(txt.encode("utf-8")).hexdigest()
        REDIS.setex(key, 86400, json.dumps(vector))
        results[idx] = vector

    return results