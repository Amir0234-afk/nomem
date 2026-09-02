"""Retrieval: vector seed + graph traversal into a bounded structured result.

Pipeline (AGENT.md "Retrieval Pipeline")::

    query -> embed
          -> core index lookup            (hierarchical mode only)
          -> vector similarity -> top-k seed nodes
          -> graph traversal -> N hops
          -> SubGraph { nodes, edges, metadata }

nomem returns the structured object; it never serializes to a prompt string —
that is the application's job. Skeleton: bodies raise ``NotImplementedError``.
"""

from __future__ import annotations

from datetime import datetime

from ..backends.base import BaseBackend
from ..config import RetrievalConfig
from ..embedders.base import BaseEmbedder
from ..models import Node, SubGraph, Vector

_PHASE = "retrieval is a Phase 1 deliverable and is not implemented yet"


class Retriever:
    """Runs the retrieval pipeline for one query."""

    def __init__(self, *, backend: BaseBackend, embedder: BaseEmbedder) -> None:
        self.backend = backend
        self.embedder = embedder

    async def retrieve(
        self,
        query: str,
        config: RetrievalConfig,
        as_of: datetime | None = None,
    ) -> SubGraph:
        """Return a bounded :class:`SubGraph` relevant to ``query``.

        Increments ``access_count`` on every returned node (Decay Model
        "increment semantics"). Raises
        :class:`~nomem.exceptions.RetrievalBudgetExceededError` if the result
        would exceed ``config.token_budget`` and no override is set.
        """
        raise NotImplementedError(_PHASE)

    # --- pipeline stages ------------------------------------------------

    async def _embed_query(self, query: str) -> Vector:
        raise NotImplementedError(_PHASE)

    async def _core_index_lookup(self, query_vec: Vector, config: RetrievalConfig) -> list[Node]:
        """Hierarchical mode: the small always-loaded high-importance node set."""
        raise NotImplementedError(_PHASE)

    async def _route_sub_index(
        self, query: str, query_vec: Vector, config: RetrievalConfig
    ) -> str | None:
        """Pick a situation sub-index (deterministic tag / semantic / hybrid)."""
        raise NotImplementedError(_PHASE)

    async def _vector_seed(self, query_vec: Vector, config: RetrievalConfig) -> list[Node]:
        raise NotImplementedError(_PHASE)

    async def _traverse(
        self, seeds: list[Node], config: RetrievalConfig, as_of: datetime | None
    ) -> SubGraph:
        raise NotImplementedError(_PHASE)

    def _enforce_budget(self, subgraph: SubGraph, config: RetrievalConfig) -> SubGraph:
        raise NotImplementedError(_PHASE)


__all__ = ["Retriever"]
