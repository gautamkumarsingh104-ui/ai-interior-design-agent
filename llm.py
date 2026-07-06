"""Thin wrapper around the Gemini (google-genai) SDK.

Centralises client creation, JSON generation and a plain-text helper so the
agent, guardrails and eval harness all share one configured client. Every
function degrades gracefully when no API key is present, so the app can still
start and show a helpful message instead of crashing.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional at runtime
    pass

MODEL_NAME = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")

_client = None
_client_error: str | None = None


def _api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def get_client():
    """Return a cached google-genai Client, or None if unavailable."""
    global _client, _client_error
    if _client is not None:
        return _client
    key = _api_key()
    if not key:
        _client_error = (
            "No GEMINI_API_KEY found. Set it in your environment or a .env file."
        )
        return None
    try:
        from google import genai

        _client = genai.Client(api_key=key)
        return _client
    except Exception as exc:  # pragma: no cover - import/network defensive
        _client_error = f"Could not initialise Gemini client: {exc}"
        return None


def is_available() -> bool:
    return get_client() is not None


def availability_message() -> str:
    return _client_error or "Gemini client ready."


def generate_text(prompt: str, system: str | None = None,
                  temperature: float = 0.4) -> str:
    """Single-shot text generation. Returns '' if the client is unavailable."""
    client = get_client()
    if client is None:
        return ""
    from google.genai import types

    kwargs: dict = {"temperature": temperature}
    if system:
        kwargs["system_instruction"] = system
    config = types.GenerateContentConfig(**kwargs)
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME, contents=prompt, config=config
        )
        return (resp.text or "").strip()
    except Exception:
        return ""


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a model response."""
    text = text.strip()
    if not text:
        return None
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        # Fall back to the first {...} or [...] block.
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except Exception:
                return None
    return None


def generate_json(prompt: str, system: str | None = None,
                  temperature: float = 0.2) -> Any:
    """Generate a JSON object/array from the model. Returns None on failure."""
    client = get_client()
    if client is None:
        return None
    from google.genai import types

    kwargs: dict = {
        "temperature": temperature,
        "response_mime_type": "application/json",
    }
    if system:
        kwargs["system_instruction"] = system
    config = types.GenerateContentConfig(**kwargs)
    try:
        resp = client.models.generate_content(
            model=MODEL_NAME, contents=prompt, config=config
        )
        return _extract_json(resp.text or "")
    except Exception:
        return None
