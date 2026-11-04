import tempfile
import unittest
from pathlib import Path
from source_mirror.lock import worker_lock

class LockTests(unittest.TestCase):
    def test_same_thread_nested_lock_is_reentrant_and_released(self):
        with tempfile.TemporaryDirectory() as root:
            with worker_lock(root):
                with worker_lock(root):pass
            with worker_lock(root):pass
