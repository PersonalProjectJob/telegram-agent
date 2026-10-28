import json
import unittest

from nailblog.quality import FIXABLE, HOLD, QualityResult, gate
from nailblog.writer import word_count
from tests.fakes import KEYWORD, image_slot, make_article, with_words

SLOT = "2026-10-20T19:00+07:00"


def run(article=None, **kwargs):
    defaults = dict(category="nail-salon", slot_at=SLOT, existing_slugs=[], others=[], ungrounded=[])
    defaults.update(kwargs)
    return gate(make_article() if article is None else article, **defaults)


def codes(result):
    return sorted(issue.code for issue in result.issues)


def with_keyword(length):
    """A string of exactly `length` characters that starts with the primary keyword."""
    return (KEYWORD + " " + "x" * length)[:length]


class BaselineTests(unittest.TestCase):
    def test_a_good_article_passes_with_no_issues(self):
        result = run()
        self.assertEqual((result.ok, result.verdict, result.issues), (True, "pass", ()))
        self.assertEqual(word_count(make_article()), 900)

    def test_result_is_structured_and_json_serialisable(self):
        result = run(make_article(title_seo=with_keyword(64), slug="Bad Slug"))
        data = json.loads(json.dumps(result.as_dict()))
        self.assertEqual(data["verdict"], "hold")
        self.assertEqual({issue["code"] for issue in data["issues"]}, {"title_seo", "slug"})
        self.assertTrue(all(set(issue) == {"code", "kind", "field", "message"} for issue in data["issues"]))
        self.assertEqual([issue.code for issue in result.fixable], ["title_seo"])
        self.assertEqual([issue.code for issue in result.hold], ["slug"])
        self.assertIsInstance(result, QualityResult)

    def test_fixable_only_gives_the_fixable_verdict(self):
        result = run(make_article(title_seo=with_keyword(64)))
        self.assertEqual((result.ok, result.verdict, len(result.hold)), (False, "fixable", 0))


class LengthTests(unittest.TestCase):
    def test_nail_salon_and_nail_job_need_760_to_1260_words(self):
        for category in ("nail-salon", "nail-job"):
            for words, ok in ((759, False), (760, True), (1260, True), (1261, False)):
                with self.subTest(category=category, words=words):
                    result = run(with_words(make_article(), words), category=category)
                    self.assertEqual("words" not in codes(result), ok)

    def test_hot_trend_and_did_you_know_need_570_to_1050_words(self):
        for category in ("hot-trend", "did-you-know"):
            for words, ok in ((569, False), (570, True), (700, True), (1050, True), (1051, False)):
                with self.subTest(category=category, words=words):
                    self.assertEqual("words" not in codes(run(with_words(make_article(), words), category=category)), ok)
        self.assertIn("words", codes(run(with_words(make_article(), 700))))       # 700 is short for nail-salon
        self.assertIn("words", codes(run(with_words(make_article(), 1100), category="hot-trend")))

    def test_length_issues_are_fixable_and_say_the_range(self):
        issue = next(i for i in run(with_words(make_article(), 700)).issues if i.code == "words")
        self.assertEqual((issue.kind, issue.message), (FIXABLE, "700 từ, cần 760-1260"))
        issue = next(i for i in run(with_words(make_article(), 1100), category="hot-trend").issues if i.code == "words")
        self.assertEqual(issue.message, "1100 từ, cần 570-1050")


class StructureTests(unittest.TestCase):
    def test_four_to_six_h2_sections(self):
        for count, slots, ok in ((3, (0, 2), False), (4, (0, 2), True), (6, (0, 2, 4), True), (7, (0, 2, 4), False)):
            with self.subTest(count=count):
                result = run(make_article(sections=count, slots=slots))
                self.assertEqual("h2_count" not in codes(result), ok)
                self.assertTrue(all(i.kind == HOLD for i in result.issues if i.code == "h2_count"))

    def test_slug_rules(self):
        self.assertEqual(codes(run(make_article(slug="mau-nail-merlot-a-b-1"))), [])
        for bad in ("Bad Slug", "-a", "a-", "a--b", "a" * 61, "", "tiệm-nail", "a_b"):
            with self.subTest(slug=bad):
                self.assertEqual(codes(run(make_article(slug=bad))), ["slug"])
        self.assertEqual(codes(run(make_article(slug=("mau-nail-merlot-" + "a" * 44)))), [])      # 60 characters
        self.assertEqual(codes(run(make_article(slug=("mau-nail-merlot-" + "a" * 45)))), ["slug"])  # 61

    def test_slug_must_not_exist_already(self):
        article = make_article()
        self.assertEqual(codes(run(article, existing_slugs=["khac", "mau-nail-merlot-mua-dong"])), ["slug_duplicate"])
        self.assertEqual(codes(run(article, existing_slugs=["khac"])), [])

    def test_unknown_published_list_is_held_never_assumed(self):
        (issue,) = run(existing_slugs=None).issues
        self.assertEqual((issue.code, issue.kind), ("slug_unverified", HOLD))

    def test_three_to_five_hashtags_each_up_to_50_characters(self):
        for tags, ok in ((["a1", "b1"], False), (["a1", "b1", "c1"], True), (list("abcde"), True),
                         (list("abcdef"), False), (["a1", "b1", ""], False), (["a1", "b1", "x" * 51], False),
                         (["a1", "b1", "x" * 50], True), (["a1", "b1", 5], False)):
            with self.subTest(tags=tags):
                self.assertEqual("hashtags" not in codes(run(make_article(hashtags=tags))), ok)

    def test_category_must_be_known(self):
        self.assertEqual(codes(run(category="news")), ["category"])
        for category in ("hot-trend", "nail-job", "nail-salon", "did-you-know"):
            self.assertEqual(codes(run(make_article(category=category), category=category)), [])


