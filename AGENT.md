# AGENT.md — nomem

> No One's Memory Management  
> Maintainer: No One's Studio  
> License: MIT (core) / Proprietary (advanced features)

---

## What This Project Is

`nomem` is a Python library that gives LLM-powered applications persistent, self-updating memory per user, structured as a knowledge graph.

Developers feed it conversation turns. nomem extracts entities and relationships, writes them into a graph using explicit CRUD operations, and retrieves relevant subgraphs at query time. The graph reflects the evolving state of conversations — not just an append-only log.

nomem is **not** a vector database, an LLM wrapper, or a chat history store. It is a graph database with an LLM-powered CRUD interface. The developer controls the rules.

---

## Core Design Philosophy

**Dev-first configurability.** nomem ships sane defaults for every behavior, but nearly everything is overridable. Pruning, decay, extraction model, traversal depth, importance thresholds, edge types — all are dev-controlled. nomem never makes opinionated decisions silently on the dev's behalf.

**Explicit CRUD semantics.** Every conversation turn maps to real graph operations:

| Conversation content | Graph operation |
|---|---|
| New entity mentioned | CREATE node |
| Existing entity updated | UPDATE node |
| Entity negated / removed | RETIRE node or edge (bi-temporal supersession — never hard delete) |
| Context retrieval | READ subgraph |

**Bounded context injection.** Retrieved subgraphs are a fixed size regardless of how long a user has been on the platform. A user with 3 years of history returns the same token budget as a user with 3 days.

**Backend and embedder agnosticism.** Storage and embedding are adapter interfaces. Swapping one touches nothing else.

---

## Public API

```python
from nomem import MemoryGraph

graph = MemoryGraph(
    user_id="u123",
    backend="sqlite",       # sqlite | postgres | neo4j
    embedder="nomic",       # default; swappable via config
    decay="combined",       # "time" | "access" | "combined" | None
    ingest_mode="auto",     # "auto" | "manual" (manual = dry run, writes nothing)
    # all other options: see Configuration Reference
)

# Feed a conversation turn
graph.ingest(
    user="What happened with Kira?",
    assistant="Kira left the room after the argument.",
    config={...}            # optional per-call overrides
)

# Retrieve structured context
result = graph.retrieve("Kira")
# Returns:
# {
#   "nodes": [...],
#   "edges": [...],
#   "metadata": { "retrieved_at": ..., "decay_scores": ..., "hop_depth": ... }
# }

# Async variants — same signatures
await graph.aingest(...)
await graph.aretrieve(...)
```

The sync API is a thin wrapper over the async core. There is no separate sync implementation.

---

## Ingest Pipeline

```
(user_msg, assistant_msg)
        │
        ▼
Entity + relation extraction        ← LLM call (cheap model, configurable)
        │
        ▼
Entity resolution                   ← match extracted entities against existing nodes
        │                              strategy: embedding similarity + string match (configurable)
        ├─ High confidence match?   → resolve to existing node
        ├─ Low confidence match?    → queue for dev review (or create new node; configurable)
        ├─ No match?                → treat as new entity
        │
        ├─ New entity?      → CREATE node, embed
        ├─ Known entity?    → UPDATE node (weight++, last_accessed = now)
        ├─ Negated entity?  → RETIRE node (set valid_to = now, create supersession record)
        │
        ▼
Edge upsert (source, target, relation, weight)
        │
        ▼
Write-boundary cross-reference      ← embedding similarity pass against existing nodes
        │                              creates candidate edges above threshold (dev-configurable)
        │                              runs post-CRUD; opt-in via cross_reference=True
        │
        ▼
Ingest receipt returned {
    nodes_created, nodes_updated, nodes_retired,
    edges_upserted, edges_cross_referenced,
    resolution_confidence,          # per-node confidence scores
    ambiguous_resolutions,          # nodes where resolution was uncertain
    queued_writes,                  # below-threshold extractions held for review
    relations_dropped               # relations filtered out by edge_types
}
```

`ingest_config` overrides per call: extraction model, edge types to track, importance floor, resolution strategy, resolution confidence threshold, whether to run cross-reference pass.

