"""Phase 0 contract: adapter ABCs and their stubs."""

from __future__ import annotations

from typing import Any

import pytest
from nomem.backends import BACKEND_REGISTRY, resolve_backend
from nomem.backends.base import BaseBackend
from nomem.embedders import EMBEDDER_REGISTRY, CallableEmbedder, resolve_embedder
from nomem.embedders.base import BaseEmbedder
from nomem.exceptions import ConfigError

# The frozen 0.1.0 contract: 14 methods, 13 abstract + purge_user.
# See docs/stability.md — no new abstract method before 0.2.0.
BACKEND_METHODS = [
    "create_node",
    "update_node",
    "retire_node",
    "get_node",
    "list_nodes",
    "upsert_edge",
    "retire_edge",
    "get_edge",
    "list_edges",
    "vector_search",
    "traverse",
    "cross_reference",
    "run_decay",
    "purge_user",
]


def test_base_classes_are_abstract() -> None:
    with pytest.raises(TypeError):
        BaseBackend()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        BaseEmbedder()  # type: ignore[abstract]


def test_backend_interface_methods_present() -> None:
    for name in BACKEND_METHODS:
        assert callable(getattr(BaseBackend, name))
    assert len(BACKEND_METHODS) == 14
    # purge_user is public but optional — everything else must be implemented.
    assert BaseBackend.__abstractmethods__ == frozenset(BACKEND_METHODS) - {"purge_user"}


async def test_sqlite_backend_constructs_and_responds() -> None:
    sqlite = resolve_backend("sqlite", {"user_id": "u1", "path": ":memory:"})
    assert isinstance(sqlite, BaseBackend)
    assert await sqlite.get_node("missing") is None  # implemented, not a stub


def test_networked_backends_require_connection_details() -> None:
    from nomem.exceptions import BackendError

    with pytest.raises(BackendError):
        resolve_backend("postgres", {"user_id": "u1"})  # missing dsn
    with pytest.raises(BackendError):
        resolve_backend("neo4j", {"user_id": "u1"})  # missing uri


def test_all_backends_registered() -> None:
    assert set(BACKEND_REGISTRY) == {"sqlite", "postgres", "neo4j"}


async def test_embedder_registry_entries_construct() -> None:
    assert resolve_embedder("nomic").dimensions == 768
    assert resolve_embedder("openai").dimensions == 1536
    assert set(EMBEDDER_REGISTRY) == {"nomic", "openai"}


async def test_openai_embedder(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plain HTTP, no `openai` extra — exercised against a stubbed transport."""
    import nomem.embedders.openai as openai_mod
    from nomem.embedders.openai import OpenAIEmbedder
    from nomem.exceptions import EmbedderError

    seen: dict[str, Any] = {}

    async def fake_post(url: str, payload: Any, **kwargs: Any) -> dict[str, Any]:
        seen["url"] = url
        seen["payload"] = payload
        seen["headers"] = kwargs.get("headers")
        # Returned out of order on purpose: `index` is what orders the result.
        vectors = [[1.0, 0.0], [0.0, 1.0]][: len(payload["input"])]
        return {
            "data": [
                {"index": i, "embedding": v} for i, v in reversed(list(enumerate(vectors)))
            ]
        }

    monkeypatch.setattr(openai_mod, "post_json", fake_post)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    emb = OpenAIEmbedder(api_key="sk-test")
    assert await emb.embed_batch(["a", "b"]) == [[1.0, 0.0], [0.0, 1.0]]
    assert seen["url"] == "https://api.openai.com/v1/embeddings"
    assert seen["payload"] == {"model": "text-embedding-3-small", "input": ["a", "b"]}
    assert seen["headers"] == {"Authorization": "Bearer sk-test"}
    assert await emb.embed_batch([]) == []

    # A truncated size is passed through to the API; the key may come from the env.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    await OpenAIEmbedder(dimensions=256).embed("a")
    assert seen["payload"]["dimensions"] == 256
    assert seen["headers"] == {"Authorization": "Bearer sk-env"}

    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(EmbedderError, match="API key"):
        await OpenAIEmbedder().embed("a")


def test_openai_embedder_rejects_bad_dimensions() -> None:
    from nomem.embedders.openai import OpenAIEmbedder
    from nomem.exceptions import EmbedderError

    with pytest.raises(EmbedderError):
        OpenAIEmbedder(dimensions=99_999)
    with pytest.raises(EmbedderError, match="truncated"):
        OpenAIEmbedder(model="text-embedding-ada-002", dimensions=256)


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
