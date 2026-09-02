# nomem architecture

## Module map

```
nomem/
├── __init__.py        # public exports: MemoryGraph, models, config, exceptions
├── graph.py           # MemoryGraph — the public entry point (async core + sync wrappers)
├── sync.py            # run_sync(): the only sync-over-async bridge
├── config.py          # MemoryGraphConfig / DecayConfig / IngestConfig / RetrievalConfig + merge()
├── models.py          # Node, Edge, SubGraph, IngestReceipt, DecayResult, pipeline intermediates
├── exceptions.py      # NomemError hierarchy
├── core/
│   ├── extraction.py  # EntityExtractor (LLM call), EntityResolver (match against graph)
│   ├── graph.py       # GraphCRUD — resolution outcomes -> CREATE/UPDATE/RETIRE + edges
│   ├── retrieval.py   # Retriever — embed -> seed -> traverse -> bounded SubGraph
│   └── decay.py       # DecayEngine — importance scoring + prune eligibility
├── backends/
│   ├── base.py        # BaseBackend ABC (the adapter contract)
│   ├── sqlite.py      # Phase 1 · zero-infra default
│   ├── postgres.py    # Phase 2 · pgvector
│   ├── neo4j.py       # Phase 3 · native graph
│   └── __init__.py    # BACKEND_REGISTRY + resolve_backend()
└── embedders/
    ├── base.py        # BaseEmbedder ABC
    ├── nomic.py       # Phase 1 default · nomic-embed-text via Ollama (384d)
    ├── openai.py      # requires [openai] extra
    ├── custom.py      # CallableEmbedder — wrap any callable (implemented)
    └── __init__.py    # EMBEDDER_REGISTRY + resolve_embedder()
```

`MemoryGraph.__init__` resolves the backend + embedder through the registries, then
constructs one instance each of `EntityExtractor`, `EntityResolver`, `GraphCRUD`,
`Retriever`, and `DecayEngine`, sharing the resolved adapters.

## Ingest pipeline

```
(user_msg, assistant_msg)
        │
        ▼
EntityExtractor.extract          ← LLM call, cheap configurable model
        │                          → [ExtractedEntity], [ExtractedRelation]
        ▼
EntityResolver.resolve           ← embedding similarity + string match vs active nodes
        │   confidence ≥ threshold → resolve to existing node id
        │   below threshold        → ResolutionOutcome(ambiguous=True) → queued / raised
        │   no candidate           → resolved_node_id = None
        ▼
GraphCRUD.apply_resolutions
        │   new      → create_node (+ embed)
        │   known    → update_node (weight++, last_accessed = now)
        │   negated  → retire_node (valid_to = now, superseded_by)
        │   then       upsert_edge for each ExtractedRelation
        ▼
GraphCRUD.cross_reference_pass   ← opt-in (ingest_config.cross_reference)
        │                          embedding pass vs existing nodes, candidate edges ≥ threshold
        ▼
IngestReceipt {
    nodes_created / updated / retired, edges_upserted / cross_referenced,
    resolution_confidence, ambiguous_resolutions, queued_writes
}
```

Per-call overrides come from the `config=` dict, merged over `ingest_config`.

## Retrieval pipeline

```
query string
        │
        ▼
Retriever._embed_query
        │
        ▼
(hierarchical only) _core_index_lookup → _route_sub_index
        │   deterministic (tags) → semantic fallback → hybrid (default)
        ▼
_vector_seed → top-k seed nodes
        │
        ▼
_traverse → N hops (config.hop_depth), honoring as_of
        │
        ▼
_enforce_budget → trim to config.token_budget (or raise RetrievalBudgetExceededError)
        │
        ▼
SubGraph { nodes, edges, metadata }
```

`retrieve()` increments `access_count` on every returned node. nomem never serializes
the `SubGraph` to a prompt string — that is the application's responsibility.

## Decay pass

Dev-invoked only — `graph.run_decay()` / `await graph.arun_decay()`. nomem never
background-schedules it.

```
importance     = (α · access_weight) + (β · recency_weight)
access_weight  = log(1 + access_count)
recency_weight = exp(-λ · days_since_last_access)
```

Nodes with `importance < importance_floor` are prune-*eligible*; they are retired only
when `decay_config.pruning` is `True`. Defaults: α=0.6, β=0.4, λ=0.1,
importance_floor=0.05, pruning off.
