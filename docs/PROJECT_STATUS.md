# nomem — Project Status

*Snapshot date: 2026-09-03 · Version `0.0.0` (unreleased) · Branch: `main`*

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
**Phases 0–3 of the 5-phase roadmap are complete.** All three storage backends (SQLite,
PostgreSQL/pgvector, Neo4j), the nomic embedder, Ollama-based extraction, entity
resolution, the decay pass, hierarchical retrieval, and a full sync API are implemented
and pass a live test suite. What remains is packaging/release (Phase 4) and the paid /
Cloud tier (Phase 5).

---

## 2. Phase status

| Phase | Deliverable (AGENT.md) | Status |
|---|---|---|
| **0** | AGENT.md, schema spec, adapter interfaces, data models | ✅ **Done** — commit `3c46e50` |
| **1** | SQLite + nomic + `ingest()`/`retrieve()` end-to-end; bi-temporal schema; entity resolution; write-boundary cross-reference hook | ✅ **Done** — commit `cfad7df` |
| **2** | PostgreSQL backend + `run_decay()` + hierarchical sub-index retrieval | ✅ **Done** — commits `78ace9a` (decay + hierarchical), `353a92f` (Postgres) |
| **3** | Neo4j backend + sync wrappers + full async/sync parity | ✅ **Done** — commit `02abd87` |
| **4** | PyPI publish + docs site + zero-infra SQLite quickstart | ⬜ **Not started** |
| **5** | Paid features + nomem Cloud managed API | ⬜ **Not started** |

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
| `close()` | ✅ | `aclose` | releases backend pools/drivers |
| `with` / `async with` | ✅ | ✅ | context managers call `close`/`aclose` |

Construction knobs: `user_id`, `backend`, `embedder`, `llm`, `decay`, `ingest_mode`,
nested `*_config` objects, `*_options` dicts, or a prebuilt `MemoryGraphConfig`.
Per-call `config={...}` dict is merged over the relevant config section (unknown keys
raise `ConfigError`).

### Backends — all three pass one identical contract suite

| Backend | Infra | Vector search | Traversal / decay | Lifecycle |
|---|---|---|---|---|
| `sqlite` (default) | none — file or `:memory:` | Python cosine over active nodes | Python (`_common`) | sync `close()`; `sqlite3` serialized behind a lock + worker threads |
| `postgres` | Postgres + `pgvector` (`postgres` extra) | pgvector `<=>` cosine index | Python (`_common`) for parity | async `close()`; lazy `asyncpg` pool |
| `neo4j` | Neo4j 5 (`neo4j` extra) | native vector index (over-fetch + filter) | Python (`_common`) for parity | async `close()`; lazy bolt driver |

- **Bi-temporal**: `retire_node` / `retire_edge` set `valid_to` (+ `superseded_by`);
  **no hard deletes anywhere**. `get_node` / `traverse` / `list_nodes` honor `as_of`.
- **User scoping**: every row carries `user_id`; a backend instance never crosses users.
- **Networked backends** store their vector column at one fixed dimension, set on first
  schema create — **one embedding model per database** (mismatch raises a clear error).
- `MemoryGraph` injects `user_id` and the embedder's `vector_dimensions` into
  `backend_options` when resolving a backend by name.

### Embedders

| Name | Status | Dimensions |
|---|---|---|
| `nomic` (default) | ✅ implemented — Ollama `/api/embed` | **768** native; smaller via Matryoshka truncation + renormalize |
| `openai` | ⚠️ **stub** — `dimensions` works, `embed`/`embed_batch` raise `NotImplementedError` | 1536 / 3072 per model |
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
- `pytest` — **140 pass** with all backends + Ollama; **95 pass / 42 skip** with no
  external services (`-m "not live"`); see [TESTING.md](TESTING.md)

---

## 4. What is NOT done / stubbed / partial

| Item | State | Where |
|---|---|---|
| `OpenAIEmbedder.embed*` | **stub** (`NotImplementedError`) | [embedders/openai.py](../nomem/embedders/openai.py) |
| `ingest_mode="manual"` | **not wired** — stored in config, no manual code path; ingestion is always "auto" | [config.py](../nomem/config.py), [graph.py](../nomem/graph.py) |
| `IngestConfig.edge_types` | **not wired** — declared, but relations are not filtered by allowed type | [core/extraction.py](../nomem/core/extraction.py) |
| `IngestConfig.embed_immediately` | **not wired** — embedding always happens inline; no deferred/batch embed pass | [core/graph.py](../nomem/core/graph.py) |
| `IngestConfig.resolution_strategy` | **label only** — strategy is hardcoded (embedding + string); the string doesn't switch behavior | [core/extraction.py](../nomem/core/extraction.py) |
| `DecayConfig.decay_schedule` | **intentionally inert** — nomem never self-schedules; the field is for the dev's own cron | [config.py](../nomem/config.py) |
| Full transaction-time history | **simplified** — one row per node/edge id, mutated in place for `importance`/`access_count`. `as_of` filters validity window + `created_at`, but cannot return *historical field values* (e.g. importance at time T). Supersession chains (`superseded_by`) are tracked. | [backends/sqlite.py](../nomem/backends/sqlite.py) docstring |
| Hierarchical `_route_sub_index` "deterministic router based on dev-supplied context tags" | ✅ done, but **sub-indexes are just tag-filtered views** — no persisted sub-index structures, no learned routing | [core/retrieval.py](../nomem/core/retrieval.py) |
| Graph export / import | **not started** (paid tier) | — |
| Cross-user graph queries | **not started** (paid tier) | — |
| Retrieval quality analytics | **not started** (paid tier) | — |
| GDPR tooling (right-to-forget, audit log) | **not started** (paid tier) | — |
| nomem Cloud managed backend | **not started** (paid tier) | — |
| Team namespacing / multi-tenant isolation | **not started** (paid tier) | — |
| PyPI package / docs site / quickstart | **not started** (Phase 4) | — |
| CI (GitHub Actions) | **not set up** | — |
| `neo4j` extra pin | `neo4j>=5.20` resolves to **6.3.0** locally; API-compatible, works, but the floor should be raised/verified | [pyproject.toml](../pyproject.toml) |

