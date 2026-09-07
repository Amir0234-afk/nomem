"""Retrieval: vector seed + graph traversal into a bounded structured result.

Pipeline (AGENT.md "Retrieval Pipeline")::

    query -> embed -> vector similarity (top-k seeds) -> N-hop traversal
          -> enforce token budget -> SubGraph { nodes, edges, metadata }

**Flat** (default): seed via ``backend.vector_search`` over all active nodes.

**Hierarchical** (``mode="hierarchical"``): a small core index (top nodes by
importance, always seeded) plus a situation sub-index chosen by a router:

* ``deterministic`` — nodes whose ``metadata["context"]`` intersects the
  dev-supplied ``context`` tags
* ``semantic`` — the tag whose member centroid is closest to the query
* ``hybrid`` — deterministic, then semantic fallback (default)

nomem returns the structured object — it never serializes to a prompt string.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from .._vector import cosine, mean
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


def _node_context(node: Node) -> set[str]:
    raw = node.metadata.get("context", [])
    return set(raw) if isinstance(raw, list) else set()


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
        context: list[str] | None = None,
    ) -> SubGraph:
        """Return a bounded :class:`SubGraph` relevant to ``query``."""
        query_vec = await self._embed_query(query)

        sub_index_used: str | None = None
        if config.mode == "hierarchical":
            seeds, sub_index_used = await self._hierarchical_seed(
                query_vec, config, context, as_of
            )
        else:
            seeds = await self._vector_seed(query_vec, config)

        subgraph = await self._traverse(seeds, config, as_of)
        subgraph = self._enforce_budget(subgraph, config)

        if as_of is None and self.count_access:
            await self._bump_access(subgraph.nodes)

        subgraph.metadata.update(
            retrieved_at=datetime.now(tz=UTC).isoformat(),
            seed_node_ids=[n.id for n in seeds],
            hop_depth=config.hop_depth,
            retrieval_mode=config.mode,
            decay_scores={n.id: n.importance for n in subgraph.nodes},
        )
        if config.mode == "hierarchical":
            subgraph.metadata["sub_index_used"] = sub_index_used
        return subgraph

    # --- pipeline stages ------------------------------------------------

    async def _embed_query(self, query: str) -> Vector:
        return await self.embedder.embed(query)

    async def _vector_seed(self, query_vec: Vector, config: RetrievalConfig) -> list[Node]:
        return await self.backend.vector_search(query_vec, config.top_k)

    async def _hierarchical_seed(
        self,
        query_vec: Vector,
        config: RetrievalConfig,
        context: list[str] | None,
        as_of: datetime | None,
    ) -> tuple[list[Node], str | None]:
        nodes = await self.backend.list_nodes(active_only=True, as_of=as_of)
        if not nodes:
            return [], None

        core = sorted(nodes, key=lambda n: n.importance, reverse=True)[
            : max(config.core_index_size_floor, 0)
        ]
        sub, used = self._route_sub_index(nodes, query_vec, config, context)

        pool: dict[str, Node] = {n.id: n for n in core}
        for n in sub:
            pool.setdefault(n.id, n)

        ranked = sorted(
            (n for n in pool.values() if n.embedding),
            key=lambda n: cosine(query_vec, n.embedding),
            reverse=True,
        )
        return ranked[: config.top_k], used

    def _route_sub_index(
        self,
        nodes: list[Node],
        query_vec: Vector,
        config: RetrievalConfig,
        context: list[str] | None,
    ) -> tuple[list[Node], str | None]:
        strategy = config.sub_index_strategy

        if strategy in ("deterministic", "hybrid"):
            hit = self._deterministic(nodes, context)
            if hit is not None:
                return hit
            if strategy == "deterministic":
                return [], None

        if strategy in ("semantic", "hybrid"):
            return self._semantic(nodes, query_vec)

        return [], None

    @staticmethod
    def _deterministic(
        nodes: list[Node], context: list[str] | None
    ) -> tuple[list[Node], str | None] | None:
        if not context:
            return None
        wanted = set(context)
        matched = [n for n in nodes if _node_context(n) & wanted]
        if not matched:
            return None
        return matched, ",".join(sorted(wanted))

    @staticmethod
    def _semantic(nodes: list[Node], query_vec: Vector) -> tuple[list[Node], str | None]:
        tags: set[str] = set()
        for node in nodes:
            tags |= _node_context(node)
        if not tags:
            return [], None

        best_tag: str | None = None
        best_score = float("-inf")
        for tag in sorted(tags):
            members = [n.embedding for n in nodes if tag in _node_context(n) and n.embedding]
            centroid = mean(members)
            if not centroid:
                continue
            score = cosine(query_vec, centroid)
            if score > best_score:
                best_score, best_tag = score, tag

        if best_tag is None:
            return [], None
        return [n for n in nodes if best_tag in _node_context(n)], best_tag

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
        """Record that these nodes were returned.

        Issued concurrently: this is one write per returned node on the hot read
        path, and serially that is `top_k`-plus round trips against a networked
        backend. The bundled backends serialize internally where they must.
        """
        if not nodes:
            return
        now = datetime.now(tz=UTC)
        await asyncio.gather(
            *(
                self.backend.update_node(
                    node.id,
                    {"access_count": node.access_count + 1, "last_accessed_at": now},
                )
                for node in nodes
            )
        )
        for node in nodes:
            node.access_count += 1
            node.last_accessed_at = now


__all__ = ["Retriever"]
