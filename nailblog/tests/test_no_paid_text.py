"""No paid or API text route may remain in nailblog/src (AST scan) and Service.model_call refuses paid providers."""
import ast
import asyncio
import tempfile
import unittest
from pathlib import Path

import nailblog
from nailblog.service import Service
from tests.helpers import settings

SRC = Path(nailblog.__file__).parent
# ai_transport.py is the byte-for-byte nailhub copy (parity test); its provider table goes away with S8 (-> net.py).
SKIP = {'ai_transport.py'}
BANNED_TEXT = ('generativelanguage', 'api.deepseek', 'api.openai')


def sources():
    for path in sorted(SRC.rglob('*.py')):
        if path.name not in SKIP:
            yield path, ast.parse(path.read_text(encoding='utf-8'), filename=str(path))


class NoPaidTextTests(unittest.TestCase):
    def test_gemini_text_module_is_gone(self):
        self.assertFalse((SRC / 'gemini_text.py').exists())

    def test_no_import_of_ai_transport_complete_or_gemini_text(self):
        bad = []
        for path, tree in sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    bad += [(path.name, a.name) for a in node.names if a.name.split('.')[-1] == 'gemini_text']
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ''
                    names = [a.name for a in node.names]
                    if module.split('.')[-1] == 'gemini_text' or 'gemini_text' in names:
                        bad.append((path.name, 'gemini_text'))
                    if module.split('.')[-1] == 'ai_transport' and 'complete' in names:
                        bad.append((path.name, 'ai_transport.complete'))
                elif isinstance(node, ast.Attribute) and node.attr == 'complete' \
                        and isinstance(node.value, ast.Name) and node.value.id == 'ai_transport':
                    bad.append((path.name, 'ai_transport.complete'))
        self.assertEqual(bad, [])

    def test_no_paid_endpoint_string_anywhere(self):
        bad = []
        for path, tree in sources():
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    bad += [(path.name, word) for word in BANNED_TEXT if word in node.value.lower()]
        self.assertEqual(bad, [])


class ServiceTextRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Service(settings(self.temp.name))

    async def test_agy_provider_runs_without_ledger_lock_or_cost(self):
        from nailblog import service as module
        entered = []

        class Lock:
            def enter(self, **kwargs):
                entered.append(kwargs)
                raise AssertionError('MODEL_LOCK must not be held for an agy call')
        original, module.MODEL_LOCK = module.MODEL_LOCK, Lock()
        try:
            self.assertEqual(await self.service.model_call(lambda a, b: a + b, 2, 3, provider='agy', model='m'), 5)
        finally:
            module.MODEL_LOCK = original
        self.assertEqual(entered, [])
        with self.service.state.connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({'paid_intents', 'paid_runs'} & tables)   # the old ledger was never opened or written

    async def test_every_other_provider_is_refused_before_the_function_runs(self):
        calls = []
        for provider in ('deepseek', 'gemini', 'ollama', 'openai'):
            with self.assertRaisesRegex(RuntimeError, 'paid_text_removed'):
                await self.service.model_call(lambda: calls.append(provider), provider=provider, model='m')
        self.assertEqual(calls, [])

    def test_text_model_is_a_bare_callable_over_agy_ask(self):
        ask = self.service.text_model()
        self.assertTrue(callable(ask))
        self.assertFalse(hasattr(ask, 'ask'))      # daily._planner_reply takes its Agy branch only for this shape
        sentinel = object()
        self.service.text = sentinel
        self.assertIs(self.service.text_model(), sentinel)

    def test_agy_jobs_today_counts_ask_calls_per_vietnam_day(self):
        from nailblog import agy_text
        seen = []
        original = agy_text.AgyText.ask
        agy_text.AgyText.ask = lambda self, prompt, schema, *, purpose, deadline=None: seen.append(purpose) or {}
        try:
            ask = self.service.text_model()
            self.assertEqual(self.service.agy_jobs_today(), 0)
            ask('p', {}, purpose='x')
            ask('p', {}, purpose='y')
        finally:
            agy_text.AgyText.ask = original
        self.assertEqual((seen, self.service.agy_jobs_today()), (['x', 'y'], 2))


if __name__ == '__main__':
    unittest.main()
