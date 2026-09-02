"""The public entry point: :class:`MemoryGraph`.

This is the one class applications import. It owns configuration, resolves the
backend + embedder adapters, wires up the four core pipeline objects, and
exposes the ``ingest`` / ``retrieve`` / ``run_decay`` surface (sync) plus the
``a``-prefixed async variants.

The async methods are the real implementation; the sync methods delegate through
:func:`nomem.sync.run_sync`. In Phase 0 the orchestration flow is written out but
the pipeline calls it makes are not implemented yet, so calling ``ingest`` /
``retrieve`` / ``run_decay`` raises ``NotImplementedError`` (never
``AttributeError``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .backends import resolve_backend
from .backends.base import BaseBackend
from .config import (
    DecayConfig,
    IngestConfig,
    MemoryGraphConfig,
    RetrievalConfig,
    merge,
)
from .core.decay import DecayEngine
from .core.extraction import EntityExtractor, EntityResolver
from .core.graph import GraphCRUD
from .core.retrieval import Retriever
from .embedders import resolve_embedder
from .embedders.base import BaseEmbedder
from .models import DecayResult, IngestReceipt, SubGraph
from .sync import run_sync


class MemoryGraph:
    """Per-user, self-updating knowledge-graph memory for LLM applications."""

    def __init__(
        self,
        user_id: str,
        *,
        backend: str | BaseBackend = "sqlite",
        embedder: str | BaseEmbedder | Any = "nomic",
        decay: str | None = "combined",
        ingest_mode: str = "auto",
        config: MemoryGraphConfig | None = None,
        decay_config: DecayConfig | None = None,
        ingest_config: IngestConfig | None = None,
        retrieval_config: RetrievalConfig | None = None,
        backend_options: dict[str, Any] | None = None,
        embedder_options: dict[str, Any] | None = None,
    ) -> None:
        self.config = config or MemoryGraphConfig(
            user_id=user_id,
            backend=backend if isinstance(backend, str) else "custom",
            embedder=embedder if isinstance(embedder, str) else "custom",
            decay=decay,  # type: ignore[arg-type]
            ingest_mode=ingest_mode,  # type: ignore[arg-type]
            decay_config=decay_config or DecayConfig(),
            ingest_config=ingest_config or IngestConfig(),
            retrieval_config=retrieval_config or RetrievalConfig(),
            backend_options=backend_options or {},
            embedder_options=embedder_options or {},
        )

        self.backend: BaseBackend = resolve_backend(backend, self.config.backend_options)
        self.embedder: BaseEmbedder = resolve_embedder(embedder, self.config.embedder_options)

        self.extractor = EntityExtractor(model=self.config.ingest_config.extraction_model)
        self.resolver = EntityResolver(backend=self.backend, embedder=self.embedder)
        self.crud = GraphCRUD(backend=self.backend, embedder=self.embedder)
        self.retriever = Retriever(backend=self.backend, embedder=self.embedder)
        self.decay_engine = DecayEngine(backend=self.backend)

    # --- async core --------------------------------------------------

    async def aingest(
        self,
        user: str,
        assistant: str,
        config: dict[str, Any] | None = None,
    ) -> IngestReceipt:
        """Feed one conversation turn into the graph; return an ingest receipt.

        Flow: extract -> resolve -> apply CRUD -> (opt-in) cross-reference.
        """
        cfg = merge(self.config.ingest_config, config)
        entities, relations = await self.extractor.extract(user, assistant, cfg)
        resolutions = await self.resolver.resolve(entities, cfg)
        receipt = await self.crud.apply_resolutions(resolutions, relations, cfg)
        return receipt

    async def aretrieve(
        self,
        query: str,
        config: dict[str, Any] | None = None,
        as_of: datetime | None = None,
    ) -> SubGraph:
        """Retrieve a bounded structured subgraph relevant to ``query``."""
        cfg = merge(self.config.retrieval_config, config)
        return await self.retriever.retrieve(query, cfg, as_of)

    async def arun_decay(self, config: dict[str, Any] | None = None) -> DecayResult:
        """Run one decay/pruning pass. Dev-invoked only — never self-scheduled."""
        cfg = merge(self.config.decay_config, config)
        return await self.decay_engine.run(cfg)

    # --- sync wrappers ----------------------------------------------

    def ingest(
        self,
        user: str,
        assistant: str,
        config: dict[str, Any] | None = None,
    ) -> IngestReceipt:
        """Sync wrapper over :meth:`aingest`."""
        return run_sync(self.aingest(user, assistant, config))

    def retrieve(
        self,
        query: str,
        config: dict[str, Any] | None = None,
        as_of: datetime | None = None,
    ) -> SubGraph:
        """Sync wrapper over :meth:`aretrieve`."""
        return run_sync(self.aretrieve(query, config, as_of))

    def run_decay(self, config: dict[str, Any] | None = None) -> DecayResult:
        """Sync wrapper over :meth:`arun_decay`."""
        return run_sync(self.arun_decay(config))


__all__ = ["MemoryGraph"]
