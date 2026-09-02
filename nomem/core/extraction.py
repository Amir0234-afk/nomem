"""Entity + relation extraction and entity resolution.

Pipeline position (AGENT.md "Ingest Pipeline")::

    (user_msg, assistant_msg)
        -> EntityExtractor.extract       (LLM call, cheap model)
        -> EntityResolver.resolve        (embedding similarity + string match)
             high confidence -> existing node
             low  confidence -> queue for review / create (configurable)
             no match        -> new entity

Both classes are skeletons — signatures and control-flow docstrings are final,
bodies raise ``NotImplementedError``.
"""

from __future__ import annotations

from ..config import IngestConfig
from ..embedders.base import BaseEmbedder
from ..exceptions import ExtractionError, ResolutionError
from ..models import ExtractedEntity, ExtractedRelation, ResolutionOutcome

_PHASE = "extraction/resolution is a Phase 1 deliverable and is not implemented yet"


class EntityExtractor:
    """Runs the (configurable, cheap) LLM call that pulls entities + relations."""

    def __init__(self, *, model: str | None = None) -> None:
        self.model = model

    async def extract(
        self,
        user: str,
        assistant: str,
        config: IngestConfig,
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelation]]:
        """Return ``(entities, relations)`` extracted from one conversation turn.

        Raises :class:`ExtractionError` on LLM failure — never returns partial
        results silently.
        """
        raise NotImplementedError(_PHASE)


class EntityResolver:
    """Matches extracted entities against existing nodes for the user."""

    def __init__(self, *, backend: object, embedder: BaseEmbedder) -> None:
        self.backend = backend
        self.embedder = embedder

    async def resolve(
        self,
        entities: list[ExtractedEntity],
        config: IngestConfig,
    ) -> list[ResolutionOutcome]:
        """Resolve each entity to an existing node id, a new node, or a queue.

        For each entity:

        * similarity (embedding) + string match against active nodes
        * ``confidence >= resolution_confidence_threshold`` -> resolve to node
        * below threshold -> ``ambiguous=True`` (queued or raised per strategy);
          nomem never silently merges on a low-confidence match
        * no candidate -> ``resolved_node_id=None`` (caller CREATEs)

        Raises :class:`ResolutionError` only on a hard failure of the strategy
        itself.
        """
        raise NotImplementedError(_PHASE)


__all__ = ["EntityExtractor", "EntityResolver", "ExtractionError", "ResolutionError"]
