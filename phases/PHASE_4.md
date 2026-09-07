# Phase 4 — Release (MIT core)

**Goal:** `pip install nomem` works, and the public extension surface is frozen well
enough that the paid plugin package can depend on it without ever forking.

**Read first:** [`../docs/stability.md`](../docs/stability.md),
[`../docs/adapters.md`](../docs/adapters.md),
[`../docs/PROJECT_STATUS.md`](../docs/PROJECT_STATUS.md) §4

## Prerequisites

Phases 0–3 complete. 140 tests green.

**Status: Parts A and B are done** (178 tests green on all three backends + live Ollama,
`ruff` + `mypy --strict` clean). Part C is not started.

---

## Part A — Freeze the extension surface ✅

Do this first. It is the only irreversible part of the release.

The paid tier is a separate package that depends on the published `nomem` and extends it.
It never contains a copy of this source. That model survives only if everything the plugin
needs is public at `0.1.0` — **adding an abstract method to `BaseBackend` after release
breaks every third-party backend.**

### A1 — Complete the backend contract (11 → 14 methods)

- [x] `async def get_edge(self, edge_id: str, as_of: datetime | None = None) -> Edge | None`
- [x] `async def list_edges(self, *, active_only: bool = True, as_of: datetime | None = None) -> list[Edge]`
- [x] `async def purge_user(self, user_id: str) -> PurgeResult` — see A2

Implement in `sqlite`, `postgres`, `neo4j`. Reuse `_common.active_at` for the temporal
filter so all three stay identical. Extend the parametrized contract suite (19 → ~24
shared tests); see [`../docs/TESTING.md`](../docs/TESTING.md) "Planned for 0.1.0".

**Why `list_edges` is not optional:** `traverse()` is seed-based and active-only. Graph
export cannot round-trip retired edges without an enumeration method, and export/import is
the first paid feature. Without this, the plugin forks.

### A2 — The GDPR hard-delete hatch (ROADMAP Q6 — **decided: public, now**)

`purge_user` performs a real hard delete of every row for one user. It exists on
`BaseBackend` and is implemented by the bundled backends. It is **never called by, and
never reachable from, `MemoryGraph` or `GraphCRUD`.**

- [x] `PurgeResult { nodes_deleted: int, edges_deleted: int }` in `models.py`
- [x] `NotSupportedError(NomemError)` in `exceptions.py` — the default body on
      `BaseBackend` raises it, so a custom backend need not implement it
- [x] Guard: raise `BackendError` unless `user_id == self.user_id`
- [x] Implement in all three bundled backends
- [x] Amend `test_no_hard_delete.py`: `purge_user` permitted **only** on `BaseBackend` and
      backend classes; absent from `GraphCRUD` and `MemoryGraph`; every other forbidden
      name (`delete_node`, `delete_edge`, `hard_delete`, `drop_node`, `remove_node`) stays
      forbidden everywhere
- [x] Document in `adapters.md` and `schema.md`

This keeps AGENT.md's rule true as written — hard deletes are not exposed in the public
API. Building it in the private package instead makes the fork unavoidable.

### A3 — Plugin attach hook

Registries extend *adapters*. Export/import, analytics, and cross-user queries are not
adapter-shaped — they are methods on the graph. Without a hook the plugin monkeypatches
or forks.

- [x] `nomem/plugins.py`:
      ```python
      class Plugin(Protocol):
          namespace: str
          def attach(self, graph: MemoryGraph) -> object: ...
      ```
- [x] Discovery via `importlib.metadata.entry_points(group="nomem.plugins")`
- [x] `MemoryGraph(..., plugins: bool = True)` — on init, discover, instantiate,
      `attach(self)`, mount the returned object at `graph.<namespace>`
- [x] `graph.plugins` → `dict[str, object]` of what loaded
- [x] Collision guard: raise `ConfigError` if `namespace` shadows an existing attribute
- [x] An import failure is raised, never swallowed

Result: `graph.pro.export(path)` from the paid package, with zero core changes per feature.

### A4 — Lock the public surface

- [x] `__all__` on `nomem/__init__.py` matching the Public table in
      [`../docs/stability.md`](../docs/stability.md)
- [x] A test that asserts `__all__` and that table haven't drifted apart
- [x] Confirm `graph.backend` / `.embedder` / `.llm` / `.config` / `.user_id` are real
      public attributes, not underscore-prefixed — plugins reach the graph through them

### A5 — Import path must accept fully-specified records

Export is worthless without import. Verify and lock with contract tests:

- [x] `create_node` / `upsert_edge` preserve caller-supplied `id`, `created_at`,
      `valid_from`, `valid_to`, `superseded_by` verbatim — no `now()` stamping
- [x] Creating an already-retired record (`valid_to` set at insert) is legal
- [x] Round-trip: dump `list_nodes(active_only=False)` + `list_edges(active_only=False)`,
      reload into an emptied store, and `get_node(as_of=T)` answers identically

*Outcome: all three backends already inserted verbatim — no production code changed here.
The value was the contract tests, which now hold it that way. The round trip empties the
store with `purge_user` rather than opening a second backend, because two handles to
Postgres or Neo4j are two views of the same database.*

