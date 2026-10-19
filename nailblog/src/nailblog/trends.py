"""Trend signals (TD-8): Google Trending RSS (US), saved snapshots, hand-entered TikTok tags and the trend score.

Only one GET per fetch, to the pinned host `trends.google.com`. Google Trends web pages and TikTok are never
scraped: TikTok numbers are typed in by the Owner (`/nailblog trend add #tag <views>`), and the official Google
Trends API (alpha) is only declared here as `TrendsApiProvider` until it is available.

Trend score of a term, in 0..1, `total = 0.2*hot + 0.4*d3 + 0.3*d7 + 0.1*tiktok`:
  hot     0 unless the term is inside a RELEVANT Google Trending item of the last 24 h; then
          0.5 + 0.5 * min(1, log10(traffic) / 6)   (traffic as Google reports it, "5000+" -> 5000; 1M+ saturates).
          Relevant = the item's keyword or one of its news titles holds a word of [trends] anchor_terms (whole
          words, any case). context_terms never make an item relevant; they only label an item that already is.
  d3      momentum of the last 3 days against the 7 days before them.
  d7      momentum of the last 7 days against the 28 days before them.
          TEMPORARY until the Google Trends API is available: the "frequency" of a term on a Vietnam calendar day is
          the number of distinct saved items (Google Trending items and beauty-RSS articles, `trend_snapshots`) whose
          keyword or news titles mention the term that day, so 8 polls a day do not count 8 times.
          momentum = 0 when the recent daily average is 0, else min(1, ratio / 3) with
          ratio = recent_avg / previous_avg, and ratio = recent_avg + 1 when previous_avg is 0 (the "+1" smoothing
          for an empty denominator). Steady mentions give 1/3, tripling or more gives 1.
  tiktok  min(1, log10(1 + views) / 8) of the best hand-entered tag of the last 14 days whose letters hold the term
          (100M views saturate); 0 without a matching tag.
"""

import logging
import math
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from urllib.parse import urlparse

from .config import TRENDS_HOST
from .schedule import VN_TZ
from .sources import (MAX_FEED_BYTES, TITLE_MAX, SourceError, _child_text, _feed_opener, _iso, _local, _read,
                      _reason, _valid_url, safe_xml_root)
from .writer import strip_accents

LOG = logging.getLogger(__name__)
GOOGLE_SOURCE = "google_trends_us"
MAX_TREND_ITEMS = 100
MAX_NEWS_PER_ITEM = 5
WEIGHTS = {"hot": 0.2, "d3": 0.4, "d7": 0.3, "tiktok": 0.1}
HOT_BASE = 0.5
TRAFFIC_LOG_FULL = 6.0       # log10(1,000,000)
RATIO_CAP = 3.0
TIKTOK_LOG_FULL = 8.0        # log10(100,000,000)
TIKTOK_DAYS = 14
MIN_TAG_LETTERS = 4            # shortest term (letters only) that can be matched by a tag
RETENTION_DAYS = 90
WINDOW_DAYS = 35             # 28 + 7 days of history are enough for every momentum window
_TRAFFIC = re.compile(r"\s*([\d.,]+)\s*([KkMmBb]?)\+?\s*")
_MULTIPLIER = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}


class TrendsApiProvider(ABC):
    """Interface of the official Google Trends API (alpha). DECLARED ONLY: there is no implementation, and
    `[trends] api_enabled = true` is refused at configuration load until one exists."""

    @abstractmethod
    def interest(self, terms, *, start, end):
        """Daily interest per term: {term: {date: value}} for the Vietnam calendar days [start, end]."""


def parse_traffic(text):
    """"5000+" -> 5000, "200K+" -> 200000, "1M+" -> 1000000, "2,000+" -> 2000; anything else -> 0."""
    found = _TRAFFIC.fullmatch(text or "")
    if not found:
        return 0
    try:
        return int(float(found.group(1).replace(",", "")) * _MULTIPLIER[found.group(2).casefold()])
    except ValueError:
        return 0


def format_traffic(value):
    value = int(value)
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M+"
    if value >= 1_000:
        return f"{value / 1_000:g}K+"
    return f"{value}+" if value else "?"


def parse_views(text):
    """"12M" / "450K" / "1200" / "1.5M" -> int views, None when it is not a positive count."""
    found = re.fullmatch(r"(\d+(?:[.,]\d+)?)([KkMmBb]?)", text or "")
    if not found:
        return None
    value = int(float(found.group(1).replace(",", ".")) * _MULTIPLIER[found.group(2).casefold()])
    return value if 0 < value <= 10 ** 12 else None


def format_views(value):
    for size, suffix in ((10 ** 9, "B"), (10 ** 6, "M"), (10 ** 3, "K")):
        if value >= size:
            return f"{value / size:g}{suffix}"
    return str(value)