**`ingest_mode="manual"`** is a dry run: extract and resolve, write nothing, and return an
`IngestReceipt` with every intended operation in `queued_writes` for the dev to inspect or
replay. Each queued entity carries its intent in `metadata["intended_op"]`
(`create` / `update` / `retire` / `queue` / `noop`) and `metadata["resolved_node_id"]`.

---

## Retrieval Pipeline

```
query string
        │
        ▼
Embed query
        │
        ▼
Core index lookup                   ← small always-loaded high-importance node set
        │                              routes into situation-matched sub-indexes (opt-in)
        │
        ▼
Vector similarity search → top-k seed nodes
        │
        ▼
Graph traversal → N hops from seed nodes (configurable depth)
        │
        ▼
Structured object { nodes, edges, metadata }
```

The developer decides what to do with the structured object. nomem does not serialize to prompt string — that is the application's responsibility.

### Retrieval Modes

**Flat (default):** embed query → vector similarity → k-hop traversal. No sub-index routing.

**Hierarchical (opt-in):** A small core index (high-importance nodes only, configurable size floor) is always loaded. It routes into larger situation-specific sub-indexes selected by a deterministic router (based on dev-supplied context tags) with a semantic similarity fallback. Retrieval seeds from the core, expands into the relevant sub-index. Reduces context size at query time — particularly effective at small-model scale.

Sub-index selection strategy is configurable: `deterministic` (tag-based only), `semantic` (similarity only), or `hybrid` (deterministic first, semantic fallback — default when hierarchical mode is enabled).

---

## Decay Model

When `decay` is not `None`, node importance scores are updated on a scheduled pass (not per ingest call):

```
importance = (α × access_weight) + (β × recency_weight)

access_weight  = log(1 + access_count)
recency_weight = exp(-λ × days_since_last_access)
```

**`access_count` increment semantics:** increments on every `retrieve()` call that returns the node. Does not increment on ingest. Configurable via `count_on_ingest=True` if the dev wants ingest mentions to also contribute.

**Defaults (all overridable):**

| Parameter | Default | Description |
|---|---|---|
| `α` | `0.6` | Access weight coefficient |
| `β` | `0.4` | Recency weight coefficient |
| `λ` | `0.1` | Decay rate (higher = faster decay) |
| `importance_floor` | `0.05` | Nodes below this are eligible for pruning |
| `pruning` | `False` | Pruning is **off by default** — dev opts in |
| `decay_schedule` | `None` | Dev sets cron/interval; nomem does not self-schedule |

The decay pass is a method the developer calls — `graph.run_decay()` / `await graph.arun_decay()`. nomem does not background-schedule anything without explicit configuration.

---

## Project Structure

```
nomem/
├── __init__.py             # public exports — see docs/stability.md
├── graph.py                # MemoryGraph — the public entry point
├── plugins.py              # Plugin protocol + "nomem.plugins" entry-point discovery
├── models.py               # Node, Edge, SubGraph, IngestReceipt, DecayResult, PurgeResult
├── sync.py                 # sync wrappers over async core
├── config.py               # configuration dataclasses
├── exceptions.py
├── core/
│   ├── graph.py            # node/edge CRUD logic
│   ├── decay.py            # importance scoring + pruning
│   ├── retrieval.py        # vector seed + graph traversal
│   └── extraction.py       # entity + relation extraction via LLM
├── backends/
│   ├── base.py             # abstract adapter interface
│   ├── _common.py          # shared bi-temporal filter + BFS (parity across backends)
│   ├── sqlite.py           # local/dev (zero infra)
│   ├── postgres.py         # production (pgvector)
│   └── neo4j.py            # power users
├── embedders/
│   ├── base.py             # abstract embedder interface
│   ├── nomic.py            # default (Ollama, nomic-embed-text, 768d)
│   ├── openai.py           # plain HTTP — no extra, no dependency
│   └── custom.py           # dev passes any callable
└── llms/
    ├── base.py             # BaseLLM — generate_json() for extraction
    ├── ollama.py           # default extraction adapter
    └── custom.py           # dev passes any callable
```

---

## Backend Adapter Interface

Every backend must implement:

