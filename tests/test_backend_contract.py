"""Backend contract — every BaseBackend implementation must behave identically.

Runs against SQLite always; against PostgreSQL when ``NOMEM_TEST_POSTGRES_DSN``
is set; against Neo4j when ``NOMEM_TEST_NEO4J_URI`` (+ ``NOMEM_TEST_NEO4J_AUTH``,
``user:password``) is set. See docker-compose.yml.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta

import pytest
from nomem.backends.base import BaseBackend
from nomem.backends.neo4j import Neo4jBackend
from nomem.backends.postgres import PostgresBackend
from nomem.backends.sqlite import SQLiteBackend
from nomem.config import DecayConfig
from nomem.exceptions import BackendError, EdgeNotFoundError, NodeNotFoundError
from nomem.models import Edge, Node

NOW = datetime(2026, 1, 1, tzinfo=UTC)
_PG_DSN = os.environ.get("NOMEM_TEST_POSTGRES_DSN")
_NEO4J_URI = os.environ.get("NOMEM_TEST_NEO4J_URI")
_NEO4J_AUTH = tuple((os.environ.get("NOMEM_TEST_NEO4J_AUTH") or "neo4j:").split(":", 1))


def make_node(
    nid: str,
    *,
    user: str = "u1",
    label: str = "x",
    vec: list[float] | None = None,
    at: datetime = NOW,
) -> Node:
    return Node(
        id=nid,
        user_id=user,
        type="entity",
        label=label,
        embedding=vec if vec is not None else [1.0, 0.0, 0.0],
        importance=1.0,
        access_count=0,
        last_accessed_at=at,
        created_at=at,
        valid_from=at,
    )


def make_edge(eid: str, src: str, tgt: str, *, user: str = "u1", at: datetime = NOW) -> Edge:
    return Edge(
        id=eid,
        user_id=user,
        source_id=src,
        target_id=tgt,
        relation="involves",
        weight=1.0,
        created_at=at,
        valid_from=at,
    )


BackendFactory = Callable[..., BaseBackend]


async def _reset_pg_schema() -> None:
    """Drop nomem's tables so the suite can recreate them at a fresh dimension."""
    import asyncpg

    conn = await asyncpg.connect(_PG_DSN)
    try:
        await conn.execute("DROP TABLE IF EXISTS nodes, edges CASCADE")
    finally:
        await conn.close()


async def _reset_neo4j() -> None:
    """Wipe the graph + vector index so the suite can recreate it at a fresh dimension."""
    import neo4j

    driver = neo4j.AsyncGraphDatabase.driver(_NEO4J_URI, auth=_NEO4J_AUTH)
    try:
        async with driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")
            await session.run("DROP INDEX nomem_node_embedding IF EXISTS")
    finally:
        await driver.close()


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param("postgres", marks=pytest.mark.postgres),
        pytest.param("neo4j", marks=pytest.mark.neo4j),
    ]
)
async def backend_factory(request: pytest.FixtureRequest) -> AsyncIterator[BackendFactory]:
    kind = request.param
    created: list[BaseBackend] = []

    if kind == "postgres":
        if not _PG_DSN:
            pytest.skip("NOMEM_TEST_POSTGRES_DSN not set")
        pytest.importorskip("asyncpg")
        pytest.importorskip("pgvector")
        await _reset_pg_schema()
    elif kind == "neo4j":
        if not _NEO4J_URI:
            pytest.skip("NOMEM_TEST_NEO4J_URI not set")
        pytest.importorskip("neo4j")
        await _reset_neo4j()

    def make(user_id: str = "u1") -> BaseBackend:
        if kind == "sqlite":
            return _track(SQLiteBackend(user_id=user_id, path=":memory:"))
        if kind == "postgres":
            return _track(PostgresBackend(user_id=user_id, dsn=_PG_DSN, vector_dimensions=3))
        return _track(
            Neo4jBackend(
                user_id=user_id, uri=_NEO4J_URI, auth=_NEO4J_AUTH, vector_dimensions=3
            )
        )

    def _track(b: BaseBackend) -> BaseBackend:
        created.append(b)
        return b

    yield make

    for b in created:
        result = b.close()
        if asyncio.iscoroutine(result):
            await result


