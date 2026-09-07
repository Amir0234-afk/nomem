# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) as qualified in
[`docs/stability.md`](docs/stability.md): before 1.0, a break in the documented public
surface is a **minor** bump, and dependents pin `nomem>=0.1,<0.2`.

## [Unreleased]

## [0.1.0] — 2026-09-07

First public release. Phases 0–4 of the project plan.

This release **freezes the extension surface**. `BaseBackend` will not gain another
abstract method, and nothing in the Public table of [`docs/stability.md`](docs/stability.md)
will change, before `0.2.0`.

### Added

- `MemoryGraph` — per-user memory as a bi-temporal knowledge graph. `ingest()` extracts
  entities and relations from a conversation turn, resolves them against the existing
  graph, and applies real CREATE / UPDATE / RETIRE operations; `retrieve()` returns a
  bounded `SubGraph`. Every method has an `a`-prefixed async twin, and the sync API is a
  thin wrapper over the async core that stays valid across calls with networked backends.
- **Three storage backends** — `sqlite` (zero infrastructure, the default), `postgres`
  (pgvector), and `neo4j` — all passing one identical behavioral contract suite, so
  swapping a backend does not change results.
- **Bi-temporal semantics** — `valid_from` / `valid_to` for event time, `created_at` for
  transaction time, `superseded_by` for supersession chains, and `as_of=` on `retrieve()`
  for historical state. Removal is retirement; the graph API performs no hard deletes.
- **Entity resolution** — a three-band model (resolve / ambiguous / new) with tunable
  `resolution_confidence_threshold`, `ambiguity_floor`, and `string_match_min`. Ambiguous
  matches are surfaced in the ingest receipt, never silently merged.
- **Decay** — `run_decay()` rescores node importance from access count and recency, with
  opt-in pruning. Never self-scheduled; the developer calls it.
- **Hierarchical retrieval** — a small always-loaded core index that routes into
  context-tagged sub-indexes, by `deterministic`, `semantic`, or `hybrid` strategy.
- **Adapters** — `BaseBackend`, `BaseEmbedder`, and `BaseLLM`, each with a registry and a
  callable-wrapping shortcut. Embedders: `nomic` (via Ollama, 768-dim) and `openai`, both
  over the standard library. Extraction defaults to Ollama.
- **Plugins** — `nomem/plugins.py` discovers the `nomem.plugins` entry-point group and
  mounts each `attach(graph)` result at `graph.<namespace>`. This is how capability is
  added without forking the core.
- `BaseBackend.list_nodes` / `list_edges` / `get_edge` — enumeration including retired
  records, so a full graph dump round-trips.
- `BaseBackend.purge_user()` — the single hard-delete path, non-abstract (default raises
  `NotSupportedError`), scoped to the backend's own user, and unreachable from
  `MemoryGraph` or `GraphCRUD`. It exists so right-to-forget tooling can be built as a
  plugin rather than a fork.
- `MemoryGraph.stats()` — node and edge counts, active versus retired, per-type breakdown.
- `ingest_mode="manual"` — a dry run: extract and resolve, write nothing, and report every
  intended operation in `IngestReceipt.queued_writes`.
- `IngestConfig.edge_types` — restrict the relation vocabulary; drops are counted in
  `IngestReceipt.relations_dropped` rather than being silent.
- `IngestConfig.resolution_strategy` — `"embedding"`, `"string"`, or `"hybrid"`.

### Notes

- `repr(MemoryGraphConfig)` and `repr(OpenAIEmbedder)` **redact secrets**. Adapter option
  values — a Postgres DSN, a Neo4j auth tuple, an API key — print as `'***'`, while field
  names and option keys stay visible. `graph.config` is public API and therefore reaches
  logs, error reporters, and tracebacks; it must not carry a password there.
- The core has **no mandatory runtime dependencies**. `postgres` and `neo4j` are extras.
- There is no telemetry, no phone-home, and no usage tracking.
- SQLite's vector search is a Python-side linear scan — correct at any size, but intended
  for development and small single-user graphs. Use Postgres or Neo4j at scale.
- `as_of=` answers *which records were active and known at T*; per-field score history is
  not retained (see [`docs/roadmap.md`](docs/roadmap.md) Q1).

[Unreleased]: https://github.com/Amir0234-afk/nomem/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Amir0234-afk/nomem/releases/tag/v0.1.0
