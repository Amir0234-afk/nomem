"""Configuration dataclasses for nomem.

nomem ships a sane default for every behavior, but nearly everything is
overridable (AGENT.md "Dev-first configurability"). The top-level object is
:class:`MemoryGraphConfig`; the per-call ``config=`` argument to ``ingest`` /
``retrieve`` is a shallow dict merged over the relevant section.

Nothing here does I/O. ``__post_init__`` only performs cheap range checks so a
misconfiguration fails loudly at construction rather than deep in a pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from .exceptions import ConfigError
from .models import DecayMode, IngestMode, RetrievalMode, SubIndexStrategy


@dataclass
class DecayConfig:
    """Importance scoring + pruning parameters.

    Defaults are the AGENT.md "Decay Model" table, verbatim::

        importance = (alpha * log(1 + access_count))
                   + (beta  * exp(-lambda_ * days_since_last_access))
    """

    alpha: float = 0.6  # AGENT.md default: access weight coefficient
    beta: float = 0.4  # AGENT.md default: recency weight coefficient
    lambda_: float = 0.1  # AGENT.md default: decay rate (higher = faster decay)
    importance_floor: float = 0.05  # AGENT.md default: below this -> prune-eligible
    pruning: bool = False  # AGENT.md default: pruning is OFF; dev opts in
    decay_schedule: str | None = None  # AGENT.md default: nomem does not self-schedule
    count_on_ingest: bool = False  # AGENT.md default: access_count increments on retrieve only

    def __post_init__(self) -> None:
        for name in ("alpha", "beta", "lambda_", "importance_floor"):
            if getattr(self, name) < 0:
                raise ConfigError(f"DecayConfig.{name} must be >= 0")


@dataclass
class IngestConfig:
    """Per-call ingest overrides (AGENT.md "ingest_config overrides per call")."""

    extraction_model: str | None = None  # None -> backend/global default cheap model
    edge_types: list[str] | None = None  # None -> track any relation the extractor emits
    importance_floor: float = 0.0  # extractions below this are held in queued_writes
    resolution_strategy: str = "embedding+string"  # match strategy identifier
    resolution_confidence_threshold: float = 0.75  # >= -> auto-resolve to the match
    ambiguity_floor: float = 0.5  # [floor, threshold) -> ambiguous; < floor -> treated as new
    string_match_min: float = 0.8  # surface-form ratio below this is ignored (embedding only)
    cross_reference_threshold: float = 0.85  # cross-ref pass: create edge when sim >= this
    on_ambiguous: str = "queue"  # "queue" (dev review) | "create" (new node anyway)
    embed_immediately: bool = True  # False -> defer embedding to a later batch pass
    cross_reference: bool = False  # AGENT.md: write-boundary cross-reference is opt-in

    def __post_init__(self) -> None:
        for name in ("resolution_confidence_threshold", "ambiguity_floor", "string_match_min"):
            if not 0.0 <= getattr(self, name) <= 1.0:
                raise ConfigError(f"{name} must be in [0, 1]")
        if self.ambiguity_floor > self.resolution_confidence_threshold:
            raise ConfigError("ambiguity_floor must be <= resolution_confidence_threshold")
        if self.on_ambiguous not in ("queue", "create"):
            raise ConfigError("on_ambiguous must be 'queue' or 'create'")


@dataclass
class RetrievalConfig:
    """Retrieval pipeline parameters (AGENT.md "Retrieval Pipeline" + "Retrieval Modes")."""

    mode: RetrievalMode = "flat"  # AGENT.md default: flat, no sub-index routing
    top_k: int = 10  # vector-similarity seed count
    hop_depth: int = 2  # graph traversal depth from seed nodes
    token_budget: int = 2048  # bounded context injection ceiling
    core_index_size_floor: int = 32  # hierarchical: min always-loaded high-importance nodes
    sub_index_strategy: SubIndexStrategy = "hybrid"  # AGENT.md default when hierarchical

    def __post_init__(self) -> None:
        if self.top_k < 1:
            raise ConfigError("RetrievalConfig.top_k must be >= 1")
        if self.hop_depth < 0:
            raise ConfigError("RetrievalConfig.hop_depth must be >= 0")
        if self.token_budget < 1:
            raise ConfigError("RetrievalConfig.token_budget must be >= 1")


@dataclass
class MemoryGraphConfig:
    """Top-level configuration for a :class:`nomem.MemoryGraph`."""

    user_id: str
    backend: str = "sqlite"  # sqlite | postgres | neo4j | BaseBackend instance name
    embedder: str = "nomic"  # nomic | openai | custom | BaseEmbedder instance name
    llm: str = "ollama"  # ollama | BaseLLM instance name (extraction model)
    decay: DecayMode = "combined"  # "time" | "access" | "combined" | None
    ingest_mode: IngestMode = "auto"  # "auto" | "manual"

    decay_config: DecayConfig = field(default_factory=DecayConfig)
    ingest_config: IngestConfig = field(default_factory=IngestConfig)
    retrieval_config: RetrievalConfig = field(default_factory=RetrievalConfig)

    backend_options: dict[str, Any] = field(default_factory=dict)
    embedder_options: dict[str, Any] = field(default_factory=dict)
    llm_options: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.user_id:
            raise ConfigError("MemoryGraphConfig.user_id is required")


def merge(base: Any, overrides: dict[str, Any] | None) -> Any:
    """Return a copy of ``base`` with ``overrides`` (a shallow dict) applied.

    Used for the per-call ``config=`` argument. Unknown keys raise
    :class:`ConfigError` so typos do not silently no-op.
    """
    if not overrides:
        return base
    known = set(base.__dataclass_fields__)
    unknown = set(overrides) - known
    if unknown:
        raise ConfigError(f"unknown config keys for {type(base).__name__}: {sorted(unknown)}")
    return replace(base, **overrides)
