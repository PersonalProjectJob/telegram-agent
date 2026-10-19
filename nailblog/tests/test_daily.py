"""The daily topic locking flow (TD-8): 10:00 propose, 13:00 decide, article writing, 20:00 preview, and the
idempotence and failure behaviour of the whole day. Fake transport and opener only."""
import os
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

from nailblog import daily
from nailblog.daily import CANDIDATES_PER_SLOT, choose_candidates
from nailblog.planner import SLOT_GROUPS
from nailblog.state import State
from nailblog.text import TextModelError
from nailblog.trends import TrendBoard
from tests.daily_support import (BLOGS_URL, FEED_1, FEED_2, ITEM_IDS, SLOT_06, SLOT_19, TARGET, TODAY, TRENDS_URL, WEEK,
                                 DailyBase, Model, at, candidate, meta)
from tests.fakes import FakeOpener, trending_feed


class ProposeTests(DailyBase):
    async def propose(self):
        return await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)

    async def test_it_creates_four_candidates_two_per_slot_in_the_right_category_groups(self):
        outcome = await self.propose()
        self.assertEqual((outcome.step, outcome.created, outcome.complete), ("propose", True, True))
        rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertEqual([row["slot_at"] for row in rows].count(SLOT_06), CANDIDATES_PER_SLOT)
        self.assertEqual([row["slot_at"] for row in rows].count(SLOT_19), CANDIDATES_PER_SLOT)
        for row in rows:
            group = SLOT_GROUPS["06:00" if row["slot_at"] == SLOT_06 else "19:00"]
            self.assertIn(row["category"], group)
            self.assertEqual((row["state"], row["week"]), ("proposed", WEEK))
        self.assertEqual({row["category"] for row in self.rows(SLOT_06)} - {"hot-trend", "nail-job"}, set())
        self.assertEqual({row["category"] for row in self.rows(SLOT_19)} - {"nail-salon", "did-you-know"}, set())
        self.assertLessEqual(sum(meta(row)["product_promo"] for row in rows), 1)

    async def test_bad_candidates_are_dropped_and_reported(self):
        outcome = await self.propose()
        titles = [row["title"] for row in self.rows()]
        self.assertNotIn("Mang lại gì cho tiệm", titles)                 # a published post of the last 90 days
        self.assertNotIn("Tin lạ không có nguồn nào", titles)            # a source id that was never supplied
        self.assertEqual(sum(meta(row)["product_promo"] for row in self.rows()), 1)   # one product post at most
        self.assertNotIn("Giới thiệu NEXORA TOUCH cho tiệm nhỏ", titles)
        self.assertIn("Ghi chú: đã loại 2 ứng viên", outcome.text)

    async def test_the_trend_score_orders_the_candidates_and_is_stored_with_its_parts(self):
        await self.propose()
        first, second = self.rows(SLOT_06)
        self.assertEqual((first["title"], second["title"]),
                         ("Chrome nails mùa lễ hội cho khách nữ", "Cat eye nails nhẹ nhàng cho ngày đông"))
        self.assertGreater(first["score"], second["score"])
        trend = meta(first)["trend"]
        self.assertEqual((trend["term"], trend["total"], trend["traffic"]), ("chrome nails", first["score"], 20000))
        self.assertGreater(trend["hot"], 0)
        self.assertEqual(meta(first)["sources"][0]["id"], ITEM_IDS[0])
        self.assertEqual(meta(first)["keyword"], "chrome nails")

    async def test_the_dm_has_the_trend_table_the_numbered_candidates_and_the_instructions(self):
        text = (await self.propose()).text
        self.assertIn("NailBlog 16/10: chọn chủ đề cho ngày mai 17/10 (T7)", text)
        self.assertIn("Trend hôm nay (Google Trending US, lọc nail/beauty):", text)
        self.assertIn("chrome nails — 20K+ — điểm", text)
        self.assertTrue(text.splitlines()[3].startswith("1. "))
        self.assertIn("taylor swift", text)                              # relevant through a news title (manicure)
        self.assertIn("[chrome]", text)                                  # context words label, they do not select
        for other in ("nfl scores", "halloween costumes", "sephora sale"):   # "beauty" is only a context word
            self.assertNotIn(other, text)
        for label in ("06·1", "06·2", "19·1", "19·2"):
            self.assertIn(label, text)
        self.assertNotIn("06·3", text)
        self.assertIn("Chrome nails mùa lễ hội cho khách nữ — điểm trend", text)
        self.assertIn("nguồn: bustle.com", text)
        self.assertIn("/nailblog pick 06 1", text)
        self.assertIn("hạn 13:00", text)
        self.assertIn("https://ads.tiktok.com/business/creativecenter", text)
        self.assertIn("/nailblog trend add #tag", text)
        self.assertLessEqual(len(text), 3500)

    async def test_the_trend_table_has_at_most_eight_lines(self):
        many = trending_feed([(f"glitter nails {number}", f"{number + 1}000+", []) for number in range(12)])
        self.opener.routes[TRENDS_URL] = many
        text = (await self.propose()).text
        section = text.split("Trend hôm nay (Google Trending US, lọc nail/beauty):")[1].split("Ứng viên slot")[0]
        numbered = [line for line in section.splitlines() if line[:1].isdigit()]
        self.assertEqual(len(numbered), 8)
        self.assertTrue(numbered[0].startswith("1. glitter nails 11 — 12K+"))   # the busiest first

    async def test_it_reads_with_get_only_the_feeds_the_post_list_and_the_trending_feed(self):
        await self.propose()
        self.assertEqual(sorted(call["url"] for call in self.opener.calls),
                         sorted([FEED_1, FEED_2, BLOGS_URL, TRENDS_URL]))
        self.assertTrue(all(call["method"] == "GET" and call["data"] is None for call in self.opener.calls))
        self.assertEqual(self.model.planner_calls, 1)
        google = [row for row in self.state.trend_snapshots(0) if row["source"] == "google_trends_us"]
        rss = [row for row in self.state.trend_snapshots(0) if row["source"].startswith("rss:")]
        self.assertEqual((len(google), len(rss)), (5, 3))
        self.assertTrue((Path(self.directory.name) / "blog-cache.json").exists())

    async def test_running_it_twice_changes_nothing_and_sends_one_dm(self):
        first = await self.propose()
        second = await self.propose()
        self.assertEqual((second.created, second.text), (False, first.text))
        self.assertEqual((len(self.rows()), self.model.planner_calls, len(self.opener.calls)), (4, 1, 4))
        self.assertEqual(await daily.flush(self.service, TODAY), 1)
        self.assertEqual(await daily.flush(self.service, TODAY), 0)
        self.sent.assert_awaited_once_with(first.text[:3500])
        self.assertEqual(self.state.daily_get("2026-10-16", "propose")["message"], first.text)

    async def test_candidates_stored_by_an_interrupted_run_are_reused_without_a_second_model_call(self):
        first = await self.propose()
        with self.state.connection() as db:
            db.execute("DELETE FROM daily_runs")
        again = await self.propose()
        self.assertTrue(again.created)
        self.assertEqual((len(self.rows()), self.model.planner_calls), (4, 1))
        self.assertIn("khôi phục ứng viên đã lưu", again.text)
        self.assertIn("Chrome nails mùa lễ hội cho khách nữ", again.text)

    async def test_a_failing_trending_feed_is_a_note_and_the_rest_still_works(self):
        self.opener.routes[TRENDS_URL] = TimeoutError()
        outcome = await self.propose()
        self.assertIn("Ghi chú: Google Trending lỗi: timeout", outcome.text)
        self.assertEqual(len(self.rows()), 4)

    async def test_a_slot_without_valid_candidates_is_said_so(self):
        self.model.candidates = [candidate(title, cat) for title, cat in (
            ("Chrome nails mùa lễ hội", "hot-trend"), ("Velvet nails nhẹ nhàng ngày đông", "hot-trend"),
            ("Lộ trình học nghề cho người mới", "nail-job"), ("Xin việc thợ nail tại Texas", "nail-job"))]
        outcome = await self.propose()
        self.assertEqual(len(self.rows(SLOT_06)), 2)
        self.assertEqual(self.rows(SLOT_19), [])
        self.assertIn("Ghi chú: slot 19:00 chỉ có 0/2 ứng viên", outcome.text)
        self.assertIn("(không có ứng viên hợp lệ)", outcome.text)

    async def test_no_product_candidate_when_the_day_already_has_a_product_post(self):
        self.state.add_post(SLOT_06, state="review", draft={"product_promo": True})
        await self.propose()
        self.assertEqual(sum(meta(row)["product_promo"] for row in self.rows()), 0)

    async def test_a_model_failure_stores_nothing_and_leaves_the_step_open(self):
        def explode(call, number):
            raise OSError("model down")
        self.model.transport.respond = explode
        with self.assertRaises(TextModelError):
            await self.propose()
        self.assertEqual((self.rows(), self.state.daily_get("2026-10-16", "propose")), ([], None))


