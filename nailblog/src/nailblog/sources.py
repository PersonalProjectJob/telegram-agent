"""Read-only source material: RSS/Atom feeds from the configured allowlist and the already-published blog list.

Only GET requests. `opener` is called like urllib's `opener(request, timeout=...)` and returns a context
manager whose response offers `read(n)`; tests inject a fake with that exact shape.
"""

import hashlib
import json
import logging
import re
import socket
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import API_ORIGIN

LOG = logging.getLogger(__name__)
MAX_FEED_BYTES = 2 * 1024 * 1024
MAX_API_BYTES = 2 * 1024 * 1024
MAX_ITEMS_PER_FEED = 30
SNIPPET_MAX = 1000
TITLE_MAX = 300
URL_MAX = 500
BLOG_PAGE_SIZE = 50
MAX_PAGES = 20
RECENT_DAYS = 90
HEADERS = {"User-Agent": "NailBlog/0.1 (+https://nailhub.ai)", "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/json"}
_DOCTYPE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_XML_DECLARATION = re.compile(rb"^\s*<\?xml[^>]*?\bencoding\s*=\s*[\"']([^\"']*)[\"']", re.IGNORECASE)
_ALLOWED_ENCODINGS = ("utf-8", "us-ascii")


class SourceError(RuntimeError):
    """A source could not be read. The message is a short code, never content."""


def _numeric_host(host):
    """Hosts that are an address in disguise: dotted/decimal/hex/octal IPv4 forms and every IPv6 literal."""
    if ":" in host:
        return True
    return all(re.fullmatch(r"(?:0[xX][0-9a-fA-F]*|\d+)", label or "x") for label in host.split("."))


def _bare(host):
    return host[4:] if host.startswith("www.") else host


