"""Exception hierarchy for nomem.

Everything raised by nomem derives from :class:`NomemError` so applications can
catch the whole surface with one ``except``. Per AGENT.md, ingest failures are
never silently discarded — they surface as one of these.
"""

from __future__ import annotations


class NomemError(Exception):
    """Base class for every error raised by nomem."""


class ConfigError(NomemError):
    """A configuration value is missing, malformed, or out of range."""


class BackendError(NomemError):
    """A storage backend adapter failed or returned an invalid result."""


class NodeNotFoundError(BackendError):
    """A node id (optionally ``as_of`` a timestamp) has no matching record."""


class EdgeNotFoundError(BackendError):
    """An edge id (optionally ``as_of`` a timestamp) has no matching record."""


class NotSupportedError(NomemError):
    """An adapter does not implement an optional capability.

    Raised by the default :meth:`~nomem.backends.base.BaseBackend.purge_user`
    body: the method is public and non-abstract so a custom backend need not
    implement it, but calling it on one that hasn't is an error, not a no-op.
    """


class EmbedderError(NomemError):
    """An embedder adapter failed to produce a vector."""


class ExtractionError(NomemError):
    """The entity/relation extraction step failed."""


class RetrievalBudgetExceededError(NomemError):
    """A retrieval would exceed the configured token budget without an override."""
