# Writing adapters

Storage, embedding, and extraction are adapter boundaries. Implementing an interface is
all it takes — swapping an adapter touches nothing else. Plugins are a fourth boundary:
they add *methods* to a graph rather than swapping a component.

> **Frozen at 0.1.0.** Everything on this page is public API under
> [`stability.md`](stability.md). `BaseBackend` will not gain another abstract method
> before `0.2.0`.

## Backend adapter

Subclass [`nomem.backends.base.BaseBackend`](../nomem/backends/base.py) and implement all
thirteen abstract coroutines (`purge_user`, the fourteenth, is optional — see below):

| Method | Contract |
|---|---|
| `create_node(node) -> Node` | Persist a new node; return the stored record |
| `update_node(node_id, updates) -> Node` | Partial update of an **active** node |
| `retire_node(node_id, superseded_by=None) -> Node` | Set `valid_to = now` (+ `superseded_by`). **Never delete.** |
| `get_node(node_id, as_of=None) -> Node | None` | Node as known at `as_of` (or now); `None` if absent |
| `list_nodes(*, active_only=True, context=None, as_of=None) -> list[Node]` | Enumerate nodes; `context` filters by `metadata["context"]` overlap. Powers hierarchical retrieval. |
| `upsert_edge(edge) -> Edge` | Create, or bump `weight` / metadata on an existing active edge |
| `retire_edge(edge_id, superseded_by=None) -> Edge` | Set `valid_to = now`. **Never delete.** |
| `get_edge(edge_id, as_of=None) -> Edge | None` | Edge as known at `as_of` (or now); `None` if absent |
| `list_edges(*, active_only=True, as_of=None) -> list[Edge]` | Enumerate edges. With `active_only=False`, **retired edges included** — this is what makes a full graph export round-trip |
| `vector_search(embedding, top_k) -> list[Node]` | `top_k` most similar **active** nodes |
| `traverse(seed_ids, hops, as_of=None) -> SubGraph` | Expand `hops` edges from seeds; honor `as_of` |
| `cross_reference(node, threshold) -> list[tuple[Node, float]]` | Active nodes with similarity ≥ `threshold` |
| `run_decay(config) -> DecayResult` | One decay/pruning pass: rescore active nodes with `core/decay.py::score_node`, collect `prune_candidates`, retire them iff `config.pruning` |

### `purge_user` — optional, and the only hard delete

```python
async def purge_user(self, user_id: str) -> PurgeResult: ...
```

Deletes every row belonging to one user, permanently. It is **not abstract**: the default
body on `BaseBackend` raises `NotSupportedError`, so ignoring it is a valid choice. The
three bundled backends implement it.

It exists for one reason — GDPR right-to-forget has to be buildable as a plugin rather
than a fork of the core. Rules:

* Raise `BackendError` unless `user_id == self.user_id`. No cross-user purge.
* Nothing in `MemoryGraph` or `GraphCRUD` calls it, and a guard test asserts it never
  appears on either. Reaching it requires holding a backend instance directly.
* `PurgeResult { nodes_deleted, edges_deleted }`.

If a task seems to want this on the public graph API, the task is wrong.

### Import support

Export is useless without import, so `create_node` / `upsert_edge` must **persist
caller-supplied `id`, `created_at`, `valid_from`, `valid_to`, and `superseded_by`
verbatim** rather than stamping `now()`. Inserting an already-retired record
(`valid_to` set at insert time) is legal. The contract suite checks that a dump of
`list_nodes(active_only=False)` + `list_edges(active_only=False)`, reloaded into a store
emptied by `purge_user`, answers `get_node(as_of=...)` / `get_edge(as_of=...)`
identically.

Rules every backend must follow:

* **No hard deletes** through the graph API. Retirement via `valid_to` is the only
  removal mechanism `MemoryGraph` can reach; `purge_user` is the gated exception above.
* **`as_of` honesty.** `get_node` / `traverse` with `as_of` return the graph as it was
  *known* at that instant (`created_at <= as_of`, validity window contains `as_of`).
* **User scoping.** Every row carries `user_id`; never leak across users.
* **Async.** Blocking drivers must be offloaded to a thread/executor inside the adapter.
* **Parity.** `active_at` and `bfs_subgraph` in [`nomem/backends/_common.py`](../nomem/backends/_common.py)
  give identical bi-temporal filtering and traversal for free — reuse them. `run_decay`
  must score with [`nomem.core.decay.score_node`](../nomem/core/decay.py).

