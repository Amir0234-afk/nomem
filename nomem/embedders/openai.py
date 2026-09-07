"""OpenAI embedder — plain HTTP, no dependency.

Talks to ``POST /v1/embeddings`` through :mod:`nomem._http` (stdlib only), so
nomem's "zero mandatory runtime dependencies" claim holds and there is no
``openai`` extra to install.

The API key comes from ``api_key=`` or the ``OPENAI_API_KEY`` environment
variable, and is only required when an embedding is actually requested —
constructing the embedder (which is what ``dimensions`` inspection needs) never
touches the network or the environment.

``dimensions`` follows the model (``text-embedding-3-small`` -> 1536). The
``-3`` models support Matryoshka truncation, so a smaller ``dimensions=`` is
passed through to the API.
"""

from __future__ import annotations

import os
from typing import Any

from .._http import HTTPError, post_json
from ..exceptions import EmbedderError
from ..models import Vector
from .base import BaseEmbedder

DEFAULT_MODEL = "text-embedding-3-small"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
#: Only the ``-3`` models accept a truncated output size.
_TRUNCATABLE = ("text-embedding-3-",)


class OpenAIEmbedder(BaseEmbedder):
    """Embeddings from OpenAI's ``/v1/embeddings`` endpoint."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        dimensions: int | None = None,
        api_key: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 60.0,
        **options: object,
    ) -> None:
        native = _MODEL_DIMS.get(model, 1536)
        if dimensions is not None and not 1 <= dimensions <= native:
            raise EmbedderError(f"dimensions must be in [1, {native}] for {model}")
        if dimensions is not None and dimensions != native and not model.startswith(_TRUNCATABLE):
            raise EmbedderError(f"{model} does not support a truncated output size")
        self.model = model
        self._dimensions = dimensions or native
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.options = options

    async def embed(self, text: str) -> Vector:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[Vector]:
        if not texts:
            return []
        key = self.api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise EmbedderError(
                "OpenAIEmbedder needs an API key: pass api_key= or set OPENAI_API_KEY"
            )

        payload: dict[str, Any] = {"model": self.model, "input": texts}
        if self._dimensions != _MODEL_DIMS.get(self.model, 1536):
            payload["dimensions"] = self._dimensions
        try:
            response = await post_json(
                f"{self.base_url}/embeddings",
                payload,
                timeout=self.timeout,
                headers={"Authorization": f"Bearer {key}"},
            )
        except HTTPError as exc:
            raise EmbedderError(f"OpenAI embeddings call failed: {exc}") from exc

        raw = response.get("data")
        if not isinstance(raw, list) or len(raw) != len(texts):
            raise EmbedderError(
                f"OpenAI returned {len(raw) if isinstance(raw, list) else 'no'} "
                f"embeddings for {len(texts)} inputs"
            )
        # The API may return results out of order; `index` is authoritative.
        ordered = sorted(raw, key=lambda item: item.get("index", 0))
        return [[float(x) for x in item["embedding"]] for item in ordered]

    @property
    def dimensions(self) -> int:
        return self._dimensions
