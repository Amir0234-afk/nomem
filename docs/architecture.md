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
├── _http.py           # stdlib async JSON-over-HTTP (Ollama transport, no deps)
├── _vector.py         # pack/unpack float blobs + cosine similarity
├── core/
│   ├── extraction.py  # EntityExtractor (LLM call), EntityResolver (match against graph)
│   ├── graph.py       # GraphCRUD — resolution outcomes -> CREATE/UPDATE/RETIRE + edges
│   ├── retrieval.py   # Retriever — embed -> seed -> traverse -> bounded SubGraph
│   └── decay.py       # DecayEngine — importance scoring + prune eligibility (Phase 2)
├── backends/
│   ├── base.py        # BaseBackend ABC (the adapter contract)
│   ├── sqlite.py      # Phase 1 · zero-infra default (stdlib sqlite3 + Python-side vectors)
│   ├── postgres.py    # Phase 2 · pgvector
│   ├── neo4j.py       # Phase 3 · native graph
│   └── __init__.py    # BACKEND_REGISTRY + resolve_backend()
├── embedders/
│   ├── base.py        # BaseEmbedder ABC
│   ├── nomic.py       # Phase 1 default · nomic-embed-text via Ollama (768d native)
│   ├── openai.py      # requires [openai] extra
│   ├── custom.py      # CallableEmbedder — wrap any callable (implemented)
│   └── __init__.py    # EMBEDDER_REGISTRY + resolve_embedder()
└── llms/
    ├── base.py        # BaseLLM ABC — generate_json() for extraction
    ├── ollama.py      # Phase 1 default · Ollama /api/chat with JSON mode
    ├── custom.py      # CallableLLM — wrap any prompt -> json callable (implemented)
    └── __init__.py    # LLM_REGISTRY + resolve_llm()
```

`MemoryGraph.__init__` resolves the backend + embedder + LLM through the registries
(injecting `user_id` into the backend), then constructs one instance each of
`EntityExtractor`, `EntityResolver`, `GraphCRUD`, `Retriever`, and `DecayEngine`,
sharing the resolved adapters.

### Dimensions note

AGENT.md states the nomic embedder is 384-dim; `nomic-embed-text` actually produces
**768**-dim vectors, which is the default. A smaller `dimensions=` is honored via
Matryoshka truncation + renormalization.

## Ingest pipeline

```
(user_msg, assistant_msg)
        │
        ▼
EntityExtractor.extract          ← LLM call, cheap configurable model
        │                          → [ExtractedEntity], [ExtractedRelation]
        ▼
EntityResolver.resolve           ← score = max(cosine, near-exact string ratio)
        │   score ≥ resolution_confidence_threshold  → resolve to existing node id
        │   ambiguity_floor ≤ score < threshold      → ResolutionOutcome(ambiguous=True)
        │   score < ambiguity_floor                  → resolved_node_id = None (new)
        ▼
GraphCRUD.apply_resolutions
        │   new       → create_node (batch-embed labels)
        │   known     → update_node (last_accessed = now; access_count only if count_on_ingest)
        │   negated   → retire_node (valid_to = now, superseded_by)
        │   ambiguous → ambiguous_resolutions + (queued_writes | create), per on_ambiguous
        │   then        upsert_edge for each ExtractedRelation whose endpoints both resolved
        ▼
GraphCRUD.cross_reference_pass   ← opt-in (ingest_config.cross_reference)
        │   backend.cross_reference() vs active nodes; edge `related_to` when sim ≥
        │   cross_reference_threshold (pairs de-duplicated)
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
(hierarchical only, Phase 2) _core_index_lookup → _route_sub_index
        │   mode='hierarchical' currently raises NotImplementedError
        ▼
_vector_seed → top-k seed nodes  (backend.vector_search, cosine in Python)
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

`retrieve()` increments `access_count` on every returned node (skipped when `as_of` is
set — historical reads are not accesses). nomem never serializes the `SubGraph` to a
prompt string — that is the application's responsibility.

## Decay pass — Phase 2

Dev-invoked only — `graph.run_decay()` / `await graph.arun_decay()`. nomem never
background-schedules it. `DecayEngine` / `SQLiteBackend.run_decay` currently raise
`NotImplementedError`; the model below is the Phase 2 target.

```
importance     = (α · access_weight) + (β · recency_weight)
access_weight  = log(1 + access_count)
recency_weight = exp(-λ · days_since_last_access)
```

Nodes with `importance < importance_floor` are prune-*eligible*; they are retired only
when `decay_config.pruning` is `True`. Defaults: α=0.6, β=0.4, λ=0.1,
importance_floor=0.05, pruning off.
