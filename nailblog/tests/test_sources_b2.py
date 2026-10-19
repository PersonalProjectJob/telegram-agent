"""Step B2 hardening of sources: opener assembly (F9), XML encodings (F1), pagination (F3)."""
import json
import unittest
from datetime import date, timedelta
from urllib.request import HTTPRedirectHandler

from nailblog import sources
from nailblog.sources import SourceError, fetch_feeds, fetch_posts, parse_feed
from tests.fakes import RSS, FakeOpener

API = "https://api.nailhub.ai"


class OpenerAssemblyTests(unittest.TestCase):
    """The real openers must carry the redirect handlers; a default build_opener() would follow any redirect."""

    @staticmethod
    def redirect_handlers(opener):
        return [h for h in opener.__self__.handlers if isinstance(h, HTTPRedirectHandler)]

    def test_the_api_opener_carries_only_the_no_redirect_handler(self):
        handlers = self.redirect_handlers(sources._api_opener())
        self.assertEqual([type(h) for h in handlers], [sources._NoRedirect])

    def test_the_feed_opener_carries_only_the_host_pinned_handler(self):
        handlers = self.redirect_handlers(sources._feed_opener("a.example"))
        self.assertEqual([type(h) for h in handlers], [sources._SafeRedirect])
        self.assertEqual(handlers[0].host, "a.example")

    def test_each_feed_gets_an_opener_pinned_to_its_own_host(self):
        built = []
        original = sources._feed_opener
        sources._feed_opener = lambda host: built.append(host) or FakeOpener({f"https://{host}/rss": RSS})
        try:
            fetch_feeds(["https://a.example/rss", "https://b.example/rss"])
        finally:
            sources._feed_opener = original
        self.assertEqual(built, ["a.example", "b.example"])


