"""Content standard in the writer: render order, code-made links, sources section, JSON-LD, word budget, prompts."""
import json
import re
import tempfile
import unittest
from datetime import date

from nailblog.config import LinksConfig
from nailblog.service import Service
from nailblog.text import TextModel
from nailblog.writer import (CLICHE_PHRASES, MIN_SECTION_WORDS, OUTLINE_SCHEMA, _body_targets,
                             build_jsonld, build_links, build_sources, cap_outline_sections,
                             layout, needs_sources, placeholder_images, render_html, rolling_section_budget,
                             section_words, trim_list_items, trim_to_ceiling, word_budget, word_count, write_article)
from tests import fakes
from tests.fakes import FakeTransport, make_article, ollama_envelope
from tests.helpers import settings

TODAY = date(2026, 10, 16)
CUSTOM = LinksConfig(jobs="https://nailhub.ai/jobs", salon_sale="https://nailhub.ai/salons", app="https://nailhub.ai/app")


def tags_of(html):
    return re.findall(r"<(h2|h3|p|ul|ol|figure)\b", html)


def posts(*items):
    return [{"slug": slug, "title": title, "categorySlug": category} for slug, title, category in items]


class RenderOrderTests(unittest.TestCase):
    def article(self):
        article = make_article()
        article["sections"][1].update(kind="steps", steps=["Bước một", "Bước hai", "Bước ba"])
        article["sections"][2].update(bullets=["Ý một", "Ý hai", "Ý ba"],
                                      h3=[{"title": "Ý nhỏ một", "paragraphs": [fakes.prose(50, 1)]}])
        article["links"] = [
            {"kind": "related", "text": "Bài liên quan A", "url": "https://nailhub.ai/blog/a", "after": 1},
            {"kind": "related", "text": "Bài liên quan B", "url": "https://nailhub.ai/blog/b", "after": None},
            {"kind": "cta", "text": "Xem tin tuyển thợ nail trên NailHub", "url": "https://nailhub.ai/jobs"}]
        return article

    def test_blocks_follow_content_standard_section_2(self):
        article = self.article()
        html = render_html(article, placeholder_images(article))
        expected = (["p", "p", "h2", "ul"]                                             # intro, Tóm tắt nhanh
                    + ["h2", "p", "figure", "p"]                                       # section 1: figure after 1st paragraph
                    + ["h2", "p", "p", "ol", "p"]                                      # section 2: steps, then "Xem thêm"
                    + ["h2", "p", "figure", "p", "ul", "h3", "p"]                      # section 3: bullets, then H3 block
                    + ["h2", "p", "p"]
                    + ["h2", "p", "figure", "p"]
                    + ["p"]                                                            # "Xem thêm" before the FAQ
                    + ["h2", "h3", "p", "h3", "p", "h3", "p"]                          # Câu hỏi thường gặp
                    + ["p"])                                                           # the CTA closes the article
        self.assertEqual(tags_of(html), expected)
        headings = re.findall(r"<h2>(.*?)</h2>", html)
        self.assertEqual(headings[0], "Tóm tắt nhanh")
        self.assertEqual(headings[-1], "Câu hỏi thường gặp")
        self.assertEqual(len(headings), 7)

    def test_the_figure_of_a_one_paragraph_section_follows_its_list_not_the_lead_in_line(self):
        article = make_article(slots=(0, 2, 4))
        article["links"] = [link for link in article["links"] if link["kind"] == "cta"]
        article["sections"][0].update(paragraphs=[fakes.prose(50, 1)], bullets=["Ý một", "Ý hai", "Ý ba"])
        article["sections"][2].update(paragraphs=[fakes.prose(50, 2)], steps=["Bước một", "Bước hai", "Bước ba"],
                                      h3=[{"title": "Ý nhỏ", "paragraphs": [fakes.prose(50, 3)]}])
        html = render_html(article, placeholder_images(article))
        sections = re.split(r"<h2>", html)
        self.assertEqual(tags_of("<h2>" + sections[2]), ["h2", "p", "ul", "figure"])
        self.assertEqual(tags_of("<h2>" + sections[4]), ["h2", "p", "ol", "figure", "h3", "p"])

    def test_no_h1_no_table_no_script_and_never_two_figures_in_a_row(self):
        article = self.article()
        for slots in ((0, 2, 4), (0, 1, 2, 3, 4)):
            article = make_article(slots=slots)
            html = render_html(article, placeholder_images(article))
            for banned in ("<h1", "<table", "<script", "<style"):
                self.assertNotIn(banned, html)
            self.assertIsNone(re.search(r"</figure>\s*<figure", html))

    def test_related_posts_go_after_their_section_or_before_the_faq_and_the_cta_link_closes_the_article(self):
        html = render_html(self.article(), placeholder_images(self.article()))
        a, b = '<p>Xem thêm: <a href="https://nailhub.ai/blog/a">Bài liên quan A</a></p>', \
            '<p>Xem thêm: <a href="https://nailhub.ai/blog/b">Bài liên quan B</a></p>'
        self.assertIn(a, html)
        self.assertLess(html.index("<h2>Mục số C cho tiệm nail</h2>"), html.index("<h2>Mục số D"))
        self.assertLess(html.index(a), html.index("<h2>Mục số C cho tiệm nail</h2>"))            # end of section 2
        self.assertGreater(html.index(a), html.index("<h2>Mục số B cho tiệm nail</h2>"))
        self.assertLess(html.index(b), html.index("<h2>Câu hỏi thường gặp</h2>"))
        self.assertGreater(html.index(b), html.index("<h2>Mục số E cho tiệm nail</h2>"))
        last = html.splitlines()[-1]
        self.assertTrue(last.startswith("<p>") and last.endswith(
            '<a href="https://nailhub.ai/jobs">Xem tin tuyển thợ nail trên NailHub</a></p>'), last)

    def test_the_sources_section_closes_the_article_with_https_links_and_the_update_date(self):
        article = make_article(sources=[{"title": "TDLR <b>", "url": "https://tdlr.texas.gov/renew?a=1&b=2"},
                                        {"title": "http only", "url": "http://tdlr.texas.gov/x"},
                                        {"title": "js", "url": "javascript:alert(1)"}], sources_updated="16/10/2026")
        html = render_html(article, placeholder_images(article))
        tail = html[html.index("<h2>Nguồn tham khảo</h2>"):]
        self.assertEqual(tail, '<h2>Nguồn tham khảo</h2>\n<ul><li><a href="https://tdlr.texas.gov/renew?a=1&amp;b=2">'
                               'TDLR &lt;b&gt;</a></li></ul>\n<p>Cập nhật ngày 16/10/2026.</p>')
        self.assertLess(html.index("<h2>Câu hỏi thường gặp</h2>"), html.index("<h2>Nguồn tham khảo</h2>"))
        self.assertNotIn("Nguồn tham khảo", render_html(make_article(), []))
        self.assertNotIn("Nguồn tham khảo", render_html(make_article(sources=[{"title": "x", "url": "http://a.example/"}]), []))

    def test_layout_is_what_the_gate_reads(self):
        kinds = [kind for kind, _ in layout(self.article())]
        self.assertEqual(kinds.count("figure"), 3)
        self.assertEqual(kinds.count("see_also"), 2)
        self.assertEqual(kinds[-1], "cta")