@pytest.fixture
async def backend(backend_factory: BackendFactory) -> BaseBackend:
    return backend_factory()


async def test_create_get_roundtrip(backend: BaseBackend) -> None:
    await backend.create_node(make_node("n1", label="Kira"))
    got = await backend.get_node("n1")
    assert got is not None
    assert got.label == "Kira"
    assert got.embedding == [1.0, 0.0, 0.0]
    assert got.valid_to is None


async def test_create_rejects_foreign_user(backend: BaseBackend) -> None:
    bad = make_node("n1", user="someone-else")
    with pytest.raises(BackendError):
        await backend.create_node(bad)


async def test_duplicate_id_rejected(backend: BaseBackend) -> None:
    await backend.create_node(make_node("n1"))
    with pytest.raises(BackendError):
        await backend.create_node(make_node("n1"))


async def test_update_node(backend: BaseBackend) -> None:
    await backend.create_node(make_node("n1"))
    updated = await backend.update_node("n1", {"importance": 0.3, "access_count": 5})
    assert updated.importance == 0.3
    assert updated.access_count == 5
    reread = await backend.get_node("n1")
    assert reread is not None and reread.importance == 0.3


async def test_update_missing_raises(backend: BaseBackend) -> None:
    with pytest.raises(NodeNotFoundError):
        await backend.update_node("nope", {"importance": 0.1})


async def test_update_rejects_unknown_field(backend: BaseBackend) -> None:
    await backend.create_node(make_node("n1"))
    with pytest.raises(BackendError):
        await backend.update_node("n1", {"id": "n2"})


async def test_retire_sets_valid_to_and_supersedes(backend: BaseBackend) -> None:
    await backend.create_node(make_node("n1"))
    await backend.create_node(make_node("n2"))
    retired = await backend.retire_node("n1", superseded_by="n2")
    assert retired.valid_to is not None
    assert retired.superseded_by == "n2"
    still_there = await backend.get_node("n1")  # never a hard delete
    assert still_there is not None and still_there.valid_to is not None


async def test_get_node_as_of_temporal_window(backend: BaseBackend) -> None:
    await backend.create_node(make_node("n1", at=NOW))
    await backend.retire_node("n1")

    assert await backend.get_node("n1", as_of=NOW + timedelta(minutes=1)) is not None
    assert await backend.get_node("n1", as_of=NOW - timedelta(days=365)) is None
    assert await backend.get_node("n1", as_of=datetime.now(tz=UTC) + timedelta(days=1)) is None


async def test_upsert_edge_reinforces_weight(backend: BaseBackend) -> None:
    await backend.create_node(make_node("a"))
    await backend.create_node(make_node("b"))
    e1 = await backend.upsert_edge(make_edge("e1", "a", "b"))
    e2 = await backend.upsert_edge(make_edge("e2", "a", "b"))
    assert e1.id == e2.id
    assert e2.weight == 2.0


async def test_retire_edge(backend: BaseBackend) -> None:
    await backend.create_node(make_node("a"))
    await backend.create_node(make_node("b"))
    await backend.upsert_edge(make_edge("e1", "a", "b"))
    retired = await backend.retire_edge("e1")
    assert retired.valid_to is not None
    with pytest.raises(EdgeNotFoundError):
        await backend.retire_edge("missing")


