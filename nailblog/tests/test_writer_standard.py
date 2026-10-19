"""Content standard in the writer: render order, code-made links, sources section, JSON-LD, word budget, prompts."""
import json
import re
import tempfile
import unittest
from datetime import date

from nailblog.config import LinksConfig
from nailblog.service import Service
from nailblog.text import TextModel
from nailblog.writer import (CLICHE_PHRASES, build_jsonld, build_links, build_sources, layout, needs_sources,
                             placeholder_images, render_html, word_budget, write_article)
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
        model = TextModel(config, Service(config).model_call,
                          transport=fakes.article_transport(outline_override={"claims": [self.LEGAL]}))
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
        model = TextModel(self.config, self.service.model_call, transport=transport)
        await write_article(model, {"title": "Nail mắt mèo nhung mùa đông 2026", "category": category, "product_promo": False},
                            [fakes.SOURCE], site_origin="https://nailhub.ai", today=TODAY)
        return [call["payload"]["messages"] for call in transport.calls]

    async def test_the_outline_request_carries_the_category_skeleton_and_the_budget(self):
        hot = (await self.calls("hot-trend"))[0][1]["content"]
        self.assertIn("Trend là gì và vì sao đang hot", hot)
        self.assertIn("cả bài tối đa 900 từ (trần 1000)", hot)
        self.assertIn("mở bài khoảng 70 từ", hot)
        self.assertIn("mỗi mục H2 khoảng 113 từ", hot)
        self.assertIn("FAQ đúng 3 câu", hot)
        job = (await self.calls("nail-job"))[0][1]["content"]
        self.assertIn("Giấy tờ và điều kiện (có nguồn)".lower(), job.lower())
        self.assertIn("cả bài tối đa 1080 từ (trần 1200)", job)
        self.assertIn("FAQ đúng 4 câu", job)
        dyk = (await self.calls("did-you-know"))[0][1]["content"]
        self.assertIn("Con số hoặc tin chính", dyk)
        salon = (await self.calls("nail-salon"))[0][1]["content"]
        self.assertIn("Vấn đề chủ tiệm gặp", salon)

    async def test_every_section_request_carries_its_own_budget_kind_keyword_and_points(self):
        messages = await self.calls("hot-trend", sections=5)
        for number, request in enumerate(messages[1:], 1):
            content = request[1]["content"]
            self.assertIn("Ngân sách: viết khoảng 113 từ cho cả mục (không dưới 90, không quá 113", content)
            self.assertIn("Mỗi đoạn khoảng 51 từ.", content)
            self.assertIn(f"Viết mục {number}:", content)
            self.assertIn("kind: paragraphs", content)
            self.assertIn("Ý chính: Ý chính của mục", content)
            self.assertIn("Từ khóa chính: màu nail merlot (phụ: merlot nails, nail đỏ rượu, gel merlot)", content)
        four = (await self.calls("hot-trend", sections=4))[1][1]["content"]
        self.assertIn("viết khoảng 141 từ cho cả mục (không dưới 112, không quá 141", four)
        self.assertIn("Mỗi đoạn khoảng 63 từ.", four)

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
        model = Spy(self.config, self.service.model_call, transport=fakes.article_transport())
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
        text = TextModel(self.config, self.service.model_call, transport=FakeTransport(transport))
        draft = await self.service.draft(self.topic, text, posts=fakes.RELATED_POSTS, today=TODAY)
        return draft, calls

    async def test_over_the_ceiling_the_longest_section_is_rewritten_once_with_a_budget_that_removes_the_excess(self):
        reply = {"body": {"paragraphs": [fakes.prose(45, 1, fakes.KEYWORD), fakes.prose(45, 2)],
                          "bullets": ["một ý ngắn", "hai ý ngắn", "ba ý ngắn"], "claims": []}}
        draft, calls = await self.draft(reply)
        self.assertEqual(len(calls), 7)                                   # exactly one repair call
        request = calls[-1]["payload"]["messages"][1]["content"]
        before = int(re.search(r"Độ dài bài: (\d+) từ, cần 600-1000\.", request).group(1))
        self.assertGreater(before, 1000)
        self.assertIn("Viết lại mục 4 cho ngắn hơn, tối đa 90 từ cho cả mục.", request)
        self.assertIn("Mục 4 (H2: Mục số D cho tiệm nail", request)
        self.assertEqual(draft.repaired, ["body"])
        section = draft.article["sections"][3]
        self.assertEqual((len(section["paragraphs"]), section["bullets"]), (2, ["một ý ngắn", "hai ý ngắn", "ba ý ngắn"]))
        self.assertEqual([len(s["paragraphs"]) for s in draft.article["sections"]], [2] * 5)
        self.assertEqual(draft.words, before - 186 + 6 + 90 + 9)
        self.assertIn("words", {issue.code for issue in draft.result.issues})   # still over: the gate says so, no 2nd round
        self.assertEqual(len(calls), 7)

    async def test_the_untouched_sections_and_claims_survive_the_rewrite(self):
        claim = fakes.claim()
        reply = {"body": {"paragraphs": [fakes.prose(45, 1), fakes.prose(45, 2)], "claims": [claim]}}
        draft, _ = await self.draft(reply)
        self.assertEqual(draft.article["claims"], [claim])
        self.assertEqual(draft.article["sections"][0]["paragraphs"][0][:15], fakes.prose(85, 0, fakes.KEYWORD)[:15])


if __name__ == "__main__":
    unittest.main()
