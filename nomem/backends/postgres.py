"""PostgreSQL backend — production, pgvector.

Requires the ``postgres`` extra (``asyncpg`` + ``pgvector``). Expects its own
database/schema; run the bundled ``docker-compose.yml`` for local testing.

Vector similarity uses the pgvector ``<=>`` (cosine distance) operator with an
IVFFlat index. Traversal and decay pull the user's rows and run the shared
in-Python helpers (:mod:`nomem.backends._common`, :func:`nomem.core.decay.score_node`)
so results match the SQLite backend exactly.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config import DecayConfig
from ..exceptions import BackendError, EdgeNotFoundError, NodeNotFoundError
from ..models import DecayResult, Edge, Node, PurgeResult, SubGraph, Vector
from ._common import active_at, bfs_subgraph, now_utc
from .base import BaseBackend

if TYPE_CHECKING:
    import asyncpg

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


def _to_vector(value: Any) -> Vector:
    """Normalize whatever pgvector hands back (Vector / ndarray / list / None)."""
    if value is None:
        return []
    if hasattr(value, "to_list"):
        return [float(x) for x in value.to_list()]
    return [float(x) for x in value]


def _schema_sql(dims: int) -> str:
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS nodes (
        id                TEXT PRIMARY KEY,
        user_id           TEXT NOT NULL,
        type              TEXT NOT NULL,
        label             TEXT NOT NULL,
        embedding         vector({dims}),
        importance        DOUBLE PRECISION NOT NULL,
        access_count      INTEGER NOT NULL,
        last_accessed_at  TIMESTAMPTZ NOT NULL,
        created_at        TIMESTAMPTZ NOT NULL,
        valid_from        TIMESTAMPTZ NOT NULL,
        valid_to          TIMESTAMPTZ,
        superseded_by     TEXT,
        resolution_source TEXT NOT NULL,
        metadata          JSONB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_nodes_user_active ON nodes (user_id, valid_to);

    CREATE TABLE IF NOT EXISTS edges (
        id            TEXT PRIMARY KEY,
        user_id       TEXT NOT NULL,
        source_id     TEXT NOT NULL,
        target_id     TEXT NOT NULL,
        relation      TEXT NOT NULL,
        weight        DOUBLE PRECISION NOT NULL,
        created_at    TIMESTAMPTZ NOT NULL,
        valid_from    TIMESTAMPTZ NOT NULL,
        valid_to      TIMESTAMPTZ,
        superseded_by TEXT,
        metadata      JSONB NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_edges_source ON edges (user_id, source_id, valid_to);
    CREATE INDEX IF NOT EXISTS idx_edges_target ON edges (user_id, target_id, valid_to);
    """


