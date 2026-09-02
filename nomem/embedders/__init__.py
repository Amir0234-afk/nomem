"""Embedder adapters and the name -> class registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..exceptions import ConfigError
from ..models import Vector
from .base import BaseEmbedder
from .custom import CallableEmbedder
from .nomic import NomicEmbedder
from .openai import OpenAIEmbedder

EMBEDDER_REGISTRY: dict[str, type[BaseEmbedder]] = {
    "nomic": NomicEmbedder,
    "openai": OpenAIEmbedder,
}


def resolve_embedder(
    embedder: str | BaseEmbedder | Callable[[str], Vector] | Callable[[str], Awaitable[Vector]],
    options: dict[str, Any] | None = None,
) -> BaseEmbedder:
    """Turn a config value into a live embedder.

    Accepts a registered name (``"nomic"``), a :class:`BaseEmbedder` instance,
    or a bare callable (wrapped in :class:`CallableEmbedder`; ``options`` must
    then include ``dimensions``).
    """
    if isinstance(embedder, BaseEmbedder):
        return embedder
    if callable(embedder) and not isinstance(embedder, type):
        opts = dict(options or {})
        if "dimensions" not in opts:
            raise ConfigError("a callable embedder requires embedder_options['dimensions']")
        return CallableEmbedder(embedder, **opts)
    if not isinstance(embedder, str):
        raise ConfigError(f"embedder must be a name, BaseEmbedder, or callable, got {embedder!r}")
    try:
        cls = EMBEDDER_REGISTRY[embedder]
    except KeyError:
        raise ConfigError(
            f"unknown embedder {embedder!r}; known: {sorted(EMBEDDER_REGISTRY)}"
        ) from None
    return cls(**(options or {}))


__all__ = [
    "EMBEDDER_REGISTRY",
    "BaseEmbedder",
    "CallableEmbedder",
    "NomicEmbedder",
    "OpenAIEmbedder",
    "resolve_embedder",
]
