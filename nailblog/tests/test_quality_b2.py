"""Step B2: title similarity in the gate and the single repair round in Service.draft (F8)."""
import json
import tempfile
import unittest
from datetime import date

from nailblog.quality import FIXABLE, gate
from nailblog.service import Service
from nailblog.text import TextModel
from tests import fakes
from tests.fakes import FakeTransport, make_article, ollama_envelope
from tests.helpers import settings

TODAY = date(2026, 10, 16)
SLOT = "2026-10-20T19:00+07:00"
TITLE = "Màu nail merlot mùa đông: hướng dẫn chọn và giữ bền cho thợ nail"
LONG_SEO = "Màu nail merlot mùa đông: cách chọn và giữ bền cho thợ nail nhé nha"


def post(title, published="2026-10-01", slug="khac"):
    return {"id": "1", "slug": slug, "title": title, "publishedAt": published, "categorySlug": "nail-salon"}


def run(article=None, **kwargs):
    defaults = dict(category="nail-salon", slot_at=SLOT, existing_slugs=[], others=[], ungrounded=[], today=TODAY)
    defaults.update(kwargs)
    return gate(make_article() if article is None else article, **defaults)


class TitleSimilarityTests(unittest.TestCase):
    def test_an_identical_title_of_a_recent_post_is_fixable(self):
        result = run(posts=[post(TITLE)])
        similar = [issue for issue in result.issues if issue.code == "title_similar"]
        self.assertTrue(similar)
        self.assertTrue(all(issue.kind == FIXABLE for issue in similar))
        self.assertIn("title", {issue.field for issue in similar})
        self.assertEqual(result.verdict, "fixable")

    def test_accents_and_case_are_ignored(self):
        shouted = "MAU NAIL MERLOT MUA DONG HUONG DAN CHON VA GIU BEN CHO THO NAIL"
        self.assertIn("title_similar", [issue.code for issue in run(posts=[post(shouted)]).issues])

    def test_jaccard_boundary_is_at_least_point_six(self):
        base = make_article(title="Màu nail merlot mùa đông", title_seo="Màu nail merlot mùa đông")
        same = run(base, posts=[post("Màu nail merlot")])      # 3 shared of 5 words = 0.6
        self.assertEqual({issue.code for issue in same.issues}, {"title_similar"})
        self.assertEqual({issue.field for issue in same.issues}, {"title", "title_seo"})
        self.assertTrue(run(base, posts=[post("Màu nail hè lạnh")]).ok)  # 2 of 7 = 0.29

    def test_posts_older_than_90_days_are_ignored_and_the_boundary_day_still_counts(self):
        self.assertTrue(run(posts=[post(TITLE, "2026-06-01")]).ok)
        self.assertIn("title_similar", [i.code for i in run(posts=[post(TITLE, "2026-07-18")]).issues])
        self.assertTrue(run(posts=[post(TITLE, "2026-07-17")]).ok)

    def test_without_a_published_list_nothing_is_compared_and_a_different_title_passes(self):
        self.assertTrue(run(posts=None).ok)
        self.assertTrue(run(posts=[post("Thuế tiệm nail 2026 cần biết gì")]).ok)
        self.assertTrue(run(posts=[post("")]).ok)

    def test_the_vietnam_day_is_used_when_today_is_not_given(self):
        self.assertIn("title_similar", [i.code for i in gate(
            make_article(), category="nail-salon", slot_at=SLOT, existing_slugs=[], posts=[post(TITLE, "2999-01-01")]).issues])


class DraftRepairTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = settings(self.directory.name)
        self.service = Service(self.config, clock=lambda: 0.0)
        self.topic = {"id": 1, "title": "Màu nail merlot", "category": "nail-salon", "slot_at": SLOT, "state": "proposed",
                      "sources_json": json.dumps({"sources": [fakes.SOURCE], "product_promo": False, "scores": {}})}

    async def draft(self, respond, posts=fakes.RELATED_POSTS, outline_override=None, section_override=None):
        calls = []

        def transport(call, number):
            calls.append(call)
            if number == 1:
                return ollama_envelope(fakes.outline(5, **(outline_override or {})))
            if number <= 6:
                return ollama_envelope(fakes.section(number - 2, **((section_override or {}).get(number - 2, {}))))
            return respond(call, number - 6)
        text = TextModel(self.config, _test_transport=FakeTransport(transport))
        draft = await self.service.draft(self.topic, text, posts=list(posts), today=TODAY)
        return draft, calls

    @staticmethod
    def reply(**fields):
        return lambda call, number: ollama_envelope(fields)

    async def test_a_fixable_only_article_gets_exactly_one_repair_for_the_named_field(self):
        long_seo = LONG_SEO
        self.assertGreater(len(long_seo), 63)
        draft, calls = await self.draft(self.reply(title_seo="Màu nail merlot mùa đông: cách chọn giữ bền"),
                                        outline_override={"title_seo": long_seo})
        self.assertEqual(len(calls), 7)
        request = json.dumps(calls[-1]["payload"], ensure_ascii=False)
        self.assertIn("title_seo hiện tại: " + long_seo, request)
        self.assertNotIn("summary hiện tại", request)
        self.assertNotIn("meta_title", request)
        self.assertEqual((draft.repaired, draft.result.verdict), (["title_seo"], "pass"))
        self.assertEqual(draft.article["title_seo"], "Màu nail merlot mùa đông: cách chọn giữ bền")
        self.assertEqual(draft.ungrounded, [])

    async def test_a_hold_issue_is_never_repaired_and_blocks_the_round(self):
        long_seo = LONG_SEO
        unsupported = {1: {"paragraphs": ["Một bộ gel thường có giá $50. " + fakes.prose(40, 0, fakes.KEYWORD), fakes.prose(40)]}}
        draft, calls = await self.draft(self.reply(title_seo="x" * 20), outline_override={"title_seo": long_seo},
                                        section_override=unsupported)
        self.assertEqual(len(calls), 6)                       # no seventh call
        self.assertEqual((draft.repaired, draft.result.verdict), ([], "hold"))
        self.assertEqual({issue.code for issue in draft.result.issues}, {"title_seo", "facts"})
        self.assertEqual([issue.kind for issue in draft.result.issues if issue.code == "facts"], ["hold"])

    async def test_the_gate_runs_again_and_a_similar_title_is_repaired(self):
        reply = self.reply(title_seo="Chọn màu nail merlot cho bộ móng mùa đông lạnh",
                           title="Chọn màu nail merlot cho bộ móng mùa đông: mẹo giữ bền")
        draft, calls = await self.draft(reply, posts=[post(TITLE)] + fakes.RELATED_POSTS)
        self.assertEqual(len(calls), 7)
        self.assertEqual((sorted(draft.repaired), draft.result.verdict), (["title", "title_seo"], "pass"))

    async def test_only_one_round_even_when_the_article_is_still_fixable(self):
        short = {index: {"paragraphs": [fakes.prose(10, index, fakes.KEYWORD)]} for index in range(5)}
        draft, calls = await self.draft(self.reply(body={"paragraphs": [fakes.prose(12)], "claims": []}),
                                        section_override=short)
        self.assertEqual(len(calls), 7)
        self.assertEqual(draft.repaired, ["body"])
        self.assertIn("words", {issue.code for issue in draft.result.issues})
        self.assertEqual(draft.result.verdict, "fixable")

    async def test_a_repair_the_model_cannot_deliver_leaves_the_article_as_it_was(self):
        long_seo = LONG_SEO
        too_long = self.reply(title_seo=long_seo)              # still over the 60 characters the schema allows
        draft, calls = await self.draft(too_long, outline_override={"title_seo": long_seo})
        self.assertEqual(len(calls), 7)                        # 6 writes + the one repair ask, which fails the schema: no re-ask
        self.assertEqual((draft.repaired, draft.result.verdict), ([], "fixable"))
        self.assertEqual(draft.article["title_seo"], long_seo)

    async def test_a_passing_article_costs_no_repair_call(self):
        draft, calls = await self.draft(self.reply(title_seo="unused"))
        self.assertEqual((len(calls), draft.repaired, draft.result.verdict), (6, [], "pass"))


if __name__ == "__main__":
    unittest.main()
