"""Phase 0 contract: config defaults match the AGENT.md Decay Model table."""

from __future__ import annotations

import pytest
from nomem.config import (
    DecayConfig,
    IngestConfig,
    MemoryGraphConfig,
    RetrievalConfig,
    merge,
)
from nomem.exceptions import ConfigError


def test_decay_defaults_match_agent_md() -> None:
    d = DecayConfig()
    assert d.alpha == 0.6
    assert d.beta == 0.4
    assert d.lambda_ == 0.1
    assert d.importance_floor == 0.05
    assert d.pruning is False
    assert d.decay_schedule is None
    assert d.count_on_ingest is False


def test_ingest_defaults() -> None:
    i = IngestConfig()
    assert i.cross_reference is False  # AGENT.md: opt-in
    assert i.resolution_strategy == "hybrid"  # = max(embedding, string), ROADMAP Q5
    assert i.edge_types is None  # track any relation the extractor emits
    assert 0.0 <= i.resolution_confidence_threshold <= 1.0
    assert not hasattr(i, "embed_immediately")  # removed at 0.1.0 — ROADMAP Q5


def test_retrieval_defaults() -> None:
    r = RetrievalConfig()
    assert r.mode == "flat"  # AGENT.md default
    assert r.sub_index_strategy == "hybrid"


def test_top_level_defaults() -> None:
    c = MemoryGraphConfig(user_id="u1")
    assert c.backend == "sqlite"
    assert c.embedder == "nomic"
    assert c.decay == "combined"
    assert c.ingest_mode == "auto"


def test_user_id_required() -> None:
    with pytest.raises(ConfigError):
        MemoryGraphConfig(user_id="")


def test_out_of_range_raises() -> None:
    with pytest.raises(ConfigError):
        DecayConfig(alpha=-1.0)
    with pytest.raises(ConfigError):
        RetrievalConfig(top_k=0)
    with pytest.raises(ConfigError):
        IngestConfig(resolution_confidence_threshold=1.5)
    with pytest.raises(ConfigError):
        IngestConfig(resolution_strategy="vibes")


def test_merge_applies_overrides() -> None:
    base = RetrievalConfig()
    merged = merge(base, {"mode": "hierarchical", "hop_depth": 4})
    assert merged.mode == "hierarchical"
    assert merged.hop_depth == 4
    assert base.mode == "flat"  # unchanged


def test_merge_rejects_unknown_keys() -> None:
    with pytest.raises(ConfigError):
        merge(RetrievalConfig(), {"nonsense": 1})


def test_merge_none_is_noop() -> None:
    base = DecayConfig()
    assert merge(base, None) is base


def test_repr_redacts_adapter_option_values() -> None:
    """Secrets must never reach a log or traceback through `graph.config`.

    `.config` is public API, so it lands in `logging`, error reporters, and
    unhandled tracebacks. The generated dataclass repr printed the Postgres DSN
    — password and all.
    """
    cfg = MemoryGraphConfig(
        user_id="u1",
        backend="postgres",
        backend_options={"dsn": "postgresql://nomem:SUPERSECRET@localhost:5433/nomem"},
        embedder_options={"api_key": "sk-TOPSECRET"},
        llm_options={"token": "HUNTER2"},
    )
    text = repr(cfg)
    for secret in ("SUPERSECRET", "sk-TOPSECRET", "HUNTER2"):
        assert secret not in text, f"{secret} leaked into repr(MemoryGraphConfig)"

    # Keys and field names stay visible — the repr must still be useful.
    assert "backend_options=" in text
    assert "'dsn'" in text
    assert "'api_key'" in text
    assert "user_id='u1'" in text
    assert "***" in text


def test_repr_of_openai_embedder_hides_the_key() -> None:
    from nomem.embedders.openai import OpenAIEmbedder

    assert "sk-TOPSECRET" not in repr(OpenAIEmbedder(api_key="sk-TOPSECRET"))
