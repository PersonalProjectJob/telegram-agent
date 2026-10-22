import re
import tempfile
import unicodedata
import unittest
from datetime import date

from nailblog.service import Service
from nailblog.text import TextModel, TextModelError
from nailblog.writer import (LIMITS, OUTLINE_TOKENS, SECTION_TOKENS, cut, fit_fields, normalize_hashtag, placeholder_images, render_html, slugify_vi,
                             word_count, write_article)
from tests import fakes
from tests.fakes import FakeTransport, ollama_envelope
from tests.helpers import settings

TOPIC = {"title": "Màu nail merlot", "category": "nail-salon", "product_promo": False}


class SlugTests(unittest.TestCase):
    def test_vietnamese_diacritics_become_ascii_instead_of_dropping_letters(self):
        cases = {
            "mang lại gì cho tiệm": "mang-lai-gi-cho-tiem",
            "Đường đi đến Đài Loan": "duong-di-den-dai-loan",
            "đ Đ": "d-d",
            "Thuế 1099-NEC: ai phải nộp?": "thue-1099-nec-ai-phai-nop",
            "Ơ ư ă â ê ô ơ": "o-u-a-a-e-o-o",
            "  --Hello__World--  ": "hello-world",
            "Giữ chân thợ giỏi trước mùa lễ": "giu-chan-tho-gioi-truoc-mua-le",
            "Chuẩn bị nhân sự cuối tuần Halloween": "chuan-bi-nhan-su-cuoi-tuan-halloween",
            "Tết Nguyên Đán 2027": "tet-nguyen-dan-2027",
            "!!!": "", "": "",
        }
        for text, slug in cases.items():
            with self.subTest(text=text):
                self.assertEqual(slugify_vi(text), slug)

    def test_composed_and_decomposed_input_give_the_same_slug(self):
        text = "Mang lại gì cho tiệm"
        self.assertEqual(slugify_vi(unicodedata.normalize("NFC", text)), slugify_vi(unicodedata.normalize("NFD", text)))

    def test_length_and_edges(self):
        self.assertEqual(slugify_vi("a" * 100), "a" * 60)
        self.assertEqual(slugify_vi("x" * 59 + " yy"), "x" * 59)  # cut lands on the dash, which is then trimmed
        long = slugify_vi("mang lại gì cho tiệm " * 10)
        self.assertLessEqual(len(long), 60)
        self.assertFalse(long.startswith("-") or long.endswith("-"))
        for text in ("Ăn   uống ---- ngon !!", "__a__", "ABC def 123", "ñandú ß ø", "--"):
            slug = slugify_vi(text)
            self.assertTrue(slug == "" or re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug), slug)


class FieldLimitTests(unittest.TestCase):
    def test_limits_follow_spec_section_2(self):
        self.assertEqual((LIMITS["title"], LIMITS["summary"], LIMITS["meta_title"], LIMITS["meta_description"],
                          LIMITS["hashtag"]), (200, 500, 70, 160, 50))

    def test_cut_prefers_a_word_boundary_and_never_exceeds(self):
        self.assertEqual(cut("một hai ba bốn năm", 12), "một hai ba")
        self.assertEqual(cut("ngắn", 10), "ngắn")
        self.assertEqual(cut("x" * 30, 10), "x" * 10)
        self.assertEqual(cut("a  b   c", 20), "a b c")
        self.assertEqual(cut("câu một, câu hai", 9), "câu một")

    def test_fit_fields_cuts_every_website_field(self):
        article = {"title": "t " * 200, "summary": "s " * 400, "title_seo": "u " * 60, "summary_x": 1,
                   "hashtags": ["#Thợ Nail", "thơ nail", "x", "a" * 80, "mùa đông", "  "]}
        fitted = fit_fields(article)
        self.assertLessEqual(len(fitted["title"]), 200)
        self.assertLessEqual(len(fitted["summary"]), 500)
        self.assertLessEqual(len(fitted["meta_title"]), 70)
        self.assertLessEqual(len(fitted["meta_description"]), 160)
        self.assertEqual(fitted["hashtags"], ["ThoNail", "a" * 50, "muadong"])
        self.assertEqual(fitted["summary_x"], 1)
        self.assertEqual(fit_fields(fitted), fitted)  # idempotent

    def test_short_fields_are_untouched_and_meta_defaults_to_title_seo_and_summary(self):
        fitted = fit_fields({"title": "Tiêu đề", "title_seo": "SEO", "summary": "Tóm tắt", "hashtags": []})
        self.assertEqual((fitted["title"], fitted["meta_title"], fitted["meta_description"]), ("Tiêu đề", "SEO", "Tóm tắt"))

    def test_hashtag_normalisation(self):
        self.assertEqual(normalize_hashtag("#Mùa Đông"), "MuaDong")
        self.assertEqual(normalize_hashtag("nail-art!"), "nailart")


