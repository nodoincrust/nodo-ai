import requests
from typing import Iterable

OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL = "llama3.1:3b"

SYSTEM_PROMPT = """
You are an enterprise-grade AI assistant operating inside a document-centric,
memory-aware question answering system.
CORE RULES:
• Answer only from provided document context
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


def ask_llm(context: str, question: str) -> dict:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"Context:\n{context}"},
            {"role": "user", "content": question},
        ],
        "stream": False,
    }

    res = requests.post(OLLAMA_URL, json=payload) # timeout=120
    res.raise_for_status()

    return {
        "status": "success",
        "data": {
            "answer": res.json()["message"]["content"]
        }
    }


def ask_llm_stream(context: str, question: str) -> Iterable[str]:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": f"Context:\n{context}"},
            {"role": "user", "content": question},
        ],
        "stream": True,
    }

    with requests.post(OLLAMA_URL, json=payload, stream=True) as r:
        for line in r.iter_lines():
            if not line:
                continue

            data = line.decode("utf-8")
            if '"content":"' in data:
                yield data.split('"content":"')[1].split('"')[0]

