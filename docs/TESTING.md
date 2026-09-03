# nomem — Test Suite

*Snapshot: 140 tests · `pytest-asyncio` auto mode · `tests/` = 1,646 lines*

## How the suite is layered

| Layer | Needs | Marker | Count |
|---|---|---|---|
| **Hermetic** — fakes for embedder + LLM, SQLite `:memory:` | nothing | *(none)* | 95 |
| **Live (Ollama)** — real `nomic-embed-text` + `llama3.1:8b` | local Ollama with both models | `live` | 3 |
| **Postgres** — real `pgvector` | `NOMEM_TEST_POSTGRES_DSN` set + `asyncpg`/`pgvector` importable | `postgres` | 21 |
| **Neo4j** — real Neo4j 5 | `NOMEM_TEST_NEO4J_URI` (+ `NOMEM_TEST_NEO4J_AUTH`) set + `neo4j` importable | `neo4j` | 21 |

`95 + 3 + 21 + 21 = 140`. Everything gated by an env var **skips cleanly** when the
service is absent — the markers also let you deselect explicitly
(`-m "not postgres and not neo4j"`).

### Observed results

| Command | Result |
|---|---|
| `pytest -m "not live"` (no containers) | **95 passed, 42 skipped, 3 deselected** (~3 s) |
| `pytest` (Ollama present, no containers) | **98 passed, 42 skipped** |
| `pytest` (Ollama + both containers, all env vars set) | **140 passed** (~50 s) |
| `ruff check .` | clean |
| `mypy nomem` (`--strict`, `warn_unreachable`) | clean, 28 files |

The 42 skips = 21 Postgres + 21 Neo4j (19 contract params + the e2e param + the sync
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

### `test_backend_contract.py` — 59 tests (19 shared × 3 backends + 2 e2e)

**The most important file.** One parametrized `backend_factory` fixture runs the same
19 behavioral tests against `sqlite`, `postgres`, and `neo4j`. Because all three share
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

### `test_interfaces.py` — 10 tests

Adapter ABCs. `BaseBackend` / `BaseEmbedder` can't be instantiated; all 11 backend
methods present; SQLite constructs and responds (not a stub); Postgres/Neo4j raise
`BackendError` without connection details; registries hold the expected names;
`CallableEmbedder` works with sync and async callables; unknown backend name →
`ConfigError`.

### `test_no_hard_delete.py` — 3 tests

Guardrail. `delete_node` / `delete_edge` / `hard_delete` / `drop_node` / `remove_node`
appear **nowhere** on `BaseBackend`, `GraphCRUD`, or `MemoryGraph`. `retire_node` /
`retire_edge` do exist.

### `test_extraction.py` — 6 tests

`EntityExtractor` parsing with a scripted `FakeLLM`: entities + relations parsed;
`negated` flag carried; relations with unknown endpoints dropped; entities deduped
case-insensitively; unknown `type` → `"entity"`; malformed payload → `ExtractionError`;
`extraction_model` override forwarded to the LLM adapter.

### `test_resolution.py` — 6 tests

`EntityResolver` three-band logic: no candidates → new (`confidence 0.0`); exact
restatement → resolves to existing, `nodes_updated`; partial match (score in
`[ambiguity_floor, threshold)`) → `ambiguous=True`, `resolved_node_id=None`, **not
merged**; `on_ambiguous="queue"` → `queued_writes`; `on_ambiguous="create"` → new node.

### `test_ingest_pipeline.py` — 7 tests

End-to-end ingest behavior (SQLite + fakes):

- `test_negation_retires_the_node` — "Kira left" retires Kira's node; it drops out of retrieval; row still present.
- `test_cross_reference_pass_is_opt_in` — off by default; `config={"cross_reference": True}` creates a `related_to` edge between similar nodes.
- `test_retrieval_respects_token_budget` — `token_budget=20` trims to fewer nodes, sets `metadata["budget_trimmed"]`.
- `test_as_of_returns_historical_state` — ingest "Berlin" then "Lisbon"; `retrieve(as_of=checkpoint)` sees Berlin only.
- `test_receipt_reports_resolution_confidence` — created node id present in `receipt.resolution_confidence`.
- `test_ingest_does_not_increment_access_count_by_default` — re-mention doesn't bump `access_count` (that's a retrieve concept).
- `test_retrieve_increments_access_count` — one `retrieve` → `access_count == 1`.

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

### `test_public_api.py` — 8 tests

`MemoryGraph` surface: `__version__ == "0.0.0"`; constructs with named backend/embedder
(`vector_dimensions` injected, `backend.user_id` set); method signatures via
`inspect.signature`; ingest→retrieve round trip returns the graph; `run_decay` runs;
`run_decay` with `decay=None` → `ConfigError`; sync method inside a running loop →
`NomemError`; accepts a `CallableEmbedder` instance.

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

## Gaps in coverage

- No test for `OpenAIEmbedder` beyond "dimensions works / embed raises".
- No test exercises `ingest_mode="manual"`, `edge_types`, `embed_immediately` — because
  those config fields aren't wired to behavior yet (see [PROJECT_STATUS.md](PROJECT_STATUS.md) §4).
- No load / performance test (SQLite O(n) vector scan is untested at scale).
- No test for the `_http.py` error paths (network failures) — marked `# pragma: no cover`.
- No CI config, so the full 140-test run only happens where all services are present.
- Concurrency test is 20 writes; no stress test for read/write interleaving under load.
