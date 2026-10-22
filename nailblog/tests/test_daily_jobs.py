"""The scheduler side of TD-8: which job is due at which Vietnam time, one pass at a time, restart safety,
failure isolation and backoff. Fake transport, opener and sender only."""
import asyncio
import unittest
from nailblog.writer import OUTLINE_ATTEMPTS
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock

from nailblog import daily
from nailblog.service import Service
from nailblog.trends import vn_date
from tests.daily_support import SLOT_06, TARGET, TODAY, TRENDS_URL, DailyBase, at


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp()


class DueTimeTests(DailyBase):
    async def run_at(self, hhmm, day=TODAY):
        self.now = at(hhmm, day)
        await daily.run_due(self.service)

    def done(self, day="2026-10-16"):
        return sorted(row["step"] for row in self.state.daily_rows(day))

    async def test_a_whole_day(self):
        await self.run_at("09:00")
        self.assertEqual(self.done("2026-10-16#03"), ["poll"])               # 09:00 is the 09-12 poll bucket
        self.assertEqual([call["url"] for call in self.opener.calls], [TRENDS_URL])
        self.assertEqual(self.done(), [])                                     # nothing daily before 10:00
        await self.run_at("09:30")
        self.assertEqual(len(self.opener.calls), 1)                           # same bucket: no second poll

        await self.run_at("10:05")
        self.assertEqual(self.done(), ["propose"])
        self.assertEqual((len(self.rows()), self.model.planner_calls, self.sent.await_count), (4, 1, 1))
        self.assertIn("/nailblog pick 06 1", self.sent.await_args.args[0])
        calls = len(self.opener.calls)
        await self.run_at("11:00")                                            # still bucket 09-12, nothing new
        self.assertEqual((len(self.opener.calls), self.sent.await_count, self.done()), (calls, 1, ["propose"]))

        self.now = at("12:00")
        self.assertIn("Đã chọn", self.service.command("pick", ("06:00", 2))[0])
        await self.run_at("12:01")
        self.assertEqual(self.done(), ["propose"])                            # 13:00 has not come

        await self.run_at("13:05")
        self.assertEqual(self.done(), ["decide", "propose", "write"])
        self.assertEqual((len(self.posts()), self.sent.await_count), (2, 2))
        self.assertIn("đã chốt chủ đề ngày mai 17/10", self.sent.await_args.args[0])
        await self.run_at("19:59")
        self.assertEqual((self.done(), self.sent.await_count), (["decide", "propose", "write"], 2))

        await self.run_at("20:00")
        self.assertEqual(self.done(), ["decide", "preview", "propose", "write"])
        self.assertEqual(self.sent.await_count, 3)
        preview = self.sent.await_args.args[0]
        for post in self.posts():
            self.assertIn(f"/nailblog approve {post['id']}", preview)

        total = (self.sent.await_count, self.model.outlines, len(self.opener.calls))
        for later in ("20:10", "21:00", "23:59"):
            await self.run_at(later)
        self.assertEqual((self.sent.await_count, self.model.outlines), total[:2])    # nothing repeats

    async def test_a_restart_repeats_nothing(self):
        for hhmm in ("10:05", "13:05", "20:05"):
            await self.run_at(hhmm)
        self.assertEqual(self.sent.await_count, 3)
        restarted = Service(self.config, clock=lambda: self.now)
        restarted.opener, restarted.text, restarted.notice_send = self.opener, self.service.text, AsyncMock()
        planner, outlines, calls = self.model.planner_calls, self.model.outlines, len(self.opener.calls)
        self.now = at("20:30")
        await daily.run_due(restarted)
        restarted.notice_send.assert_not_awaited()
        self.assertEqual((self.model.planner_calls, self.model.outlines, len(self.opener.calls)),
                         (planner, outlines, calls + 0))
        self.assertEqual(len(self.state.list_topics(("proposed", "approved", "dropped"))), 4)

    async def test_the_clock_is_the_vietnam_one_not_utc(self):
        # 2026-10-16 02:30 UTC is 09:30 in Vietnam: not yet 10:00 although the UTC clock shows the same day
        self.now = utc(2026, 10, 16, 2, 30)
        await daily.run_due(self.service)
        self.assertEqual((self.state.daily_get("2026-10-16", "propose"), self.model.planner_calls), (None, 0))
        # 03:30 UTC is 10:30 in Vietnam: due
        self.now = utc(2026, 10, 16, 3, 30)
        await daily.run_due(self.service)
        self.assertIsNotNone(self.state.daily_get("2026-10-16", "propose"))

    async def test_after_midnight_in_vietnam_it_is_the_next_day_with_nothing_due(self):
        # 2026-10-16 17:30 UTC is 00:30 on the 17th in Vietnam, although UTC still says the 16th
        self.now = utc(2026, 10, 16, 17, 30)
        self.assertEqual(vn_date(self.now), date(2026, 10, 17))
        await daily.run_due(self.service)
        self.assertEqual([row["step"] for row in self.state.daily_rows("2026-10-17")], [])
        self.assertEqual([row["step"] for row in self.state.daily_rows("2026-10-16")], [])
        self.assertEqual((self.model.planner_calls, self.sent.await_count), (0, 0))
        self.assertEqual(self.done("2026-10-17#00"), ["poll"])               # the 00:00-03:00 poll bucket of the new day

    async def test_the_last_minute_of_a_day_still_belongs_to_that_day_and_targets_tomorrow(self):
        await self.run_at("10:05")
        self.now = at("23:59")
        await daily.run_due(self.service)
        self.assertEqual(self.done(), ["decide", "preview", "propose", "write"])   # all the late steps ran for the 16th
        self.assertEqual(self.state.topics_for("daily:2026-10-17")[0]["slot_at"], SLOT_06)
        self.now = at("00:01", TARGET)
        await daily.run_due(self.service)
        self.assertEqual(self.done("2026-10-17"), [])                        # the 17th starts clean

    async def test_a_propose_missed_until_after_the_deadline_is_skipped_not_run_late(self):
        await self.run_at("13:05")
        self.assertEqual(self.done(), ["decide", "write"])
        self.assertEqual(self.model.planner_calls, 0)
        self.assertIsNone(self.state.daily_get("2026-10-16", "propose"))

    async def test_steps_are_not_due_while_paused_or_without_a_bound_sender_in_the_service_loop(self):
        service = Service(self.config, state=self.state, clock=lambda: at("10:05"))
        stop = asyncio.Event()
        service._start_jobs(stop)                                              # no sender bound
        self.assertIsNone(service._jobs)
        service.notice_send = AsyncMock()
        self.state.set_paused(True)
        service._start_jobs(stop)
        self.assertIsNone(service._jobs)
        self.state.set_paused(False)
        service.opener, service.text = self.opener, self.service.text
        service._start_jobs(stop)
        first = service._jobs
        service._start_jobs(stop)                                              # a pass is running: not started twice
        self.assertIs(service._jobs, first)
        await first
        self.assertEqual(self.state.daily_get("2026-10-16", "propose")["step"], "propose")


