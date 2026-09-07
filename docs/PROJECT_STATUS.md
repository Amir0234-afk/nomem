# nomem — Project Status

*Snapshot date: 2026-09-07 · Version `0.0.0` (unreleased) · Branch: `master`*

This document is the single source of truth for **where the project stands**: what is
built, what works, what is stubbed, where the implementation deviates from
[AGENT.md](../AGENT.md), and what is left. For design detail see
[architecture.md](architecture.md); for the schema see [schema.md](schema.md); for the
test picture see [TESTING.md](TESTING.md); for what comes next see [ROADMAP.md](ROADMAP.md).

---

## 1. One-paragraph summary

`nomem` is a Python library that gives an LLM application persistent, per-user memory as a
**bi-temporal knowledge graph** with an LLM-driven CRUD interface. You feed it conversation
turns; it extracts entities + relations, resolves them against the existing graph, writes
real CREATE / UPDATE / RETIRE operations, and returns bounded subgraphs at query time.
**Phases 0–3 are complete, and so are Phase 4 Parts A and B.** All three storage backends
(SQLite, PostgreSQL/pgvector, Neo4j), the nomic and OpenAI embedders, Ollama-based
extraction, entity resolution, the decay pass, hierarchical retrieval, a full sync API,
the frozen 14-method backend contract, and the plugin attach hook are implemented and
pass a live test suite. What remains is packaging/publishing (Phase 4 Part C) and the
paid / Cloud tier (Phase 5).

---

## 2. Phase status

| Phase | Deliverable (AGENT.md) | Status |
|---|---|---|
| **0** | AGENT.md, schema spec, adapter interfaces, data models | ✅ **Done** — commit `3c46e50` |
| **1** | SQLite + nomic + `ingest()`/`retrieve()` end-to-end; bi-temporal schema; entity resolution; write-boundary cross-reference hook | ✅ **Done** — commit `cfad7df` |
| **2** | PostgreSQL backend + `run_decay()` + hierarchical sub-index retrieval | ✅ **Done** — commits `78ace9a` (decay + hierarchical), `353a92f` (Postgres) |
| **3** | Neo4j backend + sync wrappers + full async/sync parity | ✅ **Done** — commit `02abd87` |
| **4** | Freeze the extension surface + PyPI publish + zero-infra SQLite quickstart | 🟨 **Parts A + B done** (surface frozen, config resolved); **Part C not started** — [`../phases/PHASE_4.md`](../phases/PHASE_4.md) |
| **5** | Paid features (export / import first) + nomem Cloud managed API | ⬜ **Not started** — built as a plugin package in a separate repo |

Commit history:

```
02abd87  Phase 3: Neo4j backend + full sync/async parity
353a92f  Phase 2b: PostgreSQL + pgvector backend
78ace9a  Phase 2a: decay pass + hierarchical retrieval
cfad7df  Phase 1: SQLite backend + nomic embedder + Ollama extraction, end-to-end
aae6d8b  Add .gitattributes to normalize line endings to LF
3c46e50  Phase 0: architecture scaffold
```

---

## 3. What works today

### Public API (`nomem.MemoryGraph`)

| Method | Sync | Async | Notes |
|---|---|---|---|
| `ingest(user, assistant, config=None, context=None)` | ✅ | `aingest` | extract → resolve → CRUD → optional cross-reference; returns `IngestReceipt` |
| `retrieve(query, config=None, as_of=None, context=None)` | ✅ | `aretrieve` | flat or hierarchical; returns `SubGraph`; bumps `access_count` (not when `as_of` set) |
| `run_decay(config=None)` | ✅ | `arun_decay` | rescores importance; optional pruning; returns `DecayResult` |
| `stats()` | ✅ | `astats` | node/edge counts, active vs retired, per-type breakdown. **Not** an export — that is the paid tier |
| `close()` | ✅ | `aclose` | releases backend pools/drivers |
| `with` / `async with` | ✅ | ✅ | context managers call `close`/`aclose` |

Construction knobs: `user_id`, `backend`, `embedder`, `llm`, `decay`, `ingest_mode`,
`plugins`, nested `*_config` objects, `*_options` dicts, or a prebuilt
`MemoryGraphConfig`. Per-call `config={...}` dict is merged over the relevant config
section (unknown keys raise `ConfigError`).

Public attributes (plugins reach the graph through them): `.user_id`, `.backend`,
`.embedder`, `.llm`, `.config`, `.plugins`.

`ingest_mode="manual"` is a **dry run**: extract and resolve, write nothing, and return a
receipt whose `queued_writes` carries every intended operation, each entity tagged with
`metadata["intended_op"]` (`create` / `update` / `retire` / `queue` / `noop`) and
`metadata["resolved_node_id"]`.

