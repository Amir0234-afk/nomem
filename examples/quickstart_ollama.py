"""nomem quickstart, for real — local Ollama, real embeddings, real extraction.

Prerequisites::

    ollama pull nomic-embed-text   # default embedder, 768-dim
    ollama pull llama3.1:8b        # default extraction model

Nothing else. No Postgres, no Neo4j, no API key, no network beyond localhost. The graph
is written to a SQLite file so you can reopen it and see the memory persist.

    python examples/quickstart_ollama.py

For a version that runs with no services at all, see `examples/quickstart.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

from nomem import MemoryGraph
from nomem.exceptions import NomemError

DB = Path("nomem-quickstart.sqlite")

TURNS = [
    (
        "Tell me about Kira.",
        "Kira Nolan is a marine biologist. She moved to Lisbon last year to join a "
        "coral restoration project, and she works with her colleague Tomas.",
    ),
    (
        "How's the project going?",
        "The coral project lost its funding in March, so Kira is now consulting for an "
        "aquarium instead. Tomas stayed on the project.",
    ),
]


def main() -> None:
    fresh = not DB.exists()
    print(f"{'creating' if fresh else 'reopening'} {DB}\n")

    with MemoryGraph(
        user_id="quickstart",
        backend="sqlite",
        backend_options={"path": str(DB)},
        embedder="nomic",  # nomic-embed-text via Ollama
        llm="ollama",  # llama3.1:8b for extraction
        decay="combined",
    ) as graph:
        if fresh:
            for user, assistant in TURNS:
                print(f"ingesting: {assistant[:60]}...")
                receipt = graph.ingest(user=user, assistant=assistant, context=["work"])
                print(
                    f"  created={len(receipt.nodes_created)} "
                    f"updated={len(receipt.nodes_updated)} "
                    f"retired={len(receipt.nodes_retired)} "
                    f"edges={len(receipt.edges_upserted)}"
                )
                if receipt.ambiguous_resolutions:
                    # Surfaced, never silently merged.
                    print(f"  ambiguous: {len(receipt.ambiguous_resolutions)}")
                print()

        for query in ("Where does Kira live?", "What happened to the coral project?"):
            print(f"query: {query}")
            sub = graph.retrieve(query)
            for node in sub.nodes:
                print(f"  {node.type:7} {node.label}")
            print()

        print("hierarchical retrieval (routed by the 'work' context tag):")
        sub = graph.retrieve(
            "Kira's job", config={"mode": "hierarchical"}, context=["work"]
        )
        print(f"  sub_index_used={sub.metadata.get('sub_index_used')} nodes={len(sub.nodes)}\n")

        print(f"stats: {graph.stats()}")
        print(f"\nrun again to reopen {DB} — the graph is still there.")


if __name__ == "__main__":
    try:
        main()
    except NomemError as exc:
        print(f"\nnomem error: {exc}", file=sys.stderr)
        print(
            "Is Ollama running, with `nomic-embed-text` and `llama3.1:8b` pulled?",
            file=sys.stderr,
        )
        raise SystemExit(1) from exc
