"""write_agy / repair_agy (S3): the whole article in one `ask`, at most one repair `ask`, no silent failure.

`ask` is a fake with the real contract `ask(prompt, schema, *, purpose) -> dict`; it returns real JSON that is
checked against the schema the writer sent, and every call is counted."""
import tempfile
import unittest
from datetime import date

from nailblog.quality import Issue
from nailblog.text import check_schema
from nailblog.writer import (AGY_MAX_ASKS, AGY_REPAIR_PURPOSE, AGY_WRITE_PURPOSE, agy_schema, render_html, repair_agy,
                             write_agy)
from tests import fakes
from tests.helpers import load

TOPIC = {"title": "Màu nail merlot", "category": "nail-salon"}
SHORT_SUMMARY = "Hướng dẫn chọn màu nail merlot cho mùa đông, mẹo tư vấn khách cho thợ và chủ tiệm nail ở Mỹ"   # < 140
GOOD_SUMMARY = fakes.SUMMARY


class AgyError(RuntimeError):
    """Like the errors AgyText.ask raises: a short `.code`."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def reply(**override):
    """The one-call reply: outline fields + sections with their paragraphs, claims at the top level."""
    data = fakes.outline(5)
    sections = []
    for index, planned in enumerate(data["sections"]):
        piece = fakes.section(index + 1)
        sections.append({"h2": planned["h2"], "kind": planned["kind"], "paragraphs": piece["paragraphs"],
                         **({"image_slot": planned["image_slot"]} if "image_slot" in planned else {})})
    data["sections"] = sections
    data["meta_title"] = fakes.TITLE_SEO
    data.update(override)
    return data


class FakeAsk:
    """Scripted `ask`: `script` is a list of replies (dicts or exceptions); records prompt, schema and purpose."""

    def __init__(self, *script):
        self.script, self.calls = list(script), []

    def __call__(self, prompt, schema, *, purpose):
        self.calls.append({"prompt": prompt, "schema": schema, "purpose": purpose})
        item = self.script[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(schema)
        problems = check_schema(schema, item)
        assert not problems, problems   # the fake must answer with JSON that fits the schema it was given
        return item


def repair_reply(schema, **fields):
    """A repair reply: every property of the schema asked for must be present."""
    assert set(schema["properties"]) == set(fields), (sorted(schema["properties"]), sorted(fields))
    return fields


class AgyBase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = load(self.directory.name)
        self.context = {"sources": [fakes.SOURCE], "today": date(2026, 10, 27), "posts": [],
                        "related_posts": fakes.RELATED_POSTS}

    def write(self, ask, topic=TOPIC, **context):
        return write_agy(ask, topic, {**self.context, **context}, self.config)


class SchemaTests(unittest.TestCase):
    def test_the_schema_carries_outline_sections_and_meta_title_and_accepts_the_reply(self):
        schema = agy_schema()
        self.assertEqual(check_schema(schema, reply()), [])
        sections = schema["properties"]["sections"]
        self.assertEqual((sections["minItems"], sections["maxItems"]), (4, 5))
        self.assertIn("meta_title", schema["required"])
        self.assertNotIn("claims", sections["items"]["properties"])   # claims are top level in the one-call shape


class OneCallTests(AgyBase):
    def test_a_clean_article_takes_exactly_one_ask(self):
        ask = FakeAsk(reply())
        draft = self.write(ask)
        self.assertEqual((len(ask.calls), draft.asks), (1, 1))
        self.assertEqual(ask.calls[0]["purpose"], AGY_WRITE_PURPOSE)
        self.assertFalse(draft.held)
        self.assertEqual(draft.issues, [])
        self.assertTrue(draft.can_override)
        self.assertEqual(draft.result.verdict, "pass")
        self.assertEqual(draft.words, 898)
        self.assertIn("agy_single_call", draft.article["writer_audit"])

    def test_the_prompt_carries_the_topic_the_sources_and_the_word_budget_but_no_model_choice_of_html(self):
        ask = FakeAsk(reply())
        self.write(ask)
        prompt = ask.calls[0]["prompt"]
        self.assertIn("Chủ đề: Màu nail merlot", prompt)
        self.assertIn(fakes.SOURCE_TEXT, prompt)
        self.assertIn("từ 850 đến 1120 từ", prompt)          # nail-salon 800..1200: 800+50 .. 1200-80
        self.assertIn("MỘT lượt", prompt)

    def test_html_is_built_and_escaped_by_the_writer(self):
        data = reply()
        data["sections"][1]["h2"] = "Mục <b>hai</b> & cho tiệm nail"
        data["sections"][1]["paragraphs"][0] = "<script>alert(1)</script> " + data["sections"][1]["paragraphs"][0]
        draft = self.write(FakeAsk(data))
        self.assertNotIn("<script>", draft.content)
        self.assertNotIn("<b>", draft.content)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", draft.content)
        self.assertEqual(draft.content, render_html(draft.article, draft.images, site_origin="https://nailhub.ai"))
        self.assertEqual(len(draft.images), 3)
        self.assertEqual(draft.article["jsonld"][0]["@type"], "BlogPosting")

    def test_a_reply_that_does_not_fit_the_schema_is_an_error_not_an_article(self):
        bad = reply()
        del bad["faq"]
        calls = []

        def ask(prompt, schema, *, purpose):
            calls.append(purpose)
            return bad
        with self.assertRaises(RuntimeError) as caught:
            write_agy(ask, TOPIC, self.context, self.config)
        self.assertEqual(caught.exception.code, "agy_schema_mismatch")
        self.assertEqual(calls, [AGY_WRITE_PURPOSE])


class RepairTests(AgyBase):
    def test_a_fixable_issue_takes_one_repair_ask_and_the_second_gate_passes(self):
        ask = FakeAsk(reply(summary=SHORT_SUMMARY), lambda schema: repair_reply(schema, summary=GOOD_SUMMARY))
        draft = write_agy(ask, TOPIC, self.context, self.config)
        self.assertEqual((len(ask.calls), draft.asks), (2, 2))
        self.assertEqual([call["purpose"] for call in ask.calls], [AGY_WRITE_PURPOSE, AGY_REPAIR_PURPOSE])
        self.assertIn("summary hiện tại: " + SHORT_SUMMARY, ask.calls[1]["prompt"])
        self.assertEqual(draft.article["summary"], GOOD_SUMMARY)
        self.assertIn("summary", draft.repaired)
        self.assertFalse(draft.held)
        self.assertEqual(draft.result.verdict, "pass")

    def test_still_failing_after_the_repair_returns_held_with_fixable_overridable_issues_and_no_third_ask(self):
        data = reply()
        data["sections"][0]["paragraphs"][1] = "Màu này vô cùng hợp mùa đông. " + data["sections"][0]["paragraphs"][1]
        # the repair reply fits the schema but is a useless rewrite: the writer rejects it and keeps the cliche issue
        ask = FakeAsk(data, lambda schema: {name: "chưa sửa gì" for name in schema["properties"]},
                      AssertionError("a third ask must never happen"))
        draft = write_agy(ask, TOPIC, self.context, self.config)
        self.assertEqual(len(ask.calls), 2)
        self.assertEqual(draft.asks, 2)
        self.assertLessEqual(draft.asks, AGY_MAX_ASKS)
        self.assertTrue(draft.held)
        self.assertEqual([issue["code"] for issue in draft.issues], ["cliche"])
        self.assertEqual(draft.issues[0]["kind"], "fixable")
        self.assertTrue(draft.issues[0]["overridable"])
        self.assertTrue(draft.can_override)
        self.assertEqual(draft.article["writer_repair_outcome"]["state"], "held")

    def broken(self):
        article = self.write(FakeAsk(reply())).article
        return {**article, "summary": SHORT_SUMMARY}, Issue("summary", "fixable", "summary", "summary 96 ký tự, cần 140-160")

    def test_repair_agy_is_one_ask_with_the_repair_purpose(self):
        article, issue = self.broken()
        ask = FakeAsk(lambda schema: repair_reply(schema, summary=GOOD_SUMMARY))
        fixed, repaired = repair_agy(ask, article, [issue], sources=[fakes.SOURCE])
        self.assertEqual([call["purpose"] for call in ask.calls], [AGY_REPAIR_PURPOSE])
        self.assertEqual((fixed["summary"], repaired), (GOOD_SUMMARY, ["summary"]))

    def test_the_budget_is_two_asks_and_the_whole_route_never_exceeds_it(self):
        self.assertEqual(AGY_MAX_ASKS, 2)
        data = reply()
        data["sections"][0]["paragraphs"][1] = "Màu này vô cùng hợp mùa đông. " + data["sections"][0]["paragraphs"][1]
        calls = []

        def always_bad(prompt, schema, *, purpose):
            calls.append(purpose)
            if len(calls) == 1:
                return data
            return {name: "chưa sửa gì" for name in schema["properties"]}
        draft = write_agy(always_bad, TOPIC, self.context, self.config)
        self.assertEqual(len(calls), 2)
        self.assertEqual(draft.asks, 2)
        self.assertTrue(draft.held)

    def test_repair_agy_never_makes_a_call_beyond_the_budget(self):
        article, issue = self.broken()
        ask = FakeAsk(AssertionError("must not be called"))
        with self.assertRaises(RuntimeError) as caught:
            repair_agy(ask, article, [issue], sources=[fakes.SOURCE], counter=[2])
        self.assertEqual(caught.exception.code, "agy_ask_budget_exhausted")
        self.assertEqual(ask.calls, [])


class HoldTests(AgyBase):
    def test_a_number_without_a_claim_is_a_facts_hold_no_repair_and_not_overridable(self):
        data = reply()
        data["sections"][0]["paragraphs"][1] = "Một bộ gel thường có giá $75. " + data["sections"][0]["paragraphs"][1]
        ask = FakeAsk(data)
        draft = self.write(ask)
        self.assertEqual(draft.asks, 1)                       # a hold is never sent to a repair call
        self.assertTrue(draft.held)
        self.assertIn("facts", {issue["code"] for issue in draft.issues})
        self.assertTrue(all(not issue["overridable"] for issue in draft.issues if issue["code"] == "facts"))
        self.assertFalse(draft.can_override)
        self.assertTrue(draft.ungrounded)

    def test_han_is_a_hold_that_cannot_be_overridden(self):
        data = reply()
        data["sections"][2]["paragraphs"][0] = data["sections"][2]["paragraphs"][0] + " 美甲"
        ask = FakeAsk(data)
        draft = self.write(ask)
        self.assertEqual(draft.asks, 1)
        self.assertTrue(draft.held)
        han = [issue for issue in draft.issues if issue["code"] == "unexpected_han"]
        self.assertTrue(han)
        self.assertFalse(any(issue["overridable"] for issue in han))
        self.assertFalse(draft.can_override)

    def test_a_medical_claim_is_a_hold_that_cannot_be_overridden(self):
        data = reply()
        data["sections"][3]["paragraphs"][0] = "Gel giúp điều trị nấm móng. " + data["sections"][3]["paragraphs"][0]
        draft = self.write(FakeAsk(data))
        medical = [issue for issue in draft.issues if issue["code"] == "medical_claim"]
        self.assertTrue(medical)
        self.assertFalse(any(issue["overridable"] for issue in medical))
        self.assertFalse(draft.can_override)
        self.assertEqual(draft.asks, 1)

    def test_a_structural_hold_is_not_overridable_either(self):
        draft = self.write(FakeAsk(reply()), posts=None)      # published list unavailable -> slug_unverified hold
        self.assertTrue(draft.held)
        self.assertEqual([(issue["code"], issue["overridable"]) for issue in draft.issues],
                         [("slug_unverified", False)])
        self.assertFalse(draft.can_override)


class MedicalTermTests(AgyBase):
    def codes(self, sentence):
        data = reply()
        data["sections"][3]["paragraphs"][0] = sentence + " " + data["sections"][3]["paragraphs"][0]
        return {issue["code"] for issue in self.write(FakeAsk(data)).issues}

    def test_innocent_words_containing_chua_are_not_held(self):
        for sentence in ("Thợ sửa chữa móng gãy cho khách.", "Tiệm có bình chữa cháy gần cửa."):
            self.assertNotIn("medical_claim", self.codes(sentence), sentence)

    def test_medical_phrases_are_held_even_with_odd_spacing_hyphens_and_case(self):
        for sentence in ("Gel này chữa khỏi nấm cho khách.", "Bài nói về nấm  móng ở tiệm.", "Bài nói về nấm-móng ở tiệm.",
                         "Bài nói về nấm móng ở tiệm.", "Gel giúp điều trị tại tiệm.", "Gel giúp ĐIỀU TRỊ tại tiệm."):
            self.assertIn("medical_claim", self.codes(sentence), sentence)


class NeverOverrideTests(AgyBase):
    def test_a_fixable_issue_with_a_never_override_code_is_still_not_overridable(self):
        from unittest import mock
        from nailblog import writer
        from nailblog.quality import QualityResult
        real = writer._agy_assess

        def assess(*args):
            images, content, ungrounded, result = real(*args)
            return images, content, ungrounded, QualityResult(result.issues + (Issue("facts", "fixable", "content", "x"),))
        with mock.patch.object(writer, "_agy_assess", assess):
            draft = self.write(FakeAsk(reply()))
        self.assertEqual([(i["code"], i["kind"], i["overridable"]) for i in draft.issues], [("facts", "fixable", False)])
        self.assertFalse(draft.can_override)


class ErrorTests(AgyBase):
    def test_an_error_of_the_write_ask_propagates_with_its_code(self):
        ask = FakeAsk(AgyError("agy_timeout"))
        with self.assertRaises(AgyError) as caught:
            self.write(ask)
        self.assertEqual(caught.exception.code, "agy_timeout")
        self.assertEqual(len(ask.calls), 1)

    def test_an_error_of_the_repair_ask_propagates_with_its_code(self):
        ask = FakeAsk(reply(summary=SHORT_SUMMARY), AgyError("agy_unknown"))
        with self.assertRaises(AgyError) as caught:
            write_agy(ask, TOPIC, self.context, self.config)
        self.assertEqual(caught.exception.code, "agy_unknown")
        self.assertEqual([call["purpose"] for call in ask.calls], [AGY_WRITE_PURPOSE, AGY_REPAIR_PURPOSE])


if __name__ == "__main__":
    unittest.main()
