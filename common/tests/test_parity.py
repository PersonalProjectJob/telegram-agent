"""Only the module docstring line may differ during the NailHub migration."""
import unittest
from pathlib import Path
from agentfleet_common import ai_transport, model_lock

ROOT = Path(__file__).resolve().parents[2]


class SourceParityTests(unittest.TestCase):
    def assert_source_parity(self, module):
        shared = Path(module.__file__).read_bytes().replace(b'\r\n', b'\n').splitlines(keepends=True)
        original = (ROOT / 'nailhub/src/nailhub' / Path(module.__file__).name).read_bytes().replace(b'\r\n', b'\n').splitlines(keepends=True)
        self.assertTrue(shared[0].startswith(b'"""'))
        self.assertIn(b'this package is the single source', shared[0].lower())
        self.assertEqual(shared[1:], original[1:])

    def test_ai_transport_source_parity(self):
        self.assert_source_parity(ai_transport)

    def test_model_lock_source_parity(self):
        self.assert_source_parity(model_lock)
