"""Step B2 hardening: published evidence (F4), image source URLs (F5)."""
import tempfile
import unittest
from pathlib import Path

from nailblog.state import EvidenceRequired, State
from nailblog.writer import _safe_src, render_html

SLOT = "2026-10-17T06:00+07:00"


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "s.sqlite3")

    def publish(self, old, evidence):
        post = self.state.add_post(SLOT, state=old)
        self.state.transition(post, "published", reason="r", evidence=evidence)
        return post

    def test_false_zero_numbers_and_dicts_without_a_real_readback_are_not_evidence(self):
        bad = (None, "", "   ", False, True, 0, 1, {}, [], ["readback"], {"status": False}, {"status": "Published"},
               {"readback": False}, {"readback": 0}, {"readback": ""}, {"readback": "  "}, {"readback": None},
               {"readback": {"a": False}}, {"readback": [""]}, {"READBACK": "x"}, [{"readback": "x"}],
               {"readback": True}, {"readback": 5}, {"readback": 1.5}, {"readback": {"a": 5, "b": True}},
               {"readback": [1, True, None]}, 5, 1.5)
        for old in ("before_publish", "uncertain"):
            for evidence in bad:
                with self.subTest(old=old, evidence=evidence):
                    post = self.state.add_post(SLOT, state=old)
                    with self.assertRaises(EvidenceRequired):
                        self.state.transition(post, "published", reason="r", evidence=evidence)
                    self.assertEqual(self.state.get_post(post)["state"], old)

    def test_a_non_blank_string_or_a_dict_with_real_readback_content_is_evidence(self):
        good = ("readback ok", {"readback": "Published"}, {"readback": {"status": "Published"}},
                {"readback": ["Published"], "status": False}, {"readback": {"status": 200, "title": "Bài"}})
        for old in ("before_publish", "uncertain"):
            for evidence in good:
                with self.subTest(old=old, evidence=evidence):
                    post = self.publish(old, evidence)
                    self.assertEqual(self.state.get_post(post)["state"], "published")


class SafeSrcTests(unittest.TestCase):
    def test_only_relative_names_and_https_urls_with_a_host_are_accepted(self):
        for value in ("https://cdn.nailhub.ai/a.webp", "placeholder-1.webp", "images/a.webp", "a.webp?x=1"):
            with self.subTest(value=value):
                self.assertEqual(_safe_src(value), value)

    def test_protocol_relative_backslash_and_other_schemes_are_refused(self):
        for value in ("//evil.example/x.webp", "//evil.example", "///x.webp", "\\\\evil.example\\x.webp",
                      "/\\evil.example/x.webp", "https:///x.webp", "https:/x.webp", "https://", "http://a.example/x.webp",
                      "javascript:alert(1)", "data:image/png;base64,AAAA", "ftp://a.example/x", "file:///etc/passwd",
                      "https://a.example/x y", "https://a.example/x\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _safe_src(value)

    def test_the_renderer_refuses_a_protocol_relative_image(self):
        article = {"sections": [{"h2": "Mục", "image_slot": True, "paragraphs": []}]}
        image = {"src": "//evil.example/x.webp", "alt": "Ảnh minh họa", "width": 1200, "height": 675}
        with self.assertRaises(ValueError):
            render_html(article, [image])
        html = render_html(article, [{**image, "src": "https://cdn.nailhub.ai/x.webp"}])
        self.assertIn('src="https://cdn.nailhub.ai/x.webp"', html)


if __name__ == "__main__":
    unittest.main()
