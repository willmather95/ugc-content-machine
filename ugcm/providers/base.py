"""LLM provider interface. Every text step goes through this, so swapping the offline provider
for a real model changes one flag, not the pipeline."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..prompts import PromptSpec


class ProviderError(RuntimeError):
    pass


class ProviderRefused(ProviderError):
    """Raised when a real provider is asked to run without a key or without network permission."""


@dataclass(frozen=True)
class Completion:
    text: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    meta: dict[str, Any] = field(default_factory=dict)

    def json(self) -> Any:
        return parse_json_reply(self.text)


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    is_paid: bool
    deterministic: bool

    def complete(self, spec: PromptSpec) -> Completion: ...


def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token for English)."""
    return max(1, (len(text) + 3) // 4)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_json_reply(text: str) -> Any:
    """Models sometimes wrap JSON in code fences or add a sentence. Take the first JSON object."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ProviderError(f"reply is not JSON: {text[:120]!r}") from None
        try:
            return json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ProviderError(f"reply is not JSON: {exc}") from None
