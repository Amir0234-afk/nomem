"""nomem — No One's Memory Management.

Persistent, self-updating, per-user memory for LLM applications, structured as a
bi-temporal knowledge graph with an explicit LLM-powered CRUD interface.

    from nomem import MemoryGraph

    graph = MemoryGraph(user_id="u123", backend="sqlite", embedder="nomic")
    graph.ingest(user="...", assistant="...")
    result = graph.retrieve("Kira")

Status: Phase 1 — SQLite backend, nomic embedder, and Ollama extraction wired
end-to-end. Decay and hierarchical retrieval are Phase 2.
"""

from __future__ import annotations

from .config import DecayConfig, IngestConfig, MemoryGraphConfig, RetrievalConfig
from .exceptions import (
    AmbiguousResolutionError,
    BackendError,
    ConfigError,
    EdgeNotFoundError,
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
    "EdgeNotFoundError",
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
