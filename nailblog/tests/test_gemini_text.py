"""The `gemini` text provider: request shape, usage, 429/503 handling and the real transport against a fake socket."""
import inspect
import json
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from nailblog import gemini_text
from nailblog.ai_transport import LIMIT, ProviderError, read_key as real_read_key
from nailblog.config import BudgetConfig, TextModelConfig
from nailblog.gemini_text import complete, request_gemini
from nailblog.service import Service
from nailblog.text import TextModel, TextModelError, object_schema, string_schema
from nailblog.usage import UnpricedModel
from tests.fakes import FakeTransport, gemini_envelope, read_key
from tests.helpers import settings

SCHEMA = object_schema({"title": string_schema(3, 20)})
GOOD = {"title": "Xin chào"}
URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
KEY_FILE = "/etc/nailblog/private/image-key.json"
MESSAGES = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]


def transport_of(*answers):
    """FakeTransport answering in order; an exception instance is raised."""
    queue = list(answers)

    def respond(call, number):
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer
    return FakeTransport(respond)


def run(transport, **kwargs):
    sleeps = []
    defaults = dict(key="test-key", timeout=33, max_output=700, transport=transport, sleep=sleeps.append,
                    delay=lambda: 7.0)
    defaults.update(kwargs)
    return complete("gemini-test", MESSAGES, SCHEMA, **defaults), sleeps


class SignatureTests(unittest.TestCase):
    def test_the_fake_matches_the_real_transport_and_key_reader(self):
        self.assertEqual(list(inspect.signature(FakeTransport.__call__).parameters)[1:],
                         list(inspect.signature(request_gemini).parameters))
        self.assertEqual(list(inspect.signature(read_key).parameters), list(inspect.signature(real_read_key).parameters))

    def test_the_fake_envelope_has_the_generate_content_shape(self):
        envelope = gemini_envelope(GOOD, prompt=10, candidates=20, thoughts=30)
        self.assertEqual(json.loads(envelope["candidates"][0]["content"]["parts"][0]["text"]), GOOD)
        self.assertEqual({key: envelope["usageMetadata"][key] for key in
                          ("promptTokenCount", "candidatesTokenCount", "thoughtsTokenCount")},
                         {"promptTokenCount": 10, "candidatesTokenCount": 20, "thoughtsTokenCount": 30})


