import unittest

from nailblog.facts import check, figures, find_numbers
from tests.fakes import SOURCE, claim

SENTENCE = "Một bộ gel thường có giá $50."
QUOTE = "a gel manicure costs $50 on average"


def article(paragraph=SENTENCE, claims=(), **extra):
    data = {"title": "Tiêu đề", "title_seo": "SEO", "summary": "Tóm tắt", "intro": "Mở bài.",
            "sections": [{"h2": "Mục một", "paragraphs": [paragraph], "bullets": []}], "faq": [], "cta": "Gọi ngay.",
            "claims": list(claims)}
    data.update(extra)
    return data


def reasons(found):
    return [(item["number"], item["reason"]) for item in found]


class FindNumbersTests(unittest.TestCase):
    def test_factual_numbers_are_found_by_kind(self):
        cases = [("giá $1,200 mỗi năm", [("money", "$1,200")]), ("phí $50.", [("money", "$50")]),
                 ("khoảng 5 đô la", [("money", "5 đô la")]), ("tăng 15% so với", [("percent", "15%")]),
                 ("khoảng 20 percent", [("percent", "20 percent")]), ("học 40 giờ", [("duration", "40 giờ")]),
                 ("gia hạn trong 30 ngày", [("duration", "30 ngày")]), ("ngày 26 tháng 11", [("date", "ngày 26 tháng 11")]),
                 ("vào 26/11", [("date", "26/11")]), ("hết tháng 11", [("date", "tháng 11")]),
                 ("ngày 26/11/2026", [("date", "26/11/2026")]), ("mùa đông 2026", [("year", "2026")]),
                 ("ngày 26 tháng 11 năm 2026", [("date", "ngày 26 tháng 11 năm 2026")]),
                 ("$50 và 15% và 2027", [("money", "$50"), ("percent", "15%"), ("year", "2027")])]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(find_numbers(text), expected)

    def test_plain_counts_and_codes_are_not_factual_claims(self):
        for text in ("10 mẫu nail đẹp", "top 5 màu", "3 câu hỏi thường gặp", "mẫu 1099-NEC", "bước 2", "NEXORA 100"):
            with self.subTest(text=text):
                self.assertEqual(find_numbers(text), [])

    def test_figures_normalise_separators(self):
        self.assertEqual(figures("$1,200"), {"1200"})
        self.assertEqual(figures("1.200 đô"), {"1200"})
        self.assertEqual(figures("3,5 giờ"), {"3.5"})
        self.assertEqual(figures("26/11/2026"), {"26", "11", "2026"})


class GroundedTests(unittest.TestCase):
    def test_number_with_a_verbatim_quote_that_holds_it_is_grounded(self):
        self.assertEqual(check(article(claims=[claim()]), [SOURCE]), [])

    def test_text_without_numbers_needs_no_claims(self):
        self.assertEqual(check(article("Chỉ có chữ, 10 mẫu, top 5, 3 câu."), [SOURCE]), [])

    def test_whitespace_differences_are_ignored_in_both_claim_and_quote(self):
        spaced = claim(text="Một bộ gel  thường có\ngiá $50.", quote="a gel  manicure\tcosts $50   on average")
        self.assertEqual(check(article(claims=[spaced]), [SOURCE]), [])

    def test_sources_may_be_given_as_a_mapping_too(self):
        self.assertEqual(check(article(claims=[claim()]), {"s1": SOURCE["snippet"]}), [])

    def test_thousands_separators_match_across_styles(self):
        source = {"s1": "A salon license costs $1,200 per year in total"}
        found = check(article("Giấy phép giá $1.200.", [claim("Giấy phép giá $1.200.", quote="license costs $1,200 per year")]),
                      source)
        self.assertEqual(found, [])

    def test_a_duration_figure_is_grounded_by_the_same_digits(self):
        text = "Bộ gel bền khoảng 3 tuần."
        self.assertEqual(check(article(text, [claim(text, quote="and lasts about 3 weeks")]), [SOURCE]), [])