```python
class BaseBackend(ABC):
    async def create_node(self, node: Node) -> Node: ...
    async def update_node(self, node_id: str, updates: dict) -> Node: ...
    async def retire_node(self, node_id: str, superseded_by: str | None = None) -> Node: ...
    async def get_node(self, node_id: str, as_of: datetime | None = None) -> Node | None: ...
    async def list_nodes(self, *, active_only: bool = True, context: list[str] | None = None,
                         as_of: datetime | None = None) -> list[Node]: ...
    async def upsert_edge(self, edge: Edge) -> Edge: ...
    async def retire_edge(self, edge_id: str, superseded_by: str | None = None) -> Edge: ...
    async def get_edge(self, edge_id: str, as_of: datetime | None = None) -> Edge | None: ...
    async def list_edges(self, *, active_only: bool = True,
                         as_of: datetime | None = None) -> list[Edge]: ...
    async def vector_search(self, embedding: list[float], top_k: int) -> list[Node]: ...
    async def traverse(self, seed_ids: list[str], hops: int, as_of: datetime | None = None) -> SubGraph: ...
    async def cross_reference(self, node: Node, threshold: float) -> list[tuple[Node, float]]: ...
    async def run_decay(self, config: DecayConfig) -> DecayResult: ...

    # Not abstract. Default body raises NotSupportedError. See below.
    async def purge_user(self, user_id: str) -> PurgeResult: ...
```

Any backend that implements this interface works. Devs can write their own.

**This shape is frozen at `0.1.0`.** The paid tier is a separate package that depends on
the published core and extends it; adding an abstract method here later breaks every
third-party backend and every plugin. `list_nodes`, `list_edges`, and `get_edge` exist
because enumeration — not seed-based traversal — is what graph export needs to round-trip
retired records.

### `purge_user` — the one hard-delete path

`purge_user` really deletes every row for one user. It exists so GDPR right-to-forget can
be built as a plugin instead of a fork. It is:

- **not abstract** — the default body raises `NotSupportedError`, so a custom backend
  need not implement it;
- implemented by the three bundled backends;
- **unreachable from `MemoryGraph` and `GraphCRUD`** — nothing in the public graph API
  calls it, and a guard test asserts it never appears there.

That keeps the rule below true as written: hard deletes are not exposed in the public
API. A backend handle is not the public API.

---

## Embedder Interface

```python
class BaseEmbedder(ABC):
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimensions(self) -> int: ...
```

Default: `nomic-embed-text` via Ollama (768 dimensions, free, local; smaller sizes via
Matryoshka truncation). Swappable via `embedder=` param or by passing any object implementing `BaseEmbedder`.

---

## Data Schema (Canonical)

```python
@dataclass
class Node:
    id: str                     # uuid
    user_id: str
    type: str                   # 'entity' | 'event' | 'emotion' | 'fact' | custom
    label: str
    embedding: list[float]
    importance: float
    access_count: int
    last_accessed_at: datetime
    created_at: datetime
    valid_from: datetime        # when this version of the node became true (event time)
    valid_to: datetime | None   # None = currently active; set on RETIRE
    superseded_by: str | None   # id of the node that replaced this one, if retired
    resolution_source: str      # 'llm' | 'string_match' | 'embedding' | 'manual'
    metadata: dict              # dev-defined, stored as JSON

@dataclass
class Edge:
    id: str                     # uuid
    user_id: str
    source_id: str
    target_id: str
    relation: str               # 'caused_by' | 'involves' | 'follows' | custom
    weight: float
    created_at: datetime
    valid_from: datetime        # when this edge became true (event time)
    valid_to: datetime | None   # None = currently active; set on RETIRE
    superseded_by: str | None   # id of the edge that replaced this one, if retired
    metadata: dict

@dataclass
class SubGraph:
    nodes: list[Node]           # active nodes only by default; pass as_of= for historical
    edges: list[Edge]           # active edges only by default
    metadata: dict              # hop_depth, retrieved_at, seed_node_ids, decay_scores,
                                # retrieval_mode ('flat' | 'hierarchical'), sub_index_used
```

**Bi-temporal semantics:** `valid_from` / `valid_to` track when facts were true in the world (event time). Transaction time (when nomem wrote the record) is stored in `created_at`. Querying `as_of=datetime` on `retrieve()` returns the graph state as it was known at that moment. Hard deletes are never performed — retirement is always via `valid_to`.

---

## Open Core Split

