"""Sync-over-async glue.

The sync API is a thin wrapper over the async core — there is no separate sync
implementation (AGENT.md "Public API"). Every sync method on
:class:`nomem.MemoryGraph` funnels through :func:`run_sync`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any, TypeVar

from .exceptions import NomemError

_T = TypeVar("_T")


def run_sync(coro: Coroutine[Any, Any, _T]) -> _T:
    """Run ``coro`` to completion from synchronous code.

    Raises :class:`NomemError` if called from inside a running event loop —
    use the ``a``-prefixed async methods there instead.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        coro.close()
        raise NomemError(
            "sync methods cannot be called from a running event loop; "
            "use the async variants (aingest / aretrieve / arun_decay)"
        )
    return asyncio.run(coro)


__all__ = ["run_sync"]
