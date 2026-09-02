"""Minimal async JSON-over-HTTP helper (stdlib only).

The default embedder and LLM adapters talk to a local Ollama. nomem's core has
no third-party runtime dependencies, so this wraps :mod:`urllib.request` and
offloads the blocking call to a worker thread.
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any


class HTTPError(RuntimeError):
    """A non-2xx response or a transport-level failure."""


def _post_json_sync(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        detail = exc.read().decode("utf-8", "replace")
        raise HTTPError(f"POST {url} -> {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:  # pragma: no cover - network dependent
        raise HTTPError(f"POST {url} failed: {exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise HTTPError(f"POST {url} returned non-JSON body") from exc
    if not isinstance(parsed, dict):
        raise HTTPError(f"POST {url} returned a JSON {type(parsed).__name__}, expected an object")
    return parsed


async def post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """POST ``payload`` as JSON to ``url`` and return the decoded JSON response."""
    return await asyncio.to_thread(_post_json_sync, url, payload, timeout)