class BuildLinksTests(unittest.TestCase):
    POSTS = posts(("a", "Giữ khách quen cho tiệm nail", "nail-salon"), ("b", "Màu nail mùa đông", "hot-trend"),
                  ("c", "Quản lý lịch hẹn tiệm nail", "nail-salon"), ("d", "Thuế tiệm nail", "nail-salon"))

    def test_at_most_two_older_posts_of_the_same_category_plus_one_cta_link(self):
        links = build_links("https://nailhub.ai/", "nail-salon", self.POSTS, {"target": "jobs"}, CUSTOM, [])
        self.assertEqual([(link["kind"], link["url"]) for link in links],
                         [("related", "https://nailhub.ai/blog/a"), ("related", "https://nailhub.ai/blog/c"),
                          ("cta", "https://nailhub.ai/jobs")])
        self.assertEqual([link["kind"] for link in build_links("https://nailhub.ai", "nail-job", self.POSTS, None)], ["cta"])

    def test_the_cta_target_picks_its_url_from_the_links_table(self):
        for target, url, label in (("jobs", "/jobs", "Xem tin tuyển thợ nail trên NailHub"),
                                   ("salon_sale", "/salons", "Xem tin sang tiệm nail trên NailHub"),
                                   ("app", "/app", "Tải app NailHub"), ("nonsense", "/app", "Tải app NailHub")):
            with self.subTest(target=target):
                (cta,) = build_links("https://nailhub.ai", "nail-job", [], {"target": target}, CUSTOM)
                self.assertEqual((cta["url"], cta["text"]), ("https://nailhub.ai" + url, label))
        (default,) = build_links("https://nailhub.ai", "nail-job", [], {"target": "jobs"})
        self.assertEqual(default["url"], "https://nailhub.ai/")                    # the Phase 0 placeholder URL

    def test_target_post_links_the_first_related_post_once_or_the_home_page(self):
        links = build_links("https://nailhub.ai", "nail-salon", self.POSTS, {"target": "post"}, CUSTOM, [])
        self.assertEqual([(link["kind"], link["url"]) for link in links],
                         [("related", "https://nailhub.ai/blog/c"), ("cta", "https://nailhub.ai/blog/a")])
        self.assertEqual(links[1]["text"], "Giữ khách quen cho tiệm nail")
        (home,) = build_links("https://nailhub.ai", "nail-job", [], {"target": "post"}, CUSTOM)
        self.assertEqual((home["kind"], home["url"]), ("cta", "https://nailhub.ai/"))

    def test_a_related_post_follows_the_section_that_shares_two_words_else_it_goes_before_the_faq(self):
        sections = [{"h2": "Vì sao khách quen bỏ đi"}, {"h2": "Cách quản lý lịch hẹn tiệm nail"}, {"h2": "Thuế mùa lễ"}]
        links = build_links("https://nailhub.ai", "nail-salon", self.POSTS, {"target": "app"}, CUSTOM, sections)
        self.assertEqual([(link["url"][-1], link.get("after")) for link in links if link["kind"] == "related"],
                         [("a", 0), ("c", 1)])    # khách+quen of "Giữ khách quen..." and quản+lý+lịch+hẹn+tiệm+nail
        unrelated = build_links("https://nailhub.ai", "nail-salon", self.POSTS, {"target": "app"}, CUSTOM,
                                [{"h2": "Một mục khác hẳn"}, {"h2": "Thuế mùa lễ"}])
        self.assertEqual([link["after"] for link in unrelated if link["kind"] == "related"], [None, None])
        one_word = build_links("https://nailhub.ai", "nail-salon", self.POSTS, {"target": "app"}, CUSTOM,
                               [{"h2": "Khách mới đến tiệm"}])     # shares "khách" and "tiệm" with the first post
        self.assertEqual([link["after"] for link in one_word if link["kind"] == "related"], [0, None])
        single = build_links("https://nailhub.ai", "nail-salon", self.POSTS[:1], {"target": "app"}, CUSTOM,
                             [{"h2": "Khách mới"}])                # one shared word is not enough
        self.assertEqual([link["after"] for link in single if link["kind"] == "related"], [None])
        two = build_links("https://nailhub.ai", "nail-salon", self.POSTS, {"target": "app"}, CUSTOM,
                          [{"h2": "Quản lý lịch hẹn tiệm nail"}, {"h2": "Quản lý lịch hẹn tiệm nail"}])
        related = [link for link in two if link["kind"] == "related"]
        self.assertEqual([link["after"] for link in related], [0, 1])

    def test_duplicates_are_dropped_and_the_slug_is_escaped(self):
        odd = posts(("a b", "Một bài", "nail-salon"), ("a b", "Một bài lặp", "nail-salon"), ("", "Không slug", "nail-salon"),
                    ("x", "", "nail-salon"))
        links = build_links("https://nailhub.ai", "nail-salon", odd, {"target": "app"})
        self.assertEqual([link["url"] for link in links if link["kind"] == "related"],
                         ["https://nailhub.ai/blog/a%20b"])


