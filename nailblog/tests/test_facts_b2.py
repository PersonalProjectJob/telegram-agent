"""Step B2 facts hardening: both ends of a range (F2), VND, units and alt texts (F7)."""
import unittest

from nailblog.facts import check, find_numbers, unit_pairs
from tests.test_facts import article


def source(text):
    return [{"id": "s1", "snippet": text}]


def claim(text, quote):
    return {"text": text, "source_id": "s1", "quote": quote}


def run(sentence, quote, snippet):
    return [(item["number"], item["reason"]) for item in check(article(sentence, [claim(sentence, quote)]), source(snippet))]


class RangeTests(unittest.TestCase):
    CASES = (
        # (article sentence, literal, source snippet, quote that grounds it, a source that has only the second end)
        ("Khóa học kéo dài 20 đến 30 giờ.", "20 đến 30 giờ", "The course takes 20 to 30 hours in total for beginners",
         "The course takes 20 to 30 hours in total", "The course takes 30 hours in total for beginners"),
        ("Giấy phép về sau 3-5 ngày.", "3-5 ngày", "Expect 3-5 days for the license to arrive by mail",
         "Expect 3-5 days for the license", "Expect 5 days for the license to arrive by mail"),
        ("Tiền thuê ghế 50 - 80 đô mỗi ngày.", "50 - 80 đô", "Booth rent runs 50 - 80 dollars per day downtown",
         "Booth rent runs 50 - 80 dollars per day", "Booth rent runs 80 dollars per day downtown"),
        ("Tiền thuê ghế $50–$80 mỗi ngày.", "$50–$80", "Rent is $50–$80 per day in town for most salons",
         "Rent is $50–$80 per day in town", "Rent is $80 per day in town for most salons"),
    )

    def test_a_range_is_one_assertion_with_both_ends(self):
        for sentence, literal, *_ in self.CASES:
            with self.subTest(literal=literal):
                found = find_numbers(sentence)
                self.assertEqual(len(found), 1)
                self.assertEqual(found[0][1], literal)

    def test_a_range_is_grounded_when_the_quote_holds_both_ends(self):
        for sentence, _literal, snippet, good, _bad in self.CASES:
            with self.subTest(sentence=sentence):
                self.assertEqual(run(sentence, good, snippet), [])

    def test_an_invented_first_end_is_caught(self):
        for sentence, literal, _snippet, _good, bad in self.CASES:
            with self.subTest(sentence=sentence):
                self.assertEqual(run(sentence, bad, bad), [(literal, "quote_missing_number")])

    def test_without_any_claim_the_whole_range_is_one_ungrounded_number(self):
        found = check(article("Khóa học kéo dài 20 đến 30 giờ."), source("x"))
        self.assertEqual([(item["number"], item["reason"]) for item in found], [("20 đến 30 giờ", "no_claim")])

    def test_each_end_needs_its_own_unit_in_the_quote(self):
        sentence = "Khóa học kéo dài 20 đến 30 giờ."
        snippet = "The class lasts 20 minutes and then 30 hours of practice follow"
        quote = "The class lasts 20 minutes and then 30 hours of practice"
        self.assertEqual(run(sentence, quote, snippet), [("20 đến 30 giờ", "quote_wrong_unit")])

    def test_separate_ends_in_the_quote_also_ground_a_range(self):
        sentence = "Khóa học kéo dài 20 đến 30 giờ."
        snippet = "Beginners study 20 hours first and advanced students study 30 hours more"
        self.assertEqual(run(sentence, snippet, snippet), [])