async def test_get_edge_as_of_temporal_window(backend: BaseBackend) -> None:
    await backend.create_node(make_node("a"))
    await backend.create_node(make_node("b"))
    await backend.upsert_edge(make_edge("e1", "a", "b", at=NOW))
    assert await backend.get_edge("missing") is None

    got = await backend.get_edge("e1")
    assert got is not None and got.source_id == "a"

    await backend.retire_edge("e1")
    assert await backend.get_edge("e1", as_of=NOW + timedelta(minutes=1)) is not None
    assert await backend.get_edge("e1", as_of=NOW - timedelta(days=365)) is None
    assert await backend.get_edge("e1", as_of=datetime.now(tz=UTC) + timedelta(days=1)) is None


async def test_list_edges_enumerates_including_retired(backend: BaseBackend) -> None:
    for nid in ("a", "b", "c"):
        await backend.create_node(make_node(nid))
    await backend.upsert_edge(make_edge("e1", "a", "b", at=NOW))
    await backend.upsert_edge(make_edge("e2", "b", "c", at=NOW))
    await backend.retire_edge("e2")

    assert {e.id for e in await backend.list_edges()} == {"e1"}
    assert {e.id for e in await backend.list_edges(active_only=False)} == {"e1", "e2"}
    # as_of takes precedence over active_only, exactly as list_nodes behaves.
    both = await backend.list_edges(active_only=True, as_of=NOW + timedelta(minutes=1))
    assert {e.id for e in both} == {"e1", "e2"}
    assert await backend.list_edges(active_only=False, as_of=NOW - timedelta(days=365)) == []


async def test_insert_preserves_supplied_timestamps(backend: BaseBackend) -> None:
    """The import half of export/import: no now() stamping on insert."""
    created = NOW - timedelta(days=30)
    retired_at = NOW - timedelta(days=1)

    node = make_node("n1", at=created)
    node.valid_to = retired_at  # inserting an already-retired record is legal
    node.superseded_by = "n2"
    await backend.create_node(node)
    await backend.create_node(make_node("n2", at=created))

    got = await backend.get_node("n1")
    assert got is not None
    assert got.created_at == created
    assert got.valid_from == created
    assert got.valid_to == retired_at
    assert got.superseded_by == "n2"

    edge = make_edge("e1", "n1", "n2", at=created)
    edge.valid_to = retired_at
    await backend.upsert_edge(edge)
    stored = await backend.get_edge("e1")
    assert stored is not None
    assert stored.created_at == created
    assert stored.valid_from == created
    assert stored.valid_to == retired_at


async def test_export_import_round_trip(backend: BaseBackend) -> None:
    """Dump every record, wipe the store, reload the dump, answer identically.

    Restoring in place (rather than into a second handle) is what an export
    actually has to survive, and it is the only shape that means the same thing
    on a file-per-instance backend and on a shared networked one.
    """
    for nid in ("a", "b", "c"):
        await backend.create_node(make_node(nid, label=f"node-{nid}", at=NOW))
    await backend.upsert_edge(make_edge("e1", "a", "b", at=NOW))
    await backend.upsert_edge(make_edge("e2", "b", "c", at=NOW))
    await backend.retire_node("c", superseded_by="a")
    await backend.retire_edge("e2")

    nodes = await backend.list_nodes(active_only=False)
    edges = await backend.list_edges(active_only=False)
    assert len(nodes) == 3
    assert len(edges) == 2

    at = NOW + timedelta(minutes=1)
    before_nodes = {n.id: await backend.get_node(n.id, as_of=at) for n in nodes}
    before_edges = {e.id: await backend.get_edge(e.id, as_of=at) for e in edges}

    await backend.purge_user("u1")
    assert await backend.list_nodes(active_only=False) == []

    for node in nodes:
        await backend.create_node(node)
    for edge in edges:
        await backend.upsert_edge(edge)

    for nid, before in before_nodes.items():
        after = await backend.get_node(nid, as_of=at)
        assert (before is None) == (after is None)
        if before is not None and after is not None:
            assert (before.label, before.valid_from, before.valid_to, before.superseded_by) == (
                after.label,
                after.valid_from,
                after.valid_to,
                after.superseded_by,
            )
    for eid, before_e in before_edges.items():
        after_e = await backend.get_edge(eid, as_of=at)
        assert (before_e is None) == (after_e is None)
        if before_e is not None and after_e is not None:
            assert (before_e.weight, before_e.valid_from, before_e.valid_to) == (
                after_e.weight,
                after_e.valid_from,
                after_e.valid_to,
            )

    assert {n.id for n in await backend.list_nodes()} == {"a", "b"}
    assert {e.id for e in await backend.list_edges()} == {"e1"}