---

## 5. Deviations from AGENT.md (all deliberate)

| AGENT.md says | nomem does | Why |
|---|---|---|
| nomic embedder is **384-dim** | **768-dim** default (truncatable) | `nomic-embed-text` natively emits 768; 384 appears to be a spec error. Matryoshka truncation to a smaller size is supported. |
| Project tree lists `core/graph.py`, `config.py`, `exceptions.py`, `sync.py` — no `models.py`, no top-level `graph.py` | Added `nomem/models.py` (data models need a home) and `nomem/graph.py` (the public `MemoryGraph`). `core/graph.py` stays the internal CRUD layer. | Data models + public class are explicit Phase 0 deliverables. |
| Extraction is just "an LLM call (cheap model, configurable)" | Added a full `nomem/llms/` adapter layer (`BaseLLM`, `OllamaLLM`, `CallableLLM`, registry) | Keeps extraction provider-agnostic and the default local/free, mirroring the embedder design. Chosen with the maintainer. |
| Backend interface: **10 methods** | **11** — added `list_nodes(active_only, context, as_of)` | Hierarchical retrieval and any "enumerate the graph" caller need it; the AGENT.md list is a floor, not a ceiling. |
| Resolution: "high confidence" / "low confidence" / "no match" | 3-band model with explicit `ambiguity_floor` + `string_match_min` knobs | Makes "low confidence" vs "no match" a tunable boundary rather than a hidden constant. |
| `MemoryGraph(...)` snippet shows `backend`, `embedder`, `decay`, `ingest_mode` | Also accepts `llm=`, `*_options` dicts, nested config objects, `context=` on ingest/retrieve, `config=` prebuilt | Additive; the AGENT.md snippet still works verbatim. |
| Added helper modules | `nomem/_http.py`, `nomem/_vector.py`, `nomem/backends/_common.py` | Zero-dep HTTP, vector math, and shared bi-temporal/BFS logic so the three backends stay behaviorally identical. |

**No deviations** on the hard rules ("What an Agent Must Not Do"): no hardcoded
backend/embedder/model/threshold (all from config), no self-scheduled background work, no
silent ingest-failure swallowing, no hard deletes, ambiguous resolutions surfaced in the
receipt, async-first with sync as a thin wrapper.

---

## 6. Repository layout

```
nomem/                     3,761 lines of implementation (13 modules)
├── graph.py       (202)   public MemoryGraph — async core + sync wrappers + context mgrs
├── models.py      (161)   Node, Edge, SubGraph, IngestReceipt, DecayResult, intermediates
├── config.py      (130)   MemoryGraphConfig + Decay/Ingest/RetrievalConfig + merge()
├── sync.py         (74)   run_sync() on one dedicated background event loop
├── exceptions.py   (61)   NomemError hierarchy
├── _http.py        (53)   stdlib async JSON POST (Ollama transport)
├── _vector.py      (58)   pack/unpack float blobs, cosine, mean
├── core/
│   ├── extraction.py (229)  EntityExtractor + EntityResolver
│   ├── retrieval.py  (235)  Retriever (flat + hierarchical)
│   ├── graph.py      (238)  GraphCRUD orchestration
│   └── decay.py       (65)  score_node() + DecayEngine
├── backends/
│   ├── sqlite.py     (486)  default, zero-infra
│   ├── postgres.py   (505)  asyncpg + pgvector
│   ├── neo4j.py      (483)  bolt driver + vector index
│   ├── base.py        (88)  BaseBackend ABC (11 methods)
│   ├── _common.py     (78)  active_at() + bfs_subgraph()
│   └── __init__.py    (49)  registry + resolve_backend()
├── embedders/  base.py, nomic.py, openai.py (stub), custom.py, __init__.py
└── llms/       base.py, ollama.py, custom.py, __init__.py

tests/                     1,646 lines, 140 tests (see TESTING.md)
docs/                      architecture, schema, adapters, PROJECT_STATUS, TESTING, ROADMAP
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
| Optional extras | `postgres` (`asyncpg>=0.29`, `pgvector>=0.3`), `neo4j` (`neo4j>=5.20`), `openai` (`openai>=1.30`) |
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
