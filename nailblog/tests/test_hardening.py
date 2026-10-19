"""Step B part 0: the seven notes from the independent verification of step A."""
import inspect
import logging
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

from nailblog._errors import ConfigError
from nailblog import schedule as s
from nailblog.config import BudgetConfig, _is_secret_name
from nailblog.service import Service
from nailblog.state import EvidenceRequired, InvalidTransition, State, StateError
from nailblog.usage import BudgetExceeded, UnpricedModel, UsageRecorder
from tests.helpers import load, settings

PRICES = BudgetConfig(daily_usd=1.0, text_prices={"deepseek-chat": (0.5, 2.0)}, image_prices={"img": 0.25})
SLOT = "2026-10-17T06:00+07:00"
KEY = 'key_file = "/etc/nailblog/private/model-key.json"'


class SecretKeyTests(unittest.TestCase):
    def invalid(self, pattern, service=None, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, service, **sections)

    def test_names_containing_or_ending_like_a_secret_are_refused(self):
        for name in ("api_token", "deepseek_key", "authorization", "bot_token", "Authorization", "DEEPSEEK_KEY",
                     "key", "apikey", "my_password", "db_passwd", "client_secret", "x_api_key", "monkey"):
            with self.subTest(name=name):
                self.assertTrue(_is_secret_name(name))

    def test_each_refusal_names_the_key_at_every_depth(self):
        self.invalid("text_model.deepseek_key", text_model='deepseek_key = "sk-1"')
        self.invalid("never in TOML", text_model='bot_token = "t"')
        self.invalid("never in TOML", image='authorization = "Bearer x"')
        self.invalid("never in TOML", {"api_token": "t"})
        self.invalid("never in TOML", **{"budget__text_prices": 'x = { deepseek_key = "k" }'})
        self.invalid("never in TOML", sources='feeds = []\nnested = [{ token = "a" }]')

    def test_file_suffix_names_are_allowed(self):
        for name in ("key_file", "token_file", "api_key_file", "secret_file"):
            self.assertFalse(_is_secret_name(name), name)
        for name in ("model", "feeds", "verification_ref", "daily_usd", "owner_ids", "keys_count_note"):
            self.assertFalse(_is_secret_name(name), name)
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, text_model='provider = "deepseek"\nmodel = "m"\n' + KEY)
        self.assertEqual(config.text_model.key_file, "/etc/nailblog/private/model-key.json")


class UnknownKeyTests(unittest.TestCase):
    def invalid(self, pattern, service=None, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, service, **sections)

    def test_misspelled_keys_are_refused_in_every_table(self):
        self.invalid("service.mod", {"mod": "live"})
        self.invalid("budget.daily_usdd", budget="daily_usdd = 1")
        self.invalid("telegram.sesion", telegram='sesion = "/x"')
        self.invalid("schedule.image_modes", schedule='image_modes = "fixed"')
        self.invalid("text_model.thinking", text_model="thinking = false")
        self.invalid("image.candidate", image="candidate = 1")
        self.invalid("contract.verifed", contract="verifed = true")
        self.invalid("console.revison", console='revison = "x"')
        self.invalid("sources.feed", sources="feed = []")

    def test_unknown_table_is_refused(self):
        self.invalid("extras: unknown configuration table", extras="a = 1")

    def test_all_documented_keys_are_still_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, {"timezone": "Asia/Ho_Chi_Minh", "slots": ["06:00", "19:00"],
                                      "late_window_minutes": 10, "owner_username": "SotaThao"},
                          telegram='session = "/var/lib/agentfleet/nailblog/nailblog.session"',
                          text_model='num_predict = 500\ntimeout_seconds = 30', console='revision = "r1"',
                          sources='feeds = ["https://www.bustle.com/rss"]\ntimeout_seconds = 5')
        self.assertEqual((config.text_model.num_predict, config.text_model.timeout_seconds), (500, 30))
        self.assertEqual((config.sources.feeds, config.sources.timeout_seconds), (("https://www.bustle.com/rss",), 5))

    def test_sources_and_text_model_values_are_validated(self):
        for feed in ('"http://a.example/rss"', '"https://u:p@a.example/rss"', '"ftp://a.example"', '"not a url"', "1"):
            with self.subTest(feed=feed):
                self.invalid("sources.feeds", sources=f"feeds = [{feed}]")
        self.invalid("sources.feeds", sources='feeds = ["https://a.example/x", "https://a.example/x"]')
        self.invalid("sources.timeout_seconds", sources="timeout_seconds = 0")
        self.invalid("text_model.num_predict", text_model="num_predict = 10")
        self.invalid("text_model.timeout_seconds", text_model="timeout_seconds = 5")


class PublishedEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "s.sqlite3")

    def test_published_needs_non_empty_evidence_from_both_source_states(self):
        for old in ("before_publish", "uncertain"):
            for empty in (None, "", "   ", {}, [], {"status": ""}, [""], {"a": {"b": " "}}):
                with self.subTest(old=old, evidence=empty):
                    post = self.state.add_post(SLOT, state=old)
                    with self.assertRaises(EvidenceRequired):
                        self.state.transition(post, "published", reason="r", evidence=empty)
                    self.assertEqual(self.state.get_post(post)["state"], old)

    def test_evidence_is_stored_in_details(self):
        for old in ("before_publish", "uncertain"):
            post = self.state.add_post(SLOT, state=old)
            self.state.transition(post, "published", details={"photos": 4},
                                  evidence={"readback": "Published", "url": "https://nailhub.ai/blog/x"})
            row = self.state.get_post(post)
            self.assertEqual(row["state"], "published")
            self.assertIn('"evidence": {"readback": "Published", "url": "https://nailhub.ai/blog/x"}', row["details"])
            self.assertIn('"photos": 4', row["details"])

    def test_missing_evidence_is_an_invalid_transition_and_other_edges_do_not_need_it(self):
        self.assertTrue(issubclass(EvidenceRequired, InvalidTransition))
        post = self.state.add_post(SLOT, state="before_publish")
        self.state.transition(post, "uncertain")
        self.state.transition(post, "held")
        with self.assertRaises(InvalidTransition) as raised:  # a wrong edge reports the edge, not the evidence
            self.state.transition(self.state.add_post(SLOT, state="review"), "published", evidence="x")
        self.assertNotIsInstance(raised.exception, EvidenceRequired)


class SlotValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "s.sqlite3")
        self.service = Service(settings(self.directory.name), state=self.state,
                               clock=lambda: datetime.fromisoformat(SLOT).timestamp() + 60)

    def test_slots_must_be_iso_datetimes_with_a_timezone(self):
        for bad in ("2026-10-17T06:00", "2026-10-17", "garbage", "", None, 5, "06:00"):
            with self.subTest(bad=bad):
                with self.assertRaises(StateError):
                    self.state.add_post(bad)
                if bad is not None:  # a topic may have no slot yet; a post may not
                    with self.assertRaises(StateError):
                        self.state.add_topic("2026-W43", "t", "nail-job", bad)
                post = self.state.add_post(SLOT)
                with self.assertRaises(StateError):
                    self.state.move_post(post, bad)
                self.assertEqual(self.state.get_post(post)["slot_at"], SLOT)
        self.assertEqual(self.state.list_posts(slot_from="2026-10-17T07:00+07:00"), [])
        self.state.add_post("2026-10-17T06:00:00Z")
        self.state.add_topic("2026-W43", "t", "nail-job", None)

    def test_tick_skips_and_logs_a_corrupt_row_without_blocking_the_others(self):
        with self.state.connection() as db:
            db.execute("INSERT INTO posts(slot_at,state) VALUES('garbage','scheduled')")
            db.execute("INSERT INTO posts(slot_at,state) VALUES('2026-10-17T06:00','scheduled')")  # naive
        good = self.state.add_post(SLOT, state="scheduled")
        with self.assertLogs("nailblog.service", logging.INFO) as logs:
            self.assertEqual(self.service.tick(), [good])
        bad_lines = [line for line in logs.output if "skipped post" in line]
        self.assertEqual(len(bad_lines), 2)
        self.assertTrue(any("post 1" in line for line in bad_lines) and any("post 2" in line for line in bad_lines))
        with self.assertNoLogs("nailblog.service", logging.WARNING):
            self.assertEqual(self.service.tick(), [good])  # one line per bad row per process, not per minute


class BackoffCapTests(unittest.TestCase):
    def test_attempt_is_capped_at_16(self):
        self.assertEqual(s.adaptive_backoff(16), timedelta(minutes=5 * 2 ** 16))
        self.assertEqual(s.adaptive_backoff(17), s.adaptive_backoff(16))
        self.assertEqual(s.adaptive_backoff(200), s.adaptive_backoff(16))
        self.assertEqual(s.adaptive_backoff(0), timedelta(minutes=5))
        with self.assertRaises(ValueError):
            s.adaptive_backoff(-1)

    def test_attempt_200_stays_within_the_deadline(self):
        slot = s.slot_datetime(date(2026, 10, 17), "06:00")
        now = slot - timedelta(hours=10)
        for attempt in (16, 17, 200, 10 ** 6):
            self.assertEqual(s.adaptive_next_attempt(now, attempt, slot), s.image_deadline(slot))
        self.assertLessEqual(s.adaptive_next_attempt(now, 200, slot), s.image_deadline(slot))
        self.assertEqual(s.adaptive_next_attempt(now, 0, slot), now + timedelta(minutes=5))