class SeoFieldTests(unittest.TestCase):
    def fixable(self, field, value):
        result = run(make_article(**{field: value}))
        return [issue.code for issue in result.issues if issue.kind == FIXABLE]

    def test_title_seo_at_most_63(self):
        self.assertEqual(self.fixable("title_seo", with_keyword(63)), [])
        self.assertEqual(self.fixable("title_seo", with_keyword(64)), ["title_seo"])
        self.assertEqual(sorted(self.fixable("title_seo", "")), ["kw_title", "title_seo"])
        self.assertEqual(sorted(self.fixable("title_seo", "   ")), ["kw_title", "title_seo"])

    def test_title_at_most_94(self):
        self.assertEqual(self.fixable("title", "x" * 94), [])
        self.assertEqual(self.fixable("title", "x" * 95), ["title"])

    def test_summary_between_133_and_168(self):
        for length, ok in ((132, False), (133, True), (168, True), (169, False)):
            with self.subTest(length=length):
                self.assertEqual(self.fixable("summary", "s" * length), [] if ok else ["summary"])

    def test_meta_title_and_description_limits(self):
        self.assertEqual(self.fixable("meta_title", "m" * 63), [])
        self.assertEqual(self.fixable("meta_title", "m" * 64), ["meta_title"])
        for length, ok in ((132, False), (133, True), (168, True), (169, False)):
            with self.subTest(length=length):
                self.assertEqual(self.fixable("meta_description", with_keyword(length)), [] if ok else ["meta_description"])
        self.assertEqual(sorted(self.fixable("meta_description", "")), ["kw_meta", "meta_description"])

    def test_a_meta_title_copied_from_title_seo_is_judged_once(self):
        result = run(make_article(title_seo=with_keyword(64), meta_title=with_keyword(64)))
        self.assertEqual([i.code for i in result.issues], ["title_seo"])


class ImageDeclarationTests(unittest.TestCase):
    def test_three_to_five_body_image_slots(self):
        for slots, ok in (((0, 2), False), ((0, 2, 4), True), ((0, 1, 2, 3, 4), True), ((0, 1, 2, 3, 4, 5), False)):
            with self.subTest(slots=slots):
                article = make_article(sections=6, slots=slots)
                self.assertEqual("image_slots" not in codes(run(article)), ok)
        self.assertEqual(codes(run(make_article(slots=(0, 2)))), ["image_slots"])

    def test_alt_text_is_5_to_15_words_for_every_declared_slot(self):
        def with_alt(words):
            article = make_article()
            article["sections"][0]["image_slot"] = image_slot(alt=" ".join(["từ"] * words))
            return article
        for words, ok in ((4, False), (5, True), (15, True), (16, False)):
            with self.subTest(words=words):
                self.assertEqual("image_alt" not in codes(run(with_alt(words))), ok)

    def test_a_slot_needs_a_brief(self):
        article = make_article()
        article["sections"][0]["image_slot"] = image_slot(brief="  ")
        (issue,) = run(article).issues
        self.assertEqual((issue.code, issue.kind, issue.field), ("image_brief", HOLD, "sections[0].image_slot.brief"))

    def test_image_problems_are_holds_not_fixable(self):
        result = run(make_article(slots=(0, 2)))
        self.assertEqual((result.verdict, codes(result)), ("hold", ["image_slots"]))
        self.assertEqual(result.fixable, ())

    def test_a_cover_declaration_is_not_part_of_the_content_standard(self):
        self.assertEqual(codes(run(make_article(cover_alt=""))), [])


