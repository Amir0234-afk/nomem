# nomem — Roadmap & Open Questions

*What is left, and what still needs deciding. For what exists today, read the
[CHANGELOG](../CHANGELOG.md); for what is guaranteed not to move, read
[stability.md](stability.md).*

---

## Release

Everything needed for `0.1.0` is built: the extension surface is frozen at its final
shape, no config field is inert, and the package builds, passes `twine check`, installs
into a clean venv from a wheel, and runs its quickstart with no extras. CI covers
3.11–3.13, real pgvector and Neo4j, and that wheel-install smoke test.

**What remains is the upload itself.** In order:

- [ ] Reserve `nomem` on PyPI — it was unclaimed as of 2026-09-07. Do this first.
- [ ] Publish to Test PyPI, install from it into a clean venv, run the quickstart.
- [ ] Publish `0.1.0` to PyPI.

The extension surface is free to change right up until that upload, and fixed afterwards:
the proprietary tier is a separate package depending on the published core, so anything it
needs must be public and stable *first*. Getting it wrong means either a fork — the failure
mode the whole design exists to prevent — or a `0.2.0` that breaks every third-party
backend. **The last cheap moment to review it is before the first upload.**

A docs site (MkDocs + mkdocstrings) is **not** a release blocker; `README.md` plus this
`docs/` tree is enough for `0.1.0`.

---

## Phase 5 — Paid tier + nomem Cloud

Built as a **separate private package** that depends on the published `nomem` and extends
it through the adapter registries and the `nomem.plugins` entry point. It contains no copy
of this source; core updates reach paid users as a dependency bump. Nothing about pricing,
delivery, or the storefront belongs in this repository.

**Sequencing: graph export / import ships first.** It is the only feature on the list that
is a pure extension — no auth surface, no tenant column, no hosted infrastructure, no core
modification.

| Feature | Order | Notes / prerequisites |
|---|---|---|
| Graph export / import | **1st** | JSON Lines of `Node` / `Edge`. Round-trips bi-temporal state. Both prerequisites — `list_edges(active_only=False)` and verbatim-timestamp inserts — are **in place and contract-tested** |
| GDPR tooling (right-to-forget, audit log) | 2nd | Unblocked: `BaseBackend.purge_user` has landed. Still needs the tamper-evident audit log |
| Retrieval quality analytics | later | Needs retrieval logging hooks (additive, non-breaking, so not a Phase 4 blocker) + an answer to where ground truth comes from |
| Cross-user graph queries | later | Backends are user-scoped by construction; needs an explicit admin surface that opts out of scoping, plus auth |
| Team namespacing / multi-tenant isolation | later | Row-level `tenant_id` above `user_id`; pool-per-tenant or schema-per-tenant on Postgres |
| nomem Cloud managed backend | last | Hosted multi-tenant Postgres/Neo4j + an HTTP API mirroring `MemoryGraph`. Biggest lift |

### The honest tension in shipping export first

`list_nodes` and `list_edges` are public because the plugin model requires them. That
makes a DIY graph dump about thirty lines of user code. The paid product is therefore not
"access to the rows" — it is the stable format, the round-trip guarantee, cross-backend
migration, and re-embedding on model change. This is the same accepted trade already on
record: the paid tier can be reimplemented, and that was priced in going in.

---

## Known limitations to resolve (technical debt)

### 1. Transaction-time history is simplified

Today each node/edge id is **one mutable row**. `valid_from`/`valid_to` track event-time
validity and `superseded_by` records supersession chains, but `importance` and
`access_count` are overwritten in place — so `retrieve(as_of=T)` returns *which nodes
were active at T*, not *what their scores were at T*.

**If a use case needs full history:** move to an append-only version table
(`(id, version, ...)` with `valid_from`/`valid_to` per version). The public API
(`as_of=`) already anticipates this; the backend contract + `_common.active_at` would
need to select the right version rather than filter one row. All three backends and the
contract suite would change together.

**Still open — Q1.** Note this is a `0.2.0` change either way: it alters what
`BaseBackend` implementations must *do*, even though the signatures survive.

### 2. SQLite vector search is O(n)

`SQLiteBackend.vector_search` loads every active node and computes cosine in Python.
Fine for dev and small single-user graphs; degrades past ~10⁴ nodes. Options:
`sqlite-vec` extension (adds a dependency), or accept it as the "dev backend" and point
scale users at Postgres/Neo4j. **Decided: accept it, document it in the README.**

### 3. Neo4j pulls all rows for traverse/decay

`traverse` / `run_decay` / `list_nodes` fetch the user's whole graph into Python to reuse
`bfs_subgraph` / `score_node` and guarantee parity. Native Cypher variable-length paths
and a set-based `SET n.importance = ...` would scale better. **Still open — Q2.**

### 4. Extraction quality vs. local models

`llama3.1:8b` is the default extraction model. Small models miss entities, duplicate
them, and invent relations. The resolution ambiguity band catches merges, but recall is
model-bound. Options: support few-shot examples in `IngestConfig`, document a recommended
model per size class, or make a hosted extraction option part of the paid tier.
**Still open — Q3.** All are additive, so none blocks `0.1.0`.

This is not hypothetical. `llama3.1:8b` originally returned `negated=false` for *every*
phrasing tried — "has left", "no longer lives in", "moved out of", "ended" — which
silently disabled retirement, the feature the whole graph model exists for. An emphatic
rewrite of the `negated` instruction in `_SYSTEM_PROMPT` fixed it; making `negated` a
required schema field did not. The lesson generalizes: **prompt wording is load-bearing on
small models, and a scripted `FakeLLM` will never reveal it.** Behavior that depends on
the model needs a `live` test.

