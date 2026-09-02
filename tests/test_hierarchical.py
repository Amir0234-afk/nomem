"""Phase 2: hierarchical retrieval — core index + situation sub-index routing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from nomem import MemoryGraph
from nomem.config import RetrievalConfig
from nomem.core.retrieval import Retriever
from nomem.models import Node

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _node(nid: str, vec: list[float], tags: list[str], importance: float = 1.0) -> Node:
    return Node(
        id=nid,
        user_id="u1",
        type="entity",
        label=nid,
        embedding=vec,
        importance=importance,
        access_count=0,
        last_accessed_at=NOW,
        created_at=NOW,
        valid_from=NOW,
        metadata={"context": tags},
    )


@pytest.fixture
def nodes() -> list[Node]:
    return [
        _node("work_a", [1.0, 0.0, 0.0], ["work"]),
        _node("work_b", [0.9, 0.1, 0.0], ["work"]),
        _node("home_a", [0.0, 1.0, 0.0], ["home"]),
        _node("home_b", [0.0, 0.9, 0.1], ["home"]),
        _node("untagged", [0.0, 0.0, 1.0], []),
    ]


@pytest.fixture
def retriever() -> Retriever:
    return Retriever(backend=object(), embedder=object())  # type: ignore[arg-type]


def test_deterministic_routes_by_tag(retriever: Retriever, nodes: list[Node]) -> None:
    cfg = RetrievalConfig(mode="hierarchical", sub_index_strategy="deterministic")
    sub, used = retriever._route_sub_index(nodes, [1.0, 0.0, 0.0], cfg, ["work"])
    assert {n.id for n in sub} == {"work_a", "work_b"}
    assert used == "work"


def test_deterministic_no_match_returns_empty(retriever: Retriever, nodes: list[Node]) -> None:
    cfg = RetrievalConfig(mode="hierarchical", sub_index_strategy="deterministic")
    sub, used = retriever._route_sub_index(nodes, [1.0, 0.0, 0.0], cfg, ["vacation"])
    assert sub == [] and used is None


def test_semantic_routes_by_centroid(retriever: Retriever, nodes: list[Node]) -> None:
    cfg = RetrievalConfig(mode="hierarchical", sub_index_strategy="semantic")
    # query close to the "home" cluster
    sub, used = retriever._route_sub_index(nodes, [0.0, 1.0, 0.0], cfg, None)
    assert used == "home"
    assert {n.id for n in sub} == {"home_a", "home_b"}


def test_hybrid_prefers_deterministic_then_falls_back(
    retriever: Retriever, nodes: list[Node]
) -> None:
    cfg = RetrievalConfig(mode="hierarchical", sub_index_strategy="hybrid")
    # context given and matches -> deterministic (query points at "home", tag says "work")
    _, used = retriever._route_sub_index(nodes, [0.0, 1.0, 0.0], cfg, ["work"])
    assert used == "work"
    # no context -> semantic fallback picks the cluster nearest the query
    _, used = retriever._route_sub_index(nodes, [1.0, 0.0, 0.0], cfg, None)
    assert used == "work"


# --- integration through MemoryGraph -----------------------------------


@pytest.fixture
def hierarchical_graph(make_graph: Callable[..., MemoryGraph]) -> MemoryGraph:
    return make_graph(
        llm_response=lambda p: (
            {"entities": [{"label": "quarterly report", "type": "fact"}], "relations": []}
            if "report" in p
            else {"entities": [{"label": "garden tomatoes", "type": "fact"}], "relations": []}
        ),
        retrieval_config=RetrievalConfig(mode="hierarchical", core_index_size_floor=1),
    )


async def test_ingest_tags_nodes_with_context(hierarchical_graph: MemoryGraph) -> None:
    receipt = await hierarchical_graph.aingest(
        "x", "The quarterly report is due Friday.", context=["work"]
    )
    node = await hierarchical_graph.backend.get_node(receipt.nodes_created[0])
    assert node is not None and node.metadata["context"] == ["work"]


async def test_hierarchical_retrieval_routes_to_context(
    hierarchical_graph: MemoryGraph,
) -> None:
    await hierarchical_graph.aingest("x", "The quarterly report is due Friday.", context=["work"])
    await hierarchical_graph.aingest("x", "The garden tomatoes are ripe.", context=["home"])

    result = await hierarchical_graph.aretrieve("report", context=["work"])
    assert result.metadata["retrieval_mode"] == "hierarchical"
    assert result.metadata["sub_index_used"] == "work"
    labels = {n.label for n in result.nodes}
    assert "quarterly report" in labels
    assert "garden tomatoes" not in labels


async def test_context_tags_merge_on_reingest(hierarchical_graph: MemoryGraph) -> None:
    r1 = await hierarchical_graph.aingest(
        "x", "The quarterly report is due Friday.", context=["work"]
    )
    node_id = r1.nodes_created[0]
    await hierarchical_graph.aingest(
        "x", "The quarterly report is due Friday.", context=["urgent"]
    )
    node = await hierarchical_graph.backend.get_node(node_id)
    assert node is not None and node.metadata["context"] == ["urgent", "work"]