async def test_purge_user_deletes_and_is_scoped(backend: BaseBackend) -> None:
    """The one hard-delete path: real deletion, fenced to the backend's own user."""
    await backend.create_node(make_node("a"))
    await backend.create_node(make_node("b"))
    await backend.upsert_edge(make_edge("e1", "a", "b"))
    await backend.retire_node("b")

    with pytest.raises(BackendError):
        await backend.purge_user("someone-else")
    assert await backend.get_node("a") is not None  # the guard ran before any delete

    result = await backend.purge_user("u1")
    assert result.nodes_deleted == 2  # retired rows go too
    assert result.edges_deleted == 1
    assert await backend.get_node("a") is None
    assert await backend.list_nodes(active_only=False) == []
    assert await backend.list_edges(active_only=False) == []


async def test_vector_search_orders_by_similarity(backend: BaseBackend) -> None:
    await backend.create_node(make_node("near", vec=[1.0, 0.0, 0.0]))
    await backend.create_node(make_node("mid", vec=[0.7, 0.7, 0.0]))
    await backend.create_node(make_node("far", vec=[0.0, 0.0, 1.0]))
    hits = await backend.vector_search([1.0, 0.0, 0.0], top_k=2)
    assert [n.id for n in hits] == ["near", "mid"]


async def test_vector_search_skips_retired(backend: BaseBackend) -> None:
    await backend.create_node(make_node("a", vec=[1.0, 0.0, 0.0]))
    await backend.retire_node("a")
    assert await backend.vector_search([1.0, 0.0, 0.0], top_k=5) == []


async def test_traverse_respects_hops(backend: BaseBackend) -> None:
    for nid in ("a", "b", "c", "d"):
        await backend.create_node(make_node(nid))
    await backend.upsert_edge(make_edge("e1", "a", "b"))
    await backend.upsert_edge(make_edge("e2", "b", "c"))
    await backend.upsert_edge(make_edge("e3", "c", "d"))

    assert {n.id for n in (await backend.traverse(["a"], hops=1)).nodes} == {"a", "b"}
    assert {n.id for n in (await backend.traverse(["a"], hops=2)).nodes} == {"a", "b", "c"}


async def test_traverse_as_of_excludes_future_nodes(backend: BaseBackend) -> None:
    await backend.create_node(make_node("a", at=NOW))
    await backend.create_node(make_node("b", at=NOW))
    await backend.upsert_edge(make_edge("e1", "a", "b", at=NOW))
    sg = await backend.traverse(["a"], hops=1, as_of=NOW + timedelta(minutes=1))
    assert {n.id for n in sg.nodes} == {"a", "b"}
    empty = await backend.traverse(["a"], hops=1, as_of=NOW - timedelta(days=365))
    assert empty.nodes == []


async def test_cross_reference_threshold(backend: BaseBackend) -> None:
    await backend.create_node(make_node("a", vec=[1.0, 0.0, 0.0]))
    await backend.create_node(make_node("b", vec=[0.99, 0.14, 0.0]))
    await backend.create_node(make_node("c", vec=[0.0, 1.0, 0.0]))
    node_a = await backend.get_node("a")
    assert node_a is not None
    hits = await backend.cross_reference(node_a, threshold=0.9)
    assert [n.id for n, _ in hits] == ["b"]


async def test_user_scoping(backend_factory: BackendFactory) -> None:
    one = backend_factory("u1")
    two = backend_factory("u2")
    await one.create_node(make_node("n1", user="u1"))
    assert await two.get_node("n1") is None


