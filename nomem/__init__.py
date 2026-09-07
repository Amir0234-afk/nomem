"""nomem — No One's Memory Management.

Persistent, self-updating, per-user memory for LLM applications, structured as a
bi-temporal knowledge graph with an explicit LLM-powered CRUD interface.

    from nomem import MemoryGraph

    graph = MemoryGraph(user_id="u123", backend="sqlite", embedder="nomic")
    graph.ingest(user="...", assistant="...")
    result = graph.retrieve("Kira")

Storage, embedding, and extraction are adapter boundaries: SQLite (zero infra),
PostgreSQL/pgvector, and Neo4j all pass one identical behavioral contract suite,
the nomic (Ollama) and OpenAI embedders both talk plain HTTP with no extra
dependency, and any piece can be replaced with your own class or a bare callable.
Every method has an ``a``-prefixed async twin; the sync API is a thin wrapper over
the async core.

Capability is extended through the ``nomem.plugins`` entry point rather than by
subclassing or forking — see ``docs/stability.md`` for what ``>=0.1,<0.2``
guarantees.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

from .config import DecayConfig, IngestConfig, MemoryGraphConfig, RetrievalConfig
from .exceptions import (
    AmbiguousResolutionError,
    BackendError,
    ConfigError,
    EdgeNotFoundError,
    EmbedderError,
    ExtractionError,
    NodeNotFoundError,
    NomemError,
    NotSupportedError,
    ResolutionError,
    RetrievalBudgetExceededError,
)
from .graph import MemoryGraph
from .models import (
    DecayResult,
    Edge,
    ExtractedEntity,
    ExtractedRelation,
    IngestReceipt,
    Node,
    PurgeResult,
    ResolutionOutcome,
    SubGraph,
)
from .plugins import Plugin

try:
    __version__ = _pkg_version("nomem")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0.0.0+unknown"

__all__ = [
    "AmbiguousResolutionError",
    "BackendError",
    "ConfigError",
    "DecayConfig",
    "DecayResult",
    "Edge",
    "EdgeNotFoundError",
    "EmbedderError",
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionError",
    "IngestConfig",
    "IngestReceipt",
    "MemoryGraph",
    "MemoryGraphConfig",
    "Node",
    "NodeNotFoundError",
    "NomemError",
    "NotSupportedError",
    "Plugin",
    "PurgeResult",
    "ResolutionError",
    "ResolutionOutcome",
    "RetrievalBudgetExceededError",
    "RetrievalConfig",
    "SubGraph",
    "__version__",
]
