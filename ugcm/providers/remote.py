"""Real LLM providers: Anthropic Messages API and OpenAI Chat Completions.

Wired, never called in tests or the demo. Each one refuses to send unless:
  1. its API key is in the environment, and
  2. the caller passed allow_network=True (the CLI flag is --allow-network).

`build_request()` is pure and is what the tests check. The key never enters the request dict
that gets logged; it is added to the headers only at send time.
"""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Callable

from ..prompts import PromptSpec
from .base import Completion, ProviderError, ProviderRefused

Transport = Callable[[str, dict[str, str], bytes, float], dict[str, Any]]


def _urllib_transport(url: str, headers: dict[str, str], body: bytes, timeout: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https hosts)
        return json.loads(resp.read().decode("utf-8"))


class _RemoteProvider:
    name = "remote"
    key_env = ""
    is_paid = True
    deterministic = False  # real models do not promise byte-identical output, even with a seed

    def __init__(self, model: str, *, allow_network: bool = False, transport: Transport | None = None, timeout: float = 60.0):
        self.model = model
        self.allow_network = allow_network
        self.transport = transport or _urllib_transport
        self.timeout = timeout

    def _key(self) -> str:
        key = os.environ.get(self.key_env, "")
        if not key:
            raise ProviderRefused(f"{self.name}: {self.key_env} is not set. Use --provider offline, or set the key and pass --allow-network.")
        if not self.allow_network:
            raise ProviderRefused(f"{self.name}: network is off. Pass --allow-network to call a paid model.")
        return key

    def build_request(self, spec: PromptSpec) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _auth_headers(self, key: str) -> dict[str, str]:  # pragma: no cover - abstract
        raise NotImplementedError

    def _parse(self, data: dict[str, Any]) -> Completion:  # pragma: no cover - abstract
        raise NotImplementedError

    def complete(self, spec: PromptSpec) -> Completion:
        key = self._key()
        req = self.build_request(spec)
        headers = {**req["headers"], **self._auth_headers(key)}
        data = self.transport(req["url"], headers, json.dumps(req["body"]).encode("utf-8"), self.timeout)
        return self._parse(data)


class AnthropicProvider(_RemoteProvider):
    name = "anthropic"
    key_env = "ANTHROPIC_API_KEY"
    URL = "https://api.anthropic.com/v1/messages"
    VERSION = "2023-06-01"

    def __init__(self, model: str | None = None, **kw: Any):
        super().__init__(model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5-5"), **kw)

    def build_request(self, spec: PromptSpec) -> dict[str, Any]:
        return {
            "url": self.URL,
            "headers": {"content-type": "application/json", "anthropic-version": self.VERSION},
            "body": {
                "model": self.model,
                "max_tokens": spec.max_tokens,
                "system": spec.system,
                "messages": [{"role": "user", "content": spec.user}],
                "metadata": {"user_id": f"ugcm-{spec.prompt_sha}"},
            },
        }

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {"x-api-key": key}

    def _parse(self, data: dict[str, Any]) -> Completion:
        try:
            text = "".join(b.get("text", "") for b in data["content"] if b.get("type") == "text")
            usage = data.get("usage", {})
            return Completion(text, self.name, data.get("model", self.model), int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0)), {"stop_reason": data.get("stop_reason")})
        except (KeyError, TypeError) as exc:
            raise ProviderError(f"anthropic: unexpected reply shape: {exc}") from None


class OpenAIProvider(_RemoteProvider):
    name = "openai"
    key_env = "OPENAI_API_KEY"
    URL = "https://api.openai.com/v1/chat/completions"

    def __init__(self, model: str | None = None, **kw: Any):
        # Set OPENAI_MODEL to the model you want; the default is a placeholder to verify.
        super().__init__(model or os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"), **kw)

    def build_request(self, spec: PromptSpec) -> dict[str, Any]:
        return {
            "url": self.URL,
            "headers": {"content-type": "application/json"},
            "body": {
                "model": self.model,
                "max_tokens": spec.max_tokens,
                "seed": spec.seed % (2**31),
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": spec.system},
                    {"role": "user", "content": spec.user},
                ],
            },
        }

    def _auth_headers(self, key: str) -> dict[str, str]:
        return {"authorization": f"Bearer {key}"}

    def _parse(self, data: dict[str, Any]) -> Completion:
        try:
            text = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            return Completion(text, self.name, data.get("model", self.model), int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), {"finish_reason": data["choices"][0].get("finish_reason")})
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"openai: unexpected reply shape: {exc}") from None