class CompleteTests(unittest.TestCase):
    def test_request_shape_the_key_goes_in_a_header_never_in_the_url(self):
        transport = transport_of(gemini_envelope(GOOD))
        result, _ = run(transport)
        self.assertEqual(result, GOOD)
        (call,) = transport.calls
        self.assertEqual((call["url"], call["provider"], call["timeout"]), (URL, "gemini", 33))
        self.assertEqual(call["headers"], {"Content-Type": "application/json", "x-goog-api-key": "test-key"})
        self.assertNotIn("test-key", call["url"])
        self.assertNotIn("key=", call["url"])
        payload = call["payload"]
        self.assertEqual(payload["generationConfig"]["responseMimeType"], "application/json")
        self.assertNotIn("thinkingConfig", payload["generationConfig"])
        system = payload["systemInstruction"]["parts"][0]["text"]
        self.assertTrue(system.startswith("SYS\nReturn only one JSON object that follows this JSON Schema. The object is "
                                          "the answer itself: never repeat or describe the schema. Schema: "))
        self.assertIn(json.dumps(SCHEMA, ensure_ascii=False), system)
        self.assertEqual(payload["contents"], [{"role": "user", "parts": [{"text": "USER"}]}])
        self.assertNotIn("test-key", json.dumps(payload))

    def test_thinking_level_and_output_cap_with_headroom_for_the_thinking_tokens(self):
        for thinking, extra in ((None, 16384), ("low", 2048), ("minimal", 1024), ("medium", 4096), ("high", 16384)):
            with self.subTest(thinking=thinking):
                transport = transport_of(gemini_envelope(GOOD))
                run(transport, thinking=thinking)
                config = transport.calls[0]["payload"]["generationConfig"]
                self.assertEqual(config["maxOutputTokens"], 700 + extra)
                self.assertEqual(config.get("thinkingConfig"), {"thinkingLevel": thinking} if thinking else None)

    def test_a_repair_turn_is_merged_into_the_user_turn_and_an_assistant_turn_is_the_model_role(self):
        transport = transport_of(gemini_envelope(GOOD))
        messages = MESSAGES + [{"role": "user", "content": "sửa lại"}]
        complete("gemini-test", messages, SCHEMA, key="k", transport=transport)
        self.assertEqual(transport.calls[0]["payload"]["contents"],
                         [{"role": "user", "parts": [{"text": "USER"}, {"text": "sửa lại"}]}])
        body = gemini_text.build_body(MESSAGES + [{"role": "assistant", "content": "A"}, {"role": "user", "content": "B"}],
                                      SCHEMA, max_output=10)
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])

    def test_usage_counts_prompt_candidates_and_thinking_tokens(self):
        seen = []
        run(transport_of(gemini_envelope(GOOD, prompt=1000, candidates=300, thoughts=700)), usage_sink=seen.append)
        self.assertEqual(seen, [{"prompt_tokens": 1000, "completion_tokens": 1000, "cache_hit_tokens": 0,
                                 "thought_tokens": 700}])
        seen.clear()
        run(transport_of({**gemini_envelope(GOOD), "usageMetadata": "garbage"}), usage_sink=seen.append)
        self.assertEqual(seen[0]["prompt_tokens"], 0)
        seen.clear()
        run(transport_of(gemini_envelope(GOOD, prompt=5, candidates=6)), usage_sink=seen.append)
        self.assertEqual((seen[0]["completion_tokens"], seen[0]["thought_tokens"]), (6, 0))

    def test_thought_parts_are_ignored_and_only_the_answer_text_is_parsed(self):
        parts = [{"text": "suy nghĩ", "thought": True}, {"text": json.dumps(GOOD)}]
        self.assertEqual(run(transport_of(gemini_envelope(None, parts=parts)))[0], GOOD)

    def test_a_stray_closing_brace_or_a_code_fence_around_the_object_is_tolerated(self):
        for text in ('{"title": "Xin chào"}}', '{"title": "Xin chào"}\n}\n', '{"title": "Xin chào"} ]',
                     '```json\n{"title": "Xin chào"}\n```', '```\n{"title": "Xin chào"}\n```', '  {"title": "Xin chào"}  '):
            with self.subTest(text=text):
                self.assertEqual(run(transport_of(gemini_envelope(None, parts=[{"text": text}])))[0], GOOD)

    def test_a_second_value_or_other_text_after_the_object_is_not_tolerated(self):
        for text in ('{"title": "Xin chào"}{"a": 1}', '{"title": "Xin chào"} ok', '{"title": "Xin chào"}, {"a": 1}',
                     '{"title": "Xin chào"', 'Đây là kết quả: {"title": "Xin chào"}', '{"title": "Xin chào"}}x'):
            with self.subTest(text=text), self.assertRaises(ProviderError) as raised:
                run(transport_of(gemini_envelope(None, parts=[{"text": text}])))
            self.assertEqual(raised.exception.code, "invalid_json")

    def test_unreadable_answers_are_invalid_json(self):
        for envelope in (gemini_envelope(None, parts=[{"text": "not json"}]), gemini_envelope(None, parts=[{"text": "[1]"}]),
                         gemini_envelope(None, parts=[]), {"candidates": []}, {}, {"candidates": [{"finishReason": "SAFETY"}]}):
            with self.subTest(envelope=envelope), self.assertRaises(ProviderError) as raised:
                run(transport_of(envelope))
            self.assertEqual(raised.exception.code, "invalid_json")

    def test_a_rate_limit_is_retried_once_after_a_pause_then_succeeds(self):
        transport = transport_of(ProviderError("rate_limited"), gemini_envelope(GOOD))
        result, sleeps = run(transport)
        self.assertEqual((result, len(transport.calls), sleeps), (GOOD, 2, [7.0]))
        transport = transport_of(ProviderError("provider_unavailable"), gemini_envelope(GOOD))
        self.assertEqual(run(transport)[1], [7.0])

    def test_two_failures_raise_the_code_after_exactly_two_calls_and_one_pause(self):
        for code in ("rate_limited", "provider_unavailable"):
            with self.subTest(code=code):
                transport = transport_of(ProviderError(code), ProviderError(code), gemini_envelope(GOOD))
                sleeps = []
                with self.assertRaises(ProviderError) as raised:
                    complete("gemini-test", MESSAGES, SCHEMA, key="k", transport=transport, sleep=sleeps.append,
                             delay=lambda: 6.0)
                self.assertEqual((raised.exception.code, len(transport.calls), sleeps), (code, 2, [6.0]))

    def test_other_errors_are_not_retried(self):
        for code in ("auth_failed", "model_not_found", "bad_request", "timeout", "provider_failed", "invalid_json"):
            with self.subTest(code=code):
                transport = transport_of(ProviderError(code), gemini_envelope(GOOD))
                with self.assertRaises(ProviderError) as raised:
                    run(transport)
                self.assertEqual((raised.exception.code, len(transport.calls)), (code, 1))

    def test_the_default_pause_is_between_5_and_10_seconds(self):
        self.assertEqual(gemini_text.RETRY_SECONDS, (5.0, 10.0))
        values = {gemini_text.retry_delay() for _ in range(50)}
        self.assertTrue(all(5.0 <= value <= 10.0 for value in values))
        slept = []
        with patch("nailblog.gemini_text.time.sleep", side_effect=slept.append):
            complete("gemini-test", MESSAGES, SCHEMA, key="k",
                     transport=transport_of(ProviderError("rate_limited"), gemini_envelope(GOOD)))
        self.assertTrue(len(slept) == 1 and 5.0 <= slept[0] <= 10.0)

    def test_bad_model_names_keys_and_oversized_input_are_refused_before_any_request(self):
        for model in ("", "../x", "a:b", "a/b", "a b", "x" * 101, None, "-x"):
            with self.subTest(model=model), self.assertRaises(ProviderError) as raised:
                complete(model, MESSAGES, SCHEMA, key="k", transport=transport_of())
            self.assertEqual(raised.exception.code, "invalid_model")
        for key in ("", "a\nb"):
            with self.subTest(key=key), self.assertRaises(ProviderError) as raised:
                complete("gemini-test", MESSAGES, SCHEMA, key=key, transport=transport_of())
            self.assertEqual(raised.exception.code, "key_required")
        big = [{"role": "user", "content": "x" * (128 * 1024 + 1)}]
        with self.assertRaises(ProviderError) as raised:
            complete("gemini-test", big, SCHEMA, key="k", transport=transport_of())
        self.assertEqual(raised.exception.code, "input_too_large")