class FailureTests(DailyBase):
    async def run_at(self, hhmm):
        self.now = at(hhmm)
        await daily.run_due(self.service)

    async def test_one_failing_job_never_blocks_the_others(self):
        await self.run_at("10:05")
        self.model.fail_articles = True
        self.opener.routes[TRENDS_URL] = TimeoutError()
        await self.run_at("20:05")
        self.assertIsNone(self.state.daily_get("2026-10-16#06", "poll"))     # the poll failed ...
        self.assertIn(("2026-10-16#06", "poll"), self.service.job_failures)
        self.assertIsNotNone(self.state.daily_get("2026-10-16", "decide"))   # ... decide still ran
        self.assertIsNone(self.state.daily_get("2026-10-16", "write"))       # write failed and stays open
        self.assertIn(("2026-10-16", "write"), self.service.job_failures)
        preview = self.state.daily_get("2026-10-16", "preview")               # ... and the 20:00 DM still went out
        self.assertIn("chưa có bài", preview["message"])
        self.assertEqual(self.sent.await_count, 3)                            # propose, decide, preview

    async def test_a_failed_step_waits_ten_minutes_per_failure_then_retries_and_completes(self):
        await self.run_at("10:05")
        self.model.fail_articles = True
        await self.run_at("13:05")
        attempts = self.model.outlines
        self.assertEqual(attempts, 2 * OUTLINE_ATTEMPTS)   # two chosen slots, each tried OUTLINE_ATTEMPTS times by the model layer
        await self.run_at("13:10")
        self.assertEqual(self.model.outlines, attempts)                        # inside the backoff
        self.model.fail_articles = False
        await self.run_at("13:14")
        self.assertEqual(self.model.outlines, attempts)
        await self.run_at("13:16")                                             # 600 s after the failure
        self.assertEqual((len(self.posts()), self.state.daily_get("2026-10-16", "write") is not None), (2, True))

    async def test_a_step_that_keeps_failing_is_given_up_after_four_attempts(self):
        await self.run_at("10:05")
        self.model.fail_articles = True
        await self.run_at("13:05")
        for hour in range(14, 22):
            await self.run_at(f"{hour}:30")
        self.assertEqual(self.model.outlines, 2 * OUTLINE_ATTEMPTS * daily.MAX_ATTEMPTS)   # four passes of two slots, then it gives up
        self.assertIsNone(self.state.daily_get("2026-10-16", "write"))
        self.assertIsNotNone(self.state.daily_get("2026-10-16", "preview"))

    async def test_a_failed_dm_is_retried_by_the_notice_backoff_and_never_duplicated(self):
        self.sent.side_effect = [OSError("net"), None, None, None]
        await self.run_at("10:05")
        self.assertEqual(self.sent.await_count, 1)
        self.assertEqual(await daily.flush(self.service, TODAY), 0)           # inside the 5 minute notice backoff
        self.now = at("10:12")
        self.assertEqual(await daily.flush(self.service, TODAY), 1)
        self.assertEqual(await daily.flush(self.service, TODAY), 0)
        self.assertEqual(self.sent.await_count, 2)

    async def test_a_later_pass_retries_a_dm_that_failed_even_when_no_step_is_due(self):
        self.sent.side_effect = [OSError("net"), None]
        await self.run_at("10:05")
        self.assertEqual(self.sent.await_count, 1)
        await self.run_at("10:06")                                             # inside the notice backoff
        self.assertEqual(self.sent.await_count, 1)
        await self.run_at("10:12")                                             # no step is due now, the DM still goes
        self.assertEqual(self.sent.await_count, 2)
        await self.run_at("10:30")
        self.assertEqual(self.sent.await_count, 2)

    async def test_without_a_sender_the_dm_stays_recorded_for_the_service_to_send(self):
        self.service.notice_send = None
        await self.run_at("10:05")
        self.assertEqual(await daily.flush(self.service, TODAY), 0)
        self.assertIn("/nailblog pick 06 1", self.state.daily_get("2026-10-16", "propose")["message"])
        self.service.notice_send = self.sent
        self.assertEqual(await daily.flush(self.service, TODAY), 1)

    async def test_a_stop_request_ends_a_pass_before_the_next_step(self):
        stop = asyncio.Event()
        stop.set()
        self.now = at("13:05")
        await daily.run_due(self.service, stop)
        self.assertEqual(self.state.daily_rows("2026-10-16"), [])


class SchedulerLoopTests(DailyBase):
    async def test_the_scheduler_runs_the_due_job_in_its_own_task_and_waits_for_it_on_stop(self):
        stop = asyncio.Event()
        task = asyncio.create_task(self.service.scheduler(stop))
        for _ in range(200):
            await asyncio.sleep(0.05)
            if self.state.daily_get("2026-10-16", "propose") and self.service._jobs.done():
                break
        stop.set()
        await asyncio.wait_for(task, 10)
        self.assertEqual((len(self.rows()), self.sent.await_count), (4, 1))

    async def test_the_scheduler_does_nothing_daily_without_a_bound_sender(self):
        self.service.notice_send = None
        stop = asyncio.Event()
        task = asyncio.create_task(self.service.scheduler(stop))
        await asyncio.sleep(0.2)
        stop.set()
        await asyncio.wait_for(task, 5)
        self.assertEqual((self.state.daily_rows("2026-10-16"), self.model.planner_calls, self.opener.calls), ([], 0, []))


if __name__ == "__main__":
    unittest.main()
