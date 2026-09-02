"""Live end-to-end coverage against a real local Ollama.

Skipped automatically unless Ollama is reachable with both models pulled:

    ollama pull nomic-embed-text
    ollama pull llama3.1:8b
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest
from nomem import MemoryGraph

_HOST = "http://localhost:11434"
_NEEDED = {"nomic-embed-text", "llama3.1:8b"}


def _ollama_ready() -> bool:
    try:
        with urllib.request.urlopen(f"{_HOST}/api/tags", timeout=3) as resp:
            tags = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError):
        return False
    have = {m["name"] for m in tags.get("models", [])}
    have |= {n.split(":", 1)[0] for n in have}  # accept "name" or "name:tag"
    return _NEEDED.issubset(have)


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not _ollama_ready(), reason="local Ollama with required models not available"
    ),
]


@pytest.fixture
def graph(tmp_path) -> MemoryGraph:  # type: ignore[no-untyped-def]
    return MemoryGraph(
        user_id="live-user",
        backend="sqlite",
        backend_options={"path": str(tmp_path / "live.sqlite")},
        embedder="nomic",
        llm="ollama",
        llm_options={"model": "llama3.1:8b"},
    )


async def test_real_ingest_then_retrieve(graph: MemoryGraph) -> None:
    receipt = await graph.aingest(
        "Tell me about Kira.",
        "Kira Nolan is a marine biologist who lives in Lisbon and studies octopuses.",
    )
    assert receipt.nodes_created, "extraction produced no nodes"

    result = await graph.aretrieve("Where does Kira live?")
    assert result.nodes
    assert result.metadata["retrieval_mode"] == "flat"
    labels = " ".join(n.label.lower() for n in result.nodes)
    assert "lisbon" in labels or "kira" in labels


async def test_real_embeddings_are_768_dim(graph: MemoryGraph) -> None:
    vec = await graph.embedder.embed("hello world")
    assert len(vec) == 768
