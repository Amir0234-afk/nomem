"""Node / edge CRUD orchestration.

Turns resolution outcomes into real graph operations against a
:class:`~nomem.backends.base.BaseBackend`, enforcing the bi-temporal invariants
(AGENT.md "Explicit CRUD semantics"):

    New entity     -> CREATE node, embed
    Known entity   -> UPDATE node (last_accessed = now, merge metadata)
    Negated entity -> RETIRE node (valid_to = now, superseded_by)

The public ``MemoryGraph`` class lives in ``nomem/graph.py`` and delegates here.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from ..backends.base import BaseBackend
from ..config import IngestConfig
from ..embedders.base import BaseEmbedder
from ..models import (
    Edge,
    ExtractedEntity,
    ExtractedRelation,
    IngestReceipt,
    Node,
    ResolutionOutcome,
)

#: Importance assigned to a freshly created node. Decay owns it from here on.
INITIAL_IMPORTANCE = 1.0
#: Relation used for edges created by the write-boundary cross-reference pass.
CROSS_REF_RELATION = "related_to"


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _new_id() -> str:
    return str(uuid.uuid4())


def plan_receipt(
    resolutions: list[ResolutionOutcome], config: IngestConfig
) -> IngestReceipt:
    """Classify resolutions into intended operations without touching the backend.

    Backs ``ingest_mode="manual"`` (a dry run). Every entity lands in
    ``queued_writes`` carrying its intent in ``metadata["intended_op"]`` —
    ``create`` / ``update`` / ``retire`` / ``queue`` / ``noop`` — plus
    ``resolved_node_id`` where one was matched. The entities are copies, so the
    caller's extraction results are never mutated.
    """
    receipt = IngestReceipt()
    for outcome in resolutions:
        entity = outcome.extracted
        if outcome.resolved_node_id is not None:
            receipt.resolution_confidence[outcome.resolved_node_id] = outcome.confidence
            op = "retire" if entity.negated else "update"
        elif outcome.ambiguous:
            receipt.ambiguous_resolutions.append(outcome)
            op = "create" if config.on_ambiguous == "create" and not entity.negated else "queue"
        elif entity.negated:
            op = "noop"  # nothing on record to retire
        elif config.importance_floor > INITIAL_IMPORTANCE:
            op = "queue"
        else:
            op = "create"
        receipt.queued_writes.append(
            replace(
                entity,
                metadata={
                    **entity.metadata,
                    "intended_op": op,
                    "resolved_node_id": outcome.resolved_node_id,
                },
            )
        )
    return receipt


class GraphCRUD:
    """Applies CRUD operations to the backend; enforces bi-temporal rules."""

    def __init__(
        self,
        *,
        backend: BaseBackend,
        embedder: BaseEmbedder,
        user_id: str,
        count_on_ingest: bool = False,
    ) -> None:
        self.backend = backend
        self.embedder = embedder
        self.user_id = user_id
        self.count_on_ingest = count_on_ingest

    async def create_node(self, node: Node) -> Node:
        return await self.backend.create_node(node)

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> Node:
        return await self.backend.update_node(node_id, updates)

    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node:
        """Retire via ``valid_to`` + supersession record. Never a hard delete."""
        return await self.backend.retire_node(node_id, superseded_by)

    async def upsert_edge(self, edge: Edge) -> Edge:
        return await self.backend.upsert_edge(edge)

    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge:
        return await self.backend.retire_edge(edge_id, superseded_by)

    # --- ingest orchestration --------------------------------------

    async def apply_resolutions(
        self,
        resolutions: list[ResolutionOutcome],
        relations: list[ExtractedRelation],
        config: IngestConfig,
        context: list[str] | None = None,
    ) -> IngestReceipt:
        """Map resolution outcomes to CREATE / UPDATE / RETIRE, then upsert edges.

        ``context`` tags every node created or updated this turn (stored under
        ``metadata["context"]``) so hierarchical retrieval can route to it.
        """
        receipt = IngestReceipt()
        label_to_id: dict[str, str] = {}
        tags = sorted(set(context)) if context else []

        # Pass 1: classify, and batch-embed everything that needs a new node.
        to_create: list[ResolutionOutcome] = []
        for outcome in resolutions:
            entity = outcome.extracted
            if outcome.resolved_node_id is not None:
                receipt.resolution_confidence[outcome.resolved_node_id] = outcome.confidence
                if entity.negated:
                    await self.retire_node(outcome.resolved_node_id)
                    receipt.nodes_retired.append(outcome.resolved_node_id)
                else:
                    await self._touch(outcome.resolved_node_id, tags)
                    receipt.nodes_updated.append(outcome.resolved_node_id)
                    label_to_id[entity.label] = outcome.resolved_node_id
            elif outcome.ambiguous:
                receipt.ambiguous_resolutions.append(outcome)
                if config.on_ambiguous == "create" and not entity.negated:
                    to_create.append(outcome)
                else:
                    receipt.queued_writes.append(entity)
            elif entity.negated:
                continue  # nothing on record to retire
            elif config.importance_floor > INITIAL_IMPORTANCE:
                receipt.queued_writes.append(entity)
            else:
                to_create.append(outcome)

        if to_create:
            vectors = await self.embedder.embed_batch([o.extracted.label for o in to_create])
            for outcome, vector in zip(to_create, vectors, strict=True):
                node = self._build_node(
                    outcome.extracted, vector, outcome.resolution_source, tags
                )
                await self.create_node(node)
                receipt.nodes_created.append(node.id)
                receipt.resolution_confidence[node.id] = outcome.confidence
                label_to_id[outcome.extracted.label] = node.id

        # Pass 2: relations whose endpoints both resolved this turn.
        for relation in relations:
            source_id = label_to_id.get(relation.source_label)
            target_id = label_to_id.get(relation.target_label)
            if source_id is None or target_id is None:
                continue
            edge = self._build_edge(source_id, target_id, relation)
            stored = await self.upsert_edge(edge)
            receipt.edges_upserted.append(stored.id)

        return receipt

    async def cross_reference_pass(self, node_ids: list[str], threshold: float) -> list[str]:
        """Post-CRUD embedding-similarity pass; create candidate edges >= threshold.

        Opt-in (``ingest_config.cross_reference``). Returns the ids of edges
        created by this pass.
        """
        created: list[str] = []
        seen_pairs: set[frozenset[str]] = set()
        for node_id in node_ids:
            node = await self.backend.get_node(node_id)
            if node is None or not node.embedding:
                continue
            for other, score in await self.backend.cross_reference(node, threshold):
                pair = frozenset({node_id, other.id})
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                edge = self._build_edge(
                    node_id,
                    other.id,
                    ExtractedRelation(
                        source_label=node.label,
                        target_label=other.label,
                        relation=CROSS_REF_RELATION,
                        weight=score,
                    ),
                    metadata={"cross_referenced": True, "similarity": score},
                )
                stored = await self.upsert_edge(edge)
                created.append(stored.id)
        return created

    # --- helpers --------------------------------------------------

    async def _touch(self, node_id: str, context: list[str]) -> Node:
        updates: dict[str, Any] = {"last_accessed_at": _now()}
        if self.count_on_ingest or context:
            current = await self.backend.get_node(node_id)
            if current is not None:
                if self.count_on_ingest:
                    updates["access_count"] = current.access_count + 1
                if context:
                    merged = sorted(set(current.metadata.get("context", [])) | set(context))
                    updates["metadata"] = {**current.metadata, "context": merged}
        return await self.backend.update_node(node_id, updates)

    def _build_node(
        self,
        entity: ExtractedEntity,
        embedding: list[float],
        resolution_source: str,
        context: list[str] | None = None,
    ) -> Node:
        now = _now()
        metadata = dict(entity.metadata)
        if context:
            metadata["context"] = sorted(set(context))
        return Node(
            id=_new_id(),
            user_id=self.user_id,
            type=entity.type,
            label=entity.label,
            embedding=embedding,
            importance=INITIAL_IMPORTANCE,
            access_count=0,
            last_accessed_at=now,
            created_at=now,
            valid_from=now,
            resolution_source=resolution_source,  # type: ignore[arg-type]
            metadata=metadata,
        )

    def _build_edge(
        self,
        source_id: str,
        target_id: str,
        relation: ExtractedRelation,
        metadata: dict[str, Any] | None = None,
    ) -> Edge:
        now = _now()
        return Edge(
            id=_new_id(),
            user_id=self.user_id,
            source_id=source_id,
            target_id=target_id,
            relation=relation.relation,
            weight=relation.weight,
            created_at=now,
            valid_from=now,
            metadata={**dict(relation.metadata), **(metadata or {})},
        )


__all__ = ["CROSS_REF_RELATION", "INITIAL_IMPORTANCE", "GraphCRUD", "plan_receipt"]
