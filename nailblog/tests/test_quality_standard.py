"""Content standard section 7: every code of the gate with its boundaries (values on both sides of each limit)."""
import unittest

from nailblog.quality import FIXABLE, HOLD, capital_ratio, gate, sentences_of
from nailblog.writer import CLICHE_PHRASES
from tests.fakes import KEYWORD, SECONDARY, make_article, prose

SLOT = "2026-10-20T19:00+07:00"


def run(article, **kwargs):
    defaults = dict(category="nail-salon", slot_at=SLOT, existing_slugs=[], others=[], ungrounded=[])
    defaults.update(kwargs)
    return gate(article, **defaults)


def codes(result):
    return sorted(issue.code for issue in result.issues)


def issue_of(result, code):
    return next(issue for issue in result.issues if issue.code == code)


def words(count, filler="thợ"):
    return " ".join([filler] * count)


def sentence_block(sentences, size):
    return ". ".join(words(size) for _ in range(sentences)).capitalize() + "."


class KeywordPlacementTests(unittest.TestCase):
    def test_kw_title_is_checked_on_the_meta_title_ignoring_accents_and_case(self):
        result = run(make_article(title_seo="Nail merlot mùa đông: cách chọn màu"))
        self.assertEqual((codes(result), issue_of(result, "kw_title").field, issue_of(result, "kw_title").kind),
                         (["kw_title"], "title_seo", FIXABLE))
        self.assertEqual(codes(run(make_article(title_seo="Mau nail merlot mua dong"))), [])

    def test_kw_meta(self):
        article = make_article(meta_description=("Bộ móng đẹp cho mùa đông " * 7)[:150])
        result = run(article)
        self.assertEqual((codes(result), issue_of(result, "kw_meta").field), (["kw_meta"], "meta_description"))
        self.assertEqual(codes(run(make_article(meta_description=KEYWORD.upper() + " " + "d" * 130))), [])

    def test_kw_slug_needs_every_keyword_word_in_the_slug_in_any_order(self):
        for slug, ok in (("mau-nail-merlot-mua-dong", True), ("merlot-nail-mau", True), ("mau-nail-mua-dong", False),
                         ("nail-mat-meo", False)):
            with self.subTest(slug=slug):
                self.assertEqual("kw_slug" not in codes(run(make_article(slug=slug))), ok)
        self.assertEqual(issue_of(run(make_article(slug="nail-mat-meo")), "kw_slug").kind, FIXABLE)

    def test_kw_intro_means_inside_the_first_100_words(self):
        def with_intro(lead):
            article = make_article()
            article["intro"] = [words(lead) + " " + KEYWORD, words(10)]
            return article
        self.assertNotIn("kw_intro", codes(run(with_intro(97))))     # words 98-100
        self.assertIn("kw_intro", codes(run(with_intro(98))))        # words 99-101: cut by the 100-word window
        self.assertEqual(issue_of(run(with_intro(98)), "kw_intro").field, "intro")

    def test_kw_h2_accepts_the_primary_or_a_secondary_keyword(self):
        def with_h2(text):
            article = make_article()
            for section in article["sections"]:
                section["h2"] = "Mục số một cho tiệm"
            article["sections"][2]["h2"] = text
            return article
        self.assertIn("kw_h2", codes(run(with_h2("Mục số ba cho tiệm"))))
        self.assertNotIn("kw_h2", codes(run(with_h2(f"Cách chọn {KEYWORD} cho tiệm"))))
        self.assertNotIn("kw_h2", codes(run(with_h2(f"Cách chọn {SECONDARY[0].upper()} cho tiệm"))))

    def test_a_missing_primary_keyword_is_one_fixable_issue_and_skips_the_placement_checks(self):
        result = run(make_article(primary_keyword=" "))
        self.assertEqual(codes(result), ["primary_keyword"])


