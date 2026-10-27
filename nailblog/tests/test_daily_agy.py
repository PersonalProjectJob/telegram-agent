"""Four daily slots with the Agy `ask` (S3): 4 slots x 4 topics proposed in one call, pick 10/15, decide locks one topic
per slot, `_backlog` has no KeyError for 10:00/15:00, an empty slot mapping still works. No network, no model."""
import tempfile
import unittest
from unittest.mock import AsyncMock

from nailblog import daily
from nailblog.commands import execute, parse
from nailblog.config import AGY_SLOT_CATEGORIES, AGY_SLOTS
from nailblog.planner import slot_groups
from nailblog.service import Service
from tests.daily_support import (BLOGS_URL, FEED_1, FEED_2, SOURCES, TARGET, TODAY, WEEK, DailyBase, at, candidate)
from tests.fakes import ATOM, BLOGS_JSON, RSS, TRENDING_RSS, TRENDS_URL, FakeOpener
from tests.helpers import load

FOUR = {"slots": list(AGY_SLOTS)}
SLOT = {hhmm: f"2026-10-17T{hhmm}+07:00" for hhmm in AGY_SLOTS}
WORDS = ("chrome", "velvet", "sương", "mocha", "ombré", "pastel", "ánh kim", "đỏ rượu", "nâu sữa", "xanh rêu",
         "tím khói", "hồng đào", "be kem", "đen mờ", "xám bạc", "cam đất", "vàng mật", "lam nhạt", "cherry", "satin")


def sixteen():
    """16 candidates, 4 per slot group, titles far enough apart not to be 'duplicates'."""
    groups = [("hot-trend", "nail-job"), ("did-you-know", "hot-trend"), ("nail-salon", "nail-job"),
              ("nail-salon", "did-you-know")]
    rows, number = [], 0
    for group in groups:
        for index in range(4):
            rows.append(candidate(f"{WORDS[number]} và {WORDS[(number + 7) % 20]} chủ đề {number + 1} cho tiệm",
                                  group[index % 2]))
            number += 1
    return rows


class AskRecorder:
    def __init__(self, candidates):
        self.candidates, self.calls = candidates, []

    def __call__(self, prompt, schema, *, purpose):
        self.calls.append({"prompt": prompt, "schema": schema, "purpose": purpose})
        return {"candidates": self.candidates}


class FourSlotBase(DailyBase):
    SERVICE = FOUR

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = load(self.directory.name, self.SERVICE, sources=SOURCES)
        self.now = at("10:05")
        self.service = Service(self.config, clock=lambda: self.now)
        self.state = self.service.state
        self.opener = self.service.opener = FakeOpener({FEED_1: RSS, FEED_2: ATOM, BLOGS_URL: BLOGS_JSON,
                                                         TRENDS_URL: TRENDING_RSS})
        self.sent = self.service.notice_send = AsyncMock()
        self.ask = AskRecorder(sixteen())

    async def propose(self):
        return await daily.run_step(self.service, "propose", TODAY, None, opener=self.opener, ask=self.ask)