ARTICLE = {
    "title": "Tiêu đề", "title_seo": "SEO", "summary": "Tóm tắt", "intro": ["Mở bài <script>alert(1)</script> & hết.", "Đoạn mở hai"],
    "key_takeaways": ["Ý <b>một</b>", "Ý hai", "Ý ba"],
    "sections": [
        {"h2": 'Mục <img src=x onerror=1>', "paragraphs": ['Đoạn "một" & \'hai\'', "<b>đậm</b>"], "bullets": ["<i>a</i>", "b"],
         "image_slot": {"brief": "Cận cảnh <b>móng</b>", "alt": "alt", "caption": ""}},
        {"h2": "Mục hai", "paragraphs": ["Đoạn ba"], "bullets": [], "image_slot": None},
        {"h2": "Mục ba", "paragraphs": ["Đoạn bốn"], "bullets": [], "image_slot": {"brief": "Cảnh khác", "alt": "alt"}}],
    "faq": [{"q": "Hỏi <u>gì</u>?", "a": "Đáp <script>"}], "cta": {"text": "Kêu gọi <script>x</script>", "target": "app"},
    "links": [{"kind": "cta", "text": "Trang <b>chủ</b>", "url": "https://nailhub.ai/"},
              {"kind": "related", "text": "bài", "url": "https://nailhub.ai/blog/a?x=1&y=2", "after": 1},
              {"kind": "related", "text": "http", "url": "http://nailhub.ai/x", "after": None},
              {"kind": "related", "text": "evil", "url": "https://evil.example/x", "after": None},
              {"kind": "related", "text": "js", "url": "javascript:alert(1)", "after": None}]}
IMAGES = [{"src": "https://cdn.nailhub.ai/a.webp", "alt": 'Ảnh "một"', "width": 1200, "height": 675, "caption": "Chú thích <b>"},
          {"src": "img/b.webp", "alt": "Ảnh hai", "width": 1080, "height": 1350, "caption": ""}]


