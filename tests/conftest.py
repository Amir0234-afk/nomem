"""Shared test fakes and fixtures.

The fakes let the full ingest/retrieve pipeline run hermetically — no Ollama, no
network. Live coverage against a real Ollama lives in ``test_pipeline_live.py``.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable
from typing import Any

import pytest
from nomem.embedders.base import BaseEmbedder
from nomem.graph import MemoryGraph
from nomem.llms.base import BaseLLM
from nomem.models import Vector

_DIMS = 24


def _embed(text: str, dims: int = _DIMS) -> Vector:
    """Deterministic bag-of-words hash embedding; similar text -> similar vector."""
    vec = [0.0] * dims
    tokens = [t for t in text.lower().replace("_", " ").split() if t]
    for tok in tokens or [text.lower()]:
        h = int.from_bytes(hashlib.md5(tok.encode()).digest()[:4], "big")
        vec[h % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class FakeEmbedder(BaseEmbedder):
    def __init__(self, dims: int = _DIMS) -> None:
        self._dims = dims

    async def embed(self, text: str) -> Vector:
        return _embed(text, self._dims)

    async def embed_batch(self, texts: list[str]) -> list[Vector]:
        return [_embed(t, self._dims) for t in texts]

    @property
    def dimensions(self) -> int:
        return self._dims


class FakeLLM(BaseLLM):
    """Returns a canned dict, or the result of a ``prompt -> dict`` callable."""

    def __init__(self, response: dict[str, Any] | Callable[[str], dict[str, Any]]) -> None:
        self._response = response
        self.calls: list[str] = []

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(prompt)
        if callable(self._response):
            return self._response(prompt)
        return self._response


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def make_graph() -> Callable[..., MemoryGraph]:
    """Factory for an in-memory MemoryGraph wired to fakes."""

    def _factory(
        *,
        llm_response: dict[str, Any] | Callable[[str], dict[str, Any]] | None = None,
        user_id: str = "u1",
        **overrides: Any,
    ) -> MemoryGraph:
        empty: dict[str, Any] = {"entities": [], "relations": []}
        return MemoryGraph(
            user_id=user_id,
            backend="sqlite",
            backend_options={"path": ":memory:"},
            embedder=FakeEmbedder(),
            llm=FakeLLM(llm_response if llm_response is not None else empty),
            **overrides,
        )

    return _factory
