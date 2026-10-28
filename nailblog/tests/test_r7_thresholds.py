"""R7 (D-034 bổ sung 28/10): the quality limits are widened for every post (Owner decision, +5% on continuous limits).

The four real articles of 28/10 (held with kw_density / para_len / sentence_len under the old limits) must now pass."""
import copy
import json
import unittest
from pathlib import Path

from nailblog import quality
from nailblog.config import STAGING_SITE_ORIGIN
from nailblog.quality import gate
from tests import fakes
from tests.test_quality_standard import spread_keyword
from tests.test_writer_agy import reply

FIXTURE = Path(__file__).parent / "fixtures" / "r7_held_20261028.json"
RUNS = json.loads(FIXTURE.read_text(encoding="utf-8"))


def run_gate(article):
    return gate(article, category=article.get("category"), existing_slugs=[], site_origin=STAGING_SITE_ORIGIN,
                ungrounded=[])


def sentence_stats(article):
    paragraphs = quality._paragraphs(article)
    prose = quality._intro(article) + [text for _p, text in paragraphs] + [
        item.get("a", "") for item in article.get("faq", []) if isinstance(item, dict)] + [quality.cta_text(article)]
    lengths = [quality.words_of(s) for text in prose for s in quality.sentences_of(text)]
    return sum(lengths) / len(lengths), max(lengths)


class RealArticlesTests(unittest.TestCase):
    def test_the_four_held_articles_pass_the_new_standard_with_no_issue(self):
        self.assertEqual(len(RUNS), 4)
        for run_id, run in RUNS.items():
            result = run_gate(run["article_file"]["article"])
            self.assertEqual((run_id, result.verdict, [i.code for i in result.issues]), (run_id, "pass", []))

    def test_the_old_limits_did_flag_them(self):
        old = {run_id: sorted(i["code"] for i in run["quality"]["issues"]) for run_id, run in RUNS.items()}
        self.assertEqual(old["auto-20261028-1900-t7"], ["kw_density", "para_len", "sentence_len"])
        self.assertEqual(old["auto-20261028-1000-t3"], ["kw_density"])

    def test_the_1900_run_is_the_36_word_sentence_case(self):
        article = RUNS["auto-20261028-1900-t7"]["article_file"]["article"]
        average, longest = sentence_stats(article)
        self.assertEqual(longest, 36)
        self.assertLessEqual(average, quality.SENTENCE_AVERAGE)


class SelfApprovingDmTests(unittest.TestCase):
    def test_the_decide_dm_does_not_say_the_post_still_needs_approval_when_the_machine_approves(self):
        from types import SimpleNamespace
        from nailblog.daily import _origin_text
        meta = {"chosen": {"origin": "auto"}}
        auto = SimpleNamespace(autoflow=SimpleNamespace(owner_approval=False))
        manual = SimpleNamespace(autoflow=SimpleNamespace(owner_approval=True))
        self.assertNotIn("phải duyệt", _origin_text(meta, auto))
        self.assertIn("vẫn phải duyệt bài", _origin_text(meta, manual))
        self.assertIn("vẫn phải duyệt bài", _origin_text(meta))


class ThresholdValueTests(unittest.TestCase):
    def test_owner_values(self):
        self.assertEqual((quality.DENSITY_LOW, quality.DENSITY_HIGH), (0.3, 2.625))
        self.assertEqual((quality.PARAGRAPH_WORDS, quality.PARAGRAPH_LONG_MAX), ((35, 95), 115))
        self.assertEqual((quality.SENTENCE_AVERAGE, quality.SENTENCE_MAX), (22, 36))
        self.assertEqual((quality.PARAGRAPH_SENTENCES, quality.MAX_PARAGRAPH_RUN), ((2, 4), 3))


class BoundaryTests(unittest.TestCase):
    """The limits bite exactly at the new edge, so a loosened or tightened constant is caught."""

    def article(self):
        art = copy.deepcopy(reply())
        art["category"] = "nail-salon"
        return art

    def codes(self, art):
        return {i.code for i in run_gate(art).issues} - {"slug", "internal_links"}

    def test_keyword_density_edges_on_a_900_word_article(self):
        for hits, flagged in ((2, True), (3, False), (23, False), (24, True)):   # 0.22% / 0.33% / 2.56% / 2.67%
            art = spread_keyword(self.article(), hits)
            self.assertEqual("kw_density" in self.codes(art), flagged, hits)

    def test_longest_sentence_edge_36_ok_37_not(self):
        art = self.article()
        for size, expected in ((36, False), (37, True)):
            one = " ".join(["từ"] * size) + "."
            art["sections"][1]["paragraphs"][0] = one + " " + fakes.prose(20, 5, None, sentences=1)
            self.assertEqual("sentence_len" in self.codes(art), expected, size)

    def test_paragraph_word_edges(self):
        art = self.article()
        for words, expected in ((35, False), (34, True)):
            art["sections"][1]["paragraphs"][0] = fakes.prose(words, 5, None, sentences=2)
            self.assertEqual("para_len" in self.codes(art), expected, words)
        art["sections"][1]["paragraphs"][0] = fakes.prose(95, 5, None, sentences=4)
        art["sections"][2]["paragraphs"][0] = fakes.prose(96, 6, None, sentences=4)
        self.assertNotIn("para_len", self.codes(art))                  # one long paragraph (96) is allowed
        art["sections"][3]["paragraphs"][0] = fakes.prose(96, 7, None, sentences=4)
        self.assertIn("para_len", self.codes(art))                     # a second one is not
        art["sections"][3]["paragraphs"][0] = fakes.prose(55, 7, None)
        art["sections"][2]["paragraphs"][0] = fakes.prose(115, 6, None, sentences=4)
        self.assertNotIn("para_len", self.codes(art))
        art["sections"][2]["paragraphs"][0] = fakes.prose(116, 6, None, sentences=4)
        self.assertIn("para_len", self.codes(art))


if __name__ == "__main__":
    unittest.main()
