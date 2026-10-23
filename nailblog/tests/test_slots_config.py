"""Slots come from config (S6): `service.slots` + `daily.slot_categories` drive the planner, the daily flow and
`/nailblog pick`. The default two slots keep today's output (covered by test_daily*, test_planner, test_commands)."""
import tempfile
import unittest
from datetime import date
from unittest.mock import AsyncMock

from nailblog import daily
from nailblog.commands import execute, parse
from nailblog.planner import (SLOT_GROUPS, assign_slots, build_prompt, propose, slot_groups)
from nailblog.service import Service
from nailblog.text import TextModel
from tests.daily_support import (BLOGS_URL, FEED_1, FEED_2, SOURCES, TARGET, TODAY, WEEK, DailyBase, Model, at,
                                 candidate)
from tests.fakes import ATOM, BLOGS_JSON, RSS, TRENDING_RSS, TRENDS_URL, FakeOpener
from tests.helpers import load

THREE = {"slots": ["06:00", "12:00", "19:00"]}
THREE_GROUPS = ('"06:00" = ["hot-trend", "nail-job"]\n"12:00" = ["nail-job", "nail-salon"]\n'
                '"19:00" = ["nail-salon", "did-you-know"]')
SLOT_12 = "2026-10-17T12:00+07:00"
WEEK_START = date(2026, 10, 19)   # a Monday


def topic(title, category, score, promo=False):
    return {"title": title, "category": category, "product_promo": promo, "score": score, "slot_at": None}


def three_slot_config(directory):
    return load(directory, THREE, sources=SOURCES, daily__slot_categories=THREE_GROUPS)


class NoMappingConfig(unittest.TestCase):
    def test_a_config_with_custom_slots_and_no_table_has_an_empty_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, {"slots": ["07:00", "12:00"]})
        self.assertEqual(config.daily.slot_categories, {})
        self.assertEqual(slot_groups(config), {"07:00": (), "12:00": ()})


class SlotGroupsTests(unittest.TestCase):
    def test_default_groups_without_a_config_and_with_the_default_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory)
        self.assertEqual(slot_groups(), SLOT_GROUPS)
        self.assertEqual(slot_groups(config), SLOT_GROUPS)

    def test_groups_follow_the_configured_slots_and_table(self):
        with tempfile.TemporaryDirectory() as directory:
            config = three_slot_config(directory)
        self.assertEqual(list(slot_groups(config)), ["06:00", "12:00", "19:00"])
        self.assertEqual(slot_groups(config)["12:00"], ("nail-job", "nail-salon"))


class PlanWeekTests(unittest.IsolatedAsyncioTestCase):
    def topics(self):
        cats = ("hot-trend", "nail-job", "nail-salon", "did-you-know")
        return [topic(f"Chủ đề {index}", cats[index % 4], 100 - index) for index in range(40)]

    async def test_assign_slots_gives_every_day_a_noon_slot_for_three_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            config = three_slot_config(directory)
        placed = [t for t in assign_slots(self.topics(), WEEK_START, config) if t["slot_at"]]
        noon = [t for t in placed if "T12:00" in t["slot_at"]]
        self.assertEqual(len(noon), 7)
        self.assertEqual({t["category"] for t in noon} - {"nail-job", "nail-salon"}, set())
        self.assertEqual(len(placed), 21)
        self.assertEqual([t["slot_at"] for t in placed], sorted(t["slot_at"] for t in placed))

    async def test_the_default_assignment_is_unchanged_with_or_without_the_default_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory)
        plain = assign_slots(self.topics(), WEEK_START)
        with_config = assign_slots(self.topics(), WEEK_START, config)
        self.assertEqual([(t["title"], t["slot_at"]) for t in plain], [(t["title"], t["slot_at"]) for t in with_config])
        self.assertEqual(sum(1 for t in plain if t["slot_at"]), 14)

    async def test_an_empty_mapping_orders_by_score_with_no_preferred_category(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, {"slots": ["07:00", "12:00"]})
        placed = [t for t in assign_slots(self.topics(), WEEK_START, config) if t["slot_at"]]
        self.assertEqual(len(placed), 14)
        self.assertEqual([t["score"] for t in placed], sorted((t["score"] for t in placed), reverse=True))
        self.assertEqual(placed[0]["slot_at"], "2026-10-19T07:00+07:00")
        self.assertEqual(placed[1]["slot_at"], "2026-10-19T12:00+07:00")

    async def test_propose_passes_the_config_to_the_prompt_and_the_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            config = three_slot_config(directory)
        calls = []

        class Text:
            async def ask(self, role, system, user, schema, **options):
                calls.append(user)
                return {"topics": [{**{k: v for k, v in t.items() if k not in ("score", "slot_at")},
                                    "scores": {key: 3 for key in ("hot", "fit", "image", "source", "novelty")},
                                    "source_ids": []} for t in self_topics]}

        self_topics = self.topics()
        topics, _dropped = await propose(Text(), [], [], WEEK_START, date(2026, 10, 16), config)
        self.assertIn("(21 bài, 06:00, 12:00 và 19:00)", calls[0])
        self.assertTrue(any(t["slot_at"] and "T12:00" in t["slot_at"] for t in topics))

    def test_the_default_prompt_line_is_unchanged(self):
        _system, user = build_prompt([], [], WEEK_START)
        self.assertIn("(14 bài, 06:00 và 19:00).", user)


