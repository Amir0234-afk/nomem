"""Entity + relation extraction and entity resolution.

Pipeline position (AGENT.md "Ingest Pipeline")::

    (user_msg, assistant_msg)
        -> EntityExtractor.extract       (LLM call, cheap model)
        -> EntityResolver.resolve        (embedding similarity + string match)
             high confidence -> existing node
             low  confidence -> queue for review / create (configurable)
             no match        -> new entity
"""

from __future__ import annotations

import difflib
from typing import Any

from .._vector import cosine
from ..backends.base import BaseBackend
from ..config import IngestConfig
from ..embedders.base import BaseEmbedder
from ..exceptions import ExtractionError
from ..llms.base import BaseLLM
from ..models import ExtractedEntity, ExtractedRelation, ResolutionOutcome

_VALID_TYPES = {"entity", "event", "emotion", "fact"}

_SYSTEM_PROMPT = (
    "You extract a knowledge graph from one conversational turn between a user and an "
    "assistant. Identify the salient entities (people, places, things), events, emotions, "
    "and stated facts, plus the directed relationships between them. "
    "Respond with ONLY a JSON object of this exact shape:\n"
    '{"entities": [{"label": str, "type": "entity"|"event"|"emotion"|"fact", '
    '"negated": bool}], '
    '"relations": [{"source": str, "target": str, "relation": str, "weight": number}]}\n'
    "\"negated\" is true when the turn says the entity/relationship no longer holds "
    "(left, died, ended, cancelled, broke up). "
    "Every relation's source and target MUST match an entity label exactly. "
    "Use lowercase snake_case relation verbs (e.g. lives_in, caused_by, involves, follows). "
    "Return empty lists if nothing is worth storing."
)

_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "type": {"type": "string", "enum": sorted(_VALID_TYPES)},
                    "negated": {"type": "boolean"},
                },
                "required": ["label", "type"],
            },
        },
        "relations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "relation": {"type": "string"},
                    "weight": {"type": "number"},
                },
                "required": ["source", "target", "relation"],
            },
        },
    },
    "required": ["entities", "relations"],
}


class EntityExtractor:
    """Runs the (configurable, cheap) LLM call that pulls entities + relations."""

    def __init__(self, *, llm: BaseLLM, model: str | None = None) -> None:
        self.llm = llm
        self.model = model

    async def extract(
        self,
        user: str,
        assistant: str,
        config: IngestConfig,
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation], int]:
        """Return ``(entities, relations, relations_dropped)`` for one turn.

        ``relations_dropped`` counts relations discarded by
        ``config.edge_types``; it is reported on the ingest receipt so a
        narrowed edge vocabulary is visible rather than silent.
        """
        prompt = f"USER: {user}\nASSISTANT: {assistant}"
        data = await self.llm.generate_json(
            prompt,
            system=_SYSTEM_PROMPT,
            schema=_SCHEMA,
            model=config.extraction_model or self.model,
        )

        entities = self._parse_entities(data.get("entities", []))
        known = {e.label for e in entities}
        relations = self._parse_relations(data.get("relations", []), known)
        if config.edge_types is None:
            return entities, relations, 0
        allowed = set(config.edge_types)
        kept = [r for r in relations if r.relation in allowed]
        return entities, kept, len(relations) - len(kept)

    def _parse_entities(self, raw: Any) -> list[ExtractedEntity]:
        if not isinstance(raw, list):
            raise ExtractionError(f"'entities' must be a list, got {type(raw).__name__}")
        out: list[ExtractedEntity] = []
        seen: set[str] = set()
        for item in raw:
            if not isinstance(item, dict) or not item.get("label"):
                continue
            label = str(item["label"]).strip()
            if not label or label.lower() in seen:
                continue
            seen.add(label.lower())
            etype = str(item.get("type", "entity")).lower()
            out.append(
                ExtractedEntity(
                    label=label,
                    type=etype if etype in _VALID_TYPES else "entity",
                    negated=bool(item.get("negated", False)),
                )
            )
        return out

    def _parse_relations(self, raw: Any, known: set[str]) -> list[ExtractedRelation]:
        if not isinstance(raw, list):
            raise ExtractionError(f"'relations' must be a list, got {type(raw).__name__}")
        out: list[ExtractedRelation] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source", "")).strip()
            target = str(item.get("target", "")).strip()
            relation = str(item.get("relation", "")).strip()
            if not (source and target and relation) or source not in known or target not in known:
                continue
            try:
                weight = float(item.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            out.append(
                ExtractedRelation(
                    source_label=source,
                    target_label=target,
                    relation=relation,
                    weight=weight if weight > 0 else 1.0,
                )
            )
        return out


class EntityResolver:
    """Matches extracted entities against existing nodes for the user.

    ``IngestConfig.resolution_strategy`` picks the score: ``"embedding"``
    (cosine only), ``"string"`` (surface-form ratio only), or ``"hybrid"``
    (the max of the two — the default).
    """

    def __init__(self, *, backend: BaseBackend, embedder: BaseEmbedder) -> None:
        self.backend = backend
        self.embedder = embedder

    async def resolve(
        self,
        entities: list[ExtractedEntity],
        config: IngestConfig,
    ) -> list[ResolutionOutcome]:
        """Resolve each entity to an existing node id, a new node, or a queue."""
        if not entities:
            return []

        vectors = await self.embedder.embed_batch([e.label for e in entities])
        outcomes: list[ResolutionOutcome] = []
        for entity, vec in zip(entities, vectors, strict=True):
            outcomes.append(await self._resolve_one(entity, vec, config))
        return outcomes

    async def _resolve_one(
        self,
        entity: ExtractedEntity,
        vector: list[float],
        config: IngestConfig,
    ) -> ResolutionOutcome:
        candidates = await self.backend.vector_search(vector, top_k=5)

        best_id: str | None = None
        best_score = 0.0
        best_source = "embedding"
        ranked: list[tuple[str, float]] = []
        strategy = config.resolution_strategy
        for node in candidates:
            emb_score = (
                cosine(vector, node.embedding)
                if node.embedding and strategy != "string"
                else 0.0
            )
            str_eff = 0.0
            if strategy != "embedding":
                str_score = difflib.SequenceMatcher(
                    None, entity.label.lower(), node.label.lower()
                ).ratio()
                # Trust the surface-form match only when it is near-identical; otherwise
                # a few coincidental shared letters would flag every new entity.
                str_eff = str_score if str_score >= config.string_match_min else 0.0
            score = max(emb_score, str_eff)
            ranked.append((node.id, score))
            if score > best_score:
                best_score, best_id = score, node.id
                best_source = "string_match" if str_eff >= emb_score else "embedding"

        threshold = config.resolution_confidence_threshold
        if best_id is not None and best_score >= threshold:
            return ResolutionOutcome(
                extracted=entity,
                resolved_node_id=best_id,
                confidence=best_score,
                resolution_source=best_source,  # type: ignore[arg-type]
                candidates=ranked,
            )
        if best_id is not None and best_score >= config.ambiguity_floor:
            # A low-confidence match: never silently merge — surface it as ambiguous.
            return ResolutionOutcome(
                extracted=entity,
                resolved_node_id=None,
                confidence=best_score,
                resolution_source=best_source,  # type: ignore[arg-type]
                ambiguous=True,
                candidates=ranked,
            )
        return ResolutionOutcome(
            extracted=entity,
            resolved_node_id=None,
            confidence=best_score,
            resolution_source="llm",
            candidates=ranked,
        )


__all__ = ["EntityExtractor", "EntityResolver"]
