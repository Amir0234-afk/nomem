"""Phase 1: SQLite backend — CRUD, bi-temporal semantics, search, traversal."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from nomem.backends.sqlite import SQLiteBackend
from nomem.exceptions import BackendError, EdgeNotFoundError, NodeNotFoundError
from nomem.models import Edge, Node

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def make_node(
    nid: str, *, label: str = "x", vec: list[float] | None = None, at: datetime = NOW
) -> Node:
    return Node(
        id=nid,
        user_id="u1",
        type="entity",
        label=label,
        embedding=vec if vec is not None else [1.0, 0.0, 0.0],
        importance=1.0,
        access_count=0,
        last_accessed_at=at,
        created_at=at,
        valid_from=at,
    )


def make_edge(eid: str, src: str, tgt: str, *, at: datetime = NOW) -> Edge:
    return Edge(
        id=eid,
        user_id="u1",
        source_id=src,
        target_id=tgt,
        relation="involves",
        weight=1.0,
        created_at=at,
        valid_from=at,
    )


@pytest.fixture
def backend() -> SQLiteBackend:
    return SQLiteBackend(user_id="u1", path=":memory:")


async def test_create_get_roundtrip(backend: SQLiteBackend) -> None:
    node = make_node("n1", label="Kira")
    await backend.create_node(node)
    got = await backend.get_node("n1")
    assert got is not None
    assert got.label == "Kira"
    assert got.embedding == [1.0, 0.0, 0.0]
    assert got.valid_to is None


async def test_create_rejects_foreign_user(backend: SQLiteBackend) -> None:
    bad = make_node("n1")
    bad.user_id = "someone-else"
    with pytest.raises(BackendError):
        await backend.create_node(bad)


async def test_duplicate_id_rejected(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("n1"))
    with pytest.raises(BackendError):
        await backend.create_node(make_node("n1"))


async def test_update_node(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("n1"))
    updated = await backend.update_node("n1", {"importance": 0.3, "access_count": 5})
    assert updated.importance == 0.3
    assert updated.access_count == 5
    assert (await backend.get_node("n1")).importance == 0.3  # type: ignore[union-attr]


async def test_update_missing_raises(backend: SQLiteBackend) -> None:
    with pytest.raises(NodeNotFoundError):
        await backend.update_node("nope", {"importance": 0.1})


async def test_update_rejects_unknown_field(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("n1"))
    with pytest.raises(BackendError):
        await backend.update_node("n1", {"id": "n2"})


async def test_retire_sets_valid_to_and_supersedes(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("n1"))
    await backend.create_node(make_node("n2"))
    retired = await backend.retire_node("n1", superseded_by="n2")
    assert retired.valid_to is not None
    assert retired.superseded_by == "n2"
    # row still present — retirement is never a hard delete
    still_there = await backend.get_node("n1")
    assert still_there is not None and still_there.valid_to is not None


async def test_get_node_as_of_temporal_window(backend: SQLiteBackend) -> None:
    t0 = NOW
    await backend.create_node(make_node("n1", at=t0))
    await backend.retire_node("n1")
    node = await backend.get_node("n1")
    assert node is not None and node.valid_to is not None

    # Known and active at t0+1min
    assert await backend.get_node("n1", as_of=t0 + timedelta(minutes=1)) is not None
    # Not yet created a year earlier
    assert await backend.get_node("n1", as_of=t0 - timedelta(days=365)) is None
    # Retired by "now + 1 day"
    assert await backend.get_node("n1", as_of=datetime.now(tz=UTC) + timedelta(days=1)) is None


async def test_upsert_edge_reinforces_weight(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("a"))
    await backend.create_node(make_node("b"))
    e1 = await backend.upsert_edge(make_edge("e1", "a", "b"))
    e2 = await backend.upsert_edge(make_edge("e2", "a", "b"))
    assert e1.id == e2.id  # same active edge
    assert e2.weight == 2.0


async def test_retire_edge(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("a"))
    await backend.create_node(make_node("b"))
    await backend.upsert_edge(make_edge("e1", "a", "b"))
    retired = await backend.retire_edge("e1")
    assert retired.valid_to is not None
    with pytest.raises(EdgeNotFoundError):
        await backend.retire_edge("missing")


async def test_vector_search_orders_by_similarity(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("near", vec=[1.0, 0.0, 0.0]))
    await backend.create_node(make_node("mid", vec=[0.7, 0.7, 0.0]))
    await backend.create_node(make_node("far", vec=[0.0, 0.0, 1.0]))
    hits = await backend.vector_search([1.0, 0.0, 0.0], top_k=2)
    assert [n.id for n in hits] == ["near", "mid"]


async def test_vector_search_skips_retired(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("a", vec=[1.0, 0.0, 0.0]))
    await backend.retire_node("a")
    assert await backend.vector_search([1.0, 0.0, 0.0], top_k=5) == []


async def test_traverse_respects_hops(backend: SQLiteBackend) -> None:
    for nid in ("a", "b", "c", "d"):
        await backend.create_node(make_node(nid))
    await backend.upsert_edge(make_edge("e1", "a", "b"))
    await backend.upsert_edge(make_edge("e2", "b", "c"))
    await backend.upsert_edge(make_edge("e3", "c", "d"))

    one_hop = await backend.traverse(["a"], hops=1)
    assert {n.id for n in one_hop.nodes} == {"a", "b"}

    two_hop = await backend.traverse(["a"], hops=2)
    assert {n.id for n in two_hop.nodes} == {"a", "b", "c"}


async def test_traverse_as_of_excludes_future_nodes(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("a", at=NOW))
    await backend.create_node(make_node("b", at=NOW))
    await backend.upsert_edge(make_edge("e1", "a", "b", at=NOW))
    # A node created "now" is in the future relative to as_of=NOW+1min? No — include.
    sg = await backend.traverse(["a"], hops=1, as_of=NOW + timedelta(minutes=1))
    assert {n.id for n in sg.nodes} == {"a", "b"}
    # Everything is in the future relative to a year before NOW
    empty = await backend.traverse(["a"], hops=1, as_of=NOW - timedelta(days=365))
    assert empty.nodes == []


async def test_cross_reference_threshold(backend: SQLiteBackend) -> None:
    await backend.create_node(make_node("a", vec=[1.0, 0.0, 0.0]))
    await backend.create_node(make_node("b", vec=[0.99, 0.14, 0.0]))
    await backend.create_node(make_node("c", vec=[0.0, 1.0, 0.0]))
    node_a = await backend.get_node("a")
    assert node_a is not None
    hits = await backend.cross_reference(node_a, threshold=0.9)
    assert [n.id for n, _ in hits] == ["b"]


async def test_user_scoping(backend: SQLiteBackend) -> None:
    other = SQLiteBackend(user_id="u2", path=":memory:")
    await backend.create_node(make_node("n1"))
    assert await other.get_node("n1") is None


async def test_run_decay_scores_and_optionally_prunes(backend: SQLiteBackend) -> None:
    from datetime import timedelta

    from nomem.config import DecayConfig

    stale = make_node("stale", at=NOW - timedelta(days=400))
    stale.last_accessed_at = NOW - timedelta(days=400)
    fresh = make_node("fresh", at=datetime.now(tz=UTC))
    fresh.last_accessed_at = datetime.now(tz=UTC)
    fresh.access_count = 10
    await backend.create_node(stale)
    await backend.create_node(fresh)

    # scoring only — nothing retired
    result = await backend.run_decay(DecayConfig(mode="combined", pruning=False))
    assert result.nodes_scored == 2
    assert result.scores["stale"] < result.scores["fresh"]
    assert "stale" in result.prune_candidates
    assert result.nodes_pruned == []
    assert (await backend.get_node("stale")) is not None  # still active

    # pruning on — stale node is retired, not deleted
    result = await backend.run_decay(DecayConfig(mode="combined", pruning=True))
    assert "stale" in result.nodes_pruned
    stale_after = await backend.get_node("stale")
    assert stale_after is not None and stale_after.valid_to is not None


async def test_list_nodes_filters_by_context(backend: SQLiteBackend) -> None:
    work = make_node("work")
    work.metadata = {"context": ["work"]}
    home = make_node("home")
    home.metadata = {"context": ["home"]}
    await backend.create_node(work)
    await backend.create_node(home)

    assert {n.id for n in await backend.list_nodes()} == {"work", "home"}
    assert [n.id for n in await backend.list_nodes(context=["work"])] == ["work"]


async def test_concurrent_writes_are_serialized(backend: SQLiteBackend) -> None:
    await asyncio.gather(*(backend.create_node(make_node(f"n{i}")) for i in range(20)))
    hits = await backend.vector_search([1.0, 0.0, 0.0], top_k=50)
    assert len(hits) == 20
