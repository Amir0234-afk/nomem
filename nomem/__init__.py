"""nomem — No One's Memory Management.

Persistent, self-updating, per-user memory for LLM applications, structured as a
bi-temporal knowledge graph with an explicit LLM-powered CRUD interface.

    from nomem import MemoryGraph

    graph = MemoryGraph(user_id="u123", backend="sqlite", embedder="nomic")
    graph.ingest(user="...", assistant="...")
    result = graph.retrieve("Kira")

Status: Phase 2 — SQLite backend, nomic embedder, Ollama extraction, the decay
pass, and hierarchical retrieval all work end-to-end. The PostgreSQL backend is
the remaining Phase 2 deliverable.
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
