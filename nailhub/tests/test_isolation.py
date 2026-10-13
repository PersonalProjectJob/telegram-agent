"""Static import and mention boundary, including dynamic importlib calls."""
import ast
import unittest
from pathlib import Path

SOURCE=Path(__file__).resolve().parents[1]/'src'/'nailhub'
FORBIDDEN='tg_triage'


def forbidden_imports(text):
    tree=ast.parse(text)
    dynamic={'__import__','import_module'}
    for node in ast.walk(tree):
        if isinstance(node,ast.ImportFrom) and node.module=='importlib':
            dynamic.update(alias.asname or alias.name for alias in node.names if alias.name=='import_module')
    violations=[]
    for node in ast.walk(tree):
        names=[]
        if isinstance(node,ast.Import):names=[alias.name for alias in node.names]
        elif isinstance(node,ast.ImportFrom):
            names=[node.module or '']
            if node.level:names.extend(alias.name for alias in node.names)
        elif isinstance(node,ast.Call) and ((isinstance(node.func,ast.Name) and node.func.id in dynamic)
                or (isinstance(node.func,ast.Attribute) and node.func.attr=='import_module')):
            names=[arg.value for arg in node.args[:1] if isinstance(arg,ast.Constant) and isinstance(arg.value,str)]
        if any(name.split('.')[0]==FORBIDDEN for name in names):violations.append(node.lineno)
    return violations


class IsolationTests(unittest.TestCase):
    def test_source_does_not_import_other_service(self):
        violations=[]
        for path in SOURCE.rglob('*.py'):
            violations.extend((path.name,line) for line in forbidden_imports(path.read_text(encoding='utf-8')))
        self.assertEqual(violations,[])

    def test_source_has_no_other_service_mentions(self):
        self.assertEqual([path.name for path in SOURCE.rglob('*.py') if FORBIDDEN in path.read_text(encoding='utf-8').casefold()],[])

    def test_dynamic_importlib_and_alias_are_detected(self):
        for text in ("import importlib; importlib.import_module('tg_triage.classifier')",
                     "from importlib import import_module as load; load('tg_triage')",
                     "__import__('tg_triage')"):
            self.assertTrue(forbidden_imports(text))
        self.assertFalse(forbidden_imports("import importlib; importlib.import_module('nailhub.model')"))
