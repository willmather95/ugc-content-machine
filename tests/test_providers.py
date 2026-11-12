import json
import os
import unittest
from unittest import mock

from ugcm.prompts import hooks_prompt, script_prompt
from ugcm.providers import (
    AnthropicProvider,
    OfflineProvider,
    OpenAIProvider,
    ProviderError,
    ProviderRefused,
    make_provider,
    parse_json_reply,
)
from ugcm.providers.offline import SCRIPT_FLAWS

from .helpers import brief, clean_hook, policy


def _hspec(seed=1, avoid=None):
    b = brief()
    return hooks_prompt(brief=b, fmt=b.formats[0], angle=b.angles[0], n=6, seed=seed, avoid=avoid)


def _sspec(seed=1, avoid=None):
    b = brief()
    return script_prompt(brief=b, policy=policy(), fmt=b.formats[0], angle=b.angles[1], hook=clean_hook(), seed=seed, avoid=avoid)


class OfflineProviderTest(unittest.TestCase):
    def test_deterministic_same_seed_same_bytes(self):
        p = OfflineProvider()
        self.assertEqual(p.complete(_hspec()).text, p.complete(_hspec()).text)
        self.assertEqual(p.complete(_sspec()).text, p.complete(_sspec()).text)

    def test_different_seed_can_differ(self):
        p = OfflineProvider(script_flaw_rate=0.0)
        texts = {p.complete(_sspec(seed=s)).text for s in range(20)}
        self.assertGreater(len(texts), 1)

    def test_returns_requested_schema(self):
        p = OfflineProvider()
        hooks = p.complete(_hspec()).json()["hooks"]
        self.assertEqual(len(hooks), 6)
        for h in hooks:
            self.assertEqual(set(h), {"line", "first_frame", "emotion"})
        script = p.complete(_sspec()).json()
        self.assertEqual([b["t"] for b in script["beats"]][0], "0-2s")
        self.assertIn("caption", script)

    def test_avoid_codes_suppress_those_flaws(self):
        # With every flaw on the avoid list, the provider must return a clean script.
        p = OfflineProvider(script_flaw_rate=1.0, retry_flaw_rate=1.0)
        for seed in range(25):
            s = p.complete(_sspec(seed=seed, avoid=list(SCRIPT_FLAWS))).json()
            text = json.dumps(s)
            self.assertNotIn("best bottle ever", text)
            self.assertNotIn("48 hours", text)
            self.assertNotIn("RivalFlaskCo", text)
            self.assertIn("Tap the link to get yours.", s["beats"][-1]["say"])

    def test_labeled_offline_and_free(self):
        p = OfflineProvider()
        c = p.complete(_hspec())
        self.assertFalse(p.is_paid)
        self.assertTrue(p.deterministic)
        self.assertIn("OFFLINE", c.meta["label"])

    def test_unknown_task(self):
        spec = _hspec()
        object.__setattr__(spec, "task", "poem")
        with self.assertRaises(ProviderError):
            OfflineProvider().complete(spec)


class RemoteProviderTest(unittest.TestCase):
    def test_refuses_without_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for p in (AnthropicProvider(allow_network=True), OpenAIProvider(allow_network=True)):
                with self.assertRaises(ProviderRefused):
                    p.complete(_sspec())

    def test_refuses_without_network_flag_even_with_key(self):
        called = []
        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-placeholder"}, clear=True):
            p = AnthropicProvider(transport=lambda *a: called.append(a) or {})
            with self.assertRaises(ProviderRefused):
                p.complete(_sspec())
        self.assertEqual(called, [])

    def test_anthropic_request_shape(self):
        req = AnthropicProvider(model="claude-test").build_request(_sspec())
        self.assertEqual(req["url"], "https://api.anthropic.com/v1/messages")
        self.assertEqual(req["headers"]["anthropic-version"], "2023-06-01")
        self.assertNotIn("x-api-key", req["headers"])  # key is added only at send time
        self.assertEqual(req["body"]["model"], "claude-test")
        self.assertEqual(req["body"]["messages"][0]["role"], "user")
        self.assertIn("approved claims", req["body"]["system"].lower())

    def test_openai_request_shape(self):
        req = OpenAIProvider(model="gpt-test").build_request(_sspec())
        self.assertEqual(req["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(req["body"]["response_format"], {"type": "json_object"})
        self.assertNotIn("authorization", req["headers"])
        self.assertEqual([m["role"] for m in req["body"]["messages"]], ["system", "user"])

    def test_anthropic_with_fake_transport_parses_reply(self):
        seen = {}

        def fake(url, headers, body, timeout):
            seen.update(url=url, headers=headers, body=json.loads(body))
            return {"model": "claude-test", "content": [{"type": "text", "text": '```json\n{"hooks": []}\n```'}], "usage": {"input_tokens": 10, "output_tokens": 5}, "stop_reason": "end_turn"}

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-placeholder"}, clear=True):
            c = AnthropicProvider(model="claude-test", allow_network=True, transport=fake).complete(_hspec())
        self.assertEqual(seen["headers"]["x-api-key"], "test-placeholder")
        self.assertEqual(c.json(), {"hooks": []})
        self.assertEqual((c.input_tokens, c.output_tokens), (10, 5))

    def test_openai_bad_reply_shape(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "test-placeholder"}, clear=True):
            p = OpenAIProvider(allow_network=True, transport=lambda *a: {"nope": 1})
            with self.assertRaises(ProviderError):
                p.complete(_hspec())

    def test_make_provider(self):
        self.assertIsInstance(make_provider("offline"), OfflineProvider)
        with self.assertRaises(ValueError):
            make_provider("gpt-2")


class ParseJsonTest(unittest.TestCase):
    def test_plain_fenced_and_chatty(self):
        self.assertEqual(parse_json_reply('{"a": 1}'), {"a": 1})
        self.assertEqual(parse_json_reply('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(parse_json_reply('Sure! Here it is: {"a": 1} Hope that helps.'), {"a": 1})

    def test_not_json(self):
        with self.assertRaises(ProviderError):
            parse_json_reply("no json here")


if __name__ == "__main__":
    unittest.main()
