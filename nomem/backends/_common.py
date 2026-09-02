"""Helpers shared by the concrete backends.

Keeps bi-temporal filtering and graph traversal identical across adapters, so
switching backends never changes what a query returns.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..models import Edge, Node, SubGraph


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def iso(dt: datetime) -> str:
    """Serialize a datetime as a UTC ISO-8601 string (lexically comparable)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def parse_ts(value: str | datetime | None) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def active_at(record: Node | Edge, as_of: datetime) -> bool:
    """True if ``record`` was recorded by ``as_of`` and its window contains it."""
    as_of = aware(as_of)
    if aware(record.created_at) > as_of or aware(record.valid_from) > as_of:
        return False
    return record.valid_to is None or aware(record.valid_to) > as_of


def bfs_subgraph(
    nodes_by_id: dict[str, Node],
    edges: list[Edge],
    seed_ids: list[str],
    hops: int,
) -> SubGraph:
    """Expand ``hops`` undirected edge-steps out from ``seed_ids``.

    ``nodes_by_id`` / ``edges`` must already be filtered to the visible set
    (active-only, or ``as_of`` a timestamp).
    """
    frontier = {sid for sid in seed_ids if sid in nodes_by_id}
    visited = set(frontier)
    kept_edges: dict[str, Edge] = {}

    for _ in range(max(hops, 0)):
        next_frontier: set[str] = set()
        for edge in edges:
            if edge.source_id in frontier and edge.target_id in nodes_by_id:
                kept_edges[edge.id] = edge
                if edge.target_id not in visited:
                    next_frontier.add(edge.target_id)
            if edge.target_id in frontier and edge.source_id in nodes_by_id:
                kept_edges[edge.id] = edge
                if edge.source_id not in visited:
                    next_frontier.add(edge.source_id)
        if not next_frontier:
            break
        visited |= next_frontier
        frontier = next_frontier

    return SubGraph(
        nodes=[nodes_by_id[nid] for nid in visited if nid in nodes_by_id],
        edges=list(kept_edges.values()),
        metadata={"hops": max(hops, 0), "seed_node_ids": list(seed_ids)},
    )