def spread_keyword(article, total):
    """Make the keyword occur exactly `total` times: once in the intro, once in the first H2, the rest spread over
    the section paragraphs (every occurrence replaces the first words of a paragraph, so no word count changes)."""
    filler = "thợ tiệm khách"
    texts = [article["intro"], [section["h2"] for section in article["sections"]],
             [p for section in article["sections"] for p in section["paragraphs"]]]
    clean = lambda text: text.replace(KEYWORD, filler).replace(KEYWORD.capitalize(), filler.capitalize())  # noqa: E731
    article["intro"] = [clean(text) for text in texts[0]]
    article["intro"][0] = KEYWORD + article["intro"][0][len(filler):]
    for section in article["sections"]:
        section["h2"] = clean(section["h2"])
        section["paragraphs"] = [clean(p) for p in section["paragraphs"]]
    article["sections"][0]["h2"] = f"Cách chọn {KEYWORD} cho tiệm"
    slots = [(i, j) for i, section in enumerate(article["sections"]) for j in range(len(section["paragraphs"]))]
    extra = total - 2
    counts = {slot: extra // len(slots) + (1 if n < extra % len(slots) else 0) for n, slot in enumerate(slots)}
    for (i, j), count in counts.items():
        parts = article["sections"][i]["paragraphs"][j].split()
        article["sections"][i]["paragraphs"][j] = " ".join(KEYWORD.split() * count + parts[3 * count:])
    return article


class KeywordDensityTests(unittest.TestCase):
    def test_density_is_judged_on_the_900_word_body_with_a_floor_at_half_a_percent(self):
        for total, ok in ((4, False), (5, True), (13, True), (22, True), (23, False)):
            with self.subTest(occurrences=total):
                result = run(spread_keyword(make_article(), total))
                self.assertEqual("kw_density" not in codes(result), ok, [i.message for i in result.issues])

    def test_density_issue_is_fixable_and_says_what_is_wrong(self):
        low = issue_of(run(spread_keyword(make_article(), 4)), "kw_density")
        high = issue_of(run(spread_keyword(make_article(), 23)), "kw_density")
        self.assertEqual((low.kind, high.kind), (FIXABLE, FIXABLE))
        self.assertIn("ít nhất 0.5%", low.message)
        self.assertIn("nhồi từ khóa", high.message)


class IntroAndTakeawaysTests(unittest.TestCase):
    def test_the_intro_is_two_paragraphs_of_50_to_90_words(self):
        def intro(*sizes):
            article = make_article()
            article["intro"] = [prose(size, 1, KEYWORD) for size in sizes]
            return article
        for sizes, ok in (((25, 24), False), ((25, 25), True), ((45, 45), True), ((45, 46), False), ((90,), False),
                          ((25, 25, 10), False)):
            with self.subTest(sizes=sizes):
                result = run(intro(*sizes))
                self.assertEqual("intro_len" not in codes(result), ok)
        issue = issue_of(run(intro(25, 24)), "intro_len")
        self.assertEqual((issue.kind, issue.field), (FIXABLE, "intro"))

    def test_three_to_four_takeaways_of_at_most_20_words(self):
        for lines, ok in ((2, False), (3, True), (4, True), (5, False)):
            with self.subTest(lines=lines):
                article = make_article(key_takeaways=[words(12)] * lines)
                self.assertEqual("takeaways" not in codes(run(article)), ok)
        for size, ok in ((20, True), (21, False)):
            with self.subTest(size=size):
                article = make_article(key_takeaways=[words(12), words(12), words(size)])
                self.assertEqual("takeaways" not in codes(run(article)), ok)


class ParagraphTests(unittest.TestCase):
    def with_paragraph(self, text):
        article = make_article()
        article["sections"][1]["paragraphs"][1] = text
        return article

    def test_a_paragraph_has_40_to_90_words(self):
        for size, ok in ((39, False), (40, True), (90, True)):
            with self.subTest(size=size):
                self.assertEqual("para_len" not in codes(run(self.with_paragraph(prose(size, 5)))), ok)

    def test_one_paragraph_may_reach_110_words_a_second_one_may_not(self):
        self.assertNotIn("para_len", codes(run(self.with_paragraph(prose(110, 5)))))
        self.assertNotIn("para_len", codes(run(self.with_paragraph(prose(91, 5)))))
        self.assertIn("para_len", codes(run(self.with_paragraph(prose(111, 5)))))
        article = self.with_paragraph(prose(100, 5))
        article["sections"][2]["paragraphs"][1] = prose(95, 6)
        self.assertIn("para_len", codes(run(article)))
        for second, ok in ((90, True), (91, False)):               # one 91-word paragraph is fine, two are not
            with self.subTest(second=second):
                article = self.with_paragraph(prose(91, 5))
                article["sections"][2]["paragraphs"][1] = prose(second, 6)
                self.assertEqual("para_len" not in codes(run(article)), ok)

    def test_a_paragraph_has_two_to_four_sentences(self):
        for sentences, ok in ((1, False), (2, True), (4, True), (5, False)):
            with self.subTest(sentences=sentences):
                text = sentence_block(sentences, 50 // sentences if sentences > 1 else 15) if sentences > 1 else \
                    words(50) + "."
                self.assertEqual("para_len" not in codes(run(self.with_paragraph(text))), ok)
        issue = issue_of(run(self.with_paragraph(words(50) + ".")), "para_len")
        self.assertEqual((issue.kind, issue.field), (FIXABLE, "sections[1].paragraphs[1]"))

    def test_h3_paragraphs_follow_the_same_rule(self):
        article = make_article()
        article["sections"][1]["h3"] = [{"title": "Ý nhỏ một", "paragraphs": [prose(20, 1)]}]
        issue = issue_of(run(article), "para_len")
        self.assertEqual(issue.field, "sections[1].h3[0].paragraphs[0]")

    def test_sentences_are_split_after_a_stop_and_a_space_so_decimals_stay_whole(self):
        self.assertEqual(sentences_of("Giá 3.5 đô là rẻ. Hết! Thật không? Ok"), ["Giá 3.5 đô là rẻ.", "Hết!", "Thật không?", "Ok"])


class SentenceLengthTests(unittest.TestCase):
    def test_no_sentence_may_pass_35_words(self):
        for size, ok in ((35, True), (36, False)):
            with self.subTest(size=size):
                article = make_article()
                article["sections"][1]["paragraphs"][1] = words(size).capitalize() + ". " + prose(24, 3)
                self.assertEqual("sentence_len" not in codes(run(article)), ok)

    def test_the_average_may_not_pass_20_words(self):
        for size, ok in ((20, True), (26, False)):
            with self.subTest(size=size):
                article = make_article()
                for section in article["sections"]:
                    section["paragraphs"] = [sentence_block(3, size) for _ in section["paragraphs"]]
                result = run(article)
                self.assertEqual("sentence_len" not in codes(result), ok)
        issue = issue_of(run(article), "sentence_len")
        self.assertEqual(issue.kind, FIXABLE)


class WallOfTextTests(unittest.TestCase):
    def with_paragraphs(self, count, slot=True, bullets=False):
        article = make_article()
        section = article["sections"][1]
        section["paragraphs"] = [prose(50, i) for i in range(count)]
        section["image_slot"] = section["image_slot"] if slot else None
        if bullets:
            section["bullets"] = ["một ý", "hai ý", "ba ý"]
        return article

    def test_more_than_three_paragraphs_in_a_row_is_a_wall_of_text(self):
        self.assertNotIn("wall_of_text", codes(run(self.with_paragraphs(3, slot=False))))
        result = run(self.with_paragraphs(4, slot=False))
        self.assertIn("wall_of_text", codes(result))
        self.assertEqual(issue_of(result, "wall_of_text").kind, FIXABLE)

    def test_a_figure_a_list_or_a_heading_breaks_the_run(self):
        article = self.with_paragraphs(4, slot=True)
        article["sections"][1]["image_slot"] = {"brief": "Cận cảnh bộ móng", "alt": "Ảnh bộ móng merlot cận cảnh"}
        self.assertNotIn("wall_of_text", codes(run(article)))      # the figure follows the first paragraph: 1 + 3
        self.assertNotIn("wall_of_text", codes(run(self.with_paragraphs(3, slot=False, bullets=True))))

    def test_h3_blocks_break_the_run_too(self):
        article = self.with_paragraphs(3, slot=False)
        article["sections"][1]["h3"] = [{"title": "Ý nhỏ một", "paragraphs": [prose(50, 1), prose(50, 2)]}]
        self.assertNotIn("wall_of_text", codes(run(article)))


class ClicheTests(unittest.TestCase):
    def with_text(self, text):
        article = make_article()
        article["sections"][1]["paragraphs"][1] = text
        return article

    def test_every_banned_phrase_is_caught_in_any_case(self):
        for phrase in CLICHE_PHRASES:
            with self.subTest(phrase=phrase):
                text = prose(40, 7) + " " + phrase.upper() + " " + prose(10, 8)
                result = run(self.with_text(text))
                self.assertIn("cliche", codes(result))
                self.assertEqual(issue_of(result, "cliche").kind, FIXABLE)
        self.assertIn("cliche", codes(run(self.with_text(prose(40, 7) + " Không chỉ nhẹ mà còn bền. " + prose(10, 8)))))

    def test_clean_text_a_longer_word_and_a_heading_are_judged_correctly(self):
        self.assertNotIn("cliche", codes(run(self.with_text(prose(48, 7)))))
        article = make_article()
        article["sections"][1]["h2"] = "Chọn màu sang trọng cho tiệm"
        self.assertIn("cliche", codes(run(article)))
        self.assertEqual(issue_of(run(article), "cliche").field, "sections[1].h2")


class FaqTests(unittest.TestCase):
    def faq(self, count, size=45):
        return [{"q": f"Câu hỏi số {i} là gì nhỉ?", "a": prose(size, i)} for i in range(count)]

    def test_three_to_five_questions(self):
        for count, ok in ((2, False), (3, True), (5, True), (6, False)):
            with self.subTest(count=count):
                self.assertEqual("faq" not in codes(run(make_article(faq=self.faq(count)))), ok)

    def test_each_answer_has_30_to_60_words(self):
        for size, ok in ((29, False), (30, True), (60, True), (61, False)):
            with self.subTest(size=size):
                result = run(make_article(faq=self.faq(3, size)))
                self.assertEqual("faq" not in codes(result), ok)
        issue = issue_of(run(make_article(faq=self.faq(3, 29))), "faq")
        self.assertEqual((issue.kind, issue.field), (FIXABLE, "faq[0].a"))


class SourcesSectionTests(unittest.TestCase):
    LEGAL = {"text": "Phí gia hạn license là $50.", "source_id": "s1", "quote": "License renewal costs $50 each year"}
    OTHER = {"text": "Một bộ gel thường có giá $50.", "source_id": "s1", "quote": "a gel manicure costs $50 on average"}

    def test_a_legal_number_without_a_sources_section_is_a_hold(self):
        result = run(make_article(claims=[self.LEGAL]))
        self.assertEqual((codes(result), issue_of(result, "sources_section").kind), (["sources_section"], HOLD))

    def test_the_section_with_an_https_source_satisfies_the_gate(self):
        sources = [{"title": "TDLR", "url": "https://tdlr.texas.gov/renew"}]
        self.assertEqual(codes(run(make_article(claims=[self.LEGAL], sources=sources))), [])
        for bad in ([], [{"title": "x", "url": "http://tdlr.texas.gov/renew"}], [{"title": "x", "url": ""}], ["nope"]):
            with self.subTest(sources=bad):
                self.assertEqual(codes(run(make_article(claims=[self.LEGAL], sources=bad))), ["sources_section"])

    def test_other_numbers_and_articles_without_claims_need_no_sources_section(self):
        self.assertEqual(codes(run(make_article(claims=[self.OTHER]))), [])
        self.assertEqual(codes(run(make_article())), [])


class HeadingTests(unittest.TestCase):
    def test_h1_in_the_content_is_fixable(self):
        result = run(make_article(), content="<h1>Tiêu đề</h1><p>x</p>")
        self.assertEqual((codes(result), issue_of(result, "h1_in_content").kind), (["h1_in_content"], FIXABLE))
        self.assertEqual(codes(run(make_article(), content="<h2>Tiêu đề</h2><p>x</p>")), [])
        article = make_article()
        article["sections"][1]["paragraphs"][1] = "Đoạn có <H1 class=x> trong chữ. " + prose(40, 2)
        self.assertIn("h1_in_content", codes(run(article)))

    def test_title_case_is_caught_from_60_percent_of_capitalised_words(self):
        def with_title(text):
            return make_article(title=text)
        for text, ok in (("Nail Mắt Mèo Nhung Mùa Đông", False), ("Màu nail Merlot mùa Đông", False),   # 3 of 5 = 60%
                         ("Màu nail Merlot mùa đông", True), ("Tóm Tắt", True), ("Nail mắt mèo nhung mùa đông 2026", True)):
            with self.subTest(text=text):
                self.assertEqual("title_case" not in codes(run(with_title(text))), ok)
        self.assertEqual(issue_of(run(with_title("Nail Mắt Mèo Nhung")), "title_case").kind, FIXABLE)

    def test_h2_h3_and_faq_questions_are_checked_too(self):
        article = make_article()
        article["sections"][2]["h2"] = "Cách Chọn Màu Nail Merlot"
        self.assertEqual(issue_of(run(article), "title_case").field, "sections[2].h2")
        article = make_article()
        article["sections"][2]["h3"] = [{"title": "Một Ý Nhỏ Cần Nhớ", "paragraphs": [prose(50, 1)]}]
        self.assertEqual(issue_of(run(article), "title_case").field, "sections[2].h3[0].title")
        article = make_article()
        article["faq"][0]["q"] = "Bao Giờ Nên Đổi Màu Sơn Nhỉ?"
        self.assertEqual(issue_of(run(article), "title_case").field, "faq[0].q")

    def test_capital_ratio_counts_only_words_that_start_with_a_letter(self):
        self.assertEqual(capital_ratio("Nail 2026 Mắt mèo"), (2 / 3, 3))
        self.assertEqual(capital_ratio("2026 100"), (0.0, 0))


class FigureAdjacentTests(unittest.TestCase):
    def test_two_consecutive_sections_with_a_slot_are_flagged(self):
        for slots, ok in (((0, 2, 4), True), ((0, 1, 3), False), ((1, 2, 4), False), ((0, 2, 3), False)):
            with self.subTest(slots=slots):
                result = run(make_article(slots=slots))
                self.assertEqual("figure_adjacent" not in codes(result), ok)
        issue = issue_of(run(make_article(slots=(0, 1, 3))), "figure_adjacent")
        self.assertEqual(issue.kind, FIXABLE)


if __name__ == "__main__":
    unittest.main()
