from __future__ import annotations

from typing import Any

from .base import Completion, LLMProvider, ProviderError, ProviderRefused, parse_json_reply
from .offline import OfflineProvider
from .remote import AnthropicProvider, OpenAIProvider

__all__ = [
    "Completion",
    "LLMProvider",
    "ProviderError",
    "ProviderRefused",
    "parse_json_reply",
    "OfflineProvider",
    "AnthropicProvider",
    "OpenAIProvider",
    "make_provider",
]


def make_provider(name: str, **kw: Any) -> LLMProvider:
    if name == "offline":
        return OfflineProvider()
    if name == "anthropic":
        return AnthropicProvider(**kw)
    if name == "openai":
        return OpenAIProvider(**kw)
    raise ValueError(f"unknown provider {name!r} (offline | anthropic | openai)")