class ChooseCandidatesTests(unittest.TestCase):
    """choose_candidates on plain data: category preference by weekday, trend order, promo limit."""

    def board(self):
        import tempfile
        from nailblog.config import TrendsConfig
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return TrendBoard(State(Path(directory.name) / "s.sqlite3"), TrendsConfig(), at("10:00"))

    def raw(self, *items):
        return [{"title": t, "category": c, "product_promo": p, "keyword": "", "source_ids": []} for t, c, p in items]

    def test_a_sunday_prefers_nail_job_at_06_and_did_you_know_at_19(self):
        sunday = date(2026, 10, 18)
        raw = self.raw(("Chrome nails thịnh hành", "hot-trend", False), ("Nghề nail cho người mới", "nail-job", False),
                       ("Thuế tiệm nail cần biết", "nail-salon", False), ("Gel bền bao lâu vậy", "did-you-know", False),
                       ("Học nghề nail ở đâu tốt", "nail-job", False), ("Khử trùng dụng cụ đúng cách", "did-you-know", False))
        chosen, dropped = choose_candidates(raw, [], [], TODAY, sunday, self.board(), promo_taken=False)
        self.assertEqual([c["category"] for c in chosen["06:00"]], ["nail-job", "nail-job"])
        self.assertEqual([c["category"] for c in chosen["19:00"]], ["did-you-know", "did-you-know"])
        self.assertEqual(dropped, [])

    def test_a_group_with_too_few_of_the_preferred_category_fills_from_the_other_category(self):
        raw = self.raw(("Chrome nails thịnh hành", "hot-trend", False), ("Nghề nail cho người mới", "nail-job", False))
        chosen, _ = choose_candidates(raw, [], [], TODAY, TARGET, self.board(), promo_taken=False)
        self.assertEqual([c["category"] for c in chosen["06:00"]], ["hot-trend", "nail-job"])
        self.assertEqual(chosen["19:00"], [])

    def test_at_most_one_product_candidate_and_none_when_the_day_has_one(self):
        raw = self.raw(("Giới thiệu Nexora cho tiệm", "nail-salon", True), ("Nexora Touch cho thợ mới học", "nail-salon", True),
                       ("Quản lý lịch hẹn đông khách", "nail-salon", False), ("Sức khỏe thợ nail", "did-you-know", False))
        chosen, dropped = choose_candidates(raw, [], [], TODAY, TARGET, self.board(), promo_taken=False)
        self.assertEqual(sum(c["product_promo"] for rows in chosen.values() for c in rows), 1)
        self.assertIn(("Nexora Touch cho thợ mới học", "promo_limit"), dropped)
        chosen, _ = choose_candidates(raw, [], [], TODAY, TARGET, self.board(), promo_taken=True)
        self.assertEqual(sum(c["product_promo"] for rows in chosen.values() for c in rows), 0)

    def test_similar_titles_are_not_both_offered(self):
        raw = self.raw(("Chrome nails mùa đông cho khách", "hot-trend", False),
                       ("Chrome nails mùa đông cho khách nữ", "hot-trend", False),
                       ("Nghề nail cho người mới", "nail-job", False))
        chosen, dropped = choose_candidates(raw, [], [], TODAY, TARGET, self.board(), promo_taken=False)
        self.assertEqual(len(chosen["06:00"]), 2)
        self.assertIn("duplicate_candidate", [reason for _, reason in dropped])


