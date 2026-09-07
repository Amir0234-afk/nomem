# nomem — Test Suite

*Snapshot: 178 tests · `pytest-asyncio` auto mode · `tests/` = 2,226 lines*

> Everything below describes the suite **as it stands today** — Phase 4 Parts A and B
> included. What is still missing for `0.1.0` is in
> [Remaining for 0.1.0](#remaining-for-010) at the bottom.

## How the suite is layered

| Layer | Needs | Marker | Count |
|---|---|---|---|
| **Hermetic** — fakes for embedder + LLM, SQLite `:memory:` | nothing | *(none)* | 123 |
| **Live (Ollama)** — real `nomic-embed-text` + `llama3.1:8b` | local Ollama with both models | `live` | 3 |
| **Postgres** — real `pgvector` | `NOMEM_TEST_POSTGRES_DSN` set + `asyncpg`/`pgvector` importable | `postgres` | 26 |
| **Neo4j** — real Neo4j 5 | `NOMEM_TEST_NEO4J_URI` (+ `NOMEM_TEST_NEO4J_AUTH`) set + `neo4j` importable | `neo4j` | 26 |

`123 + 3 + 26 + 26 = 178`. Everything gated by an env var **skips cleanly** when the
service is absent — the markers also let you deselect explicitly
(`-m "not postgres and not neo4j"`).

### Observed results

| Command | Result |
|---|---|
| `pytest -m "not live"` (no containers) | **123 passed, 52 skipped, 3 deselected** (~3 s) |
| `pytest` (Ollama present, no containers) | **126 passed, 52 skipped** |
| `pytest` (Ollama + both containers, all env vars set) | **178 passed** (~65 s) |
| `ruff check .` | clean |
| `mypy nomem` (`--strict`, `warn_unreachable`) | clean, 29 files |

The 52 skips = 26 Postgres + 26 Neo4j (24 contract params + the e2e param + the sync
payoff param, each backend).

---

## The fakes (`tests/conftest.py`)

| Fake | Behavior |
|---|---|
| `FakeEmbedder(dims=24)` | Deterministic bag-of-words hash → 24-dim unit vector. Identical text → identical vector (cosine 1.0); shared tokens → high cosine; disjoint → ~0. Enough structure for resolution/routing assertions without a model. |
| `FakeLLM(response)` | `response` is a canned `dict` **or** a `prompt -> dict` callable. Records every prompt in `.calls`. Lets tests script exactly what "the model extracted". |
| `make_graph(...)` fixture | Factory → in-memory `MemoryGraph` wired to `FakeEmbedder` + `FakeLLM`, `backend_options={"path": ":memory:"}`. Accepts `llm_response=`, `decay=`, `retrieval_config=`, etc. |

Live tests build a real `MemoryGraph(embedder="nomic", llm="ollama")` instead.

---

## File-by-file

### `test_backend_contract.py` — 74 tests (24 shared × 3 backends + 2 e2e)

**The most important file.** One parametrized `backend_factory` fixture runs the same
24 behavioral tests against `sqlite`, `postgres`, and `neo4j`. Because all three share
`nomem/backends/_common.py` (`active_at`, `bfs_subgraph`) and `core/decay.py::score_node`,
these tests prove **behavioral parity** — swapping backends never changes a result.

| Test | Asserts |
|---|---|
| `test_create_get_roundtrip` | insert → read back with fields + embedding intact, `valid_to is None` |
| `test_create_rejects_foreign_user` | `node.user_id != backend.user_id` → `BackendError` |
| `test_duplicate_id_rejected` | second insert of an id → `BackendError` |
| `test_update_node` | partial update persists; re-read confirms |
| `test_update_missing_raises` | `NodeNotFoundError` |
| `test_update_rejects_unknown_field` | updating `id` (non-updatable) → `BackendError` |
| `test_retire_sets_valid_to_and_supersedes` | `valid_to` + `superseded_by` set; **row still present** (no hard delete) |
| `test_get_node_as_of_temporal_window` | `as_of` before-create → `None`; mid-window → node; after-retire → `None` |
| `test_upsert_edge_reinforces_weight` | second upsert of same (src,tgt,relation) → same edge id, weight `1.0 → 2.0` |
| `test_retire_edge` | `valid_to` set; retiring a missing id → `EdgeNotFoundError` |
| `test_get_edge_as_of_temporal_window` | mirror of the node case: missing → `None`; mid-window → edge; before-create / after-retire → `None` |
| `test_list_edges_enumerates_including_retired` | `active_only=False` includes retired edges; `as_of` takes precedence over `active_only` |
| `test_insert_preserves_supplied_timestamps` | `create_node` / `upsert_edge` persist caller-supplied `id` / `created_at` / `valid_from` / `valid_to` / `superseded_by` verbatim; inserting an already-retired record is legal |
| `test_export_import_round_trip` | dump `list_nodes(active_only=False)` + `list_edges(active_only=False)`, `purge_user`, reload — `get_node(as_of=T)` / `get_edge(as_of=T)` answer identically |
| `test_purge_user_deletes_and_is_scoped` | rows really gone (retired ones too); a foreign `user_id` → `BackendError`, raised **before** any delete |
| `test_vector_search_orders_by_similarity` | results ordered by cosine, `top_k` respected |
| `test_vector_search_skips_retired` | retired node absent from results |
| `test_traverse_respects_hops` | `hops=1` → seed + neighbors; `hops=2` → +one more ring |
| `test_traverse_as_of_excludes_future_nodes` | `as_of` before nodes existed → empty |
| `test_cross_reference_threshold` | only nodes with cosine ≥ threshold returned, ranked |
| `test_user_scoping` | a second backend on user `u2` can't see user `u1`'s node |
| `test_list_nodes_filters_by_context` | `context=["work"]` returns only `metadata["context"]` overlaps |
| `test_run_decay_scores_and_optionally_prunes` | `pruning=False` → `prune_candidates` populated, nothing retired; `pruning=True` → sub-floor node retired (`valid_to` set, not deleted) |
| `test_concurrent_writes` | 20 concurrent `create_node` via `asyncio.gather` all land |

Plus `test_memorygraph_end_to_end_on_networked_backend[postgres|neo4j]` — a full
`MemoryGraph` ingest → retrieve → **hierarchical** retrieve (`sub_index_used`) → decay
against the real DB, using fakes for embedder/LLM.

**Fixture mechanics for parity:** Postgres/Neo4j tests `DROP` tables / `DETACH DELETE` +
drop the vector index before each test so the schema can be recreated at the test's
dimension (3 for contract tests, 24 for e2e). SQLite gets a fresh `:memory:` DB per test.

### `test_config.py` — 9 tests

Config dataclass contract. `test_decay_defaults_match_agent_md` pins α=0.6, β=0.4,
λ=0.1, `importance_floor=0.05`, `pruning=False`, `decay_schedule=None` **verbatim from
the AGENT.md table**. Also: `merge()` applies overrides / rejects unknown keys / no-ops
on `None`; out-of-range values raise `ConfigError`.

### `test_models.py` — 6 tests

Dataclass shape. `Node` / `Edge` field-name sets match the AGENT.md "Data Schema"
exactly. `SubGraph` / `IngestReceipt` / `DecayResult` default to empty collections.

### `test_interfaces.py` — 12 tests

Adapter ABCs. `BaseBackend` / `BaseEmbedder` can't be instantiated; **all 14 backend
methods present**, with `__abstractmethods__` equal to those minus `purge_user` — the
frozen shape, asserted; SQLite constructs and responds; Postgres/Neo4j raise
`BackendError` without connection details; registries hold the expected names;
`CallableEmbedder` works with sync and async callables; unknown backend name →
`ConfigError`. Plus `test_openai_embedder` against a **stubbed transport** (no network,
no key): URL, payload, `Authorization` header, `index`-ordered results, truncated
`dimensions` passed through, key from `OPENAI_API_KEY`, and a missing key →
`EmbedderError` at call time.

### `test_no_hard_delete.py` — 5 tests

Guardrail. `delete_node` / `delete_edge` / `hard_delete` / `drop_node` / `remove_node`
appear **nowhere** on `BaseBackend`, `GraphCRUD`, or `MemoryGraph`. `retire_node` /
`retire_edge` do exist.

The `purge_user` fence: it appears on `BaseBackend` and the three bundled backend classes
and **nowhere else** — its absence from `GraphCRUD` and `MemoryGraph` is what keeps "hard
deletes are not exposed in the public API" true. A backend implementing only the 13
abstract methods inherits a `purge_user` that raises `NotSupportedError`, so ignoring it
is a choice, never a silent no-op.

### `test_extraction.py` — 7 tests

`EntityExtractor` parsing with a scripted `FakeLLM`: entities + relations parsed;
`negated` flag carried; relations with unknown endpoints dropped; entities deduped
case-insensitively; unknown `type` → `"entity"`; malformed payload → `ExtractionError`;
`extraction_model` override forwarded to the LLM adapter. `test_edge_types_filters_relations`
covers the `edge_types` allow-list: relations outside it are dropped and counted
(`edge_types=None` keeps everything, `[]` drops everything).

### `test_resolution.py` — 10 tests

`EntityResolver` three-band logic: no candidates → new (`confidence 0.0`); exact
restatement → resolves to existing, `nodes_updated`; partial match (score in
`[ambiguity_floor, threshold)`) → `ambiguous=True`, `resolved_node_id=None`, **not
merged**; `on_ambiguous="queue"` → `queued_writes`; `on_ambiguous="create"` → new node. Plus
`test_resolution_strategy_switches_scoring` — "Kiera" vs "Kira" is a near-identical
surface form that the bag-of-words embedder scores at zero, so `"embedding"` /
`"string"` / `"hybrid"` provably disagree rather than sharing one code path.

### `test_ingest_pipeline.py` — 11 tests

End-to-end ingest behavior (SQLite + fakes):

- `test_negation_retires_the_node` — "Kira left" retires Kira's node; it drops out of retrieval; row still present.
- `test_cross_reference_pass_is_opt_in` — off by default; `config={"cross_reference": True}` creates a `related_to` edge between similar nodes.
- `test_retrieval_respects_token_budget` — `token_budget=20` trims to fewer nodes, sets `metadata["budget_trimmed"]`.
- `test_as_of_returns_historical_state` — ingest "Berlin" then "Lisbon"; `retrieve(as_of=checkpoint)` sees Berlin only.
- `test_receipt_reports_resolution_confidence` — created node id present in `receipt.resolution_confidence`.
- `test_ingest_does_not_increment_access_count_by_default` — re-mention doesn't bump `access_count` (that's a retrieve concept).
- `test_retrieve_increments_access_count` — one `retrieve` → `access_count == 1`.
- `test_ingest_mode_manual_writes_nothing` — the dry run: backend untouched, every entity in `queued_writes` tagged `intended_op="create"`.
- `test_ingest_mode_manual_plans_updates_and_retires` — against a populated store, a known entity plans `update` (with `resolved_node_id`) and a negated one plans `retire`; the row stays active.
- `test_edge_types_drops_are_counted_on_the_receipt` — the filter reaches `IngestReceipt.relations_dropped` and only the allowed relation is stored.
- `test_stats_counts_active_and_retired` — `astats()` totals, active/retired split, and per-type breakdown, before and after a retirement.

