"""SQLite backend — local/dev, zero infra.

Target: **Phase 1**. Uses the stdlib ``sqlite3`` module with vectors stored as
BLOB columns and similarity computed in Python (no extension required). This is
the default backend and the one the quickstart demo runs on.

Every method is currently a stub.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..config import DecayConfig
from ..models import DecayResult, Edge, Node, SubGraph, Vector
from .base import BaseBackend

_PHASE = "SQLiteBackend is a Phase 1 deliverable and is not implemented yet"


class SQLiteBackend(BaseBackend):
    """Stub. See module docstring."""

    def __init__(self, *, path: str = "nomem.sqlite", **options: object) -> None:
        self.path = path
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