class UngroundedTests(unittest.TestCase):
    def check_one(self, claims, paragraph=SENTENCE, sources=(SOURCE,)):
        return reasons(check(article(paragraph, claims), list(sources)))

    def test_number_without_any_claim(self):
        found = check(article(), [SOURCE])
        self.assertEqual(found, [{"number": "$50", "field": "sections[0].paragraphs[0]", "reason": "no_claim"}])

    def test_quote_that_is_not_in_the_source_is_rejected(self):
        self.assertEqual(self.check_one([claim(quote="a gel manicure costs $50 on average today")]),
                         [("$50", "quote_not_in_source")])

    def test_quote_that_lacks_the_number_is_rejected(self):
        self.assertEqual(self.check_one([claim(quote="Merlot is the top shade this winter")]),
                         [("$50", "quote_missing_number")])

    def test_a_quote_glued_together_from_separate_sentences_is_rejected(self):
        glued = ["Merlot is the top shade this winter. a gel manicure costs $50 on average",   # skips a sentence
                 "Merlot is the top shade this winter ... a gel manicure costs $50 on average",
                 "Merlot is the top shade this winter… a gel manicure costs $50 on average"]
        expected = ["quote_not_in_source", "quote_joined_or_too_short", "quote_joined_or_too_short"]
        for quote, reason in zip(glued, expected):
            with self.subTest(quote=quote):
                self.assertEqual(self.check_one([claim(quote=quote)]), [("$50", reason)])

    def test_a_bare_number_is_not_a_quote(self):
        self.assertEqual(self.check_one([claim(quote="costs $50")]), [("$50", "quote_joined_or_too_short")])

    def test_contiguous_multi_sentence_quote_from_the_source_is_allowed(self):
        quote = "Merlot is the top shade this winter. Salons report that a gel manicure costs $50 on average"
        self.assertEqual(self.check_one([claim(quote=quote)]), [])

    def test_unknown_source_and_claim_not_in_the_article(self):
        self.assertEqual(self.check_one([claim(source_id="s9")]), [("$50", "unknown_source")])
        self.assertEqual(self.check_one([claim(text="Một câu khác hẳn có $50.")], paragraph="Giá $50 ở đây."),
                         [("$50", "claim_not_in_article")])

    def test_quote_must_hold_the_asserted_figure_not_another_one(self):
        text = "Một bộ gel thường có giá $500."
        self.assertEqual(self.check_one([claim(text=text)], paragraph=text), [("$500", "quote_missing_number")])

    def test_money_and_percent_need_their_marker_in_the_quote(self):
        source = [{"id": "s1", "snippet": "A total of 50 salons reported higher demand, and 15 salons closed."}]
        money = claim("Giá $50.", quote="A total of 50 salons reported higher demand")
        self.assertEqual(self.check_one([money], "Giá $50.", source), [("$50", "quote_missing_currency")])
        percent = claim("Tăng 15%.", quote="and 15 salons closed")
        self.assertEqual(self.check_one([percent], "Tăng 15%.", source), [("15%", "quote_missing_percent")])

    def test_a_date_needs_every_part_in_the_quote(self):
        source = [{"id": "s1", "snippet": "Thanksgiving is on November 26 this year for everyone."}]
        text = "Lễ vào ngày 26 tháng 11."
        found = self.check_one([claim(text, quote="Thanksgiving is on November 26 this year")], text, source)
        self.assertEqual(found, [("ngày 26 tháng 11", "quote_missing_number")])
        numeric = [{"id": "s1", "snippet": "Thanksgiving is on 11/26/2026 for everyone."}]
        self.assertEqual(self.check_one([claim("Lễ vào 26/11/2026.", quote="Thanksgiving is on 11/26/2026")],
                                        "Lễ vào 26/11/2026.", numeric), [])

    def test_every_number_in_a_sentence_needs_its_own_support(self):
        text = "Giá $50 và giảm 20%."
        first = claim(text, quote=QUOTE)
        self.assertEqual(self.check_one([first], text), [("20%", "quote_missing_number")])
        source = [{"id": "s1", "snippet": SOURCE["snippet"] + " Many shops offer 20% off for students."}]
        second = claim(text, quote="Many shops offer 20% off for students")
        self.assertEqual(self.check_one([first, second], text, source), [])

    def test_every_text_field_is_scanned(self):
        data = article(title="Giá $10", title_seo="Năm 2026", summary="Giảm 5%", intro="Học 40 giờ",
                       cta="Hạn ngày 5 tháng 3")
        data["sections"][0].update(h2="Mốc 2027", bullets=["Phí $7"])
        data["faq"] = [{"q": "Bao lâu 30 ngày?", "a": "Khoảng $9"}]
        fields = {item["field"] for item in check(data, [SOURCE])}
        self.assertEqual(fields, {"title", "title_seo", "summary", "intro", "cta", "sections[0].h2",
                                  "sections[0].bullets[0]", "sections[0].paragraphs[0]", "faq[0].q", "faq[0].a"})

    def test_malformed_claims_never_crash_and_never_ground_anything(self):
        for claims in (["text", None, 5], [{"text": 50, "source_id": "s1", "quote": QUOTE}],
                       [{"text": SENTENCE}], "not a list", None):
            with self.subTest(claims=claims):
                found = check(article(claims=claims) if claims is not None else {**article(), "claims": None}, [SOURCE])
                self.assertEqual(len(found), 1)


if __name__ == "__main__":
    unittest.main()