class UnitTests(unittest.TestCase):
    def test_hours_are_not_minutes(self):
        snippet = "The video takes 2 minutes to watch and nothing more"
        self.assertEqual(run("Học trong 2 giờ.", "The video takes 2 minutes to watch", snippet),
                         [("2 giờ", "quote_wrong_unit")])
        self.assertEqual(run("Học trong 2 phút.", "The video takes 2 minutes to watch", snippet), [])
        snippet = "The video takes 2 hours to watch and nothing more"
        self.assertEqual(run("Học trong 2 phút.", "The video takes 2 hours to watch", snippet),
                         [("2 phút", "quote_wrong_unit")])
        self.assertEqual(run("Học trong 2 tiếng.", "The video takes 2 hours to watch", snippet), [])

    def test_days_and_weeks_are_different_units(self):
        snippet = "Delivery usually takes 3 weeks in this state"
        self.assertEqual(run("Mất 3 ngày.", "Delivery usually takes 3 weeks", snippet), [("3 ngày", "quote_wrong_unit")])
        self.assertEqual(run("Mất 3 tuần.", "Delivery usually takes 3 weeks", snippet), [])

    def test_usd_is_not_vnd(self):
        snippet = "A basic manicure costs 50 VND per visit at the stall"
        self.assertEqual(run("Giá $50 một lần.", "A basic manicure costs 50 VND per visit", snippet),
                         [("$50", "quote_wrong_unit")])
        snippet = "A basic manicure costs 50 USD per visit at the salon"
        self.assertEqual(run("Giá 50 triệu một lần.", "A basic manicure costs 50 USD per visit", snippet),
                         [("50 triệu", "quote_wrong_unit")])
        self.assertEqual(run("Giá $50 một lần.", "A basic manicure costs 50 USD per visit", snippet), [])


class VndTests(unittest.TestCase):
    def test_vnd_amounts_are_recognised(self):
        cases = {"giá 2 triệu đồng": "2 triệu đồng", "tiền công 50k": "50k", "giá 50.000đ": "50.000đ",
                 "trả 1.500.000 VND": "1.500.000 VND", "khoảng 300 nghìn": "300 nghìn", "lương 15 triệu": "15 triệu"}
        for text, literal in cases.items():
            with self.subTest(text=text):
                self.assertEqual(find_numbers(text), [("money", literal)])

    def test_words_that_only_start_like_a_unit_are_not_money(self):
        for text in ("đến nơi lúc đó", "5 đến 6 người", "50 khách hàng", "chữ k nhỏ"):
            with self.subTest(text=text):
                self.assertEqual([kind for kind, _ in find_numbers(text) if kind == "money"], [])

    def test_vnd_amounts_need_a_vnd_quote(self):
        snippet = "A basic manicure costs 50.000đ at most local shops"
        quote = "A basic manicure costs 50.000đ at most"
        self.assertEqual(run("Một bộ giá 50.000đ.", quote, snippet), [])
        self.assertEqual(run("Một bộ giá $50.", quote, snippet), [("$50", "quote_missing_number")])  # 50 vs 50000
        snippet = "A basic manicure costs 2 triệu đồng at most local shops"
        quote = "A basic manicure costs 2 triệu đồng at most"
        self.assertEqual(run("Một bộ giá 2 triệu đồng.", quote, snippet), [])

    def test_unit_pairs_read_both_ends_and_both_currencies(self):
        self.assertEqual(unit_pairs("duration", "20 đến 30 giờ and 5 min"), {("20", "hour"), ("30", "hour"), ("5", "minute")})
        self.assertEqual(unit_pairs("money", "$50–$80"), {("50", "usd"), ("80", "usd")})
        self.assertEqual(unit_pairs("money", "50 - 80 đô"), {("50", "usd"), ("80", "usd")})
        self.assertEqual(unit_pairs("money", "2 triệu đồng, 50.000đ"), {("2", "vnd"), ("50000", "vnd")})


class AltTextTests(unittest.TestCase):
    def test_alt_texts_are_scanned_and_named_by_field(self):
        data = article("Chỉ có chữ.", cover_alt="Ảnh bìa tiệm giá 50 đô la")
        data["sections"][0]["image_alt"] = "Ảnh minh họa học 40 giờ"
        found = {(item["field"], item["number"]) for item in check(data, source("x"))}
        self.assertEqual(found, {("cover_alt", "50 đô la"), ("sections[0].image_alt", "40 giờ")})

    def test_a_grounded_alt_number_passes(self):
        data = article(cover_alt="Ảnh bìa bộ gel giá $50", claims=[claim("Ảnh bìa bộ gel giá $50", "a gel manicure costs $50 on average")])
        self.assertEqual(check(data, source("Salons report that a gel manicure costs $50 on average today")), [])

    def test_alt_text_without_numbers_is_fine_and_year_in_a_title_behaves_as_before(self):
        self.assertEqual(check(article("Chỉ có chữ.", cover_alt="Ảnh bìa bộ móng merlot"), source("x")), [])
        found = check(article("Chỉ có chữ.", title="Xu hướng nail 2026"), source("x"))
        self.assertEqual([(item["number"], item["field"]) for item in found], [("2026", "title")])


if __name__ == "__main__":
    unittest.main()
