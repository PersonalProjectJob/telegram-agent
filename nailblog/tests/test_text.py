import inspect
import logging
import tempfile
import unittest
from dataclasses import replace
from unittest.mock import Mock

from nailblog import service as service_module
from nailblog.ai_transport import ProviderError, read_key as real_read_key, request_json
from nailblog.config import BudgetConfig, TextModelConfig
from nailblog.model_lock import MODEL_LOCK
from nailblog.service import Service
from nailblog.text import TextModel, TextModelError, array_schema, check_schema, object_schema, string_schema
from nailblog.usage import BudgetExceeded
from tests.fakes import FakeTransport, deepseek_envelope, ollama_envelope, read_key, scripted
from tests.helpers import settings

SCHEMA = object_schema({"title": string_schema(3, 20), "n": {"type": "integer", "minimum": 1, "maximum": 5},
                        "kind": {"type": "string", "enum": ["a", "b"]},
                        "tags": array_schema(string_schema(1, 10), 1, 3)})
GOOD = {"title": "Xin chào", "n": 3, "kind": "a", "tags": ["x"]}
KEY_FILE = "/etc/nailblog/private/model-key.json"
PRICES = BudgetConfig(daily_usd=1.0, text_prices={"deepseek-chat": (0.5, 2.0)})


class SchemaCheckTests(unittest.TestCase):
    def test_valid_value_has_no_problems(self):
        self.assertEqual(check_schema(SCHEMA, GOOD), [])

    def test_each_kind_of_violation_is_reported_with_its_path(self):
        cases = [({k: v for k, v in GOOD.items() if k != "n"}, "$.n: missing"),
                 ({**GOOD, "n": "3"}, "$.n: expected integer"),
                 ({**GOOD, "n": True}, "$.n: expected integer"),
                 ({**GOOD, "n": 9}, "$.n: above 5"), ({**GOOD, "n": 0}, "$.n: below 1"),
                 ({**GOOD, "kind": "c"}, "$.kind: value not allowed"),
                 ({**GOOD, "title": "ab"}, "$.title: too short"), ({**GOOD, "title": "x" * 21}, "$.title: too long (max 20)"),
                 ({**GOOD, "tags": []}, "$.tags: needs at least 1 items"),
                 ({**GOOD, "tags": list("abcd")}, "$.tags: needs at most 3 items"),
                 ({**GOOD, "tags": ["ok", 5]}, "$.tags[1]: expected string"),
                 ({**GOOD, "extra": 1}, "$.extra: unexpected field"), ([], "$: expected object")]
        for value, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, check_schema(SCHEMA, value))


