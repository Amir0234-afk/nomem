"""Contract: the `nomem.plugins` entry point — the paid tier's only attachment point.

If this breaks, a downstream package has to monkeypatch or fork, which is the
one failure mode the extension surface exists to prevent (docs/stability.md).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import nomem.plugins as plugins_mod
import pytest
from nomem import MemoryGraph
from nomem.exceptions import ConfigError


class FakeEntryPoint:
    """Stands in for an `importlib.metadata.EntryPoint`."""

    def __init__(self, name: str, target: Any) -> None:
        self.name = name
        self._target = target

    def load(self) -> Any:
        if isinstance(self._target, Exception):
            raise self._target
        return self._target


class ExportPlugin:
    namespace = "pro"

    def __init__(self) -> None:
        self.attached_to: MemoryGraph | None = None

    def attach(self, graph: MemoryGraph) -> object:
        self.attached_to = graph
        return self


@pytest.fixture
def install_entry_points(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    def _install(*entries: FakeEntryPoint) -> None:
        monkeypatch.setattr(
            plugins_mod, "entry_points", lambda group=None: list(entries) if group else []
        )

    return _install


def test_plugin_mounts_at_namespace(
    make_graph: Callable[..., MemoryGraph], install_entry_points: Callable[..., None]
) -> None:
    install_entry_points(FakeEntryPoint("pro", ExportPlugin))
    g = make_graph()

    assert isinstance(g.pro, ExportPlugin)  # type: ignore[attr-defined]
    assert g.plugins == {"pro": g.pro}  # type: ignore[attr-defined]
    # attach() sees a fully wired graph, not a half-built one.
    assert g.pro.attached_to is g  # type: ignore[attr-defined]
    assert g.pro.attached_to.backend is g.backend  # type: ignore[attr-defined]


def test_plugins_false_skips_discovery(
    make_graph: Callable[..., MemoryGraph], install_entry_points: Callable[..., None]
) -> None:
    install_entry_points(FakeEntryPoint("pro", ExportPlugin))
    g = make_graph(plugins=False)
    assert g.plugins == {}
    assert not hasattr(g, "pro")


def test_no_plugins_installed_is_empty(make_graph: Callable[..., MemoryGraph]) -> None:
    assert make_graph().plugins == {}


def test_namespace_collision_raises(
    make_graph: Callable[..., MemoryGraph], install_entry_points: Callable[..., None]
) -> None:
    class Shadow(ExportPlugin):
        namespace = "backend"  # would silently replace graph.backend

    install_entry_points(FakeEntryPoint("shadow", Shadow))
    with pytest.raises(ConfigError, match="shadows"):
        make_graph()


def test_invalid_namespace_raises(
    make_graph: Callable[..., MemoryGraph], install_entry_points: Callable[..., None]
) -> None:
    class Bad(ExportPlugin):
        namespace = "not an identifier"

    install_entry_points(FakeEntryPoint("bad", Bad))
    with pytest.raises(ConfigError, match="identifier"):
        make_graph()


def test_import_failure_propagates(
    make_graph: Callable[..., MemoryGraph], install_entry_points: Callable[..., None]
) -> None:
    """A broken plugin is surfaced, never swallowed (AGENT.md hard rule)."""
    install_entry_points(FakeEntryPoint("broken", ImportError("no module named nomem_pro")))
    with pytest.raises(ImportError):
        make_graph()


def test_attach_failure_propagates(
    make_graph: Callable[..., MemoryGraph], install_entry_points: Callable[..., None]
) -> None:
    class Exploding(ExportPlugin):
        def attach(self, graph: MemoryGraph) -> object:
            raise RuntimeError("incompatible core version")

    install_entry_points(FakeEntryPoint("boom", Exploding))
    with pytest.raises(RuntimeError, match="incompatible"):
        make_graph()