class PostgresBackend(BaseBackend):
    """pgvector-backed production backend."""

    def __init__(
        self,
        *,
        user_id: str,
        dsn: str | None = None,
        vector_dimensions: int = 768,
        **options: Any,
    ) -> None:
        if not user_id:
            raise BackendError("PostgresBackend requires a user_id")
        if not dsn:
            raise BackendError("PostgresBackend requires a dsn")
        self.user_id = user_id
        self.dsn = dsn
        self.dimensions = int(vector_dimensions)
        self.options = options
        self._pool: asyncpg.Pool | None = None
        self._ready = asyncio.Lock()

    # --- connection lifecycle ------------------------------------------

    async def _pool_or_init(self) -> asyncpg.Pool:
        async with self._ready:
            if self._pool is None:
                self._pool = await self._build_pool()
            return self._pool

    async def _build_pool(self) -> asyncpg.Pool:
        try:
            import asyncpg
            from pgvector.asyncpg import register_vector
        except ImportError as exc:  # pragma: no cover - covered by the postgres extra
            raise BackendError(
                "PostgresBackend needs the 'postgres' extra: pip install 'nomem[postgres]'"
            ) from exc

        # Bootstrap: the vector type must exist before register_vector runs.
        bootstrap = await asyncpg.connect(self.dsn)
        try:
            await bootstrap.execute(_schema_sql(self.dimensions))
            existing_dim = await bootstrap.fetchval(
                """SELECT atttypmod FROM pg_attribute
                   WHERE attrelid = 'nodes'::regclass AND attname = 'embedding'"""
            )
            if existing_dim not in (None, -1) and existing_dim != self.dimensions:
                raise BackendError(
                    f"the 'nodes' table already exists with vector({existing_dim}), but this "
                    f"backend/embedder expects vector({self.dimensions}). Use a dedicated "
                    f"database per embedding model, or migrate the column."
                )
        finally:
            await bootstrap.close()

        async def _init(conn: asyncpg.Connection) -> None:
            await conn.set_type_codec(
                "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
            )
            await register_vector(conn)

        return await asyncpg.create_pool(self.dsn, init=_init, min_size=1, max_size=4)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    # --- (de)serialization -------------------------------------------

    def _row_to_node(self, row: asyncpg.Record) -> Node:
        return Node(
            id=row["id"],
            user_id=row["user_id"],
            type=row["type"],
            label=row["label"],
            embedding=_to_vector(row["embedding"]),
            importance=row["importance"],
            access_count=row["access_count"],
            last_accessed_at=row["last_accessed_at"],
            created_at=row["created_at"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            superseded_by=row["superseded_by"],
            resolution_source=row["resolution_source"],
            metadata=row["metadata"] or {},
        )

    def _row_to_edge(self, row: asyncpg.Record) -> Edge:
        return Edge(
            id=row["id"],
            user_id=row["user_id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            relation=row["relation"],
            weight=row["weight"],
            created_at=row["created_at"],
            valid_from=row["valid_from"],
            valid_to=row["valid_to"],
            superseded_by=row["superseded_by"],
            metadata=row["metadata"] or {},
        )

    def _node_values(self, node: Node) -> list[Any]:
        return [
            node.id,
            node.user_id,
            node.type,
            node.label,
            node.embedding or None,
            float(node.importance),
            int(node.access_count),
            node.last_accessed_at,
            node.created_at,
            node.valid_from,
            node.valid_to,
            node.superseded_by,
            node.resolution_source,
            node.metadata,
        ]

    def _edge_values(self, edge: Edge) -> list[Any]:
        return [
            edge.id,
            edge.user_id,
            edge.source_id,
            edge.target_id,
            edge.relation,
            float(edge.weight),
            edge.created_at,
            edge.valid_from,
            edge.valid_to,
            edge.superseded_by,
            edge.metadata,
        ]

    # --- node CRUD --------------------------------------------------

    async def create_node(self, node: Node) -> Node:
        if node.user_id != self.user_id:
            raise BackendError(
                f"node.user_id {node.user_id!r} does not match backend user {self.user_id!r}"
            )
        import asyncpg

        pool = await self._pool_or_init()
        cols = ", ".join(_NODE_COLS)
        params = ", ".join(f"${i}" for i in range(1, len(_NODE_COLS) + 1))
        try:
            await pool.execute(
                f"INSERT INTO nodes ({cols}) VALUES ({params})", *self._node_values(node)
            )
        except asyncpg.UniqueViolationError as exc:
            raise BackendError(f"node {node.id} already exists") from exc
        return node

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> Node:
        unknown = set(updates) - _UPDATABLE_NODE_FIELDS
        if unknown:
            raise BackendError(f"cannot update node fields: {sorted(unknown)}")
        pool = await self._pool_or_init()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM nodes WHERE id = $1 AND user_id = $2 FOR UPDATE",
                node_id,
                self.user_id,
            )
            if row is None:
                raise NodeNotFoundError(node_id)
            node = self._row_to_node(row)
            for key, value in updates.items():
                setattr(node, key, value)
            assignments = ", ".join(
                f"{col} = ${i}" for i, col in enumerate(_NODE_COLS[1:], start=2)
            )
            await conn.execute(
                f"UPDATE nodes SET {assignments} WHERE id = $1",
                node_id,
                *self._node_values(node)[1:],
            )
        return node

    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node:
        pool = await self._pool_or_init()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM nodes WHERE id = $1 AND user_id = $2 FOR UPDATE",
                node_id,
                self.user_id,
            )
            if row is None:
                raise NodeNotFoundError(node_id)
            node = self._row_to_node(row)
            if node.valid_to is None:
                node.valid_to = now_utc()
            if superseded_by is not None:
                node.superseded_by = superseded_by
            await conn.execute(
                "UPDATE nodes SET valid_to = $2, superseded_by = $3 WHERE id = $1",
                node_id,
                node.valid_to,
                node.superseded_by,
            )
        return node

    async def get_node(self, node_id: str, as_of: datetime | None = None) -> Node | None:
        pool = await self._pool_or_init()
        row = await pool.fetchrow(
            "SELECT * FROM nodes WHERE id = $1 AND user_id = $2", node_id, self.user_id
        )
        if row is None:
            return None
        node = self._row_to_node(row)
        if as_of is not None and not active_at(node, as_of):
            return None
        return node

    # --- edge CRUD ------------------------------------------------

    async def upsert_edge(self, edge: Edge) -> Edge:
        if edge.user_id != self.user_id:
            raise BackendError(
                f"edge.user_id {edge.user_id!r} does not match backend user {self.user_id!r}"
            )
        pool = await self._pool_or_init()
        async with pool.acquire() as conn, conn.transaction():
            existing = await conn.fetchrow(
                """SELECT * FROM edges
                   WHERE user_id = $1 AND source_id = $2 AND target_id = $3
                     AND relation = $4 AND valid_to IS NULL
                   FOR UPDATE""",
                edge.user_id,
                edge.source_id,
                edge.target_id,
                edge.relation,
            )
            if existing is not None:
                merged = self._row_to_edge(existing)
                merged.weight += edge.weight
                merged.metadata = {**merged.metadata, **edge.metadata}
                await conn.execute(
                    "UPDATE edges SET weight = $2, metadata = $3 WHERE id = $1",
                    merged.id,
                    merged.weight,
                    merged.metadata,
                )
                return merged
            cols = ", ".join(_EDGE_COLS)
            params = ", ".join(f"${i}" for i in range(1, len(_EDGE_COLS) + 1))
            await conn.execute(
                f"INSERT INTO edges ({cols}) VALUES ({params})", *self._edge_values(edge)
            )
        return edge

    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge:
        pool = await self._pool_or_init()
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM edges WHERE id = $1 AND user_id = $2 FOR UPDATE",
                edge_id,
                self.user_id,
            )
            if row is None:
                raise EdgeNotFoundError(edge_id)
            edge = self._row_to_edge(row)
            if edge.valid_to is None:
                edge.valid_to = now_utc()
            if superseded_by is not None:
                edge.superseded_by = superseded_by
            await conn.execute(
                "UPDATE edges SET valid_to = $2, superseded_by = $3 WHERE id = $1",
                edge_id,
                edge.valid_to,
                edge.superseded_by,
            )
        return edge

    async def get_edge(self, edge_id: str, as_of: datetime | None = None) -> Edge | None:
        pool = await self._pool_or_init()
        row = await pool.fetchrow(
            "SELECT * FROM edges WHERE id = $1 AND user_id = $2", edge_id, self.user_id
        )
        if row is None:
            return None
        edge = self._row_to_edge(row)
        if as_of is not None and not active_at(edge, as_of):
            return None
        return edge

    async def list_edges(
        self,
        *,
        active_only: bool = True,
        as_of: datetime | None = None,
    ) -> list[Edge]:
        pool = await self._pool_or_init()
        rows = await pool.fetch("SELECT * FROM edges WHERE user_id = $1", self.user_id)
        edges = [self._row_to_edge(r) for r in rows]
        if as_of is not None:
            return [e for e in edges if active_at(e, as_of)]
        if active_only:
            return [e for e in edges if e.valid_to is None]
        return edges

    # --- search + traversal -------------------------------------

    async def vector_search(self, embedding: Vector, top_k: int) -> list[Node]:
        if not embedding or top_k <= 0:
            return []
        pool = await self._pool_or_init()
        rows = await pool.fetch(
            """SELECT * FROM nodes
               WHERE user_id = $1 AND valid_to IS NULL AND embedding IS NOT NULL
               ORDER BY embedding <=> $2 LIMIT $3""",
            self.user_id,
            embedding,
            top_k,
        )
        return [self._row_to_node(r) for r in rows]

    async def list_nodes(
        self,
        *,
        active_only: bool = True,
        context: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> list[Node]:
        pool = await self._pool_or_init()
        rows = await pool.fetch("SELECT * FROM nodes WHERE user_id = $1", self.user_id)
        nodes = [self._row_to_node(r) for r in rows]
        if as_of is not None:
            nodes = [n for n in nodes if active_at(n, as_of)]
        elif active_only:
            nodes = [n for n in nodes if n.valid_to is None]
        if context:
            wanted = set(context)
            nodes = [n for n in nodes if wanted & set(n.metadata.get("context", []))]
        return nodes

    async def traverse(
        self, seed_ids: list[str], hops: int, as_of: datetime | None = None
    ) -> SubGraph:
        pool = await self._pool_or_init()
        node_rows = await pool.fetch("SELECT * FROM nodes WHERE user_id = $1", self.user_id)
        edge_rows = await pool.fetch("SELECT * FROM edges WHERE user_id = $1", self.user_id)

        nodes_by_id = {r["id"]: self._row_to_node(r) for r in node_rows}
        edges = [self._row_to_edge(r) for r in edge_rows]
        if as_of is not None:
            nodes_by_id = {k: v for k, v in nodes_by_id.items() if active_at(v, as_of)}
            edges = [e for e in edges if active_at(e, as_of)]
        else:
            nodes_by_id = {k: v for k, v in nodes_by_id.items() if v.valid_to is None}
            edges = [e for e in edges if e.valid_to is None]
        return bfs_subgraph(nodes_by_id, edges, seed_ids, hops)

    async def cross_reference(self, node: Node, threshold: float) -> list[tuple[Node, float]]:
        from .._vector import cosine

        others = await self.list_nodes(active_only=True)
        hits = [
            (other, cosine(node.embedding, other.embedding))
            for other in others
            if other.id != node.id and other.embedding
        ]
        return sorted(
            ((n, s) for n, s in hits if s >= threshold),
            key=lambda pair: pair[1],
            reverse=True,
        )

    async def run_decay(self, config: DecayConfig) -> DecayResult:
        from ..core.decay import score_node

        pool = await self._pool_or_init()
        now = now_utc()
        async with pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT * FROM nodes WHERE user_id = $1 AND valid_to IS NULL", self.user_id
            )
            nodes = [self._row_to_node(r) for r in rows]

            scores: dict[str, float] = {}
            prune_candidates: list[str] = []
            for node in nodes:
                new_score = score_node(node, config, now)
                scores[node.id] = new_score
                if new_score < config.importance_floor:
                    prune_candidates.append(node.id)
            await conn.executemany(
                "UPDATE nodes SET importance = $2 WHERE id = $1",
                [(nid, s) for nid, s in scores.items()],
            )

            pruned: list[str] = []
            if config.pruning and prune_candidates:
                await conn.executemany(
                    "UPDATE nodes SET valid_to = $2 WHERE id = $1 AND valid_to IS NULL",
                    [(nid, now) for nid in prune_candidates],
                )
                pruned = list(prune_candidates)

        return DecayResult(
            ran_at=now,
            nodes_scored=len(nodes),
            nodes_pruned=pruned,
            prune_candidates=prune_candidates,
            scores=scores,
        )

    # --- the one hard-delete path -------------------------------

    async def purge_user(self, user_id: str) -> PurgeResult:
        """Hard-delete every row for ``user_id``. See :meth:`BaseBackend.purge_user`."""
        if user_id != self.user_id:
            raise BackendError(
                f"purge_user {user_id!r} does not match backend user {self.user_id!r}"
            )
        pool = await self._pool_or_init()
        async with pool.acquire() as conn, conn.transaction():
            edges = await conn.fetch("DELETE FROM edges WHERE user_id = $1 RETURNING id", user_id)
            nodes = await conn.fetch("DELETE FROM nodes WHERE user_id = $1 RETURNING id", user_id)
        return PurgeResult(nodes_deleted=len(nodes), edges_deleted=len(edges))
