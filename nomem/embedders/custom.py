"""Adapter that turns any callable into a :class:`BaseEmbedder`.

This is how a dev "passes any callable" (AGENT.md). It is fully implemented —
it is trivial glue with no external dependency.

The wrapped callable may be sync or async and takes a single string::

    graph = MemoryGraph(
        user_id="u1",
        embedder=CallableEmbedder(my_embed_fn, dimensions=768),
    )
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from ..exceptions import ConfigError
from ..models import Vector
from .base import BaseEmbedder

EmbedFn = Callable[[str], Vector] | Callable[[str], Awaitable[Vector]]


class CallableEmbedder(BaseEmbedder):
    """Wrap ``fn`` (sync or async, ``str -> vector``) as an embedder."""

    def __init__(
        self,
        fn: EmbedFn,
        *,
        dimensions: int,
        batch_fn: Callable[[list[str]], list[Vector]]
        | Callable[[list[str]], Awaitable[list[Vector]]]
        | None = None,
    ) -> None:
        if dimensions < 1:
            raise ConfigError("CallableEmbedder requires dimensions >= 1")
        self._fn = fn
        self._batch_fn = batch_fn
        self._dimensions = dimensions

    async def embed(self, text: str) -> Vector:
        result = self._fn(text)
        if isinstance(result, Awaitable):
            return await result
        return result

    async def embed_batch(self, texts: list[str]) -> list[Vector]:
        if self._batch_fn is not None:
            result = self._batch_fn(texts)
            if isinstance(result, Awaitable):
                return await result
            return result
        return list(await asyncio.gather(*(self.embed(t) for t in texts)))

    @property
    def dimensions(self) -> int:
        return self._dimensions
