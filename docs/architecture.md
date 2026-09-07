# nomem architecture

## Module map

```
nomem/
├── __init__.py        # public exports: MemoryGraph, models, config, exceptions
├── graph.py           # MemoryGraph — the public entry point (async core + sync wrappers)
├── plugins.py         # Plugin protocol + "nomem.plugins" entry-point discovery
├── sync.py            # run_sync() on one dedicated background loop (networked backends stay valid)
├── config.py          # MemoryGraphConfig / DecayConfig / IngestConfig / RetrievalConfig + merge()
├── models.py          # Node, Edge, SubGraph, IngestReceipt, DecayResult, pipeline intermediates
├── exceptions.py      # NomemError hierarchy
├── _http.py           # stdlib async JSON-over-HTTP (Ollama transport, no deps)
├── _vector.py         # pack/unpack float blobs + cosine + mean
├── core/
│   ├── extraction.py  # EntityExtractor (LLM call), EntityResolver (match against graph)
│   ├── graph.py       # GraphCRUD — resolution outcomes -> CREATE/UPDATE/RETIRE + edges
│   ├── retrieval.py   # Retriever — flat + hierarchical seed -> traverse -> bounded SubGraph
│   └── decay.py       # score_node() formula + DecayEngine (delegates to backend.run_decay)
├── backends/
│   ├── base.py        # BaseBackend ABC (the adapter contract)
│   ├── _common.py     # shared bi-temporal filter (active_at) + BFS (bfs_subgraph)
│   ├── sqlite.py      # zero-infra default (stdlib sqlite3 + Python-side vectors)
│   ├── postgres.py    # asyncpg + pgvector (vector index; BFS/decay in Python for parity)
│   ├── neo4j.py       # bolt driver + vector index ((:Node)-[:EDGE]->; BFS/decay in Python)
│   └── __init__.py    # BACKEND_REGISTRY + resolve_backend()
├── embedders/
│   ├── base.py        # BaseEmbedder ABC
│   ├── nomic.py       # default · nomic-embed-text via Ollama (768d native)
│   ├── openai.py      # plain HTTP via _http.py — no extra, no dependency
│   ├── custom.py      # CallableEmbedder — wrap any callable (implemented)
│   └── __init__.py    # EMBEDDER_REGISTRY + resolve_embedder()
└── llms/
    ├── base.py        # BaseLLM ABC — generate_json() for extraction
    ├── ollama.py      # Phase 1 default · Ollama /api/chat with JSON mode
    ├── custom.py      # CallableLLM — wrap any prompt -> json callable (implemented)
    └── __init__.py    # LLM_REGISTRY + resolve_llm()
```

