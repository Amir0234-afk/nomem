"""Importance scoring + pruning.

Runs only when the dev calls ``graph.run_decay()`` — nomem never
background-schedules it (AGENT.md "Decay Model"). Formula::

    importance      = (alpha * access_weight) + (beta * recency_weight)
    access_weight   = log(1 + access_count)
    recency_weight  = exp(-lambda_ * days_since_last_access)

Nodes with ``importance < importance_floor`` are prune-*eligible*; they are only
removed (retired) when ``config.pruning`` is True. Skeleton: bodies raise
``NotImplementedError``.
"""

from __future__ import annotations

from ..backends.base import BaseBackend
from ..config import DecayConfig
from ..models import DecayResult, Node

_PHASE = "decay is a Phase 2 deliverable and is not implemented yet"


class DecayEngine:
    """Executes one decay/pruning pass over a user's nodes."""

    def __init__(self, *, backend: BaseBackend) -> None:
        self.backend = backend

    async def run(self, config: DecayConfig) -> DecayResult:
        """Rescore every active node; retire prune candidates iff ``config.pruning``."""
        raise NotImplementedError(_PHASE)

    def _score(self, node: Node, config: DecayConfig) -> float:
        """Pure function of the node's access_count + last_accessed_at."""
        raise NotImplementedError(_PHASE)

    def _pruning_candidates(self, scored: dict[str, float], config: DecayConfig) -> list[str]:
        """Ids whose score is below ``config.importance_floor`` (eligibility only)."""
        raise NotImplementedError(_PHASE)


__all__ = ["DecayEngine"]
