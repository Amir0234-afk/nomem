# nomem

Persistent, per-user memory for LLM applications, stored as a knowledge graph that
updates itself.

Feed it conversation turns. It pulls out entities and relationships, matches them against
what it already knows, and applies real CREATE, UPDATE, and RETIRE operations. Ask it a
question later and you get back a small, relevant subgraph instead of a growing pile of
chat history.

```bash
pip install nomem
```

The core has no required dependencies. SQLite comes with Python, and the default embedder
talks to a local [Ollama](https://ollama.com) over plain HTTP.

## Quick start

```bash
ollama pull nomic-embed-text   # embeddings, 768-dim
ollama pull llama3.1:8b        # extraction
```

```python
from datetime import UTC, datetime
from nomem import MemoryGraph

with MemoryGraph(user_id="u123", backend_options={"path": "memory.sqlite"}) as graph:
    graph.ingest(
        user="Where's Kira living now?",
        assistant="Kira lives in Berlin.",
    )

    checkpoint = datetime.now(UTC)

    # A later turn that contradicts the first. nomem retires what no longer holds.
    receipt = graph.ingest(
        user="Any news about Kira?",
        assistant="Kira has left Berlin. She lives in Lisbon now.",
    )
    print("retired:", receipt.nodes_retired)
    print("held for review:", [(o.extracted.label, round(o.confidence, 2))
                                for o in receipt.ambiguous_resolutions])

    for node in graph.retrieve("Where does Kira live?").nodes:
        print(node.type, node.label)
```

Nothing was deleted. The superseded facts were retired, so you can ask what the graph
believed before that second turn by passing the checkpoint you captured:

```python
for node in graph.retrieve("Where does Kira live?", as_of=checkpoint).nodes:
    print(node.type, node.label)
```

What comes back depends on your extraction model — these snippets print whatever it
found rather than promising exact labels. You may also see "Lisbon" land in
`ambiguous_resolutions` instead of becoming a node: nomem never silently merges an
entity on a low-confidence match, and a real embedding model can score two cities
close enough to each other to fall into that band. That is the ambiguity-reporting
behavior described below working as intended, not a bug in the example. For a version
that is fully deterministic and needs no Ollama at all, see
[examples/quickstart.py](https://github.com/Amir0234-afk/nomem/blob/master/examples/quickstart.py).

Every method has an async twin (`aingest`, `aretrieve`, `arun_decay`). The synchronous API
is a thin wrapper, not a second implementation.

## Why it works this way

**Conversations change facts, so the graph changes with them.** When someone moves house,
the old fact is retired and superseded rather than left to contradict the new one. Nothing
is ever hard-deleted, and both timelines are tracked: when a fact was true in the world,
and when nomem learned it.

**Retrieval stays bounded.** A user with three years of history gets the same token budget
as one with three days. You configure the budget; nomem drops the least important nodes to
stay inside it.

**Uncertainty is reported, not resolved silently.** When a name is a near-match for an
existing entity, nomem will not quietly merge them. The ambiguity comes back in the ingest
receipt and you decide what happens.

**You own the settings.** Extraction model, traversal depth, decay rates, pruning, edge
vocabulary, resolution thresholds: all configurable, with defaults that are documented
rather than hidden.

## Backends

| Backend | Setup | Vector search |
|---|---|---|
| `sqlite` | none, the default | linear scan in Python |
| `postgres` | `pip install "nomem[postgres]"` | pgvector index |
| `neo4j` | `pip install "nomem[neo4j]"` | native vector index |

```python
MemoryGraph(
    user_id="u1",
    backend="postgres",
    backend_options={"dsn": "postgresql://user:pass@localhost:5432/nomem"},
)
```

All three pass one shared behavioral test suite, so switching backends does not change
results. SQLite is for development and small graphs; its vector search is a linear scan
that degrades past roughly ten thousand nodes. Use Postgres or Neo4j in production, and
give each embedding model its own database, since the vector width is fixed when the
schema is created.

## Extending it

Storage, embedding, and extraction are all interfaces. Implement `BaseBackend`,
`BaseEmbedder`, or `BaseLLM` and pass an instance, or wrap a plain function:

```python
from nomem.embedders import CallableEmbedder

MemoryGraph(user_id="u1", embedder=CallableEmbedder(my_embed_fn, dimensions=768))
```

For capability rather than substitution, publish a `nomem.plugins` entry point and
`MemoryGraph` will mount it at `graph.<yourname>`. See
[docs/adapters.md](https://github.com/Amir0234-afk/nomem/blob/master/docs/adapters.md), and [docs/stability.md](https://github.com/Amir0234-afk/nomem/blob/master/docs/stability.md) for what
counts as public API.

## Documentation

[docs/](https://github.com/Amir0234-afk/nomem/tree/master/docs) covers the [architecture](https://github.com/Amir0234-afk/nomem/blob/master/docs/architecture.md),
[schema](https://github.com/Amir0234-afk/nomem/blob/master/docs/schema.md), [adapters and plugins](https://github.com/Amir0234-afk/nomem/blob/master/docs/adapters.md),
[stability guarantees](https://github.com/Amir0234-afk/nomem/blob/master/docs/stability.md), and the
[roadmap](https://github.com/Amir0234-afk/nomem/blob/master/docs/roadmap.md).

## Licence and paid tier

The core is MIT and covers everything above: graph CRUD, all three backends, the default
embedder, decay, and ingest/retrieve.

Graph export and import, cross-user queries, retrieval analytics, GDPR tooling, team
namespacing, and a hosted backend are **planned** as a separate commercial package. None of
them is built or available yet, and nothing in this repository depends on them.

That package, if and when it ships, will depend on this one and extend it through the same
public interfaces documented above — the plugin entry point and the adapter registries. It
will not be a fork, and none of it will be required to use nomem.