"""Findings of the step B2 verification (F1-F11): each rule with the cases on both sides of its limit."""
import contextlib
import io
import os
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

from nailblog import __main__ as cli
from nailblog import daily, state as state_module, trends
from nailblog.__main__ import Runtime
from nailblog.commands import execute
from nailblog.config import TrendsConfig
from nailblog.schedule import VN_TZ
from nailblog.sources import SourceError
from nailblog.state import SlotTaken, State
from nailblog.trends import TrendBoard, fetch_trending
from tests.daily_support import BLOGS_URL, FEED_1, FEED_2, SLOT_06, SLOT_19, SOURCES, TARGET, TODAY, WEEK, DailyBase, Model, at, meta
from tests.fakes import ATOM, BLOGS_JSON, RSS, TRENDING_RSS, TRENDS_URL, FakeOpener
from tests.helpers import load, settings, write_config

NOW = at("12:00")


class StateCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "s.sqlite3")
        self.config = settings(self.directory.name)

    def topic(self, slot, state="proposed", week="2026-W43", title="Chủ đề"):
        return self.state.add_topic(week, title, "hot-trend", slot, 5, state=state)

    def states(self, *ids):
        return [self.state.get_topic(i)["state"] for i in ids]

    def reply(self, name, args):
        return execute(self.state, name, args, now=NOW, config=self.config)[0]


