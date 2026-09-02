"""OpenAI embedder.

Target: **Phase 1+**. Requires the ``openai`` extra. ``dimensions`` follows the
chosen model (``text-embedding-3-small`` -> 1536 by default; overridable).

The ``embed`` methods are stubs.
"""

from __future__ import annotations

from ..models import Vector
from .base import BaseEmbedder

_PHASE = "OpenAIEmbedder is not implemented yet (requires the 'openai' extra)"

_MODEL_DIMS = {
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}


class OpenAIEmbedder(BaseEmbedder):
    """Stub. See module docstring."""

    def __init__(
        self,
        *,
        model: str = "text-embedding-3-small",
        dimensions: int | None = None,
        api_key: str | None = None,
        **options: object,
    ) -> None:
        self.model = model
        self._dimensions = dimensions or _MODEL_DIMS.get(model, 1536)
        self.api_key = api_key
        self.options = options

    async def embed(self, text: str) -> Vector:
        raise NotImplementedError(_PHASE)

    async def embed_batch(self, texts: list[str]) -> list[Vector]:
        raise NotImplementedError(_PHASE)

    @property
    def dimensions(self) -> int:
        return self._dimensions
