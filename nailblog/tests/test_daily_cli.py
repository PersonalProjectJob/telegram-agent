"""`python -m nailblog trends` and `daily --at 10|13|20 [--dry-run] [--date]`: output, scratch dry-runs, idempotence."""
import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from nailblog import __main__ as cli
from nailblog.__main__ import Runtime
from nailblog.state import State
from tests.daily_support import BLOGS_URL, FEED_1, FEED_2, SOURCES, SLOT_06, WEEK, Model, at
from tests.fakes import ATOM, BLOGS_JSON, RSS, TRENDING_RSS, TRENDS_URL, FakeOpener
from tests.helpers import load, write_config


class CliBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.config_path = write_config(self.root, sources=SOURCES)
        self.config = load(self.root, sources=SOURCES)
        self.opener = FakeOpener({FEED_1: RSS, FEED_2: ATOM, BLOGS_URL: BLOGS_JSON, TRENDS_URL: TRENDING_RSS})
        self.model = Model()

    def run_cli(self, *argv, **runtime):
        old = os.umask(0o022)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(list(argv) + ["--config", str(self.config_path)],
                                runtime=Runtime(transport=self.model.transport, opener=self.opener, **runtime))
            return code, out.getvalue()
        finally:
            os.umask(old)

    def real_state(self):
        return State(self.config.db_path)

    def untouched(self):
        """The real state directory holds the database (and its WAL files) and nothing else."""
        state = self.real_state()
        self.assertEqual((state.list_topics(("proposed", "approved", "dropped")), state.list_posts(),
                          state.trend_snapshots(0), state.tiktok_tags(), state.daily_rows("2026-10-16")),
                         ([], [], [], [], []))
        names = {name for name in os.listdir(self.root) if not name.startswith("nailblog.sqlite3")}
        self.assertEqual(names - {"nailblog.toml"}, set())


class TrendsCommandTests(CliBase):
    def test_dry_run_prints_the_score_table_and_stores_nothing(self):
        code, output = self.run_cli("trends", "--dry-run", now=at("12:00"))
        self.assertEqual(code, 0, output)
        lines = output.splitlines()
        self.assertEqual(lines[0], "Google Trending US: 5 mục lấy được (--dry-run: không lưu snapshot)")
        self.assertIn("Bảng điểm (hot 0.2 · đà 3 ngày 0.4 · đà 7 ngày 0.3 · TikTok 0.1), 2 dòng:", lines[1])
        table = "\n".join(lines[2:])
        for term in ("chrome nails | 20K+ |", "taylor swift | 2M+ |"):
            self.assertIn(term, table)
        for hidden in ("nfl scores", "halloween costumes", "sephora sale"):
            self.assertNotIn(hidden, output)
        self.assertEqual([call["url"] for call in self.opener.calls], [TRENDS_URL])
        self.untouched()

    def test_without_dry_run_the_snapshots_are_saved(self):
        code, output = self.run_cli("trends", now=at("12:00"))
        self.assertEqual(code, 0, output)
        self.assertIn("(đã lưu snapshot)", output)
        self.assertEqual(len(self.real_state().trend_snapshots(0)), 5)

    def test_a_failing_feed_is_printed_and_exits_zero(self):
        self.opener.routes[TRENDS_URL] = TimeoutError()
        code, output = self.run_cli("trends", "--dry-run", now=at("12:00"))
        self.assertEqual(code, 0)
        self.assertIn("Google Trending lỗi: timeout", output)
        self.assertIn("0 mục lấy được", output)

    def test_tiktok_tags_typed_in_count_in_the_table(self):
        self.real_state().add_tiktok_tag(at("11:00"), "chromenails", 12_000_000)
        _, output = self.run_cli("trends", "--dry-run", now=at("12:00"))
        self.assertIn("tt 0.88", output)