class FakeHttpResponse:
    def __init__(self, status, body):
        self.status, self.pending, self.shut = status, body, False
        self.fp = SimpleNamespace(raw=SimpleNamespace(_sock=SimpleNamespace(settimeout=lambda seconds: None)))

    def isclosed(self):
        return not self.pending

    def read1(self, amount):
        piece, self.pending = self.pending[:amount], self.pending[amount:]
        return piece

    def close(self):
        self.shut = True


class FakeConnection:
    answer = None
    made = []

    def __init__(self, host, address, timeout):
        self.host, self.address, self.timeout, self.requests = host, address, timeout, []
        self.sock = SimpleNamespace(shutdown=lambda how: None)
        self.closed = False
        FakeConnection.made.append(self)

    def connect(self):
        pass

    def request(self, method, path, body, headers):
        self.requests.append((method, path, body, headers))

    def getresponse(self):
        return FakeConnection.answer

    def close(self):
        self.closed = True


PUBLIC = [(2, 1, 6, "", ("142.250.74.10", 443))]


class RealTransportTests(unittest.TestCase):
    def call(self, status, body=b'{"ok": true}', addresses=PUBLIC, url=URL):
        FakeConnection.answer, FakeConnection.made = FakeHttpResponse(status, body), []
        with patch.object(gemini_text, "PinnedHTTPS", FakeConnection), patch.object(gemini_text, "resolve", return_value=addresses):
            return request_gemini(url, {"a": 1}, {"x-goog-api-key": "k"}, 5, "gemini")

    def test_a_200_answer_is_decoded_and_the_request_goes_to_the_pinned_host_only(self):
        self.assertEqual(self.call(200), {"ok": True})
        (connection,) = FakeConnection.made
        self.assertEqual((connection.host, connection.address, connection.timeout), ("generativelanguage.googleapis.com",
                                                                                    "142.250.74.10", 5))
        (method, path, body, headers), = connection.requests
        self.assertEqual((method, path, json.loads(body), headers), ("POST", "/v1beta/models/gemini-test:generateContent",
                                                                       {"a": 1}, {"x-goog-api-key": "k"}))
        self.assertTrue(connection.closed and FakeConnection.answer.shut)

    def test_http_statuses_become_distinct_codes(self):
        for status, code in ((400, "bad_request"), (401, "auth_failed"), (403, "auth_failed"), (404, "model_not_found"),
                             (429, "rate_limited"), (500, "provider_failed"), (502, "provider_failed"),
                             (503, "provider_unavailable")):
            with self.subTest(status=status), self.assertRaises(ProviderError) as raised:
                self.call(status)
            self.assertEqual(raised.exception.code, code)
            self.assertTrue(FakeConnection.made[0].closed)

    def test_bad_bodies_and_oversized_bodies(self):
        with self.assertRaises(ProviderError) as raised:
            self.call(200, b"not json")
        self.assertEqual(raised.exception.code, "invalid_json")
        with self.assertRaises(ProviderError) as raised:
            self.call(200, b"x" * (LIMIT + 1))
        self.assertEqual(raised.exception.code, "response_too_large")

    def test_private_or_missing_addresses_and_foreign_urls_are_refused_without_connecting(self):
        private = [(2, 1, 6, "", ("10.0.0.5", 443))]
        loopback = [(2, 1, 6, "", ("127.0.0.1", 443))]
        for addresses in (private, loopback, PUBLIC + private, []):
            with self.subTest(addresses=addresses), self.assertRaises(ProviderError) as raised:
                self.call(200, addresses=addresses)
            self.assertEqual(raised.exception.code, "invalid_endpoint")
            self.assertTrue(all(connection.requests == [] for connection in FakeConnection.made))
        for url in ("https://evil.example/v1beta/models/m:generateContent", "http://generativelanguage.googleapis.com/v1beta/models/m:generateContent",
                    URL + "?key=abc", URL + "#x", "https://generativelanguage.googleapis.com/v1beta/models/m:other",
                    "https://generativelanguage.googleapis.com/v1/models/m:generateContent",
                    "https://user@generativelanguage.googleapis.com/v1beta/models/m:generateContent",
                    "https://generativelanguage.googleapis.com:8443/v1beta/models/m:generateContent",
                    "https://generativelanguage.googleapis.com.evil.example/v1beta/models/m:generateContent"):
            with self.subTest(url=url), self.assertRaises(ProviderError) as raised:
                self.call(200, url=url)
            self.assertEqual(raised.exception.code, "invalid_endpoint")
            self.assertEqual(FakeConnection.made, [])