class FourSlotTests(FourSlotBase):
    def test_the_config_has_four_slots_with_the_agy_groups(self):
        self.assertEqual(list(slot_groups(self.config)), list(AGY_SLOTS))
        self.assertEqual(slot_groups(self.config), {slot: tuple(group) for slot, group in AGY_SLOT_CATEGORIES.items()})
        self.assertEqual(daily.group_labels(self.config)["10:00"], "did-you-know / hot-trend")
        self.assertEqual(daily.group_labels(), daily.GROUP_LABEL)   # no config: the default slots

    def test_the_schema_for_four_slots_admits_sixteen_candidates(self):
        candidates = daily.daily_schema(4)["properties"]["candidates"]
        self.assertLessEqual(candidates["minItems"], 16)
        self.assertGreaterEqual(candidates["maxItems"], 16)
        self.assertEqual(len(sixteen()), 16)

    async def test_propose_makes_one_ask_for_sixteen_topics_and_stores_two_per_slot(self):
        outcome = await self.propose()
        self.assertEqual(len(self.ask.calls), 1)
        call = self.ask.calls[0]
        self.assertEqual(call["purpose"], "daily-planner")
        self.assertIn("Đề xuất 16 chủ đề", call["prompt"])
        for hhmm in AGY_SLOTS:
            self.assertIn(f"4 chủ đề cho slot {hhmm}", call["prompt"])
        self.assertIn("Bốn slot: 06:00, 10:00, 15:00 và 19:00.", call["prompt"])
        self.assertEqual(daily.daily_schema(4), call["schema"])
        for hhmm in AGY_SLOTS:
            rows = self.rows(SLOT[hhmm])
            self.assertEqual(len(rows), daily.CANDIDATES_PER_SLOT, hhmm)
            self.assertTrue({row["category"] for row in rows} <= set(AGY_SLOT_CATEGORIES[hhmm]), hhmm)
            self.assertIn(f"Ứng viên slot {hhmm} (", outcome.text)
        self.assertIn("/nailblog pick 06 1 · /nailblog pick 10 2 · /nailblog pick 15 1 · /nailblog pick 19 2",
                      outcome.text)

    async def test_a_bare_callable_as_text_is_the_ask_too(self):
        await daily.run_step(self.service, "propose", TODAY, self.ask, opener=self.opener)
        self.assertEqual(len(self.ask.calls), 1)
        self.assertEqual(len(self.rows()), 4 * daily.CANDIDATES_PER_SLOT)

    async def test_an_ask_reply_that_does_not_fit_the_schema_is_a_daily_error(self):
        self.ask.candidates = [candidate("Chrome nails", "hot-trend")]   # fewer than the schema's minimum
        with self.assertRaises(daily.DailyError) as caught:
            await self.propose()
        self.assertEqual(str(caught.exception), "planner_schema_mismatch")
        self.assertEqual(self.rows(), [])

    async def test_an_error_of_ask_propagates(self):
        def failing(prompt, schema, *, purpose):
            error = RuntimeError("agy_timeout")
            error.code = "agy_timeout"
            raise error
        with self.assertRaises(RuntimeError) as caught:
            await daily.run_step(self.service, "propose", TODAY, None, opener=self.opener, ask=failing)
        self.assertEqual(caught.exception.code, "agy_timeout")

    async def test_pick_10_1_and_pick_15_2_choose_those_candidates(self):
        await self.propose()
        self.assertEqual(parse("/nailblog pick 10 1"), ("pick", ("10:00", 1)))
        self.assertEqual(parse("/nailblog pick 15 2"), ("pick", ("15:00", 2)))
        ten, fifteen = self.rows(SLOT["10:00"]), self.rows(SLOT["15:00"])
        reply10 = execute(self.state, "pick", ("10:00", 1), now=at("11:00"), config=self.config)
        reply15 = execute(self.state, "pick", ("15:00", 2), now=at("11:00"), config=self.config)
        self.assertIn("Đã chọn slot 10 ngày 17/10", reply10[0])
        self.assertIn("Đã chọn slot 15 ngày 17/10", reply15[0])
        self.assertEqual(self.state.get_topic(ten[0]["id"])["state"], "approved")
        self.assertEqual(self.state.get_topic(fifteen[1]["id"])["state"], "approved")

    async def test_decide_locks_exactly_one_topic_for_each_of_the_four_slots(self):
        await self.propose()
        execute(self.state, "pick", ("15:00", 2), now=at("11:00"), config=self.config)
        self.now = at("13:05")
        outcome = await daily.run_step(self.service, "decide", TODAY, None, opener=self.opener)
        locked = []
        for hhmm in AGY_SLOTS:
            row = daily.chosen_topic(self.state, TARGET, hhmm)
            self.assertIsNotNone(row, hhmm)
            self.assertIn(f"{hhmm} — {row['title'][:40]}", outcome.text)
            approved = [r for r in self.state.topics_for(WEEK, SLOT[hhmm]) if r["state"] == "approved"]
            self.assertEqual(len(approved), 1, hhmm)
            locked.append(row["id"])
        self.assertEqual(len(set(locked)), 4)
        picked = self.rows(SLOT["15:00"])[1]
        self.assertEqual(daily.chosen_topic(self.state, TARGET, "15:00")["id"], picked["id"])
        self.assertEqual(daily.chosen_topic(self.state, TARGET, "06:00")["id"],
                         max(self.rows(SLOT["06:00"]), key=lambda r: (r["score"], -r["id"]))["id"])
        again = await daily.run_step(self.service, "decide", TODAY, None, opener=self.opener)
        self.assertFalse(again.created)

    async def test_backlog_has_no_key_error_for_the_ten_and_fifteen_slots(self):
        salon = self.state.add_topic("2026-W43", "Backlog tiệm", "nail-salon", "2026-10-22T15:00+07:00", 20,
                                     state="approved")
        wise = self.state.add_topic("2026-W43", "Backlog kiến thức", "did-you-know", "2026-10-22T10:00+07:00", 21,
                                    state="approved")
        self.assertEqual(daily._backlog(self.state, TARGET, "15:00", self.config)["id"], salon)
        self.assertEqual(daily._backlog(self.state, TARGET, "10:00", self.config)["id"], wise)
        self.assertIsNotNone(daily._backlog(self.state, TARGET, "10:00"))   # no config: any category fits


class EmptyMappingFourSlotTests(FourSlotBase):
    """`slot_categories = {}` with the four slots: any category, no preferred one, ordered by score."""
    SERVICE = {"slots": ["07:00", "10:00", "15:00", "20:00"]}

    async def test_an_empty_mapping_accepts_every_category_for_every_slot(self):
        self.assertEqual(self.config.daily.slot_categories, {})
        self.assertEqual(slot_groups(self.config), {slot: () for slot in ("07:00", "10:00", "15:00", "20:00")})
        await self.propose()
        self.assertIn("(mọi category)", self.ask.calls[0]["prompt"])
        for hhmm in ("07:00", "10:00", "15:00", "20:00"):
            self.assertEqual(len(self.rows(f"2026-10-17T{hhmm}+07:00")), daily.CANDIDATES_PER_SLOT, hhmm)


if __name__ == "__main__":
    unittest.main()