class TextModelTests(unittest.IsolatedAsyncioTestCase):
    def make(self, transport, provider="ollama", **text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        model = "qwen3.5:9b" if provider == "ollama" else "deepseek-chat"
        config = replace(settings(directory.name), budget=PRICES,
                         text_model=TextModelConfig(provider=provider, model=model, key_file=KEY_FILE if provider == "deepseek" else "",
                                                    **text))
        self.service = Service(config)
        self.keys = []

        def reader(path, scope, environ=None, group_name=None):
            self.keys.append((path, scope, group_name))
            return read_key(path, scope, environ, group_name)
        return TextModel(config, self.service.model_call, transport=transport, key_reader=reader)

    def test_fakes_match_the_real_signatures(self):
        self.assertEqual(list(inspect.signature(FakeTransport.__call__).parameters)[1:],
                         list(inspect.signature(request_json).parameters))
        self.assertEqual(list(inspect.signature(read_key).parameters), list(inspect.signature(real_read_key).parameters))

    async def test_ollama_request_shape(self):
        transport = scripted(ollama_envelope(GOOD))
        model = self.make(transport, num_predict=2000, timeout_seconds=77)
        self.assertEqual(await model.ask("t", "SYS", "USER", SCHEMA, max_tokens=900, context=4096), GOOD)
        (call,) = transport.calls
        self.assertEqual((call["url"], call["provider"], call["timeout"]), ("http://127.0.0.1:11434/api/chat", "ollama", 77))
        payload = call["payload"]
        self.assertIs(payload["think"], False)
        self.assertIs(payload["stream"], False)
        self.assertEqual(payload["format"], SCHEMA)
        self.assertEqual(payload["options"], {"temperature": 0, "num_predict": 900, "num_ctx": 4096})
        self.assertEqual([m["role"] for m in payload["messages"]], ["system", "user"])
        self.assertEqual((payload["messages"][0]["content"], payload["messages"][1]["content"]), ("SYS", "USER"))

    async def test_num_predict_from_config_caps_the_per_call_limit(self):
        transport = scripted(ollama_envelope(GOOD), ollama_envelope(GOOD))
        model = self.make(transport, num_predict=500)
        await model.ask("t", "s", "u", SCHEMA, max_tokens=900)
        await model.ask("t", "s", "u", SCHEMA, max_tokens=300)
        self.assertEqual([c["payload"]["options"]["num_predict"] for c in transport.calls], [500, 300])

    async def test_deepseek_goes_through_complete_with_a_scoped_key_and_is_billed(self):
        transport = scripted(deepseek_envelope(GOOD, prompt=1000, completion=500))
        model = self.make(transport, provider="deepseek")
        self.assertEqual(await model.ask("t", "s", "u", SCHEMA, max_tokens=800), GOOD)
        (call,) = transport.calls
        self.assertEqual((call["url"], call["provider"]), ("https://api.deepseek.com/chat/completions", "deepseek"))
        self.assertEqual(call["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(call["payload"]["max_tokens"], 800)
        self.assertEqual(self.keys, [(KEY_FILE, "deepseek|https://api.deepseek.com", "tgnailblog")])
        rows = self.service.state.usage_between(0, 2 * 10 ** 10)
        self.assertEqual([(r[1], r[2], r[3]) for r in rows], [("deepseek", "deepseek-chat", 1500.0)])
        self.assertAlmostEqual(rows[0][4], 0.0015)

    async def test_deepseek_is_refused_when_the_daily_budget_is_spent(self):
        transport = scripted(deepseek_envelope(GOOD))
        model = self.make(transport, provider="deepseek")
        self.service.usage.record("deepseek", "deepseek-chat", 1, 5.0)
        with self.assertRaises(BudgetExceeded):
            await model.ask("t", "s", "u", SCHEMA, max_tokens=800)
        self.assertEqual(transport.calls, [])

    async def test_local_call_is_recorded_as_free_usage(self):
        model = self.make(scripted(ollama_envelope(GOOD, prompt=300, completion=120)))
        await model.ask("t", "s", "u", SCHEMA, max_tokens=900)
        rows = self.service.state.usage_between(0, 2 * 10 ** 10)
        self.assertEqual([(r[1], r[2], r[3], r[4]) for r in rows], [("ollama", "qwen3.5:9b", 420.0, 0.0)])

    async def test_one_repair_call_carries_only_a_short_error_note(self):
        bad = {k: v for k, v in GOOD.items() if k != "n"}
        transport = scripted(ollama_envelope({**bad, "title": "DỮ LIỆU RIÊNG TƯ"}), ollama_envelope(GOOD))
        model = self.make(transport)
        self.assertEqual(await model.ask("t", "s", "u", SCHEMA, max_tokens=900), GOOD)
        self.assertEqual(len(transport.calls), 2)
        repair = transport.calls[1]["payload"]["messages"]
        self.assertEqual([m["role"] for m in repair], ["system", "user", "user"])
        self.assertIn("$.n: missing", repair[2]["content"])
        self.assertNotIn("DỮ LIỆU RIÊNG TƯ", repair[2]["content"])
        self.assertLess(len(repair[2]["content"]), 400)

    async def test_never_more_than_one_repair_then_a_clear_error(self):
        bad = {**GOOD, "n": "three"}
        transport = scripted(ollama_envelope(bad), ollama_envelope(bad), ollama_envelope(GOOD))
        model = self.make(transport)
        with self.assertRaisesRegex(TextModelError, r"does not match the schema: \$\.n: expected integer"):
            await model.ask("t", "s", "u", SCHEMA, max_tokens=900)
        self.assertEqual(len(transport.calls), 2)

    async def test_unreadable_json_is_repaired_once_too(self):
        broken = {"message": {"content": "not json at all"}}
        transport = scripted(broken, ollama_envelope(GOOD))
        self.assertEqual(await self.make(transport).ask("t", "s", "u", SCHEMA, max_tokens=900), GOOD)
        transport = scripted(broken, broken, ollama_envelope(GOOD))
        with self.assertRaisesRegex(TextModelError, "invalid_json"):
            await self.make(transport).ask("t", "s", "u", SCHEMA, max_tokens=900)
        self.assertEqual(len(transport.calls), 2)

    async def test_transport_failures_are_not_retried(self):
        def fail(call, number):
            raise ProviderError("timeout")
        transport = FakeTransport(fail)
        with self.assertRaisesRegex(TextModelError, "timeout"):
            await self.make(transport).ask("t", "s", "u", SCHEMA, max_tokens=900)
        self.assertEqual(len(transport.calls), 1)

    async def test_calls_enter_the_model_lock_as_background(self):
        model = self.make(scripted(ollama_envelope(GOOD)))
        spy = Mock(wraps=MODEL_LOCK)
        original = service_module.MODEL_LOCK
        service_module.MODEL_LOCK = spy
        try:
            await model.ask("t", "s", "u", SCHEMA, max_tokens=900)
        finally:
            service_module.MODEL_LOCK = original
        spy.enter.assert_called_once_with(background=True, stop=None)

    async def test_logs_never_contain_prompt_source_or_output_text(self):
        model = self.make(scripted(ollama_envelope({**GOOD, "title": "ĐẦU RA BÍ MẬT"})))
        with self.assertLogs("nailblog.text", logging.INFO) as logs:
            await model.ask("planner", "HỆ THỐNG BÍ MẬT", "NGUỒN BÍ MẬT", SCHEMA, max_tokens=900)
        text = "\n".join(logs.output)
        for secret in ("BÍ MẬT", "test-key"):
            self.assertNotIn(secret, text)
        self.assertIn("purpose=planner", text)


if __name__ == "__main__":
    unittest.main()
