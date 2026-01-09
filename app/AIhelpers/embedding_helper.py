import redis
import requests
import hashlib
import json
import logging
from typing import List

logger = logging.getLogger("ai.embedding")

REDIS = redis.Redis(host="localhost", port=6379, decode_responses=True)  
EMBED_URL = "http://localhost:11434/api/embeddings"                      # Ollama embedding endpoint
EMBED_MODEL = "nomic-embed-text"                                        
EMBED_DIM = 768                                                         
CACHE_TTL = 86400                                                       
TIMEOUT = 60                                                           


def hashText(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def getCacheKey(text: str) -> str:
    return f"emb:{hashText(text)}"                                     


def createEmbedding(text: str) -> List[float]:
    if not isinstance(text, str):
        raise TypeError(f"Expected str, got {type(text)}")              

    text = text.strip()
    if not text:
        raise ValueError("Empty text passed for embedding")              

    cache_key = getCacheKey(text)
    cached = REDIS.get(cache_key)
    if cached:
        vec = json.loads(cached)
        if isinstance(vec, list) and len(vec) == EMBED_DIM:
            return vec                                                   
        REDIS.delete(cache_key)                                         

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
        logger.error("Embedding generation failed: %s", response.text)
        raise RuntimeError("Embedding generation failed")                

    data = response.json()
    embedding = data.get("embedding")

    if not isinstance(embedding, list):
        raise RuntimeError("Invalid embedding format")                   

    if len(embedding) != EMBED_DIM:
        raise RuntimeError(f"Invalid embedding dimension {len(embedding)}")

    REDIS.setex(cache_key, CACHE_TTL, json.dumps(embedding))            
    return embedding


def createEmbeddings(texts: List[str]) -> List[List[float]]:
    if not isinstance(texts, list):
        raise TypeError("createEmbeddings expects a list of strings")     # Validates batch input

    return [createEmbedding(text) for text in texts]                     # Generates embeddings sequentially