class DailyPromptTests(unittest.TestCase):
    def test_the_default_system_prompt_and_schema_are_the_old_ones(self):
        self.assertIn("Đề xuất 8 chủ đề bài blog cho NGÀY MAI: 4 chủ đề cho slot 06:00 (category hot-trend hoặc nail-job) "
                      "và 4 chủ đề cho slot 19:00 (category nail-salon hoặc did-you-know). Ưu tiên bám", daily.DAILY_SYSTEM)
        self.assertEqual((daily.DAILY_SCHEMA["properties"]["candidates"]["minItems"],
                          daily.DAILY_SCHEMA["properties"]["candidates"]["maxItems"]), (4, 12))
        self.assertEqual(daily.GROUP_LABEL, {"06:00": "hot-trend / nail-job", "19:00": "nail-salon / did-you-know"})

    def test_three_slots_ask_for_twelve_topics_and_allow_eighteen(self):
        groups = {"06:00": ("hot-trend",), "12:00": ("nail-job", "nail-salon"), "19:00": ("did-you-know",)}
        system = daily.daily_system(groups)
        self.assertIn("Đề xuất 12 chủ đề", system)
        self.assertIn("4 chủ đề cho slot 12:00 (category nail-job hoặc nail-salon)", system)
        candidates = daily.daily_schema(3)["properties"]["candidates"]
        self.assertEqual((candidates["minItems"], candidates["maxItems"]), (6, 18))

    def test_an_unmapped_slot_accepts_every_category(self):
        self.assertIn("4 chủ đề cho slot 12:00 (mọi category)", daily.daily_system({"12:00": ()}))


class ThreeSlotDailyTests(DailyBase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = three_slot_config(self.directory.name)
        self.now = at("10:05")
        self.service = Service(self.config, clock=lambda: self.now)
        self.state = self.service.state
        self.opener = self.service.opener = FakeOpener({FEED_1: RSS, FEED_2: ATOM, BLOGS_URL: BLOGS_JSON,
                                                         TRENDS_URL: TRENDING_RSS})
        self.model = Model(self.candidates())
        self.service.text = TextModel(self.config, self.service.model_call, transport=self.model.transport)
        self.sent = self.service.notice_send = AsyncMock()

    def candidates(self):
        return [candidate("Chrome nails mùa lễ hội cho khách nữ", "hot-trend", keyword="chrome nails"),
                candidate("Cat eye nails nhẹ nhàng cho ngày đông", "hot-trend", keyword="cat eye nails"),
                candidate("Lộ trình học nghề nail cho người mới", "nail-job"),
                candidate("Cách xin việc thợ nail tại Texas", "nail-job"),
                candidate("Quản lý lịch hẹn tiệm nail đông khách", "nail-salon"),
                candidate("Mẹo giữ chân thợ nail giỏi", "nail-salon"),
                candidate("Bạn có biết gel bền được bao lâu", "did-you-know"),
                candidate("Chọn dụng cụ khử trùng nào cho tiệm", "did-you-know")]

    async def propose(self):
        return await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)

    async def test_propose_sends_a_twelve_topic_prompt_and_stores_candidates_for_the_noon_slot(self):
        outcome = await self.propose()
        call = self.model.transport.calls[0]
        candidates = call["payload"]["format"]["properties"]["candidates"]
        self.assertEqual((candidates["minItems"], candidates["maxItems"]), (6, 18))
        self.assertIn("Đề xuất 12 chủ đề", str(call["payload"]))
        self.assertIn("Ba slot: 06:00, 12:00 và 19:00.", str(call["payload"]))
        noon = self.rows(SLOT_12)
        self.assertEqual(len(noon), daily.CANDIDATES_PER_SLOT)
        self.assertEqual({row["category"] for row in noon} - {"nail-job", "nail-salon"}, set())
        self.assertIn("Ứng viên slot 12:00 (nail-job / nail-salon):", outcome.text)
        self.assertIn("/nailblog pick 06 1 · /nailblog pick 12 2 · /nailblog pick 19 1", outcome.text)

    async def test_pick_12_1_chooses_the_first_noon_candidate(self):
        await self.propose()
        self.assertEqual(parse("/nailblog pick 12 1"), ("pick", ("12:00", 1)))
        first = self.rows(SLOT_12)[0]
        reply = execute(self.state, "pick", ("12:00", 1), now=at("11:00"), config=self.config)
        self.assertIn("Đã chọn slot 12 ngày 17/10", reply[0])
        self.assertEqual(self.state.get_topic(first["id"])["state"], "approved")

    async def test_pick_rejects_a_slot_that_is_not_configured(self):
        reply = execute(self.state, "pick", ("13:00", 1), now=at("11:00"), config=self.config)
        self.assertEqual(reply, ["Slot phải là một trong: 06, 12, 19."])

    async def test_backlog_has_no_key_error_for_a_third_slot(self):
        wanted = self.state.add_topic("2026-W43", "Backlog việc làm", "nail-job", "2026-10-22T12:00+07:00", 20,
                                      state="approved")
        self.state.add_topic("2026-W43", "Backlog trend", "hot-trend", "2026-10-22T06:00+07:00", 30, state="approved")
        self.assertEqual(daily._backlog(self.state, TARGET, "12:00", self.config)["id"], wanted)
        self.assertIsNotNone(daily._backlog(self.state, TARGET, "12:00"))   # without a config: any category fits

    async def test_decide_locks_a_topic_for_each_of_the_three_slots(self):
        await self.propose()
        self.now = at("13:05")
        outcome = await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)
        for hhmm in ("06:00", "12:00", "19:00"):
            self.assertIsNotNone(daily.chosen_topic(self.state, TARGET, hhmm))
            self.assertIn(f"{hhmm} — ", outcome.text)
        self.assertEqual(len(self.state.topics_for(WEEK)), 6)


