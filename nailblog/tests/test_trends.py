"""Trend signals: Trending RSS parsing, relevance filter, snapshots, the score formula. No network."""
import math
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

from nailblog import trends
from nailblog.config import DEFAULT_ANCHORS, DEFAULT_CONTEXT, DEFAULT_KEYWORDS, TrendsConfig
from nailblog.schedule import VN_TZ
from nailblog.sources import SourceError
from nailblog.state import State
from nailblog.trends import TrendBoard, TrendsApiProvider, fetch_trending, is_relevant, ratio_score
from tests.fakes import TRENDING_RSS, TRENDS_URL, FakeOpener, trending_feed

NOON = datetime(2026, 10, 16, 12, 0, tzinfo=VN_TZ)
NOW = NOON.timestamp()


def at(days_back, hour=12):
    return (NOON - timedelta(days=days_back)).replace(hour=hour).timestamp()


class ParseTests(unittest.TestCase):
    def test_the_observed_trending_shape_is_parsed(self):
        items = fetch_trending(TRENDS_URL, opener=FakeOpener({TRENDS_URL: TRENDING_RSS}))
        self.assertEqual([item["term"] for item in items],
                         ["chrome nails", "taylor swift", "nfl scores", "sephora sale", "halloween costumes"])
        first = items[0]
        self.assertEqual((first["traffic"], first["traffic_text"]), (20000, "20000+"))
        self.assertEqual(first["published"], "2026-10-16T09:00:00+00:00")
        self.assertEqual(first["news"], [
            {"title": "Chrome nails are the fall look", "url": "https://www.allure.com/chrome-nails", "source": "Allure"},
            {"title": "How to do chrome powder at home", "url": "https://www.byrdie.com/chrome", "source": "Byrdie"}])
        self.assertEqual(items[1]["traffic"], 2_000_000)

    def test_it_is_one_get_to_the_pinned_url(self):
        opener = FakeOpener({TRENDS_URL: TRENDING_RSS})
        fetch_trending(TRENDS_URL, opener=opener, timeout=7)
        self.assertEqual([(call["url"], call["method"], call["timeout"]) for call in opener.calls],
                         [(TRENDS_URL, "GET", 7)])

    def test_other_hosts_and_plain_http_are_refused_before_any_request(self):
        for url in ("https://evil.example/trending/rss?geo=US", "http://trends.google.com/trending/rss?geo=US",
                    "https://trends.google.com.evil.example/trending/rss", ""):
            with self.subTest(url=url):
                opener = FakeOpener({})
                with self.assertRaisesRegex(SourceError, "origin_not_pinned"):
                    fetch_trending(url, opener=opener)
                self.assertEqual(opener.calls, [])

    def test_the_xml_and_size_guards_of_sources_apply(self):
        cases = ((b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "b">]><rss/>', "doctype_refused"),
                 ('<rss/>'.encode("utf-16"), "encoding_refused"), (b"<html/>", "not_a_feed"), (b"<rss>", "invalid_xml"),
                 (b" " * (2 * 1024 * 1024 + 1), "too_large"),
                 (HTTPError(TRENDS_URL, 429, "x", {}, None), "http_429"), (TimeoutError(), "timeout"))
        for route, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(SourceError, code):
                fetch_trending(TRENDS_URL, opener=FakeOpener({TRENDS_URL: route}))

    def test_items_are_capped_and_untitled_or_unsafe_parts_are_dropped(self):
        news = [(f"News {i}", "https://a.example/" + str(i), "A") for i in range(9)]
        raw = trending_feed([("kept", "1M+", news), ("", "5+", []), ("javascript news", "5+",
                                                                      [("x", "javascript:alert(1)", "A")])])
        items = fetch_trending(TRENDS_URL, opener=FakeOpener({TRENDS_URL: raw}))
        self.assertEqual([item["term"] for item in items], ["kept", "javascript news"])
        self.assertEqual(len(items[0]["news"]), 5)
        self.assertEqual(items[1]["news"][0]["url"], "")
        many = trending_feed([(f"term {i}", "1+", []) for i in range(130)])
        self.assertEqual(len(fetch_trending(TRENDS_URL, opener=FakeOpener({TRENDS_URL: many}))), 100)

    def test_the_default_opener_is_pinned_to_the_trends_host(self):
        built = []
        original = trends._feed_opener
        trends._feed_opener = lambda host: built.append(host) or FakeOpener({TRENDS_URL: TRENDING_RSS})
        try:
            fetch_trending(TRENDS_URL)
        finally:
            trends._feed_opener = original
        self.assertEqual(built, ["trends.google.com"])

    def test_traffic_and_views_texts(self):
        for text, value in (("5000+", 5000), ("200K+", 200_000), ("1M+", 1_000_000), ("2,000+", 2000), ("2B+", 2 * 10 ** 9),
                            ("", 0), ("lots", 0), ("1.5M+", 1_500_000)):
            with self.subTest(text=text):
                self.assertEqual(trends.parse_traffic(text), value)
        for text, value in (("12M", 12_000_000), ("450K", 450_000), ("1200", 1200), ("1.5m", 1_500_000), ("2b", 2 * 10 ** 9),
                            ("0", None), ("-5", None), ("12X", None), ("", None), ("K", None), ("9999999999999", None)):
            with self.subTest(views=text):
                self.assertEqual(trends.parse_views(text), value)
        self.assertEqual([trends.format_traffic(n) for n in (0, 5000, 20000, 2_000_000)], ["?", "5K+", "20K+", "2M+"])
        self.assertEqual([trends.format_views(n) for n in (900, 450_000, 12_000_000)], ["900", "450K", "12M"])


