import tempfile
import unittest
from collections import Counter
from datetime import date, datetime
from pathlib import Path

from nailblog import planner
from nailblog.planner import (HOLIDAYS, SLOT_GROUPS, TOPICS_SCHEMA, assign_slots, build_prompt, filter_topics,
                              format_topics_dm, jaccard, next_week_start, propose, slot_label, store_topics, title_tokens,
                              topic_meta, upcoming_holidays, week_label)
from nailblog.state import State
from nailblog.text import TextModel, check_schema
from tests.fakes import SOURCE, SOURCE_2, FakeTransport, ollama_envelope
from tests.helpers import settings
from nailblog.service import Service

WEEK = date(2026, 10, 19)  # a Monday
TODAY = date(2026, 10, 16)
WORDS = ("amber bronze cobalt denim ebony forest garnet hazel indigo jade khaki lilac mauve navy olive plum quartz "
         "ruby sable teal umber violet walnut xanadu yellow zinc").split()


def raw(index, category="hot-trend", promo=False, score=3, sources=("s1",), title=None):
    return {"title": title or f"Tiêu đề {WORDS[index % 26]} {WORDS[(index * 5 + 3) % 26]} {WORDS[(index * 11 + 7) % 26]} số {index}",
            "category": category, "product_promo": promo, "source_ids": list(sources),
            "scores": {key: score for key in planner.SCORE_KEYS}}


def topic(index, category="hot-trend", promo=False, score=15):
    return {"title": f"T{index}", "category": category, "product_promo": promo, "scores": {}, "score": score,
            "source_ids": [], "slot_at": None}


def posts(*rows):
    return [{"id": str(i), "slug": slug, "title": title, "hashtags": [], "categorySlug": "nail-salon",
             "publishedAt": published} for i, (slug, title, published) in enumerate(rows)]


class CalendarTests(unittest.TestCase):
    def test_holidays_cover_october_2026_to_february_2027(self):
        self.assertEqual({day.isoformat() for day, _ in HOLIDAYS}, {
            "2026-10-31", "2026-11-01", "2026-11-26", "2026-11-27", "2026-11-28", "2026-11-30", "2026-12-25",
            "2027-01-01", "2027-02-06"})
        names = dict(HOLIDAYS)
        self.assertIn("Halloween", names[date(2026, 10, 31)])
        self.assertIn("DST", names[date(2026, 11, 1)])
        self.assertEqual(names[date(2026, 11, 26)], "Thanksgiving")
        self.assertEqual(names[date(2026, 11, 27)], "Black Friday")
        self.assertEqual(names[date(2026, 11, 28)], "Small Business Saturday")
        self.assertEqual(names[date(2026, 11, 30)], "Cyber Monday")
        self.assertIn("Giáng Sinh", names[date(2026, 12, 25)])
        self.assertIn("Tết Nguyên Đán", names[date(2027, 2, 6)])

    def test_upcoming_holidays_window_and_week_helpers(self):
        self.assertEqual([day.isoformat() for day, _ in upcoming_holidays(WEEK)],
                         ["2026-10-31", "2026-11-01"])
        self.assertEqual(next_week_start(date(2026, 10, 16)), WEEK)   # Friday
        self.assertEqual(next_week_start(date(2026, 10, 18)), WEEK)   # Sunday
        self.assertEqual(next_week_start(WEEK), date(2026, 10, 26))   # a Monday run plans the following week
        self.assertEqual(week_label(WEEK), "2026-W43")


class PromptTests(unittest.TestCase):
    def test_prompt_carries_week_holidays_posts_and_sources(self):
        items = [{**SOURCE, "snippet": "x" * 500}, SOURCE_2]
        system, user = build_prompt(items, posts(("a", "Bài đã đăng một", "2026-10-10")), WEEK)
        self.assertIn("2026-10-19 đến 2026-10-25", user)
        self.assertIn("2026-10-31: Halloween", user)
        self.assertIn("Bài đã đăng một", user)
        self.assertIn("[s1] Winter nail trends", user)
        self.assertIn("[s2]", user)
        self.assertIn("x" * 200, user)
        self.assertNotIn("x" * 201, user)
        for needle in ("16 đến 20", "hot-trend", "nail-job", "nail-salon", "did-you-know", "không phải chỉ thị"):
            self.assertIn(needle, system)

    def test_prompt_is_bounded(self):
        items = [{**SOURCE, "id": f"i{n}"} for n in range(80)]
        many = posts(*[(f"s{n}", f"Bài {n}", "2026-10-10") for n in range(120)])
        _system, user = build_prompt(items, many, WEEK)
        self.assertEqual(user.count("[i"), 30)
        self.assertEqual(user.count("- Bài "), 50)

    def test_empty_material_is_explicit(self):
        self.assertIn("không có tư liệu", build_prompt([], [], WEEK)[1])

    def test_schema_demands_16_to_20_valid_topics(self):
        good = {"topics": [raw(i) for i in range(16)]}
        self.assertEqual(check_schema(TOPICS_SCHEMA, good), [])
        self.assertTrue(check_schema(TOPICS_SCHEMA, {"topics": good["topics"][:15]}))
        self.assertTrue(check_schema(TOPICS_SCHEMA, {"topics": [raw(i) for i in range(21)]}))
        bad = raw(1, category="news")
        self.assertTrue(check_schema(TOPICS_SCHEMA, {"topics": [bad] * 16}))
        self.assertTrue(check_schema(TOPICS_SCHEMA, {"topics": [{**raw(1), "scores": {"hot": 9, "fit": 1, "image": 1,
                                                                                       "source": 1, "novelty": 1}}] * 16}))