class FakeUsage:
    """Same shape as UsageRecorder.check() / record(provider, model, units, usd); keeps the order of events."""

    def __init__(self, events, fail_check=False):
        self.events, self.fail_check = events, fail_check

    def check(self):
        self.events.append("check")
        if self.fail_check:
            raise BudgetExceeded("daily USD budget reached")

    def record(self, provider, model, units, usd):
        self.events.append(("record", provider, model, units, round(usd, 6)))


class ModelCallBillingTests(unittest.IsolatedAsyncioTestCase):
    def make(self, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        service = Service(replace(settings(directory.name), budget=PRICES), **kwargs)
        self.events = []
        service.usage = FakeUsage(self.events)
        return service

    def test_the_fake_matches_the_real_recorder_signatures(self):
        for name in ("check", "record"):
            self.assertEqual(inspect.signature(getattr(FakeUsage, name)).parameters.keys(),
                             inspect.signature(getattr(UsageRecorder, name)).parameters.keys())

    async def test_paid_provider_checks_before_and_records_after(self):
        service = self.make()

        def call(value, usage_sink=None):
            self.events.append("call")
            usage_sink({"prompt_tokens": 1000, "completion_tokens": 500})
            return value
        self.assertEqual(await service.model_call(call, 7, provider="deepseek", model="deepseek-chat"), 7)
        self.assertEqual(self.events, ["check", "call", ("record", "deepseek", "deepseek-chat", 1500, 0.0015)])

    async def test_budget_exceeded_stops_the_call_before_it_starts(self):
        service = self.make()
        service.usage.fail_check = True
        with self.assertRaises(BudgetExceeded):
            await service.model_call(lambda usage_sink=None: self.events.append("call"),
                                     provider="deepseek", model="deepseek-chat")
        self.assertEqual(self.events, ["check"])

    async def test_unpriced_paid_model_is_refused_before_the_call(self):
        service = self.make()
        with self.assertRaises(UnpricedModel):
            await service.model_call(lambda usage_sink=None: self.events.append("call"),
                                     provider="deepseek", model="not-priced")
        self.assertEqual(self.events, ["check"])

    async def test_failed_paid_call_is_still_recorded(self):
        service = self.make()

        def call(usage_sink=None):
            usage_sink({"prompt_tokens": 10, "completion_tokens": 0})
            raise OSError("boom")
        with self.assertRaises(OSError):
            await service.model_call(call, provider="deepseek", model="deepseek-chat")
        self.assertEqual(self.events, ["check", ("record", "deepseek", "deepseek-chat", 10, 0.000005)])

    async def test_local_provider_is_never_checked_and_costs_nothing(self):
        service = self.make()

        def call(usage_sink=None):
            usage_sink({"prompt_tokens": 400, "completion_tokens": 100})
        await service.model_call(call, provider="ollama", model="qwen3.5:9b")
        self.assertEqual(self.events, [("record", "ollama", "qwen3.5:9b", 500, 0.0)])

    async def test_unbilled_call_has_the_old_plain_shape(self):
        service = self.make()
        self.assertEqual(await service.model_call(lambda a, b: a + b, 2, 3), 5)
        self.assertEqual(self.events, [])

    async def test_real_recorder_refuses_once_the_daily_budget_is_spent(self):
        with tempfile.TemporaryDirectory() as directory:
            service = Service(replace(settings(directory), budget=replace(PRICES, daily_usd=0.001)))
            service.usage.record("deepseek", "deepseek-chat", 1, 0.002)
            with self.assertRaises(BudgetExceeded):
                await service.model_call(lambda usage_sink=None: None, provider="deepseek", model="deepseek-chat")


class SummaryImagesTests(unittest.TestCase):
    def test_image_units_get_their_own_key_and_do_not_inflate_tokens(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(settings(directory), budget=PRICES)
            usage = UsageRecorder(State(config.db_path), config)
            usage.record_text("deepseek", "deepseek-chat", 1000, 500)
            usage.record_image("gemini", "img", 3)
            usage.publish(force=True)
            import json
            data = json.loads((Path(directory) / "agentfleet-usage.json").read_text(encoding="utf-8"))
        today = data["windows"]["today"]
        self.assertEqual((today["promptTokens"], today["images"], today["calls"]), (1500, 3, 2))
        rows = {row["model"]: row for row in today["byModel"]}
        self.assertEqual((rows["img"]["images"], rows["img"]["promptTokens"]), (3, 0))
        self.assertEqual((rows["deepseek-chat"]["images"], rows["deepseek-chat"]["promptTokens"]), (0, 1500))
        self.assertEqual(data["windows"]["30d"]["images"], 3)


if __name__ == "__main__":
    unittest.main()
