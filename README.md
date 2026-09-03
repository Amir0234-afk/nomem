# nomem

> **No One's Memory Management** — persistent, self-updating memory for LLM apps.
> Maintainer: No One's Studio · License: MIT (core) / Proprietary (advanced features)

`nomem` gives LLM-powered applications persistent, per-user memory structured as a
**bi-temporal knowledge graph** with an explicit, LLM-powered CRUD interface.

You feed it conversation turns. nomem extracts entities and relationships, writes them
into a graph using real CREATE / UPDATE / RETIRE operations, and retrieves a
bounded, relevant subgraph at query time. The graph reflects the *evolving state* of a
conversation — not an append-only log.

nomem is **not** a vector database, an LLM wrapper, or a chat-history store. It is a graph
database with an LLM-powered CRUD interface. The developer controls the rules.

## Status: Phase 3 complete

Working end-to-end: Ollama-based extraction, entity resolution (embedding + near-exact
string match), the bi-temporal node/edge schema, `as_of` historical retrieval, the
opt-in cross-reference pass, the **decay pass** (`run_decay()` — scoring + optional
pruning), and **hierarchical retrieval** (core index + context-tag-routed situation
sub-indexes) — on **all three backends** (SQLite, PostgreSQL/pgvector, Neo4j), which
pass one identical behavioral contract suite. The **sync API** is a thin wrapper over
the async core and stays valid across calls with networked backends. Phase 4 is
packaging + docs. See [AGENT.md](AGENT.md) for the roadmap.

### Backends other than SQLite

```bash
docker compose up -d   # pgvector on :5433, Neo4j on :7688 (see docker-compose.yml)
```
```python
MemoryGraph(
    user_id="u1", embedder="nomic",
    backend="postgres",
    backend_options={"dsn": "postgresql://nomem:nomem@localhost:5433/nomem"},
)
MemoryGraph(
    user_id="u1", embedder="nomic",
    backend="neo4j",
    backend_options={"uri": "bolt://localhost:7688", "auth": ("neo4j", "nomemtest123")},
)
```
Each keeps its vector store at one fixed dimension — use a dedicated database per
embedding model. Close networked backends with `graph.close()` / `await graph.aclose()`,
or use `with` / `async with`.

## Install

```bash
uv add nomem             # core: zero mandatory dependencies
uv add "nomem[postgres]" # + asyncpg / pgvector
uv add "nomem[neo4j]"    # + neo4j driver
uv add "nomem[openai]"   # OpenAI embedder
```

Local dev needs nothing beyond Python 3.11+ and a running
[Ollama](https://ollama.com):

```bash
ollama pull nomic-embed-text   # default embedder (768-dim)
ollama pull llama3.1:8b        # default extraction model
```

Any piece is swappable — pass a `BaseBackend` / `BaseEmbedder` / `BaseLLM` instance or a
bare callable instead of the adapter name.

## Usage

```python
from nomem import MemoryGraph

graph = MemoryGraph(
    user_id="u123",
    backend="sqlite",      # sqlite | postgres | neo4j | your BaseBackend
    backend_options={"path": "memory.sqlite"},
    embedder="nomic",      # nomic | openai | any BaseEmbedder / callable
    llm="ollama",          # ollama | any BaseLLM / callable (extraction)
    decay="combined",      # "time" | "access" | "combined" | None
    ingest_mode="auto",
)

graph.ingest(
    user="What happened with Kira?",
    assistant="Kira left the room after the argument.",
    context=["household"],           # optional tags for hierarchical routing
)

result = graph.retrieve("Kira")
# -> SubGraph(nodes=[...], edges=[...], metadata={...})

result = graph.retrieve("Kira", as_of=some_datetime)   # historical state
result = graph.retrieve(                                # hierarchical
    "Kira", config={"mode": "hierarchical"}, context=["household"],
)

graph.run_decay()   # rescore importance; prune if decay_config.pruning is on

# async variants — identical signatures
await graph.aingest(...)
await graph.aretrieve(...)
await graph.arun_decay()
```

The sync API is a thin wrapper over the async core.

## Docs

Start at [docs/README.md](docs/README.md) for the index.

| Doc | What |
|---|---|
| [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) | Where the project stands — phases, what works, what's stubbed, deviations from AGENT.md |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phase 4 / 5 tasks, known limitations, open design questions |
| [docs/TESTING.md](docs/TESTING.md) | The 140-test suite, file by file, and how to run it |
| [docs/architecture.md](docs/architecture.md) | Module map + ingest / retrieval / decay pipelines + the sync model |
| [docs/schema.md](docs/schema.md) | Canonical `Node` / `Edge` / `SubGraph` schema + bi-temporal semantics |
| [docs/adapters.md](docs/adapters.md) | Writing your own backend, embedder, or LLM |

## Open-core split

The MIT core covers graph CRUD, the SQLite / PostgreSQL / Neo4j backends, the default
embedder, decay + pruning, and `ingest()` / `retrieve()`. Graph export/import, cross-user
queries, retrieval analytics, GDPR tooling, the hosted backend, and team namespacing are
proprietary. See [AGENT.md](AGENT.md).
