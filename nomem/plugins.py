"""Plugin discovery — the sanctioned way to add capability to a graph.

The adapter registries swap *components* (backend, embedder, LLM). Some
features are not adapter-shaped: graph export/import, analytics, cross-user
queries are methods *on the graph*. Without an attach hook, a package that
wants to add one has to monkeypatch or fork — and the no-fork model is the
whole reason this surface exists (see ``docs/stability.md``).

A plugin is any object with a ``namespace`` and an ``attach(graph)`` method,
advertised through the ``nomem.plugins`` entry-point group::

    # in the plugin package's pyproject.toml
    [project.entry-points."nomem.plugins"]
    pro = "nomem_pro:ProPlugin"

:class:`~nomem.MemoryGraph` discovers those on construction, calls
``attach(self)``, and mounts whatever it returns at ``graph.<namespace>``::

    graph.pro.export("dump.jsonl")

Failures are never swallowed: a plugin that cannot be imported, instantiated,
or attached raises out of ``MemoryGraph(...)``. Pass ``plugins=False`` to skip
discovery entirely.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .exceptions import ConfigError

if TYPE_CHECKING:
    from .graph import MemoryGraph

#: The entry-point group nomem scans. Part of the public contract.
ENTRY_POINT_GROUP = "nomem.plugins"


@runtime_checkable
class Plugin(Protocol):
    """What an entry point must resolve to (after instantiation)."""

    namespace: str

    def attach(self, graph: MemoryGraph) -> object:
        """Return the object to mount at ``graph.<namespace>``."""
        ...


def discover_plugins() -> list[Plugin]:
    """Instantiate every plugin advertised under ``nomem.plugins``.

    Import and construction errors propagate — a plugin that is installed but
    broken is a bug to surface, not to hide.
    """
    found: list[Plugin] = []
    for entry in entry_points(group=ENTRY_POINT_GROUP):
        obj: Any = entry.load()
        plugin = obj() if isinstance(obj, type) else obj
        namespace = getattr(plugin, "namespace", None) or entry.name
        if not isinstance(namespace, str) or not namespace.isidentifier():
            raise ConfigError(
                f"plugin {entry.name!r} has an invalid namespace {namespace!r}: "
                "it must be a valid Python identifier"
            )
        plugin.namespace = namespace
        if not callable(getattr(plugin, "attach", None)):
            raise ConfigError(f"plugin {entry.name!r} has no callable attach(graph)")
        found.append(plugin)
    return found


def attach_plugins(graph: MemoryGraph) -> dict[str, object]:
    """Attach every discovered plugin to ``graph``; return what was mounted.

    Raises :class:`~nomem.exceptions.ConfigError` if a namespace would shadow an
    existing attribute — silently replacing ``graph.backend`` is not a feature.
    """
    mounted: dict[str, object] = {}
    for plugin in discover_plugins():
        namespace = plugin.namespace
        if hasattr(graph, namespace):
            raise ConfigError(
                f"plugin namespace {namespace!r} shadows an existing "
                f"{type(graph).__name__} attribute"
            )
        mounted[namespace] = plugin.attach(graph)
        setattr(graph, namespace, mounted[namespace])
    return mounted


__all__ = ["ENTRY_POINT_GROUP", "Plugin", "attach_plugins", "discover_plugins"]