class RelevanceTests(unittest.TestCase):
    ANCHORS, CONTEXT = DEFAULT_ANCHORS, DEFAULT_CONTEXT

    def relevant(self, term, *titles):
        return is_relevant(term, [{"title": title} for title in titles], self.ANCHORS)

    def test_an_item_needs_an_anchor_word_in_its_keyword_or_a_news_title(self):
        self.assertTrue(self.relevant("chrome nails trend"))
        self.assertTrue(self.relevant("Megan Fox manicure"))
        self.assertTrue(self.relevant("taylor swift", "Her manicure goes viral"))
        self.assertTrue(self.relevant("viral look", "Nail art ideas for fall"))
        self.assertFalse(self.relevant("nfl scores", "Sunday scores and standings"))
        self.assertFalse(self.relevant("halloween costumes"))

    def test_context_words_alone_never_make_an_item_relevant(self):
        for term in ("google chrome update", "salon owner arrested", "gel blaster", "sephora sale", "glitter bomb",
                     "acrylic paint", "makeup tutorial", "french tip jar", "cat eye glasses", "polish election"):
            with self.subTest(term=term):
                self.assertFalse(self.relevant(term))
        self.assertFalse(self.relevant("sephora sale", "Best beauty deals this week"))
        for word in self.CONTEXT:
            self.assertFalse(self.relevant(word, word))

    def test_anchors_match_whole_words_without_case_or_accents(self):
        self.assertFalse(self.relevant("snails in the garden"))             # not "nails"
        self.assertFalse(self.relevant("nailed it"))
        self.assertFalse(self.relevant("manicured lawn"))
        self.assertFalse(self.relevant("mania"))
        self.assertTrue(self.relevant("NAILS today"))
        self.assertTrue(self.relevant("new manis"))                          # plural s tolerated
        self.assertTrue(self.relevant("Làm Móng Nail"))
        self.assertTrue(self.relevant("x", "A pedi and a mani"))
        self.assertFalse(is_relevant("x", [{"title": "Nail-art look"}], ("nail art",)))  # a hyphen is not a space

    def test_the_anchor_list_is_configurable(self):
        self.assertFalse(is_relevant("chrome nails", [], ("hair",)))
        self.assertTrue(is_relevant("hair color", [], ("hair",)))

    def test_context_words_label_a_relevant_item_without_deciding_anything(self):
        labels = trends.context_labels("chrome nails", [{"title": "Glitter and gel look"}], self.CONTEXT)
        self.assertEqual(labels, ("gel", "chrome", "glitter"))
        self.assertEqual(trends.context_labels("nfl scores", [], self.CONTEXT), ())

    def test_defaults_match_the_spec(self):
        self.assertEqual(DEFAULT_ANCHORS, ("nail", "nails", "manicure", "pedicure", "mani", "pedi", "nail art", "nail salon",
                                           "nail polish", "nail tech"))
        self.assertEqual(DEFAULT_CONTEXT, ("polish", "gel", "acrylic", "chrome", "french tip", "cat eye", "salon", "beauty",
                                           "makeup", "glitter"))
        self.assertGreaterEqual(len(DEFAULT_KEYWORDS), 14)
        for expected in ("chrome nails", "cat eye nails", "velvet nails", "burgundy nails", "chocolate nails",
                         "french tip nails", "halloween nails", "christmas nails", "glitter nails", "gel x", "builder gel",
                         "nail art", "short nails", "almond nails"):
            self.assertIn(expected, DEFAULT_KEYWORDS)


class StateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "s.sqlite3")

    def test_refresh_stores_a_snapshot_per_item_with_its_news(self):
        config = type("C", (), {"trends": TrendsConfig()})
        items, note = trends.refresh(self.state, config, NOW, opener=FakeOpener({TRENDS_URL: TRENDING_RSS}))
        self.assertEqual((len(items), note), (5, None))
        rows = self.state.trend_snapshots(0)
        self.assertEqual([(row["source"], row["term"], row["traffic"], row["fetched_at"]) for row in rows[:2]],
                         [("google_trends_us", "chrome nails", 20000, NOW), ("google_trends_us", "taylor swift", 2_000_000, NOW)])
        self.assertEqual(rows[0]["news"][0]["source"], "Allure")

    def test_a_failed_fetch_is_a_note_and_stores_nothing(self):
        config = type("C", (), {"trends": TrendsConfig()})
        items, note = trends.refresh(self.state, config, NOW, opener=FakeOpener({TRENDS_URL: TimeoutError()}))
        self.assertEqual((items, note), ([], "Google Trending lỗi: timeout"))
        self.assertEqual(self.state.trend_snapshots(0), [])

    def test_old_snapshots_are_pruned_after_90_days(self):
        config = type("C", (), {"trends": TrendsConfig()})
        self.state.add_trend_snapshots(NOW - 91 * 86400, "google_trends_us", [("ancient", 1, [])])
        self.state.add_trend_snapshots(NOW - 89 * 86400, "google_trends_us", [("recent", 1, [])])
        trends.refresh(self.state, config, NOW, opener=FakeOpener({TRENDS_URL: TRENDING_RSS}))
        self.assertEqual({row["term"] for row in self.state.trend_snapshots(0)} & {"ancient", "recent"}, {"recent"})

    def test_known_rss_articles_are_not_stored_twice(self):
        row = [("Merlot nails", 0, [])]
        self.assertEqual(self.state.add_trend_snapshots(NOW, "rss:bustle.com", row, skip_known=True), 1)
        self.assertEqual(self.state.add_trend_snapshots(NOW + 1, "rss:bustle.com", row, skip_known=True), 0)
        self.assertEqual(self.state.add_trend_snapshots(NOW + 2, "rss:other.com", row, skip_known=True), 1)

    def test_tables_exist_with_the_columns_of_the_spec(self):
        with self.state.connection() as db:
            snapshot = [row[1] for row in db.execute("PRAGMA table_info(trend_snapshots)")]
            tags = [row[1] for row in db.execute("PRAGMA table_info(tiktok_tags)")]
        self.assertEqual(snapshot, ["id", "fetched_at", "source", "term", "traffic", "news_json"])
        self.assertEqual(tags, ["id", "added_at", "tag", "views"])