`MemoryGraph` injects `user_id` and `vector_dimensions` (the embedder's size) into
`backend_options` when resolving by name, so accept `**options` and take what you need.

The bundled backends:

| Backend | Infra | Vector search | Notes |
|---|---|---|---|
| `sqlite` | none (file or `:memory:`) | Python cosine over all active nodes | default; blocking driver serialized behind a lock + worker threads |
| `postgres` | Postgres + pgvector (`postgres` extra) | pgvector `<=>` cosine index | `vector(N)` column fixed at first schema create — one embedding model per database; `close()` is async |
| `neo4j` | Neo4j 5 (`neo4j` extra) | native vector index (over-fetch + filter) | `(:Node)-[:EDGE {relation}]->(:Node)`; `metadata` stored as a JSON string; index dimension fixed at first create; `close()` is async |

`MemoryGraph.aclose()` calls `backend.close()` (awaiting it if it's a coroutine); the
SQLite `close()` is sync. Networked backends build their pool/driver lazily on first use.

Register a custom backend so it can be named in config:

```python
from nomem.backends import BACKEND_REGISTRY
BACKEND_REGISTRY["mydb"] = MyBackend
# then: MemoryGraph(user_id=..., backend="mydb", backend_options={...})
```

Or pass an instance directly: `MemoryGraph(user_id=..., backend=MyBackend(...))`.

## Plugins

Registries swap a *component*. A plugin adds *capability* — methods that need the whole
graph, like export/import or analytics. This is the boundary the paid tier extends
through, and it is why the paid package is not a fork.

```python
# nomem/plugins.py
class Plugin(Protocol):
    namespace: str
    def attach(self, graph: MemoryGraph) -> object: ...
```

Advertise it from your package's `pyproject.toml`:

```toml
[project.entry-points."nomem.plugins"]
pro = "nomem_pro:Plugin"
```

On construction, `MemoryGraph` discovers every entry point in the `nomem.plugins` group,
instantiates it, calls `attach(self)`, and mounts the returned object at
`graph.<namespace>`:

```python
graph = MemoryGraph(user_id="u1")
graph.pro.export("graph.jsonl")     # from the installed plugin
graph.plugins                       # {"pro": <object>}
```

* `MemoryGraph(plugins=False)` disables discovery entirely.
* A `namespace` that shadows an existing attribute raises `ConfigError`. Plugins never
  monkeypatch core methods.
* An import failure is raised, never swallowed.
* `attach` receives the graph, so `graph.backend`, `.embedder`, `.llm`, `.config`, and
  `.user_id` are public and stable — see [`stability.md`](stability.md).

## Embedder adapter

Subclass [`nomem.embedders.base.BaseEmbedder`](../nomem/embedders/base.py):

```python
class BaseEmbedder(ABC):
    async def embed(self, text: str) -> list[float]: ...
    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
    @property
    def dimensions(self) -> int: ...
```

`dimensions` must be a stable constant — it sizes vector columns and validates config.

Shortcut — wrap any callable (sync or async, `str -> vector`):

```python
from nomem.embedders import CallableEmbedder

emb = CallableEmbedder(my_embed_fn, dimensions=768)
MemoryGraph(user_id="u1", embedder=emb)
```

## LLM adapter

Extraction needs a chat model that can return structured JSON. Subclass
[`nomem.llms.base.BaseLLM`](../nomem/llms/base.py):

```python
class BaseLLM(ABC):
    async def generate_json(
        self, prompt: str, *, system: str | None = None,
        schema: dict | None = None, model: str | None = None,
    ) -> dict: ...
```

The implementation must ask the provider for structured output (Ollama `format`, an
OpenAI JSON-mode flag, a tool schema, …) and raise `ExtractionError` if the response is
not a JSON object. The default `OllamaLLM` posts to `/api/chat` with `format` set to the
supplied JSON schema.

Shortcut — wrap any `prompt -> json` callable (returns a `dict` or a JSON string):

```python
from nomem.llms import CallableLLM

MemoryGraph(user_id="u1", llm=CallableLLM(my_llm_fn))
```

Register a named adapter the same way as backends:

```python
from nomem.llms import LLM_REGISTRY
LLM_REGISTRY["anthropic"] = MyAnthropicLLM
```