| Feature | MIT | Paid |
|---|---|---|
| Core graph CRUD | ✓ | |
| SQLite + PostgreSQL + Neo4j backends | ✓ | |
| Default nomic embedder | ✓ | |
| Decay + pruning | ✓ | |
| `ingest()` + `retrieve()` | ✓ | |
| Graph export / import | | ✓ |
| Cross-user graph queries | | ✓ |
| Retrieval quality analytics | | ✓ |
| GDPR tooling (right to forget, audit log) | | ✓ |
| Hosted managed backend (nomem Cloud) | | ✓ |
| Team namespacing + multi-tenant isolation | | ✓ |

**The paid tier is a plugin package, not a fork.** It is a separate private distribution
that depends on the published `nomem` from PyPI and extends it through the public
surface: the adapter registries and the `nomem.plugins` entry point. It contains no copy
of this source, so core updates reach paid users as an ordinary dependency bump and there
is never a merge from public to private.

Consequence: anything a paid feature needs must be **public and stable** before `0.1.0`
ships. That is why `BaseBackend` grew `list_edges` / `get_edge` / `purge_user` and why
`docs/stability.md` exists.

**Graph export / import ships first.** It is the only paid feature that is a pure
extension — no auth surface, no tenant column, no hosted infrastructure, no core change.

Selling and delivering the paid tier is a separate repository
(`no0nestudio-website`); nothing about it belongs in this one.

---

## Decisions already made — do not re-open

| Decision | Where |
|---|---|
| Core stays **MIT**; copyleft considered and rejected | this file |
| Paid tier is a **plugin package**, never a fork | Open Core Split, above |
| `BaseBackend` is **14 methods, frozen at 0.1.0** | Backend Adapter Interface, above |
| GDPR hard-delete lives in **public `BaseBackend.purge_user`**, unreachable from `MemoryGraph` | Backend Adapter Interface, above |
| Plugins attach via the **`nomem.plugins` entry point**, mounted at `graph.<namespace>` | `docs/stability.md` |
| First paid feature: **graph export / import** | Open Core Split, above |
| `ingest_mode="manual"` is a **dry run** | Ingest Pipeline, above |
| `embed_immediately` **removed**; `edge_types` and `resolution_strategy` **wired** | `docs/ROADMAP.md` |
| `OpenAIEmbedder` implemented over plain HTTP; the **`openai` extra is dropped** | `docs/ROADMAP.md` |
| nomic embedder is **768-dim** (the 384 in earlier drafts was wrong) | Embedder Interface, above |

---

## Development Phases

| Phase | Deliverable |
|---|---|
| 0 | AGENT.md, schema spec, adapter interfaces, data models — no implementation |
| 1 | SQLite backend + nomic embedder + `ingest()` + `retrieve()` working end-to-end; bi-temporal node/edge schema; entity resolution (embedding + string match); write-boundary cross-reference hook |
| 2 | PostgreSQL backend + decay pass (`run_decay()`) + hierarchical sub-index retrieval |
| 3 | Neo4j backend + sync wrappers + full async/sync parity |
| 4 | Freeze the extension surface, publish `0.1.0` to PyPI, quickstart (zero-infra SQLite demo) — see [`phases/PHASE_4.md`](phases/PHASE_4.md) |
| 5 | Paid features (graph export / import first) + nomem Cloud managed API — built as a plugin package in a separate repo |

---

## What an Agent Must Not Do

- Never hardcode a backend, embedder, model, or threshold — read from config
- Never self-schedule background tasks — expose methods, let the dev call them
- Never silently discard ingest failures — surface exceptions
- Never exceed the retrieval token budget without explicit dev override
- Never make assumptions about what "important" means — use the importance score, let the dev tune it
- Never hard-delete nodes or edges — always retire via `valid_to`; hard deletes are not exposed in the public API. `BaseBackend.purge_user` is the single, deliberately gated exception and must never become reachable from `MemoryGraph`
- Never silently merge entities on low-confidence resolution — surface ambiguous resolutions in the ingest receipt

---

## Constraints

- Python 3.11+ (uses `@dataclass`, `match`, type hints throughout)
- Async-first; sync is a wrapper layer
- No mandatory external services at install time — SQLite + nomic via Ollama covers local dev entirely
- No telemetry, no phone-home, no usage tracking in the MIT tier