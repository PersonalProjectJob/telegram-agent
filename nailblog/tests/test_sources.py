import hashlib
import inspect
import io
import json
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import OpenerDirector, Request

from nailblog import sources
from nailblog.sources import MAX_FEED_BYTES, SourceError, fetch_feeds, fetch_posts, parse_feed
from tests.fakes import ATOM, BLOGS_JSON, RSS, FakeOpener

API = "https://api.nailhub.ai"
BLOGS_URL = API + "/api/v1/blogs?pageNumber=1&pageSize=50"


class ParseFeedTests(unittest.TestCase):
    def test_rss_items_have_the_five_fields_and_plain_text_snippets(self):
        items = parse_feed(RSS)
        self.assertEqual([item["title"] for item in items], ["Merlot nails & more", "Chocolate"])
        first = items[0]
        self.assertEqual(set(first), {"id", "title", "url", "published", "snippet"})
        self.assertEqual(first["url"], "https://www.bustle.com/merlot")
        self.assertEqual(first["id"], hashlib.sha256(first["url"].encode()).hexdigest()[:16])
        self.assertEqual(first["published"], "2026-10-14T09:00:00+00:00")
        self.assertEqual(first["snippet"], "Merlot is hot.")  # tags and the script body are gone
        self.assertNotIn("alert", first["snippet"])
        self.assertEqual(items[1]["snippet"], "Chocolate brown is back for winter.")
        self.assertEqual(items[1]["published"], "")

    def test_items_without_title_or_with_a_non_http_link_are_skipped(self):
        urls = [item["url"] for item in parse_feed(RSS)]
        self.assertEqual(urls, ["https://www.bustle.com/merlot", "https://www.bustle.com/chocolate"])

    def test_atom_entry_uses_the_alternate_link_and_updated_time(self):
        (item,) = parse_feed(ATOM)
        self.assertEqual(item["url"], "https://tdlr.texas.gov/renew")
        self.assertEqual(item["published"], "2026-10-13T08:00:00+00:00")
        self.assertEqual(item["snippet"], "Renew your license on time.")

    def test_snippet_is_capped_at_1000_characters(self):
        body = ("word " * 600).encode()
        feed = b"<rss><channel><item><title>T</title><link>https://a.example/x</link><description>" + body + \
               b"</description></item></channel></rss>"
        self.assertIn(len(parse_feed(feed)[0]["snippet"]), (999, 1000))  # cut at 1000, then trimmed

    def test_at_most_30_items_per_feed(self):
        entries = "".join(f"<item><title>T{i}</title><link>https://a.example/{i}</link></item>" for i in range(45))
        self.assertEqual(len(parse_feed(f"<rss><channel>{entries}</channel></rss>".encode())), 30)

    def test_hostile_or_foreign_documents_are_refused(self):
        for raw, code in ((b"<rss><channel>", "invalid_xml"), (b"<html><body/></html>", "not_a_feed"),
                          (b'<!DOCTYPE rss [<!ENTITY a "b">]><rss/>', "doctype_refused"),
                          (b'<?xml version="1.0"?><!DOCTYPE x SYSTEM "http://evil/x.dtd"><rss/>', "doctype_refused")):
            with self.subTest(code=code):
                with self.assertRaisesRegex(SourceError, code):
                    parse_feed(raw)


class FetchFeedsTests(unittest.TestCase):
    def test_fake_opener_matches_the_real_open_signature(self):
        self.assertEqual(list(inspect.signature(FakeOpener.__call__).parameters)[1:],
                         list(inspect.signature(OpenerDirector.open).parameters)[1:])

    def test_one_failing_feed_does_not_spoil_the_pass_and_the_reasons_are_noted(self):
        opener = FakeOpener({
            "https://ok.example/rss": RSS, "https://atom.example/feed": ATOM, "https://bad.example/rss": b"<rss>",
            "https://gone.example/rss": HTTPError("https://gone.example/rss", 404, "nf", {}, None),
            "https://slow.example/rss": TimeoutError(), "https://down.example/rss": URLError("dns"),
            "https://big.example/rss": b"x" * (MAX_FEED_BYTES + 10)})
        items, notes = fetch_feeds(list(opener.routes) + ["http://plain.example/rss"], opener=opener, timeout=7)
        self.assertEqual(len(items), 3)
        self.assertEqual(sorted(notes), sorted([
            "nguồn bad.example lỗi: invalid_xml", "nguồn gone.example lỗi: http_404", "nguồn slow.example lỗi: timeout",
            "nguồn down.example lỗi: unreachable", "nguồn big.example lỗi: too_large",
            "nguồn plain.example lỗi: https_only"]))
        self.assertNotIn("http://plain.example/rss", [call["url"] for call in opener.calls])  # never even requested
        self.assertTrue(all(call["method"] == "GET" and call["timeout"] == 7 for call in opener.calls))

    def test_size_limit_is_exactly_two_megabytes(self):
        pad = b" " * (MAX_FEED_BYTES - len(RSS))
        items, notes = fetch_feeds(["https://a.example/rss"], opener=FakeOpener({"https://a.example/rss": RSS + pad}))
        self.assertEqual((len(items), notes), (2, []))
        items, notes = fetch_feeds(["https://a.example/rss"], opener=FakeOpener({"https://a.example/rss": RSS + pad + b" "}))
        self.assertEqual((items, notes), ([], ["nguồn a.example lỗi: too_large"]))

    def test_the_same_article_from_two_feeds_appears_once(self):
        opener = FakeOpener({"https://a.example/1": RSS, "https://b.example/2": RSS})
        items, notes = fetch_feeds(list(opener.routes), opener=opener)
        self.assertEqual((len(items), notes), (2, []))

    def test_feed_failure_notes_carry_no_content(self):
        opener = FakeOpener({"https://a.example/rss": b"<rss><SECRET-BODY>"})
        _items, notes = fetch_feeds(["https://a.example/rss"], opener=opener)
        self.assertNotIn("SECRET", " ".join(notes))