### `test_decay.py` — 8 tests

`score_node` formula: `combined` matches `α·ln(1+count) + β·exp(-λ·days)` exactly;
`access` mode drops the recency term; `time` mode drops the access term; `None` mode
returns importance unchanged; recency decays monotonically with staleness. Plus:
`arun_decay` updates stored importance; pruning retires sub-floor nodes (not delete);
pruning off by default leaves them.

### `test_hierarchical.py` — 7 tests

`Retriever._route_sub_index` unit tests with hand-built nodes: deterministic routes by
tag overlap; no match → empty; semantic routes to the tag whose centroid is nearest the
query; hybrid prefers deterministic then falls back to semantic. Integration: `ingest(...,
context=[...])` tags nodes; hierarchical `retrieve(..., context=[...])` returns only that
sub-index (+ core), sets `metadata["sub_index_used"]`; re-ingest merges context tags.

### `test_public_api.py` — 11 tests

`MemoryGraph` surface: `__version__ == "0.0.0"`; constructs with named backend/embedder
(`vector_dimensions` injected, `backend.user_id` set); method signatures via
`inspect.signature`; ingest→retrieve round trip returns the graph; `run_decay` runs;
`run_decay` with `decay=None` → `ConfigError`; sync method inside a running loop →
`NomemError`; accepts a `CallableEmbedder` instance. Plus the surface locks:
`__all__` is sorted and fully importable, every symbol enumerated in
[stability.md](stability.md)'s Public table exists and is exported (**the drift test**),
and `.user_id` / `.backend` / `.embedder` / `.llm` / `.config` / `.plugins` are real
public attributes.