class OneApprovedTopicPerSlotTests(StateCase):
    """F1: the rule lives in State (one function inside the transaction), every road to `approved` goes through it."""

    def test_topics_ok_with_two_topics_of_one_slot_approves_the_first_and_refuses_the_second(self):
        first, second = self.topic(SLOT_06), self.topic(SLOT_06)
        reply = self.reply("topics_ok", (first, second))
        self.assertIn(f"Đã duyệt chủ đề: {first}.", reply)
        self.assertIn(f"#{second} (slot đã có chủ đề đã duyệt #{first})", reply)
        self.assertIn("Mỗi slot chỉ duyệt một chủ đề", reply)
        self.assertEqual(self.states(first, second), ["approved", "proposed"])

    def test_topics_ok_on_a_second_slot_topic_in_a_later_command_is_refused_too(self):
        first, second = self.topic(SLOT_06), self.topic(SLOT_06)
        self.reply("topics_ok", (first,))
        reply = self.reply("topics_ok", (second,))
        self.assertIn("Đã duyệt chủ đề: không có.", reply)
        self.assertIn("slot đã có chủ đề đã duyệt", reply)
        self.assertEqual(self.states(first, second), ["approved", "proposed"])

    def test_different_slots_and_topics_without_a_slot_are_not_limited(self):
        a, b, c, d = self.topic(SLOT_06), self.topic(SLOT_19), self.topic(None), self.topic(None)
        self.assertIn(f"Đã duyệt chủ đề: {a}, {b}, {c}, {d}.", self.reply("topics_ok", (a, b, c, d)))

    def test_state_refuses_on_its_own_with_the_holder_named(self):
        first, second = self.topic(SLOT_06), self.topic(SLOT_06)
        self.assertTrue(self.state.set_topic_state(first, "approved"))
        with self.assertRaises(SlotTaken) as raised:
            self.state.set_topic_state(second, "approved")
        self.assertEqual((raised.exception.holder_id, raised.exception.slot_at), (first, SLOT_06))
        self.assertEqual(self.states(first, second), ["approved", "proposed"])
        self.assertTrue(self.state.set_topic_state(first, "approved"))          # approving it again is not a conflict
        self.assertFalse(self.state.set_topic_state(first, "approved", only_from=("proposed",)))

    def test_dropping_the_holder_frees_the_slot(self):
        first, second = self.topic(SLOT_06), self.topic(SLOT_06)
        self.state.set_topic_state(first, "approved")
        self.state.set_topic_state(first, "dropped")
        self.assertTrue(self.state.set_topic_state(second, "approved"))
        self.assertEqual(self.state.slot_holder(SLOT_06), second)
        self.assertIsNone(self.state.slot_holder(SLOT_06, excluding=second))
        self.assertIsNone(self.state.slot_holder(SLOT_19))

    def test_a_planned_topic_approved_for_a_slot_blocks_pick_of_that_slot(self):
        planned = self.topic(SLOT_06, week="2026-W43")
        self.reply("topics_ok", (planned,))
        week = "daily:2026-10-17"
        candidates = [self.topic(SLOT_06, week=week, title=f"Ứng viên {n}") for n in (1, 2)]
        reply = self.reply("pick", ("06:00", 1))
        self.assertIn(f"đã có chủ đề đã duyệt #{planned}", reply)
        self.assertIn(f"/nailblog topics drop {planned}", reply)
        self.assertEqual(self.states(*candidates), ["proposed", "proposed"])
        self.assertEqual(self.states(planned), ["approved"])

    def test_a_pick_blocks_topics_ok_of_a_planned_topic_for_the_same_slot(self):
        week = "daily:2026-10-17"
        candidates = [self.topic(SLOT_06, week=week, title=f"Ứng viên {n}") for n in (1, 2)]
        self.assertIn("Đã chọn slot 06", self.reply("pick", ("06:00", 2)))
        planned = self.topic(SLOT_06, week="2026-W43")
        reply = self.reply("topics_ok", (planned,))
        self.assertIn(f"#{planned} (slot đã có chủ đề đã duyệt #{candidates[1]})", reply)
        self.assertEqual(self.states(planned, *candidates), ["proposed", "dropped", "approved"])

    def test_choosing_again_still_swaps_inside_the_candidates(self):
        week = "daily:2026-10-17"
        candidates = [self.topic(SLOT_06, week=week, title=f"Ứng viên {n}") for n in (1, 2)]
        self.reply("pick", ("06:00", 1))
        self.assertIn("Đã chọn", self.reply("pick", ("06:00", 2)))
        self.assertEqual(self.states(*candidates), ["dropped", "approved"])
        self.assertIn("Đã chọn", self.reply("pick", ("06:00", 1)))
        self.assertEqual(self.states(*candidates), ["approved", "dropped"])

    def test_add_topic_and_moving_an_approved_topic_obey_the_same_rule(self):
        first = self.topic(SLOT_06, state="approved")
        with self.assertRaises(SlotTaken):
            self.topic(SLOT_06, state="approved")
        self.assertEqual(len(self.state.list_topics(("approved",))), 1)
        free = self.topic(None, state="approved")
        with self.assertRaises(SlotTaken):
            self.state.set_topic_slot(free, SLOT_06)
        self.assertIsNone(self.state.get_topic(free)["slot_at"])
        proposed = self.topic(None)
        self.assertTrue(self.state.set_topic_slot(proposed, SLOT_06))        # a proposed topic may share a slot
        self.assertTrue(self.state.set_topic_slot(first, SLOT_06))            # and moving onto its own slot is fine
        self.assertEqual(self.state.slot_holder(SLOT_06), first)

    def test_slot_taken_is_a_state_error(self):
        self.assertTrue(issubclass(SlotTaken, state_module.StateError))


class DecideNeverDoublesASlotTests(DailyBase):
    async def decide(self):
        self.now = at("13:05")
        return await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)

    async def test_a_backlog_topic_is_not_moved_onto_a_slot_another_approved_topic_holds(self):
        holder = self.state.add_topic("2026-W43", "Đã giữ slot", "nail-salon", SLOT_19, 5, state="approved")
        self.state.add_post(SLOT_19, topic_id=holder, state="held")           # used, so never a backlog candidate itself
        free = self.state.add_topic("2026-W43", "Backlog tự do", "nail-salon", None, 9, state="approved")
        self.assertIsNone(daily._backlog(self.state, TARGET, "19:00"))
        outcome = await self.decide()
        self.assertEqual(self.state.slot_holder(SLOT_19), holder)
        self.assertEqual(len([r for r in self.state.list_topics(("approved",)) if r["slot_at"] == SLOT_19]), 1)
        self.assertIsNone(self.state.get_topic(free)["slot_at"])
        self.assertIn("19:00 — chưa có chủ đề", outcome.text)

    async def test_the_backlog_still_works_when_nothing_holds_the_slot(self):
        free = self.state.add_topic("2026-W43", "Backlog tự do", "nail-salon", None, 9, state="approved")
        self.assertEqual(daily._backlog(self.state, TARGET, "19:00")["id"], free)


