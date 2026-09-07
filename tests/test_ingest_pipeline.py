"""Phase 1: full ingest -> retrieve behavior (negation, cross-ref, budget, as_of)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime

from nomem import MemoryGraph
from nomem.core.graph import CROSS_REF_RELATION

from conftest import FakeLLM


async def test_negation_retires_the_node(make_graph: Callable[..., MemoryGraph]) -> None:
    present = {"entities": [{"label": "Kira", "type": "entity"}], "relations": []}
    gone = {"entities": [{"label": "Kira", "type": "entity", "negated": True}], "relations": []}
    g = make_graph(llm_response=lambda p: present if "joined" in p else gone)
    created = await g.aingest("x", "Kira joined the team.")
    node_id = created.nodes_created[0]

    retired = await g.aingest("x", "Kira left the team.")
    assert retired.nodes_retired == [node_id]

    node = await g.backend.get_node(node_id)
    assert node is not None and node.valid_to is not None  # retired, not deleted

    # retired nodes drop out of retrieval
    result = await g.aretrieve("Kira")
    assert node_id not in {n.id for n in result.nodes}


async def test_cross_reference_pass_is_opt_in(make_graph: Callable[..., MemoryGraph]) -> None:
    payload = {
        "entities": [
            {"label": "the meeting", "type": "event"},
            {"label": "the meeting room", "type": "entity"},
        ],
        "relations": [],
    }
    off = make_graph(llm_response=payload)
    receipt = await off.aingest("x", "We booked the meeting and the meeting room.")
    assert receipt.edges_cross_referenced == []

    on = make_graph(llm_response=payload)
    receipt = await on.aingest(
        "x",
        "We booked the meeting and the meeting room.",
        config={"cross_reference": True, "cross_reference_threshold": 0.3},
    )
    assert len(receipt.edges_cross_referenced) == 1
    sg = await on.aretrieve("meeting")
    assert any(e.relation == CROSS_REF_RELATION for e in sg.edges)


async def test_retrieval_respects_token_budget(make_graph: Callable[..., MemoryGraph]) -> None:
    g = make_graph(
        llm_response={
            "entities": [{"label": f"entity number {i}", "type": "entity"} for i in range(8)],
            "relations": [],
        }
    )
    await g.aingest("x", "many things")
    tight = await g.aretrieve("entity", config={"token_budget": 20, "hop_depth": 0})
    assert tight.metadata.get("budget_trimmed") is True
    assert len(tight.nodes) < 8


async def test_as_of_returns_historical_state(make_graph: Callable[..., MemoryGraph]) -> None:
    g = make_graph(
        llm_response=lambda p: (
            {"entities": [{"label": "Berlin", "type": "entity"}], "relations": []}
            if "Berlin" in p
            else {"entities": [{"label": "Lisbon", "type": "entity"}], "relations": []}
        )
    )
    await g.aingest("x", "Kira lives in Berlin.")
    await asyncio.sleep(0.02)
    checkpoint = datetime.now(tz=UTC)
    await asyncio.sleep(0.02)
    await g.aingest("x", "Kira lives in Lisbon.")

    now_view = await g.aretrieve("where does Kira live")
    assert "Lisbon" in {n.label for n in now_view.nodes}

    past_view = await g.aretrieve("where does Kira live", as_of=checkpoint)
    labels = {n.label for n in past_view.nodes}
    assert "Berlin" in labels and "Lisbon" not in labels


async def test_receipt_reports_resolution_confidence(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph(
        llm_response={"entities": [{"label": "Kira", "type": "entity"}], "relations": []}
    )
    receipt = await g.aingest("x", "Kira waved.")
    node_id = receipt.nodes_created[0]
    assert node_id in receipt.resolution_confidence


async def test_ingest_does_not_increment_access_count_by_default(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph(
        llm_response={"entities": [{"label": "Kira", "type": "entity"}], "relations": []}
    )
    r1 = await g.aingest("x", "Kira waved.")
    node_id = r1.nodes_created[0]
    await g.aingest("x", "Kira waved again.")
    node = await g.backend.get_node(node_id)
    assert node is not None and node.access_count == 0  # ingest never counts as access


async def test_retrieve_increments_access_count(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    g = make_graph(
        llm_response={"entities": [{"label": "Kira", "type": "entity"}], "relations": []}
    )
    r1 = await g.aingest("x", "Kira waved.")
    node_id = r1.nodes_created[0]
    await g.aretrieve("Kira")
    node = await g.backend.get_node(node_id)
    assert node is not None and node.access_count == 1


async def test_ingest_mode_manual_writes_nothing(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    """ROADMAP Q7: a dry run — extract, resolve, write nothing, report the plan."""
    turn = {
        "entities": [
            {"label": "Kira", "type": "entity"},
            {"label": "Lisbon", "type": "entity"},
        ],
        "relations": [{"source": "Kira", "target": "Lisbon", "relation": "lives_in"}],
    }
    g = make_graph(llm_response=turn, ingest_mode="manual")
    receipt = await g.aingest("where is Kira?", "Kira moved to Lisbon.")

    assert receipt.nodes_created == []
    assert receipt.nodes_updated == []
    assert receipt.edges_upserted == []
    assert await g.backend.list_nodes(active_only=False) == []
    assert await g.backend.list_edges(active_only=False) == []

    assert [e.label for e in receipt.queued_writes] == ["Kira", "Lisbon"]
    assert {e.metadata["intended_op"] for e in receipt.queued_writes} == {"create"}
    assert all(e.metadata["resolved_node_id"] is None for e in receipt.queued_writes)


async def test_ingest_mode_manual_plans_updates_and_retires(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    present = {"entities": [{"label": "Kira", "type": "entity"}], "relations": []}
    gone = {"entities": [{"label": "Kira", "type": "entity", "negated": True}], "relations": []}

    g = make_graph(llm_response=present)
    node_id = (await g.aingest("x", "Kira joined.")).nodes_created[0]

    # Same store, dry-run graph: an existing entity plans an update, a negated one a retire.
    planner = MemoryGraph(
        user_id="u1",
        backend=g.backend,
        embedder=g.embedder,
        llm=FakeLLM(present),
        ingest_mode="manual",
    )
    planned = await planner.aingest("x", "Kira is still here.")
    assert [e.metadata["intended_op"] for e in planned.queued_writes] == ["update"]
    assert planned.queued_writes[0].metadata["resolved_node_id"] == node_id
    assert planned.resolution_confidence[node_id] > 0.9

    planner.llm._response = gone  # type: ignore[attr-defined]
    retire_plan = await planner.aingest("x", "Kira left.")
    assert [e.metadata["intended_op"] for e in retire_plan.queued_writes] == ["retire"]

    node = await g.backend.get_node(node_id)
    assert node is not None and node.valid_to is None  # the dry run wrote nothing


async def test_edge_types_drops_are_counted_on_the_receipt(
    make_graph: Callable[..., MemoryGraph],
) -> None:
    turn = {
        "entities": [
            {"label": "Kira", "type": "entity"},
            {"label": "Lisbon", "type": "entity"},
        ],
        "relations": [
            {"source": "Kira", "target": "Lisbon", "relation": "lives_in"},
            {"source": "Lisbon", "target": "Kira", "relation": "gossips_about"},
        ],
    }
    g = make_graph(llm_response=turn)
    receipt = await g.aingest("x", "Kira lives in Lisbon.", config={"edge_types": ["lives_in"]})
    assert len(receipt.edges_upserted) == 1
    assert receipt.relations_dropped == 1

    edges = await g.backend.list_edges()
    assert [e.relation for e in edges] == ["lives_in"]


async def test_stats_counts_active_and_retired(make_graph: Callable[..., MemoryGraph]) -> None:
    present = {
        "entities": [
            {"label": "Kira", "type": "entity"},
            {"label": "the move", "type": "event"},
        ],
        "relations": [{"source": "Kira", "target": "the move", "relation": "involves"}],
    }
    gone = {"entities": [{"label": "Kira", "type": "entity", "negated": True}], "relations": []}
    g = make_graph(llm_response=lambda p: present if "moved" in p else gone)
    await g.aingest("x", "Kira moved to Lisbon.")

    stats = await g.astats()
    assert stats["user_id"] == "u1"
    assert stats["nodes_total"] == stats["nodes_active"] == 2
    assert stats["nodes_retired"] == 0
    assert stats["edges_total"] == stats["edges_active"] == 1
    assert stats["nodes_by_type"] == {"entity": 1, "event": 1}

    await g.aingest("x", "Kira left.")
    after = await g.astats()
    assert after["nodes_total"] == 2
    assert after["nodes_active"] == 1
    assert after["nodes_retired"] == 1
    assert after["nodes_by_type"] == {"event": 1}
