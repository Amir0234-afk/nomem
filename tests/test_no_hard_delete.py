"""Phase 0 contract: no hard-delete path is exposed anywhere."""

from __future__ import annotations

from nomem import MemoryGraph
from nomem.backends.base import BaseBackend
from nomem.core.graph import GraphCRUD

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
