"""Sync-over-async glue.

The sync API is a thin wrapper over the async core — there is no separate sync
implementation (AGENT.md "Public API"). Every sync method on
:class:`nomem.MemoryGraph` funnels through :func:`run_sync`.

Sync calls run on one dedicated background event loop that lives for the process,
not a fresh ``asyncio.run()`` loop per call. Networked backends (the Postgres
pool, the Neo4j driver) bind their connections to the loop that created them, so
a per-call loop would break the second call. The shared loop keeps them valid.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from .exceptions import NomemError

_T = TypeVar("_T")


class _LoopThread:
    """A single asyncio loop running on a daemon thread, created on first use."""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            with self._lock:
                if self._loop is None:
                    loop = asyncio.new_event_loop()
                    thread = threading.Thread(
                        target=loop.run_forever,
                        name="nomem-sync-loop",
                        daemon=True,
                    )
                    thread.start()
                    self._loop = loop
        return self._loop

    def run(self, coro: Coroutine[Any, Any, _T]) -> _T:
        loop = self._ensure()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result()


_RUNNER = _LoopThread()


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
            "use the async variants (aingest / aretrieve / arun_decay / aclose)"
        )
    return _RUNNER.run(coro)


__all__ = ["run_sync"]
