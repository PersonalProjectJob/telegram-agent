"""Shared fixtures of the daily-flow tests: scripted model, routes, a Service wired to fakes. No network, no Ollama."""
import json
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock

from nailblog.schedule import VN_TZ, parse_hhmm
from nailblog.service import Service
from nailblog.sources import item_id
from nailblog.text import TextModel
from tests import fakes
from tests.fakes import ATOM, BLOGS_JSON, RSS, TRENDING_RSS, TRENDS_URL, FakeOpener, FakeTransport, ollama_envelope
from tests.helpers import load

FEED_1, FEED_2 = "https://www.bustle.com/rss", "https://tdlr.texas.gov/feed"
BLOGS_URL = "https://api.nailhub.ai/api/v1/blogs?pageNumber=1&pageSize=50"
SOURCES = f'feeds = ["{FEED_1}", "{FEED_2}"]'
TODAY = date(2026, 10, 16)       # Friday
TARGET = date(2026, 10, 17)      # Saturday: 06:00 prefers hot-trend, 19:00 prefers nail-salon
WEEK = "daily:2026-10-17"
SLOT_06, SLOT_19 = "2026-10-17T06:00+07:00", "2026-10-17T19:00+07:00"
ITEM_IDS = [item_id(url) for url in ("https://www.bustle.com/merlot", "https://www.bustle.com/chocolate",
                                     "https://tdlr.texas.gov/renew")]


def at(hhmm, day=TODAY):
    return datetime.combine(day, parse_hhmm(hhmm), tzinfo=VN_TZ).timestamp()


def candidate(title, category, *, keyword="", promo=False, sources=()):
    return {"title": title, "category": category, "product_promo": promo, "keyword": keyword,
            "source_ids": list(sources)}


def default_candidates():
    return [
        candidate("Chrome nails mùa lễ hội cho khách nữ", "hot-trend", keyword="chrome nails", sources=[ITEM_IDS[0]]),
        candidate("Cat eye nails nhẹ nhàng cho ngày đông", "hot-trend", keyword="cat eye nails"),
        candidate("Lộ trình học nghề nail cho người mới", "nail-job"),
        candidate("Cách xin việc thợ nail tại Texas", "nail-job", sources=[ITEM_IDS[2]]),
        candidate("Quản lý lịch hẹn tiệm nail đông khách", "nail-salon", sources=[ITEM_IDS[2]]),
        candidate("Mẹo giữ chân thợ nail giỏi", "nail-salon", promo=True),
        candidate("Bạn có biết gel bền được bao lâu", "did-you-know"),
        candidate("Chọn dụng cụ khử trùng nào cho tiệm", "did-you-know"),
        candidate("Mang lại gì cho tiệm", "nail-salon"),                                   # repeats a published post
        candidate("Tin lạ không có nguồn nào", "hot-trend", sources=["zzzzzzzzzzzzzzzz"]),  # unknown source id
        candidate("Giới thiệu NEXORA TOUCH cho tiệm nhỏ", "nail-salon", promo=True),        # a second product post
    ]


class Model:
    """Scripted Ollama transport: answers by the schema the call asks for."""

    def __init__(self, candidates=None):
        self.candidates = default_candidates() if candidates is None else candidates
        self.fail_articles = False
        self.hold_article = None      # 1-based article number whose first section states an unsupported price
        self.planner_calls = self.outlines = self.sections = 0
        self.transport = FakeTransport(self.respond)

    def respond(self, call, number):
        props = call["payload"]["format"]["properties"]
        if "candidates" in props:
            self.planner_calls += 1
            return ollama_envelope({"candidates": self.candidates})
        if "sections" in props:
            self.outlines += 1
            self.section_index = 0
            if self.fail_articles:
                raise OSError("model down")
            return ollama_envelope(fakes.outline(5))
        if "paragraphs" in props:
            self.sections += 1
            self.section_index += 1
            if self.hold_article == self.outlines and self.section_index == 1:
                return ollama_envelope(fakes.section(0, paragraphs=["Một bộ gel thường có giá $50. " + fakes.prose(40),
                                                                    fakes.prose(40)]))
            return ollama_envelope(fakes.section(self.section_index))
        raise AssertionError("unexpected model call: " + ",".join(props))


class DailyBase(unittest.IsolatedAsyncioTestCase):
    SOURCES = SOURCES

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = load(self.directory.name, sources=self.SOURCES)
        self.now = at("10:05")
        self.service = Service(self.config, clock=lambda: self.now)
        self.state = self.service.state
        self.opener = self.service.opener = FakeOpener({FEED_1: RSS, FEED_2: ATOM, BLOGS_URL: BLOGS_JSON,
                                                         TRENDS_URL: TRENDING_RSS})
        self.model = Model()
        self.service.text = TextModel(self.config, self.service.model_call, transport=self.model.transport)
        self.sent = self.service.notice_send = AsyncMock()

    def rows(self, slot=None):
        return [row for row in self.state.topics_for(WEEK) if slot is None or row["slot_at"] == slot]

    def posts(self):
        return self.state.list_posts()


def meta(row):
    return json.loads(row["sources_json"])
