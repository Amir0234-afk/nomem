"""Phase 0 contract: adapter ABCs and their stubs."""

from __future__ import annotations

import pytest
from nomem.backends import BACKEND_REGISTRY, resolve_backend
from nomem.backends.base import BaseBackend
from nomem.embedders import EMBEDDER_REGISTRY, CallableEmbedder, resolve_embedder
from nomem.embedders.base import BaseEmbedder
from nomem.exceptions import ConfigError

BACKEND_METHODS = [
    "create_node",
    "update_node",
    "retire_node",
    "get_node",
    "upsert_edge",
    "retire_edge",
    "vector_search",
    "traverse",
    "cross_reference",
    "run_decay",
]


def test_base_classes_are_abstract() -> None:
    with pytest.raises(TypeError):
        BaseBackend()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        BaseEmbedder()  # type: ignore[abstract]


def test_backend_interface_methods_present() -> None:
    for name in BACKEND_METHODS:
        assert callable(getattr(BaseBackend, name))


@pytest.mark.parametrize("name", ["postgres", "neo4j"])
async def test_unimplemented_backend_stubs_subclass_and_raise(name: str) -> None:
    backend = resolve_backend(name)
    assert isinstance(backend, BaseBackend)
    with pytest.raises(NotImplementedError):
        await backend.get_node("x")


async def test_sqlite_backend_is_live() -> None:
    backend = resolve_backend("sqlite", {"user_id": "u1", "path": ":memory:"})
    assert isinstance(backend, BaseBackend)
    assert await backend.get_node("missing") is None  # no NotImplementedError


def test_all_backends_registered() -> None:
    assert set(BACKEND_REGISTRY) == {"sqlite", "postgres", "neo4j"}


async def test_embedder_registry_entries_construct() -> None:
    assert resolve_embedder("nomic").dimensions == 768
    assert resolve_embedder("openai").dimensions > 0
    with pytest.raises(NotImplementedError):
        await resolve_embedder("openai").embed("hello")  # requires the 'openai' extra
    assert set(EMBEDDER_REGISTRY) == {"nomic", "openai"}


def test_resolve_backend_rejects_unknown() -> None:
    with pytest.raises(ConfigError):
        resolve_backend("mongodb")


async def test_callable_embedder_works_end_to_end() -> None:
    calls: list[str] = []

    def fake(text: str) -> list[float]:
        calls.append(text)
        return [float(len(text))] * 3

    emb = CallableEmbedder(fake, dimensions=3)
    assert await emb.embed("abc") == [3.0, 3.0, 3.0]
    assert await emb.embed_batch(["a", "bb"]) == [[1.0, 1.0, 1.0], [2.0, 2.0, 2.0]]
    assert calls == ["abc", "a", "bb"]


async def test_callable_embedder_accepts_async_fn() -> None:
    async def fake(text: str) -> list[float]:
        return [1.0, 2.0]

    emb = CallableEmbedder(fake, dimensions=2)
    assert await emb.embed("x") == [1.0, 2.0]


def test_resolve_embedder_callable_needs_dimensions() -> None:
    with pytest.raises(ConfigError):
        resolve_embedder(lambda t: [0.0])
