import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nailblog.state import State, startup_binding, startup_gate


class StartupGateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.package = self.root / "pkg"
        self.package.mkdir()
        (self.package / "a.py").write_text("X = 1\n", encoding="utf-8")
        self.config_file = self.root / "nailblog.toml"
        self.config_file.write_text("[service]\n", encoding="utf-8")
        self.config = SimpleNamespace(mode="preview")
        self.state = State(self.root / "state" / "nailblog.sqlite3")

    def gate(self):
        return startup_gate(self.config, state=self.state, config_path=self.config_file, package_dir=self.package)

    def arm_current(self):
        binding, _ = startup_binding(self.config, config_path=self.config_file, package_dir=self.package)
        self.state.arm(binding)

    def test_migration_of_old_database_keeps_rows(self):
        path = self.root / "old" / "nailblog.sqlite3"
        path.parent.mkdir()
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE control(id INTEGER PRIMARY KEY CHECK(id=1), paused INTEGER NOT NULL,"
                   " image_mode TEXT NOT NULL, last_result TEXT NOT NULL)")
        db.execute("INSERT INTO control VALUES(1,1,'random','kept')")
        db.commit()
        db.close()
        state = State(path)
        control = state.control()
        self.assertEqual((control["paused"], control["image_mode"], control["last_result"]), (1, "random", "kept"))
        self.assertEqual(control["armed_binding"], "")
        State(path)  # second open is a no-op

    def test_arm_disarm_round_trip(self):
        self.assertEqual(self.state.armed_binding(), "")
        self.state.arm("abc")
        self.assertEqual(self.state.armed_binding(), "abc")
        self.state.disarm()
        self.assertEqual(self.state.armed_binding(), "")

    def test_unarmed_start_pauses_and_stays_disarmed(self):
        self.state.set_paused(False)
        result = self.gate()
        self.assertEqual(result["decision"], "paused_disarmed")
        self.assertTrue(self.state.control()["paused"])
        self.assertEqual(self.state.armed_binding(), "")

    def test_new_build_pauses_and_disarms(self):
        self.arm_current()
        self.state.set_paused(False)
        (self.package / "a.py").write_text("X = 2\n", encoding="utf-8")
        self.assertEqual(self.gate()["decision"], "paused_disarmed")
        self.assertTrue(self.state.control()["paused"])
        self.assertEqual(self.state.armed_binding(), "")

    def test_new_config_or_mode_pauses_and_disarms(self):
        for change in ("file", "mode"):
            with self.subTest(change=change):
                self.arm_current()
                self.state.set_paused(False)
                if change == "file":
                    self.config_file.write_text("[service]\n# changed\n", encoding="utf-8")
                else:
                    self.config = SimpleNamespace(mode="live")
                self.assertEqual(self.gate()["decision"], "paused_disarmed")
                self.assertTrue(self.state.control()["paused"])
                self.assertEqual(self.state.armed_binding(), "")

    def test_crash_restart_leaves_paused_flag_untouched(self):
        for paused in (False, True):
            with self.subTest(paused=paused):
                self.arm_current()
                self.state.set_paused(paused)
                result = self.gate()
                self.assertEqual(result["decision"], "unchanged")
                self.assertEqual(bool(self.state.control()["paused"]), paused)
                self.assertTrue(self.state.armed_binding())

    def test_build_json_is_preferred_over_package_digest(self):
        (self.package / "build.json").write_text("{}", encoding="utf-8")
        _, kind = startup_binding(self.config, config_path=self.config_file, package_dir=self.package)
        self.assertEqual(kind, "build.json")

    def test_missing_config_file_fails_closed(self):
        self.arm_current()
        self.config_file.unlink()
        self.assertEqual(self.gate()["decision"], "paused_disarmed")


if __name__ == "__main__":
    unittest.main()
