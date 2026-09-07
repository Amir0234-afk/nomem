"""Canonical data models for nomem.

These dataclasses are the contract between the core pipelines, the backend
adapters, and application code. They carry no behavior — they are pure data.

Bi-temporal semantics (see AGENT.md "Data Schema"):

* ``valid_from`` / ``valid_to`` track when a fact was true *in the world*
  (event time). ``valid_to is None`` means the record is currently active.
* ``created_at`` tracks when nomem *wrote* the record (transaction time).
* Retirement sets ``valid_to`` and, when a replacement exists, ``superseded_by``.
  Hard deletes are never performed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

# --- Type aliases -----------------------------------------------------------

# Node ``type`` is open — these are the built-ins; devs may use any string.
NodeType = Literal["entity", "event", "emotion", "fact"] | str

ResolutionSource = Literal["llm", "string_match", "embedding", "manual"]

RetrievalMode = Literal["flat", "hierarchical"]

SubIndexStrategy = Literal["deterministic", "semantic", "hybrid"]

DecayMode = Literal["time", "access", "combined"] | None

IngestMode = Literal["auto", "manual"]

Vector = list[float]


# --- Graph records --------------------------------------------------------


@dataclass
class Node:
    """A single versioned node in a user's memory graph."""

    id: str
    user_id: str
    type: NodeType
    label: str
    embedding: Vector
    importance: float
    access_count: int
    last_accessed_at: datetime
    created_at: datetime
    valid_from: datetime
    valid_to: datetime | None = None
    superseded_by: str | None = None
    resolution_source: ResolutionSource = "llm"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Edge:
    """A single versioned relationship between two nodes."""

    id: str
    user_id: str
    source_id: str
    target_id: str
    relation: str
    weight: float
    created_at: datetime
    valid_from: datetime
    valid_to: datetime | None = None
    superseded_by: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SubGraph:
    """The structured result of a retrieval.

    By default ``nodes`` and ``edges`` contain only currently-active records.
    Pass ``as_of=`` to :meth:`nomem.MemoryGraph.retrieve` to get historical state.

    ``metadata`` carries at least: ``hop_depth``, ``retrieved_at``,
    ``seed_node_ids``, ``decay_scores``, ``retrieval_mode`` and, when
    hierarchical, ``sub_index_used``.
    """

    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# --- Pipeline intermediates ----------------------------------------------


@dataclass
class ExtractedEntity:
    """An entity surfaced by the extraction LLM call, before resolution."""

    label: str
    type: NodeType
    negated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExtractedRelation:
    """A relation surfaced by the extraction LLM call, before resolution.

    ``source_label`` / ``target_label`` reference :class:`ExtractedEntity`
    labels; they are bound to node ids after resolution.
    """

    source_label: str
    target_label: str
    relation: str
    weight: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ResolutionOutcome:
    """The result of resolving one :class:`ExtractedEntity` against the graph."""

    extracted: ExtractedEntity
    resolved_node_id: str | None
    confidence: float
    resolution_source: ResolutionSource
    ambiguous: bool = False
    candidates: list[tuple[str, float]] = field(default_factory=list)


# --- Receipts / results -------------------------------------------------


@dataclass
class IngestReceipt:
    """Returned by every ``ingest`` call — a full accounting of what changed."""

    nodes_created: list[str] = field(default_factory=list)
    nodes_updated: list[str] = field(default_factory=list)
    nodes_retired: list[str] = field(default_factory=list)
    edges_upserted: list[str] = field(default_factory=list)
    edges_cross_referenced: list[str] = field(default_factory=list)
    resolution_confidence: dict[str, float] = field(default_factory=dict)
    ambiguous_resolutions: list[ResolutionOutcome] = field(default_factory=list)
    #: Extractions held rather than written: below-threshold entities, ambiguous
    #: ones under ``on_ambiguous="queue"``, and — in ``ingest_mode="manual"`` —
    #: every intended operation, tagged in each entity's ``metadata`` with the
    #: **reserved keys** ``intended_op`` and ``resolved_node_id`` (public contract,
    #: not implementation detail — see ``docs/schema.md`` "Reserved metadata keys").
    #: Only present under ``ingest_mode="manual"``; absent in ``"auto"``, where the
    #: real CRUD ran instead of being reported.
    queued_writes: list[ExtractedEntity] = field(default_factory=list)
    #: Relations discarded because they fell outside ``IngestConfig.edge_types``.
    relations_dropped: int = 0


@dataclass
class DecayResult:
    """Returned by ``run_decay`` — the outcome of one scheduled decay pass."""

    ran_at: datetime
    nodes_scored: int = 0
    nodes_pruned: list[str] = field(default_factory=list)
    prune_candidates: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


@dataclass
class PurgeResult:
    """Returned by ``BaseBackend.purge_user`` — the one hard-delete path.

    Unreachable from :class:`nomem.MemoryGraph`; see
    :meth:`nomem.backends.base.BaseBackend.purge_user`.
    """

    nodes_deleted: int = 0
    edges_deleted: int = 0
