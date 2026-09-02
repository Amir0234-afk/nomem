"""Importance scoring + pruning.

Runs only when the dev calls ``graph.run_decay()`` — nomem never
background-schedules it (AGENT.md "Decay Model")::

    importance      = (alpha * access_weight) + (beta * recency_weight)
    access_weight   = log(1 + access_count)
    recency_weight  = exp(-lambda_ * days_since_last_access)

``DecayConfig.mode`` (set from ``MemoryGraph(decay=...)``) zeroes one term:
``"access"`` -> recency off, ``"time"`` -> access off, ``"combined"`` -> both,
``None`` -> decay disabled (``run_decay`` raises).

Nodes scoring below ``importance_floor`` are prune-*eligible*; they are retired
(never hard-deleted) only when ``pruning`` is True. The per-node formula lives
here so every backend scores identically; the pass itself is a backend method.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from ..backends.base import BaseBackend
from ..config import DecayConfig
from ..exceptions import ConfigError
from ..models import DecayResult, Node


def score_node(node: Node, config: DecayConfig, now: datetime | None = None) -> float:
    """Return the decayed importance for ``node`` under ``config``."""
    if config.mode is None:
        return node.importance
    now = now or datetime.now(tz=UTC)

    last = node.last_accessed_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    days = max(0.0, (now - last).total_seconds() / 86_400.0)

    access_w = math.log1p(max(0, node.access_count))
    recency_w = math.exp(-config.lambda_ * days)

    alpha = config.alpha if config.mode in ("access", "combined") else 0.0
    beta = config.beta if config.mode in ("time", "combined") else 0.0
    return alpha * access_w + beta * recency_w


class DecayEngine:
    """Entry point for one decay/pruning pass. Delegates the write to the backend."""

    def __init__(self, *, backend: BaseBackend) -> None:
        self.backend = backend

    async def run(self, config: DecayConfig) -> DecayResult:
        """Rescore every active node; retire prune candidates iff ``config.pruning``."""
        if config.mode is None:
            raise ConfigError(
                "decay is disabled (decay=None); construct MemoryGraph with "
                "decay='time' | 'access' | 'combined' to run a decay pass"
            )
        return await self.backend.run_decay(config)


__all__ = ["DecayEngine", "score_node"]
