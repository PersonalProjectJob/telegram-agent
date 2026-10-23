"""/nailblog trend add and /nailblog pick (TD-8)."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from nailblog.commands import USAGE, execute, parse, route_command
from nailblog.config import DailyConfig
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State
from tests.helpers import OWNER, dm, settings

WEEK = "daily:2026-10-17"
SLOT_06 = "2026-10-17T06:00+07:00"
SLOT_19 = "2026-10-17T19:00+07:00"


def vn(hour, minute=0, day=16):
    return datetime(2026, 10, day, hour, minute, tzinfo=VN_TZ).timestamp()


class ParseTests(unittest.TestCase):
    def test_pick_and_trend_add_parse(self):
        self.assertEqual(parse("/nailblog pick 06 1"), ("pick", ("06:00", 1)))
        self.assertEqual(parse("/nailblog pick 19  2"), ("pick", ("19:00", 2)))
        self.assertEqual(parse("/nailblog trend add #ChromeNails 12M"), ("trend_add", ("chromenails", 12_000_000)))
        self.assertEqual(parse("/nailblog trend add #nail_art 450K"), ("trend_add", ("nail_art", 450_000)))
        self.assertEqual(parse("/nailblog trend add #gelx 1200"), ("trend_add", ("gelx", 1200)))
        self.assertEqual(parse("/nailblog trend add #gelx 1.5m"), ("trend_add", ("gelx", 1_500_000)))

    def test_malformed_variants_give_none(self):
        for text in ("/nailblog pick", "/nailblog pick 06", "/nailblog pick 6 1", "/nailblog pick 06 x", "/nailblog pick 06 0",
                     "/nailblog pick 06 -1", "/nailblog pick 06 1 2", "/nailblog pick abc 1",
                     "/nailblog trend", "/nailblog trend add", "/nailblog trend add #tag", "/nailblog trend add tag 12M",
                     "/nailblog trend add #tag 0", "/nailblog trend add #tag 12X", "/nailblog trend add #tag -3",
                     "/nailblog trend add # 12M", "/nailblog trend add #a 12M", "/nailblog trend remove #tag 12M",
                     "/nailblog trend add #tag 12M extra", "/nailblog trend add #" + "x" * 41 + " 12M"):
            with self.subTest(text=text):
                self.assertIsNone(parse(text))

    def test_the_usage_line_names_both_commands(self):
        self.assertIn("pick <giờ> <số>", USAGE)
        self.assertIn("trend add #tag", USAGE)


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = settings(self.directory.name)
        self.state = State(self.config.db_path)

    def run_command(self, text, now):
        name, args = parse(text)
        return execute(self.state, name, args, now=now, config=self.config)

    def candidates(self, slot=SLOT_06, count=2, prefix="Ứng viên"):
        return [self.state.add_topic(WEEK, f"{prefix} {slot[11:13]} số {n}", "hot-trend", slot, 0.5 - n / 10)
                for n in range(1, count + 1)]


class TrendAddTests(Base):
    def test_the_tag_is_stored_lowercase_without_the_hash_with_the_time(self):
        reply = self.run_command("/nailblog trend add #ChromeNails 12M", vn(11))
        self.assertEqual(self.state.tiktok_tags(), [{"added_at": vn(11), "tag": "chromenails", "views": 12_000_000}])
        self.assertIn("#chromenails", reply[0])
        self.assertIn("12M", reply[0])

    def test_entries_accumulate(self):
        self.run_command("/nailblog trend add #a1 100", vn(11))
        self.run_command("/nailblog trend add #a1 200", vn(12))
        self.assertEqual([row["views"] for row in self.state.tiktok_tags()], [100, 200])


class PickTests(Base):
    def test_a_pick_approves_that_candidate_and_drops_its_sibling_only(self):
        first, second = self.candidates(SLOT_06)
        other = self.candidates(SLOT_19, prefix="Khác")
        reply = self.run_command("/nailblog pick 06 2", vn(11))
        self.assertIn(f"#{second}", reply[0])
        self.assertEqual([self.state.get_topic(i)["state"] for i in (first, second)], ["dropped", "approved"])
        self.assertEqual([self.state.get_topic(i)["state"] for i in other], ["proposed", "proposed"])

    def test_picking_again_before_the_deadline_changes_the_choice(self):
        first, second = self.candidates(SLOT_06)
        self.run_command("/nailblog pick 06 1", vn(11))
        self.run_command("/nailblog pick 06 2", vn(12, 59))
        self.assertEqual([self.state.get_topic(i)["state"] for i in (first, second)], ["dropped", "approved"])
        self.run_command("/nailblog pick 06 1", vn(12, 59))
        self.assertEqual([self.state.get_topic(i)["state"] for i in (first, second)], ["approved", "dropped"])

    def test_the_deadline_is_inclusive_of_12_59_and_closed_from_13_00(self):
        first, _ = self.candidates(SLOT_06)
        reply = self.run_command("/nailblog pick 06 1", vn(13, 0))
        self.assertIn("quá hạn", reply[0])
        self.assertEqual(self.state.get_topic(first)["state"], "proposed")
        self.assertIn("Đã chọn", self.run_command("/nailblog pick 06 1", vn(12, 59))[0])

    def test_the_deadline_comes_from_the_config(self):
        self.config = settings(self.directory.name, daily=DailyConfig("08:00", "09:00", "20:00"))
        first, _ = self.candidates(SLOT_06)
        self.assertIn("quá hạn chọn (09:00)", self.run_command("/nailblog pick 06 1", vn(9, 0))[0])
        self.assertIn("Đã chọn", self.run_command("/nailblog pick 06 1", vn(8, 59))[0])

    def test_a_number_beyond_the_candidates_changes_nothing(self):
        first, second = self.candidates(SLOT_06)
        reply = self.run_command("/nailblog pick 06 3", vn(11))
        self.assertIn("chỉ có 2 ứng viên", reply[0])
        self.assertEqual([self.state.get_topic(i)["state"] for i in (first, second)], ["proposed", "proposed"])

    def test_a_wrong_slot_or_a_slot_without_candidates_is_explained(self):
        self.candidates(SLOT_06)
        self.assertIn("Slot phải là một trong: 06, 19", self.run_command("/nailblog pick 07 1", vn(11))[0])
        self.assertIn("Chưa có ứng viên cho slot 19", self.run_command("/nailblog pick 19 1", vn(11))[0])

    def test_candidates_of_another_day_are_not_picked(self):
        self.state.add_topic("daily:2026-10-18", "Ngày khác", "hot-trend", "2026-10-18T06:00+07:00", 0.1)
        self.assertIn("Chưa có ứng viên", self.run_command("/nailblog pick 06 1", vn(11))[0])
        # on the 17th the candidates of the 18th become tomorrow's
        self.assertIn("Đã chọn", self.run_command("/nailblog pick 06 1", vn(11, day=17))[0])

    def test_once_a_post_exists_the_choice_is_final(self):
        first, second = self.candidates(SLOT_06)
        self.state.add_post(SLOT_06, topic_id=first, state="review")
        reply = self.run_command("/nailblog pick 06 2", vn(11))
        self.assertIn("đã có bài", reply[0])
        self.assertEqual(self.state.get_topic(second)["state"], "proposed")

    def test_pick_works_without_a_config_object(self):
        first, _ = self.candidates(SLOT_06)
        self.assertIn("Đã chọn", execute(self.state, "pick", ("06:00", 1), now=vn(11))[0])
        self.assertEqual(self.state.get_topic(first)["state"], "approved")

    def test_ordinary_topic_commands_are_untouched_by_candidates(self):
        first, _ = self.candidates(SLOT_06)
        self.assertEqual(self.run_command("/nailblog topics ok " + str(first), vn(11))[0], f"Đã duyệt chủ đề: {first}.")


class OwnerOnlyTests(unittest.IsolatedAsyncioTestCase):
    """The commands reach state only through route_command, which admits the verified Owner's private DM alone."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.service = Service(settings(self.directory.name), clock=lambda: vn(11))
        self.topics = [self.service.state.add_topic(WEEK, f"Ứng viên {n}", "hot-trend", SLOT_06, 0.1) for n in (1, 2)]

    async def test_the_owner_can_pick_and_add_a_tag(self):
        event = dm(raw_text="/nailblog pick 06 2")
        self.assertTrue(await route_command(event, self.service, (OWNER,)))
        self.assertEqual(self.service.state.get_topic(self.topics[1])["state"], "approved")
        event.reply.assert_awaited_once()
        event = dm(raw_text="/nailblog trend add #chromenails 12M")
        await route_command(event, self.service, (OWNER,))
        self.assertEqual(self.service.state.tiktok_tags()[0]["tag"], "chromenails")

    async def test_anyone_else_changes_nothing_and_gets_no_reply(self):
        for change in ({"sender_id": 99}, {"chat_id": 99}, {"is_private": False, "is_group": True, "chat_id": -100},
                       {"out": True}, {"fwd_from": object()}):
            for text in ("/nailblog pick 06 2", "/nailblog trend add #chromenails 12M"):
                with self.subTest(change=list(change), text=text):
                    event = dm(raw_text=text, **change)
                    self.assertTrue(await route_command(event, self.service, (OWNER,)))
                    event.reply.assert_not_awaited()
        self.assertEqual([self.service.state.get_topic(i)["state"] for i in self.topics], ["proposed", "proposed"])
        self.assertEqual(self.service.state.tiktok_tags(), [])

    async def test_malformed_pick_and_trend_from_the_owner_get_the_usage_line(self):
        for text in ("/nailblog pick 06", "/nailblog trend add #tag"):
            event = dm(raw_text=text)
            await route_command(event, self.service, (OWNER,))
            event.reply.assert_awaited_once_with(USAGE, parse_mode=None, link_preview=False)
        self.assertEqual(self.service.state.tiktok_tags(), [])


if __name__ == "__main__":
    unittest.main()
