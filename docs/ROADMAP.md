# nomem — Roadmap & Open Questions

*Phases 0–3 are done ([PROJECT_STATUS.md](PROJECT_STATUS.md)). This is what's left and
what still needs deciding.*

---

## Phase 4 — Release (MIT tier)

**Goal:** anyone can `pip install nomem` and run the quickstart.

| Task | Detail | Effort |
|---|---|---|
| PyPI packaging | Fill real metadata in `pyproject.toml` (authors, URLs, `Development Status`), pick a real first version (`0.1.0`), test `uv build` + `twine check`, reserve the name | S |
| CI | GitHub Actions: `ruff` + `mypy` + hermetic `pytest` on 3.11/3.12/3.13; a separate job with `services:` for pgvector + Neo4j; optionally an Ollama job (or keep `live` manual) | M |
| Quickstart | `examples/quickstart.py` — zero-infra SQLite + a stubbed/callable LLM so it runs with no Ollama; a second example with real Ollama | S |
| Docs site | MkDocs or similar from `docs/`; API reference (mkdocstrings); the pipeline diagrams | M |
| `CHANGELOG.md` + `CONTRIBUTING.md` | — | S |
| Version pins | Confirm `neo4j` floor (resolves to 6.x today), `pgvector` floor, `pytest-asyncio` config for its 1.x line | S |
| `OpenAIEmbedder` | Finish the stub (it's the one unimplemented adapter method) or drop the `openai` extra until someone needs it | S |

### Nice-to-have before release

- Wire the **dangling config fields** so they're not misleading:
  `ingest_mode="manual"`, `IngestConfig.edge_types`, `embed_immediately`,
  `resolution_strategy`. Either implement or remove them.
- A `MemoryGraph.export()` / import for the MIT tier is *paid* per AGENT.md — but a
  minimal read-only `graph.snapshot()` for debugging would help adoption.
- `graph.stats()` — node/edge counts, active vs retired, per-type breakdown.

---

## Phase 5 — Paid tier + nomem Cloud

Per AGENT.md "Open Core Split". None started.

| Feature | Notes / prerequisites |
|---|---|
| Graph export / import | Needs a stable serialization format (JSON Lines of `Node`/`Edge`?). Should round-trip bi-temporal state. |
| Cross-user graph queries | Backends are user-scoped by construction today; this needs an explicit "admin" surface that opts out of scoping, plus auth. |
| Retrieval quality analytics | Requires logging retrieval inputs/outputs + a feedback signal. Design question: where does ground truth come from? |
| GDPR tooling (right-to-forget, audit log) | "Right to forget" **conflicts with "never hard delete"** — needs a real hard-delete path gated behind this feature only, plus a tamper-evident audit log of every CRUD op. |
| nomem Cloud managed backend | Hosted multi-tenant Postgres/Neo4j + an HTTP API mirroring `MemoryGraph`. Biggest lift. |
| Team namespacing / multi-tenant isolation | Row-level `tenant_id` above `user_id`; connection-pool-per-tenant or schema-per-tenant on Postgres. |

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

**Decision needed:** is full bi-temporal history a v1 requirement or a v2 feature?

### 2. SQLite vector search is O(n)

`SQLiteBackend.vector_search` loads every active node and computes cosine in Python.
Fine for dev and small single-user graphs; degrades past ~10⁴ nodes. Options:
`sqlite-vec` extension (adds a dependency), or accept it as the "dev backend" and point
scale users at Postgres/Neo4j. **Leaning: accept it, document it.**

### 3. Neo4j pulls all rows for traverse/decay

`traverse` / `run_decay` / `list_nodes` fetch the user's whole graph into Python to reuse
`bfs_subgraph` / `score_node` and guarantee parity. Native Cypher variable-length paths
and a set-based `SET n.importance = ...` would scale better. **Decision needed:** is
byte-for-byte parity worth the scaling ceiling, or should each backend be allowed to
diverge as long as it passes the contract suite's *observable* assertions?

### 4. Extraction quality vs. local models

`llama3.1:8b` is the default extraction model. Small models miss entities, duplicate
them, and invent relations. The resolution ambiguity band catches merges, but recall is
model-bound. Options: ship a better default prompt, support few-shot examples in
`IngestConfig`, document a recommended model per size class, or make a hosted extraction
option part of the paid tier.

### 5. `vector(N)` fixed per database

Switching embedding models on an existing Postgres/Neo4j store needs a new database. A
migration helper (`re-embed all nodes with the new model`) would be a good Phase 4/5
utility.

### 6. No batching / rate-limiting for the LLM + embedder

Each `ingest` is one LLM call + one embed batch. A high-throughput ingester would want
connection pooling, retries with backoff, and concurrency limits on the adapter side.

---

## Open design questions

| # | Question | Context |
|---|---|---|
| Q1 | Is **full transaction-time history** in scope for v1? | Drives whether §1 above is debt or a feature. |
| Q2 | Should backends be allowed to **diverge from the shared Python helpers** as long as the contract suite passes? | Affects Neo4j/Postgres scaling (§3). |
| Q3 | What's the **default extraction model / strategy**? Keep `llama3.1:8b`, or ship a smaller/faster default, or few-shot? | §4 |
| Q4 | Keep the **`openai` extra** and finish `OpenAIEmbedder`, or drop it until requested? | Phase 4 |
| Q5 | Which **dangling config fields** get implemented vs. removed before 0.1.0? (`ingest_mode`, `edge_types`, `embed_immediately`, `resolution_strategy`) | Phase 4 |
| Q6 | GDPR "right to forget" needs a **hard-delete path** — how is that reconciled with the "never hard delete" rule? Paid-tier-only escape hatch? | Phase 5 |
| Q7 | **`ingest_mode="manual"`** — what does it actually do? Return extracted entities/relations for the dev to approve before writing? | AGENT.md mentions it but doesn't define it. |
| Q8 | Should there be a **`graph.stats()` / `graph.snapshot()`** read API for debugging in the MIT tier? | Adoption / DX |
| Q9 | **Retrieval as prompt string** — AGENT.md says nomem returns the structured object and the app serializes. Ship an *optional* `SubGraph.to_prompt()` helper, or keep it strictly out? | DX vs. purity |
| Q10 | **Hierarchical sub-indexes** are tag-filtered views today. Does the vision need persisted/learned sub-index structures, or is tag routing enough? | AGENT.md "situation-specific sub-indexes" is vague |

---

## Suggested next step

**Phase 4, in this order:**

1. Decide Q4 + Q5 (small, unblocks a clean 0.1.0 API).
2. `examples/quickstart.py` + `CHANGELOG.md` + real `pyproject.toml` metadata.
3. GitHub Actions CI (hermetic + a services job).
4. `uv build`, `twine check`, publish `0.1.0` to Test PyPI, then PyPI.
5. Docs site from `docs/`.

Defer Q1/Q2/Q3 (bigger architecture calls) until there's a real user pushing on scale or
history.
