"""Default embedder: ``nomic-embed-text`` via a local Ollama.

Target: **Phase 1**. 384 dimensions, runs locally, no API key. Talks to the
Ollama HTTP API (``/api/embeddings``) using the stdlib — no runtime dependency.

The ``embed`` methods are stubs; ``dimensions`` is already correct so config
validation and wiring work today.
"""

from __future__ import annotations

from ..models import Vector
from .base import BaseEmbedder

_PHASE = "NomicEmbedder is a Phase 1 deliverable and is not implemented yet"


class NomicEmbedder(BaseEmbedder):
    """Stub. See module docstring."""

    def __init__(
        self,
        *,
        model: str = "nomic-embed-text",
        host: str = "http://localhost:11434",
        **options: object,
    ) -> None:
        self.model = model
        self.host = host
        self.options = options

    async def embed(self, text: str) -> Vector:
        raise NotImplementedError(_PHASE)

    async def embed_batch(self, texts: list[str]) -> list[Vector]:
        raise NotImplementedError(_PHASE)

    @property
    def dimensions(self) -> int:
        return 384
