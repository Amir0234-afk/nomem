"""Contract: the public MemoryGraph surface."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable

import nomem
import pytest
from nomem import MemoryGraph
from nomem.embedders import CallableEmbedder


def test_version() -> None:
    assert nomem.__version__ == "0.0.0"


def test_constructs_with_named_backend_and_embedder() -> None:
    g = MemoryGraph(
        user_id="u123",
        backend="sqlite",
        backend_options={"path": ":memory:"},
        embedder="nomic",
    )
    assert g.config.user_id == "u123"
    assert g.backend.__class__.__name__ == "SQLiteBackend"
    assert g.embedder.dimensions == 768  # nomic-embed-text native size
    assert g.backend.user_id == "u123"  # user scoping is injected


def test_public_methods_exist_with_expected_signatures() -> None:
    for name in ("ingest", "aingest"):
        params = list(inspect.signature(getattr(MemoryGraph, name)).parameters)
        assert params[:4] == ["self", "user", "assistant", "config"]
    for name in ("retrieve", "aretrieve"):
        params = list(inspect.signature(getattr(MemoryGraph, name)).parameters)
        assert params[:2] == ["self", "query"]
        assert "as_of" in params
    for name in ("run_decay", "arun_decay"):
        assert "config" in inspect.signature(getattr(MemoryGraph, name)).parameters


async def test_ingest_and_retrieve_run_end_to_end(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph(
        llm_response={
            "entities": [
                {"label": "Kira", "type": "entity"},
                {"label": "the argument", "type": "event"},
            ],
            "relations": [
                {"source": "Kira", "target": "the argument", "relation": "involves"},
            ],
        }
    )
    receipt = await g.aingest("What happened with Kira?", "Kira left after the argument.")
    assert len(receipt.nodes_created) == 2
    assert len(receipt.edges_upserted) == 1

    result = await g.aretrieve("Kira")
    assert {n.label for n in result.nodes} == {"Kira", "the argument"}
    assert result.metadata["retrieval_mode"] == "flat"


async def test_run_decay_is_phase_two(make_graph: Callable[..., MemoryGraph]) -> None:
    with pytest.raises(NotImplementedError):
        await make_graph().arun_decay()


def test_sync_methods_reject_running_loop(make_graph: Callable[..., MemoryGraph]) -> None:
    async def _inner() -> None:
        g = make_graph()
        with pytest.raises(nomem.NomemError):
            g.retrieve("q")

    asyncio.run(_inner())


def test_accepts_callable_embedder_instance() -> None:
    g = MemoryGraph(
        user_id="u1",
        backend_options={"path": ":memory:"},
        embedder=CallableEmbedder(lambda t: [0.0, 0.0], dimensions=2),
    )
    assert g.embedder.dimensions == 2