class _SafeRedirect(HTTPRedirectHandler):
    """A redirect is followed only to the configured host (or its www./bare twin), over https on the default
    port, without credentials; never to localhost and never to an address written as an IP literal."""

    def __init__(self, host):
        self.host = (host or "").casefold()
        if not self.host or _numeric_host(self.host) or self.host == "localhost":
            raise ValueError("redirect policy needs a real host name")

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        try:
            port = parsed.port
        except ValueError:
            raise URLError("redirect refused") from None
        target = (parsed.hostname or "").casefold()
        if (parsed.scheme != "https" or not target or parsed.username or parsed.password or port not in (None, 443)
                or target == "localhost" or _numeric_host(target) or _bare(target) != _bare(self.host)):
            raise URLError("redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise URLError("redirect refused")


def _feed_opener(host):
    return build_opener(_SafeRedirect(host)).open


def _api_opener():
    return build_opener(_NoRedirect).open


def _read(opener, url, timeout, limit):
    request = Request(url, headers=HEADERS, method="GET")
    with opener(request, timeout=timeout) as response:
        raw = response.read(limit + 1)
    if len(raw) > limit:
        raise SourceError("too_large")
    return raw


def _reason(error):
    if isinstance(error, SourceError):
        return str(error)
    if isinstance(error, HTTPError):
        return f"http_{error.code}"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(error, URLError):
        return "unreachable"
    return type(error).__name__


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        elif tag in ("p", "br", "li", "div", "h1", "h2", "h3", "h4"):
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(markup):
    """Tags removed, entities decoded, whitespace collapsed."""
    parser = _PlainText()
    try:
        parser.feed(markup or "")
        parser.close()
    except Exception:
        return " ".join(re.sub(r"<[^>]*>", " ", markup or "").split())
    return " ".join("".join(parser.parts).split())


def _iso(text):
    text = (text or "").strip()
    if not text:
        return ""
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _child_text(element, names):
    for child in element:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()
    return ""


def _link(element):
    for child in element:
        if _local(child.tag) != "link":
            continue
        if child.get("rel") not in (None, "alternate"):
            continue
        value = (child.get("href") or child.text or "").strip()
        if value:
            return value
    return ""


def _valid_url(value):
    parsed = urlparse(value)
    return value if (0 < len(value) <= URL_MAX and parsed.scheme in ("http", "https") and parsed.hostname
                     and not any(ord(c) < 33 or ord(c) == 127 for c in value)) else ""


def item_id(url):
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


def safe_xml_root(raw):
    """Parse untrusted XML bytes. Only UTF-8 (or ASCII) documents are accepted, so a DOCTYPE/ENTITY cannot hide
    in UTF-16/UTF-32 or another encoding: the scan runs on the UTF-8 text, before the parser sees anything."""
    if b"\x00" in raw:  # UTF-16/UTF-32 with or without a BOM; a UTF-8 document never holds NUL
        raise SourceError("encoding_refused")
    declared = _XML_DECLARATION.match(raw[:300])
    if declared and declared.group(1).decode("ascii", "replace").casefold() not in _ALLOWED_ENCODINGS:
        raise SourceError("encoding_refused")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SourceError("encoding_refused") from None
    if _DOCTYPE.search(text):
        raise SourceError("doctype_refused")
    try:
        return ET.fromstring(raw)
    except ET.ParseError:
        raise SourceError("invalid_xml") from None


def parse_feed(raw):
    """RSS 2.0 / RSS 1.0 / Atom bytes -> items {id, title, url, published, snippet}."""
    root = safe_xml_root(raw)
    if _local(root.tag) not in ("rss", "feed", "RDF"):
        raise SourceError("not_a_feed")
    items = []
    for element in root.iter():
        if _local(element.tag) not in ("item", "entry"):
            continue
        url = _valid_url(_link(element) or _child_text(element, ("guid", "id")))
        title = " ".join(_child_text(element, ("title",)).split())[:TITLE_MAX]
        if not url or not title:
            continue
        bodies = [plain_text(child.text or "") for child in element
                  if _local(child.tag) in ("description", "summary", "content", "encoded")]
        snippet = max(bodies, key=len, default="")[:SNIPPET_MAX].strip()
        items.append({"id": item_id(url), "title": title, "url": url,
                      "published": _iso(_child_text(element, ("pubDate", "published", "updated", "date"))),
                      "snippet": snippet})
        if len(items) >= MAX_ITEMS_PER_FEED:
            break
    return items


def fetch_feeds(feeds, *, opener=None, timeout=15):
    """Read every feed; one failing feed never spoils the rest. Returns (items, notes)."""
    items, notes, seen = [], [], set()
    for url in feeds:
        host = urlparse(url).hostname or "?"
        try:
            if urlparse(url).scheme != "https":
                raise SourceError("https_only")
            found = parse_feed(_read(opener or _feed_opener(host), url, timeout, MAX_FEED_BYTES))
        except Exception as error:
            reason = _reason(error)
            notes.append(f"nguồn {host} lỗi: {reason}")
            LOG.warning("NailBlog feed failed host=%s reason=%s", host, reason)
            continue
        for item in found:
            if item["id"] not in seen:
                seen.add(item["id"])
                items.append(item)
    return items, notes


def _pinned_origin(origin):
    parsed = urlparse(origin or "")
    expected = urlparse(API_ORIGIN)
    if (parsed.scheme != "https" or parsed.hostname != expected.hostname or parsed.username or parsed.password
            or parsed.port not in (None, 443) or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
        raise SourceError("origin_not_pinned")
    return API_ORIGIN


def _published_date(value):
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment.astimezone(timezone.utc).date() if moment.tzinfo else moment.date()


def _first_title(row):
    """The Vietnamese title when there is one, otherwise the title of the first translation that has one."""
    translations = [t for t in row.get("translations") or () if isinstance(t, dict) and isinstance(t.get("title"), str)]
    for translation in translations:
        if str(translation.get("languageCode", "")).casefold() == "vi":
            return translation["title"]
    return translations[0]["title"] if translations else ""


def _post_rows(raw):
    try:
        data = json.loads(raw)
    except ValueError:
        raise SourceError("invalid_json") from None
    rows = data.get("items") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise SourceError("unexpected_shape")
    posts = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("slug"), str) or not row["slug"]:
            continue
        tags = row.get("hashtags")
        posts.append({"id": str(row.get("id", "")), "slug": row["slug"], "title": _first_title(row),
                      "hashtags": [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else [],
                      "categorySlug": str(row.get("categorySlug") or ""),
                      "publishedAt": str(row.get("publishedAt") or "")})
    return data, posts


def _has_next(data, page):
    """Paging flags of the API: hasNextPage wins, else totalPages; a bare list is a single page."""
    if not isinstance(data, dict):
        return False
    if isinstance(data.get("hasNextPage"), bool):
        return data["hasNextPage"]
    total = data.get("totalPages")
    return type(total) is int and page < total


def fetch_posts(api_origin=API_ORIGIN, *, opener=None, timeout=15, today=None):
    """Published blog list from the public API -> [{id, slug, title, hashtags, categorySlug, publishedAt}].

    Reads `pageNumber=1..` with `pageSize=50`. Stops when the API reports no next page, right after a page that
    holds a post older than 90 days (the API lists newest first), or at MAX_PAGES. No sort order is assumed inside
    a page: every post of every page read is kept, and the caller filters by date afterwards."""
    origin = _pinned_origin(api_origin)
    opener = opener or _api_opener()
    cutoff = (today or datetime.now(timezone.utc).date()) - timedelta(days=RECENT_DAYS)
    posts, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        try:
            raw = _read(opener, f"{origin}/api/v1/blogs?pageNumber={page}&pageSize={BLOG_PAGE_SIZE}", timeout,
                        MAX_API_BYTES)
        except Exception as error:
            raise SourceError(_reason(error)) from None
        data, rows = _post_rows(raw)
        for post in rows:
            if post["slug"] not in seen:
                seen.add(post["slug"])
                posts.append(post)
        dates = [_published_date(post["publishedAt"]) for post in rows]
        if not rows or not _has_next(data, page) or any(day is not None and day < cutoff for day in dates):
            break
    return posts
