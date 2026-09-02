"""nomem — No One's Memory Management.

Persistent, self-updating, per-user memory for LLM applications, structured as a
bi-temporal knowledge graph with an explicit LLM-powered CRUD interface.

    from nomem import MemoryGraph

    graph = MemoryGraph(user_id="u123", backend="sqlite", embedder="nomic")
    graph.ingest(user="...", assistant="...")
    result = graph.retrieve("Kira")

Status: Phase 0 — the full public surface exists and is typed; the pipelines are
not implemented yet.
"""

from __future__ import annotations

from .config import DecayConfig, IngestConfig, MemoryGraphConfig, RetrievalConfig
from .exceptions import (
    AmbiguousResolutionError,
    BackendError,
    ConfigError,
    EmbedderError,
    ExtractionError,
    HardDeleteNotSupportedError,
    NodeNotFoundError,
    NomemError,
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
    ResolutionOutcome,
    SubGraph,
)

__version__ = "0.0.0"

__all__ = [
    "AmbiguousResolutionError",
    "BackendError",
    "ConfigError",
    "DecayConfig",
    "DecayResult",
    "Edge",
    "EmbedderError",
    "ExtractedEntity",
    "ExtractedRelation",
    "ExtractionError",
    "HardDeleteNotSupportedError",
    "IngestConfig",
    "IngestReceipt",
    "MemoryGraph",
    "MemoryGraphConfig",
    "Node",
    "NodeNotFoundError",
    "NomemError",
    "ResolutionError",
    "ResolutionOutcome",
    "RetrievalBudgetExceededError",
    "RetrievalConfig",
    "SubGraph",
    "__version__",
]
