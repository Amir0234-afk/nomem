"""Phase 1: entity/relation extraction parsing."""

from __future__ import annotations

import pytest
from nomem.config import IngestConfig
from nomem.core.extraction import EntityExtractor
from nomem.exceptions import ExtractionError

from conftest import FakeLLM


async def test_parses_entities_and_relations() -> None:
    llm = FakeLLM(
        {
            "entities": [
                {"label": "Kira", "type": "entity", "negated": False},
                {"label": "moved out", "type": "event", "negated": True},
            ],
            "relations": [
                {"source": "Kira", "target": "moved out", "relation": "involves", "weight": 2},
            ],
        }
    )
    entities, relations = await EntityExtractor(llm=llm).extract("u", "a", IngestConfig())
    assert [e.label for e in entities] == ["Kira", "moved out"]
    assert entities[1].negated is True
    assert relations[0].relation == "involves"
    assert relations[0].weight == 2.0


async def test_drops_relations_with_unknown_endpoints() -> None:
    llm = FakeLLM(
        {
            "entities": [{"label": "Kira", "type": "entity"}],
            "relations": [{"source": "Kira", "target": "Ghost", "relation": "knows"}],
        }
    )
    _, relations = await EntityExtractor(llm=llm).extract("u", "a", IngestConfig())
    assert relations == []


async def test_dedupes_entities_case_insensitively() -> None:
    llm = FakeLLM(
        {
            "entities": [
                {"label": "Kira", "type": "entity"},
                {"label": "kira", "type": "entity"},
            ],
            "relations": [],
        }
    )
    entities, _ = await EntityExtractor(llm=llm).extract("u", "a", IngestConfig())
    assert len(entities) == 1


async def test_unknown_type_falls_back_to_entity() -> None:
    llm = FakeLLM({"entities": [{"label": "X", "type": "banana"}], "relations": []})
    entities, _ = await EntityExtractor(llm=llm).extract("u", "a", IngestConfig())
    assert entities[0].type == "entity"


async def test_malformed_payload_raises() -> None:
    llm = FakeLLM({"entities": "not a list", "relations": []})
    with pytest.raises(ExtractionError):
        await EntityExtractor(llm=llm).extract("u", "a", IngestConfig())


async def test_extraction_model_override_passed_through() -> None:
    seen: dict[str, str | None] = {}

    class RecordingLLM(FakeLLM):
        async def generate_json(self, prompt, *, system=None, schema=None, model=None):  # type: ignore[no-untyped-def]
            seen["model"] = model
            return {"entities": [], "relations": []}

    await EntityExtractor(llm=RecordingLLM({})).extract(
        "u", "a", IngestConfig(extraction_model="llama3.2:1b")
    )
    assert seen["model"] == "llama3.2:1b"