class StaleBacklogTests(DailyBase):
    """F2: a backlog topic whose own slot is more than 14 days before the day needed is not used."""

    def backlog(self, days_before, hhmm="19:00", slot=None):
        slot_at = daily.slot_text(TARGET - timedelta(days=days_before), hhmm) if slot is None else slot
        self.state.add_topic("2026-W40", "Backlog cũ", "nail-salon", slot_at, 9, state="approved")
        return daily._backlog(self.state, TARGET, hhmm)

    def test_fourteen_days_old_is_still_fresh_fifteen_is_stale(self):
        for days, fresh in ((0, True), (1, True), (13, True), (14, True), (15, False), (30, False)):
            with self.subTest(days=days):
                self.setUp()
                self.assertEqual(self.backlog(days) is not None, fresh)

    def test_a_topic_without_a_slot_or_planned_ahead_is_fresh(self):
        self.assertIsNotNone(self.backlog(0))
        self.setUp()
        self.state.add_topic("2026-W44", "Backlog xa", "nail-salon", daily.slot_text(TARGET + timedelta(days=9), "19:00"),
                             9, state="approved")
        self.assertIsNotNone(daily._backlog(self.state, TARGET, "19:00"))
        self.setUp()
        self.state.add_topic("2026-W43", "Chưa xếp", "nail-salon", None, 9, state="approved")
        self.assertIsNotNone(daily._backlog(self.state, TARGET, "19:00"))

    async def test_decide_skips_a_stale_topic_and_takes_the_best_candidate(self):
        await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)
        stale = self.state.add_topic("2026-W40", "Backlog cũ", "nail-salon", daily.slot_text(TARGET - timedelta(days=15), "19:00"),
                                     99, state="approved")
        self.now = at("13:05")
        await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)
        self.assertIsNone(daily.meta_of(self.state.get_topic(stale)).get("chosen"))
        self.assertEqual(self.state.get_topic(stale)["slot_at"], daily.slot_text(TARGET - timedelta(days=15), "19:00"))
        self.assertTrue(meta(daily.chosen_topic(self.state, TARGET, "19:00"))["chosen"]["auto"])


class Locker:
    """Make State's sqlite connections run `hook` right after a statement that contains `marker` has been executed, and
    let the hook try a write from a second connection the way a concurrent process would."""

    def __init__(self, path, marker):
        self.path, self.marker, self.outcome = path, marker, None
        self.real = sqlite3.connect

    def hook(self):
        other = self.real(self.path, timeout=0.05)
        try:
            other.execute("UPDATE topics SET title='intruder' WHERE id=1")
            other.commit()
            self.outcome = "wrote"
        except sqlite3.OperationalError as error:
            self.outcome = "blocked" if "locked" in str(error) else str(error)
        finally:
            other.close()

    def __enter__(self):
        locker = self

        class Hooked(sqlite3.Connection):
            def execute(self, sql, *args):
                result = super().execute(sql, *args)
                if locker.marker in sql and locker.outcome is None:
                    locker.hook()
                return result
        self.patch = patch.object(state_module.sqlite3, "connect",
                                  lambda path, timeout=10, **kw: self.real(path, timeout=timeout, factory=Hooked))
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()


