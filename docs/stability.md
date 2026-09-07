# Stability & the public API

What `nomem>=0.1,<0.2` guarantees. This exists because the paid tier is a **separate
package** that depends on the published core and extends it — if the extension surface
moves, that package forks, and the fork is the failure mode the whole design is built to
avoid.

## Versioning

Pre-1.0 SemVer, read strictly:

| Change | Bump |
|---|---|
| Anything breaking in the table below | **minor** (`0.1` → `0.2`) |
| New method, new config field with a default, new adapter, new registry entry | patch |
| Bug fix, docs, internals | patch |

Dependents pin `nomem>=0.1,<0.2`.

## Public

Stable within a minor version. Changing any of it is a breaking change.

| Surface | Detail |
|---|---|
| `MemoryGraph` | Constructor keywords; `ingest` / `retrieve` / `run_decay` / `close` and the `a`-prefixed coroutines; `with` / `async with` |
| `MemoryGraph` attributes | `.user_id`, `.backend`, `.embedder`, `.llm`, `.config`, `.plugins` — plugins reach the graph's internals through these, so they are API, not implementation detail |
| `nomem.models` | `Node`, `Edge`, `SubGraph`, `IngestReceipt`, `DecayResult`, `PurgeResult` — field names and types |
| `Node.metadata` reserved keys | `context`, `intended_op` (+ `resolved_node_id`) — see [schema.md](schema.md#reserved-metadata-keys) |
| `nomem.config` | `MemoryGraphConfig`, `DecayConfig`, `IngestConfig`, `RetrievalConfig`, `merge()` |
| `nomem.exceptions` | The `NomemError` hierarchy, including `NotSupportedError` |
| `nomem.backends.base.BaseBackend` | **All 14 methods. Frozen — no new abstract method before `0.2.0`** |
| `nomem.embedders.base.BaseEmbedder` | `embed`, `embed_batch`, `dimensions` |
| `nomem.llms.base.BaseLLM` | `generate_json` |
| Registries | `BACKEND_REGISTRY`, `EMBEDDER_REGISTRY`, `LLM_REGISTRY` and their `resolve_*` helpers |
| `nomem.plugins` | The `Plugin` protocol and the `nomem.plugins` entry-point group |

`nomem/__init__.py` carries an `__all__` matching this list.

## Internal — change at any time

- Everything under `nomem/core/` (`EntityExtractor`, `EntityResolver`, `GraphCRUD`,
  `Retriever`, `DecayEngine`). `score_node` is called *by* backends but is not a contract
  for outside code.
- Every `_`-prefixed module: `_http.py`, `_vector.py`, `backends/_common.py`.
- Private attributes and methods on any public class.
- Prompt text, JSON schemas sent to the extraction model, and default model names.
- Storage layout: table names, column types, the Neo4j label/relationship shape. A backend
  owns its own schema; nothing outside may query it directly.

Depending on any of this and then filing a regression is not a bug report.

## The extension contract

Three ways to extend, all public:

1. **Adapters** — implement `BaseBackend` / `BaseEmbedder` / `BaseLLM`, register a name or
   pass an instance. Swaps a component.
2. **Plugins** — advertise a `nomem.plugins` entry point; `MemoryGraph` mounts the object
   returned by `attach(graph)` at `graph.<namespace>`. Adds capability. See
   [adapters.md](adapters.md).
3. **Config** — every threshold and mode is a dataclass field, overridable per call.

**If a feature can be built through 1–3, it never touches this repository.** If it cannot,
that is a design bug in the surface, and the fix is to widen the surface here — in the
MIT core, in the open — not to fork.

Two things landed in `0.1.0` for exactly that reason and would otherwise have forced a
fork:

- `list_edges` / `get_edge` — enumeration, including retired records. Seed-based
  `traverse` cannot round-trip a graph, so export is unbuildable without them.
- `purge_user` — the single gated hard-delete path, unreachable from `MemoryGraph`. GDPR
  right-to-forget contradicts "never hard delete" and cannot be added downstream.

## No telemetry

The MIT core does not phone home, count invocations, or report anything anywhere. Plugin
discovery imports installed packages and nothing else. This is not a default; there is no
switch.
