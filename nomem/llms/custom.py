"""Adapter that turns any callable into a :class:`BaseLLM`.

The callable receives the fully-rendered prompt (system + user joined) and must
return either a JSON string or a ``dict``. Sync or async.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from ..exceptions import ExtractionError
from .base import BaseLLM

LLMFn = Callable[[str], "str | dict[str, Any]"] | Callable[[str], Awaitable["str | dict[str, Any]"]]


class CallableLLM(BaseLLM):
    """Wrap ``fn`` (sync or async, ``prompt -> json``) as an LLM adapter."""

    def __init__(self, fn: LLMFn) -> None:
        self._fn = fn

    async def generate_json(
        self,
        prompt: str,
        *,
        system: str | None = None,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        rendered = f"{system}\n\n{prompt}" if system else prompt
        result = self._fn(rendered)
        if isinstance(result, Awaitable):
            result = await result

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except json.JSONDecodeError as exc:
                raise ExtractionError(
                    f"callable LLM returned invalid JSON: {result[:200]!r}"
                ) from exc

        if not isinstance(result, dict):
            raise ExtractionError(
                f"callable LLM must return a JSON object, got {type(result).__name__}"
            )
        return result