### Plugins

`nomem/plugins.py` discovers the `nomem.plugins` entry-point group at construction,
calls `attach(graph)` on each, and mounts the result at `graph.<namespace>` (recorded in
`graph.plugins`). `plugins=False` skips discovery; a namespace collision raises
`ConfigError`; an import or attach failure propagates. This is the paid tier's only
attachment point — see [stability.md](stability.md).

### Backends — all three pass one identical contract suite

| Backend | Infra | Vector search | Traversal / decay | Lifecycle |
|---|---|---|---|---|
| `sqlite` (default) | none — file or `:memory:` | Python cosine over active nodes | Python (`_common`) | sync `close()`; `sqlite3` serialized behind a lock + worker threads |
| `postgres` | Postgres + `pgvector` (`postgres` extra) | pgvector `<=>` cosine index | Python (`_common`) for parity | async `close()`; lazy `asyncpg` pool |
| `neo4j` | Neo4j 5 (`neo4j` extra) | native vector index (over-fetch + filter) | Python (`_common`) for parity | async `close()`; lazy bolt driver |

- **Bi-temporal**: `retire_node` / `retire_edge` set `valid_to` (+ `superseded_by`);
  **no hard deletes** through the graph API. `get_node` / `get_edge` / `traverse` /
  `list_nodes` / `list_edges` honor `as_of`.
- **Enumeration**: `list_nodes` / `list_edges` with `active_only=False` return retired
  records too, and inserts preserve caller-supplied ids and timestamps verbatim — so a
  dump round-trips (verified per backend by the contract suite).
- **`purge_user`**: the one real hard-delete path, implemented by all three, guarded to
  the backend's own user, and unreachable from `MemoryGraph` / `GraphCRUD`.
- **User scoping**: every row carries `user_id`; a backend instance never crosses users.
- **Networked backends** store their vector column at one fixed dimension, set on first
  schema create — **one embedding model per database** (mismatch raises a clear error).
- `MemoryGraph` injects `user_id` and the embedder's `vector_dimensions` into
  `backend_options` when resolving a backend by name.

### Embedders

| Name | Status | Dimensions |
|---|---|---|
| `nomic` (default) | ✅ implemented — Ollama `/api/embed` | **768** native; smaller via Matryoshka truncation + renormalize |
| `openai` | ✅ implemented — plain HTTP to `/v1/embeddings` via `_http.py`; **no extra, no dependency**. Key from `api_key=` or `OPENAI_API_KEY`, required only at call time | 1536 / 3072 per model; `-3` models accept a truncated size |
| `CallableEmbedder` | ✅ implemented — wraps any sync/async `str -> vector` callable | dev-supplied |

### LLM adapters (extraction)

> **Not in AGENT.md's file tree** — added as an adapter boundary so extraction is
> pluggable and the default stays local/free (see §5).

| Name | Status |
|---|---|
| `ollama` (default) | ✅ implemented — `/api/chat` with JSON-schema `format`; default model `llama3.1:8b` |
| `CallableLLM` | ✅ implemented — wraps any `prompt -> dict|json-str` callable |

### Pipelines

- **Extraction** ([core/extraction.py](../nomem/core/extraction.py)) — one LLM call with a
  fixed system prompt + JSON schema; defensive parsing (drops malformed entities,
  relations with unknown endpoints, dedupes case-insensitively).
- **Resolution** ([core/extraction.py](../nomem/core/extraction.py)) — `score =
  max(embedding cosine, near-exact string ratio)`; three bands:
  `≥ resolution_confidence_threshold` → resolve; `[ambiguity_floor, threshold)` →
  **ambiguous** (never silently merged — queued or created per `on_ambiguous`);
  `< ambiguity_floor` → treated as new. String match only counts at
  `≥ string_match_min` (default 0.8) so coincidental shared letters don't flag every
  new entity.
- **CRUD** ([core/graph.py](../nomem/core/graph.py)) — maps resolution outcomes to
  CREATE / UPDATE / RETIRE; batch-embeds new labels; upserts edges whose endpoints both
  resolved this turn (edge weight accumulates on repeat); opt-in `cross_reference_pass`.
- **Retrieval** ([core/retrieval.py](../nomem/core/retrieval.py)) — flat (vector seed →
  traverse) or hierarchical (core index + situation sub-index routing:
  deterministic / semantic / hybrid); token-budget trimming by dropping
  lowest-importance nodes.
