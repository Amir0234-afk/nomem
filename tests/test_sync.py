"""Phase 3: sync / async parity — the sync API is a faithful wrapper."""

from __future__ import annotations

import asyncio
import os

import pytest
from nomem import MemoryGraph, NomemError

from conftest import FakeEmbedder, FakeLLM

_PG_DSN = os.environ.get("NOMEM_TEST_POSTGRES_DSN")
_NEO4J_URI = os.environ.get("NOMEM_TEST_NEO4J_URI")
_NEO4J_AUTH = tuple((os.environ.get("NOMEM_TEST_NEO4J_AUTH") or "neo4j:").split(":", 1))

_LLM = {
    "entities": [{"label": "Kira", "type": "entity"}, {"label": "Lisbon", "type": "entity"}],
    "relations": [{"source": "Kira", "target": "Lisbon", "relation": "lives_in"}],
}


def _sqlite_graph() -> MemoryGraph:
    return MemoryGraph(
        user_id="u1",
        backend_options={"path": ":memory:"},
        embedder=FakeEmbedder(),
        llm=FakeLLM(_LLM),
        decay="combined",
    )


def test_sync_ingest_retrieve_decay_roundtrip() -> None:
    g = _sqlite_graph()
    receipt = g.ingest("where?", "Kira lives in Lisbon.")
    assert len(receipt.nodes_created) == 2

    result = g.retrieve("Kira")
    assert {n.label for n in result.nodes} == {"Kira", "Lisbon"}

    decayed = g.run_decay()
    assert decayed.nodes_scored == 2
    g.close()


def test_sync_matches_async() -> None:
    sync_g = _sqlite_graph()
    sync_g.ingest("q", "Kira lives in Lisbon.")
    sync_labels = {n.label for n in sync_g.retrieve("Kira").nodes}

    async def _async_path() -> set[str]:
        g = _sqlite_graph()
        await g.aingest("q", "Kira lives in Lisbon.")
        result = await g.aretrieve("Kira")
        return {n.label for n in result.nodes}

    assert sync_labels == asyncio.run(_async_path())


def test_repeated_sync_calls_share_one_loop() -> None:
    g = _sqlite_graph()
    for _ in range(5):
        g.ingest("q", "Kira lives in Lisbon.")
    node_id = g.retrieve("Kira").nodes[0].id
    # access_count accrued across independent sync retrieve() calls
    before = g.retrieve("Kira").metadata["decay_scores"]
    assert node_id in before


def test_sync_methods_reject_running_loop() -> None:
    async def _inner() -> None:
        g = _sqlite_graph()
        with pytest.raises(NomemError):
            g.retrieve("q")

    asyncio.run(_inner())


def test_context_manager_closes_backend() -> None:
    closed = []
    g = _sqlite_graph()
    original = g.backend.close

    def spy() -> None:
        closed.append(True)
        original()

    g.backend.close = spy  # type: ignore[method-assign]
    with g:
        g.ingest("q", "Kira lives in Lisbon.")
    assert closed == [True]


async def test_async_context_manager() -> None:
    async with _sqlite_graph() as g:
        receipt = await g.aingest("q", "Kira lives in Lisbon.")
    assert receipt.nodes_created


# --- the persistent-loop payoff: sync + a networked backend ------------


@pytest.mark.parametrize(
    "kind",
    [
        pytest.param("postgres", marks=pytest.mark.postgres),
        pytest.param("neo4j", marks=pytest.mark.neo4j),
    ],
)
def test_sync_api_survives_multiple_calls_on_networked_backend(kind: str) -> None:
    """A per-call asyncio.run() loop would kill the pool/driver after call 1."""
    if kind == "postgres":
        if not _PG_DSN:
            pytest.skip("NOMEM_TEST_POSTGRES_DSN not set")
        pytest.importorskip("asyncpg")
        _run_async(_drop_pg())
        opts: dict[str, object] = {"backend": "postgres", "backend_options": {"dsn": _PG_DSN}}
    else:
        if not _NEO4J_URI:
            pytest.skip("NOMEM_TEST_NEO4J_URI not set")
        pytest.importorskip("neo4j")
        _run_async(_wipe_neo4j())
        opts = {"backend": "neo4j", "backend_options": {"uri": _NEO4J_URI, "auth": _NEO4J_AUTH}}

    g = MemoryGraph(
        user_id=f"{kind}-sync",
        embedder=FakeEmbedder(),
        llm=FakeLLM(_LLM),
        decay="time",
        **opts,  # type: ignore[arg-type]
    )
    try:
        g.ingest("q1", "Kira lives in Lisbon.")          # call 1: builds pool/driver
        g.ingest("q2", "Kira lives in Lisbon.")          # call 2: reuses it
        assert {n.label for n in g.retrieve("Kira").nodes} == {"Kira", "Lisbon"}
        assert g.run_decay().nodes_scored == 2
    finally:
        g.close()


def _run_async(coro: object) -> None:
    asyncio.run(coro)  # type: ignore[arg-type]


async def _drop_pg() -> None:
    import asyncpg

    conn = await asyncpg.connect(_PG_DSN)
    try:
        await conn.execute("DROP TABLE IF EXISTS nodes, edges CASCADE")
    finally:
        await conn.close()


async def _wipe_neo4j() -> None:
    import neo4j

    driver = neo4j.AsyncGraphDatabase.driver(_NEO4J_URI, auth=_NEO4J_AUTH)
    try:
        async with driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")
            await session.run("DROP INDEX nomem_node_embedding IF EXISTS")
    finally:
        await driver.close()