### `test_plugins.py` — 7 tests

The paid tier's only attachment point, exercised with a fake entry point (monkeypatched
`entry_points`): a plugin mounts at `graph.<namespace>` and is recorded in
`graph.plugins`; `attach(graph)` receives a **fully wired** graph; `plugins=False` skips
discovery; no plugins installed → `{}`; a namespace that shadows an existing attribute
(`backend`) → `ConfigError`; a non-identifier namespace → `ConfigError`; an import
failure and an `attach` failure both **propagate** rather than being swallowed.

### `test_sync.py` — 8 tests

Sync/async parity: sync round trip works; sync result == async result; repeated sync
calls reuse one loop; sync inside a running loop → `NomemError`; `with` / `async with`
call `close`/`aclose`. **The payoff:**
`test_sync_api_survives_multiple_calls_on_networked_backend[postgres|neo4j]` — two sync
`ingest()` calls + `retrieve()` + `run_decay()` on a real networked backend. This fails
with a per-call `asyncio.run()` loop (pool/driver bound to the dead first loop); passes
with the shared background loop.

### `test_pipeline_live.py` — 3 tests (`live`)

Auto-skips unless Ollama has `nomic-embed-text` + `llama3.1:8b`. Real extraction + real
768-dim embeddings, SQLite in a `tmp_path`:

