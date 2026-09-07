"""Phase 0 contract: no hard-delete path is exposed anywhere.

`purge_user` is the one deliberate exception (ROADMAP Q6): it exists on
`BaseBackend` and the bundled backends so GDPR right-to-forget can be built as a
plugin, and it must never become reachable from the public graph API.
"""

from __future__ import annotations

import pytest
from nomem import MemoryGraph
from nomem.backends.base import BaseBackend
from nomem.backends.neo4j import Neo4jBackend
from nomem.backends.postgres import PostgresBackend
from nomem.backends.sqlite import SQLiteBackend
from nomem.core.graph import GraphCRUD
from nomem.exceptions import NotSupportedError

FORBIDDEN = {"delete_node", "delete_edge", "hard_delete", "drop_node", "remove_node"}


def test_backend_interface_has_no_delete() -> None:
    assert FORBIDDEN.isdisjoint(dir(BaseBackend))
    # retirement is the only removal mechanism
    assert "retire_node" in dir(BaseBackend)
    assert "retire_edge" in dir(BaseBackend)


def test_crud_layer_has_no_delete() -> None:
    assert FORBIDDEN.isdisjoint(dir(GraphCRUD))


def test_public_api_has_no_delete() -> None:
    assert FORBIDDEN.isdisjoint(dir(MemoryGraph))


def test_purge_user_lives_on_backends_only() -> None:
    """The fence: a backend handle has it, the public graph API does not."""
    assert "purge_user" in dir(BaseBackend)
    for backend in (SQLiteBackend, PostgresBackend, Neo4jBackend):
        assert "purge_user" in dir(backend)
    assert "purge_user" not in dir(GraphCRUD)
    assert "purge_user" not in dir(MemoryGraph)


async def test_purge_user_default_body_raises() -> None:
    """Non-abstract: a custom backend need not implement it, but must not no-op."""

    async def _stub(self: object, *args: object, **kwargs: object) -> None:
        raise NotImplementedError

    # A third-party backend that implements the 13 required methods and nothing else.
    minimal = type("Minimal", (BaseBackend,), dict.fromkeys(BaseBackend.__abstractmethods__, _stub))
    assert "purge_user" not in BaseBackend.__abstractmethods__

    with pytest.raises(NotSupportedError):
        await minimal().purge_user("u1")
