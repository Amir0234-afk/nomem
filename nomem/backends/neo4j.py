"""Neo4j backend — power users.

Requires the ``neo4j`` extra. Nodes are ``(:Node {...})``; edges are
``[:EDGE {relation, ...}]`` relationships (the relation verb is a property so it
need not be a static type). Vector search uses Neo4j's native vector index;
traversal, decay, and cross-reference pull the user's rows and run the shared
Python helpers so results match the other backends exactly.

``metadata`` is stored as a JSON string (Neo4j properties can't nest); timestamps
are ISO-8601 strings, as in the SQLite backend.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ..config import DecayConfig
from ..exceptions import BackendError, EdgeNotFoundError, NodeNotFoundError
from ..models import DecayResult, Edge, Node, PurgeResult, SubGraph, Vector
from ._common import active_at, bfs_subgraph, iso, now_utc, parse_ts
from .base import BaseBackend

if TYPE_CHECKING:
    import neo4j

_INDEX = "nomem_node_embedding"

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


class Neo4jBackend(BaseBackend):
    """Native-graph backend on Neo4j 5 (bolt driver + vector index)."""

    def __init__(
        self,
        *,
        user_id: str,
        uri: str | None = None,
        auth: tuple[str, str] | list[str] | None = None,
        vector_dimensions: int = 768,
        database: str = "neo4j",
        **options: Any,
    ) -> None:
        if not user_id:
            raise BackendError("Neo4jBackend requires a user_id")
        if not uri:
            raise BackendError("Neo4jBackend requires a uri")
        self.user_id = user_id
        self.uri = uri
        self.auth: tuple[str, str] | None = None
        if auth is not None:
            creds = tuple(auth)
            if len(creds) != 2:
                raise BackendError("Neo4jBackend auth must be (username, password)")
            self.auth = (creds[0], creds[1])
        self.dimensions = int(vector_dimensions)
        self.database = database
        self.options = options
        self._driver: neo4j.AsyncDriver | None = None
        self._ready = asyncio.Lock()

    # --- connection lifecycle --------------------------------------

    async def _driver_or_init(self) -> neo4j.AsyncDriver:
        async with self._ready:
            if self._driver is None:
                self._driver = await self._build_driver()
            return self._driver

    async def _build_driver(self) -> neo4j.AsyncDriver:
        try:
            import neo4j
        except ImportError as exc:  # pragma: no cover - covered by the neo4j extra
            raise BackendError(
                "Neo4jBackend needs the 'neo4j' extra: pip install 'nomem[neo4j]'"
            ) from exc

        driver = neo4j.AsyncGraphDatabase.driver(self.uri, auth=self.auth)
        await driver.verify_connectivity()
        async with driver.session(database=self.database) as session:
            await session.run(
                "CREATE CONSTRAINT nomem_node_id IF NOT EXISTS "
                "FOR (n:Node) REQUIRE n.id IS UNIQUE"
            )
            await session.run(
                "CREATE CONSTRAINT nomem_edge_id IF NOT EXISTS "
                "FOR ()-[e:EDGE]-() REQUIRE e.id IS UNIQUE"
            )
            await self._check_dimensions(session)
            import neo4j.exceptions

            try:
                await session.run(
                    f"CREATE VECTOR INDEX {_INDEX} IF NOT EXISTS FOR (n:Node) ON n.embedding "
                    "OPTIONS {indexConfig: {"
                    "`vector.dimensions`: $dims, `vector.similarity_function`: 'cosine'}}",
                    dims=self.dimensions,
                )
            except neo4j.exceptions.ClientError as exc:
                if "AlreadyExists" not in (exc.code or ""):
                    raise
        return driver

    async def _check_dimensions(self, session: neo4j.AsyncSession) -> None:
        result = await session.run(
            "SHOW INDEXES YIELD name, options WHERE name = $name", name=_INDEX
        )
        record = await result.single()
        if record is None:
            return
        existing = (record["options"] or {}).get("indexConfig", {}).get("vector.dimensions")
        if existing is not None and int(existing) != self.dimensions:
            raise BackendError(
                f"vector index {_INDEX} exists with dimension {existing}, but this "
                f"backend/embedder expects {self.dimensions}. Use a dedicated database."
            )

    async def close(self) -> None:
        if self._driver is not None:
            await self._driver.close()
            self._driver = None

    # --- (de)serialization ----------------------------------------

    def _node_props(self, node: Node) -> dict[str, Any]:
        return {
            "id": node.id,
            "user_id": node.user_id,
            "type": node.type,
            "label": node.label,
            "embedding": list(node.embedding) or None,
            "importance": float(node.importance),
            "access_count": int(node.access_count),
            "last_accessed_at": iso(node.last_accessed_at),
            "created_at": iso(node.created_at),
            "valid_from": iso(node.valid_from),
            "valid_to": iso(node.valid_to) if node.valid_to else None,
            "superseded_by": node.superseded_by,
            "resolution_source": node.resolution_source,
            "metadata": json.dumps(node.metadata),
        }

    def _props_to_node(self, props: dict[str, Any]) -> Node:
        return Node(
            id=props["id"],
            user_id=props["user_id"],
            type=props["type"],
            label=props["label"],
            embedding=[float(x) for x in props.get("embedding") or []],
            importance=props["importance"],
            access_count=props["access_count"],
            last_accessed_at=parse_ts(props["last_accessed_at"]),  # type: ignore[arg-type]
            created_at=parse_ts(props["created_at"]),  # type: ignore[arg-type]
            valid_from=parse_ts(props["valid_from"]),  # type: ignore[arg-type]
            valid_to=parse_ts(props.get("valid_to")),
            superseded_by=props.get("superseded_by"),
            resolution_source=props.get("resolution_source", "llm"),
            metadata=json.loads(props.get("metadata") or "{}"),
        )

    def _edge_props(self, edge: Edge) -> dict[str, Any]:
        return {
            "id": edge.id,
            "user_id": edge.user_id,
            "source_id": edge.source_id,
            "target_id": edge.target_id,
            "relation": edge.relation,
            "weight": float(edge.weight),
            "created_at": iso(edge.created_at),
            "valid_from": iso(edge.valid_from),
            "valid_to": iso(edge.valid_to) if edge.valid_to else None,
            "superseded_by": edge.superseded_by,
            "metadata": json.dumps(edge.metadata),
        }

    def _props_to_edge(self, props: dict[str, Any]) -> Edge:
        return Edge(
            id=props["id"],
            user_id=props["user_id"],
            source_id=props["source_id"],
            target_id=props["target_id"],
            relation=props["relation"],
            weight=props["weight"],
            created_at=parse_ts(props["created_at"]),  # type: ignore[arg-type]
            valid_from=parse_ts(props["valid_from"]),  # type: ignore[arg-type]
            valid_to=parse_ts(props.get("valid_to")),
            superseded_by=props.get("superseded_by"),
            metadata=json.loads(props.get("metadata") or "{}"),
        )

    # --- node CRUD ----------------------------------------------

    async def create_node(self, node: Node) -> Node:
        if node.user_id != self.user_id:
            raise BackendError(
                f"node.user_id {node.user_id!r} does not match backend user {self.user_id!r}"
            )
        import neo4j.exceptions

        driver = await self._driver_or_init()
        try:
            async with driver.session(database=self.database) as session:
                await session.run("CREATE (n:Node) SET n = $props", props=self._node_props(node))
        except neo4j.exceptions.ConstraintError as exc:
            raise BackendError(f"node {node.id} already exists") from exc
        return node

    async def update_node(self, node_id: str, updates: dict[str, Any]) -> Node:
        unknown = set(updates) - _UPDATABLE_NODE_FIELDS
        if unknown:
            raise BackendError(f"cannot update node fields: {sorted(unknown)}")
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            record = await (
                await session.run(
                    "MATCH (n:Node {id: $id, user_id: $uid}) RETURN properties(n) AS p",
                    id=node_id,
                    uid=self.user_id,
                )
            ).single()
            if record is None:
                raise NodeNotFoundError(node_id)
            node = self._props_to_node(record["p"])
            for key, value in updates.items():
                setattr(node, key, value)
            await session.run(
                "MATCH (n:Node {id: $id}) SET n = $props",
                id=node_id,
                props=self._node_props(node),
            )
        return node

    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node:
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            record = await (
                await session.run(
                    "MATCH (n:Node {id: $id, user_id: $uid}) RETURN properties(n) AS p",
                    id=node_id,
                    uid=self.user_id,
                )
            ).single()
            if record is None:
                raise NodeNotFoundError(node_id)
            node = self._props_to_node(record["p"])
            if node.valid_to is None:
                node.valid_to = now_utc()
            if superseded_by is not None:
                node.superseded_by = superseded_by
            await session.run(
                "MATCH (n:Node {id: $id}) SET n.valid_to = $vt, n.superseded_by = $sb",
                id=node_id,
                vt=iso(node.valid_to),
                sb=node.superseded_by,
            )
        return node

    async def get_node(self, node_id: str, as_of: datetime | None = None) -> Node | None:
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            record = await (
                await session.run(
                    "MATCH (n:Node {id: $id, user_id: $uid}) RETURN properties(n) AS p",
                    id=node_id,
                    uid=self.user_id,
                )
            ).single()
        if record is None:
            return None
        node = self._props_to_node(record["p"])
        if as_of is not None and not active_at(node, as_of):
            return None
        return node

    # --- edge CRUD --------------------------------------------

    async def upsert_edge(self, edge: Edge) -> Edge:
        if edge.user_id != self.user_id:
            raise BackendError(
                f"edge.user_id {edge.user_id!r} does not match backend user {self.user_id!r}"
            )
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            existing = await (
                await session.run(
                    """MATCH (:Node {id: $sid})-[e:EDGE {relation: $rel}]->(:Node {id: $tid})
                       WHERE e.user_id = $uid AND e.valid_to IS NULL
                       RETURN properties(e) AS p""",
                    sid=edge.source_id,
                    tid=edge.target_id,
                    rel=edge.relation,
                    uid=self.user_id,
                )
            ).single()
            if existing is not None:
                merged = self._props_to_edge(existing["p"])
                merged.weight += edge.weight
                merged.metadata = {**merged.metadata, **edge.metadata}
                await session.run(
                    "MATCH ()-[e:EDGE {id: $id}]->() SET e.weight = $w, e.metadata = $m",
                    id=merged.id,
                    w=merged.weight,
                    m=json.dumps(merged.metadata),
                )
                return merged

            summary = await session.run(
                """MATCH (s:Node {id: $sid, user_id: $uid}), (t:Node {id: $tid, user_id: $uid})
                   CREATE (s)-[e:EDGE]->(t) SET e = $props
                   RETURN e.id AS id""",
                sid=edge.source_id,
                tid=edge.target_id,
                uid=self.user_id,
                props=self._edge_props(edge),
            )
            if await summary.single() is None:
                raise BackendError(
                    f"cannot create edge {edge.id}: source or target node is missing"
                )
        return edge

    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge:
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            record = await (
                await session.run(
                    "MATCH ()-[e:EDGE {id: $id}]->() WHERE e.user_id = $uid "
                    "RETURN properties(e) AS p",
                    id=edge_id,
                    uid=self.user_id,
                )
            ).single()
            if record is None:
                raise EdgeNotFoundError(edge_id)
            edge = self._props_to_edge(record["p"])
            if edge.valid_to is None:
                edge.valid_to = now_utc()
            if superseded_by is not None:
                edge.superseded_by = superseded_by
            await session.run(
                "MATCH ()-[e:EDGE {id: $id}]->() SET e.valid_to = $vt, e.superseded_by = $sb",
                id=edge_id,
                vt=iso(edge.valid_to),
                sb=edge.superseded_by,
            )
        return edge

    async def get_edge(self, edge_id: str, as_of: datetime | None = None) -> Edge | None:
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            record = await (
                await session.run(
                    "MATCH ()-[e:EDGE {id: $id}]->() WHERE e.user_id = $uid "
                    "RETURN properties(e) AS p",
                    id=edge_id,
                    uid=self.user_id,
                )
            ).single()
        if record is None:
            return None
        edge = self._props_to_edge(record["p"])
        if as_of is not None and not active_at(edge, as_of):
            return None
        return edge

    async def list_edges(
        self,
        *,
        active_only: bool = True,
        as_of: datetime | None = None,
    ) -> list[Edge]:
        edges = await self._all_edges()
        if as_of is not None:
            return [e for e in edges if active_at(e, as_of)]
        if active_only:
            return [e for e in edges if e.valid_to is None]
        return edges

    # --- search + traversal ---------------------------------

    async def vector_search(self, embedding: Vector, top_k: int) -> list[Node]:
        if not embedding or top_k <= 0:
            return []
        driver = await self._driver_or_init()
        overfetch = max(top_k * 5, 50)
        async with driver.session(database=self.database) as session:
            result = await session.run(
                f"""CALL db.index.vector.queryNodes('{_INDEX}', $overfetch, $vec)
                    YIELD node
                    WHERE node.user_id = $uid AND node.valid_to IS NULL
                    RETURN properties(node) AS p
                    LIMIT $k""",
                overfetch=overfetch,
                vec=list(embedding),
                uid=self.user_id,
                k=top_k,
            )
            return [self._props_to_node(r["p"]) async for r in result]

    async def _all_nodes(self) -> list[Node]:
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            result = await session.run(
                "MATCH (n:Node {user_id: $uid}) RETURN properties(n) AS p", uid=self.user_id
            )
            return [self._props_to_node(r["p"]) async for r in result]

    async def _all_edges(self) -> list[Edge]:
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            result = await session.run(
                "MATCH ()-[e:EDGE {user_id: $uid}]->() RETURN properties(e) AS p",
                uid=self.user_id,
            )
            return [self._props_to_edge(r["p"]) async for r in result]

    async def list_nodes(
        self,
        *,
        active_only: bool = True,
        context: list[str] | None = None,
        as_of: datetime | None = None,
    ) -> list[Node]:
        nodes = await self._all_nodes()
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
        nodes_by_id = {n.id: n for n in await self._all_nodes()}
        edges = await self._all_edges()
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

        driver = await self._driver_or_init()
        now = now_utc()
        nodes = [n for n in await self._all_nodes() if n.valid_to is None]

        scores: dict[str, float] = {}
        prune_candidates: list[str] = []
        for node in nodes:
            new_score = score_node(node, config, now)
            scores[node.id] = new_score
            if new_score < config.importance_floor:
                prune_candidates.append(node.id)

        async with driver.session(database=self.database) as session:
            await session.run(
                "UNWIND $rows AS row MATCH (n:Node {id: row.id}) SET n.importance = row.score",
                rows=[{"id": nid, "score": s} for nid, s in scores.items()],
            )
            pruned: list[str] = []
            if config.pruning and prune_candidates:
                await session.run(
                    "UNWIND $ids AS nid MATCH (n:Node {id: nid}) "
                    "WHERE n.valid_to IS NULL SET n.valid_to = $now",
                    ids=prune_candidates,
                    now=iso(now),
                )
                pruned = list(prune_candidates)

        return DecayResult(
            ran_at=now,
            nodes_scored=len(nodes),
            nodes_pruned=pruned,
            prune_candidates=prune_candidates,
            scores=scores,
        )

    # --- the one hard-delete path ---------------------------

    async def purge_user(self, user_id: str) -> PurgeResult:
        """Hard-delete every row for ``user_id``. See :meth:`BaseBackend.purge_user`."""
        if user_id != self.user_id:
            raise BackendError(
                f"purge_user {user_id!r} does not match backend user {self.user_id!r}"
            )
        driver = await self._driver_or_init()
        async with driver.session(database=self.database) as session:
            # Delete the user's relationships first so the count is exact even if
            # an edge ever spans a node this user does not own.
            edges = await (
                await session.run(
                    "MATCH ()-[e:EDGE {user_id: $uid}]->() DELETE e RETURN count(e) AS n",
                    uid=user_id,
                )
            ).single()
            nodes = await (
                await session.run(
                    "MATCH (n:Node {user_id: $uid}) DETACH DELETE n RETURN count(n) AS n",
                    uid=user_id,
                )
            ).single()
        return PurgeResult(
            nodes_deleted=int(nodes["n"]) if nodes else 0,
            edges_deleted=int(edges["n"]) if edges else 0,
        )
