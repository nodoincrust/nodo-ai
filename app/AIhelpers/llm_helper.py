import requests
import re
from typing import Iterable, List, Dict

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.1"

SYSTEM_PROMPT = """
You are an enterprise-grade AI assistant operating inside a document-centric,
memory-aware question answering system.

CORE RULES:
• Answer only from provided document context
• If the document provides partial information, respond with the best possible interpretation and clearly state any limitations.
• Do NOT hallucinate or invent facts
• If information is missing, say: "The provided document does not contain this information"
• Do NOT use external knowledge unless explicitly allowed

RESPONSE STYLE:
• Be direct and concise
• Use structured format (lists, steps) when helpful
• Cite aligned text from documents
• Ask for clarification if the question is ambiguous

MEMORY & CONTEXT:
• Use session memory only for relevant context
• Do NOT contradict previous information
• Synthesize multiple document chunks logically

ERROR HANDLING:
• Never guess or assume
• Acknowledge uncertainty explicitly
• Refuse unsafe or off-topic requests

Every response must be: Grounded, Accurate, and Trustworthy.
"""


def cleanInputText(text: str) -> str:

    return re.sub(r"[\x00-\x1F\x7F]", " ", text)


def askLlm(*, context: str, question: str) -> Dict[str, Dict[str, str]]:
    cleanedContext = cleanInputText(context)

    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"Context:\n{cleanedContext}"},
            {"role": "user", "content": question},
        ],
        "options": {
            "temperature": 0.8,
            "num_predict": 400,
        },
        "stream": False,
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload)
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


def askLlmStream(*, context: str, question: str) -> Iterable[str]:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"Context:\n{context}"},
            {"role": "user", "content": question},
        ],
        "stream": True,
    }

    with requests.post(OLLAMA_URL, json=payload, stream=True) as response:
        for line in response.iter_lines():
            if not line:
                continue

            decoded = line.decode("utf-8")
            if '"content":"' in decoded:
                yield decoded.split('"content":"')[1].split('"')[0]


# =========================
# OPTIONAL TEXT UTILITIES
# =========================

_STOPWORDS = {
    "the", "and", "is", "in", "to", "of", "a", "for", "on", "with", "as", "by",
    "that", "this", "are", "was", "it", "be", "or", "from", "at", "an", "which",
}


def tokenizeText(text: str) -> List[str]:
    tokens = re.findall(r"[A-Za-z]{2,}", text.lower())
    return [token for token in tokens if token not in _STOPWORDS]


def splitIntoSentences(text: str) -> List[str]:
    sentences = re.split(r"(?<=[\.\?\!])\s+", text.strip())
    return [s.replace("\n", " ").strip() for s in sentences if s.strip()]


def compressSentence(sentence: str, maxChars: int = 200) -> str:
    for separator in [",", ";", " - ", " — ", ":"]:
        if separator in sentence:
            sentence = sentence.split(separator)[0]
            break

    sentence = " ".join(sentence.split())
    if len(sentence) > maxChars:
        return sentence[: maxChars - 1].rstrip() + "…"

    return sentence
