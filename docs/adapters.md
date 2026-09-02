# Writing adapters

Storage, embedding, and extraction are adapter boundaries. Implementing an interface is
all it takes — swapping an adapter touches nothing else.

## Backend adapter

Subclass [`nomem.backends.base.BaseBackend`](../nomem/backends/base.py) and implement all
ten coroutines:

| Method | Contract |
|---|---|
| `create_node(node) -> Node` | Persist a new node; return the stored record |
| `update_node(node_id, updates) -> Node` | Partial update of an **active** node |
| `retire_node(node_id, superseded_by=None) -> Node` | Set `valid_to = now` (+ `superseded_by`). **Never delete.** |
| `get_node(node_id, as_of=None) -> Node | None` | Node as known at `as_of` (or now); `None` if absent |
| `upsert_edge(edge) -> Edge` | Create, or bump `weight` / metadata on an existing active edge |
| `retire_edge(edge_id, superseded_by=None) -> Edge` | Set `valid_to = now`. **Never delete.** |
| `vector_search(embedding, top_k) -> list[Node]` | `top_k` most similar **active** nodes |
| `traverse(seed_ids, hops, as_of=None) -> SubGraph` | Expand `hops` edges from seeds; honor `as_of` |
| `cross_reference(node, threshold) -> list[tuple[Node, float]]` | Active nodes with similarity ≥ `threshold` |
| `run_decay(config) -> DecayResult` | One decay/pruning pass over this user's nodes |

Rules every backend must follow:

* **No hard deletes.** Retirement via `valid_to` is the only removal mechanism.
* **`as_of` honesty.** `get_node` / `traverse` with `as_of` return the graph as it was
  *known* at that instant (`created_at <= as_of`, validity window contains `as_of`).
* **User scoping.** Every row carries `user_id`; never leak across users.
* **Async.** Blocking drivers must be offloaded to a thread/executor inside the adapter.

Register it so it can be named in config:

```python
from nomem.backends import BACKEND_REGISTRY
BACKEND_REGISTRY["mydb"] = MyBackend
# then: MemoryGraph(user_id=..., backend="mydb", backend_options={...})
```

Or pass an instance directly: `MemoryGraph(user_id=..., backend=MyBackend(...))`.

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
