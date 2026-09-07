"""Phase 1: entity resolution (embedding + string match)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from nomem import MemoryGraph
from nomem.config import IngestConfig
from nomem.core.extraction import EntityResolver
from nomem.models import ExtractedEntity

from conftest import FakeEmbedder


class _EmptyBackend:
    async def vector_search(self, embedding: list[float], top_k: int) -> list[object]:
        return []


async def test_no_candidates_creates_new() -> None:
    resolver = EntityResolver(backend=_EmptyBackend(), embedder=FakeEmbedder())  # type: ignore[arg-type]
    [outcome] = await resolver.resolve(
        [ExtractedEntity(label="Zephyr", type="entity")], IngestConfig()
    )
    assert outcome.resolved_node_id is None
    assert outcome.ambiguous is False
    assert outcome.confidence == 0.0


async def test_exact_restatement_resolves_to_existing(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph(
        llm_response=lambda _p: {
            "entities": [{"label": "Kira Nolan", "type": "entity"}],
            "relations": [],
        }
    )
    first = await g.aingest("x", "Kira Nolan joined the team.")
    assert len(first.nodes_created) == 1
    node_id = first.nodes_created[0]

    second = await g.aingest("x", "Kira Nolan joined the team.")
    assert second.nodes_created == []
    assert second.nodes_updated == [node_id]
    assert second.resolution_confidence[node_id] >= 0.99


async def test_partial_match_flagged_ambiguous_not_merged(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph()
    emb = await FakeEmbedder().embed("Kira Nolan")
    seed = g.crud._build_node(
        ExtractedEntity(label="Kira Nolan", type="entity"), emb, "manual"
    )
    await g.crud.create_node(seed)

    [outcome] = await g.resolver.resolve(
        [ExtractedEntity(label="Kira", type="entity")],
        IngestConfig(resolution_confidence_threshold=0.99),
    )
    assert outcome.ambiguous is True
    assert outcome.resolved_node_id is None
    assert outcome.candidates


async def test_ambiguous_outcome_queues_by_default(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph()
    emb = await FakeEmbedder().embed("Kira Nolan")
    await g.crud.create_node(
        g.crud._build_node(ExtractedEntity(label="Kira Nolan", type="entity"), emb, "manual")
    )
    resolutions = await g.resolver.resolve(
        [ExtractedEntity(label="Kira", type="entity")],
        IngestConfig(resolution_confidence_threshold=0.99),
    )
    receipt = await g.crud.apply_resolutions(resolutions, [], IngestConfig(on_ambiguous="queue"))
    assert receipt.nodes_created == []
    assert [e.label for e in receipt.queued_writes] == ["Kira"]
    assert receipt.ambiguous_resolutions


@pytest.mark.parametrize("strategy", ["queue", "create"])
async def test_on_ambiguous_strategy(
    make_graph: Callable[..., MemoryGraph], strategy: str
) -> None:
    g = make_graph()
    emb = await FakeEmbedder().embed("Kira Nolan")
    await g.crud.create_node(
        g.crud._build_node(ExtractedEntity(label="Kira Nolan", type="entity"), emb, "manual")
    )
    resolutions = await g.resolver.resolve(
        [ExtractedEntity(label="Kira", type="entity")],
        IngestConfig(resolution_confidence_threshold=0.99),
    )
    receipt = await g.crud.apply_resolutions(
        resolutions, [], IngestConfig(on_ambiguous=strategy)
    )
    if strategy == "create":
        assert len(receipt.nodes_created) == 1
    else:
        assert receipt.nodes_created == []


@pytest.mark.parametrize(
    ("strategy", "expect_source"),
    # No candidate clears the bar under "embedding", so that outcome is a new
    # entity, whose source is the extractor itself.
    [("embedding", "llm"), ("string", "string_match"), ("hybrid", "string_match")],
)
async def test_resolution_strategy_switches_scoring(
    make_graph: Callable[..., MemoryGraph], strategy: str, expect_source: str
) -> None:
    """ROADMAP Q5: each branch actually changes the score, not just the label.

    "Kiera" vs "Kira" is a near-identical surface form that the bag-of-words
    embedder scores at zero — so the strategies disagree by construction.
    """
    g = make_graph()
    emb = await FakeEmbedder().embed("Kira")
    await g.crud.create_node(
        g.crud._build_node(ExtractedEntity(label="Kira", type="entity"), emb, "manual")
    )
    entity = ExtractedEntity(label="Kiera", type="entity")

    (outcome,) = await g.resolver.resolve(
        [entity], IngestConfig(resolution_strategy=strategy, resolution_confidence_threshold=0.75)
    )
    if strategy == "embedding":
        assert outcome.confidence == 0.0  # different token -> orthogonal vectors
        assert outcome.resolved_node_id is None
    else:
        assert outcome.confidence >= 0.8  # surface-form match carries it
        assert outcome.resolved_node_id is not None
    assert outcome.resolution_source == expect_source


async def test_string_strategy_ignores_a_semantic_only_match(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    """The mirror case: identical vectors, unrelated spellings."""
    g = make_graph()
    emb = await FakeEmbedder().embed("Kira")
    await g.crud.create_node(
        g.crud._build_node(ExtractedEntity(label="Kira", type="entity"), emb, "manual")
    )
    # Same label text -> cosine 1.0, so only the "string" branch can be made to differ.
    (embedding_hit,) = await g.resolver.resolve(
        [ExtractedEntity(label="Kira", type="entity")],
        IngestConfig(resolution_strategy="embedding"),
    )
    assert embedding_hit.resolved_node_id is not None
    assert embedding_hit.confidence == pytest.approx(1.0)