---

## Part B — Resolve the dangling config (ROADMAP Q5 — decided) ✅

An inert field in a released config is a lie. Resolve all four.

| Field | Decision | Work |
|---|---|---|
| `IngestConfig.edge_types` | **Wire** | Filter extracted relations to the allowed set; drop the rest; count drops in the receipt |
| `IngestConfig.resolution_strategy` | **Wire** | `"embedding"` / `"string"` / `"hybrid"` (default, = today's `max()`); each branch actually switches scoring |
| `ingest_mode="manual"` | **Implement** | Extract + resolve, write nothing, return an `IngestReceipt` with every intended operation in `queued_writes`. A dry run. ~15 lines; AGENT.md advertises it in the public snippet, so it cannot just be deleted |
| `IngestConfig.embed_immediately` | **Remove** | Deferred embedding needs a pending queue and a re-embed pass. A feature, not a flag |

`DecayConfig.decay_schedule` stays inert **by design** — metadata for the dev's own cron,
pinned to the AGENT.md defaults table by `test_config.py`. Say so in the docstring so it
doesn't read as unfinished.

- [x] `OpenAIEmbedder`: implement `embed` / `embed_batch` over `nomem/_http.py`
      (`POST https://api.openai.com/v1/embeddings`), and **delete the `openai` extra**.
      Zero runtime dependencies stays true. (ROADMAP Q4)
- [x] `MemoryGraph.stats()` — node/edge counts, active vs retired, per-type breakdown.
      **No `snapshot()`** — that is paid export. (ROADMAP Q8)

---

## Part C — Package and publish

- [ ] Reserve `nomem` on PyPI. It was unclaimed as of 2026-09-07 — do this before anything
      else in Part C
- [ ] `pyproject.toml`: real `authors`, `license = "MIT"`, `urls` (Homepage, Source,
      Issues), `Development Status :: 4 - Beta`, keywords, `readme`
- [x] `LICENSE` — MIT, No One's Studio
- [ ] Version `0.1.0`. `__version__` reads `importlib.metadata.version("nomem")`; update
      the `test_public_api.py` assertion that pins `"0.0.0"`
- [ ] Extras after the openai removal: `postgres`, `neo4j` only (**the `openai` extra is
      already gone**). Confirm the `neo4j` floor
      (resolves to 6.x today — raise it or cap it), the `pgvector` floor, and
      `pytest-asyncio` 1.x config
- [ ] `examples/quickstart.py` — SQLite `:memory:` + `CallableEmbedder` + `CallableLLM`,
      runs with **no Ollama and no Docker**
- [ ] `examples/quickstart_ollama.py` — the real thing
- [ ] `CHANGELOG.md`, `CONTRIBUTING.md`, and a `README.md` that works as the PyPI long
      description
- [ ] CI: GitHub Actions — `ruff` + `mypy --strict` + hermetic `pytest` on 3.11/3.12/3.13;
      a second job with `services:` for pgvector + Neo4j; keep `live` manual
- [ ] `uv build`, `twine check`, publish to **Test PyPI**, install from it into a clean
      venv, run the quickstart
- [ ] Publish `0.1.0` to PyPI; make the GitHub repo public

The docs site (MkDocs + mkdocstrings) is **not** a release blocker. `README.md` plus the
`docs/` tree on GitHub is enough for `0.1.0`.

---

## Rules

- Nothing in Part A may be deferred "until someone needs it." It is the only part that
  cannot be added later without a breaking release, and the paid tier's no-fork model
  rests entirely on it.
- `purge_user` must not become reachable from `MemoryGraph`. If a task seems to call for
  exposing it, the task is wrong.
- No telemetry, no phone-home, no usage tracking. Not in the quickstart, not in CI.
- Do not add to the MIT core a feature the paid tier was going to sell. `list_nodes` /
  `list_edges` are the deliberate exception — the plugin model requires them, and the
  consequence (a DIY dump is ~30 lines) is accepted, not accidental.

## Done when

`pip install nomem` in a clean venv runs `examples/quickstart.py` with no external
services; `BaseBackend` has its final 14-method shape with all three bundled backends
passing the extended contract suite; a plugin registered through the `nomem.plugins` entry
point mounts and can enumerate and reinsert a full bi-temporal graph; and
`docs/stability.md` matches `__all__`.

---

## Then, in order

1. **nomem** — Part A, Part B, Part C. Publish `0.1.0`. Repo public.
2. **The paid plugin package** (new private repo) — graph export / import only. Depends on
   `nomem>=0.1,<0.2`. Entry point `nomem.plugins`, namespace `pro`. Ships with its own
   licence file: the storefront's terms cover the transaction, not the artifact. Fix the
   product identifier before the first release — it keys the download endpoint and an
   entitlement uniqueness constraint, so it is effectively permanent.
3. **The storefront** — fill in the now-public nomem GitHub URL, point the artifact fetch
   at a real release tag, and go live.

The storefront must not take money before step 2 exists.