class DecideTests(DailyBase):
    async def setUp_proposed(self):
        await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)
        self.now = at("13:05")

    async def decide(self):
        return await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)

    def states(self, slot):
        return [row["state"] for row in self.rows(slot)]

    async def test_a_pick_is_kept_and_a_slot_without_one_takes_the_best_candidate_as_auto(self):
        await self.setUp_proposed()
        self.now = at("12:00")
        self.assertIn("Đã chọn", self.service.command("pick", ("06:00", 2))[0])
        self.now = at("13:05")
        outcome = await self.decide()
        picked = self.rows(SLOT_06)[1]
        self.assertEqual(self.states(SLOT_06), ["dropped", "approved"])
        self.assertEqual(meta(self.state.get_topic(picked["id"]))["chosen"],
                         {"day": "2026-10-17", "slot": "06:00", "origin": "pick", "auto": False})
        self.assertIn(f"06:00 — {picked['title']} (Owner đã chọn)", outcome.text)
        best = max(self.rows(SLOT_19), key=lambda row: (row["score"], -row["id"]))
        self.assertEqual([row["state"] for row in self.rows(SLOT_19) if row["id"] == best["id"]], ["approved"])
        self.assertEqual(sorted(self.states(SLOT_19)), ["approved", "dropped"])
        self.assertTrue(meta(self.state.get_topic(best["id"]))["chosen"]["auto"])
        self.assertIn(f"19:00 — {best['title']} (tự chọn: auto, điểm cao nhất, vẫn phải duyệt bài)", outcome.text)

    async def test_a_backlog_topic_of_the_right_group_comes_before_an_auto_candidate(self):
        await self.setUp_proposed()
        backlog = self.state.add_topic("2026-W43", "Backlog quản lý tiệm", "nail-salon", "2026-10-22T19:00+07:00", 20,
                                       state="approved")
        outcome = await self.decide()
        row = self.state.get_topic(backlog)
        self.assertEqual((row["state"], row["slot_at"]), ("approved", SLOT_19))
        self.assertEqual(meta(row)["chosen"], {"day": "2026-10-17", "slot": "19:00", "origin": "backlog", "auto": False})
        self.assertEqual(self.states(SLOT_19), ["dropped", "dropped"])
        self.assertIn("19:00 — Backlog quản lý tiệm (lấy từ backlog tuần)", outcome.text)
        self.assertEqual(daily.chosen_topic(self.state, TARGET, "19:00")["id"], backlog)

    async def test_backlog_choice_order_and_what_is_not_eligible(self):
        await self.setUp_proposed()
        add = lambda title, cat, slot, score, state="approved": self.state.add_topic(  # noqa: E731
            "2026-W43", title, cat, slot, score, state=state)
        wrong_group = add("Sai nhóm hot-trend", "hot-trend", None, 99)
        not_approved = add("Chưa duyệt nail-salon", "nail-salon", SLOT_19, 99, "proposed")
        used = add("Đã có bài nail-salon", "nail-salon", "2026-10-24T19:00+07:00", 99)
        self.state.add_post("2026-10-24T19:00+07:00", topic_id=used, state="held")
        low = add("Điểm thấp did-you-know", "did-you-know", None, 5)
        planned_elsewhere = add("Điểm cao nail-salon", "nail-salon", "2026-10-23T19:00+07:00", 18)
        planned_here = add("Xếp đúng slot did-you-know", "did-you-know", SLOT_19, 1)
        await self.decide()
        self.assertEqual(daily.chosen_topic(self.state, TARGET, "19:00")["id"], planned_here)   # the slot match wins
        self.assertEqual(daily.chosen_topic(self.state, TARGET, "06:00")["id"], wrong_group)   # fits 06:00, not 19:00
        for untouched in (low, planned_elsewhere):
            self.assertIsNone(daily.meta_of(self.state.get_topic(untouched)).get("chosen"))
        self.assertEqual(self.state.get_topic(not_approved)["state"], "proposed")

    async def test_a_backlog_topic_is_never_used_for_two_days(self):
        await self.setUp_proposed()
        only = self.state.add_topic("2026-W43", "Chỉ một bài", "nail-salon", None, 10, state="approved")
        await self.decide()
        self.assertEqual(daily.chosen_topic(self.state, TARGET, "19:00")["id"], only)
        self.assertIsNone(daily._backlog(self.state, date(2026, 10, 18), "19:00"))

    async def test_nothing_at_all_is_said_plainly(self):
        outcome = await self.decide()
        self.assertIn("06:00 — chưa có chủ đề (không có ứng viên, backlog tuần trống)", outcome.text)
        self.assertIn("19:00 — chưa có chủ đề", outcome.text)

    async def test_deciding_twice_changes_nothing(self):
        await self.setUp_proposed()
        first = await self.decide()
        before = [(row["id"], row["state"]) for row in self.state.list_topics(("proposed", "approved", "dropped"))]
        second = await self.decide()
        self.assertEqual((second.created, second.text), (False, first.text))
        self.assertEqual(before, [(row["id"], row["state"]) for row in self.state.list_topics(("proposed", "approved", "dropped"))])

    async def test_after_the_decision_a_pick_is_refused(self):
        await self.setUp_proposed()
        await self.decide()
        self.assertIn("quá hạn", self.service.command("pick", ("06:00", 1))[0])

    async def test_decide_makes_no_model_call_and_no_network_call(self):
        await self.setUp_proposed()
        calls = (len(self.model.transport.calls), len(self.opener.calls))
        await self.decide()
        self.assertEqual(calls, (len(self.model.transport.calls), len(self.opener.calls)))


