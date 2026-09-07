"""nomem quickstart — no Ollama, no Docker, no network, no API key.

Runs the whole pipeline against SQLite `:memory:` using two tiny stand-ins: a
deterministic hash embedder and a scripted LLM. That keeps the example honest about
*nomem's* behavior while removing every external dependency, so this file doubles as a
packaging smoke test.

    python examples/quickstart.py

For the real thing — Ollama, `nomic-embed-text`, `llama3.1:8b` — see
`examples/quickstart_ollama.py`.
"""

from __future__ import annotations

import hashlib
import math
from datetime import UTC, datetime
from typing import Any

from nomem import MemoryGraph
from nomem.embedders import CallableEmbedder
from nomem.llms import CallableLLM

DIMS = 32


def embed(text: str) -> list[float]:
    """Deterministic bag-of-words hash -> unit vector.

    Not semantic. Identical text gives cosine 1.0 and shared tokens give a high score,
    which is all entity resolution needs to be demonstrable.
    """
    vec = [0.0] * DIMS
    for token in text.lower().split():
        digest = hashlib.sha256(token.encode()).digest()
        vec[digest[0] % DIMS] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def scripted_llm(prompt: str) -> dict[str, Any]:
    """Stands in for the extraction model.

    A real adapter sends the prompt to a model and returns its JSON. This returns a
    canned graph so the example is deterministic. The shape is exactly what
    `EntityExtractor` expects.

    Note the entity labels are short and distinct ("Berlin", "Lisbon") rather than
    whole sentences. `embed` above is a *hash* of tokens, not a semantic model, so
    "Kira lives in Berlin" and "Kira lives in Lisbon" score 0.75 cosine against each
    other — over the 0.75 default resolution threshold — and the new fact would be
    resolved onto the old node instead of creating one. Naming the things themselves
    is both better modeling and what keeps this example legible.
    """
    if "Lisbon" in prompt:
        return {
            "entities": [
                {"label": "Kira", "type": "entity", "negated": False},
                # `negated` is how a turn retires a fact that no longer holds.
                {"label": "Berlin", "type": "entity", "negated": True},
                {"label": "Lisbon", "type": "entity", "negated": False},
            ],
            "relations": [
                {
                    "source": "Kira",
                    "target": "Lisbon",
                    "relation": "lives_in",
                    "weight": 1.0,
                }
            ],
        }
    if "Berlin" in prompt:
        return {
            "entities": [
                {"label": "Kira", "type": "entity", "negated": False},
                {"label": "Berlin", "type": "entity", "negated": False},
            ],
            "relations": [
                {
                    "source": "Kira",
                    "target": "Berlin",
                    "relation": "lives_in",
                    "weight": 1.0,
                }
            ],
        }
    return {"entities": [], "relations": []}


def build(**kwargs: Any) -> MemoryGraph:
    return MemoryGraph(
        user_id="quickstart",
        backend="sqlite",
        backend_options={"path": ":memory:"},
        embedder=CallableEmbedder(embed, dimensions=DIMS),
        llm=CallableLLM(scripted_llm),
        decay="combined",
        **kwargs,
    )


def main() -> None:
    with build() as graph:
        print("=== 1. ingest a turn ===")
        receipt = graph.ingest(
            user="Where is Kira these days?",
            assistant="Kira moved to Berlin last year.",
            context=["people"],
        )
        print(f"created={len(receipt.nodes_created)} edges={len(receipt.edges_upserted)}")

        checkpoint = datetime.now(UTC)

        print("\n=== 2. the world changes ===")
        receipt = graph.ingest(
            user="Any news about Kira?",
            assistant="Kira has moved again — she lives in Lisbon now.",
            context=["people"],
        )
        print(
            f"created={len(receipt.nodes_created)} "
            f"retired={len(receipt.nodes_retired)}  <- Berlin was retired, not deleted"
        )

        print("\n=== 3. retrieve ===")
        for node in graph.retrieve("Where does Kira live?").nodes:
            print(f"  {node.type:7} {node.label}")

        print("\n=== 4. retrieve as of the checkpoint (before the move) ===")
        for node in graph.retrieve("Where does Kira live?", as_of=checkpoint).nodes:
            print(f"  {node.type:7} {node.label}")

        print("\n=== 5. stats ===")
        stats = graph.stats()
        print(f"  nodes: {stats['nodes_active']} active, {stats['nodes_retired']} retired")
        print(f"  edges: {stats['edges_active']} active, {stats['edges_retired']} retired")
        print(f"  by type: {stats['nodes_by_type']}")

        print("\n=== 6. decay ===")
        result = graph.run_decay()
        print(f"  rescored={len(result.scores)} prune_candidates={len(result.prune_candidates)}")

    print("\n=== 7. dry run (ingest_mode='manual') — writes nothing ===")
    with build(ingest_mode="manual") as graph:
        receipt = graph.ingest(
            user="Where is Kira these days?",
            assistant="Kira moved to Berlin last year.",
        )
        for entity in receipt.queued_writes:
            print(f"  would {entity.metadata['intended_op']:6} {entity.label}")
        print(f"  nodes actually written: {graph.stats()['nodes_total']}")


if __name__ == "__main__":
    main()