class TransactionTests(StateCase):
    """F3: the readers-then-writers of one change hold the write lock from their first statement (BEGIN IMMEDIATE)."""

    def test_choose_candidate_holds_the_write_lock_while_it_reads_and_updates(self):
        week = "daily:2026-10-17"
        self.topic(SLOT_06, week=week)
        self.topic(SLOT_06, week=week)
        with Locker(self.state.path, "SELECT id FROM topics WHERE week=?") as locker:
            self.assertEqual(self.state.choose_candidate(week, SLOT_06, 2), 2)
        self.assertEqual(locker.outcome, "blocked")                # a competing write could not slip in
        self.assertEqual(self.state.get_topic(1)["title"], "Chủ đề")
        self.assertEqual(self.states(1, 2), ["dropped", "approved"])

    def test_approving_a_topic_holds_the_lock_between_the_slot_check_and_the_update(self):
        first = self.topic(SLOT_06)
        with Locker(self.state.path, "SELECT state, slot_at FROM topics WHERE id=?") as locker:
            self.assertTrue(self.state.set_topic_state(first, "approved"))
        self.assertEqual(locker.outcome, "blocked")

    def test_moving_a_topic_and_adding_one_take_the_lock_first_too(self):
        first = self.topic(None, state="approved")
        with Locker(self.state.path, "SELECT state FROM topics WHERE id=?") as locker:
            self.assertTrue(self.state.set_topic_slot(first, SLOT_06))
        self.assertEqual(locker.outcome, "blocked")

    def test_the_helper_does_detect_a_missing_lock(self):
        """Control: without BEGIN IMMEDIATE the intruder's write succeeds, so the checks above can fail."""
        self.topic(SLOT_06)
        with Locker(self.state.path, "SELECT id FROM topics WHERE week=?") as locker:
            with self.state.connection() as db:
                db.execute("SELECT id FROM topics WHERE week=? AND slot_at=?", ("2026-W43", SLOT_06)).fetchall()
        self.assertEqual(locker.outcome, "wrote")


class DueBoundaryTests(StateCase):
    """F3: exactly 13:00:00 is outside propose (<) and inside decide (>=)."""

    def due(self, step, local, day=TODAY):
        return daily._due(self.state, self.config, step, day, local)

    def test_propose_stops_and_decide_starts_at_the_deadline_second(self):
        for local, propose, decide in ((time(12, 59, 59), True, False), (time(13, 0, 0), False, True),
                                       (time(13, 0, 1), False, True)):
            with self.subTest(local=local):
                self.assertEqual((self.due("propose", local), self.due("decide", local)), (propose, decide))

    def test_propose_starts_at_ten_sharp(self):
        self.assertEqual((self.due("propose", time(9, 59, 59)), self.due("propose", time(10, 0, 0))), (False, True))

    def test_preview_starts_at_its_minute(self):
        self.assertEqual((self.due("preview", time(19, 59, 59)), self.due("preview", time(20, 0, 0))), (False, True))


class RunDueBoundaryTests(DailyBase):
    @staticmethod
    def moment(h, m, s):
        return datetime.combine(TODAY, time(h, m, s), tzinfo=VN_TZ).timestamp()

    async def test_at_12_59_59_only_propose_runs(self):
        self.now = self.moment(12, 59, 59)
        await daily.run_due(self.service)
        self.assertIsNotNone(self.state.daily_get("2026-10-16", "propose"))
        self.assertIsNone(self.state.daily_get("2026-10-16", "decide"))

    async def test_at_13_00_00_decide_runs_and_propose_does_not(self):
        self.now = self.moment(13, 0, 0)
        await daily.run_due(self.service)
        self.assertIsNone(self.state.daily_get("2026-10-16", "propose"))
        self.assertIsNotNone(self.state.daily_get("2026-10-16", "decide"))
        self.assertEqual(self.model.planner_calls, 0)


