"""Contract: the public MemoryGraph surface."""

from __future__ import annotations

import asyncio
import importlib
import inspect
import re
from collections.abc import Callable
from pathlib import Path

import nomem
import pytest
from nomem import MemoryGraph
from nomem.embedders import CallableEmbedder

_STABILITY_DOC = Path(__file__).resolve().parent.parent / "docs" / "stability.md"
# Rows of the Public table whose Detail column names exported symbols one by one.
_ENUMERATED_ROWS = ("nomem.models", "nomem.config", "nomem.exceptions")


def test_version() -> None:
    assert nomem.__version__ == "0.0.0"


def test_all_is_sorted_and_importable() -> None:
    assert nomem.__all__ == sorted(nomem.__all__)
    for name in nomem.__all__:
        assert hasattr(nomem, name), f"{name} is in __all__ but not importable"


def test_all_matches_stability_doc() -> None:
    """`__all__` and docs/stability.md's Public table must not drift apart.

    Only the rows that enumerate symbols are checked; rows that describe a
    surface in prose ("Constructor keywords", "All 14 methods") are not a list
    of exports.
    """
    documented: dict[str, set[str]] = {}
    for line in _STABILITY_DOC.read_text(encoding="utf-8").splitlines():
        row = re.match(r"\|\s*`(nomem\.\w+)`\s*\|(.*?)\|\s*$", line)
        if row is None or row.group(1) not in _ENUMERATED_ROWS:
            continue
        names = re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?`", row.group(2))
        documented.setdefault(row.group(1), set()).update(names)

    assert set(documented) == set(_ENUMERATED_ROWS), (
        f"parsed {sorted(documented)} out of docs/stability.md — did the Public table move?"
    )
    for module_name, names in documented.items():
        module = importlib.import_module(module_name)
        for name in sorted(names):
            assert hasattr(module, name), f"{module_name}.{name} is documented but missing"
            if name[:1].isupper():  # classes are re-exported from the package root
                assert name in nomem.__all__, f"{name} is documented public but not in __all__"


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


def test_public_attributes_are_reachable(make_graph: Callable[..., MemoryGraph]) -> None:
    """Plugins reach the graph's internals through these — they are API."""
    g = make_graph(user_id="u123")
    assert g.user_id == "u123"
    for name in ("backend", "embedder", "llm", "config", "plugins"):
        assert hasattr(g, name)
        assert not name.startswith("_")


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


async def test_run_decay_runs(make_graph: Callable[..., MemoryGraph]) -> None:
    g = make_graph(
        llm_response={"entities": [{"label": "Kira", "type": "entity"}], "relations": []}
    )
    await g.aingest("x", "Kira waved.")
    result = await g.arun_decay()
    assert result.nodes_scored == 1
    assert result.scores


async def test_run_decay_raises_when_disabled(make_graph: Callable[..., MemoryGraph]) -> None:
    g = make_graph(decay=None)
    with pytest.raises(nomem.ConfigError):
        await g.arun_decay()


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
