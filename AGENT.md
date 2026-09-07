# AGENT.md — nomem

> No One's Memory Management  
> Maintainer: No One's Studio  
> License: MIT (core) / Proprietary (advanced features)

The design brief: the philosophy, the invariants, and the decisions that are settled. It
deliberately does **not** describe how anything is built — that lives in `docs/`, which is
the source of truth for detail:

| For | Read |
|---|---|
| Module map, and the ingest / retrieval / decay / sync / plugin pipelines | [`docs/architecture.md`](docs/architecture.md) |
| `Node` / `Edge` / `SubGraph` fields, bi-temporal semantics, reserved metadata keys | [`docs/schema.md`](docs/schema.md) |
| The frozen public API and what `>=0.1,<0.2` guarantees | [`docs/stability.md`](docs/stability.md) |
| Writing a backend, embedder, LLM, or plugin | [`docs/adapters.md`](docs/adapters.md) |
| What is left and what is still open | [`docs/roadmap.md`](docs/roadmap.md) |
| Setup, the gate, the test suite | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

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

## Decay defaults

The formula and the pass itself are in [`docs/architecture.md`](docs/architecture.md).
These values are the contract, pinned by `tests/test_config.py`:

| Parameter | Default | Description |
|---|---|---|
| `α` | `0.6` | Access weight coefficient |
| `β` | `0.4` | Recency weight coefficient |
| `λ` | `0.1` | Decay rate (higher = faster decay) |
| `importance_floor` | `0.05` | Nodes below this are eligible for pruning |
| `pruning` | `False` | Pruning is **off by default** — dev opts in |
| `decay_schedule` | `None` | Dev sets cron/interval; nomem does not self-schedule |

**`access_count` increment semantics:** increments on every `retrieve()` call that returns the node. Does not increment on ingest. Configurable via `count_on_ingest=True` if the dev wants ingest mentions to also contribute.

The decay pass is a method the developer calls — `graph.run_decay()` / `await graph.arun_decay()`. nomem does not background-schedule anything without explicit configuration.

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

**None of the paid column is built.** The split is the plan, not the present state.

**The paid tier is a plugin package, not a fork.** It is a separate private distribution
that depends on the published `nomem` from PyPI and extends it through the public
surface: the adapter registries and the `nomem.plugins` entry point. It contains no copy
of this source, so core updates reach paid users as an ordinary dependency bump and there
is never a merge from public to private.

Consequence: anything a paid feature needs must be **public and stable** before `0.1.0`
ships. That is why `BaseBackend` grew `list_edges` / `get_edge` / `purge_user` and why
[`docs/stability.md`](docs/stability.md) exists.

**Graph export / import ships first.** It is the only paid feature that is a pure
extension — no auth surface, no tenant column, no hosted infrastructure, no core change.

Selling and delivering the paid tier is a separate repository; nothing about it belongs in
this one.

---

## Decisions already made — do not re-open

| Decision | Detail |
|---|---|
| Core stays **MIT**; copyleft considered and rejected | this file |
| Paid tier is a **plugin package**, never a fork | Open Core Split, above |
| `BaseBackend` is **14 methods, frozen at 0.1.0** | [`docs/stability.md`](docs/stability.md) |
| GDPR hard-delete lives in **public `BaseBackend.purge_user`**, unreachable from `MemoryGraph` | [`docs/adapters.md`](docs/adapters.md) |
| Plugins attach via the **`nomem.plugins` entry point**, mounted at `graph.<namespace>` | [`docs/adapters.md`](docs/adapters.md) |
| First paid feature: **graph export / import** | Open Core Split, above |
| `ingest_mode="manual"` is a **dry run** | [`docs/architecture.md`](docs/architecture.md) |
| `embed_immediately` **removed**; `edge_types` and `resolution_strategy` **wired** | [`docs/roadmap.md`](docs/roadmap.md) |
| `OpenAIEmbedder` implemented over plain HTTP; the **`openai` extra is dropped** | [`docs/roadmap.md`](docs/roadmap.md) |
| nomic embedder is **768-dim** (the 384 in earlier drafts was wrong) | [`docs/adapters.md`](docs/adapters.md) |

---

## What an Agent Must Not Do

- Never hardcode a backend, embedder, model, or threshold — read from config
- Never self-schedule background tasks — expose methods, let the dev call them
- Never silently discard ingest failures — surface exceptions
- Never exceed the retrieval token budget without explicit dev override
- Never make assumptions about what "important" means — use the importance score, let the dev tune it
- Never hard-delete nodes or edges — always retire via `valid_to`; hard deletes are not exposed in the public API. `BaseBackend.purge_user` is the single, deliberately gated exception and must never become reachable from `MemoryGraph`
- Never silently merge entities on low-confidence resolution — surface ambiguous resolutions in the ingest receipt
- Never assert an example's output without running it. Two shipped examples have claimed
  behavior the code did not perform; both were written, not executed

---

## Constraints

- Python 3.11+ (uses `@dataclass`, `match`, type hints throughout)
- Async-first; sync is a wrapper layer
- No mandatory external services at install time — SQLite + nomic via Ollama covers local dev entirely
- No telemetry, no phone-home, no usage tracking in the MIT tier
