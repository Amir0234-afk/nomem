"""Phase 0 contract: the canonical dataclasses have the AGENT.md shape."""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime

from nomem.models import DecayResult, Edge, IngestReceipt, Node, ResolutionOutcome, SubGraph

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _field_names(cls: type) -> set[str]:
    return {f.name for f in fields(cls)}


def test_node_fields_match_spec() -> None:
    assert _field_names(Node) == {
        "id",
        "user_id",
        "type",
        "label",
        "embedding",
        "importance",
        "access_count",
        "last_accessed_at",
        "created_at",
        "valid_from",
        "valid_to",
        "superseded_by",
        "resolution_source",
        "metadata",
    }


def test_edge_fields_match_spec() -> None:
    assert _field_names(Edge) == {
        "id",
        "user_id",
        "source_id",
        "target_id",
        "relation",
        "weight",
        "created_at",
        "valid_from",
        "valid_to",
        "superseded_by",
        "metadata",
    }


def test_node_constructs_with_defaults() -> None:
    node = Node(
        id="n1",
        user_id="u1",
        type="entity",
        label="Kira",
        embedding=[0.0, 1.0],
        importance=0.5,
        access_count=0,
        last_accessed_at=NOW,
        created_at=NOW,
        valid_from=NOW,
    )
    assert node.valid_to is None  # active
    assert node.superseded_by is None
    assert node.resolution_source == "llm"
    assert node.metadata == {}


def test_subgraph_defaults_empty() -> None:
    sg = SubGraph()
    assert sg.nodes == [] and sg.edges == [] and sg.metadata == {}


def test_receipts_default_empty() -> None:
    r = IngestReceipt()
    assert r.nodes_created == [] and r.ambiguous_resolutions == [] and r.queued_writes == []
    d = DecayResult(ran_at=NOW)
    assert d.nodes_scored == 0 and d.nodes_pruned == []


def test_resolution_outcome_shape() -> None:
    names = _field_names(ResolutionOutcome)
    assert {"resolved_node_id", "confidence", "resolution_source", "ambiguous"} <= names