- **Decay** ([core/decay.py](../nomem/core/decay.py)) — `score_node()` is the shared
  formula; `DecayConfig.mode` (`time`/`access`/`combined`/`None`) zeroes a term;
  pruning retires sub-floor nodes only when `pruning=True`.

### Quality gates (all green)

- `ruff` (E, F, I, UP, B, SIM, RUF) — clean
- `mypy --strict` + `warn_unreachable` — clean, 28 source files
- `pytest` — **178 pass** with all backends + Ollama; **123 pass / 52 skip** with no
  external services (`-m "not live"`); see [TESTING.md](TESTING.md)

---

## 4. What is NOT done / stubbed / partial

### 4a. The frozen surface — ✅ built (Phase 4 Part A)

These were the irreversible ones: `BaseBackend` cannot gain an abstract method after
release without breaking every third-party backend and every plugin. All landed; the
shape below is what `0.1.0` freezes. Spec:
[`../phases/PHASE_4.md`](../phases/PHASE_4.md) Part A.

| Item | State | Where |
|---|---|---|
| `BaseBackend.list_edges(*, active_only, as_of)` | ✅ all three backends + contract test. Export could not round-trip retired edges without it; `traverse` is seed-based and active-only | [backends/base.py](../nomem/backends/base.py) |
| `BaseBackend.get_edge(edge_id, as_of)` | ✅ symmetric with `get_node` | same |
| `BaseBackend.purge_user(user_id) -> PurgeResult` | ✅ non-abstract (default raises `NotSupportedError`), implemented by the bundled three, guarded to the backend's own user, **unreachable from `MemoryGraph`** — enforced by `test_no_hard_delete.py`. ROADMAP Q6 | same |
| `nomem/plugins.py` | ✅ `Plugin` protocol + `nomem.plugins` entry-point discovery, mounted at `graph.<namespace>` | [plugins.py](../nomem/plugins.py) |
| Verbatim-timestamp inserts | ✅ **verified**, no code change needed — all three already persisted caller-supplied `id` / `created_at` / `valid_from` / `valid_to` / `superseded_by`. Now locked by `test_insert_preserves_supplied_timestamps` + `test_export_import_round_trip` | all three backends + contract suite |
| `__all__` ↔ `stability.md` | ✅ a test parses the Public table and fails on drift | [test_public_api.py](../tests/test_public_api.py) |

### 4b. The dangling config — ✅ resolved (Phase 4 Part B)

| Item | State | Where |
|---|---|---|
| `OpenAIEmbedder.embed*` | ✅ implemented over `_http.py`; the `openai` extra is **deleted** (ROADMAP Q4) | [embedders/openai.py](../nomem/embedders/openai.py) |
| `ingest_mode="manual"` | ✅ a **dry run** — extract, resolve, write nothing, report every intended op in `queued_writes` (ROADMAP Q7) | [graph.py](../nomem/graph.py), [core/graph.py](../nomem/core/graph.py) `plan_receipt` |
| `IngestConfig.edge_types` | ✅ wired — relations outside the allowed set are dropped and counted in `IngestReceipt.relations_dropped` | [core/extraction.py](../nomem/core/extraction.py) |
| `IngestConfig.embed_immediately` | ✅ **removed**. Deferred embedding needs a pending queue and a re-embed pass — a feature, not a flag | [config.py](../nomem/config.py) |
| `IngestConfig.resolution_strategy` | ✅ wired — `"embedding"` / `"string"` / `"hybrid"` (default; `max()` of both), validated in `__post_init__` | [core/extraction.py](../nomem/core/extraction.py) |
| `MemoryGraph.stats()` | ✅ shipped (counts, active vs retired, per-type). No `snapshot()` — that is paid export (ROADMAP Q8) | [graph.py](../nomem/graph.py) |

### 4c. Everything else

