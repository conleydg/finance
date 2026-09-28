"""Thin client for the local Ollama server. Only ever talks to 127.0.0.1."""
import json
import os

import httpx

OLLAMA = "http://127.0.0.1:11434"
MODEL = os.environ.get("FINANCE_MODEL", "gemma4:26b-a4b-it-q4_K_M")


def available() -> bool:
    try:
        tags = httpx.get(f"{OLLAMA}/api/tags", timeout=3).json()
        return any(m["name"] == MODEL for m in tags.get("models", []))
    except Exception:
        return False


def chat_json(prompt: str, schema: dict, model: str = MODEL) -> dict:
    r = httpx.post(f"{OLLAMA}/api/chat", timeout=600, json={
        "model": model,
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_ctx": 16384},
        "keep_alive": "10m",
        "messages": [{"role": "user", "content": prompt}],
    })
    r.raise_for_status()
    return json.loads(r.json()["message"]["content"])


def chat(messages: list[dict], tools: list[dict] | None = None, model: str = MODEL) -> dict:
    """One chat turn with optional tool definitions; returns Ollama's message dict."""
    body = {"model": model, "stream": False, "think": False, "messages": messages,
            "options": {"temperature": 0.3, "num_ctx": 16384}, "keep_alive": "10m"}
    if tools:
        body["tools"] = tools
    r = httpx.post(f"{OLLAMA}/api/chat", timeout=600, json=body)
    r.raise_for_status()
    return r.json()["message"]
