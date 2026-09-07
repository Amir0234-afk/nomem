"""The public entry point: :class:`MemoryGraph`.

Owns configuration, resolves the backend / embedder / LLM adapters, wires up the
core pipeline objects, and exposes the ``ingest`` / ``retrieve`` / ``run_decay``
surface (sync) plus the ``a``-prefixed async variants.

The async methods are the real implementation; the sync methods delegate through
:func:`nomem.sync.run_sync`.
"""

from __future__ import annotations

import inspect
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
from .core.graph import GraphCRUD, plan_receipt
from .core.retrieval import Retriever
from .embedders import resolve_embedder
from .embedders.base import BaseEmbedder
from .llms import resolve_llm
from .llms.base import BaseLLM
from .models import DecayResult, IngestReceipt, SubGraph
from .plugins import attach_plugins
from .sync import run_sync


class MemoryGraph:
    """Per-user, self-updating knowledge-graph memory for LLM applications."""

    def __init__(
        self,
        user_id: str,
        *,
        backend: str | BaseBackend = "sqlite",
        embedder: str | BaseEmbedder | Any = "nomic",
        llm: str | BaseLLM | Any = "ollama",
        decay: str | None = "combined",
        ingest_mode: str = "auto",
        config: MemoryGraphConfig | None = None,
        decay_config: DecayConfig | None = None,
        ingest_config: IngestConfig | None = None,
        retrieval_config: RetrievalConfig | None = None,
        backend_options: dict[str, Any] | None = None,
        embedder_options: dict[str, Any] | None = None,
        llm_options: dict[str, Any] | None = None,
        plugins: bool = True,
    ) -> None:
        self.config = config or MemoryGraphConfig(
            user_id=user_id,
            backend=backend if isinstance(backend, str) else "custom",
            embedder=embedder if isinstance(embedder, str) else "custom",
            llm=llm if isinstance(llm, str) else "custom",
            decay=decay,  # type: ignore[arg-type]
            ingest_mode=ingest_mode,  # type: ignore[arg-type]
            decay_config=decay_config or DecayConfig(),
            ingest_config=ingest_config or IngestConfig(),
            retrieval_config=retrieval_config or RetrievalConfig(),
            backend_options=backend_options or {},
            embedder_options=embedder_options or {},
            llm_options=llm_options or {},
        )
        uid = self.user_id = self.config.user_id
        # `decay=` is the documented knob for the decay mode; keep the nested
        # DecayConfig in sync so per-call overrides start from the right place.
        self.config.decay_config.mode = self.config.decay

        self.embedder: BaseEmbedder = resolve_embedder(embedder, self.config.embedder_options)

        backend_opts = {
            "user_id": uid,
            "vector_dimensions": self.embedder.dimensions,
            **self.config.backend_options,
        }
        self.backend: BaseBackend = (
            backend if isinstance(backend, BaseBackend) else resolve_backend(backend, backend_opts)
        )
        self.llm: BaseLLM = resolve_llm(None if llm == "ollama" else llm, self.config.llm_options)

        count_on_ingest = self.config.decay_config.count_on_ingest
        self.extractor = EntityExtractor(
            llm=self.llm, model=self.config.ingest_config.extraction_model
        )
        self.resolver = EntityResolver(backend=self.backend, embedder=self.embedder)
        self.crud = GraphCRUD(
            backend=self.backend,
            embedder=self.embedder,
            user_id=uid,
            count_on_ingest=count_on_ingest,
        )
        self.retriever = Retriever(backend=self.backend, embedder=self.embedder)
        self.decay_engine = DecayEngine(backend=self.backend)

        # Last: a plugin's attach(graph) must see a fully wired graph.
        self.plugins: dict[str, object] = attach_plugins(self) if plugins else {}

    # --- async core --------------------------------------------------

    async def aingest(
        self,
        user: str,
        assistant: str,
        config: dict[str, Any] | None = None,
        context: list[str] | None = None,
    ) -> IngestReceipt:
        """Feed one conversation turn into the graph; return an ingest receipt.

        Flow: extract -> resolve -> apply CRUD -> (opt-in) cross-reference.
        ``context`` tags this turn's nodes for hierarchical retrieval routing.

        Under ``ingest_mode="manual"`` this is a **dry run**: it extracts and
        resolves, writes nothing, and reports every intended operation in
        ``receipt.queued_writes``.
        """
        cfg = merge(self.config.ingest_config, config)
        entities, relations, dropped = await self.extractor.extract(user, assistant, cfg)
        resolutions = await self.resolver.resolve(entities, cfg)

        if self.config.ingest_mode == "manual":
            receipt = plan_receipt(resolutions, cfg)
            receipt.relations_dropped = dropped
            return receipt

        receipt = await self.crud.apply_resolutions(resolutions, relations, cfg, context)
        receipt.relations_dropped = dropped

        if cfg.cross_reference:
            touched = [*receipt.nodes_created, *receipt.nodes_updated]
            receipt.edges_cross_referenced = await self.crud.cross_reference_pass(
                touched, cfg.cross_reference_threshold
            )
        return receipt

    async def aretrieve(
        self,
        query: str,
        config: dict[str, Any] | None = None,
        as_of: datetime | None = None,
        context: list[str] | None = None,
    ) -> SubGraph:
        """Retrieve a bounded structured subgraph relevant to ``query``.

        ``context`` tags route hierarchical retrieval to a situation sub-index.
        """
        cfg = merge(self.config.retrieval_config, config)
        return await self.retriever.retrieve(query, cfg, as_of, context)

    async def arun_decay(self, config: dict[str, Any] | None = None) -> DecayResult:
        """Run one decay/pruning pass. Dev-invoked only — never self-scheduled."""
        cfg = merge(self.config.decay_config, config)
        return await self.decay_engine.run(cfg)

    async def astats(self) -> dict[str, Any]:
        """Counts for this user's graph: totals, active vs retired, nodes per type.

        A cheap health check, not an export — the records themselves stay behind
        ``retrieve()`` and the backend handle.
        """
        nodes = await self.backend.list_nodes(active_only=False)
        edges = await self.backend.list_edges(active_only=False)
        active_nodes = [n for n in nodes if n.valid_to is None]
        by_type: dict[str, int] = {}
        for node in active_nodes:
            by_type[node.type] = by_type.get(node.type, 0) + 1
        return {
            "user_id": self.user_id,
            "nodes_total": len(nodes),
            "nodes_active": len(active_nodes),
            "nodes_retired": len(nodes) - len(active_nodes),
            "edges_total": len(edges),
            "edges_active": sum(1 for e in edges if e.valid_to is None),
            "edges_retired": sum(1 for e in edges if e.valid_to is not None),
            "nodes_by_type": dict(sorted(by_type.items())),
        }

    # --- sync wrappers ----------------------------------------------

    def ingest(
        self,
        user: str,
        assistant: str,
        config: dict[str, Any] | None = None,
        context: list[str] | None = None,
    ) -> IngestReceipt:
        """Sync wrapper over :meth:`aingest`."""
        return run_sync(self.aingest(user, assistant, config, context))

    def retrieve(
        self,
        query: str,
        config: dict[str, Any] | None = None,
        as_of: datetime | None = None,
        context: list[str] | None = None,
    ) -> SubGraph:
        """Sync wrapper over :meth:`aretrieve`."""
        return run_sync(self.aretrieve(query, config, as_of, context))

    def run_decay(self, config: dict[str, Any] | None = None) -> DecayResult:
        """Sync wrapper over :meth:`arun_decay`."""
        return run_sync(self.arun_decay(config))

    def stats(self) -> dict[str, Any]:
        """Sync wrapper over :meth:`astats`."""
        return run_sync(self.astats())

    # --- lifecycle -------------------------------------------------

    async def aclose(self) -> None:
        """Release backend resources (connection pools, drivers)."""
        close = getattr(self.backend, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    def close(self) -> None:
        """Sync wrapper over :meth:`aclose`."""
        run_sync(self.aclose())

    async def __aenter__(self) -> MemoryGraph:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    def __enter__(self) -> MemoryGraph:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["MemoryGraph"]
