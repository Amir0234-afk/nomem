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

## Status: Phase 0

The full public surface — data models, config, adapter interfaces, and the `MemoryGraph`
class — exists and is fully typed. The pipelines are **not implemented yet**; calling
`ingest` / `retrieve` / `run_decay` raises `NotImplementedError`. See
[AGENT.md](AGENT.md) for the phase roadmap.

## Install

```bash
uv add nomem            # core: zero mandatory dependencies
uv add "nomem[postgres]" # + asyncpg / pgvector
uv add "nomem[neo4j]"
uv add "nomem[openai]"
```

Local dev needs nothing beyond Python 3.11+. The default embedder targets a local
[Ollama](https://ollama.com) running `nomic-embed-text`.

## Usage

```python
from nomem import MemoryGraph

graph = MemoryGraph(
    user_id="u123",
    backend="sqlite",      # sqlite | postgres | neo4j | your BaseBackend
    embedder="nomic",      # nomic | openai | any BaseEmbedder / callable
    decay="combined",      # "time" | "access" | "combined" | None
    ingest_mode="auto",
)

graph.ingest(
    user="What happened with Kira?",
    assistant="Kira left the room after the argument.",
)

result = graph.retrieve("Kira")
# -> SubGraph(nodes=[...], edges=[...], metadata={...})

# async variants — identical signatures
await graph.aingest(...)
await graph.aretrieve(...)
```

The sync API is a thin wrapper over the async core.

## Docs

| Doc | What |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Module map + ingest / retrieval / decay pipelines |
| [docs/schema.md](docs/schema.md) | Canonical `Node` / `Edge` / `SubGraph` schema + bi-temporal semantics |
| [docs/adapters.md](docs/adapters.md) | Writing your own backend or embedder |

## Open-core split

The MIT core covers graph CRUD, the SQLite / PostgreSQL / Neo4j backends, the default
embedder, decay + pruning, and `ingest()` / `retrieve()`. Graph export/import, cross-user
queries, retrieval analytics, GDPR tooling, the hosted backend, and team namespacing are
proprietary. See [AGENT.md](AGENT.md).
