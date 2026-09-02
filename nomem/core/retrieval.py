"""Retrieval: vector seed + graph traversal into a bounded structured result.

Pipeline (AGENT.md "Retrieval Pipeline")::

    query -> embed -> vector similarity (top-k seeds) -> N-hop traversal
          -> enforce token budget -> SubGraph { nodes, edges, metadata }

Flat mode only in Phase 1; hierarchical sub-index routing is Phase 2. nomem
returns the structured object — it never serializes to a prompt string.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .._vector import cosine
from ..backends.base import BaseBackend
from ..config import RetrievalConfig
from ..embedders.base import BaseEmbedder
from ..exceptions import RetrievalBudgetExceededError
from ..models import Node, SubGraph, Vector


def _est_tokens(text: str) -> int:
    """Rough token estimate (~4 chars/token)."""
    return max(1, len(text) // 4)


def _node_tokens(node: Node) -> int:
    return _est_tokens(node.label) + 8  # + small fixed overhead for structure


class Retriever:
    """Runs the retrieval pipeline for one query."""

    def __init__(
        self,
        *,
        backend: BaseBackend,
        embedder: BaseEmbedder,
        count_access: bool = True,
    ) -> None:
        self.backend = backend
        self.embedder = embedder
        self.count_access = count_access

    async def retrieve(
        self,
        query: str,
        config: RetrievalConfig,
        as_of: datetime | None = None,
    ) -> SubGraph:
        """Return a bounded :class:`SubGraph` relevant to ``query``."""
        if config.mode == "hierarchical":
            raise NotImplementedError(
                "hierarchical retrieval is a Phase 2 deliverable; use mode='flat'"
            )

        query_vec = await self._embed_query(query)
        seeds = await self._vector_seed(query_vec, config)
        subgraph = await self._traverse(seeds, config, as_of)

        subgraph = self._enforce_budget(subgraph, config)

        if as_of is None and self.count_access:
            await self._bump_access(subgraph.nodes)

        subgraph.metadata.update(
            retrieved_at=datetime.now(tz=UTC).isoformat(),
            seed_node_ids=[n.id for n in seeds],
            hop_depth=config.hop_depth,
            retrieval_mode="flat",
            decay_scores={n.id: n.importance for n in subgraph.nodes},
        )
        return subgraph

    # --- pipeline stages ------------------------------------------------

    async def _embed_query(self, query: str) -> Vector:
        return await self.embedder.embed(query)

    async def _vector_seed(self, query_vec: Vector, config: RetrievalConfig) -> list[Node]:
        return await self.backend.vector_search(query_vec, config.top_k)

    async def _traverse(
        self, seeds: list[Node], config: RetrievalConfig, as_of: datetime | None
    ) -> SubGraph:
        if not seeds:
            return SubGraph()
        return await self.backend.traverse([n.id for n in seeds], config.hop_depth, as_of)

    def _enforce_budget(self, subgraph: SubGraph, config: RetrievalConfig) -> SubGraph:
        budget = config.token_budget
        total = sum(_node_tokens(n) for n in subgraph.nodes)
        if total <= budget:
            return subgraph

        # Drop lowest-importance nodes until we fit; keep edges among survivors.
        ranked = sorted(subgraph.nodes, key=lambda n: n.importance, reverse=True)
        kept: list[Node] = []
        running = 0
        for node in ranked:
            cost = _node_tokens(node)
            if running + cost > budget:
                continue
            kept.append(node)
            running += cost

        if not kept:
            raise RetrievalBudgetExceededError(
                f"single node exceeds token_budget={budget}; raise the budget or override"
            )

        kept_ids = {n.id for n in kept}
        subgraph.nodes = kept
        subgraph.edges = [
            e for e in subgraph.edges if e.source_id in kept_ids and e.target_id in kept_ids
        ]
        subgraph.metadata["budget_trimmed"] = True
        return subgraph

    async def _bump_access(self, nodes: list[Node]) -> None:
        now = datetime.now(tz=UTC)
        for node in nodes:
            await self.backend.update_node(
                node.id,
                {"access_count": node.access_count + 1, "last_accessed_at": now},
            )
            node.access_count += 1
            node.last_accessed_at = now

    # kept for parity with the Phase 0 skeleton / future hierarchical mode
    def _score_relevance(self, query_vec: Vector, node: Node) -> float:
        return cosine(query_vec, node.embedding)


__all__ = ["Retriever"]
