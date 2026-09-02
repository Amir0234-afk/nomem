"""LLM adapters and the name -> class registry."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from ..exceptions import ConfigError
from .base import BaseLLM
from .custom import CallableLLM
from .ollama import OllamaLLM

LLM_REGISTRY: dict[str, type[BaseLLM]] = {
    "ollama": OllamaLLM,
}


def resolve_llm(
    llm: str | BaseLLM | Callable[[str], Any] | Callable[[str], Awaitable[Any]] | None,
    options: dict[str, Any] | None = None,
) -> BaseLLM:
    """Turn a config value into a live LLM adapter.

    Accepts a registered name (``"ollama"``), a :class:`BaseLLM` instance, a
    bare callable (wrapped in :class:`CallableLLM`), or ``None`` (the default
    Ollama adapter).
    """
    if llm is None:
        return OllamaLLM(**(options or {}))
    if isinstance(llm, BaseLLM):
        return llm
    if callable(llm) and not isinstance(llm, type):
        return CallableLLM(llm)
    if not isinstance(llm, str):
        raise ConfigError(f"llm must be a name, BaseLLM, or callable, got {llm!r}")
    try:
        cls = LLM_REGISTRY[llm]
    except KeyError:
        raise ConfigError(f"unknown llm {llm!r}; known: {sorted(LLM_REGISTRY)}") from None
    return cls(**(options or {}))


__all__ = ["LLM_REGISTRY", "BaseLLM", "CallableLLM", "OllamaLLM", "resolve_llm"]