`MemoryGraph.__init__` resolves the embedder first, then the backend + LLM through the
registries (injecting `user_id` and the embedder's `vector_dimensions` into the backend),
syncs `DecayConfig.mode` from the `decay=` knob, constructs one instance each of
`EntityExtractor`, `EntityResolver`, `GraphCRUD`, `Retriever`, and `DecayEngine` sharing
the resolved adapters, and finally loads plugins (see below) unless `plugins=False`.

### Dimensions note

`nomic-embed-text` produces **768**-dim vectors, which is the default. A smaller
`dimensions=` is honored via Matryoshka truncation + renormalization.

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

`ingest_mode="manual"` short-circuits after resolution: nothing is written, and every
intended operation comes back in `receipt.queued_writes`. `ingest_config.edge_types`, when
set, drops extracted relations outside the allowed set before the edge upsert step.
`ingest_config.resolution_strategy` selects the scoring function —
`"embedding"` (cosine only), `"string"` (near-exact ratio only), or `"hybrid"`
(the `max()` of both, the default).

## Retrieval pipeline

```
query string
        │
        ▼
Retriever._embed_query
        │
        ├─ flat (default) ──────────────────────────────────────────────┐
        │   _vector_seed → backend.vector_search → top-k seed nodes      │
        │                                                               │
        └─ hierarchical (mode='hierarchical') ──────────────────────────┤
            backend.list_nodes(active_only) →                           │
            core index   = top `core_index_size_floor` by importance    │
            sub-index    = _route_sub_index(context tags, query_vec):   │
                deterministic → nodes whose metadata["context"] ∩ tags  │
                semantic      → tag whose member-centroid ≈ query       │
                hybrid        → deterministic, then semantic fallback   │
            seeds = top-k of (core ∪ sub) by cosine to query            │
                                                                        ▼
_traverse → N hops (config.hop_depth), honoring as_of
        │
        ▼
_enforce_budget → drop lowest-importance nodes past config.token_budget
        │            (raise RetrievalBudgetExceededError if one node alone exceeds it)
        ▼
SubGraph { nodes, edges, metadata }   # metadata.retrieval_mode, .sub_index_used
```

`retrieve()` increments `access_count` on every returned node (skipped when `as_of` is
set — historical reads are not accesses). `context=` tags on `ingest()` are stored under
`node.metadata["context"]` (merged on re-mention) and are what the router matches.
nomem never serializes the `SubGraph` to a prompt string — that is the application's
responsibility.

## Decay pass

Dev-invoked only — `graph.run_decay()` / `await graph.arun_decay()`. nomem never
background-schedules it. `DecayEngine.run` validates that decay is enabled and delegates
to `backend.run_decay(config)`; the per-node formula (`core/decay.py::score_node`) is
shared so every backend scores identically.

```
importance     = (α · access_weight) + (β · recency_weight)
access_weight  = log(1 + access_count)
recency_weight = exp(-λ · days_since_last_access)
```

`MemoryGraph(decay=...)` sets `DecayConfig.mode`, which zeroes one term:
`"access"` → recency off, `"time"` → access off, `"combined"` → both, `None` →
`run_decay()` raises `ConfigError`.

Nodes scoring `< importance_floor` are prune-*eligible* (`DecayResult.prune_candidates`);
they are retired — `valid_to` set, never hard-deleted — only when `pruning` is `True`
(`DecayResult.nodes_pruned`). Defaults: α=0.6, β=0.4, λ=0.1, importance_floor=0.05,
pruning off.

## Sync API

`ingest` / `retrieve` / `run_decay` / `close` and `with MemoryGraph(...)` are thin
wrappers over the `a`-prefixed coroutines — there is no second implementation. They run
through `nomem.sync.run_sync`, which drives the coroutine on **one dedicated background
event loop** created on first use (a daemon thread), not a fresh `asyncio.run()` loop per
call. asyncpg pools and the Neo4j driver bind their connections to the loop that created
them, so a per-call loop would break the second sync call; the shared loop keeps them
valid for the life of the process. Calling a sync method from inside a running event loop
raises `NomemError` — use the async variant there.

## Plugins

Adapters swap a component; plugins add capability. `nomem/plugins.py` defines a `Plugin`
protocol (`namespace: str`, `attach(graph) -> object`) and discovers implementations
through the `nomem.plugins` entry-point group at `MemoryGraph.__init__` time. Each
returned object is mounted at `graph.<namespace>` and recorded in `graph.plugins`.

```
MemoryGraph.__init__
        │
        ├─ resolve embedder → backend → llm  (registries)
        ├─ build pipelines                    (extractor, resolver, crud, retriever, decay)
        └─ plugins is True?
               entry_points(group="nomem.plugins")
                    → Plugin()  → attach(graph) → setattr(graph, namespace, obj)
                    → collision with an existing attribute → ConfigError
                    → import failure → raised, never swallowed
```

`MemoryGraph(plugins=False)` skips discovery. Plugins never monkeypatch core methods; they
read the graph through its public attributes (`backend`, `embedder`, `llm`, `config`,
`user_id`) documented in [stability.md](stability.md).

This is the boundary the proprietary tier extends through, which is why it is a separate
package rather than a fork.