class LinkTests(unittest.TestCase):
    @staticmethod
    def links(*urls):
        return [{"kind": "related", "text": "x", "url": url, "after": None} for url in urls]

    def test_cta_is_required(self):
        self.assertEqual(codes(run(make_article(cta={"text": "", "target": "app"}))), ["cta"])
        self.assertEqual(codes(run(make_article(cta={"text": "   ", "target": "app"}))), ["cta"])
        self.assertEqual(codes(run(make_article(cta="Xem tin tuyển thợ trên NailHub ngay hôm nay"))), [])

    def test_zero_links_hold_one_is_fixable_two_or_three_pass(self):
        good = ["https://nailhub.ai/a", "https://nailhub.ai/b", "https://nailhub.ai/c", "https://nailhub.ai/d"]
        for count, expected in ((0, [("internal_links", HOLD)]), (1, [("internal_links", FIXABLE)]), (2, []), (3, []),
                                (4, [("internal_links", FIXABLE)])):
            with self.subTest(count=count):
                result = run(make_article(links=self.links(*good[:count])))
                self.assertEqual([(i.code, i.kind) for i in result.issues], expected)

    def test_only_https_links_to_the_site_host_count_and_repeats_count_once(self):
        for url in ("http://nailhub.ai/", "https://evil.example/", "https://nailhub.ai.evil.example/", "javascript:x", ""):
            with self.subTest(url=url):
                result = run(make_article(links=self.links("https://nailhub.ai/a", url)))
                self.assertEqual([(i.code, i.kind) for i in result.issues], [("internal_links", FIXABLE)])
        result = run(make_article(links=self.links("https://nailhub.ai/a", "https://nailhub.ai/a")))
        self.assertEqual([(i.code, i.kind) for i in result.issues], [("internal_links", FIXABLE)])


class ScheduleRuleTests(unittest.TestCase):
    def test_one_product_post_per_day(self):
        article = make_article(product_promo=True)
        same_day = [{"slot_at": "2026-10-20T06:00+07:00", "product_promo": True}]
        self.assertEqual(codes(run(article, product_promo=True, others=same_day)), ["product_promo"])
        for others in ([{"slot_at": "2026-10-21T06:00+07:00", "product_promo": True}],
                       [{"slot_at": "2026-10-20T06:00+07:00", "product_promo": False}], []):
            self.assertEqual(codes(run(article, product_promo=True, others=others)), [])
        self.assertEqual(codes(run(article, product_promo=False, others=same_day)), [])

    def test_the_day_is_the_vietnam_calendar_day(self):
        others = [{"slot_at": "2026-10-19T17:30:00+00:00", "product_promo": True}]  # 00:30 on the 20th in Vietnam
        self.assertEqual(codes(run(product_promo=True, others=others)), ["product_promo"])
        others = [{"slot_at": "2026-10-20T16:30:00+00:00", "product_promo": True}]  # 23:30 on the 20th in Vietnam
        self.assertEqual(codes(run(product_promo=True, others=others)), ["product_promo"])
        others = [{"slot_at": "2026-10-20T17:30:00+00:00", "product_promo": True}]  # 00:30 on the 21st in Vietnam
        self.assertEqual(codes(run(product_promo=True, others=others)), [])

    def test_a_slot_already_taken_by_another_post_is_refused(self):
        self.assertEqual(codes(run(others=[{"slot_at": SLOT, "product_promo": False}])), ["slot_taken"])
        utc_same_instant = [{"slot_at": "2026-10-20T12:00:00+00:00", "product_promo": False}]
        self.assertEqual(codes(run(others=utc_same_instant)), ["slot_taken"])
        self.assertEqual(codes(run(others=[{"slot_at": "2026-10-20T06:00+07:00", "product_promo": False}])), [])
        self.assertEqual(codes(run(others=[{"slot_at": "garbage"}, {}, {"slot_at": "2026-10-20T06:00"}])), [])

    def test_invalid_or_absent_slot(self):
        self.assertEqual(codes(run(slot_at="2026-10-20T19:00")), ["slot"])
        self.assertEqual(codes(run(slot_at="garbage")), ["slot"])
        self.assertEqual(codes(run(slot_at=None, others=[{"slot_at": SLOT, "product_promo": True}])), [])


class FactsRuleTests(unittest.TestCase):
    def test_each_ungrounded_number_is_a_hold(self):
        found = [{"number": "$50", "field": "sections[0].paragraphs[0]", "reason": "no_claim"},
                 {"number": "2026", "field": "title", "reason": "quote_not_in_source"}]
        result = run(ungrounded=found)
        self.assertEqual((result.verdict, codes(result)), ("hold", ["facts", "facts"]))
        self.assertTrue(all(issue.kind == HOLD for issue in result.issues))
        self.assertIn("$50", result.issues[0].message)
        self.assertEqual(result.issues[1].field, "title")


if __name__ == "__main__":
    unittest.main()
