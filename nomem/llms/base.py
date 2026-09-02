"""LLM adapter interface.

Entity/relation extraction needs a chat-capable model. Like storage and
embedding, this is an adapter boundary (AGENT.md "Dev-first configurability"):
the default talks to a local Ollama, but any provider — or a plain callable —
can be supplied via ``llm=`` / ``ingest_config.extraction_model``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseLLM(ABC):
    """Abstract chat LLM used by the extraction pipeline."""

    @abstractmethod
    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        """Return the model's response parsed as a JSON object.

        Implementations must request structured output from the provider
        (e.g. Ollama ``format``, a JSON-mode flag, or a tool schema) and raise
        :class:`~nomem.exceptions.ExtractionError` if the response is not a
        JSON object.
        """
