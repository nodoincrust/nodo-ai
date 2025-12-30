import redis
import requests
import hashlib
import json
from typing import List

REDIS = redis.Redis(host="localhost", port=6379, decode_responses=True)

NOMIC_URL = "http://localhost:11434/api/embeddings"
MODEL = "nomic-embed-text"


def create_embedding(text: str) -> List[float]:
    """
    Create or fetch cached embedding for text
    """
    key = "emb:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

    cached = REDIS.get(key)
    if cached:
        return json.loads(cached)

    payload = {"model": MODEL, "prompt": text}

    res = requests.post(NOMIC_URL, json=payload)  # timeout=60
    res.raise_for_status()

    vector = res.json()["embedding"]
    REDIS.setex(key, 86400, json.dumps(vector))  # 24h cache

    return vector
