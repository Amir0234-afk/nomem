"""Phase 0 contract: the public MemoryGraph surface."""

from __future__ import annotations

import inspect

import nomem
import pytest
from nomem import MemoryGraph


def test_version_is_phase_zero() -> None:
    assert nomem.__version__ == "0.0.0"


def test_constructs_with_named_backend_and_embedder() -> None:
    g = MemoryGraph(user_id="u123", backend="sqlite", embedder="nomic")
    assert g.config.user_id == "u123"
    assert g.backend.__class__.__name__ == "SQLiteBackend"
    assert g.embedder.dimensions == 384


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


async def test_pipeline_calls_raise_not_implemented_not_attribute_error() -> None:
    g = MemoryGraph(user_id="u1")
    with pytest.raises(NotImplementedError):
        await g.aingest("hi", "there")
    with pytest.raises(NotImplementedError):
        await g.aretrieve("q")
    with pytest.raises(NotImplementedError):
        await g.arun_decay()


def test_sync_methods_reject_running_loop() -> None:
    async def _inner() -> None:
        with pytest.raises(nomem.NomemError):
            MemoryGraph(user_id="u1").retrieve("q")

    import asyncio

    asyncio.run(_inner())


def test_accepts_callable_embedder_instance() -> None:
    from nomem.embedders import CallableEmbedder

    g = MemoryGraph(user_id="u1", embedder=CallableEmbedder(lambda t: [0.0, 0.0], dimensions=2))
    assert g.embedder.dimensions == 2
