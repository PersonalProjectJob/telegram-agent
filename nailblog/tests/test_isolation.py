"""Static import boundary: NailBlog never imports the other services, including dynamically."""
import ast
import unittest
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src" / "nailblog"
FORBIDDEN = ("nailhub", "tg_triage")


def forbidden_imports(text):
    tree = ast.parse(text)
    dynamic = {"__import__", "import_module"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "importlib":
            dynamic.update(alias.asname or alias.name for alias in node.names if alias.name == "import_module")
    violations = []
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
            if node.level:
                names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Call) and ((isinstance(node.func, ast.Name) and node.func.id in dynamic)
                                             or (isinstance(node.func, ast.Attribute) and node.func.attr == "import_module")):
            names = [arg.value for arg in node.args[:1] if isinstance(arg, ast.Constant) and isinstance(arg.value, str)]
        if any(name.split(".")[0] in FORBIDDEN for name in names):
            violations.append(node.lineno)
    return violations


class IsolationTests(unittest.TestCase):
    def test_source_does_not_import_other_services(self):
        files = list(SOURCE.rglob("*.py"))
        self.assertGreater(len(files), 10)
        violations = [(path.name, line) for path in files for line in forbidden_imports(path.read_text(encoding="utf-8"))]
        self.assertEqual(violations, [])

    def test_detector_finds_static_and_dynamic_imports(self):
        for text in ("import nailhub.model", "from nailhub import state", "import tg_triage",
                     "import importlib; importlib.import_module('nailhub.config')",
                     "from importlib import import_module as load; load('tg_triage')", "__import__('nailhub')"):
            self.assertTrue(forbidden_imports(text), text)
        self.assertFalse(forbidden_imports("import importlib; importlib.import_module('nailblog.config')"))
        self.assertFalse(forbidden_imports("from nailblog import state"))


if __name__ == "__main__":
    unittest.main()