| Item | State | Where |
|---|---|---|
| `DecayConfig.decay_schedule` | **intentionally inert** — nomem never self-schedules; the field is for the dev's own cron, and the docstring now says so | [config.py](../nomem/config.py) |
| Full transaction-time history | **simplified** — one row per node/edge id, mutated in place for `importance`/`access_count`. `as_of` filters validity window + `created_at`, but cannot return *historical field values* (e.g. importance at time T). Supersession chains (`superseded_by`) are tracked. | [backends/sqlite.py](../nomem/backends/sqlite.py) docstring |
| Hierarchical `_route_sub_index` "deterministic router based on dev-supplied context tags" | ✅ done, but **sub-indexes are just tag-filtered views** — no persisted sub-index structures, no learned routing | [core/retrieval.py](../nomem/core/retrieval.py) |
| Graph export / import | **not started** — paid tier, **ships first**; separate plugin package. Both prerequisites (`list_edges(active_only=False)`, verbatim-timestamp inserts) are now in place | — |
| GDPR tooling (right-to-forget, audit log) | **not started** — paid tier, second. The core-side hatch (`purge_user`) has landed; the tamper-evident audit log has not | — |
| Retrieval quality analytics | **not started** — paid tier, later. Needs retrieval hooks (additive) | — |
| Cross-user graph queries | **not started** — paid tier, later | — |
| Team namespacing / multi-tenant isolation | **not started** — paid tier, later | — |
| nomem Cloud managed backend | **not started** — paid tier, last | — |
| PyPI package / quickstart / `CHANGELOG` | **not started** (Phase 4 Part C). The name `nomem` was unclaimed on PyPI as of 2026-09-07. `LICENSE` (MIT) is in place; `version` is still `0.0.0` | [pyproject.toml](../pyproject.toml) |
| Docs site | **not started** — explicitly **not** a release blocker | — |
| CI (GitHub Actions) | **not set up** | — |
| `neo4j` extra pin | `neo4j>=5.20` resolves to **6.3.0** locally; API-compatible, works, but the floor should be raised/verified | [pyproject.toml](../pyproject.toml) |

---

## 5. Deviations from AGENT.md (all deliberate)

Earlier deviations — the 384-dim embedder, the missing `models.py` / top-level `graph.py`,
the `nomem/llms/` adapter layer, `backends/_common.py`, and the 11-method backend
interface — have all been closed, either by folding them into AGENT.md or (for the
interface) by building the missing three methods. What remains:

| AGENT.md says | nomem does | Why |
|---|---|---|
| Resolution: "high confidence" / "low confidence" / "no match" | 3-band model with explicit `ambiguity_floor` + `string_match_min` knobs | Makes "low confidence" vs "no match" a tunable boundary rather than a hidden constant |
| `MemoryGraph(...)` snippet shows `backend`, `embedder`, `decay`, `ingest_mode` | Also accepts `llm=`, `plugins=`, `*_options` dicts, nested config objects, `context=` on ingest/retrieve, `config=` prebuilt | Additive; the AGENT.md snippet still works verbatim |
| Ingest receipt fields | Also carries `relations_dropped` (edge-type filtering) | Additive; a narrowed edge vocabulary is visible rather than silent |
| Helper modules unlisted | `nomem/_http.py`, `nomem/_vector.py` | Zero-dep HTTP and vector math. Private by [stability.md](stability.md) — no outside code may depend on them |

**No deviations** on the hard rules ("What an Agent Must Not Do"): no hardcoded
backend/embedder/model/threshold (all from config), no self-scheduled background work, no
silent ingest-failure swallowing, no hard deletes, ambiguous resolutions surfaced in the
receipt, async-first with sync as a thin wrapper.

`purge_user` is not an exception to the hard-delete rule as written — the rule is that
hard deletes are not exposed in the *public API*, and nothing in `MemoryGraph` or
`GraphCRUD` can reach it. See [adapters.md](adapters.md) and ROADMAP Q6.

---

## 6. Repository layout

```
nomem/                     4,266 lines of implementation (14 modules)
├── graph.py       (245)   public MemoryGraph — async core + sync wrappers + context mgrs
├── plugins.py      (93)   Plugin protocol + "nomem.plugins" entry-point discovery
├── models.py      (179)   Node, Edge, SubGraph, IngestReceipt, DecayResult, PurgeResult
├── config.py      (135)   MemoryGraphConfig + Decay/Ingest/RetrievalConfig + merge()
├── sync.py         (74)   run_sync() on one dedicated background event loop
├── exceptions.py   (70)   NomemError hierarchy
├── _http.py        (63)   stdlib async JSON POST (Ollama + OpenAI transport)
├── _vector.py      (58)   pack/unpack float blobs, cosine, mean
├── core/
│   ├── extraction.py (249)  EntityExtractor + EntityResolver
│   ├── retrieval.py  (235)  Retriever (flat + hierarchical)
│   ├── graph.py      (278)  GraphCRUD orchestration + plan_receipt() (manual dry run)
│   └── decay.py       (65)  score_node() + DecayEngine
├── backends/
│   ├── sqlite.py     (545)  default, zero-infra
│   ├── postgres.py   (546)  asyncpg + pgvector
│   ├── neo4j.py      (543)  bolt driver + vector index
│   ├── base.py       (135)  BaseBackend ABC — the frozen 14 methods
│   ├── _common.py     (78)  active_at() + bfs_subgraph()
│   └── __init__.py    (49)  registry + resolve_backend()
├── embedders/  base.py, nomic.py, openai.py, custom.py, __init__.py
└── llms/       base.py, ollama.py, custom.py, __init__.py

tests/                     2,226 lines, 178 tests (see TESTING.md)
docs/                      architecture, schema, adapters, stability, PROJECT_STATUS,
                           TESTING, ROADMAP
phases/                    PHASE_4.md (the release spec)
docker-compose.yml         dedicated nomem-postgres (:5433) + nomem-neo4j (:7688)
```