PRICED = BudgetConfig(daily_usd=1.0, text_prices={"gemini-test": (0.0, 0.0)})


class GeminiTextModelTests(unittest.IsolatedAsyncioTestCase):
    def make(self, transport, budget=PRICED, **text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = replace(settings(directory.name), budget=budget,
                         text_model=TextModelConfig(provider="gemini", model="gemini-test", key_file=KEY_FILE, **text))
        self.service = Service(config)
        self.keys = []

        def reader(path, scope, environ=None, group_name=None):
            self.keys.append((path, scope, group_name))
            return read_key(path, scope, environ, group_name)
        return TextModel(config, self.service.model_call, transport=transport, key_reader=reader)

    async def test_ask_goes_through_gemini_with_a_scoped_key_and_is_recorded_as_free_usage(self):
        transport = transport_of(gemini_envelope(GOOD, prompt=1000, candidates=300, thoughts=700))
        model = self.make(transport)
        self.assertEqual(await model.ask("t", "SYS", "USER", SCHEMA, max_tokens=700), GOOD)
        (call,) = transport.calls
        self.assertEqual((call["url"], call["provider"]), (URL, "gemini"))
        self.assertEqual(call["headers"]["x-goog-api-key"], "test-key")
        self.assertEqual(self.keys, [(KEY_FILE, "gemini|https://generativelanguage.googleapis.com", "tgnailblog")])
        rows = self.service.state.usage_between(0, 2 * 10 ** 10)
        self.assertEqual([(r[1], r[2], r[3], r[4]) for r in rows], [("gemini", "gemini-test", 2000.0, 0.0)])

    async def test_only_light_calls_get_the_section_thinking_level(self):
        transport = transport_of(gemini_envelope(GOOD), gemini_envelope(GOOD), gemini_envelope(GOOD))
        model = self.make(transport)
        await model.ask("outline", "s", "u", SCHEMA, max_tokens=700)
        await model.ask("section", "s", "u", SCHEMA, max_tokens=700, light=True)
        configs = [call["payload"]["generationConfig"] for call in transport.calls]
        self.assertNotIn("thinkingConfig", configs[0])
        self.assertEqual(configs[1]["thinkingConfig"], {"thinkingLevel": "low"})
        self.assertEqual((configs[0]["maxOutputTokens"], configs[1]["maxOutputTokens"]), (700 + 16384, 700 + 2048))
        model = self.make(transport_of(gemini_envelope(GOOD)), section_thinking="")
        await model.ask("section", "s", "u", SCHEMA, max_tokens=700, light=True)
        self.assertNotIn("thinkingConfig", model.transport.calls[0]["payload"]["generationConfig"])

    async def test_num_predict_caps_the_visible_output_before_the_headroom(self):
        transport = transport_of(gemini_envelope(GOOD))
        await self.make(transport, num_predict=500).ask("t", "s", "u", SCHEMA, max_tokens=900, light=True)
        self.assertEqual(transport.calls[0]["payload"]["generationConfig"]["maxOutputTokens"], 500 + 2048)

    async def test_a_model_without_a_price_is_refused_before_the_request(self):
        transport = transport_of(gemini_envelope(GOOD))
        model = self.make(transport, budget=BudgetConfig(daily_usd=1.0))
        with self.assertRaises(UnpricedModel):
            await model.ask("t", "s", "u", SCHEMA, max_tokens=700)
        self.assertEqual(transport.calls, [])

    async def test_provider_errors_become_text_model_errors_with_their_code(self):
        for code in ("auth_failed", "model_not_found", "bad_request"):
            with self.subTest(code=code):
                transport = transport_of(ProviderError(code))
                with self.assertRaisesRegex(TextModelError, f"model call failed: {code}"):
                    await self.make(transport).ask("t", "s", "u", SCHEMA, max_tokens=700)
                self.assertEqual(len(transport.calls), 1)

    async def test_a_rate_limit_waits_once_then_reports_its_code(self):
        transport = transport_of(ProviderError("rate_limited"), ProviderError("rate_limited"), gemini_envelope(GOOD))
        slept = []
        with patch("nailblog.gemini_text.time.sleep", side_effect=slept.append):
            with self.assertRaisesRegex(TextModelError, "model call failed: rate_limited"):
                await self.make(transport).ask("t", "s", "u", SCHEMA, max_tokens=700)
        self.assertEqual((len(transport.calls), len(slept)), (2, 1))
        self.assertTrue(5.0 <= slept[0] <= 10.0)

    async def test_the_default_transport_is_the_real_gemini_one_when_none_is_injected(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = replace(settings(directory.name), budget=PRICED,
                         text_model=TextModelConfig(provider="gemini", model="gemini-test", key_file=KEY_FILE))
        service = Service(config)
        model = TextModel(config, service.model_call, key_reader=read_key)
        FakeConnection.answer = FakeHttpResponse(200, json.dumps(gemini_envelope(GOOD)).encode())
        FakeConnection.made = []
        with patch.object(gemini_text, "PinnedHTTPS", FakeConnection), patch.object(gemini_text, "resolve", return_value=PUBLIC):
            self.assertEqual(await model.ask("t", "s", "u", SCHEMA, max_tokens=700), GOOD)
        self.assertEqual(FakeConnection.made[0].requests[0][1], "/v1beta/models/gemini-test:generateContent")
        self.assertEqual(FakeConnection.made[0].requests[0][3]["x-goog-api-key"], "test-key")


if __name__ == "__main__":
    unittest.main()
