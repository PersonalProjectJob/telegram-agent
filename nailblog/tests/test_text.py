import logging
import tempfile
import threading
import unittest

from nailblog.service import Service
from nailblog.text import TextModel, TextModelError, array_schema, check_schema, object_schema, string_schema
from tests.helpers import settings

SCHEMA = object_schema({"title": string_schema(3, 20), "n": {"type": "integer", "minimum": 1, "maximum": 5},
                        "kind": {"type": "string", "enum": ["a", "b"]},
                        "tags": array_schema(string_schema(1, 10), 1, 3)})
GOOD = {"title": "Xin chào", "n": 3, "kind": "a", "tags": ["x"]}


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


class AgyAdapterTests(unittest.IsolatedAsyncioTestCase):
    """`TextModel` is only an async adapter over the sync Agy `ask(prompt, schema, *, purpose, deadline=None)`."""

    def make(self, *script):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.calls = []

        def ask(prompt, schema, *, purpose, deadline=None):
            self.calls.append((prompt, schema, purpose, threading.current_thread() is threading.main_thread()))
            item = script[min(len(self.calls), len(script)) - 1]
            if isinstance(item, BaseException):
                raise item
            return item
        return TextModel(settings(directory.name), ask=ask)

    async def test_ask_sends_system_and_user_as_one_prompt_in_a_worker_thread(self):
        model = self.make(GOOD)
        self.assertEqual(await model.ask("t", "SYS", "USER", SCHEMA, max_tokens=900, context=4096), GOOD)
        prompt, schema, purpose, on_main = self.calls[0]
        self.assertEqual((prompt, schema, purpose, on_main), ("SYS" + chr(10) * 2 + "USER", SCHEMA, "t", False))

    async def test_a_reply_that_breaks_the_schema_is_a_clear_error_and_is_not_asked_again(self):
        model = self.make({**GOOD, "n": 99})
        with self.assertRaisesRegex(TextModelError, r"does not match the schema: \$\.n: above 5"):
            await model.ask("t", "s", "u", SCHEMA, max_tokens=900)
        self.assertEqual(len(self.calls), 1)

    async def test_agy_errors_surface_by_code_without_retry(self):
        class AgyError(Exception):
            code = "unknown"
        model = self.make(AgyError("secret detail"))
        with self.assertRaisesRegex(TextModelError, "model call failed: unknown") as caught:
            await model.ask("t", "s", "u", SCHEMA, max_tokens=900)
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    async def test_before_dispatch_runs_before_the_ask(self):
        order = []
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        model = TextModel(settings(directory.name), ask=lambda *a, **k: order.append("ask") or GOOD,
                          before_dispatch=lambda: order.append("hook"))
        await model.ask("t", "s", "u", SCHEMA, max_tokens=900)
        self.assertEqual(order, ["hook", "ask"])

    async def test_legacy_constructor_options_are_accepted_and_ignored(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = Service(settings(directory.name))
        TextModel(service.config, service.model_call, transport=object(), key_reader=object(), ask=lambda *a, **k: GOOD)

    async def test_logs_never_contain_prompt_source_or_output_text(self):
        model = self.make({**GOOD, "title": "ĐẦU RA BÍ MẬT"})
        with self.assertLogs("nailblog.text", logging.INFO) as logs:
            await model.ask("planner", "HỆ THỐNG BÍ MẬT", "NGUỒN BÍ MẬT", SCHEMA, max_tokens=900)
        text = chr(10).join(logs.output)
        self.assertNotIn("BÍ MẬT", text)
        self.assertIn("purpose=planner", text)


if __name__ == "__main__":
    unittest.main()
