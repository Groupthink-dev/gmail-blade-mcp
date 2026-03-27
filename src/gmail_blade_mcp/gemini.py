"""Gemini-powered intelligence layer for Gmail Blade MCP.

Uses the Google GenAI SDK (``google-genai``) for email classification and
summarisation. Gated behind ``GOOGLE_API_KEY`` — tools degrade gracefully
when the key is absent.

Model default: ``gemini-2.0-flash-lite`` (fast, cheap, sufficient for
single-email classification/summarisation).
"""

# mypy: disable-error-code="no-any-return"

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-2.0-flash-lite"

# Concise system prompts — minimise input tokens
_CLASSIFY_SYSTEM = (
    "You are an email classifier. Given an email (headers + body), return a JSON object with:\n"
    '- "category": one of: personal, work, transactional, marketing, notification, social, finance, travel, support\n'
    '- "priority": one of: high, normal, low\n'
    '- "action": one of: reply_needed, review, fyi, archive\n'
    '- "summary": one sentence (max 20 words)\n'
    "Return ONLY the JSON object, no markdown fences."
)

_SUMMARISE_SYSTEM = (
    "You are an email summariser. Given an email or thread, produce a concise summary.\n"
    "Rules:\n"
    "- Lead with the key decision, request, or information\n"
    "- Include action items if any, prefixed with '- ACTION:'\n"
    "- Include deadlines if mentioned\n"
    "- Max 150 words\n"
    "- No preamble ('This email is about...'). Start directly with the content."
)


def _get_api_key() -> str | None:
    """Return the Google API key from env, or None."""
    return os.environ.get("GOOGLE_API_KEY", "").strip() or None


def is_gemini_available() -> bool:
    """Check if Gemini is configured (API key present)."""
    return _get_api_key() is not None


def require_gemini() -> str | None:
    """Return an error message if Gemini is not available, else None."""
    if not is_gemini_available():
        return "Error: Gemini not configured. Set GOOGLE_API_KEY to enable AI features."
    return None


class GeminiClient:
    """Thin wrapper around the Google GenAI SDK for email intelligence."""

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        from google import genai

        api_key = _get_api_key()
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set")

        self._client = genai.Client(api_key=api_key)
        self._model = model

    def classify(self, email_text: str) -> dict[str, Any]:
        """Classify an email. Returns dict with category, priority, action, summary."""
        import json

        response = self._client.models.generate_content(
            model=self._model,
            contents=email_text,
            config={
                "system_instruction": _CLASSIFY_SYSTEM,
                "temperature": 0.1,
                "max_output_tokens": 200,
            },
        )

        text = (response.text or "").strip()
        # Strip markdown fences if model includes them despite instructions
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw": text, "error": "Failed to parse classification JSON"}

    def summarise(self, email_text: str) -> str:
        """Summarise an email or thread. Returns plain text summary."""
        response = self._client.models.generate_content(
            model=self._model,
            contents=email_text,
            config={
                "system_instruction": _SUMMARISE_SYSTEM,
                "temperature": 0.2,
                "max_output_tokens": 300,
            },
        )

        return (response.text or "").strip()


# Lazy singleton
_gemini_client: GeminiClient | None = None


def get_gemini_client(model: str = DEFAULT_MODEL) -> GeminiClient:
    """Get or create the GeminiClient singleton."""
    global _gemini_client  # noqa: PLW0603
    if _gemini_client is None:
        _gemini_client = GeminiClient(model=model)
    return _gemini_client
