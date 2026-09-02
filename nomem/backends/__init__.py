"""Backend adapters and the name -> class registry."""

from __future__ import annotations

from typing import Any

from ..exceptions import ConfigError
from .base import BaseBackend
from .neo4j import Neo4jBackend
from .postgres import PostgresBackend
from .sqlite import SQLiteBackend

BACKEND_REGISTRY: dict[str, type[BaseBackend]] = {
    "sqlite": SQLiteBackend,
    "postgres": PostgresBackend,
    "neo4j": Neo4jBackend,
}


def resolve_backend(
    backend: str | BaseBackend,
    options: dict[str, Any] | None = None,
) -> BaseBackend:
    """Turn a config value into a live backend.

    ``backend`` may be a registered name (``"sqlite"``) or an already-built
    :class:`BaseBackend` instance (which is returned as-is).
    """
    if isinstance(backend, BaseBackend):
        return backend
    if not isinstance(backend, str):
        raise ConfigError(f"backend must be a name or a BaseBackend, got {type(backend)!r}")
    try:
        cls = BACKEND_REGISTRY[backend]
    except KeyError:
        raise ConfigError(
            f"unknown backend {backend!r}; known: {sorted(BACKEND_REGISTRY)}"
        ) from None
    return cls(**(options or {}))


__all__ = [
    "BACKEND_REGISTRY",
    "BaseBackend",
    "Neo4jBackend",
    "PostgresBackend",
    "SQLiteBackend",
    "resolve_backend",
]
