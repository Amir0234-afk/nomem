"""PostgreSQL backend — production, pgvector.

Target: **Phase 2**. Requires the ``postgres`` extra (``asyncpg`` + ``pgvector``).
Vector similarity and traversal run in the database.

Every method is currently a stub.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..config import DecayConfig
from ..models import DecayResult, Edge, Node, SubGraph, Vector
from .base import BaseBackend

_PHASE = "PostgresBackend is a Phase 2 deliverable and is not implemented yet"


class PostgresBackend(BaseBackend):
    """Stub. See module docstring."""

    def __init__(self, *, dsn: str | None = None, **options: object) -> None:
        self.dsn = dsn
        self.options = options

    async def create_node(self, node: Node) -> Node:
        raise NotImplementedError(_PHASE)

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> Node:
        raise NotImplementedError(_PHASE)

    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node:
        raise NotImplementedError(_PHASE)

    async def get_node(self, node_id: str, as_of: datetime | None = None) -> Node | None:
        raise NotImplementedError(_PHASE)

    async def upsert_edge(self, edge: Edge) -> Edge:
        raise NotImplementedError(_PHASE)

    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge:
        raise NotImplementedError(_PHASE)

    async def vector_search(self, embedding: Vector, top_k: int) -> list[Node]:
        raise NotImplementedError(_PHASE)

    async def traverse(
        self, seed_ids: list[str], hops: int, as_of: datetime | None = None
    ) -> SubGraph:
        raise NotImplementedError(_PHASE)

    async def cross_reference(self, node: Node, threshold: float) -> list[tuple[Node, float]]:
        raise NotImplementedError(_PHASE)

    async def run_decay(self, config: DecayConfig) -> DecayResult:
        raise NotImplementedError(_PHASE)