class ScoreTests(unittest.TestCase):
    """total = 0.2*hot + 0.4*d3 + 0.3*d7 + 0.1*tiktok, checked against hand-computed days."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.state = State(Path(self.directory.name) / "s.sqlite3")
        self.config = TrendsConfig(keywords=("cat eye nails", "chrome nails", "gel x"))

    def mention(self, days_back, term="Cat eye nails trend", source="rss:a.example", hour=12):
        self.state.add_trend_snapshots(at(days_back, hour), source, [(f"{term} {days_back}", 0, [])])

    def board(self):
        return TrendBoard(self.state, self.config, NOW)

    def steady_history(self):
        """1 mention a day today..-9 (10 days) plus 11 more days -10..-20: recent3=1/day, previous7=1/day,
        recent7=1/day, previous28=14/28=0.5/day."""
        for back in list(range(0, 21)):
            self.mention(back)

    def test_no_data_scores_zero(self):
        score = self.board().score("cat eye nails")
        self.assertEqual((score.total, score.hot, score.d3, score.d7, score.tiktok), (0.0, 0.0, 0.0, 0.0, 0.0))

    def test_three_and_seven_day_momentum_from_daily_frequency(self):
        self.steady_history()
        score = self.board().score("cat eye nails")
        # d3: recent avg 1 vs previous avg 1 -> ratio 1 -> 1/3.  d7: recent 1 vs previous (days -7..-20 = 14)/28 -> 2 -> 2/3
        self.assertAlmostEqual(score.d3, 1 / 3, places=4)
        self.assertAlmostEqual(score.d7, 2 / 3, places=4)
        self.assertEqual((score.hot, score.tiktok), (0.0, 0.0))
        self.assertAlmostEqual(score.total, 0.4 * (1 / 3) + 0.3 * (2 / 3), places=3)

    def test_growth_raises_the_momentum_and_it_saturates_at_three_times(self):
        for back in range(3, 10):
            self.mention(back)                       # previous 7 days: 1 a day
        for back in range(0, 3):
            for copy in range(3):
                self.mention(back, term=f"Cat eye nails copy{copy}")   # last 3 days: 3 a day -> ratio 3
        self.assertAlmostEqual(self.board().score("cat eye nails").d3, 1.0, places=4)
        for copy in range(3):
            self.mention(0, term=f"Cat eye nails more{copy}")          # ratio above 3 stays at 1
        self.assertAlmostEqual(self.board().score("cat eye nails").d3, 1.0, places=4)

    def test_an_empty_previous_window_is_smoothed_with_plus_one(self):
        self.mention(0)
        self.mention(1)
        self.mention(2)                              # recent avg 1, previous avg 0 -> ratio 1 + 1 = 2 -> 2/3
        score = self.board().score("cat eye nails")
        self.assertAlmostEqual(score.d3, 2 / 3, places=4)
        self.assertAlmostEqual(score.d7, min(1.0, ((3 / 7) + 1) / 3), places=4)
        self.assertEqual(ratio_score(0, 5), 0.0)
        self.assertEqual(ratio_score(0, 0), 0.0)
        self.assertAlmostEqual(ratio_score(2, 0), 1.0)
        self.assertAlmostEqual(ratio_score(1, 0), 2 / 3)
        self.assertAlmostEqual(ratio_score(1, 1), 1 / 3)

    def test_polling_eight_times_a_day_counts_the_same_item_once(self):
        for hour in range(0, 24, 3):
            self.state.add_trend_snapshots(at(0, hour), trends.GOOGLE_SOURCE, [("cat eye nails", 20000, [])])
        board = self.board()
        self.assertEqual(board.frequency("cat eye nails", 0, 0), 1.0)

    def test_hot_today_needs_a_relevant_item_of_the_last_24_hours_and_uses_traffic(self):
        self.state.add_trend_snapshots(at(0, 9), trends.GOOGLE_SOURCE, [("cat eye nails 2026", 20000, [])])
        score = self.board().score("cat eye nails")
        self.assertAlmostEqual(score.hot, 0.5 + 0.5 * math.log10(20000) / 6, places=4)
        self.assertEqual(score.traffic, 20000)
        # the highest traffic among the matching items wins; 1M and more saturates
        self.state.add_trend_snapshots(at(0, 10), trends.GOOGLE_SOURCE, [("cat eye nails today", 5_000_000, [])])
        self.assertAlmostEqual(self.board().score("cat eye nails").hot, 1.0, places=4)

    def test_only_the_last_24_hours_count_as_hot(self):
        self.state.add_trend_snapshots(NOW - 25 * 3600, trends.GOOGLE_SOURCE, [("cat eye nails", 20000, [])])
        self.assertEqual(self.board().score("cat eye nails").hot, 0.0)
        self.state.add_trend_snapshots(NOW - 23 * 3600, trends.GOOGLE_SOURCE, [("cat eye nails", 20000, [])])
        self.assertGreater(self.board().score("cat eye nails").hot, 0.0)

    def test_an_irrelevant_or_other_source_item_is_not_hot(self):
        self.state.add_trend_snapshots(at(0, 9), "rss:a.example", [("cat eye nails", 20000, [])])
        self.assertEqual(self.board().score("cat eye nails").hot, 0.0)            # only Google Trending items are hot
        self.state.add_trend_snapshots(at(0, 9), trends.GOOGLE_SOURCE, [("cat eye nails", 20000, [])])
        self.assertGreater(self.board().score("cat eye nails").hot, 0.0)
        config = TrendsConfig(keywords=("cat eye nails",), anchor_terms=("hair",))   # the anchor list no longer matches
        self.assertEqual(TrendBoard(self.state, config, NOW).score("cat eye nails").hot, 0.0)
        self.assertEqual(self.board().score("gel x").hot, 0.0)                    # another keyword is not in those items

    def test_tiktok_uses_the_best_recent_matching_tag(self):
        self.state.add_tiktok_tag(NOW - 20 * 86400, "cateyenails", 900_000_000)     # older than 14 days: ignored
        self.assertEqual(self.board().score("cat eye nails").tiktok, 0.0)
        self.state.add_tiktok_tag(NOW - 86400, "cateyenails", 12_000_000)
        self.state.add_tiktok_tag(NOW - 3600, "cateyenails", 450_000)
        self.state.add_tiktok_tag(NOW - 3600, "nails", 99_000_000)                # generic tag: does not lift the term
        score = self.board().score("cat eye nails")
        self.assertAlmostEqual(score.tiktok, math.log10(1 + 12_000_000) / 8, places=4)
        self.assertEqual(self.board().score("chrome nails").tiktok, 0.0)
        self.state.add_tiktok_tag(NOW, "chromenailsdesign", 100_000_000_000)      # a more specific tag saturates at 1
        self.assertEqual(self.board().score("chrome nails").tiktok, 1.0)
        self.assertAlmostEqual(self.board().score("chrome nails").total, 0.1)

    def test_total_is_the_weighted_sum_with_all_four_parts(self):
        self.steady_history()
        self.state.add_trend_snapshots(at(0, 9), trends.GOOGLE_SOURCE, [("cat eye nails 2026", 20000, [])])
        self.state.add_tiktok_tag(NOW - 3600, "cateyenails", 12_000_000)
        score = self.board().score("cat eye nails")
        d3 = ((2 + 1 + 1) / 3) / 1.0 / 3          # today now holds 2 items: recent avg 4/3, previous avg 1
        d7 = ((2 + 6) / 7) / (14 / 28) / 3
        hot = 0.5 + 0.5 * math.log10(20000) / 6
        tiktok = math.log10(1 + 12_000_000) / 8
        self.assertAlmostEqual(score.total, 0.2 * hot + 0.4 * d3 + 0.3 * d7 + 0.1 * tiktok, places=3)
        self.assertEqual(set(score.as_dict()), {"term", "total", "hot", "d3", "d7", "tiktok", "traffic"})

    def test_best_for_picks_the_highest_known_term_the_text_mentions(self):
        self.steady_history()
        self.state.add_trend_snapshots(at(0, 9), trends.GOOGLE_SOURCE, [("viral glass skin nails", 20000, [
            {"title": "A manicure trend"}])])
        board = self.board()
        self.assertEqual(board.best_for("Cat eye nails mùa đông cho tiệm").term, "cat eye nails")
        self.assertGreater(board.best_for("Cat eye nails mùa đông").total, 0)
        self.assertEqual(board.best_for("Thuế tiệm nail cần biết"), trends.TrendScore("", 0.0, 0.0, 0.0, 0.0, 0.0))
        self.assertIn("viral glass skin nails", board.known_terms())      # today's relevant trending terms are known too

    def test_table_lists_relevant_trending_first_then_scored_keywords_and_never_irrelevant_ones(self):
        self.steady_history()
        for term, traffic, news in (("chrome nails", 20000, []), ("taylor swift", 2_000_000, [{"title": "manicure viral"}]),
                                    ("nfl scores", 500_000, [{"title": "scores"}])):
            self.state.add_trend_snapshots(at(0, 9), trends.GOOGLE_SOURCE, [(term, traffic, news)])
        rows = self.board().table(8)
        names = [row.term for row in rows]
        self.assertNotIn("nfl scores", names)
        self.assertEqual(set(names[:2]), {"chrome nails", "taylor swift"})
        self.assertIn("cat eye nails", names[2:])                       # a watched keyword with a score follows
        self.assertNotIn("gel x", names)                                # a watched keyword without any score does not
        self.assertEqual([row.total for row in rows[:2]], sorted([row.total for row in rows[:2]], reverse=True))
        self.assertEqual(len(self.board().table(1)), 1)

    def test_twelve_relevant_items_still_give_at_most_eight_rows(self):
        for number in range(12):
            self.state.add_trend_snapshots(at(0, 9), trends.GOOGLE_SOURCE, [(f"glitter nails {number}", 1000 * (number + 1), [])])
        self.assertEqual(len(self.board().table(8)), 8)


class ApiInterfaceTests(unittest.TestCase):
    def test_the_google_trends_api_is_declared_but_has_no_implementation(self):
        with self.assertRaises(TypeError):
            TrendsApiProvider()

        class Partial(TrendsApiProvider):
            pass
        with self.assertRaises(TypeError):
            Partial()

        class Full(TrendsApiProvider):
            def interest(self, terms, *, start, end):
                return {}
        self.assertEqual(Full().interest(["a"], start=None, end=None), {})


if __name__ == "__main__":
    unittest.main()
