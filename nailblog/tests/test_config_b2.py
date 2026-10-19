"""[trends] and [daily] configuration (TD-8): validated fail-closed like every other table."""
import tempfile
import unittest
from pathlib import Path

from nailblog._errors import ConfigError
from nailblog.config import DEFAULT_ANCHORS, DEFAULT_CONTEXT, DEFAULT_KEYWORDS, DailyConfig, TrendsConfig, load_config
from tests.helpers import OWNER, load

ROOT = Path(__file__).resolve().parents[2]
URL = "https://trends.google.com/trending/rss?geo=US"


class TrendsConfigTests(unittest.TestCase):
    def invalid(self, pattern, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, None, **sections)

    def test_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory)
        self.assertEqual(config.trends, TrendsConfig(URL, 3, DEFAULT_KEYWORDS, DEFAULT_ANCHORS, DEFAULT_CONTEXT, False))
        self.assertEqual(config.daily, DailyConfig("10:00", "13:00", "20:00"))

    def test_values_are_read_and_terms_are_trimmed(self):
        body = 'poll_hours = 6\nkeywords = ["  chrome   nails ", "gel x"]\nanchor_terms = ["nail", " hair  cut "]\ncontext_terms = ["gel"]'
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None, trends=body)
        self.assertEqual((config.trends.poll_hours, config.trends.keywords, config.trends.anchor_terms,
                          config.trends.context_terms),
                         (6, ("chrome nails", "gel x"), ("nail", "hair cut"), ("gel",)))

    def test_the_rss_url_is_pinned_to_the_google_us_trending_feed(self):
        for url in ("https://evil.example/trending/rss?geo=US", "http://trends.google.com/trending/rss?geo=US",
                    "https://trends.google.com/trending/rss?geo=VN", "https://trends.google.com/trending/rss",
                    "https://trends.google.com/trending/rss?geo=US&x=1", "https://trends.google.com/other?geo=US",
                    "https://user@trends.google.com/trending/rss?geo=US", "https://trends.google.com:8443/trending/rss?geo=US",
                    "https://trends.google.com.evil.example/trending/rss?geo=US", "https://trends.google.com/trending/rss?geo=US#x"):
            with self.subTest(url=url):
                self.invalid("rss_url", trends=f'rss_url = "{url}"')
        self.invalid("rss_url", trends="rss_url = 5")

    def test_poll_hours_keywords_anchor_and_context_terms_are_validated(self):
        for value in ("0", "25", '"3"', "true", "1.5"):
            with self.subTest(poll=value):
                self.invalid("poll_hours", trends=f"poll_hours = {value}")
        for body in ('keywords = []', 'keywords = "chrome"', 'keywords = ["a", "A"]', 'keywords = [""]', 'keywords = [5]',
                     'keywords = ["' + "x" * 61 + '"]', 'keywords = ["' + '","'.join(f"k{n}" for n in range(61)) + '"]'):
            with self.subTest(keywords=body[:30]):
                self.invalid("keywords", trends=body)
        self.invalid("anchor_terms", trends="anchor_terms = []")
        self.invalid("anchor_terms", trends='anchor_terms = ["nail", "Nail"]')
        self.invalid("anchor_terms", trends='anchor_terms = "nail"')
        self.invalid("context_terms", trends='context_terms = ["gel", "GEL"]')
        self.invalid("context_terms", trends="context_terms = [5]")
        with tempfile.TemporaryDirectory() as directory:      # context words are optional, anchors are not
            self.assertEqual(load(directory, None, trends="context_terms = []").trends.context_terms, ())

    def test_the_old_relevance_terms_key_is_refused_with_a_clear_message(self):
        self.invalid("relevance_terms was replaced by trends.anchor_terms and trends.context_terms",
                     trends='relevance_terms = ["nail"]')
        self.invalid("relevance_terms was replaced", trends='relevance_terms = ["nail"]\nanchor_terms = ["nail"]')

    def test_the_official_api_cannot_be_enabled_without_an_implementation(self):
        self.invalid("TrendsApiProvider implementation", trends="api_enabled = true")
        self.invalid("api_enabled", trends='api_enabled = "false"')
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(load(directory, None, trends="api_enabled = false").trends.api_enabled)

    def test_unknown_keys_and_secret_like_keys_are_refused(self):
        self.invalid("unknown configuration key", trends="cookie = 1")
        self.invalid("unknown configuration key", daily="send_at = \"09:00\"")
        self.invalid("never in TOML", trends='api_key = "x"')


class DailyConfigTests(unittest.TestCase):
    def invalid(self, pattern, **sections):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ConfigError, pattern):
                load(directory, None, **sections)

    def test_times_are_read(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory, None, daily='propose_at = "09:30"\npick_deadline = "12:00"\npreview_at = "21:15"')
        self.assertEqual(config.daily, DailyConfig("09:30", "12:00", "21:15"))

    def test_bad_times_and_wrong_order_are_refused(self):
        for name in ("propose_at", "pick_deadline", "preview_at"):
            for value in ('"9am"', "1000", '"24:00"', '"10:60"', '""'):
                with self.subTest(name=name, value=value):
                    self.invalid("daily." + name, daily=f"{name} = {value}")
        self.invalid("propose_at < pick_deadline < preview_at", daily='propose_at = "13:00"')
        self.invalid("propose_at < pick_deadline < preview_at", daily='pick_deadline = "10:00"')
        self.invalid("propose_at < pick_deadline < preview_at", daily='preview_at = "13:00"')

    def test_the_example_template_carries_both_tables_equal_to_the_defaults(self):
        text = (ROOT / "config" / "nailblog.example.toml").read_text(encoding="utf-8")
        for header in ("[trends]", "[daily]"):
            self.assertIn(header, text)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "example.toml"
            path.write_text(text.replace('"/var/lib/agentfleet/nailblog/', f'"{Path(directory).as_posix()}/')
                            .replace("owner_ids = []", f"owner_ids = [{OWNER}]"), encoding="utf-8")
            config = load_config(path)
        self.assertEqual(config.trends, TrendsConfig())
        self.assertEqual(config.daily, DailyConfig())


if __name__ == "__main__":
    unittest.main()