class TrendWindowTests(StateCase):
    """F3: the d3 'previous' window is exactly the days 3..9 before today."""

    def board(self, *days_back):
        now = at("12:00")
        for back in days_back:
            stamp = at("12:00", TODAY - timedelta(days=back))
            self.state.add_trend_snapshots(stamp, "rss:example.com", [(f"chrome nails story {back}", 0, [])])
        return TrendBoard(self.state, TrendsConfig(), now)

    def test_days_3_to_9_are_in_the_previous_window_2_and_10_are_not(self):
        for back, inside in ((2, False), (3, True), (6, True), (9, True), (10, False)):
            with self.subTest(days_back=back):
                self.setUp()
                frequency = self.board(back).frequency("chrome nails", 3, 9)
                self.assertEqual(frequency, 1 / 7 if inside else 0)

    def test_the_recent_window_is_exactly_today_and_the_two_days_before(self):
        for back, inside in ((0, True), (2, True), (3, False)):
            with self.subTest(days_back=back):
                self.setUp()
                self.assertEqual(self.board(back).frequency("chrome nails", 0, 2), 1 / 3 if inside else 0)

    def test_d3_changes_when_a_mention_moves_from_day_9_to_day_10(self):
        self.assertEqual(self.board(1, 9).score("chrome nails").d3, 0.7778)      # (1/3) / (1/7) = 2.33 -> 0.78
        self.setUp()
        self.assertEqual(self.board(1, 10).score("chrome nails").d3, 0.4444)     # empty previous window: (1/3 + 1) / 3
        self.setUp()
        self.assertEqual(self.board(1, 2).score("chrome nails").d3, 0.5556)     # day 2 is recent: previous stays empty
        self.setUp()
        self.assertEqual(self.board(1, 3).score("chrome nails").d3, 0.7778)     # day 3 is the first previous day


class PinnedTrendsUrlTests(unittest.TestCase):
    """F7: host, path and the geo query are all pinned."""

    def test_only_the_exact_google_us_feed_is_fetched(self):
        opener = FakeOpener({TRENDS_URL: TRENDING_RSS})
        self.assertEqual(len(fetch_trending(TRENDS_URL, opener=opener)), 5)
        self.assertEqual([call["url"] for call in opener.calls], [TRENDS_URL])
        for url in ("https://trends.google.com/trending/rss?geo=GB", "https://trends.google.com/trending/rss",
                    "https://trends.google.com/trending/rss?geo=US&hl=vi", "https://trends.google.com/trending?geo=US",
                    "https://trends.google.com/other/rss?geo=US", "https://trends.google.com/trending/rss/?geo=US",
                    "https://trends.google.com/trending/rss?geo=us", "http://trends.google.com/trending/rss?geo=US",
                    "https://trends.google.com.evil.example/trending/rss?geo=US", "https://evil.example/trending/rss?geo=US",
                    "", "trends.google.com/trending/rss?geo=US"):
            with self.subTest(url=url):
                opener = FakeOpener({url: TRENDING_RSS})
                with self.assertRaises(SourceError) as raised:
                    fetch_trending(url, opener=opener)
                self.assertEqual(str(raised.exception), "origin_not_pinned")
                self.assertEqual(opener.calls, [])


class PastDateDoesNotFetchTrendsTests(DailyBase):
    """F9 (service) and F10 (recent poll)."""

    def trend_calls(self):
        return [call for call in self.opener.calls if call["url"] == TRENDS_URL]

    async def propose(self):
        return await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)

    async def test_without_the_flag_propose_fetches_the_trends(self):
        outcome = await self.propose()
        self.assertEqual(len(self.trend_calls()), 1)
        self.assertNotIn("quá khứ", outcome.text)

    async def test_with_refresh_off_propose_does_not_fetch_and_says_so(self):
        self.service.refresh_trends = False
        outcome = await self.propose()
        self.assertEqual(self.trend_calls(), [])
        self.assertIn("Ghi chú: ngày chạy là quá khứ (--date): không lấy trend thật, dùng snapshot đã lưu", outcome.text)

    async def test_a_poll_younger_than_ten_minutes_is_reused(self):
        self.state.add_trend_snapshots(self.now - 599, trends.GOOGLE_SOURCE, [("chrome nails", 20000, [])])
        outcome = await self.propose()
        self.assertEqual(self.trend_calls(), [])
        self.assertIn("dùng trend đã lấy cách đây 9 phút", outcome.text)

    async def test_a_poll_ten_minutes_old_or_older_is_fetched_again(self):
        for age in (600, 601, 3600):
            with self.subTest(age=age):
                self.setUp()
                self.state.add_trend_snapshots(self.now - age, trends.GOOGLE_SOURCE, [("chrome nails", 20000, [])])
                outcome = await self.propose()
                self.assertEqual(len(self.trend_calls()), 1)
                self.assertNotIn("dùng trend đã lấy", outcome.text)

    async def test_only_google_snapshots_count_as_a_poll(self):
        self.state.add_trend_snapshots(self.now - 60, "rss:www.bustle.com", [("chrome nails story", 0, [])])
        await self.propose()
        self.assertEqual(len(self.trend_calls()), 1)