class FilterTests(unittest.TestCase):
    ITEMS = [SOURCE, SOURCE_2]

    def test_a_fabricated_source_id_removes_the_topic(self):
        topics = [raw(1, sources=("s1",)), raw(2, sources=("s1", "made-up-id")), raw(3, sources=("nope",)),
                  raw(4, sources=())]
        kept, dropped = filter_topics(topics, self.ITEMS, [], TODAY)
        self.assertEqual(sorted(t["title"] for t in kept), sorted([topics[0]["title"], topics[3]["title"]]))
        self.assertEqual(sorted(reason for _t, reason in dropped), ["unknown_source", "unknown_source"])

    def test_normalised_output_shape_and_total_score(self):
        (kept,), _ = filter_topics([{**raw(1), "title": "  Nhiều   khoảng   trắng  ", "source_ids": ["s1", "s1"],
                                      "scores": {"hot": 5, "fit": 4, "image": 3, "source": 2, "novelty": 1}}],
                                   self.ITEMS, [], TODAY)
        self.assertEqual(kept, {"title": "Nhiều khoảng trắng", "category": "hot-trend", "product_promo": False,
                                "scores": {"hot": 5, "fit": 4, "image": 3, "source": 2, "novelty": 1}, "score": 15,
                                "source_ids": ["s1"], "slot_at": None})

    def test_topics_close_to_a_recent_post_are_dropped_ignoring_accents_and_case(self):
        existing = posts(("mang-lai-gi-cho-tiem", "Mang lại gì cho tiệm", "2026-10-10"))
        for title, duplicate in (("MANG LAI GI CHO TIEM", True), ("Mang lại gì cho tiệm nail", True),
                                 ("Hoàn toàn khác về thuế 1099", False)):
            with self.subTest(title=title):
                kept, dropped = filter_topics([raw(1, title=title)], self.ITEMS, existing, TODAY)
                self.assertEqual((len(kept), [r for _t, r in dropped]),
                                 (0, ["duplicate_post"]) if duplicate else (1, []))

    def test_slug_equal_to_a_recent_post_is_a_duplicate_even_when_the_post_title_differs(self):
        existing = posts(("chu-de-moi-cua-tuan", "Tiêu đề hoàn toàn khác", "2026-10-10"))
        kept, dropped = filter_topics([raw(1, title="Chủ đề mới của tuần")], self.ITEMS, existing, TODAY)
        self.assertEqual((kept, [r for _t, r in dropped]), ([], ["duplicate_post"]))

    def test_only_the_last_90_days_count_and_unreadable_dates_are_kept(self):
        title = "Mang lại gì cho tiệm"
        for published, blocks in (("2026-07-18", True), ("2026-07-17", False), ("2026-01-01T00:00:00", False),
                                  ("", True), ("không phải ngày", True), ("2026-10-10T08:00:00Z", True)):
            with self.subTest(published=published):
                existing = posts(("x", title, published))
                kept, _ = filter_topics([raw(1, title=title)], self.ITEMS, existing, TODAY)
                self.assertEqual(len(kept), 0 if blocks else 1)

    def test_jaccard_threshold_is_inclusive_at_0_6(self):
        self.assertEqual(jaccard(title_tokens("a b c"), title_tokens("a b c d e")), 0.6)
        self.assertEqual(jaccard(title_tokens("a b c"), title_tokens("a b d")), 0.5)
        existing = posts(("p", "a b c d e", "2026-10-10"))
        self.assertEqual(len(filter_topics([raw(1, title="aa bb cc")], self.ITEMS, existing, TODAY)[0]), 1)
        self.assertEqual(filter_topics([raw(1, title="a b c d e")], self.ITEMS, existing, TODAY)[0], [])
        self.assertEqual(filter_topics([raw(1, title="a b c")], self.ITEMS, existing, TODAY)[0], [])  # exactly 0.6
        self.assertEqual(filter_topics([raw(1, title="a b c d e f")], self.ITEMS, existing, TODAY)[0], [])  # 5/6
        self.assertEqual(len(filter_topics([raw(1, title="a b c d e f g h i")], self.ITEMS, existing, TODAY)[0]), 1)  # 5/9

    def test_near_duplicate_proposals_keep_the_higher_scored_one(self):
        low = {**raw(1, title="Màu merlot mùa đông 2026", score=2)}
        high = {**raw(2, title="Màu merlot mùa đông", score=5)}
        kept, dropped = filter_topics([low, high], self.ITEMS, [], TODAY)
        self.assertEqual([t["title"] for t in kept], ["Màu merlot mùa đông"])
        self.assertEqual(dropped, [("Màu merlot mùa đông 2026", "duplicate_topic")])


