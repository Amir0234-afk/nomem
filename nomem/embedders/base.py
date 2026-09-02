"""Embedder adapter interface.

Embedding is an adapter boundary (AGENT.md "Backend and embedder agnosticism").
The default is ``nomic-embed-text`` via a local Ollama (384 dims, free). Any
object implementing :class:`BaseEmbedder` — or any plain callable, via
:class:`nomem.embedders.CallableEmbedder` — can be supplied instead.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import Vector


class BaseEmbedder(ABC):
    """Abstract text -> vector adapter."""

    @abstractmethod
    async def embed(self, text: str) -> Vector:
        """Return the embedding vector for a single string."""

    @abstractmethod
    async def embed_batch(self, texts: list[str]) -> list[Vector]:
        """Return embedding vectors for many strings, order preserved."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """The fixed length of every vector this embedder produces."""
