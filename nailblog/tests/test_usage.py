import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from nailblog.config import BudgetConfig
from nailblog.schedule import VN_TZ
from nailblog.state import State
from nailblog.usage import (SUMMARY_NAME, UnpricedModel, UsageRecorder, day_bounds, image_cost,
                            text_cost)
from tests.helpers import settings

PRICES = BudgetConfig(daily_usd=1.0, text_prices={"deepseek-chat": (0.5, 2.0)}, image_prices={"img": 0.25})


def vn(day, hhmm="12:00"):
    hour, minute = map(int, hhmm.split(":"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=VN_TZ).timestamp()


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = replace(settings(self.directory.name), budget=PRICES)
        self.state = State(self.config.db_path)
        self.clock = Clock(vn(date(2026, 10, 17)))
        self.usage = UsageRecorder(self.state, self.config, clock=self.clock)

    def summary(self):
        return json.loads((Path(self.directory.name) / SUMMARY_NAME).read_text(encoding="utf-8"))

    def test_day_bounds_are_vietnam_calendar_days(self):
        start, end = day_bounds(vn(date(2026, 10, 17), "23:59"))
        self.assertEqual((start, end), (vn(date(2026, 10, 17), "00:00"), vn(date(2026, 10, 18), "00:00")))
        self.assertEqual(day_bounds(vn(date(2026, 10, 17), "00:00"))[0], start)
        self.assertEqual(day_bounds(vn(date(2026, 10, 16), "23:59"))[1], start)
        self.assertEqual(end - start, 86400)

    def test_cost_tables(self):
        self.assertAlmostEqual(text_cost(PRICES, "deepseek", "deepseek-chat", 1_000_000, 500_000), 1.5)
        self.assertEqual(text_cost(PRICES, "ollama", "qwen3.5:9b", 10 ** 6, 10 ** 6), 0.0)
        self.assertEqual(text_cost(PRICES, "agy", "gemini-3.1-pro", 10 ** 6, 10 ** 6), 0.0)   # Agy: unpriced, free of API bill
        self.assertAlmostEqual(image_cost(PRICES, "img", 4), 1.0)
        self.assertEqual(image_cost(PRICES, "subscription", 4), 0.0)
        self.assertEqual(image_cost(PRICES, "unpriced-model", 4, provider="subscription"), 0.0)
        with self.assertRaises(UnpricedModel):
            text_cost(PRICES, "deepseek", "mystery", 1, 1)
        with self.assertRaises(UnpricedModel):
            image_cost(PRICES, "mystery", 1)

    def test_totals_accumulate_per_vietnam_day(self):
        base = vn(date(2026, 10, 17), "00:00")
        self.state.add_usage(base - 1, "gemini", "img", 1, 0.9)      # 23:59:59 the day before: not today
        self.state.add_usage(base, "gemini", "img", 1, 0.1)          # first second of today
        self.state.add_usage(base + 3600, "gemini", "img", 1, 0.2)
        self.state.add_usage(base + 86400, "gemini", "img", 1, 5.0)  # first second of tomorrow: not today
        self.assertAlmostEqual(self.usage.total_today(), 0.3)
        self.clock.now = base + 86400 + 10
        self.assertAlmostEqual(self.usage.total_today(), 5.0)

    def test_utc_day_boundary_is_not_used(self):
        # 2026-10-17 05:00 in Vietnam is still 2026-10-16 22:00 UTC: both entries belong to the same Vietnam day.
        self.state.add_usage(vn(date(2026, 10, 17), "01:00"), "gemini", "img", 1, 0.4)  # 18:00 UTC the day before
        self.state.add_usage(vn(date(2026, 10, 17), "20:00"), "gemini", "img", 1, 0.4)  # 13:00 UTC same day
        self.clock.now = vn(date(2026, 10, 17), "21:00")
        self.assertAlmostEqual(self.usage.total_today(), 0.8)

    def test_check_is_a_no_op_because_no_api_budget_is_enforced_any_more(self):
        self.usage.check()
        self.usage.record("gemini", "img", 3, 5.0)   # far above budget.daily_usd; the old ledger is retired
        self.usage.check()
        self.assertAlmostEqual(self.usage.total_today(), 5.0)

    def test_record_helpers_use_the_price_tables(self):
        self.assertAlmostEqual(self.usage.record_text("deepseek", "deepseek-chat", 1000, 500), 0.0015)
        self.assertAlmostEqual(self.usage.record_image("gemini", "img", 2), 0.5)
        self.assertAlmostEqual(self.usage.total_today(), 0.5015)
        self.assertEqual(self.usage.record_text("ollama", "qwen3.5:9b", 5000, 5000), 0.0)
        with self.assertRaises(UnpricedModel):
            self.usage.record_image("gemini", "other", 1)
        self.assertEqual(len(self.state.usage_between(0, 2 * 10 ** 10)), 3)

    def test_summary_file_matches_listener_schema(self):
        self.usage.record_text("deepseek", "deepseek-chat", 1000, 500)
        data = self.summary()
        self.assertEqual((data["schema"], data["currency"]), (1, "USD"))
        self.assertEqual(data["generatedAt"], self.clock.now)
        self.assertEqual(set(data), {"schema", "generatedAt", "currency", "windows", "daily"})
        self.assertEqual(set(data["windows"]), {"today", "7d", "30d"})
        self.assertEqual(len(data["daily"]), 30)
        self.assertEqual(data["daily"][-1], {"date": "2026-10-17", "calls": 1, "errors": 0, "fallbacks": 0, "cost": 0.0015})
        today = data["windows"]["today"]
        self.assertEqual((today["calls"], today["ok"], today["promptTokens"], today["cost"]), (1, 1, 1500, 0.0015))
        self.assertEqual(today["byModel"][0]["model"], "deepseek-chat")
        self.assertEqual(set(today), {"calls", "ok", "errors", "fallbacks", "promptTokens", "completionTokens",
                                      "cacheHitTokens", "images", "cost", "byModel"})

    def test_summary_windows_cover_7_and_30_days(self):
        self.state.add_usage(vn(date(2026, 10, 12)), "gemini", "img", 1, 1.0)  # 5 days ago
        self.state.add_usage(vn(date(2026, 10, 5)), "gemini", "img", 1, 2.0)   # 12 days ago
        self.state.add_usage(vn(date(2026, 8, 1)), "gemini", "img", 1, 4.0)    # older than 30 days
        self.usage.record("gemini", "img", 1, 0.5)
        windows = self.summary()["windows"]
        self.assertAlmostEqual(windows["today"]["cost"], 0.5)
        self.assertAlmostEqual(windows["7d"]["cost"], 1.5)
        self.assertAlmostEqual(windows["30d"]["cost"], 3.5)

    def test_summary_is_written_next_to_the_database_atomically_and_throttled(self):
        path = Path(self.directory.name) / SUMMARY_NAME
        self.assertFalse(path.exists())
        self.usage.record("gemini", "img", 1, 0.1)
        self.assertEqual(self.summary()["windows"]["today"]["calls"], 1)
        self.clock.now += 29
        self.usage.record("gemini", "img", 1, 0.1)  # throttled: file still shows one call
        self.assertEqual(self.summary()["windows"]["today"]["calls"], 1)
        self.assertFalse(self.usage.publish())
        self.clock.now += 1
        self.assertTrue(self.usage.publish())  # 30 s elapsed: refreshed with both calls
        self.assertEqual(self.summary()["windows"]["today"]["calls"], 2)
        self.assertTrue(self.usage.publish(force=True))
        self.assertFalse(path.with_suffix(".tmp").exists())

    def test_summary_failure_never_raises(self):
        broken = UsageRecorder(self.state, replace(self.config, db_path=str(Path(self.directory.name) / "no" / "x.sqlite3")),
                               clock=self.clock)
        self.assertFalse(broken.publish())
        broken.record("gemini", "img", 1, 0.1)  # accounting row still written

    def test_prune_keeps_90_days(self):
        self.state.add_usage(self.clock.now - 91 * 86400, "gemini", "img", 1, 1.0)
        self.state.add_usage(self.clock.now - 89 * 86400, "gemini", "img", 1, 1.0)
        self.assertEqual(self.usage.prune(), 1)
        self.assertEqual(len(self.state.usage_between(0, 2 * 10 ** 10)), 1)


if __name__ == "__main__":
    unittest.main()
