"""SQLite backend — local/dev, zero infra.

Stdlib ``sqlite3`` only. Embeddings are stored as packed-float BLOBs and vector
similarity is computed in Python (:mod:`nomem._vector`); fine at dev scale.

The blocking driver is serialized behind an :class:`asyncio.Lock` and every
query runs in a worker thread via :func:`asyncio.to_thread`, so the backend is
safe to share across concurrent ``await`` calls.

**Bi-temporal model (Phase 1 simplification).** Each node/edge id has one row.
``valid_from`` / ``valid_to`` bound its active window; ``created_at`` is the
write time. ``retire_*`` sets ``valid_to`` (+ ``superseded_by``) and never
deletes. ``as_of`` queries filter ``created_at <= as_of`` and an active window
containing ``as_of``. Per-field value history is not retained.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

from .._vector import cosine, pack, unpack
from ..config import DecayConfig
from ..exceptions import BackendError, EdgeNotFoundError, NodeNotFoundError
from ..models import DecayResult, Edge, Node, SubGraph, Vector
from .base import BaseBackend

_NODE_COLS = (
    "id",
    "user_id",
    "type",
    "label",
    "embedding",
    "importance",
    "access_count",
    "last_accessed_at",
    "created_at",
    "valid_from",
    "valid_to",
    "superseded_by",
    "resolution_source",
    "metadata",
)
_EDGE_COLS = (
    "id",
    "user_id",
    "source_id",
    "target_id",
    "relation",
    "weight",
    "created_at",
    "valid_from",
    "valid_to",
    "superseded_by",
    "metadata",
)

_UPDATABLE_NODE_FIELDS = frozenset(
    {
        "type",
        "label",
        "embedding",
        "importance",
        "access_count",
        "last_accessed_at",
        "resolution_source",
        "metadata",
        "valid_to",
        "superseded_by",
    }
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    type TEXT NOT NULL,
    label TEXT NOT NULL,
    embedding BLOB NOT NULL,
    importance REAL NOT NULL,
    access_count INTEGER NOT NULL,
    last_accessed_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    superseded_by TEXT,
    resolution_source TEXT NOT NULL,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nodes_user_active ON nodes(user_id, valid_to);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relation TEXT NOT NULL,
    weight REAL NOT NULL,
    created_at TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    superseded_by TEXT,
    metadata TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(user_id, source_id, valid_to);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(user_id, target_id, valid_to);
"""


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SQLiteBackend(BaseBackend):
    """Zero-infra backend backed by a single SQLite file (or ``:memory:``)."""

    def __init__(
        self,
        *,
        user_id: str,
        path: str = "nomem.sqlite",
        **options: object,
    ) -> None:
        if not user_id:
            raise BackendError("SQLiteBackend requires a user_id")
        self.user_id = user_id
        self.path = path
        self.options = options
        self._lock = asyncio.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn_guard = threading.Lock()
        with self._conn_guard:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._conn_guard:
            self._conn.close()

    # --- (de)serialization ------------------------------------------

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        return Node(
            id=row["id"],
            user_id=row["user_id"],
            type=row["type"],
            label=row["label"],
            embedding=unpack(row["embedding"]),
            importance=row["importance"],
            access_count=row["access_count"],
            last_accessed_at=_parse(row["last_accessed_at"]),  # type: ignore[arg-type]
            created_at=_parse(row["created_at"]),  # type: ignore[arg-type]
            valid_from=_parse(row["valid_from"]),  # type: ignore[arg-type]
            valid_to=_parse(row["valid_to"]),
            superseded_by=row["superseded_by"],
            resolution_source=row["resolution_source"],
            metadata=json.loads(row["metadata"]),
        )

    def _row_to_edge(self, row: sqlite3.Row) -> Edge:
        return Edge(
            id=row["id"],
            user_id=row["user_id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            relation=row["relation"],
            weight=row["weight"],
            created_at=_parse(row["created_at"]),  # type: ignore[arg-type]
            valid_from=_parse(row["valid_from"]),  # type: ignore[arg-type]
            valid_to=_parse(row["valid_to"]),
            superseded_by=row["superseded_by"],
            metadata=json.loads(row["metadata"]),
        )

    def _node_params(self, node: Node) -> tuple[Any, ...]:
        return (
            node.id,
            node.user_id,
            node.type,
            node.label,
            pack(node.embedding),
            float(node.importance),
            int(node.access_count),
            _iso(node.last_accessed_at),
            _iso(node.created_at),
            _iso(node.valid_from),
            _iso(node.valid_to) if node.valid_to else None,
            node.superseded_by,
            node.resolution_source,
            json.dumps(node.metadata),
        )

    def _edge_params(self, edge: Edge) -> tuple[Any, ...]:
        return (
            edge.id,
            edge.user_id,
            edge.source_id,
            edge.target_id,
            edge.relation,
            float(edge.weight),
            _iso(edge.created_at),
            _iso(edge.valid_from),
            _iso(edge.valid_to) if edge.valid_to else None,
            edge.superseded_by,
            json.dumps(edge.metadata),
        )

    # --- node CRUD -------------------------------------------------

    async def create_node(self, node: Node) -> Node:
        if node.user_id != self.user_id:
            raise BackendError(
                f"node.user_id {node.user_id!r} does not match backend user {self.user_id!r}"
            )
        async with self._lock:
            await asyncio.to_thread(self._create_node_sync, node)
        return node

    def _create_node_sync(self, node: Node) -> None:
        placeholders = ", ".join("?" for _ in _NODE_COLS)
        with self._conn_guard:
            try:
                self._conn.execute(
                    f"INSERT INTO nodes ({', '.join(_NODE_COLS)}) VALUES ({placeholders})",
                    self._node_params(node),
                )
            except sqlite3.IntegrityError as exc:
                raise BackendError(f"node {node.id} already exists") from exc
            self._conn.commit()

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> Node:
        async with self._lock:
            return await asyncio.to_thread(self._update_node_sync, node_id, updates)

    def _update_node_sync(self, node_id: str, updates: dict[str, Any]) -> Node:
        unknown = set(updates) - _UPDATABLE_NODE_FIELDS
        if unknown:
            raise BackendError(f"cannot update node fields: {sorted(unknown)}")
        with self._conn_guard:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ? AND user_id = ?", (node_id, self.user_id)
            ).fetchone()
            if row is None:
                raise NodeNotFoundError(node_id)
            node = self._row_to_node(row)
            for key, value in updates.items():
                setattr(node, key, value)
            assignments = ", ".join(f"{col} = ?" for col in _NODE_COLS[1:])
            self._conn.execute(
                f"UPDATE nodes SET {assignments} WHERE id = ?",
                (*self._node_params(node)[1:], node_id),
            )
            self._conn.commit()
        return node

    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node:
        async with self._lock:
            return await asyncio.to_thread(self._retire_node_sync, node_id, superseded_by)

    def _retire_node_sync(self, node_id: str, superseded_by: str | None) -> Node:
        with self._conn_guard:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ? AND user_id = ?", (node_id, self.user_id)
            ).fetchone()
            if row is None:
                raise NodeNotFoundError(node_id)
            node = self._row_to_node(row)
            if node.valid_to is None:
                node.valid_to = _now()
            if superseded_by is not None:
                node.superseded_by = superseded_by
            self._conn.execute(
                "UPDATE nodes SET valid_to = ?, superseded_by = ? WHERE id = ?",
                (_iso(node.valid_to), node.superseded_by, node_id),
            )
            self._conn.commit()
        return node

    async def get_node(self, node_id: str, as_of: datetime | None = None) -> Node | None:
        async with self._lock:
            return await asyncio.to_thread(self._get_node_sync, node_id, as_of)

    def _get_node_sync(self, node_id: str, as_of: datetime | None) -> Node | None:
        with self._conn_guard:
            row = self._conn.execute(
                "SELECT * FROM nodes WHERE id = ? AND user_id = ?", (node_id, self.user_id)
            ).fetchone()
        if row is None:
            return None
        node = self._row_to_node(row)
        if as_of is not None and not _active_at(node, as_of):
            return None
        return node

    # --- edge CRUD -----------------------------------------------

    async def upsert_edge(self, edge: Edge) -> Edge:
        if edge.user_id != self.user_id:
            raise BackendError(
                f"edge.user_id {edge.user_id!r} does not match backend user {self.user_id!r}"
            )
        async with self._lock:
            return await asyncio.to_thread(self._upsert_edge_sync, edge)

    def _upsert_edge_sync(self, edge: Edge) -> Edge:
        with self._conn_guard:
            existing = self._conn.execute(
                """SELECT * FROM edges
                   WHERE user_id = ? AND source_id = ? AND target_id = ?
                     AND relation = ? AND valid_to IS NULL""",
                (edge.user_id, edge.source_id, edge.target_id, edge.relation),
            ).fetchone()
            if existing is not None:
                merged = self._row_to_edge(existing)
                merged.weight += edge.weight
                merged.metadata = {**merged.metadata, **edge.metadata}
                self._conn.execute(
                    "UPDATE edges SET weight = ?, metadata = ? WHERE id = ?",
                    (merged.weight, json.dumps(merged.metadata), merged.id),
                )
                self._conn.commit()
                return merged
            placeholders = ", ".join("?" for _ in _EDGE_COLS)
            self._conn.execute(
                f"INSERT INTO edges ({', '.join(_EDGE_COLS)}) VALUES ({placeholders})",
                self._edge_params(edge),
            )
            self._conn.commit()
        return edge

    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge:
        async with self._lock:
            return await asyncio.to_thread(self._retire_edge_sync, edge_id, superseded_by)

    def _retire_edge_sync(self, edge_id: str, superseded_by: str | None) -> Edge:
        with self._conn_guard:
            row = self._conn.execute(
                "SELECT * FROM edges WHERE id = ? AND user_id = ?", (edge_id, self.user_id)
            ).fetchone()
            if row is None:
                raise EdgeNotFoundError(edge_id)
            edge = self._row_to_edge(row)
            if edge.valid_to is None:
                edge.valid_to = _now()
            if superseded_by is not None:
                edge.superseded_by = superseded_by
            self._conn.execute(
                "UPDATE edges SET valid_to = ?, superseded_by = ? WHERE id = ?",
                (_iso(edge.valid_to), edge.superseded_by, edge_id),
            )
            self._conn.commit()
        return edge

    # --- search + traversal -------------------------------------

    async def vector_search(self, embedding: Vector, top_k: int) -> list[Node]:
        async with self._lock:
            nodes = await asyncio.to_thread(self._active_nodes_sync)
        scored = sorted(
            ((cosine(embedding, n.embedding), n) for n in nodes if n.embedding),
            key=lambda pair: pair[0],
            reverse=True,
        )
        return [node for _, node in scored[: max(top_k, 0)]]

    def _active_nodes_sync(self) -> list[Node]:
        with self._conn_guard:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE user_id = ? AND valid_to IS NULL", (self.user_id,)
            ).fetchall()
        return [self._row_to_node(r) for r in rows]

    async def traverse(
        self, seed_ids: list[str], hops: int, as_of: datetime | None = None
    ) -> SubGraph:
        async with self._lock:
            return await asyncio.to_thread(self._traverse_sync, seed_ids, hops, as_of)

    def _traverse_sync(
        self, seed_ids: list[str], hops: int, as_of: datetime | None
    ) -> SubGraph:
        with self._conn_guard:
            node_rows = self._conn.execute(
                "SELECT * FROM nodes WHERE user_id = ?", (self.user_id,)
            ).fetchall()
            edge_rows = self._conn.execute(
                "SELECT * FROM edges WHERE user_id = ?", (self.user_id,)
            ).fetchall()

        nodes_by_id = {r["id"]: self._row_to_node(r) for r in node_rows}
        edges = [self._row_to_edge(r) for r in edge_rows]
        if as_of is not None:
            nodes_by_id = {k: v for k, v in nodes_by_id.items() if _active_at(v, as_of)}
            edges = [e for e in edges if _active_at(e, as_of)]
        else:
            nodes_by_id = {k: v for k, v in nodes_by_id.items() if v.valid_to is None}
            edges = [e for e in edges if e.valid_to is None]

        frontier = {sid for sid in seed_ids if sid in nodes_by_id}
        visited = set(frontier)
        kept_edges: dict[str, Edge] = {}
        for _ in range(max(hops, 0)):
            next_frontier: set[str] = set()
            for edge in edges:
                if edge.source_id in frontier and edge.target_id in nodes_by_id:
                    kept_edges[edge.id] = edge
                    if edge.target_id not in visited:
                        next_frontier.add(edge.target_id)
                if edge.target_id in frontier and edge.source_id in nodes_by_id:
                    kept_edges[edge.id] = edge
                    if edge.source_id not in visited:
                        next_frontier.add(edge.source_id)
            if not next_frontier:
                break
            visited |= next_frontier
            frontier = next_frontier

        return SubGraph(
            nodes=[nodes_by_id[nid] for nid in visited if nid in nodes_by_id],
            edges=list(kept_edges.values()),
            metadata={"hops": max(hops, 0), "seed_node_ids": list(seed_ids)},
        )

    async def cross_reference(self, node: Node, threshold: float) -> list[tuple[Node, float]]:
        async with self._lock:
            nodes = await asyncio.to_thread(self._active_nodes_sync)
        hits = [
            (other, cosine(node.embedding, other.embedding))
            for other in nodes
            if other.id != node.id and other.embedding
        ]
        return sorted(
            ((n, s) for n, s in hits if s >= threshold),
            key=lambda pair: pair[1],
            reverse=True,
        )

    async def run_decay(self, config: DecayConfig) -> DecayResult:
        raise NotImplementedError(
            "SQLiteBackend.run_decay is a Phase 2 deliverable and is not implemented yet"
        )


def _active_at(record: Node | Edge, as_of: datetime) -> bool:
    """True if ``record`` was recorded by ``as_of`` and its window contains it."""
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    if record.created_at > as_of or record.valid_from > as_of:
        return False
    return record.valid_to is None or record.valid_to > as_of