def fetch_trending(url, *, opener=None, timeout=15):
    """Google Trending RSS -> [{term, traffic, traffic_text, published, news: [{title, url, source}]}]."""
    parsed = urlparse(url or "")
    if (parsed.scheme != "https" or parsed.hostname != TRENDS_HOST or parsed.path != "/trending/rss"
            or parsed.query != "geo=US"):
        raise SourceError("origin_not_pinned")   # host, path /trending/rss and query geo=US are all pinned
    try:
        raw = _read(opener or _feed_opener(TRENDS_HOST), url, timeout, MAX_FEED_BYTES)
    except Exception as error:
        raise SourceError(_reason(error)) from None
    root = safe_xml_root(raw)
    if _local(root.tag) != "rss":
        raise SourceError("not_a_feed")
    items = []
    for element in root.iter():
        if _local(element.tag) != "item":
            continue
        term = " ".join(_child_text(element, ("title",)).split())[:TITLE_MAX]
        if not term:
            continue
        news = []
        for child in element:
            if _local(child.tag) != "news_item" or len(news) >= MAX_NEWS_PER_ITEM:
                continue
            title = " ".join(_child_text(child, ("news_item_title",)).split())[:TITLE_MAX]
            if title:
                news.append({"title": title, "url": _valid_url(_child_text(child, ("news_item_url",))),
                             "source": " ".join(_child_text(child, ("news_item_source",)).split())[:100]})
        traffic_text = _child_text(element, ("approx_traffic",))
        items.append({"term": term, "traffic": parse_traffic(traffic_text), "traffic_text": traffic_text,
                      "published": _iso(_child_text(element, ("pubDate",))), "news": news})
        if len(items) >= MAX_TREND_ITEMS:
            break
    return items


def store_trending(state, items, now):
    return state.add_trend_snapshots(now, GOOGLE_SOURCE, [(item["term"], item["traffic"], item["news"])
                                                          for item in items])


def refresh(state, config, now, *, opener=None, timeout=15):
    """One poll: fetch, store, prune old rows. Returns (items, note); a failure is a note, never an exception."""
    try:
        items = fetch_trending(config.trends.rss_url, opener=opener, timeout=timeout)
    except SourceError as error:
        LOG.warning("NailBlog trending fetch failed reason=%s", error)
        return [], f"Google Trending lỗi: {error}"
    store_trending(state, items, now)
    state.prune_trend_snapshots(now - RETENTION_DAYS * 86400)
    return items, None


def fold(text):
    return strip_accents(str(text)).casefold()


@lru_cache(maxsize=512)
def _pattern(term):
    return re.compile(r"(?<!\w)" + re.escape(fold(term).strip()) + r"s?(?!\w)")


def mentions(text, term):
    """Whether `text` holds the word or phrase `term` (accent-free, case-free, plural s tolerated)."""
    return bool(term and term.strip() and _pattern(term).search(fold(text)))


def _texts(term, news):
    return [term] + [entry.get("title", "") for entry in news if isinstance(entry, dict)]


def is_relevant(term, news, anchor_terms):
    """True when the keyword or a news title holds an anchor word as a whole word (case and accents ignored)."""
    return any(mentions(text, word) for text in _texts(term, news) for word in anchor_terms)


def context_labels(term, news, context_terms):
    """The context words an item mentions (labels only; they never decide relevance)."""
    texts = _texts(term, news)
    return tuple(word for word in context_terms if any(mentions(text, word) for text in texts))


def vn_date(epoch):
    return datetime.fromtimestamp(epoch, VN_TZ).date()


def _compact(text):
    return re.sub(r"[^a-z0-9]", "", fold(text))


def ratio_score(recent_avg, previous_avg):
    if recent_avg <= 0:
        return 0.0
    ratio = recent_avg / previous_avg if previous_avg > 0 else recent_avg + 1.0
    return min(1.0, ratio / RATIO_CAP)


@dataclass(frozen=True)
class TrendScore:
    term: str
    total: float
    hot: float
    d3: float
    d7: float
    tiktok: float
    traffic: int = 0
    labels: tuple = ()   # context words of the relevant trending items this term matched; never scored

    def as_dict(self):
        return {"term": self.term, "total": self.total, "hot": self.hot, "d3": self.d3, "d7": self.d7,
                "tiktok": self.tiktok, "traffic": self.traffic}


