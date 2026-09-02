"""Phase 2: decay scoring + pruning."""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from nomem import MemoryGraph
from nomem.config import DecayConfig
from nomem.core.decay import score_node
from nomem.models import Node

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _node(*, access_count: int = 0, days_stale: float = 0.0) -> Node:
    at = NOW - timedelta(days=days_stale)
    return Node(
        id="n",
        user_id="u1",
        type="entity",
        label="x",
        embedding=[1.0],
        importance=0.5,
        access_count=access_count,
        last_accessed_at=at,
        created_at=at,
        valid_from=at,
    )


def test_combined_formula_matches_agent_md() -> None:
    cfg = DecayConfig(alpha=0.6, beta=0.4, lambda_=0.1, mode="combined")
    node = _node(access_count=9, days_stale=10)
    expected = 0.6 * math.log(1 + 9) + 0.4 * math.exp(-0.1 * 10)
    assert score_node(node, cfg, NOW) == pytest.approx(expected)


def test_access_mode_drops_recency_term() -> None:
    cfg = DecayConfig(mode="access")
    node = _node(access_count=4, days_stale=30)
    assert score_node(node, cfg, NOW) == pytest.approx(cfg.alpha * math.log(5))


def test_time_mode_drops_access_term() -> None:
    cfg = DecayConfig(mode="time")
    node = _node(access_count=100, days_stale=5)
    assert score_node(node, cfg, NOW) == pytest.approx(cfg.beta * math.exp(-cfg.lambda_ * 5))


def test_none_mode_leaves_importance_untouched() -> None:
    node = _node(access_count=3, days_stale=3)
    assert score_node(node, DecayConfig(mode=None), NOW) == node.importance


def test_recency_decays_monotonically() -> None:
    cfg = DecayConfig(mode="time")
    scores = [score_node(_node(days_stale=d), cfg, NOW) for d in (0, 10, 100, 1000)]
    assert scores == sorted(scores, reverse=True)


async def test_decay_pass_updates_stored_importance(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph(
        llm_response={"entities": [{"label": "Kira", "type": "entity"}], "relations": []},
        decay="time",
    )
    receipt = await g.aingest("x", "Kira waved.")
    node_id = receipt.nodes_created[0]
    assert (await g.backend.get_node(node_id)).importance == 1.0  # type: ignore[union-attr]

    result = await g.arun_decay()
    scored = await g.backend.get_node(node_id)
    assert scored is not None
    assert scored.importance == pytest.approx(result.scores[node_id])
    assert scored.importance < 1.0  # decayed from the initial value


async def test_pruning_retires_below_floor_without_deleting(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph(
        llm_response={"entities": [{"label": "Kira", "type": "entity"}], "relations": []},
        decay="time",
    )
    receipt = await g.aingest("x", "Kira waved.")
    node_id = receipt.nodes_created[0]

    # floor above any achievable score -> guaranteed prune candidate
    result = await g.arun_decay(config={"importance_floor": 5.0, "pruning": True})
    assert node_id in result.nodes_pruned

    node = await g.backend.get_node(node_id)
    assert node is not None and node.valid_to is not None  # retired, not deleted
    assert (await g.aretrieve("Kira")).nodes == []  # gone from retrieval


async def test_pruning_off_by_default(make_graph: Callable[..., MemoryGraph]) -> None:
    g = make_graph(
        llm_response={"entities": [{"label": "Kira", "type": "entity"}], "relations": []},
        decay="time",
    )
    receipt = await g.aingest("x", "Kira waved.")
    node_id = receipt.nodes_created[0]
    result = await g.arun_decay(config={"importance_floor": 5.0})  # pruning defaults False
    assert node_id in result.prune_candidates
    assert result.nodes_pruned == []
    assert (await g.backend.get_node(node_id)) is not None
