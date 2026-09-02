"""Backend adapter interface.

Any object implementing :class:`BaseBackend` is a valid nomem storage backend —
devs can write their own. Storage is an adapter boundary: swapping a backend
touches nothing else (AGENT.md "Backend and embedder agnosticism").

Contract notes that apply to every implementation:

* **Bi-temporal, no hard delete.** ``retire_node`` / ``retire_edge`` set
  ``valid_to = now`` and, when given, ``superseded_by``. Records are never
  removed.
* **``as_of`` honesty.** ``get_node`` and ``traverse`` with ``as_of=`` must
  return the graph as it was *known* at that instant.
* **User scoping.** Every record carries ``user_id``; a backend instance must
  never leak rows across users.
* **Async.** Every method is a coroutine. Blocking drivers must be run in a
  thread/executor by the adapter.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from ..config import DecayConfig
from ..models import DecayResult, Edge, Node, SubGraph, Vector


class BaseBackend(ABC):
    """Abstract storage adapter. See module docstring for the shared contract."""

    @abstractmethod
    async def create_node(self, node: Node) -> Node:
        """Persist a new node and return the stored record."""

    @abstractmethod
    async def update_node(self, node_id: str, updates: dict[str, Any]) -> Node:
        """Apply a partial update to an active node and return the new record."""

    @abstractmethod
    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node:
        """Retire a node: set ``valid_to = now`` (+ ``superseded_by``). Never delete."""

    @abstractmethod
    async def get_node(self, node_id: str, as_of: datetime | None = None) -> Node | None:
        """Return a node as known at ``as_of`` (or now), or ``None`` if absent."""

    @abstractmethod
    async def upsert_edge(self, edge: Edge) -> Edge:
        """Create the edge, or bump ``weight`` / metadata if an active one exists."""

    @abstractmethod
    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge:
        """Retire an edge: set ``valid_to = now`` (+ ``superseded_by``). Never delete."""

    @abstractmethod
    async def vector_search(self, embedding: Vector, top_k: int) -> list[Node]:
        """Return the ``top_k`` active nodes most similar to ``embedding``."""

    @abstractmethod
    async def traverse(
        self, seed_ids: list[str], hops: int, as_of: datetime | None = None
    ) -> SubGraph:
        """Expand ``hops`` edges out from ``seed_ids``; honor ``as_of`` if given."""

    @abstractmethod
    async def cross_reference(self, node: Node, threshold: float) -> list[tuple[Node, float]]:
        """Return active nodes whose similarity to ``node`` is >= ``threshold``."""

    @abstractmethod
    async def run_decay(self, config: DecayConfig) -> DecayResult:
        """Run one decay/pruning pass over this user's nodes per ``config``."""
