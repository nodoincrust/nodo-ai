import requests
import re
from typing import Dict, List, Iterable

OLLAMA_URL = "http://localhost:11434/api/chat"   # Ollama endpoint
MODEL = "llama3.1"                               # Optimized local model


SYSTEM_PROMPT = """
You are an enterprise-grade AI assistant.

RULES:
- Answer strictly from provided context
- Do NOT hallucinate
- If information is missing, say:
  "The provided document does not contain this information"

STYLE:
- Be concise
- Be factual
"""

_NON_PRINTABLE_RE = re.compile(r"[\x00-\x1F\x7F]")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[\.\?\!])\s+")
_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")

_STOPWORDS = {
    "the", "and", "is", "in", "to", "of", "a", "for", "on", "with", "as", "by",
    "that", "this", "are", "was", "it", "be", "or", "from", "at", "an", "which",
}

# Removes non-printable chars
def cleanInputText(text: str) -> str:
    return _NON_PRINTABLE_RE.sub(" ", text)  

# Extracts meaningful tokens only
def tokenizeText(text: str) -> List[str]:
    return [
        t for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS
    ]  

 # Splits text into sentences
def splitIntoSentences(text: str) -> List[str]:
    return [
        s.replace("\n", " ").strip()
        for s in _SENTENCE_SPLIT_RE.split(text.strip())
        if s.strip()
    ] 


def compressSentence(sentence: str, maxChars: int = 200) -> str:
    for sep in (",", ";", " - ", " — ", ":"):
        if sep in sentence:
            sentence = sentence.split(sep)[0]
            break
    sentence = " ".join(sentence.split())
    return (
        sentence if len(sentence) <= maxChars
        else sentence[: maxChars - 1].rstrip() + "…"
    )

def optimizeContext(context: str, maxSentences: int = 20) -> str:
    context = cleanInputText(context)                     # Sanitizes input
    tokens = set(tokenizeText(context))                   # Token set for relevance
    sentences = splitIntoSentences(context)               # Sentence segmentation

    filtered = []
    for s in sentences:
        if tokens.intersection(tokenizeText(s)):
            filtered.append(compressSentence(s))          # Keeps only relevant sentences
        if len(filtered) >= maxSentences:
            break

    return "\n".join(filtered)                             # Returns compact context


def askLlm(*, context: str, question: str) -> Dict[str, Dict[str, str]]:
    optimizedContext = optimizeContext(context)            # Shrinks context before LLM

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"Context:\n{optimizedContext}"},
            {"role": "user", "content": question},
        ],
        "options": {
            "temperature": 0.6,                            # Faster, more deterministic
            "num_predict": 1000,                            # Hard response cap
            "num_ctx": 4096,
            "top_k": 40,
            "top_p": 0.9        
        },
        "stream": False,
    }

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            # timeout=45,                                    # Prevents blocking
        )
        response.raise_for_status()

        return {
            "status": "success",
            "data": {
                "answer": response.json()["message"]["content"]
            },
        }

    except Exception as exc:
        return {
            "status": "error",
            "data": {
                "answer": str(exc)
            },
        }

# def askLlmStream(*, context: str, question: str) -> Iterable[str]:
#     optimizedContext = optimizeContext(context)             # Same optimization for streaming

#     payload = {
#         "model": MODEL,
#         "messages": [
#             {"role": "system", "content": SYSTEM_PROMPT},
#             {"role": "system", "content": f"Context:\n{optimizedContext}"},
#             {"role": "user", "content": question},
#         ],
#         "options": {
#             "temperature": 0.6,
#             "num_predict": 300,
#         },
#         "stream": True,
#     }

#     with requests.post(
#         OLLAMA_URL,
#         json=payload,
#         stream=True,
#         timeout=45,
#     ) as response:
#         for line in response.iter_lines():
#             if not line:
#                 continue
#             decoded = line.decode("utf-8")
#             if '"content":"' in decoded:
#                 yield decoded.split('"content":"')[1].split('"')[0]  # Token stream
