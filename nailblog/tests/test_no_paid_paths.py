"""NailBlog is Agy-only: no paid or API route may exist anywhere under nailblog/src.

Scans every file of the source tree (AST string constants and the raw text, so comments, docstrings and AGENTS.md
count too), refuses imports of modules that were deleted, and keeps the two small Service text helpers that used to
live in test_no_paid_text.py.
"""
import ast
import tempfile
import unittest
from pathlib import Path

import nailblog
from nailblog.service import Service
from tests.helpers import settings

SRC = Path(nailblog.__file__).parent
BANNED = ('generativelanguage', 'api.deepseek', 'api.openai', 'api.cloudflare', 'ai_transport', 'gemini_text',
          'geminiimages', 'neurons')
REMOVED_MODULES = ('ai_transport', 'gemini_text')


def source_files():
    return sorted(p for p in SRC.rglob('*') if p.is_file() and '__pycache__' not in p.parts
                  and p.suffix in ('.py', '.md', '.toml', '.json', '.html', '.txt', ''))


def python_files():
    return [p for p in source_files() if p.suffix == '.py']


def hits(text):
    folded = text.casefold()
    return [word for word in BANNED if word in folded]


class NoPaidPathTests(unittest.TestCase):
    def test_the_scan_sees_the_source_tree(self):
        names = {p.name for p in python_files()}
        self.assertTrue({'service.py', 'config.py', 'text.py', 'net.py'} <= names)

    def test_removed_modules_are_gone(self):
        for name in REMOVED_MODULES:
            self.assertFalse((SRC / (name + '.py')).exists(), name)

    def test_no_banned_word_in_any_source_file_text(self):
        bad = [(str(p.relative_to(SRC)), word) for p in source_files()
               for word in hits(p.read_text(encoding='utf-8', errors='replace'))]
        self.assertEqual(bad, [])

    def test_no_banned_word_in_any_string_constant_or_identifier(self):
        bad = []
        for path in python_files():
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'), filename=str(path))):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    bad += [(path.name, word) for word in hits(node.value)]
                elif isinstance(node, ast.Name):
                    bad += [(path.name, word) for word in hits(node.id)]
                elif isinstance(node, ast.Attribute):
                    bad += [(path.name, word) for word in hits(node.attr)]
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    bad += [(path.name, word) for word in hits(node.name)]
        self.assertEqual(bad, [])

    def test_every_import_resolves_to_a_module_that_exists(self):
        missing = []
        for path in python_files():
            package = path.parent
            for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'), filename=str(path))):
                if isinstance(node, ast.ImportFrom) and node.level:
                    base = package
                    for _ in range(node.level - 1):
                        base = base.parent
                    if node.module:
                        target = base.joinpath(*node.module.split('.'))
                        if not (target.with_suffix('.py').is_file() or (target / '__init__.py').is_file()):
                            missing.append((path.name, node.module))
                    else:
                        for alias in node.names:
                            target = base / alias.name
                            if not (target.with_suffix('.py').is_file() or (target / '__init__.py').is_file()
                                    or alias.name in (base / '__init__.py').read_text(encoding='utf-8')):
                                missing.append((path.name, alias.name))
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or '']
                    missing += [(path.name, n) for n in names if n.split('.')[0] in REMOVED_MODULES]
        self.assertEqual(missing, [])

    def test_the_scanner_would_catch_a_planted_endpoint(self):
        self.assertEqual(hits('url = "https://api.deepseek.com/v1"'), ['api.deepseek'])
        self.assertEqual(hits('from .ai_transport import complete'), ['ai_transport'])
        self.assertEqual(hits('Cloudflare Neurons budget'), ['neurons'])
        self.assertEqual(hits('class GeminiImages: pass'), ['geminiimages'])


class AgyTextRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.service = Service(settings(self.temp.name))

    def test_service_has_no_model_call_route_any_more(self):
        self.assertFalse(hasattr(Service, 'model_call'))
        with self.service.state.connection() as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse({'paid_intents', 'paid_runs'} & tables)   # the old ledger is never created by the bot

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