class XmlEncodingTests(unittest.TestCase):
    DOCTYPE = '<?xml version="1.0" encoding="{enc}"?><!DOCTYPE rss [<!ENTITY a "b">]><rss><channel/></rss>'

    def test_doctype_cannot_hide_in_utf16_or_utf32(self):
        for codec in ("utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
            for text in (self.DOCTYPE.format(enc=codec.upper()), "<rss><channel/></rss>"):
                with self.subTest(codec=codec, doctype="DOCTYPE" in text):
                    with self.assertRaisesRegex(SourceError, "encoding_refused"):
                        parse_feed(text.encode(codec))

    def test_a_bom_less_utf16_document_is_refused_too(self):
        with self.assertRaisesRegex(SourceError, "encoding_refused"):
            parse_feed("<rss><channel/></rss>".encode("utf-16-le"))

    def test_only_utf8_and_ascii_declarations_are_accepted(self):
        for enc in ("ISO-8859-1", "windows-1252", "utf-16", "EBCDIC-CP-US"):
            with self.subTest(enc=enc), self.assertRaisesRegex(SourceError, "encoding_refused"):
                parse_feed(f'<?xml version="1.0" encoding="{enc}"?><rss><channel/></rss>'.encode("ascii"))
        for enc in ("UTF-8", "utf-8", "US-ASCII"):
            with self.subTest(enc=enc):
                self.assertEqual(parse_feed(f'<?xml version="1.0" encoding="{enc}"?><rss><channel/></rss>'.encode()), [])

    def test_a_utf8_bom_is_fine_and_invalid_utf8_is_refused(self):
        self.assertEqual(parse_feed(b"\xef\xbb\xbf<rss><channel/></rss>"), [])
        with self.assertRaisesRegex(SourceError, "encoding_refused"):
            parse_feed(b"<rss><channel><title>\xff\xfe</title></channel></rss>")

    def test_doctype_in_plain_utf8_still_has_its_own_code(self):
        with self.assertRaisesRegex(SourceError, "doctype_refused"):
            parse_feed(self.DOCTYPE.format(enc="UTF-8").encode())


def page(rows, **flags):
    return json.dumps({"items": rows, **flags}).encode()


def post_row(slug, day, **extra):
    return {"id": slug, "slug": slug, "categorySlug": "hot-trend", "publishedAt": day, "hashtags": [],
            "translations": [{"languageCode": "Vi", "title": f"Bài {slug}"}], **extra}


def page_url(number):
    return f"{API}/api/v1/blogs?pageNumber={number}&pageSize=50"


class PaginationTests(unittest.TestCase):
    TODAY = date(2026, 10, 16)

    def fetch(self, routes):
        opener = FakeOpener(routes)
        return fetch_posts(API, opener=opener, today=self.TODAY), opener

    def test_follows_pages_until_the_api_has_no_next_page(self):
        routes = {page_url(1): page([post_row("a", "2026-10-10")], totalPages=3, hasNextPage=True),
                  page_url(2): page([post_row("b", "2026-10-09")], totalPages=3, hasNextPage=True),
                  page_url(3): page([post_row("c", "2026-10-08")], totalPages=3, hasNextPage=False)}
        posts, opener = self.fetch(routes)
        self.assertEqual([post["slug"] for post in posts], ["a", "b", "c"])
        self.assertEqual([call["url"] for call in opener.calls], [page_url(1), page_url(2), page_url(3)])
        self.assertTrue(all(call["method"] == "GET" for call in opener.calls))

    def test_total_pages_decides_when_has_next_page_is_absent(self):
        routes = {page_url(1): page([post_row("a", "2026-10-10")], totalPages=2),
                  page_url(2): page([post_row("b", "2026-10-09")], totalPages=2),
                  page_url(3): page([post_row("never", "2026-10-08")], totalPages=2)}
        posts, opener = self.fetch(routes)
        self.assertEqual(([post["slug"] for post in posts], len(opener.calls)), (["a", "b"], 2))

    def test_stops_after_a_page_holding_a_post_older_than_90_days_and_keeps_that_whole_page(self):
        routes = {page_url(1): page([post_row("new", "2026-10-10"), post_row("old", "2026-06-01")], hasNextPage=True),
                  page_url(2): page([post_row("never", "2026-05-01")], hasNextPage=True)}
        posts, opener = self.fetch(routes)
        self.assertEqual(([post["slug"] for post in posts], len(opener.calls)), (["new", "old"], 1))

    def test_no_sort_order_is_assumed_inside_a_page(self):
        routes = {page_url(1): page([post_row("old", "2026-06-01"), post_row("new", "2026-10-10"),
                                     post_row("newer", "2026-10-12")])}
        posts, _ = self.fetch(routes)
        self.assertEqual([post["slug"] for post in posts], ["old", "new", "newer"])

    def test_the_boundary_is_exactly_90_days(self):
        edge = (self.TODAY - timedelta(days=90)).isoformat()
        before = (self.TODAY - timedelta(days=91)).isoformat()
        routes = {page_url(1): page([post_row("edge", edge)], hasNextPage=True),
                  page_url(2): page([post_row("before", before)], hasNextPage=True)}
        posts, opener = self.fetch(routes)
        self.assertEqual(([post["slug"] for post in posts], len(opener.calls)), (["edge", "before"], 2))

    def test_at_most_20_pages_are_read(self):
        routes = {page_url(n): page([post_row(f"p{n}", "2026-10-10")], hasNextPage=True) for n in range(1, 30)}
        posts, opener = self.fetch(routes)
        self.assertEqual((len(posts), len(opener.calls)), (20, 20))

    def test_an_empty_page_ends_the_walk_and_repeated_slugs_are_kept_once(self):
        routes = {page_url(1): page([post_row("a", "2026-10-10")], hasNextPage=True),
                  page_url(2): page([post_row("a", "2026-10-10")], hasNextPage=True),
                  page_url(3): page([], hasNextPage=True)}
        posts, opener = self.fetch(routes)
        self.assertEqual(([post["slug"] for post in posts], len(opener.calls)), (["a"], 3))

    def test_a_failing_page_fails_the_whole_read_with_a_short_code(self):
        routes = {page_url(1): page([post_row("a", "2026-10-10")], hasNextPage=True), page_url(2): TimeoutError()}
        with self.assertRaisesRegex(SourceError, "timeout"):
            self.fetch(routes)

    def test_the_title_falls_back_to_the_first_translation_when_there_is_no_vietnamese_one(self):
        row = post_row("a", "2026-10-10", translations=[{"languageCode": "en", "title": "English first"},
                                                         {"languageCode": "ko", "title": "Korean"}])
        self.assertEqual(self.fetch({page_url(1): page([row])})[0][0]["title"], "English first")
        row = post_row("a", "2026-10-10", translations=[{"languageCode": "en"}, {"languageCode": "ko", "title": "Hàn"},
                                                         {"languageCode": "vi", "title": "Việt"}])
        self.assertEqual(self.fetch({page_url(1): page([row])})[0][0]["title"], "Việt")
        empty = post_row("a", "2026-10-10", translations=[])
        self.assertEqual(self.fetch({page_url(1): page([empty])})[0][0]["title"], "")


if __name__ == "__main__":
    unittest.main()