### 5. `vector(N)` fixed per database

Switching embedding models on an existing Postgres/Neo4j store needs a new database. The
re-embed migration helper is a natural companion to paid export/import — build it there.
Now that `OpenAIEmbedder` ships (1536/3072-dim) alongside nomic (768), hitting this is a
matter of when, not if.

### 6. No batching / rate-limiting for the LLM + embedder

Each `ingest` is one LLM call + one embed batch. A high-throughput ingester would want
connection pooling, retries with backoff, and concurrency limits on the adapter side.
Adapter-local, additive, non-breaking.

### 7. Hierarchical sub-indexes are tag-filtered views

`Retriever._route_sub_index` works as documented — deterministic, semantic, or hybrid
routing over `metadata["context"]` — but a "sub-index" is a filtered view computed per
query, not a persisted or learned structure. Whether the vision needs more than that is
**Q10**.

### 8. Driver exceptions escape the `NomemError` hierarchy

`nomem/exceptions.py` promises that everything nomem raises derives from `NomemError`, so
one `except` covers the surface. That is false for the most common real failure: a
database that is down or rejects credentials surfaces `asyncpg.InvalidPasswordError`,
`neo4j.AuthError`, or a bare `ConnectionRefusedError`. An application following the
documented advice will crash on an outage, and backend-agnostic error handling is not
currently possible. The fix — wrapping connection failures in `BackendError` in
`_build_pool` / `_build_driver` — is purely additive.

### 9. `_http.py` has no total deadline or response cap

`urlopen(timeout=...)` bounds each socket operation, not the request, so a server that
trickles bytes holds a worker thread indefinitely; `resp.read()` is unbounded, so a very
large response is buffered whole. Both are bounded in practice by the endpoints being a
local Ollama and the OpenAI API, which are developer-configured — but `host` / `base_url`
must never be attacker-controlled, and that should be stated in the adapter docs.

---

## Decided

**Q4–Q8 are now shipped**, not just decided — each one landed in Phase 4 Part A or B as
described below. They stay on this page as the record of *why* the code looks the way it
does; do not re-open them.

| # | Question | Decision |
|---|---|---|
| Q4 | Keep the `openai` extra and finish `OpenAIEmbedder`, or drop it? | **Implement it over `nomem/_http.py` and delete the extra.** ~30 lines of plain HTTP to `/v1/embeddings`; "zero mandatory dependencies" stays true and the most-requested embedder works out of the box. Shipping a `NotImplementedError` in `0.1.0` was the worst of both |
| Q5 | Which dangling config fields get implemented vs. removed? | `edge_types` → **wire** (filter relations to the allowed set; count drops in the receipt). `resolution_strategy` → **wire** (`"embedding"` / `"string"` / `"hybrid"`; hybrid default = today's `max()`). `ingest_mode="manual"` → **implement**, see Q7. `embed_immediately` → **remove**; deferred embedding needs a pending queue and a re-embed pass, which is a feature, not a flag. `decay_schedule` stays inert **by design** — it is metadata for the dev's own cron and the docstring must say so |
| Q6 | How is GDPR right-to-forget reconciled with "never hard delete"? | **`BaseBackend.purge_user`, public, non-abstract, in `0.1.0`.** Default body raises `NotSupportedError`; the three bundled backends implement it; it is unreachable from `MemoryGraph` / `GraphCRUD` and a guard test enforces that. Building it in the private package instead makes the fork unavoidable — the one thing the plugin model cannot survive. See [adapters.md](adapters.md) |
| Q7 | What does `ingest_mode="manual"` do? | **A dry run.** Extract and resolve, write nothing, return an `IngestReceipt` with every intended operation in `queued_writes`. ~15 lines, and AGENT.md advertises the knob in the public snippet, so it cannot simply be deleted |
| Q8 | Ship `graph.stats()` / `graph.snapshot()` in the MIT tier? | **`stats()` yes** (node/edge counts, active vs retired, per-type breakdown — cheap, pure DX). **`snapshot()` no** — that is graph export, the first paid feature. `list_nodes` / `list_edges` already make a DIY dump possible; see the tension noted above |
| — | Which paid feature ships first? | **Graph export / import.** See Phase 5 |
| — | Is the backend contract frozen? | **Yes, at 14 methods, from `0.1.0`.** See [stability.md](stability.md) |

## Still open

| # | Question | Context | Blocks 0.1.0? |
|---|---|---|---|
| Q1 | Is **full transaction-time history** in scope for v1? | §1 | No — a `0.2.0` change either way |
| Q2 | May backends **diverge from the shared Python helpers** as long as the contract suite passes? | §3 | No |
| Q3 | What's the **default extraction model / strategy**? | §4 | No — all options additive |
| Q9 | **Retrieval as prompt string** — ship an optional `SubGraph.to_prompt()`, or keep it strictly out? | DX vs. purity; additive whenever answered | No |
| Q10 | **Hierarchical sub-indexes** are tag-filtered views today. Does the vision need persisted or learned structures? | AGENT.md's "situation-specific sub-indexes" is vague | No |

Defer all five. None touches the frozen surface.

---

## Suggested next step

1. ~~Part A — the frozen surface~~ and ~~Part B~~ are done.
2. **Review the frozen surface once more** — after publish, changing it costs a `0.2.0`.
3. Part C: reserve `nomem` on PyPI, bump to `0.1.0`, quickstarts, CHANGELOG, CI, then
   Test PyPI → PyPI. Make the GitHub repo public.
4. Start the paid plugin package (export / import) in its own private repo.