class DmParenthesesTests(DailyBase):
    """F11: no parenthesis inside a parenthesis in the 13:00 DM."""

    @staticmethod
    def depth(text):
        deepest = level = 0
        for char in text:
            level += (char == "(") - (char == ")")
            deepest = max(deepest, level)
        return deepest, level

    async def test_pick_auto_and_backlog_lines_have_balanced_flat_parentheses(self):
        await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)
        self.service.command("pick", ("06:00", 1))
        self.now = at("13:05")
        outcome = await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)
        self.assertIn("tự chọn: auto, điểm cao nhất, vẫn phải duyệt bài", outcome.text)
        for line in outcome.text.splitlines():
            with self.subTest(line=line):
                self.assertEqual(self.depth(line), (1, 0) if "(" in line else (0, 0))

    async def test_a_backlog_line_too(self):
        await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)
        self.state.add_topic("2026-W43", "Backlog quản lý tiệm", "nail-salon", SLOT_19, 20, state="approved")
        self.now = at("13:05")
        outcome = await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)
        self.assertIn("Backlog quản lý tiệm (lấy từ backlog tuần)", outcome.text)
        self.assertTrue(all(self.depth(line)[0] <= 1 for line in outcome.text.splitlines()))


class DryRunCase(unittest.TestCase):
    """F5/F6: a --dry-run creates and writes nothing in the real state directory, even when it does not exist yet."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state_dir = self.root / "var" / "nailblog"
        db = (self.state_dir / "nailblog.sqlite3").as_posix()
        self.config_path = write_config(self.root, service={"db_path": db}, sources=SOURCES)
        self.opener = FakeOpener({FEED_1: RSS, FEED_2: ATOM, BLOGS_URL: BLOGS_JSON, TRENDS_URL: TRENDING_RSS})
        self.model = Model()

    def run_cli(self, *argv, **runtime):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.main(list(argv) + ["--config", str(self.config_path)],
                            runtime=Runtime(transport=self.model.transport, opener=self.opener, **runtime))
        return code, out.getvalue()

    def tree(self):
        found = {}
        for folder, _dirs, files in os.walk(self.root):
            found[os.path.relpath(folder, self.root)] = sorted(files)
        return found

    def commands(self):
        yield "trends", ("trends", "--dry-run"), {"now": at("12:00")}
        yield "daily-10", ("daily", "--at", "10", "--dry-run", "--date", "2026-10-16"), {"now": at("10:05")}
        yield "daily-13", ("daily", "--at", "13", "--dry-run", "--date", "2026-10-16"), {"now": at("13:05")}
        yield "daily-20", ("daily", "--at", "20", "--dry-run", "--date", "2026-10-16"), {"now": at("20:05")}
        yield "plan-week", ("plan-week", "--dry-run"), {"today": TODAY}


class DryRunWithoutStateTests(DryRunCase):
    def test_no_directory_no_database_no_cache_no_file_is_created_for_any_command(self):
        before = self.tree()
        for name, argv, runtime in self.commands():
            with self.subTest(command=name):
                if name == "plan-week":
                    self.model = Model()
                    self.model.transport.respond = self.plan_week_answer
                code, output = self.run_cli(*argv, **runtime)
                self.assertEqual(code, 0, output)
                self.assertFalse(self.state_dir.exists())
                self.assertFalse((self.root / "var").exists())
                self.assertEqual(self.tree(), before)

    def plan_week_answer(self, call, number):
        from tests.fakes import ollama_envelope
        categories = ["hot-trend", "nail-job", "nail-salon", "did-you-know"]
        topics = [{"title": f"Chủ đề {chr(97 + i)}{chr(100 + i)} số {i}", "category": categories[i % 4],
                   "product_promo": False, "source_ids": [],
                   "scores": {k: 3 for k in ("hot", "fit", "image", "source", "novelty")}} for i in range(16)]
        return ollama_envelope({"topics": topics})

    def test_the_commands_still_do_their_work_on_the_empty_scratch_state(self):
        code, output = self.run_cli("trends", "--dry-run", now=at("12:00"))
        self.assertIn("5 mục lấy được (--dry-run: không lưu snapshot)", output)
        code, output = self.run_cli("daily", "--at", "10", "--dry-run", "--date", "2026-10-16", now=at("10:05"))
        self.assertEqual(code, 0, output)
        self.assertIn("== daily propose 2026-10-16", output)
        self.assertIn("/nailblog pick 06 1", output)
        self.model.transport.respond = self.plan_week_answer
        code, output = self.run_cli("plan-week", "--dry-run", today=TODAY)
        self.assertEqual(code, 0, output)
        self.assertIn("NailBlog: 16 chủ đề tuần 19–25/10", output)
        self.assertIn("(DM chưa gửi: chế độ --dry-run; đã làm trên bản sao state, không lưu gì)", output)

    def test_without_dry_run_the_directory_is_created_as_before(self):
        code, _ = self.run_cli("trends", now=at("12:00"))
        self.assertEqual(code, 0)
        self.assertTrue((self.state_dir / "nailblog.sqlite3").is_file())


class DryRunWithStateTests(DryRunCase):
    def test_an_existing_state_directory_is_left_exactly_as_it_was(self):
        self.state_dir.mkdir(parents=True)
        state = State(self.state_dir / "nailblog.sqlite3")
        state.add_topic("2026-W43", "Đã có", "hot-trend", SLOT_06, 5)
        (self.state_dir / "blog-cache.json").write_text('{"posts": [], "items": []}', encoding="utf-8")
        db = self.state_dir / "nailblog.sqlite3"

        def fingerprint():
            return ({name: (path.stat().st_size, path.stat().st_mtime_ns) for name, path in
                     ((p.name, p) for p in self.state_dir.iterdir())}, db.read_bytes())
        before = fingerprint()
        names = self.tree()
        for name, argv, runtime in self.commands():
            with self.subTest(command=name):
                if name == "plan-week":
                    self.model.transport.respond = DryRunWithoutStateTests.plan_week_answer.__get__(self)
                code, output = self.run_cli(*argv, **runtime)
                self.assertEqual(code, 0, output)
                self.assertEqual(fingerprint(), before)
                self.assertEqual(self.tree(), names)
        self.assertEqual(len(State(db).list_topics()), 1)


class PastDateCliTests(DryRunCase):
    """F9: `daily --date <past>` does not read today's real trends."""

    def trend_urls(self):
        return [call["url"] for call in self.opener.calls if call["url"] == TRENDS_URL]

    def test_a_past_date_skips_the_trend_fetch_and_prints_the_note(self):
        code, output = self.run_cli("daily", "--at", "10", "--dry-run", "--date", "2026-10-14", now=at("10:05"))
        self.assertEqual(code, 0, output)
        self.assertEqual(self.trend_urls(), [])
        self.assertIn("không lấy trend thật", output)

    def test_today_and_a_future_date_still_fetch(self):
        for given in ("2026-10-16", "2026-10-17"):
            with self.subTest(date=given):
                self.opener.calls.clear()
                code, output = self.run_cli("daily", "--at", "10", "--dry-run", "--date", given, now=at("10:05"))
                self.assertEqual(code, 0, output)
                self.assertEqual(len(self.trend_urls()), 1)
                self.assertNotIn("không lấy trend thật", output)

    def test_without_date_the_clock_decides_and_trends_are_fetched(self):
        code, output = self.run_cli("daily", "--at", "10", "--dry-run", now=at("10:05"))
        self.assertEqual(len(self.trend_urls()), 1)

    def test_the_trends_command_has_no_date_and_always_fetches(self):
        self.run_cli("trends", "--dry-run", now=at("12:00"))
        self.assertEqual(len(self.trend_urls()), 1)


if __name__ == "__main__":
    unittest.main()
