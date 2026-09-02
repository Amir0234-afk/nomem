"""Node / edge CRUD orchestration.

This is the internal layer that turns resolution outcomes into real graph
operations against a :class:`~nomem.backends.base.BaseBackend`, enforcing the
bi-temporal invariants (AGENT.md "Explicit CRUD semantics"):

    New entity     -> CREATE node, embed
    Known entity   -> UPDATE node (weight++, last_accessed = now)
    Negated entity -> RETIRE node (valid_to = now, supersession record)

The public ``MemoryGraph`` class lives in ``nomem/graph.py`` and delegates here.
Skeleton: bodies raise ``NotImplementedError``.
"""

from __future__ import annotations

from typing import Any

from ..backends.base import BaseBackend
from ..config import IngestConfig
from ..embedders.base import BaseEmbedder
from ..models import Edge, ExtractedRelation, IngestReceipt, Node, ResolutionOutcome

_PHASE = "graph CRUD orchestration is a Phase 1 deliverable and is not implemented yet"


class GraphCRUD:
    """Applies CRUD operations to the backend; enforces bi-temporal rules."""

    def __init__(self, *, backend: BaseBackend, embedder: BaseEmbedder) -> None:
        self.backend = backend
        self.embedder = embedder

    async def create_node(self, node: Node) -> Node:
        raise NotImplementedError(_PHASE)

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> Node:
        raise NotImplementedError(_PHASE)

    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node:
        """Retire via ``valid_to`` + supersession record. Never a hard delete."""
        raise NotImplementedError(_PHASE)

    async def upsert_edge(self, edge: Edge) -> Edge:
        raise NotImplementedError(_PHASE)

    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge:
        raise NotImplementedError(_PHASE)

    async def apply_resolutions(
        self,
        resolutions: list[ResolutionOutcome],
        relations: list[ExtractedRelation],
        config: IngestConfig,
    ) -> IngestReceipt:
        """Map resolution outcomes to CREATE / UPDATE / RETIRE, then upsert edges.

        Ambiguous outcomes and below-floor extractions are recorded in the
        returned :class:`IngestReceipt` (``ambiguous_resolutions`` /
        ``queued_writes``) rather than acted on.
        """
        raise NotImplementedError(_PHASE)

    async def cross_reference_pass(
        self,
        nodes: list[Node],
        threshold: float,
    ) -> list[str]:
        """Post-CRUD embedding-similarity pass; create candidate edges >= threshold.

        Opt-in (``ingest_config.cross_reference``). Returns the ids of edges
        created by this pass.
        """
        raise NotImplementedError(_PHASE)


__all__ = ["GraphCRUD"]