class WriteAndPreviewTests(DailyBase):
    async def prepared(self, pick=True):
        await daily.run_step(self.service, "propose", TODAY, self.service.text, opener=self.opener)
        if pick:
            self.now = at("12:00")
            self.service.command("pick", ("06:00", 1))
        self.now = at("13:05")
        await daily.run_step(self.service, "decide", TODAY, self.service.text, opener=self.opener)

    async def step(self, name):
        return await daily.run_step(self.service, name, TODAY, self.service.text, opener=self.opener)

    async def test_each_chosen_topic_gets_a_post_in_review_with_files_in_the_state_directory(self):
        await self.prepared()
        outcome = await self.step("write")
        self.assertEqual((outcome.complete, outcome.created), (True, True))
        posts = self.posts()
        self.assertEqual([(post["slot_at"], post["state"], post["reason"]) for post in posts],
                         [(SLOT_06, "review", "gate pass"), (SLOT_19, "review", "gate pass")])
        base = Path(self.directory.name) / "previews" / "2026-10-17"
        for post, folder in zip(posts, ("0600", "1900")):
            self.assertEqual(sorted(os.listdir(base / folder)), ["article.html", "article.json"])
            document = __import__("json").loads(post["draft_json"])
            self.assertEqual(document["files"], {"json": str(base / folder / "article.json"),
                                                 "html": str(base / folder / "article.html")})
            self.assertEqual((document["quality"]["verdict"], document["topic_id"], document["mode"]),
                             ("pass", post["topic_id"], "preview"))
            self.assertEqual(len(post["fingerprint"]), 16)
        self.assertEqual(self.model.outlines, 2)
        self.assertEqual(self.posts()[0]["topic_id"], daily.chosen_topic(self.state, TARGET, "06:00")["id"])

    async def test_writing_twice_creates_no_second_post_and_no_model_call(self):
        await self.prepared()
        await self.step("write")
        calls = len(self.model.transport.calls)
        again = await self.step("write")
        self.assertFalse(again.created)
        self.assertEqual((len(self.posts()), len(self.model.transport.calls)), (2, calls))

    async def test_a_topic_that_already_has_a_post_is_not_written_again(self):
        await self.prepared()
        topic = daily.chosen_topic(self.state, TARGET, "06:00")
        self.state.add_post(SLOT_06, topic_id=topic["id"], state="held")
        await self.step("write")
        self.assertEqual([post["topic_id"] for post in self.posts()].count(topic["id"]), 1)
        self.assertEqual(self.model.outlines, 1)                     # only the 19:00 article was written

    async def test_an_article_with_an_unsupported_number_is_held_not_reviewed(self):
        self.model.hold_article = 2
        await self.prepared()
        await self.step("write")
        self.assertEqual([post["state"] for post in self.posts()], ["review", "held"])
        self.assertEqual(self.posts()[1]["reason"], "gate hold: facts")

    async def test_a_model_failure_leaves_the_step_open_and_a_later_pass_completes_it(self):
        await self.prepared()
        self.model.fail_articles = True
        failed = await self.step("write")
        self.assertEqual((failed.complete, self.posts(), self.state.daily_get("2026-10-16", "write")), (False, [], None))
        self.assertEqual(self.state.get_topic(daily.chosen_topic(self.state, TARGET, "06:00")["id"])["state"], "approved")
        self.model.fail_articles = False
        done = await self.step("write")
        self.assertEqual((done.complete, len(self.posts())), (True, 2))

    async def test_no_chosen_topic_means_nothing_to_write_and_the_step_is_done(self):
        outcome = await self.step("write")
        self.assertEqual((outcome.complete, outcome.text, self.posts()), (True, "", []))

    async def test_the_preview_dm_has_one_block_per_slot_with_everything_the_owner_needs(self):
        await self.prepared()
        await self.step("write")
        self.now = at("20:05")
        text = (await self.step("preview")).text
        first, second = self.posts()
        self.assertIn("NailBlog 16/10 20:00: xem trước bài ngày mai 17/10", text)
        for post, deadline in ((first, "04:00 17/10"), (second, "17:00 17/10")):
            document = __import__("json").loads(post["draft_json"])
            self.assertIn(f"bài #{post['id']} [review]", text)
            self.assertIn("Tiêu đề: " + document["title"], text)
            self.assertIn("Tóm tắt: " + document["summary"], text)
            self.assertIn(f"Số từ: {document['words']} · Gate: pass", text)
            self.assertIn("File xem trước: " + document["files"]["html"], text)
            self.assertIn(f"Hạn duyệt: {deadline} (trước giờ đăng 2 tiếng)", text)
            self.assertIn(f"Duyệt: /nailblog approve {post['id']}", text)
        self.assertIn("Slot 06:00 ngày 17/10", text)
        self.assertIn("Slot 19:00 ngày 17/10", text)
        self.assertIn("không có link website", text)
        self.assertIn("chưa đăng gì lên nailhub.ai", text)
        self.assertLessEqual(len(text), 3500)

    async def test_the_approve_command_in_the_dm_really_schedules_the_post(self):
        await self.prepared()
        await self.step("write")
        post = self.posts()[0]
        self.assertEqual(self.service.command("approve", (post["id"],)), [f"Bài #{post['id']}: đã lên lịch."])
        self.assertEqual(self.state.get_post(post["id"])["state"], "scheduled")

    async def test_a_held_post_shows_why_and_offers_no_approve_command(self):
        self.model.hold_article = 2
        await self.prepared()
        await self.step("write")
        held = self.posts()[1]
        text = (await self.step("preview")).text
        self.assertIn(f"bài #{held['id']} [held]", text)
        self.assertIn("ĐANG GIỮ (gate hold: facts)", text)
        self.assertIn("Gate: hold (facts)", text)
        self.assertNotIn(f"/nailblog approve {held['id']}", text)
        self.assertIn(f"/nailblog approve {self.posts()[0]['id']}", text)

    async def test_slots_without_a_post_or_a_topic_are_named(self):
        await self.prepared()
        self.model.fail_articles = True
        await self.step("write")
        text = (await self.step("preview")).text
        self.assertIn("chưa có bài (viết lỗi hoặc đang chờ), sẽ không lên lịch", text)
        self.assertNotIn("/nailblog approve", text)
        other = await daily.preview_dm(type("S", (), {"state": State(Path(self.directory.name) / "x.sqlite3"),
                                                     "config": self.config})(), None, TODAY)
        self.assertIn("Slot 06:00 ngày 17/10: chưa có chủ đề.", other[0])

    async def test_the_preview_step_is_recorded_and_sent_once(self):
        await self.prepared()
        await self.step("write")
        first = await self.step("preview")
        self.assertEqual(await daily.flush(self.service, TODAY), 3)       # propose, decide and preview, each once
        self.assertEqual(await daily.flush(self.service, TODAY), 0)
        self.assertEqual(self.sent.await_args_list[-1].args[0], first.text[:3500])


if __name__ == "__main__":
    unittest.main()