class SourcesTests(unittest.IsolatedAsyncioTestCase):
    LEGAL = fakes.claim("Phí đăng ký license là $50.", quote="a gel manicure costs $50 on average")

    def test_only_legal_tax_fee_hours_claims_need_a_sources_section(self):
        for text, expected in (("Phí gia hạn là $50.", True), ("Luật mới có hiệu lực.", True), ("Thuế 1099-NEC.", True),
                               ("Cần 400 giờ học.", True), ("Cần license còn hạn.", True), ("Giấy phép tiệm.", True),
                               ("Theo State Board.", True), ("Một bộ gel thường có giá $50.", False),
                               ("Phim hay.", False)):
            with self.subTest(text=text):
                self.assertEqual(needs_sources({"claims": [{"text": text}]}), expected)
        self.assertFalse(needs_sources({"claims": []}))
        self.assertFalse(needs_sources({}))

    def test_build_sources_lists_the_https_sources_behind_the_claims_with_the_vietnam_date(self):
        sources = [dict(fakes.SOURCE), {**fakes.SOURCE_2, "url": "http://tdlr.texas.gov/news"},
                   {**fakes.SOURCE, "id": "s3", "url": "https://other.example/x", "title": "Khác"}]
        listed, updated = build_sources({"claims": [self.LEGAL, {**self.LEGAL, "source_id": "s2"}]}, sources, TODAY)
        self.assertEqual((listed, updated), ([{"title": "Winter nail trends", "url": "https://www.bustle.com/winter-nails"}],
                                              "16/10/2026"))
        self.assertEqual(build_sources({"claims": []}, sources, TODAY), ([], ""))

    async def test_write_article_adds_the_section_data_only_when_the_article_needs_it(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = settings(directory.name)
        model = TextModel(config,
                          _test_transport=fakes.article_transport(outline_override={"claims": [self.LEGAL]}))
        article = await write_article(model, {"title": "Chủ đề", "category": "nail-job", "product_promo": False},
                                      [fakes.SOURCE], site_origin="https://nailhub.ai", today=TODAY)
        self.assertEqual((article["sources"], article["sources_updated"]),
                         ([{"title": "Winter nail trends", "url": "https://www.bustle.com/winter-nails"}], "16/10/2026"))
        html = render_html(article, placeholder_images(article))
        self.assertIn('<a href="https://www.bustle.com/winter-nails">Winter nail trends</a>', html)
        self.assertTrue(html.endswith("<p>Cập nhật ngày 16/10/2026.</p>"))


class JsonLdTests(unittest.TestCase):
    def test_blogposting_and_faqpage_are_built_from_the_article(self):
        article = make_article()
        post, faq = build_jsonld(article, "https://nailhub.ai/", "2026-10-17T06:00+07:00")
        self.assertEqual((post["@context"], post["@type"], post["inLanguage"]), ("https://schema.org", "BlogPosting", "vi"))
        self.assertEqual((post["headline"], post["description"], post["datePublished"]),
                         (article["title"], article["meta_description"], "2026-10-17T06:00+07:00"))
        self.assertEqual(post["url"], "https://nailhub.ai/blog/mau-nail-merlot-mua-dong")
        self.assertEqual(post["keywords"], "màu nail merlot, merlot nails, nail đỏ rượu, gel merlot")
        self.assertEqual((post["wordCount"], post["articleSection"]), (900, "nail-salon"))
        self.assertEqual((faq["@type"], len(faq["mainEntity"])), ("FAQPage", 3))
        self.assertEqual(faq["mainEntity"][0], {"@type": "Question", "name": article["faq"][0]["q"],
                                                "acceptedAnswer": {"@type": "Answer", "text": article["faq"][0]["a"]}})
        self.assertNotIn("datePublished", build_jsonld(article, "https://nailhub.ai")[0])
        json.dumps([post, faq], ensure_ascii=False)


class WordBudgetTests(unittest.TestCase):
    def test_budget_follows_the_category_ceiling(self):
        hot = word_budget("hot-trend", 5)
        self.assertEqual((hot["ceiling"], hot["target"], hot["intro"], hot["faq_items"], hot["section"]), (1000, 900, 70, 3, 113))
        self.assertEqual(word_budget("did-you-know", 5), hot)
        job = word_budget("nail-job", 5)
        self.assertEqual((job["ceiling"], job["target"], job["faq_items"], job["section"]), (1200, 1080, 4, 138))
        self.assertEqual(word_budget("nail-salon", 5), job)
        self.assertEqual(word_budget("hot-trend", 4)["section"], 141)
        self.assertGreater(word_budget("hot-trend", 6)["section"], 89)     # never below the 90-word floor
        self.assertEqual(word_budget("hot-trend", 50)["section"], 90)


class PromptTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = settings(directory.name)
        self.service = Service(self.config)

    async def calls(self, category, sections=5):
        transport = fakes.article_transport(sections=sections)
        model = TextModel(self.config, _test_transport=transport)
        await write_article(model, {"title": "Nail mắt mèo nhung mùa đông 2026", "category": category, "product_promo": False},
                            [fakes.SOURCE], site_origin="https://nailhub.ai", today=TODAY)
        return [call["payload"]["messages"] for call in transport.calls]

    async def test_the_outline_request_carries_the_category_skeleton_and_the_budget(self):
        hot = (await self.calls("hot-trend"))[0][0]["content"]
        self.assertIn("Trend là gì và vì sao đang hot", hot)
        self.assertIn("cả bài tối đa 900 từ (trần 1000)", hot)
        self.assertIn("mở bài khoảng 70 từ", hot)
        self.assertIn("mỗi mục H2 khoảng 113 từ", hot)
        self.assertIn("FAQ đúng 3 câu", hot)
        job = (await self.calls("nail-job"))[0][0]["content"]
        self.assertIn("Giấy tờ và điều kiện (có nguồn)".lower(), job.lower())
        self.assertIn("cả bài tối đa 1080 từ (trần 1200)", job)
        self.assertIn("FAQ đúng 4 câu", job)
        dyk = (await self.calls("did-you-know"))[0][0]["content"]
        self.assertIn("Con số hoặc tin chính", dyk)
        salon = (await self.calls("nail-salon"))[0][0]["content"]
        self.assertIn("Vấn đề chủ tiệm gặp", salon)

    async def test_every_section_request_carries_its_own_budget_kind_keyword_and_points(self):
        messages = await self.calls("hot-trend", sections=5)
        outline = fakes.outline(5)
        written = sum(len(p.split()) for p in outline["intro"]) + sum(len(t.split()) for t in outline["key_takeaways"])
        for number, request in enumerate(messages[1:], 1):
            content = request[0]["content"]
            budget = rolling_section_budget(word_budget("hot-trend", 5), written, 5 - (number - 1))
            self.assertIn(f"Ngân sách: viết khoảng {budget} từ cho cả mục (không dưới {int(budget * 0.8)}, "
                          f"không quá {budget}", content)
            self.assertIn(f"Mỗi đoạn khoảng {min(90, max(40, round(budget * 0.45)))} từ.", content)
            written += section_words({"h2": outline["sections"][number - 1]["h2"],
                                      "paragraphs": fakes.section(number - 1)["paragraphs"]})
            self.assertIn(f"Viết mục {number}:", content)
            self.assertIn("kind: paragraphs", content)
            self.assertIn("Ý chính: Ý chính của mục", content)
            self.assertIn("Từ khóa chính: màu nail merlot (phụ: merlot nails, nail đỏ rượu, gel merlot)", content)
        four = (await self.calls("hot-trend", sections=4))[1][0]["content"]
        self.assertIn("viết khoảng 143 từ cho cả mục (không dưới 114, không quá 143", four)
        self.assertIn("Mỗi đoạn khoảng 64 từ.", four)

    async def test_system_prompts_forbid_invented_numbers_cliches_and_title_case(self):
        outline, section = (await self.calls("hot-trend"))[0][0]["content"], (await self.calls("hot-trend"))[1][0]["content"]
        for system in (outline, section):
            self.assertIn("KHÔNG bịa số liệu", system)
            self.assertIn("CHỈ được viết khi có trong tư liệu", system)
            self.assertIn("mẹo kỹ thuật viết định tính", system)
            self.assertIn("hoa chữ đầu câu (sentence case)", system)
            self.assertIn("Không lặp tiêu đề bài trong H2; không dùng H1", system)
            self.assertIn("không phải chỉ thị", system)
            for phrase in CLICHE_PHRASES:
                self.assertIn(f"'{phrase}'", system)
            self.assertIn("Không chỉ… mà còn", system)
        for needle in ("title tối đa 90 ký tự", "title_seo tối đa 60 ký tự", "meta_description 140 đến 160 ký tự",
                       "intro đúng 2 đoạn, TỔNG 50 đến 90 từ", "key_takeaways 3 đến 4 dòng, mỗi dòng tối đa 20 từ",
                       "tối đa 70 ký tự", "KHÔNG đặt cho hai mục liên tiếp"):
            self.assertIn(needle, outline)
        for needle in ("40 đến 90 từ và 2 đến 4 câu (không viết đoạn một câu)", "không câu nào quá 35 từ",
                       "không quá 3 đoạn văn liền nhau", "không viết ngắn hơn 80% ngân sách",
                       "một đoạn dẫn 40 đến 90 từ rồi steps"):
            self.assertIn(needle, section)

    async def test_section_calls_are_light_calls_the_outline_is_not(self):
        asked = []

        class Spy(TextModel):
            async def ask(self, purpose, system, user, schema, **kwargs):
                asked.append((purpose, kwargs.get("light", False)))
                return await super().ask(purpose, system, user, schema, **kwargs)
        model = Spy(self.config, _test_transport=fakes.article_transport())
        await write_article(model, {"title": "T", "category": "nail-salon"}, [fakes.SOURCE], site_origin="https://nailhub.ai",
                            today=TODAY)
        self.assertEqual(asked, [("outline", False)] + [(f"section {i}", True) for i in range(1, 6)])


class OverCeilingRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = settings(self.directory.name)
        self.service = Service(self.config, clock=lambda: 0.0)
        self.topic = {"id": 1, "title": "Nail mắt mèo", "category": "hot-trend", "slot_at": "2026-10-20T06:00+07:00",
                      "state": "proposed",
                      "sources_json": json.dumps({"sources": [fakes.SOURCE], "product_promo": False, "scores": {}})}

    async def draft(self, repair_reply):
        calls = []
        big = {i: {"paragraphs": [fakes.prose(85, i, fakes.KEYWORD), fakes.prose(85, i + 1)]} for i in range(5)}
        big[3] = {"paragraphs": [fakes.prose(90, 3, fakes.KEYWORD), fakes.prose(90, 4)]}

        def transport(call, number):
            calls.append(call)
            if number == 1:
                return ollama_envelope(fakes.outline(5))
            if number <= 6:
                return ollama_envelope(fakes.section(number - 2, **big[number - 2]))
            return ollama_envelope(repair_reply)
        text = TextModel(self.config, _test_transport=FakeTransport(transport))
        draft = await self.service.draft(self.topic, text, posts=fakes.RELATED_POSTS, today=TODAY)
        return draft, calls

    async def test_over_the_ceiling_the_longest_sections_are_rewritten_once_with_budgets_that_remove_the_excess(self):
        piece = {"paragraphs": [fakes.prose(45, 1, fakes.KEYWORD), fakes.prose(45, 2)],
                 "bullets": ["một ý ngắn", "hai ý ngắn", "ba ý ngắn"], "claims": []}
        reply = {"body": piece, "body_2": piece, "body_3": piece}
        draft, calls = await self.draft(reply)
        self.assertEqual(len(calls), 7)                                   # exactly one repair call
        request = calls[-1]["payload"]["messages"][0]["content"]
        before = int(re.search(r"Độ dài bài: (\d+) từ, cần 570-1050.", request).group(1))
        self.assertGreater(before, 1050)
        self.assertIn("Viết lại mục 1 cho ngắn hơn, tối đa 97 từ cho cả mục", request)
        self.assertIn("Viết lại mục 2 cho ngắn hơn, tối đa 98 từ cho cả mục", request)
        self.assertIn("Viết lại mục 4 cho ngắn hơn, tối đa 106 từ cho cả mục", request)
        self.assertIn("Mục 4 (H2: Mục số D cho tiệm nail", request)
        self.assertEqual(draft.repaired, ["body"])
        section = draft.article["sections"][3]
        self.assertEqual((len(section["paragraphs"]), section["bullets"]), (2, ["một ý ngắn", "hai ý ngắn", "ba ý ngắn"]))
        self.assertEqual([len(s["paragraphs"]) for s in draft.article["sections"]], [2] * 5)
        self.assertEqual(draft.words, before - 223)       # sections 1, 2, 4 become h2 + 90 + 9 words each
        self.assertNotIn("words", {issue.code for issue in draft.result.issues})
        self.assertEqual(len(calls), 7)

    async def test_the_untouched_sections_and_claims_survive_the_rewrite(self):
        claim = fakes.claim()
        piece = {"paragraphs": [fakes.prose(45, 1), fakes.prose(45, 2)], "claims": [claim]}
        draft, _ = await self.draft({"body": piece, "body_2": piece, "body_3": piece})
        # A repair must not append an orphan assertion absent from the original body/claims.
        self.assertEqual(draft.article["claims"], [])
        self.assertEqual(draft.article["sections"][3]["paragraphs"],
                         [fakes.prose(90, 3, fakes.KEYWORD), fakes.prose(90, 4)])
        self.assertEqual(draft.repaired, [])
        self.assertIn("repair_rejected_new_claim:body", draft.article["writer_audit"])
        self.assertEqual(draft.article["sections"][0]["paragraphs"][0][:15], fakes.prose(85, 0, fakes.KEYWORD)[:15])


class OutlineCapAndBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = settings(self.directory.name)
        self.service = Service(self.config, clock=lambda: 0.0)

    async def write(self, transport, category="nail-salon"):
        text = TextModel(self.config, _test_transport=transport)
        return await write_article(text, {"title": "T", "category": category}, [fakes.SOURCE],
                                   site_origin="https://nailhub.ai", today=TODAY)

    def test_the_outline_schema_asks_for_four_to_five_sections(self):
        sections = OUTLINE_SCHEMA["properties"]["sections"]
        self.assertEqual((sections["minItems"], sections["maxItems"]), (4, 5))

    def test_cap_keeps_the_image_sections_and_records_the_original_count(self):
        outline = fakes.outline(6, slots=(0, 2, 5))
        capped, audit = cap_outline_sections(outline)
        self.assertEqual(audit, ["outline_sections_capped:6"])
        self.assertEqual(len(capped["sections"]), 5)
        self.assertEqual(sum(1 for s in capped["sections"] if s.get("image_slot")), 3)
        self.assertEqual([s["h2"] for s in capped["sections"]],
                         [s["h2"] for i, s in enumerate(outline["sections"]) if i != 4])
        same, none = cap_outline_sections(fakes.outline(5))
        self.assertEqual((len(same["sections"]), none), (5, []))

    async def test_a_six_section_outline_passes_the_real_ask_path_and_is_capped_to_five(self):
        transport = FakeTransport(lambda call, number: ollama_envelope(
            fakes.outline(6, slots=(1, 3, 5)) if number == 1 else fakes.section(number - 2)))
        article = await self.write(transport)
        self.assertEqual(len(transport.calls), 1 + 5)                 # no retry of the outline, five section calls
        self.assertEqual(len(article["sections"]), 5)
        self.assertIn("outline_sections_capped:6", article["writer_audit"])
        slots = [bool(s["image_slot"]) for s in article["sections"]]
        self.assertEqual(sum(slots), 3)
        self.assertFalse(any(a and b for a, b in zip(slots, slots[1:])))

    def test_the_published_schema_says_five_but_the_outline_prompt_asks_for_exactly_five(self):
        from nailblog.writer import OUTLINE_SYSTEM
        self.assertIn("đúng 5 mục", OUTLINE_SYSTEM)
        self.assertEqual(OUTLINE_SCHEMA["properties"]["sections"]["minItems"], 4)

    def test_rolling_budget_is_what_is_left_after_written_words_and_the_cta_faq_allowance(self):
        salon = word_budget("nail-salon", 5)      # target 1080, CTA 45 + 4 FAQ x 55
        self.assertEqual(rolling_section_budget(salon, 200, 5), (1080 - 200 - 265) // 5)
        self.assertEqual(rolling_section_budget(salon, 500, 3), (1080 - 500 - 265) // 3)
        self.assertEqual(rolling_section_budget(salon, 900, 1), MIN_SECTION_WORDS)
        trend = word_budget("hot-trend", 5)       # target 900, CTA 45 + 3 FAQ x 55
        self.assertEqual(rolling_section_budget(trend, 150, 5), (900 - 150 - 210) // 5)
        self.assertEqual((salon["target"], salon["faq_items"], salon["section"]), (1080, 4, (1080 - 390) // 5))

    async def test_each_section_prompt_carries_the_rolling_budget(self):
        transport = fakes.article_transport()
        article = await self.write(transport)
        outline = fakes.outline(5)
        written = sum(len(p.split()) for p in outline["intro"]) + sum(len(t.split()) for t in outline["key_takeaways"])
        budget = word_budget("nail-salon", 5)
        for index in range(5):
            request = transport.calls[1 + index]["payload"]["messages"][0]["content"]
            expected = rolling_section_budget(budget, written, 5 - index)
            self.assertIn(f"viết khoảng {expected} từ cho cả mục", request)
            self.assertIn(f"không quá {expected};", request)
            section = fakes.section(index)
            written += section_words({"h2": outline["sections"][index]["h2"], "paragraphs": section["paragraphs"]})
        self.assertEqual(len(article["sections"]), 5)


class TrimToCeilingTests(unittest.TestCase):
    def long_article(self, sections=6, slots=(0, 2, 4), extra=None, words=1330):
        article = make_article(sections=sections, slots=slots, words=words)
        for index, grow in (extra or {}).items():
            article["sections"][index]["paragraphs"][0] += " thêm" * grow
        return article

    def test_removes_the_longest_section_without_an_image_and_remaps_the_links(self):
        article = self.long_article(slots=(0, 3), extra={4: 150, 2: 40}, words=1300)
        article["links"] = [{"kind": "related", "text": "A", "url": "https://nailhub.ai/blog/a", "after": 5},
                            {"kind": "related", "text": "B", "url": "https://nailhub.ai/blog/b", "after": 4},
                            {"kind": "related", "text": "C", "url": "https://nailhub.ai/blog/c", "after": 1},
                            {"kind": "related", "text": "D", "url": "https://nailhub.ai/blog/d", "after": None},
                            {"kind": "cta", "text": "cta", "url": "https://nailhub.ai/"}]
        removed = article["sections"][4]["h2"]
        before = word_count(article)
        self.assertGreater(before, 1200)
        trimmed = trim_to_ceiling(article)
        self.assertEqual(trimmed["writer_audit"][0], "trim_section:" + removed)
        self.assertNotIn(removed, [s["h2"] for s in trimmed["sections"]])
        self.assertLessEqual(word_count(trimmed), 1200)
        # link anchored on the removed section moves to the previous kept one, later anchors shift down
        self.assertEqual([link.get("after") for link in trimmed["links"]], [4, 3, 1, None, None])
        self.assertEqual(len(article["sections"]), 6)   # the input is not mutated

    def test_never_makes_two_image_sections_adjacent(self):
        article = self.long_article(extra={3: 150, 5: 60, 1: 20})   # slots 0, 2, 4; the longest free one is 3
        trimmed = trim_to_ceiling(article)
        kept = trimmed["sections"]
        self.assertFalse(any(a["image_slot"] and b["image_slot"] for a, b in zip(kept, kept[1:])))
        self.assertNotIn("trim_section:" + article["sections"][3]["h2"], trimmed.get("writer_audit", []))
        self.assertNotIn("trim_section:" + article["sections"][1]["h2"], trimmed.get("writer_audit", []))
        self.assertEqual(trimmed["writer_audit"][0], "trim_section:" + article["sections"][5]["h2"])

    def test_keeps_at_least_four_sections_and_never_removes_an_image_section(self):
        article = self.long_article(sections=5, slots=(0,), words=1700)
        trimmed = trim_to_ceiling(article)
        self.assertEqual(len(trimmed["sections"]), 4)
        self.assertTrue(trimmed["sections"][0]["image_slot"])
        self.assertEqual(len(trimmed["writer_audit"]), 1)
        self.assertGreater(word_count(trimmed), 1200)   # the model repair handles the rest

    def test_untouched_when_within_the_ceiling_or_when_a_section_carries_a_number(self):
        article = make_article(sections=6, slots=(0, 3), words=1100)
        self.assertIs(trim_to_ceiling(article), article)
        article = self.long_article(slots=(0, 3), extra={4: 200, 2: 100})
        article["sections"][4]["paragraphs"][1] += " Giá là $50."
        article["claims"] = [fakes.claim("Giá là $50.")]
        trimmed = trim_to_ceiling(article)
        self.assertIn(article["sections"][4]["h2"], [s["h2"] for s in trimmed["sections"]])


class TrimFloorTests(unittest.TestCase):
    def test_trim_never_goes_below_the_category_word_floor(self):
        article = make_article(sections=6, slots=(0, 3), words=1000, category="hot-trend")
        article["sections"][4]["paragraphs"][0] += " thêm" * 600      # 1600 words, ceiling 1000, floor 600
        trimmed = trim_to_ceiling(article)
        self.assertGreaterEqual(word_count(trimmed), 600)
        floor_case = make_article(sections=5, slots=(0, 3), words=1000, category="nail-job")   # floor 800
        floor_case["sections"][1]["paragraphs"][0] += " thêm" * 230   # 1230 words, over 1200 by 30
        floor_case["sections"][1]["h2"] = floor_case["sections"][1]["h2"]
        result = trim_to_ceiling(floor_case)
        self.assertGreaterEqual(word_count(result), 800)
        self.assertIsNot(result, floor_case)
        tiny = make_article(sections=6, slots=(0, 3), words=1000, category="nail-job")
        tiny["sections"][1]["paragraphs"][0] += " thêm" * 205          # one removal would drop below 800
        for section in tiny["sections"][2:]:
            section["paragraphs"] = [fakes.prose(45, 1), fakes.prose(45, 2)]
        self.assertGreaterEqual(word_count(trim_to_ceiling(tiny)), 800)


class KeywordReaskTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = settings(directory.name)
        self.service = Service(self.config, clock=lambda: 0.0)

    async def run_write(self, miss_first, miss_again):
        missing = {"paragraphs": [fakes.prose(100, 1), fakes.prose(100, 2)], "claims": []}

        def respond(call, number):
            content = call["payload"]["messages"][0]["content"]
            if number == 1:
                return ollama_envelope(fakes.outline(5))
            index = int(re.search(r"Viết mục (\d+):", content).group(1)) - 1
            again = "Lần trước thiếu từ khóa chính" in content
            if index == 2 and ((miss_again and again) or (miss_first and not again)):
                return ollama_envelope(missing)
            return ollama_envelope(fakes.section(index, words=100))
        transport = FakeTransport(respond)
        text = TextModel(self.config, _test_transport=transport)
        article = await write_article(text, {"title": "T", "category": "nail-salon"}, [fakes.SOURCE],
                                      site_origin="https://nailhub.ai", today=TODAY)
        return article, transport

    async def test_a_missing_keyword_re_asks_that_section_exactly_once(self):
        article, transport = await self.run_write(miss_first=True, miss_again=False)
        self.assertEqual(len(transport.calls), 1 + 5 + 1)
        self.assertIn("keyword_reask:2", article["writer_audit"])
        self.assertIn(fakes.KEYWORD, article["sections"][2]["paragraphs"][0].casefold())
        last = [c for c in transport.calls if "Lần trước thiếu từ khóa chính" in c["payload"]["messages"][0]["content"]]
        self.assertEqual(len(last), 1)
        from nailblog.writer import KEYWORD_ASK, KEYWORD_AVOID
        asked = last[0]["payload"]["messages"][0]["content"]
        self.assertIn(KEYWORD_ASK, asked)
        self.assertNotIn(KEYWORD_AVOID, asked)

    async def test_the_keyword_present_means_no_extra_call(self):
        article, transport = await self.run_write(miss_first=False, miss_again=False)
        self.assertEqual(len(transport.calls), 6)
        self.assertFalse([a for a in article["writer_audit"] if a.startswith("keyword_reask")])

    async def test_still_missing_after_the_re_ask_keeps_the_section_without_another_call(self):
        article, transport = await self.run_write(miss_first=True, miss_again=True)
        self.assertEqual(len(transport.calls), 7)
        self.assertIn("keyword_reask:2", article["writer_audit"])
        self.assertNotIn(fakes.KEYWORD, article["sections"][2]["paragraphs"][0].casefold())

    async def test_a_dense_article_is_not_re_asked_and_the_prompt_switches_with_the_running_density(self):
        from nailblog.writer import KEYWORD_ASK, KEYWORD_AVOID
        missing = {"paragraphs": [fakes.prose(15, 1), fakes.prose(15, 2)], "claims": []}

        def respond(call, number):
            if number == 1:
                return ollama_envelope(fakes.outline(5))
            index = number - 2
            return ollama_envelope(fakes.section(index, words=12) if index < 3 else missing)
        transport = FakeTransport(respond)
        text = TextModel(self.config, _test_transport=transport)
        article = await write_article(text, {"title": "T", "category": "nail-salon"}, [fakes.SOURCE],
                                      site_origin="https://nailhub.ai", today=TODAY)
        requests = [c["payload"]["messages"][0]["content"] for c in transport.calls[1:]]
        self.assertEqual(len(transport.calls), 6)                  # dense enough: no re-ask for the later sections
        self.assertFalse([a for a in article["writer_audit"] if a.startswith("keyword_reask")])
        self.assertIn(KEYWORD_ASK, requests[0])
        self.assertIn(KEYWORD_AVOID, requests[-1])
        self.assertNotIn(KEYWORD_ASK, requests[-1])

    def test_the_section_system_prompt_leaves_the_keyword_rule_to_the_request(self):
        from nailblog.writer import SECTION_SYSTEM
        self.assertIn("dòng 'Từ khóa chính' của yêu cầu", SECTION_SYSTEM)


class OutlineAttemptsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = settings(directory.name)
        self.service = Service(self.config, clock=lambda: 0.0)

    def model(self, respond):
        transport = FakeTransport(respond)
        return TextModel(self.config, _test_transport=transport), transport

    async def test_an_invalid_outline_or_section_fails_closed_without_a_second_call(self):
        from nailblog.text import TextModelError
        text, transport = self.model(lambda call, number: ollama_envelope({}))
        with self.assertRaises(TextModelError):
            await write_article(text, {"title": "T", "category": "nail-salon"}, [fakes.SOURCE],
                                site_origin="https://nailhub.ai", today=TODAY)
        self.assertEqual(len(transport.calls), 1)                      # the Agy route has no JSON repair round
        text, transport = self.model(lambda call, number: ollama_envelope(
            fakes.outline(5) if number == 1 else {}))
        with self.assertRaises(TextModelError):
            await write_article(text, {"title": "T", "category": "nail-salon"}, [fakes.SOURCE],
                                site_origin="https://nailhub.ai", today=TODAY)
        self.assertEqual(len(transport.calls), 1 + 1)                  # the outline, then the bad section: stop


class TrimListItemsTests(unittest.TestCase):
    def article(self, words=1140):
        article = make_article(sections=5, slots=(0, 2, 4), words=words)
        article["sections"][1]["kind"], article["sections"][3]["kind"] = "list", "list"
        article["sections"][1]["bullets"] = [fakes.prose(10, i, sentences=1) for i in range(6)]   # 60 words
        article["sections"][3]["bullets"] = [fakes.prose(10, i + 7, sentences=1) for i in range(5)]  # 50 words
        return article

    def test_removes_trailing_bullets_longest_section_first_and_keeps_three(self):
        article = self.article()
        self.assertEqual(word_count(article), 1250)
        trimmed = trim_list_items(article)
        self.assertEqual(trimmed["writer_audit"], ["trim_items:1:3", "trim_items:3:2"])
        self.assertEqual(word_count(trimmed), 1200)
        self.assertEqual(trimmed["sections"][1]["bullets"], article["sections"][1]["bullets"][:3])
        self.assertEqual(trimmed["sections"][3]["bullets"], article["sections"][3]["bullets"][:3])
        for before, after in zip(article["sections"], trimmed["sections"]):
            self.assertEqual(before["paragraphs"], after["paragraphs"])
        self.assertEqual(trimmed["claims"], article["claims"])

    def test_never_leaves_fewer_than_three_items_and_does_nothing_within_the_ceiling(self):
        article = self.article(words=1500)
        trimmed = trim_list_items(article)
        self.assertEqual((len(trimmed["sections"][1]["bullets"]), len(trimmed["sections"][3]["bullets"])), (3, 3))
        self.assertGreater(word_count(trimmed), 1200)
        within = make_article(sections=5, words=1000)
        self.assertIs(trim_list_items(within), within)

    def test_an_item_with_a_number_is_never_removed(self):
        article = self.article()
        article["sections"][1]["bullets"][-1] = "Một bộ gel thường có giá $50."
        article["claims"] = [fakes.claim()]
        trimmed = trim_list_items(article)
        self.assertEqual(trimmed["sections"][1]["bullets"], article["sections"][1]["bullets"])
        self.assertEqual(trimmed["sections"][3]["bullets"], article["sections"][3]["bullets"][:3])

    def test_steps_are_never_trimmed_even_when_the_article_is_over_the_ceiling(self):
        article = self.article(words=1500)
        article["sections"][3]["kind"] = "steps"
        article["sections"][3]["steps"] = [fakes.prose(10, i + 3, sentences=1) for i in range(6)]
        article["sections"][3]["bullets"] = []
        article["sections"][3]["paragraphs"][0] = "Làm theo 5 bước sau. " + article["sections"][3]["paragraphs"][0]
        trimmed = trim_list_items(article)
        self.assertEqual(trimmed["sections"][3]["steps"], article["sections"][3]["steps"])
        self.assertEqual(len(trimmed["sections"][1]["bullets"]), 3)           # plain bullets still go

    def test_a_bullets_list_whose_count_is_stated_in_the_prose_is_left_alone(self):
        for intro, count in (("Có 4 lý do chính.", 4), ("Đây là năm gợi ý.", 5), ("Chọn 6 mẫu sau.", 6)):
            with self.subTest(intro=intro):
                article = self.article(words=1500)
                section = article["sections"][3]
                section["bullets"] = [fakes.prose(10, i + 3, sentences=1) for i in range(count)]
                section["paragraphs"][1] = intro + " " + section["paragraphs"][1]
                trimmed = trim_list_items(article)
                self.assertEqual(trimmed["sections"][3]["bullets"], section["bullets"])
                self.assertEqual(len(trimmed["sections"][1]["bullets"]), 3)
        article = self.article(words=1500)
        article["sections"][3]["h3"] = [{"title": "Ý nhỏ", "paragraphs": ["Có tất cả 5 mẹo ở đây."]}]
        trimmed = trim_list_items(article)
        self.assertEqual(trimmed["sections"][3]["bullets"], article["sections"][3]["bullets"])


class StuffingRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_over_density_high_up_to_three_highest_count_sections_are_rewritten(self):
        from nailblog.quality import Issue
        from nailblog.writer import repair_article
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = settings(directory.name)
        service = Service(config, clock=lambda: 0.0)
        article = make_article(sections=5, words=900)
        for index, extra in ((1, 3), (3, 2), (4, 1)):
            article["sections"][index]["paragraphs"][1] += " màu nail merlot" * extra
        issue = Issue("kw_density", "fixable", "content", "từ khóa chính chiếm 3.09% số từ, nhồi từ khóa (trên 2.5%)")
        piece = {"paragraphs": [fakes.prose(45, 1), fakes.prose(45, 2)], "claims": []}
        transport = FakeTransport(lambda call, number: ollama_envelope({"body": piece, "body_2": piece, "body_3": piece}))
        text = TextModel(config, _test_transport=transport)
        result, repaired = await repair_article(text, article, [issue], sources=[fakes.SOURCE])
        request = transport.calls[0]["payload"]["messages"][0]["content"]
        self.assertEqual(len(transport.calls), 1)
        self.assertIn("thay một số lần nhắc NGUYÊN VĂN từ khóa chính bằng từ khóa phụ hoặc đại từ", request)
        self.assertNotIn('giữ NGUYÊN VĂN từ khóa chính "', request)
        for shown in (2, 4, 5):
            self.assertIn(f"Mục {shown} (H2:", request)
        for hidden in (1, 3):
            self.assertNotIn(f"Mục {hidden} (H2:", request)
        self.assertEqual(repaired, ["body"])
        self.assertEqual([result["sections"][i]["paragraphs"] for i in (1, 3, 4)], [piece["paragraphs"]] * 3)
        self.assertEqual(result["sections"][0]["paragraphs"], article["sections"][0]["paragraphs"])

    async def test_with_a_words_issue_the_note_shortens_and_swaps_and_includes_the_most_keyword_heavy_section(self):
        from nailblog.quality import Issue
        from nailblog.writer import repair_article
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = settings(directory.name)
        service = Service(config, clock=lambda: 0.0)
        article = make_article(sections=5, words=1500)
        article["sections"][4]["paragraphs"][1] = article["sections"][4]["paragraphs"][1] + " màu nail merlot" * 5
        article["sections"][4]["paragraphs"][1] = " ".join(article["sections"][4]["paragraphs"][1].split()[:30]) + \
            " màu nail merlot" * 5 + "."
        issues = [Issue("words", "fixable", "content", f"{word_count(article)} từ, cần 800-1200"),
                  Issue("kw_density", "fixable", "content", "từ khóa chính chiếm 3.09% số từ, nhồi từ khóa (trên 2.5%)")]
        piece = {"paragraphs": [fakes.prose(45, 1), fakes.prose(45, 2)], "claims": []}
        transport = FakeTransport(lambda call, number: ollama_envelope(
            {"body": piece, "body_2": piece, "body_3": piece}))
        text = TextModel(config, _test_transport=transport)
        await repair_article(text, article, issues, sources=[fakes.SOURCE])
        request = transport.calls[0]["payload"]["messages"][0]["content"]
        self.assertEqual(len(transport.calls), 1)
        self.assertIn("rút ngắn theo ngân sách từ VÀ thay một số lần nhắc NGUYÊN VĂN từ khóa chính", request)
        self.assertNotIn("giữ nguyên độ dài", request)
        self.assertIn("Mục 5 (H2:", request)                       # the keyword-heavy section is among the targets
        self.assertEqual(request.count("(H2:"), 3)


class MultiSectionRepairRequestTests(unittest.TestCase):
    def issue(self, words):
        from nailblog.quality import Issue
        return Issue("words", "fixable", "content", f"{words} từ, cần 800-1200")

    def article(self, counts):
        article = make_article(sections=len(counts), words=900)
        for section, count in zip(article["sections"], counts):
            section["paragraphs"] = [fakes.prose(count // 2, 3), fakes.prose(count - count // 2 - 2, 4)]
            section["h2"] = "Mục " + section["h2"].split()[-1]
        return article

    def test_over_the_ceiling_up_to_three_longest_sections_share_the_excess(self):
        article = self.article([150, 200, 160, 190, 120])
        counts = [section_words(s) for s in article["sections"]]
        targets = _body_targets(article, self.issue(1400))      # excess 200 (+80 margin)
        self.assertEqual([t[0] for t in targets], [1, 2, 3])
        self.assertTrue(all(t[1] == "ngắn hơn" for t in targets))
        removed = sum(counts[index] - budget for index, _direction, budget in targets)
        self.assertEqual(removed, 280)
        self.assertTrue(all(budget >= MIN_SECTION_WORDS for _i, _d, budget in targets))

    def test_a_small_excess_still_uses_one_section_and_a_short_article_the_shortest(self):
        article = self.article([150, 200, 160, 190, 120])
        counts = [section_words(s) for s in article["sections"]]
        self.assertEqual(_body_targets(article, self.issue(1230)), [(1, "ngắn hơn", counts[1] - 110)])
        short = _body_targets(article, self.issue(700))
        self.assertEqual([(i, d) for i, d, _ in short], [(4, "dài hơn")])


class MultiSectionRepairCallTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_repair_call_rewrites_every_targeted_section(self):
        from nailblog.quality import Issue
        from nailblog.writer import repair_article
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = settings(directory.name)
        service = Service(config, clock=lambda: 0.0)
        article = make_article(sections=5, words=1500)
        issue = Issue("words", "fixable", "content", f"{word_count(article)} từ, cần 800-1200")
        targets = _body_targets(article, issue)
        self.assertGreaterEqual(len(targets), 2)
        shorter = {"paragraphs": [fakes.prose(45, 1), fakes.prose(45, 2)], "claims": []}
        reply = {"body": shorter, **{f"body_{n + 1}": shorter for n in range(1, len(targets))}}
        transport = FakeTransport(lambda call, number: ollama_envelope(reply))
        text = TextModel(config, _test_transport=transport)
        result, repaired = await repair_article(text, article, [issue], sources=[fakes.SOURCE])
        self.assertEqual(len(transport.calls), 1)
        request = transport.calls[0]["payload"]["messages"][0]["content"]
        for number, (index, _direction, budget) in enumerate(targets):
            self.assertIn(f"Viết lại mục {index + 1} cho ngắn hơn, tối đa {budget} từ", request)
            self.assertEqual(result["sections"][index]["paragraphs"], shorter["paragraphs"])
        self.assertEqual(repaired, ["body"])
        self.assertIn('giữ NGUYÊN VĂN từ khóa chính "' + article["primary_keyword"] + '"', request)
        self.assertLess(word_count(result), word_count(article))


if __name__ == "__main__":
    unittest.main()