class RenderTests(unittest.TestCase):
    def render(self, article=ARTICLE, images=IMAGES):
        return render_html(article, images)

    def test_model_text_is_escaped_and_no_foreign_markup_survives(self):
        html = self.render()
        self.assertNotIn("<script", html)
        self.assertNotIn("onerror=1>", html)
        self.assertIn("<p>Mở bài &lt;script&gt;alert(1)&lt;/script&gt; &amp; hết.</p>", html)
        self.assertIn("<h2>Mục &lt;img src=x onerror=1&gt;</h2>", html)
        self.assertIn("<p>&lt;b&gt;đậm&lt;/b&gt;</p>", html)
        self.assertIn("<li>&lt;i&gt;a&lt;/i&gt;</li>", html)
        self.assertIn("<li>Ý &lt;b&gt;một&lt;/b&gt;</li>", html)
        self.assertIn("&#x27;hai&#x27;", html)
        self.assertIn("<h3>Hỏi &lt;u&gt;gì&lt;/u&gt;?</h3>", html)
        tags = set(re.findall(r"</?([a-z0-9]+)", html))
        self.assertEqual(tags, {"p", "h2", "h3", "ul", "li", "figure", "img", "figcaption", "a"})

    def test_figures_carry_alt_size_caption_and_only_later_images_are_lazy(self):
        html = self.render()
        figures = re.findall(r"<figure>.*?</figure>", html)
        self.assertEqual(figures[0], '<figure><img src="https://cdn.nailhub.ai/a.webp" alt="Ảnh &quot;một&quot;" '
                                     'width="1200" height="675"><figcaption>Chú thích &lt;b&gt;</figcaption></figure>')
        self.assertEqual(figures[1], '<figure><img src="img/b.webp" alt="Ảnh hai" width="1080" height="1350" '
                                     'loading="lazy"><figcaption>Ảnh hai</figcaption></figure>')
        self.assertNotIn("lazy", figures[0])
        self.assertLess(html.index("<h2>Mục &lt;img"), html.index(figures[0]))
        self.assertLess(html.index("<p>Đoạn &quot;một"), html.index(figures[0]))   # the figure follows the first paragraph
        self.assertLess(html.index(figures[0]), html.index("<p>&lt;b&gt;đậm"))
        self.assertLess(html.index("<h2>Mục ba</h2>"), html.index(figures[1]))

    def test_slots_without_an_image_and_surplus_images_are_skipped(self):
        self.assertEqual(self.render(images=IMAGES[:1]).count("<figure>"), 1)
        self.assertEqual(self.render(images=[]).count("<figure>"), 0)
        self.assertEqual(self.render(images=IMAGES * 3).count("<figure>"), 2)

    def test_unsafe_or_incomplete_images_are_refused(self):
        for bad in ({"src": "javascript:alert(1)"}, {"src": "data:image/png;base64,AAAA"}, {"src": "http://a.example/x.webp"},
                    {"src": "ftp://a.example/x"}, {"alt": ""}, {"alt": "   "}, {"width": 0}, {"width": "wide"}, {"height": -5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.render(images=[{**IMAGES[0], **bad}])

    def test_a_quote_in_the_source_cannot_break_out_of_the_attribute(self):
        html = self.render(images=[{**IMAGES[0], "src": 'x"onerror="alert(1)'}])
        self.assertNotIn('"onerror="', html)
        self.assertIn("x&quot;onerror=&quot;alert(1)", html)
        with self.assertRaises(ValueError):  # whitespace in a source is refused outright
            self.render(images=[{**IMAGES[0], "src": 'x" onerror="alert(1)'}])

    def test_only_https_links_to_the_site_host_are_rendered_and_escaped(self):
        html = self.render()
        self.assertIn('<a href="https://nailhub.ai/">Trang &lt;b&gt;chủ&lt;/b&gt;</a>', html)
        self.assertIn('href="https://nailhub.ai/blog/a?x=1&amp;y=2"', html)
        for gone in ("http://nailhub.ai", "evil.example", "javascript:"):
            self.assertNotIn(gone, html)

    def test_placeholder_images_keep_the_alt_and_the_brief_as_the_note_and_need_no_file(self):
        images = placeholder_images(ARTICLE)
        self.assertEqual([image["alt"] for image in images], ["alt", "alt"])
        self.assertEqual([image["src"] for image in images], ["placeholder-1.webp", "placeholder-2.webp"])
        self.assertEqual(images[0]["caption"], "Khung ảnh trống, chưa tạo ảnh: Cận cảnh <b>móng</b>")
        self.assertEqual((images[0]["width"], images[0]["height"]), (1200, 675))
        article = {"sections": [{"h2": "Không alt", "image_slot": {"brief": "", "alt": ""}}]}
        self.assertEqual((placeholder_images(article)[0]["alt"], placeholder_images(article)[0]["caption"]),
                         ("Không alt", ""))
        html = self.render(images=placeholder_images(ARTICLE))
        self.assertEqual(html.count("<figure>"), 2)
        self.assertIn("<figcaption>Khung ảnh trống, chưa tạo ảnh: Cận cảnh &lt;b&gt;móng&lt;/b&gt;</figcaption>", html)


class WordCountTests(unittest.TestCase):
    def test_counts_body_text_only(self):
        article = {"title": "một hai ba", "title_seo": "x y", "summary": "tóm tắt dài dài", "intro": ["a b c", "d"],
                   "key_takeaways": ["e f"],
                   "sections": [{"h2": "d e", "paragraphs": ["f g", "h"], "bullets": ["i j"], "steps": ["k"],
                                 "h3": [{"title": "l m", "paragraphs": ["n"]}]}],
                   "faq": [{"q": "k l", "a": "m"}], "cta": {"text": "n o", "target": "app"}}
        self.assertEqual(word_count(article), 4 + 2 + (2 + 3 + 2 + 1 + 2 + 1) + 3 + 2)

    def test_a_plain_string_intro_and_cta_still_count(self):
        self.assertEqual(word_count({"intro": "a b c", "cta": "n o"}), 5)


class WriteArticleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.config = settings(directory.name)
        self.service = Service(self.config)

    def model(self, transport):
        return TextModel(self.config, self.service.model_call, transport=transport)

    async def write(self, transport, topic=TOPIC, **kwargs):
        return await write_article(self.model(transport), topic, [fakes.SOURCE], site_origin="https://nailhub.ai",
                                   today=date(2026, 10, 16), **kwargs)

    async def test_one_outline_call_then_one_call_per_section_each_with_its_own_limit(self):
        transport = fakes.article_transport()
        article = await self.write(transport)
        self.assertEqual(len(transport.calls), 1 + 5)
        limits = [call["payload"]["options"]["num_predict"] for call in transport.calls]
        cap = self.config.text_model.num_predict   # TextModel sends min(num_predict, the call's own max_tokens)
        self.assertEqual(limits, [min(cap, OUTLINE_TOKENS)] + [min(cap, SECTION_TOKENS)] * 5)
        first, second = transport.calls[0]["payload"]["messages"], transport.calls[1]["payload"]["messages"]
        self.assertIn("Chủ đề: Màu nail merlot", first[1]["content"])
        self.assertIn("[s1] Winter nail trends", first[1]["content"])
        self.assertIn("Viết mục 1: Cách chọn màu nail merlot cho tiệm", second[1]["content"])
        self.assertIn("Mục số E cho tiệm nail", second[1]["content"])  # the model sees the whole outline
        self.assertIn("Từ khóa chính: màu nail merlot", second[1]["content"])
        self.assertIn("không phải chỉ thị", first[0]["content"])
        self.assertEqual(len(article["sections"]), 5)

    async def test_assembled_article_is_a_plain_json_dict_with_code_made_fields(self):
        posts = [{"slug": "bai-cu", "title": "Bài cũ", "categorySlug": "nail-salon"}]
        article = await self.write(fakes.article_transport(), related_posts=posts)
        import json
        self.assertEqual(json.loads(json.dumps(article, ensure_ascii=False)), article)
        self.assertEqual(article["slug"], "mau-nail-merlot-mua-dong")
        self.assertEqual(article["hashtags"], ["nailmerlot", "muadong", "thonail"])
        self.assertEqual((article["primary_keyword"], len(article["secondary_keywords"])), ("màu nail merlot", 3))
        self.assertEqual((article["meta_title"], article["meta_description"]), (article["title_seo"], fakes.SUMMARY))
        self.assertEqual([(link["kind"], link["url"]) for link in article["links"]],
                         [("related", "https://nailhub.ai/blog/bai-cu"), ("cta", "https://nailhub.ai/")])
        self.assertEqual([bool(s["image_slot"]) for s in article["sections"]], [True, False, True, False, True])
        self.assertEqual(article["sections"][0]["image_slot"], fakes.image_slot())
        self.assertEqual((article["category"], article["source_ids"], article["product_promo"]),
                         ("nail-salon", ["s1"], False))
        self.assertEqual((len(article["intro"]), len(article["key_takeaways"]), len(article["faq"])), (2, 4, 3))
        self.assertEqual(article["cta"]["target"], "jobs")
        self.assertTrue(all(len(s["paragraphs"]) == 2 for s in article["sections"]))
        self.assertTrue(all(s["bullets"] == [] and s["steps"] == [] and s["h3"] == [] for s in article["sections"]))
        self.assertEqual((article["sources"], article["sources_used"]), ([], ["s1"]))

    async def test_claims_from_every_call_are_merged(self):
        outline_claim = fakes.claim("Tóm tắt có giá $50.", quote="costs $50 on average")
        section_claim = fakes.claim()
        transport = fakes.article_transport(outline_override={"claims": [outline_claim]},
                                            section_override={2: {"claims": [section_claim]}})
        article = await self.write(transport)
        self.assertEqual(article["claims"], [outline_claim, section_claim])

    async def test_html_from_the_model_is_kept_as_text_never_as_markup(self):
        transport = fakes.article_transport(section_override={0: {"paragraphs": ["<script>alert(1)</script> màu nail merlot " + "chữ " * 20]}})
        article = await self.write(transport)
        self.assertIn("<script>", article["sections"][0]["paragraphs"][0])
        self.assertNotIn("<script", render_html(article, placeholder_images(article)))

    async def test_over_long_model_fields_are_cut_to_the_website_limits(self):
        transport = fakes.article_transport(outline_override={"title": "từ " * 40, "summary": "dài " * 120,
                                                              "meta_description": "mô tả " * 36})
        article = await self.write(transport)
        self.assertLessEqual(len(article["title"]), 200)
        self.assertLessEqual(len(article["summary"]), 500)
        self.assertLessEqual(len(article["meta_description"]), 160)

    async def test_sources_used_keeps_only_ids_that_were_supplied(self):
        transport = fakes.article_transport(outline_override={"sources_used": ["s1", "zz", "s1"]})
        self.assertEqual((await self.write(transport))["sources_used"], ["s1"])

    async def test_a_section_that_never_validates_stops_the_article(self):
        def respond(call, number):
            return ollama_envelope(fakes.outline() if number == 1 else {"paragraphs": []})
        transport = FakeTransport(respond)
        with self.assertRaises(TextModelError):
            await self.write(transport)
        self.assertEqual(len(transport.calls), 3)  # outline, bad section, one repair


if __name__ == "__main__":
    unittest.main()