class DailyCommandTests(CliBase):
    def test_dry_run_at_10_prints_the_dm_and_leaves_the_real_state_untouched(self):
        code, output = self.run_cli("daily", "--at", "10", "--dry-run", "--date", "2026-10-16")
        self.assertEqual(code, 0, output)
        self.assertIn("== daily propose 2026-10-16", output)
        self.assertIn("/nailblog pick 06 1", output)
        self.assertIn("(DM chưa gửi: chế độ --dry-run)", output)
        self.assertEqual(self.model.planner_calls, 1)
        self.assertTrue(all(call["method"] == "GET" for call in self.opener.calls))
        self.untouched()
        self.assertFalse((self.root / "blog-cache.json").exists())

    def test_the_real_run_stores_candidates_records_the_step_and_a_repeat_changes_nothing(self):
        code, output = self.run_cli("daily", "--at", "10", "--date", "2026-10-16")
        self.assertEqual(code, 0, output)
        self.assertIn("CLI không có phiên Telegram; service sẽ gửi khi chạy trong ngày này", output)
        state = self.real_state()
        self.assertEqual(len(state.topics_for(WEEK)), 4)
        self.assertIn("/nailblog pick 06 1", state.daily_get("2026-10-16", "propose")["message"])
        code, again = self.run_cli("daily", "--at", "10", "--date", "2026-10-16")
        self.assertEqual(code, 0)
        self.assertIn("đã chạy trước đó, không chạy lại", again)
        self.assertEqual((self.model.planner_calls, len(self.real_state().topics_for(WEEK))), (1, 4))

    def test_at_13_decides_then_writes_and_at_20_previews(self):
        self.run_cli("daily", "--at", "10", "--date", "2026-10-16")
        State(self.config.db_path)
        from nailblog.service import Service
        service = Service(self.config, clock=lambda: at("12:00"))
        service.command("pick", ("06:00", 1))
        code, output = self.run_cli("daily", "--at", "13", "--date", "2026-10-16")
        self.assertEqual(code, 0, output)
        self.assertIn("== daily decide 2026-10-16", output)
        self.assertIn("== daily write 2026-10-16", output)
        self.assertIn(f"slot {SLOT_06} [review] gate pass", output)
        self.assertTrue((self.root / "previews" / "2026-10-17" / "0600" / "article.html").is_file())
        self.assertTrue((self.root / "previews" / "2026-10-17" / "1900" / "article.json").is_file())
        code, preview = self.run_cli("daily", "--at", "20", "--date", "2026-10-16")
        self.assertEqual(code, 0, preview)
        self.assertIn("xem trước bài ngày mai 17/10", preview)
        self.assertIn("/nailblog approve", preview)
        self.assertIn("không có link website", preview)

    def test_dry_run_at_13_writes_into_a_scratch_copy_only(self):
        self.run_cli("daily", "--at", "10", "--date", "2026-10-16")
        before = sorted(os.listdir(self.root))
        code, output = self.run_cli("daily", "--at", "13", "--dry-run", "--date", "2026-10-16")
        self.assertEqual(code, 0, output)
        self.assertIn("bài #1", output)
        state = self.real_state()
        self.assertEqual((state.list_posts(), state.daily_get("2026-10-16", "decide")), ([], None))
        self.assertEqual(sorted(os.listdir(self.root)), before)
        self.assertFalse((self.root / "previews").exists())

    def test_without_date_the_service_clock_is_used(self):
        code, output = self.run_cli("daily", "--at", "10", "--dry-run", now=at("10:30"))
        self.assertEqual(code, 0, output)
        self.assertIn("== daily propose 2026-10-16", output)

    def test_argument_combinations_are_checked(self):
        base = ("--config", str(self.config_path))
        for argv in (("daily",) + base, ("daily", "--at", "11") + base, ("daily", "--at", "x") + base,
                     ("daily", "--at", "10", "--date", "16/10/2026") + base,
                     ("status", "--at", "10") + base, ("plan-week", "--date", "2026-10-16") + base,
                     ("trends", "--at", "10") + base, ("trends", "--date", "2026-10-16") + base,
                     ("status", "--dry-run") + base, ("run", "--dry-run") + base):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cli.main(list(argv))
            self.assertEqual(raised.exception.code, 2)

    def test_a_model_failure_exits_one_with_a_short_code_and_leaves_no_candidates(self):
        def explode(call, number):
            raise OSError("model down")
        self.model.transport.respond = explode
        code, output = self.run_cli("daily", "--at", "10", "--date", "2026-10-16")
        self.assertEqual(code, 1)
        self.assertTrue(output.strip().endswith("NailBlog operation failed: model call failed: invalid_json"))
        self.assertEqual(self.real_state().topics_for(WEEK), [])


if __name__ == "__main__":
    unittest.main()
