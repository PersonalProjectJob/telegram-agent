"""Fakes whose call signatures match the real functions the code calls, plus article fixtures. No network."""
import json

SOURCE_TEXT = ("Merlot is the top shade this winter. Salons report that a gel manicure costs $50 on average "
               "and lasts about 3 weeks. Chocolate brown follows closely behind.")
SOURCE = {"id": "s1", "title": "Winter nail trends", "url": "https://www.bustle.com/winter-nails",
          "published": "2026-10-14T09:00:00+00:00", "snippet": SOURCE_TEXT}
SOURCE_2 = {"id": "s2", "title": "State board news", "url": "https://tdlr.texas.gov/news",
            "published": "2026-10-13T09:00:00+00:00", "snippet": "License renewal reminders are sent by mail."}


def ollama_envelope(data, prompt=100, completion=50):
    return {"message": {"role": "assistant", "content": json.dumps(data, ensure_ascii=False)},
            "prompt_eval_count": prompt, "eval_count": completion}


def deepseek_envelope(data, prompt=1000, completion=500):
    return {"choices": [{"message": {"content": json.dumps(data, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


def gemini_envelope(data, prompt=1000, candidates=500, thoughts=0, parts=None):
    """generateContent answer: candidates[0].content.parts[0].text and usageMetadata, as the real API returns them."""
    parts = parts if parts is not None else [{"text": json.dumps(data, ensure_ascii=False)}]
    usage = {"promptTokenCount": prompt, "candidatesTokenCount": candidates,
             "totalTokenCount": prompt + candidates + thoughts}
    if thoughts:
        usage["thoughtsTokenCount"] = thoughts
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP", "index": 0}],
            "usageMetadata": usage, "modelVersion": "fake"}


class FakeTransport:
    """Same signature as nailblog.ai_transport.request_json; `respond(call, number)` returns the envelope."""

    def __init__(self, respond):
        self.respond = respond
        self.calls = []

    def __call__(self, url, payload, headers, timeout, provider):
        self.calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout,
                           "provider": provider})
        return self.respond(self.calls[-1], len(self.calls))


def scripted(*envelopes):
    """Transport answering with the given envelopes in order."""
    queue = list(envelopes)
    return FakeTransport(lambda call, number: queue.pop(0))


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, amount=-1):
        return self.data if amount is None or amount < 0 else self.data[:amount]


class FakeOpener:
    """Same call shape as OpenerDirector.open / urlopen: (fullurl, data=None, timeout=...).

    `routes` maps URL -> bytes (served) or an Exception instance (raised)."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, fullurl, data=None, timeout=None):
        url = getattr(fullurl, "full_url", fullurl)
        method = fullurl.get_method() if hasattr(fullurl, "get_method") else "GET"
        self.calls.append({"url": url, "method": method, "timeout": timeout, "data": data})
        result = self.routes[url]
        if isinstance(result, BaseException):
            raise result
        return FakeResponse(result)


def read_key(path, scope, environ=None, group_name=None):
    """Same signature as nailblog.ai_transport.read_key."""
    return "test-key"


RSS = b"""<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel><title>Beauty</title>
<item><title>Merlot nails &amp; more</title><link>https://www.bustle.com/merlot</link>
<pubDate>Wed, 14 Oct 2026 09:00:00 GMT</pubDate>
<description>&lt;p&gt;Merlot is &lt;b&gt;hot&lt;/b&gt;.&lt;/p&gt;&lt;script&gt;alert(1)&lt;/script&gt;</description></item>
<item><title>No link here</title><description>skipped</description></item>
<item><title>Second</title><link>javascript:alert(1)</link></item>
<item><title>Chocolate</title><link>https://www.bustle.com/chocolate</link>
<content:encoded><![CDATA[<div>Chocolate brown   is back for winter.</div>]]></content:encoded></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Board</title>
<entry><title>Renewal notice</title><id>tag:example,2026:1</id>
<link rel="self" href="https://example.org/self"/><link rel="alternate" href="https://tdlr.texas.gov/renew"/>
<updated>2026-10-13T08:00:00Z</updated><summary type="html">Renew your &lt;em&gt;license&lt;/em&gt; on time.</summary></entry>
</feed>"""

BLOGS_JSON = json.dumps({"items": [
    {"id": "a1", "slug": "mang-lai-gi-cho-tiem", "categorySlug": "nail-salon", "publishedAt": "2026-10-10T08:00:00.1",
     "hashtags": ["nail", "tiem"], "translations": [{"languageCode": "En", "title": "Hi"},
                                                    {"languageCode": "Vi", "title": "Mang lại gì cho tiệm"}]},
    {"id": "a2", "slug": "bai-cu", "categorySlug": "hot-trend", "publishedAt": "2026-01-01T08:00:00", "hashtags": None,
     "translations": [{"languageCode": "Vi", "title": "Bài cũ hơn 90 ngày"}]},
    {"slug": "", "translations": []}, "garbage"]}).encode()


VOCAB = ("thợ tiệm khách lịch hẹn dụng cụ sạch màu sơn gel móng bền đẹp mùa đông nhanh gọn rõ ràng cẩn thận "
         "chọn giữ làm xếp ghi nhớ hỏi trả lời tư vấn kiểm tra").split()
KEYWORD = "màu nail merlot"
SECONDARY = ["merlot nails", "nail đỏ rượu", "gel merlot"]


def prose(count, seed=0, keyword=None, sentences=None):
    """`count` words in 2-4 sentences (about 20 words each); `keyword` replaces the first words."""
    words = [VOCAB[(seed * 7 + i * 3 + i // 5) % len(VOCAB)] for i in range(count)]
    if keyword:
        words[:len(keyword.split())] = keyword.split()
    number = sentences or max(2, min(4, -(-count // 20)))
    size, extra = divmod(count, number)
    parts, position = [], 0
    for i in range(number):
        take = size + (1 if i < extra else 0)
        parts.append(" ".join(words[position:position + take]))
        position += take
    return ". ".join(part[0].upper() + part[1:] for part in parts) + "."


def pad_to(text, low, high):
    while len(text) < low:
        text += " rõ ràng"
    assert len(text) <= high, (len(text), text)
    return text


TITLE_SEO = "Màu nail merlot mùa đông: cách chọn và giữ bền"
SUMMARY = pad_to("Hướng dẫn chọn màu nail merlot cho mùa đông, cách giữ gel bền và mẹo tư vấn khách cho thợ và chủ tiệm", 140, 160)
SLOT_BRIEF = "Cận cảnh bộ móng merlot trên nền sáng, ánh sáng tự nhiên"
SLOT_ALT = "Ảnh minh họa bộ móng merlot trên nền sáng"
RELATED_POSTS = [{"slug": "bai-cu-mot", "title": "Ghi chép tiệm nail số một", "categorySlug": "nail-salon"},
                 {"slug": "bai-cu-hai", "title": "Ghi chép tiệm nail số hai", "categorySlug": "nail-salon"}]


def claim(text="Một bộ gel thường có giá $50.", source_id="s1", quote="a gel manicure costs $50 on average"):
    return {"text": text, "source_id": source_id, "quote": quote}


def image_slot(**override):
    return {"brief": SLOT_BRIEF, "alt": SLOT_ALT, "caption": "", **override}


def _faq(count=3):
    return [{"q": f"Câu hỏi thường gặp số {chr(65 + i)} là gì?", "a": prose(45, 2 + i)} for i in range(count)]


def outline(sections=5, slots=(0, 2, 4), **override):
    """The outline JSON the model returns; every field follows content standard section 6 and passes the gate."""
    data = {
        "primary_keyword": KEYWORD, "secondary_keywords": list(SECONDARY),
        "title_seo": TITLE_SEO, "title": "Màu nail merlot mùa đông: hướng dẫn chọn và giữ bền cho thợ nail",
        "meta_description": SUMMARY, "summary": SUMMARY, "slug": "Màu nail merlot mùa đông",
        "intro": [prose(35, 1, KEYWORD), prose(35, 2)],
        "key_takeaways": [prose(12, 3 + i, sentences=1) for i in range(4)],
        "sections": [{"h2": (f"Cách chọn {KEYWORD} cho tiệm" if i == 0 else f"Mục số {chr(65 + i)} cho tiệm nail"),
                      "kind": "paragraphs", "points": [f"Ý chính của mục {i}", "Ý phụ"],
                      **({"image_slot": image_slot()} if i in slots else {})} for i in range(sections)],
        "faq": _faq(3), "cta": {"text": prose(40, 9, sentences=2), "target": "jobs"},
        "hashtags": ["nailmerlot", "mùa đông", "#thợ nail"], "sources_used": ["s1"], "claims": []}
    data.update(override)
    return data


def section(index=0, words=55, **override):
    """One section the model returns: two paragraphs (the first holds the keyword), optional parts left out."""
    data = {"paragraphs": [prose(words, index + 3, KEYWORD), prose(words, index + 4)], "claims": []}
    data.update(override)
    return data


def article_transport(sections=5, outline_override=None, section_override=None):
    """Ollama transport that answers an outline call, then one call per section."""
    def respond(call, number):
        if number == 1:
            return ollama_envelope(outline(sections, **(outline_override or {})))
        return ollama_envelope(section(number - 2, **((section_override or {}).get(number - 2, {}))))
    return FakeTransport(respond)


def with_words(article, total):
    """Make the body word count exactly `total`: pad the last intro paragraph, or trim paragraphs from the end.
    For length tests only: the padded article keeps no other standard."""
    from nailblog.writer import word_count
    article = {**article, "intro": list(article["intro"]),
               "sections": [{**s, "paragraphs": list(s["paragraphs"])} for s in article["sections"]]}
    diff = total - word_count(article)
    if diff >= 0:
        article["intro"][-1] += " từ" * diff
        return article
    for sec in reversed(article["sections"]):
        for index in reversed(range(len(sec["paragraphs"]))):
            words = sec["paragraphs"][index].split()
            cut = min(-diff, max(0, len(words) - 8))
            sec["paragraphs"][index] = " ".join(words[:len(words) - cut])
            diff += cut
            if diff == 0:
                return article
    raise AssertionError(("cannot shrink to", total, word_count(article)))


def make_article(sections=5, slots=(0, 2, 4), words=900, category="nail-salon", **override):
    """An article that meets every content standard rule, assembled like writer.write_article.

    The paragraphs are sized so the body has exactly `words` words (810..1190 stay inside the paragraph rules)."""
    from nailblog.writer import build_links, fit_fields, slugify_vi, word_count
    planned = outline(sections, slots)
    cta = planned["cta"]

    def build(lengths):
        built = [{"h2": s["h2"], "kind": s["kind"],
                  "paragraphs": [prose(lengths[2 * i], i + 3, KEYWORD), prose(lengths[2 * i + 1], i + 4)],
                  "bullets": [], "steps": [], "h3": [], "image_slot": s.get("image_slot")}
                 for i, s in enumerate(planned["sections"])]
        return {
            "topic": "Màu nail merlot", "category": category, "product_promo": False,
            "primary_keyword": KEYWORD, "secondary_keywords": list(SECONDARY),
            "title_seo": planned["title_seo"], "title": planned["title"], "meta_description": planned["meta_description"],
            "summary": planned["summary"], "intro": planned["intro"], "key_takeaways": planned["key_takeaways"],
            "sections": built, "faq": planned["faq"], "cta": cta,
            "links": build_links("https://nailhub.ai", category,
                                 [{**post, "categorySlug": category} for post in RELATED_POSTS], cta, None, built),
            "hashtags": ["nailmerlot", "muadong", "thonail"], "slug": slugify_vi(planned["slug"]),
            "claims": [], "source_ids": ["s1"], "sources_used": ["s1"], "sources": [], "sources_updated": ""}

    lengths = [50] * (2 * sections)
    diff = words - word_count(build(lengths))
    lengths = [n + diff // len(lengths) + (1 if i < diff % len(lengths) else 0) for i, n in enumerate(lengths)]
    article = fit_fields(build(lengths))
    article.update(override)
    return article


TRENDS_URL = "https://trends.google.com/trending/rss?geo=US"


def trending_feed(items):
    """Google Trending RSS in the shape observed on 2026-10-16 (xmlns:ht, ht:approx_traffic, ht:news_item*).

    items: (keyword, approx_traffic, [(news title, news url, news source)])."""
    entries = []
    for term, traffic, news in items:
        news_xml = "".join(
            "<ht:news_item><ht:news_item_title>" + title + "</ht:news_item_title>"
            "<ht:news_item_snippet>snippet</ht:news_item_snippet><ht:news_item_url>" + url + "</ht:news_item_url>"
            "<ht:news_item_source>" + source + "</ht:news_item_source></ht:news_item>" for title, url, source in news)
        entries.append(
            "<item><title>" + term + "</title><ht:approx_traffic>" + traffic + "</ht:approx_traffic>"
            "<description></description><link>https://trends.google.com/trending/rss?geo=US</link>"
            "<pubDate>Fri, 16 Oct 2026 02:00:00 -0700</pubDate>"
            "<ht:picture>https://t0.gstatic.com/p.jpg</ht:picture><ht:picture_source>Source</ht:picture_source>"
            + news_xml + "</item>")
    return ('<?xml version="1.0" encoding="UTF-8"?><rss xmlns:atom="http://www.w3.org/2005/Atom" '
            'xmlns:ht="https://trends.google.com/trending/rss" version="2.0"><channel><title>Daily Search Trends</title>'
            '<description>Recent searches</description><link>https://trends.google.com/trending?geo=US</link>'
            + "".join(entries) + "</channel></rss>").encode("utf-8")


TRENDING_RSS = trending_feed([
    ("chrome nails", "20000+", [("Chrome nails are the fall look", "https://www.allure.com/chrome-nails", "Allure"),
                                ("How to do chrome powder at home", "https://www.byrdie.com/chrome", "Byrdie")]),
    ("taylor swift", "2000000+", [("Her manicure goes viral overnight", "https://www.vogue.com/swift-manicure", "Vogue")]),
    ("nfl scores", "500000+", [("Sunday scores and standings", "https://www.espn.com/nfl", "ESPN")]),
    ("sephora sale", "100000+", [("Best beauty deals this week", "https://www.cnet.com/beauty", "CNET")]),
    ("halloween costumes", "50000+", [("Costume ideas for adults", "https://www.nytimes.com/costumes", "NYT")]),
])