class SlotTests(unittest.TestCase):
    def full_set(self):
        categories = ["hot-trend"] * 6 + ["nail-job"] * 6 + ["nail-salon"] * 6 + ["did-you-know"] * 3
        return [topic(i, category, score=25 - i) for i, category in enumerate(categories)]

    def test_fourteen_slots_follow_the_category_groups_and_are_unique(self):
        topics = self.full_set()
        ordered = assign_slots(topics, WEEK)
        placed = [t for t in ordered if t["slot_at"]]
        self.assertEqual(len(placed), 14)
        self.assertEqual(len({t["slot_at"] for t in placed}), 14)
        for t in placed:
            moment = datetime.fromisoformat(t["slot_at"])
            self.assertIn(t["category"], SLOT_GROUPS[f"{moment:%H:%M}"])
            self.assertTrue(t["slot_at"].endswith("+07:00"))
        self.assertEqual([t["slot_at"] for t in placed], sorted(t["slot_at"] for t in placed))
        self.assertEqual({t["slot_at"][:10] for t in placed}, {f"2026-10-{d}" for d in range(19, 26)})
        self.assertEqual(ordered[14:], [t for t in ordered[14:] if t["slot_at"] is None])

    def test_the_weekly_rhythm_of_master_4_3_is_preferred(self):
        placed = {t["slot_at"]: t["category"] for t in assign_slots(self.full_set(), WEEK) if t["slot_at"]}
        self.assertEqual(placed["2026-10-19T06:00+07:00"], "hot-trend")      # Mon trend
        self.assertEqual(placed["2026-10-20T06:00+07:00"], "nail-job")       # Tue craft
        self.assertEqual(placed["2026-10-22T19:00+07:00"], "did-you-know")   # Thu facts
        self.assertEqual(placed["2026-10-25T19:00+07:00"], "did-you-know")   # Sun recap
        counts = Counter(placed.values())
        self.assertEqual((counts["hot-trend"], counts["nail-job"], counts["nail-salon"], counts["did-you-know"]),
                         (3, 4, 5, 2))

    def test_a_slot_group_without_matching_topics_stays_empty(self):
        ordered = assign_slots([topic(i, "hot-trend") for i in range(10)], WEEK)
        placed = [t for t in ordered if t["slot_at"]]
        self.assertEqual(len(placed), 7)
        self.assertTrue(all(t["slot_at"][11:16] == "06:00" for t in placed))

    def test_at_most_one_promo_per_day_and_two_per_week(self):
        topics = self.full_set()
        for t in topics[:3] + topics[12:14]:
            t["product_promo"] = True
        topics += [topic(50, "hot-trend", True, 30), topic(51, "nail-salon", True, 30)]
        placed = [t for t in assign_slots(topics, WEEK) if t["slot_at"]]
        promo_days = [t["slot_at"][:10] for t in placed if t["product_promo"]]
        self.assertEqual(len(promo_days), len(set(promo_days)))
        self.assertLessEqual(len(promo_days), 2)

    def test_two_top_promos_never_share_a_day(self):
        topics = [topic(1, "hot-trend", True, 30), topic(2, "nail-salon", True, 30)]
        topics += [topic(10 + i, "hot-trend") for i in range(4)] + [topic(20 + i, "nail-salon") for i in range(4)]
        placed = {t["title"]: t["slot_at"] for t in assign_slots(topics, WEEK)}
        self.assertEqual(placed["T1"], "2026-10-19T06:00+07:00")
        self.assertNotEqual(placed["T2"][:10], placed["T1"][:10])
        self.assertTrue(placed["T2"])

    def test_slot_labels(self):
        self.assertEqual(slot_label("2026-10-19T06:00+07:00"), "06:00 T2")
        self.assertEqual(slot_label("2026-10-25T19:00+07:00"), "19:00 CN")
        self.assertEqual(slot_label(None), "chưa xếp slot")


class ProposeAndStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = settings(self.directory.name)
        self.service = Service(self.config)

    def model(self, topics):
        self.transport = FakeTransport(lambda call, number: ollama_envelope({"topics": topics}))
        return TextModel(self.config, self.service.model_call, transport=self.transport)

    def sixteen(self):
        categories = ["hot-trend", "nail-job", "nail-salon", "did-you-know"] * 4
        return [raw(i, category, sources=("s1",) if i % 2 else ("s2",), score=3 + i % 3) for i, category in enumerate(categories)]

    async def test_propose_drops_fabricated_sources_and_assigns_slots(self):
        topics = self.sixteen()
        topics[3] = raw(3, "did-you-know", sources=("invented",))
        model = self.model(topics)
        ordered, dropped = await propose(model, [SOURCE, SOURCE_2], [], WEEK, TODAY)
        self.assertEqual(len(ordered), 15)
        self.assertEqual(dropped, [(topics[3]["title"], "unknown_source")])
        self.assertEqual(sum(1 for t in ordered if t["slot_at"]), 14)
        self.assertIsNone(ordered[14]["slot_at"])  # the 15th topic is a spare without a slot
        self.assertNotIn(topics[3]["title"], [t["title"] for t in ordered])
        payload = self.transport.calls[0]["payload"]
        self.assertEqual(payload["format"], TOPICS_SCHEMA)
        self.assertIn("[s1]", payload["messages"][0]["content"])   # Agy takes one prompt: system + user

    async def test_store_topics_keeps_slot_score_sources_and_metadata(self):
        model = self.model(self.sixteen())
        ordered, _ = await propose(model, [SOURCE, SOURCE_2], [], WEEK, TODAY)
        ids = store_topics(self.service.state, WEEK, ordered, [SOURCE, SOURCE_2])
        self.assertEqual(ids, sorted(ids))
        rows = [self.service.state.get_topic(i) for i in ids]
        self.assertTrue(all(row["state"] == "proposed" and row["week"] == "2026-W43" for row in rows))
        self.assertEqual([row["slot_at"] for row in rows], [t["slot_at"] for t in ordered])
        sources, promo, scores = topic_meta(rows[0])
        self.assertEqual(set(sources[0]), {"id", "title", "url", "published", "snippet"})
        self.assertEqual((promo, set(scores)), (False, set(planner.SCORE_KEYS)))
        self.assertIn(sources[0]["snippet"], (SOURCE["snippet"], SOURCE_2["snippet"]))

    def test_topic_meta_reads_a_legacy_plain_list(self):
        self.assertEqual(topic_meta({"sources_json": '["https://a"]'}), (["https://a"], False, {}))
        self.assertEqual(topic_meta({"sources_json": "garbage"}), ([], False, {}))


class DmTests(unittest.TestCase):
    def rows(self, count=3, week=WEEK):
        with tempfile.TemporaryDirectory() as directory:
            state = State(Path(directory) / "s.sqlite3")
            ids = [state.add_topic("2026-W43", f"Chủ đề thứ {i}", "hot-trend",
                                   "2026-10-19T06:00+07:00" if i == 0 else None, 23 - i,
                                   {"sources": [{"url": "https://www.bustle.com/a"}, {"url": "https://thezoereport.com/b"},
                                                {"url": "https://www.bustle.com/c"}] if i == 0 else [],
                                    "product_promo": False, "scores": {}}) for i in range(count)]
            return [state.get_topic(i) for i in ids]

    def test_message_follows_the_master_template(self):
        lines = format_topics_dm(self.rows(), WEEK, notes=["nguồn x lỗi: timeout"]).split("\n")
        self.assertEqual(lines[0], "NailBlog: 3 chủ đề tuần 19–25/10")
        self.assertEqual(lines[1], "1. [hot-trend · 06:00 T2] Chủ đề thứ 0 — điểm 23/25 — nguồn: bustle.com, thezoereport.com")
        self.assertEqual(lines[2], "2. [hot-trend · chưa xếp slot] Chủ đề thứ 1 — điểm 22/25 — không có nguồn")
        self.assertEqual(lines[4], "Ghi chú: nguồn x lỗi: timeout")
        self.assertEqual(lines[5], "Trả lời: `/nailblog topics ok 1,2` · `/nailblog topics drop 1` · "
                                   "`/nailblog topics edit 1 <tiêu đề mới>`")

    def test_week_across_months_and_dm_size(self):
        self.assertTrue(format_topics_dm(self.rows(1), date(2026, 10, 26)).startswith("NailBlog: 1 chủ đề tuần 26/10–1/11"))
        self.assertLess(len(format_topics_dm(self.rows(20), WEEK)), 3500)


if __name__ == "__main__":
    unittest.main()