- `test_real_ingest_then_retrieve` — "Kira Nolan is a marine biologist in Lisbon…" → nodes created, retrieve surfaces Lisbon/Kira.
- `test_real_embeddings_are_768_dim` — `embedder.embed(...)` length 768.
- `test_real_decay_and_hierarchical` — `context=["work"|"home"]` ingests, hierarchical retrieve routes to `work`, `run_decay` scores ≥ 3 nodes.

---

## Running

```bash
# hermetic only — fast, no services
uv run pytest -q -m "not live and not postgres and not neo4j"

# add live Ollama
uv run pytest -q -m "not postgres and not neo4j"

# everything
docker compose up -d
export NOMEM_TEST_POSTGRES_DSN=postgresql://nomem:nomem@localhost:5433/nomem
export NOMEM_TEST_NEO4J_URI=bolt://localhost:7688
export NOMEM_TEST_NEO4J_AUTH=neo4j:nomemtest123
uv run pytest -q

# one backend
uv run pytest -q tests/test_backend_contract.py -k neo4j
```

<h2 id="remaining-for-010">Remaining for 0.1.0</h2>

Everything Parts A and B called for has landed: the contract suite went from 19 shared
tests to 24 (59 → 74 parametrized), `test_plugins.py` is new, and the surface-lock,
manual-mode, `edge_types`, `resolution_strategy`, `stats()`, and OpenAI-embedder tests
are all in place. Two items are left, both belonging to
[`../phases/PHASE_4.md`](../phases/PHASE_4.md) **Part C**:

| Item | What changes |
|---|---|
| `test_public_api.py::test_version` | `__version__ == "0.0.0"` becomes a read of the installed package metadata when the version bumps to `0.1.0` |
| CI | GitHub Actions: `ruff` + `mypy --strict` + the hermetic run on 3.11/3.12/3.13, plus a second job with `services:` for pgvector and Neo4j. `live` stays manual |

## Gaps in coverage

- No load / performance test (SQLite O(n) vector scan is untested at scale).
- No test for the `_http.py` error paths (network failures) — marked `# pragma: no cover`.
  The OpenAI embedder is covered through a stubbed `post_json`, so its own transport is
  exercised only as far as the arguments it passes.
- **No CI config**, so the full run only happens where all services are present. This is a
  Phase 4 blocker: a suite that only passes on one machine is not a release gate.
- Concurrency test is 20 writes; no stress test for read/write interleaving under load.
- Nothing exercises a *third-party* backend implementing the ABC from scratch — the
  contract suite only ever runs against the three bundled ones, so a gap between "the
  contract as documented" and "the contract as the bundled backends happen to behave"
  would go unnoticed.