"""Default embedder: ``nomic-embed-text`` via a local Ollama.

Talks to Ollama's ``POST /api/embed`` (batch-capable) using the stdlib — no
runtime dependency.

**Dimensions.** AGENT.md states 384, but ``nomic-embed-text`` natively produces
**768**-dim vectors; that is the default here. The model supports Matryoshka
truncation, so a smaller ``dimensions=`` (e.g. 256) is honored by slicing +
renormalizing.
"""

from __future__ import annotations

import math

from .._http import HTTPError, post_json
from ..exceptions import EmbedderError
from ..models import Vector
from .base import BaseEmbedder

NATIVE_DIMENSIONS = 768
DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_HOST = "http://localhost:11434"


def _renormalize(vec: Vector) -> Vector:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0.0:
        return vec
    return [x / norm for x in vec]


class NomicEmbedder(BaseEmbedder):
    """Embeddings from a local Ollama running ``nomic-embed-text``."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        host: str = DEFAULT_HOST,
        dimensions: int = NATIVE_DIMENSIONS,
        timeout: float = 60.0,
        **options: object,
    ) -> None:
        if not 1 <= dimensions <= NATIVE_DIMENSIONS:
            raise EmbedderError(
                f"dimensions must be in [1, {NATIVE_DIMENSIONS}] for {model}"
            )
        self.model = model
        self.host = host.rstrip("/")
        self._dimensions = dimensions
        self.timeout = timeout
        self.options = options

    async def embed(self, text: str) -> Vector:
        return (await self.embed_batch([text]))[0]

    async def embed_batch(self, texts: list[str]) -> list[Vector]:
        if not texts:
            return []
        payload = {"model": self.model, "input": texts}
        try:
            response = await post_json(f"{self.host}/api/embed", payload, timeout=self.timeout)
        except HTTPError as exc:
            raise EmbedderError(f"Ollama embed call failed: {exc}") from exc

        raw = response.get("embeddings")
        if not isinstance(raw, list) or len(raw) != len(texts):
            raise EmbedderError(
                f"Ollama returned {len(raw) if isinstance(raw, list) else 'no'} "
                f"embeddings for {len(texts)} inputs"
            )

        vectors: list[Vector] = []
        for vec in raw:
            floats = [float(x) for x in vec]
            if self._dimensions < len(floats):
                floats = _renormalize(floats[: self._dimensions])
            vectors.append(floats)
        return vectors

    @property
    def dimensions(self) -> int:
        return self._dimensions