async def test_list_nodes_filters_by_context(backend: BaseBackend) -> None:
    work = make_node("work")
    work.metadata = {"context": ["work"]}
    home = make_node("home")
    home.metadata = {"context": ["home"]}
    await backend.create_node(work)
    await backend.create_node(home)

    assert {n.id for n in await backend.list_nodes()} == {"work", "home"}
    assert [n.id for n in await backend.list_nodes(context=["work"])] == ["work"]


async def test_run_decay_scores_and_optionally_prunes(backend: BaseBackend) -> None:
    stale = make_node("stale", at=NOW - timedelta(days=400))
    stale.last_accessed_at = NOW - timedelta(days=400)
    fresh = make_node("fresh", at=datetime.now(tz=UTC))
    fresh.last_accessed_at = datetime.now(tz=UTC)
    fresh.access_count = 10
    await backend.create_node(stale)
    await backend.create_node(fresh)

    result = await backend.run_decay(DecayConfig(mode="combined", pruning=False))
    assert result.nodes_scored == 2
    assert result.scores["stale"] < result.scores["fresh"]
    assert "stale" in result.prune_candidates
    assert result.nodes_pruned == []
    assert (await backend.get_node("stale")) is not None

    result = await backend.run_decay(DecayConfig(mode="combined", pruning=True))
    assert "stale" in result.nodes_pruned
    stale_after = await backend.get_node("stale")
    assert stale_after is not None and stale_after.valid_to is not None


async def test_concurrent_writes(backend: BaseBackend) -> None:
    await asyncio.gather(*(backend.create_node(make_node(f"n{i}")) for i in range(20)))
    hits = await backend.vector_search([1.0, 0.0, 0.0], top_k=50)
    assert len(hits) == 20


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param("postgres", marks=pytest.mark.postgres),
        pytest.param("neo4j", marks=pytest.mark.neo4j),
    ],
)
async def test_memorygraph_end_to_end_on_networked_backend(kind: str) -> None:
    """Full ingest -> retrieve -> decay through MemoryGraph on a networked backend."""
    from nomem import MemoryGraph

    from conftest import FakeEmbedder, FakeLLM

    if kind == "postgres":
        if not _PG_DSN:
            pytest.skip("NOMEM_TEST_POSTGRES_DSN not set")
        pytest.importorskip("asyncpg")
        await _reset_pg_schema()
        opts = {"backend": "postgres", "backend_options": {"dsn": _PG_DSN}}
    else:
        if not _NEO4J_URI:
            pytest.skip("NOMEM_TEST_NEO4J_URI not set")
        pytest.importorskip("neo4j")
        await _reset_neo4j()
        opts = {
            "backend": "neo4j",
            "backend_options": {"uri": _NEO4J_URI, "auth": _NEO4J_AUTH},
        }

    g = MemoryGraph(
        user_id=f"{kind}-e2e",
        embedder=FakeEmbedder(),
        llm=FakeLLM(
            {
                "entities": [
                    {"label": "Kira", "type": "entity"},
                    {"label": "the merger", "type": "event"},
                ],
                "relations": [{"source": "Kira", "target": "the merger", "relation": "involves"}],
            }
        ),
        decay="combined",
        **opts,  # type: ignore[arg-type]
    )
    receipt = await g.aingest("news?", "Kira closed the merger.", context=["deals"])
    assert len(receipt.nodes_created) == 2
    assert len(receipt.edges_upserted) == 1

    result = await g.aretrieve("Kira")
    assert {n.label for n in result.nodes} == {"Kira", "the merger"}

    routed = await g.aretrieve(
        "Kira", config={"mode": "hierarchical", "core_index_size_floor": 1}, context=["deals"]
    )
    assert routed.metadata["sub_index_used"] == "deals"

    decayed = await g.arun_decay()
    assert decayed.nodes_scored == 2
    await g.backend.close()  # type: ignore[attr-defined]