---

## 7. Environment & tooling

| | |
|---|---|
| Python | `>=3.11` (developed/tested on 3.11.15; classifiers cover 3.11–3.13) |
| Package manager | `uv` (0.11.x) |
| Build backend | `hatchling` |
| Core runtime deps | **none** |
| Optional extras | `postgres` (`asyncpg>=0.29`, `pgvector>=0.3`), `neo4j` (`neo4j>=5.20`). The `openai` extra is **gone** — that embedder is plain HTTP over `_http.py` |
| Dev deps | `pytest>=8.2`, `pytest-asyncio>=0.23`, `ruff>=0.5`, `mypy>=1.10` |
| Lint | `ruff` — E, F, I, UP, B, SIM, RUF; line length 100 |
| Types | `mypy --strict` + `warn_unreachable`; `asyncpg.*`/`pgvector.*`/`neo4j.*` set `ignore_missing_imports` |
| Local services | Ollama (`nomic-embed-text`, `llama3.1:8b`); Docker for pgvector + Neo4j |

### Test containers (dedicated, throwaway)

`docker compose up -d` starts:

- **`nomem-postgres`** — `pgvector/pgvector:pg16`, bolt on `localhost:5433`,
  `nomem/nomem`, ephemeral (tmpfs). DSN: `postgresql://nomem:nomem@localhost:5433/nomem`
- **`nomem-neo4j`** — `neo4j:5.26`, bolt on `localhost:7688`, HTTP on `localhost:7475`,
  auth `neo4j/nomemtest123`, ephemeral (tmpfs)

These are **separate from any other Postgres/Neo4j** on the machine (non-default ports).

---

## 8. Known risks / things to watch

1. **Transaction-time history is simplified** (§4). If a real use case needs "what did the
   graph believe at time T, including scores", the single-row model needs to become an
   append-only version table. The public API (`as_of=`) already anticipates it.
2. **Python-side vector search on SQLite** is O(n) per query — fine for dev, not for large
   single-user graphs. Postgres/Neo4j use native indexes.
3. **Extraction quality depends on the local model.** `llama3.1:8b` produces reasonable
   graphs in smoke tests but small models miss/duplicate entities; resolution's ambiguity
   band is the safety net (surfaces rather than merges).
4. **`neo4j` traverse/decay pull all of a user's rows** into Python. Native Cypher
   variable-length paths would scale better but risk diverging from `bfs_subgraph`
   semantics; parity was prioritized.
5. **No CI yet** — the 3-backend + Ollama suite only runs where those services exist.
6. **`vector(N)` is fixed per database.** Switching embedding models on an existing
   Postgres/Neo4j store requires a new database or a manual column migration.

7. **The extension surface is frozen the moment `0.1.0` is published.** It is now built
   (§4a) but unpublished, so it is still cheap to change — and stops being so at the
   first upload. The proprietary tier is a separate package that depends on the published
   core; anything it needs must be public and stable first. Getting it wrong means either
   a fork (the failure mode the whole design exists to avoid) or a `0.2.0` that breaks
   every third-party backend. Last chance to review is before Part C.

---

## 9. How to run it right now

```bash
uv sync --extra postgres --extra neo4j          # full dev env
ollama pull nomic-embed-text && ollama pull llama3.1:8b
docker compose up -d                            # optional: pgvector + neo4j

# fast, hermetic
uv run pytest -q -m "not live and not postgres and not neo4j"

# everything
export NOMEM_TEST_POSTGRES_DSN=postgresql://nomem:nomem@localhost:5433/nomem
export NOMEM_TEST_NEO4J_URI=bolt://localhost:7688
export NOMEM_TEST_NEO4J_AUTH=neo4j:nomemtest123
uv run pytest -q

uv run ruff check . && uv run mypy nomem
```

```python
from nomem import MemoryGraph

with MemoryGraph(user_id="u1", backend="sqlite",
                 backend_options={"path": "mem.sqlite"}) as g:
    g.ingest("What's up with Kira?", "Kira moved from Berlin to Lisbon.", context=["people"])
    sub = g.retrieve("Where does Kira live?")
    g.run_decay()
```