class EmptyMappingDailyTests(ThreeSlotDailyTests):
    """`slot_categories = {}` with custom slots: no preferred category, any category, ordered by score."""

    def setUp(self):
        super().setUp()
        self.config = load(self.directory.name, {"slots": ["07:00", "12:00"]}, sources=SOURCES)
        self.service = Service(self.config, clock=lambda: self.now)
        self.state = self.service.state
        self.opener = self.service.opener = FakeOpener({FEED_1: RSS, FEED_2: ATOM, BLOGS_URL: BLOGS_JSON,
                                                         TRENDS_URL: TRENDING_RSS})
        self.service.text = TextModel(self.config, self.service.model_call, transport=self.model.transport)
        self.sent = self.service.notice_send = AsyncMock()

    async def test_propose_and_decide_work_without_a_table(self):
        outcome = await self.propose()
        slot_07, slot_12 = "2026-10-17T07:00+07:00", "2026-10-17T12:00+07:00"
        self.assertEqual((len(self.rows(slot_07)), len(self.rows(slot_12))), (2, 2))
        self.assertIn("Ứng viên slot 07:00 (mọi category, xếp theo điểm):", outcome.text)
        self.assertIn("Đề xuất 8 chủ đề", str(self.model.transport.calls[0]["payload"]))
        self.now = at("13:05")
        await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)
        self.assertIsNotNone(daily.chosen_topic(self.state, TARGET, "07:00"))
        self.assertIsNotNone(daily.chosen_topic(self.state, TARGET, "12:00"))

    async def test_the_backlog_takes_any_category_for_an_unmapped_slot(self):
        topic_id = self.state.add_topic("2026-W43", "Backlog bất kỳ", "did-you-know", "2026-10-22T12:00+07:00", 20,
                                        state="approved")
        self.assertEqual(daily._backlog(self.state, TARGET, "12:00", self.config)["id"], topic_id)

    async def test_pick_12_is_accepted_for_the_configured_slot(self):
        await self.propose()
        reply = execute(self.state, "pick", ("12:00", 2), now=at("11:00"), config=self.config)
        self.assertIn("Đã chọn slot 12", reply[0])

    # Inherited three-slot tests do not apply to this configuration.
    test_propose_sends_a_twelve_topic_prompt_and_stores_candidates_for_the_noon_slot = None
    test_pick_12_1_chooses_the_first_noon_candidate = None
    test_pick_rejects_a_slot_that_is_not_configured = None
    test_backlog_has_no_key_error_for_a_third_slot = None
    test_decide_locks_a_topic_for_each_of_the_three_slots = None


if __name__ == "__main__":
    unittest.main()
