"""Default LLM adapter: a local Ollama chat model.

Uses ``POST /api/chat`` with ``format: "json"`` (or a JSON schema when the
caller supplies one). No third-party runtime dependency — see
:mod:`nomem._http`.
"""

from __future__ import annotations

import json
from typing import Any

from .._http import HTTPError, post_json
from ..exceptions import ExtractionError
from .base import BaseLLM

DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_HOST = "http://localhost:11434"


class OllamaLLM(BaseLLM):
    """Chat completion via a local Ollama server."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        timeout: float = 120.0,
        **options: object,
    ) -> None:
        self.model = model
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.options = options

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            # A JSON schema pins the shape; "json" alone still forces valid JSON.
            "format": schema if schema is not None else "json",
            "options": {"temperature": 0.0},
        }

        try:
            response = await post_json(
                f"{self.host}/api/chat", payload, timeout=self.timeout
            )
        except HTTPError as exc:
            raise ExtractionError(f"Ollama chat call failed: {exc}") from exc

        content = response.get("message", {}).get("content", "")
        if not content:
            raise ExtractionError("Ollama chat returned an empty message")

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ExtractionError(
                f"Ollama chat did not return valid JSON: {content[:200]!r}"
            ) from exc

        if not isinstance(parsed, dict):
            raise ExtractionError(f"expected a JSON object, got {type(parsed).__name__}")
        return parsed
