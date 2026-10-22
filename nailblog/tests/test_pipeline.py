"""plan-week and preview through Service and the CLI: sources, planner, writer, facts and quality together,
with fake transport/opener. Nothing here may touch the network or write to the website."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from nailblog import __main__ as cli
from nailblog.__main__ import Runtime
from nailblog.service import PreviewError, Service
from nailblog.sources import SourceError, parse_feed
from nailblog.state import State
from nailblog.text import TextModel
from nailblog.schedule import VN_TZ
from tests import fakes
from tests.fakes import ATOM, BLOGS_JSON, RSS, FakeOpener, FakeTransport, ollama_envelope
from tests.helpers import load, write_config

FEED_1, FEED_2 = "https://www.bustle.com/rss", "https://tdlr.texas.gov/feed"
BLOGS_URL = "https://api.nailhub.ai/api/v1/blogs?pageNumber=1&pageSize=50"
TODAY = date(2026, 10, 16)
NOW = datetime(2026, 10, 16, 12, 0, tzinfo=VN_TZ).timestamp()
SOURCES = f'feeds = ["{FEED_1}", "{FEED_2}"]'
ITEM_IDS = [item["id"] for item in parse_feed(RSS) + parse_feed(ATOM)]
TWO_POSTS = [{"slug": "bai-cu-mot", "title": "Ghi chép tiệm nail số một", "categorySlug": "nail-salon", "hashtags": [],
              "publishedAt": "2026-10-01", "id": "1"},
             {"slug": "bai-cu-hai", "title": "Ghi chép tiệm nail số hai", "categorySlug": "nail-salon", "hashtags": [],
              "publishedAt": "2026-10-02", "id": "2"}]


def opener(**changes):
    routes = {FEED_1: RSS, FEED_2: ATOM, BLOGS_URL: BLOGS_JSON}
    routes.update(changes)
    return FakeOpener(routes)


def topics_transport(count=18, titles=None, cited=True):
    categories = ["hot-trend", "nail-job", "nail-salon", "did-you-know"]
    topics = [{"title": (titles or {}).get(i, f"Tiêu đề {chr(97 + i)}{chr(97 + (i * 7) % 26)} số {i}"),
               "category": categories[i % 4], "product_promo": i == 2, "source_ids": [ITEM_IDS[i % 3]] if cited else [],
               "scores": {k: 3 + i % 3 for k in ("hot", "fit", "image", "source", "novelty")}} for i in range(count)]
    return FakeTransport(lambda call, number: ollama_envelope({"topics": topics}))


class PlanWeekTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = load(self.directory.name, sources=SOURCES)
        self.service = Service(self.config, clock=lambda: NOW)

    def model(self, transport):
        return TextModel(self.config, self.service.model_call, transport=transport)

    async def test_dry_run_reads_plans_stores_and_never_sends(self):
        net, transport = opener(), topics_transport()
        self.service.notice_send = AsyncMock()
        outcome = await self.service.plan_week(self.model(transport), opener=net, dry_run=True)
        self.assertEqual((outcome.created, outcome.sent), (True, False))
        self.service.notice_send.assert_not_awaited()
        self.assertEqual(len(outcome.topic_ids), 18)
        rows = [self.service.state.get_topic(i) for i in outcome.topic_ids]
        self.assertTrue(all(row["state"] == "proposed" and row["week"] == "2026-W43" for row in rows))
        self.assertEqual(sum(1 for row in rows if row["slot_at"]), 14)
        self.assertTrue(outcome.text.startswith("NailBlog: 18 chủ đề tuần 19–25/10\n1. ["))
        self.assertIn(f"{outcome.topic_ids[0]}. [", outcome.text)
        self.assertEqual(sorted(call["url"] for call in net.calls), sorted([FEED_1, FEED_2, BLOGS_URL]))
        self.assertTrue(all(call["method"] == "GET" and call["data"] is None for call in net.calls))
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(self.service.state.control()["last_result"], "plan-week 2026-W43: 18 chủ đề")

    async def test_the_dm_is_sent_once_when_a_sender_is_bound_and_a_second_run_is_refused(self):
        net, transport = opener(), topics_transport()
        self.service.notice_send = AsyncMock()
        outcome = await self.service.plan_week(self.model(transport), opener=net)
        self.assertTrue(outcome.sent)
        self.service.notice_send.assert_awaited_once_with(outcome.text[:3500])
        again = await self.service.plan_week(self.model(transport), opener=net)
        self.assertEqual((again.created, again.sent, again.topic_ids), (False, False, []))
        self.assertIn("2026-W43 đã có chủ đề", again.text)
        self.assertEqual((len(transport.calls), len(net.calls)), (1, 3))
        self.assertEqual(len(self.service.state.list_topics()), 18)

    async def test_without_a_sender_the_dm_is_only_returned(self):
        outcome = await self.service.plan_week(self.model(topics_transport()), opener=opener())
        self.assertEqual((outcome.sent, outcome.created), (False, True))

    async def test_the_week_is_the_next_monday_even_when_run_on_a_monday(self):
        monday = datetime(2026, 10, 19, 9, 0, tzinfo=VN_TZ).timestamp()
        service = Service(self.config, state=self.service.state, clock=lambda: monday)
        outcome = await service.plan_week(self.model(topics_transport()), opener=opener(), dry_run=True)
        self.assertIn("tuần 26/10–1/11", outcome.text)
        self.assertEqual(self.service.state.get_topic(outcome.topic_ids[0])["week"], "2026-W44")

    async def test_a_failing_feed_is_reported_in_the_dm_and_the_rest_still_work(self):
        net = opener(**{FEED_2: TimeoutError()})
        outcome = await self.service.plan_week(self.model(topics_transport()), opener=net, dry_run=True)
        self.assertIn("Ghi chú: nguồn tdlr.texas.gov lỗi: timeout", outcome.text)
        self.assertEqual(len(outcome.topic_ids) > 0, True)

    async def test_when_every_feed_fails_last_weeks_material_is_used(self):
        await self.service.plan_week(self.model(topics_transport()), opener=opener(), dry_run=True)
        net = opener(**{FEED_1: URLError_(), FEED_2: URLError_()})
        outcome = await self.service.plan_week(
            self.model(topics_transport()), opener=net, dry_run=True,
            today=date(2026, 10, 23))
        self.assertIn("dùng tư liệu đã lưu từ lần trước", outcome.text)
        self.assertIn("nguồn www.bustle.com lỗi: unreachable", outcome.text)

    async def test_published_posts_failing_falls_back_to_the_cache_or_stops(self):
        with self.assertRaises(SourceError):
            await self.service.plan_week(self.model(topics_transport()), opener=opener(**{BLOGS_URL: TimeoutError()}))
        self.assertEqual(self.service.state.list_topics(), [])  # nothing half-written
        await self.service.plan_week(self.model(topics_transport()), opener=opener(), dry_run=True)
        for row in self.service.state.list_topics():
            self.service.state.set_topic_state(row["id"], "dropped")
        outcome = await self.service.plan_week(self.model(topics_transport()), opener=opener(**{BLOGS_URL: TimeoutError()}),
                                               dry_run=True)
        self.assertIn("không đọc được bài đã đăng (timeout), dùng danh sách đã lưu", outcome.text)

    async def test_topics_repeating_a_recent_post_are_removed_and_reported(self):
        transport = topics_transport(titles={3: "Mang lại gì cho tiệm", 4: "Bài cũ hơn 90 ngày"})
        outcome = await self.service.plan_week(self.model(transport), opener=opener(), dry_run=True)
        titles = [self.service.state.get_topic(i)["title"] for i in outcome.topic_ids]
        self.assertNotIn("Mang lại gì cho tiệm", titles)
        self.assertIn("Bài cũ hơn 90 ngày", titles)  # older than 90 days: allowed again
        self.assertIn("Ghi chú: đã loại 1 chủ đề", outcome.text)

    async def test_missing_feed_configuration_and_short_plans_are_explained(self):
        config = load(self.directory.name)
        service = Service(config, state=self.service.state, clock=lambda: NOW)
        model = TextModel(config, service.model_call, transport=topics_transport(16, cited=False))
        outcome = await service.plan_week(model, opener=opener(), dry_run=True)
        self.assertIn("chưa cấu hình nguồn RSS", outcome.text)
        self.assertEqual(len(outcome.topic_ids), 16)


def URLError_():
    from urllib.error import URLError
    return URLError("dns")


class CliTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_path = write_config(self.directory.name, sources=SOURCES)
        self.config = load(self.directory.name, sources=SOURCES)
        self.out = Path(self.directory.name) / "out"
        self.state = State(self.config.db_path)

    def run_cli(self, *argv, runtime=None):
        old = os.umask(0o022)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = cli.main(list(argv), runtime=runtime)
            return code, out.getvalue()
        finally:
            os.umask(old)

    def add_topic(self, slot="2026-10-20T19:00+07:00", promo=False, sources=(fakes.SOURCE,)):
        return self.state.add_topic("2026-W43", "Màu nail merlot", "nail-salon", slot, 20,
                                    {"sources": list(sources), "product_promo": promo, "scores": {}})

    def write_cache(self, posts):
        (Path(self.directory.name) / "blog-cache.json").write_text(json.dumps({"savedAt": "x", "items": [], "posts": posts}),
                                                                    encoding="utf-8")

    def preview(self, topic, transport, net=None, *extra):
        net = net or FakeOpener({})
        code, output = self.run_cli("preview", "--config", str(self.config_path), "--topic", str(topic), "--out",
                                    str(self.out), *extra, runtime=Runtime(transport=transport, opener=net))
        return code, output, net

    def article(self):
        return json.loads((self.out / "article.json").read_text(encoding="utf-8"))

    def test_plan_week_dry_run_prints_the_dm_and_says_it_was_not_sent(self):
        net, transport = opener(), topics_transport()
        code, output = self.run_cli("plan-week", "--dry-run", "--config", str(self.config_path),
                                    runtime=Runtime(transport=transport, opener=net, today=TODAY))
        self.assertEqual(code, 0)
        self.assertIn("NailBlog: 18 chủ đề tuần 19–25/10", output)
        self.assertIn("(DM chưa gửi: chế độ --dry-run;", output)
        self.assertEqual(State(self.config.db_path).list_topics(), [])   # the dry run worked on a scratch copy
        self.assertTrue(all(call["method"] == "GET" for call in net.calls))

    def test_plan_week_without_dry_run_has_no_telegram_in_the_cli_and_says_so(self):
        code, output = self.run_cli("plan-week", "--config", str(self.config_path),
                                    runtime=Runtime(transport=topics_transport(), opener=opener(), today=TODAY))
        self.assertEqual(code, 0)
        self.assertIn("CLI không có phiên Telegram", output)

    def test_plan_week_failures_exit_1_with_a_short_code(self):
        code, output = self.run_cli("plan-week", "--config", str(self.config_path),
                                    runtime=Runtime(transport=topics_transport(), opener=opener(**{BLOGS_URL: TimeoutError()}),
                                                    today=TODAY))
        self.assertEqual((code, output.strip()),
                         (1, "NailBlog operation failed: cannot read published posts and no cache exists: timeout"))

    def test_preview_writes_json_and_html_with_no_network_and_no_images(self):
        topic = self.add_topic()
        self.write_cache(TWO_POSTS)
        transport = fakes.article_transport()
        # asyncio itself needs socket.socket on Windows, so block the layers the code would use to reach out.
        with patch("socket.create_connection", side_effect=AssertionError("network used")), \
                patch("socket.getaddrinfo", side_effect=AssertionError("dns used")), \
                patch("http.client.HTTPConnection.connect", side_effect=AssertionError("http used")), \
                patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("urllib used")):
            code, output, net = self.preview(topic, transport)
        self.assertEqual(code, 0, output)
        self.assertEqual(net.calls, [])                       # no website, no feed
        self.assertEqual(len(transport.calls), 6)             # outline + 5 sections, all through the fake
        self.assertEqual(sorted(os.listdir(self.out)), ["article.html", "article.json"])
        data = self.article()
        self.assertEqual((data["topic_id"], data["mode"], data["slot_at"]), (topic, "preview", "2026-10-20T19:00+07:00"))
        self.assertEqual(data["quality"], {"verdict": "pass", "issues": []})
        self.assertEqual(data["facts"], {"ungrounded": []})
        self.assertEqual(data["words"], 898)
        self.assertEqual(data["jsonld"][0]["@type"], "BlogPosting")
        self.assertEqual(data["jsonld"][1]["@type"], "FAQPage")
        self.assertNotIn("ld+json", data["content_html"])
        self.assertIn("<h2>Mục số B cho tiệm nail</h2>", data["content_html"])
        self.assertEqual([image["src"] for image in data["images"]],
                         ["placeholder-1.webp", "placeholder-2.webp", "placeholder-3.webp"])
        self.assertEqual(data["content_html"].count("<figure>"), 3)
        self.assertTrue(all(f'alt="{image["alt"]}"' in data["content_html"] for image in data["images"]))
        self.assertIn("https://nailhub.ai/blog/bai-cu-mot", data["content_html"])
        self.assertIn("https://nailhub.ai/blog/bai-cu-hai", data["content_html"])
        self.assertEqual(data["slug"], "mau-nail-merlot-mua-dong")
        self.assertIn("preview: verdict=pass words=898 issues=0", output)
        html = (self.out / "article.html").read_text(encoding="utf-8")
        self.assertIn('<meta charset="utf-8">', html)
        self.assertIn(data["content_html"], html)

    def test_preview_changes_no_post_and_no_topic_state(self):
        topic = self.add_topic()
        self.write_cache([])
        self.preview(topic, fakes.article_transport())
        state = State(self.config.db_path)
        self.assertEqual((state.list_posts(), state.get_topic(topic)["state"]), ([], "proposed"))

    def test_preview_holds_a_number_without_support_and_still_writes_the_files(self):
        topic = self.add_topic()
        self.write_cache(TWO_POSTS)
        transport = fakes.article_transport(section_override={1: {"paragraphs": ["Một bộ gel thường có giá $50. màu nail merlot " + "chữ " * 20,
                                                                                 fakes.prose(40)]}})
        code, output, _ = self.preview(topic, transport)
        self.assertEqual(code, 0)
        data = self.article()
        self.assertEqual(data["quality"]["verdict"], "hold")
        self.assertIn(("facts", "hold"), [(i["code"], i["kind"]) for i in data["quality"]["issues"]])
        self.assertEqual(data["facts"]["ungrounded"][0]["number"], "$50")
        self.assertIn("[hold] facts: số $50 chưa có căn cứ (no_claim)", output)

    def test_preview_accepts_a_number_that_a_verbatim_claim_supports(self):
        topic = self.add_topic()
        self.write_cache(TWO_POSTS)
        paragraph = "Một bộ gel thường có giá $50. " + fakes.prose(40, 0, fakes.KEYWORD)
        transport = fakes.article_transport(section_override={1: {"paragraphs": [paragraph, fakes.prose(40)],
                                                                  "claims": [fakes.claim()]}})
        self.preview(topic, transport)
        self.assertEqual(self.article()["quality"]["verdict"], "pass")

    def test_preview_never_lets_model_markup_through(self):
        topic = self.add_topic()
        self.write_cache([])
        transport = fakes.article_transport(section_override={0: {"paragraphs": ["<script>alert(1)</script> màu nail merlot " + "chữ " * 20]}})
        self.preview(topic, transport)
        self.assertNotIn("<script", (self.out / "article.html").read_text(encoding="utf-8"))
        self.assertNotIn("<script", self.article()["content_html"])
        self.assertIn("&lt;script&gt;", self.article()["content_html"])

    def test_without_the_published_list_the_slug_cannot_be_verified_so_the_article_is_held(self):
        topic = self.add_topic()
        self.preview(topic, fakes.article_transport())
        # no cache: the slug cannot be checked, and with no published posts only the CTA link exists
        self.assertEqual(sorted(i["code"] for i in self.article()["quality"]["issues"]),
                         ["internal_links", "slug_unverified"])

    def test_a_slug_that_already_exists_is_held(self):
        topic = self.add_topic()
        self.write_cache(TWO_POSTS + [{"slug": "mau-nail-merlot-mua-dong", "title": "x", "categorySlug": "x"}])
        self.preview(topic, fakes.article_transport())
        self.assertEqual([i["code"] for i in self.article()["quality"]["issues"]], ["slug_duplicate"])

    def test_an_occupied_slot_and_a_second_product_post_the_same_day_are_held(self):
        topic = self.add_topic(promo=True)
        self.write_cache(TWO_POSTS)
        self.state.add_post("2026-10-20T19:00+07:00", state="review")
        self.state.add_post("2026-10-20T06:00+07:00", state="scheduled", draft={"product_promo": True})
        self.state.add_post("2026-10-20T07:00+07:00", state="held", draft={"product_promo": True})  # held: not occupying
        self.preview(topic, fakes.article_transport())
        self.assertEqual(sorted(i["code"] for i in self.article()["quality"]["issues"]), ["product_promo", "slot_taken"])

    def test_unknown_or_dropped_topics_and_model_failures_exit_1_without_content(self):
        code, output, _ = self.preview(99, fakes.article_transport())
        self.assertEqual((code, output.strip()), (1, "NailBlog operation failed: topic 99 not found or dropped"))
        topic = self.add_topic()
        self.state.set_topic_state(topic, "dropped")
        self.assertEqual(self.preview(topic, fakes.article_transport())[0], 1)
        self.state.set_topic_state(topic, "proposed")
        bad = FakeTransport(lambda call, number: ollama_envelope({"nope": 1}))
        code, output, _ = self.preview(topic, bad)
        self.assertEqual(code, 1)
        self.assertIn("does not match the schema", output)
        self.assertFalse(self.out.exists())

    def test_argument_combinations_are_checked(self):
        base = ("--config", str(self.config_path))
        for argv in (("preview",) + base, ("preview", "--topic", "1") + base, ("preview", "--out", "x") + base,
                     ("preview", "--topic", "0", "--out", "x") + base,
                     ("plan-week", "--topic", "1") + base, ("plan-week", "--out", "x") + base,
                     ("status", "--dry-run") + base, ("preview", "--topic", "1", "--out", "x", "--dry-run") + base):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                cli.main(list(argv))
            self.assertEqual(raised.exception.code, 2)

    def test_preview_service_method_raises_for_a_missing_topic(self):
        service = Service(self.config)
        text = TextModel(self.config, service.model_call, transport=fakes.article_transport())
        import asyncio
        with self.assertRaises(PreviewError):
            asyncio.run(service.preview(5, self.out, text))


if __name__ == "__main__":
    unittest.main()