class FetchPostsTests(unittest.TestCase):
    def test_reads_the_pinned_endpoint_and_maps_fields(self):
        opener = FakeOpener({BLOGS_URL: BLOGS_JSON})
        posts = fetch_posts(API, opener=opener, timeout=9)
        self.assertEqual([call["url"] for call in opener.calls], [BLOGS_URL])
        self.assertEqual((opener.calls[0]["method"], opener.calls[0]["timeout"]), ("GET", 9))
        self.assertEqual(posts, [
            {"id": "a1", "slug": "mang-lai-gi-cho-tiem", "title": "Mang lại gì cho tiệm", "hashtags": ["nail", "tiem"],
             "categorySlug": "nail-salon", "publishedAt": "2026-10-10T08:00:00.1"},
            {"id": "a2", "slug": "bai-cu", "title": "Bài cũ hơn 90 ngày", "hashtags": [], "categorySlug": "hot-trend",
             "publishedAt": "2026-01-01T08:00:00"}])

    def test_a_bare_list_is_accepted(self):
        raw = json.dumps([{"slug": "a", "translations": []}]).encode()
        self.assertEqual(fetch_posts(API, opener=FakeOpener({BLOGS_URL: raw}))[0]["slug"], "a")

    def test_origin_is_pinned_and_nothing_is_requested_otherwise(self):
        for origin in ("https://evil.example", "http://api.nailhub.ai", "https://api.nailhub.ai.evil.example",
                       "https://api.nailhub.ai:8443", "https://user@api.nailhub.ai", "https://api.nailhub.ai/v1", ""):
            with self.subTest(origin=origin):
                opener = FakeOpener({})
                with self.assertRaisesRegex(SourceError, "origin_not_pinned"):
                    fetch_posts(origin, opener=opener)
                self.assertEqual(opener.calls, [])

    def test_failures_are_short_codes(self):
        for route, code in ((HTTPError(BLOGS_URL, 500, "x", {}, None), "http_500"), (TimeoutError(), "timeout"),
                            (b"not json", "invalid_json"), (b'{"items": 5}', "unexpected_shape"),
                            (b" " * (sources.MAX_API_BYTES + 1), "too_large")):
            with self.subTest(code=code):
                with self.assertRaisesRegex(SourceError, code):
                    fetch_posts(API, opener=FakeOpener({BLOGS_URL: route}))


class RedirectPolicyTests(unittest.TestCase):
    def redirect(self, handler, target):
        return handler.redirect_request(Request("https://a.example/rss"), io.BytesIO(), 301, "Moved", {}, target)

    def test_a_feed_redirect_stays_on_the_configured_host_or_its_www_twin_over_https(self):
        handler = sources._SafeRedirect("a.example")
        for target in ("https://a.example/rss2", "https://www.a.example/rss", "https://A.Example/rss"):
            with self.subTest(target=target):
                self.assertEqual(self.redirect(handler, target).full_url, target)
        self.assertEqual(self.redirect(sources._SafeRedirect("www.a.example"), "https://a.example/x").full_url,
                         "https://a.example/x")

    def test_other_hosts_localhost_numeric_ips_and_unsafe_urls_are_refused(self):
        handler = sources._SafeRedirect("a.example")
        for target in ("https://b.example/rss", "https://a.example.evil.example/rss", "https://evil.example/a.example",
                       "http://a.example/rss", "https://localhost/rss", "https://127.0.0.1/rss", "https://[::1]/rss",
                       "https://2130706433/rss", "https://0x7f000001/rss", "https://0177.0.0.1/rss",
                       "https://127.1/rss", "https://a.example:8443/rss", "https://user@a.example/rss",
                       "file:///etc/passwd", "ftp://a.example/rss"):
            with self.subTest(target=target), self.assertRaises(URLError):
                self.redirect(handler, target)

    def test_the_policy_cannot_be_built_for_an_ip_or_localhost(self):
        for host in ("", "localhost", "127.0.0.1", "2130706433", "0x7f.1", "::1"):
            with self.subTest(host=host), self.assertRaises(ValueError):
                sources._SafeRedirect(host)

    def test_the_blog_api_never_follows_a_redirect(self):
        with self.assertRaises(URLError):
            self.redirect(sources._NoRedirect(), "https://api.nailhub.ai/other")


if __name__ == "__main__":
    unittest.main()