class TrendBoard:
    """Everything the score needs, read once from state: snapshots of the last 35 days and recent TikTok tags."""

    def __init__(self, state, trends, now):
        self.config, self.now, self.today = trends, now, vn_date(now)
        first = datetime.combine(self.today - timedelta(days=WINDOW_DAYS - 1), datetime.min.time(), tzinfo=VN_TZ)
        rows = state.trend_snapshots(first.timestamp())
        self.days = {}   # Vietnam date -> {(source, term): folded text of the term and its news titles}
        self.labels = {}  # folded trending term -> context words it mentions
        self.hot = {}    # folded trending term -> (term, max traffic) of relevant items of the last 24 h
        for row in rows:
            titles = " ".join(entry.get("title", "") for entry in row["news"] if isinstance(entry, dict))
            self.days.setdefault(vn_date(row["fetched_at"]), {})[(row["source"], row["term"])] = fold(
                row["term"] + " " + titles)
            if (row["source"] == GOOGLE_SOURCE and row["fetched_at"] >= now - 86400
                    and is_relevant(row["term"], row["news"], trends.anchor_terms)):
                key = fold(row["term"])
                known = self.hot.get(key, (row["term"], 0))
                self.hot[key] = (known[0], max(known[1], int(row["traffic"])))
                self.labels[key] = set(self.labels.get(key, ())) | set(
                    context_labels(row["term"], row["news"], trends.context_terms))
        self.tags = {}
        for tag in state.tiktok_tags(now - TIKTOK_DAYS * 86400):
            compact = _compact(tag["tag"])
            self.tags[compact] = max(self.tags.get(compact, 0), int(tag["views"]))

    def _days_between(self, first_back, last_back):
        return [self.today - timedelta(days=back) for back in range(last_back, first_back - 1, -1)]

    def frequency(self, term, first_back, last_back):
        """Average per-day count of distinct saved items mentioning `term`, for the days `first_back..last_back`
        before today (0 = today)."""
        pattern = _pattern(term)
        days = self._days_between(first_back, last_back)
        counts = [sum(1 for text in self.days.get(day, {}).values() if pattern.search(text)) for day in days]
        return sum(counts) / len(days)

    def _hot(self, term):
        best = 0
        matched = False
        pattern = _pattern(term)
        for folded, (_original, traffic) in self.hot.items():
            if pattern.search(folded):
                matched, best = True, max(best, traffic)
        if not matched:
            return 0.0, 0
        return HOT_BASE + (1 - HOT_BASE) * min(1.0, math.log10(max(best, 1)) / TRAFFIC_LOG_FULL), best

    def _tiktok(self, term):
        """A tag matches when its letters hold the term's letters (the tag is at least as specific as the term);
        a generic tag such as #nails does not lift every term that merely contains "nails"."""
        compact = _compact(term).removesuffix("s")
        views = [count for tag, count in self.tags.items()
                 if len(compact) >= MIN_TAG_LETTERS and compact in tag.removesuffix("s")]
        return min(1.0, math.log10(1 + max(views)) / TIKTOK_LOG_FULL) if views else 0.0

    def score(self, term):
        hot, traffic = self._hot(term)
        d3 = ratio_score(self.frequency(term, 0, 2), self.frequency(term, 3, 9))
        d7 = ratio_score(self.frequency(term, 0, 6), self.frequency(term, 7, 34))
        tiktok = self._tiktok(term)
        total = WEIGHTS["hot"] * hot + WEIGHTS["d3"] * d3 + WEIGHTS["d7"] * d7 + WEIGHTS["tiktok"] * tiktok
        pattern = _pattern(term)
        labels = sorted({label for key, found in self.labels.items() if pattern.search(key) for label in found})
        return TrendScore(term, round(total, 4), round(hot, 4), round(d3, 4), round(d7, 4), round(tiktok, 4), traffic,
                          tuple(labels))

    def known_terms(self):
        """Watched keywords plus the relevant Google trending terms of the last 24 h, without repeats."""
        terms = list(self.config.keywords)
        known = {fold(term) for term in terms}
        for folded, (original, _traffic) in self.hot.items():
            if folded not in known:
                terms.append(original)
        return terms

    def best_for(self, text):
        """The best score among the known terms that `text` mentions (zero score when none is mentioned)."""
        scores = [self.score(term) for term in self.known_terms() if mentions(text, term)]
        return max(scores, key=lambda found: found.total, default=TrendScore("", 0.0, 0.0, 0.0, 0.0, 0.0))

    def table(self, limit=8):
        """Today's relevant Google trending terms first (by score), then watched keywords that have a score."""
        rows = sorted((self.score(original) for original, _t in self.hot.values()),
                      key=lambda found: (-found.total, -found.traffic, found.term))
        shown = {fold(row.term) for row in rows}
        watched = sorted((self.score(term) for term in self.config.keywords if fold(term) not in shown),
                         key=lambda found: (-found.total, found.term))
        return (rows + [row for row in watched if row.total > 0])[:limit]
