"""model_lock.py must stay byte-identical with the nailhub copy (ai_transport.py was removed with S8)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ParityTests(unittest.TestCase):
    def test_copies_are_byte_identical_to_nailhub(self):
        for name in ("model_lock.py",):
            with self.subTest(module=name):
                own = ROOT / "nailblog" / "src" / "nailblog" / name
                reference = ROOT / "nailhub" / "src" / "nailhub" / name
                self.assertTrue(own.is_file() and reference.is_file())
                self.assertGreater(len(reference.read_bytes()), 1000)
                self.assertEqual(own.read_bytes(), reference.read_bytes())

    def test_a_single_byte_difference_would_be_detected(self):
        reference = (ROOT / "nailhub" / "src" / "nailhub" / "model_lock.py").read_bytes()
        self.assertNotEqual(reference + b"\n", reference)
        self.assertNotEqual(reference.replace(b"ollama.lock", b"ollama.lock2", 1), reference)


if __name__ == "__main__":
    unittest.main()
