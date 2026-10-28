"""Quality gate (Master 4.7, BB-01..12 and content standard section 7) returning structured issues.

`fixable` issues (length, titles, summary, SEO placement, paragraph shape) can be repaired by rewriting one field or
one section; every other issue is a `hold`. Step B checks image slots and their brief/alt declarations only; image
files are checked in step C. Words are counted by whitespace; sentences are split on `.`, `!` and `?` followed by a
space, so decimal numbers stay whole; keyword matching ignores accents and case.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlparse

from .config import SITE_ORIGIN
from .planner import JACCARD_LIMIT, jaccard, recent_posts, title_tokens
from .schedule import VN_TZ
from .writer import (BODY_PREFIXES, CLICHE_NOT_ONLY, CLICHE_PHRASES, article_texts, cta_text, layout, needs_sources,
                     slot_of, strip_accents, word_count, word_range)

CATEGORIES = ("hot-trend", "nail-job", "nail-salon", "did-you-know")
H2_RANGE = (4, 6)
IMAGE_SLOTS = (3, 5)
HASHTAGS = (3, 5)
ALT_WORDS = (5, 15)
# D-034 bổ sung 28/10: nới ngưỡng + 5%. Owner: every continuous limit widened by 5% (max x 1.05 rounded down, min x 0.95
# rounded up) for ALL posts, and the measured limits below set by the Owner. Small counts (H2, slots, hashtags, takeaway
# lines, FAQ items, 2-4 sentences, 3 paragraphs in a row) are unchanged. Old value -> new value is in the comments.
INTRO_WORDS = (48, 94)             # was (50, 90)
TAKEAWAYS = (3, 4)
TAKEAWAY_MAX_WORDS = 21            # was 20
PARAGRAPH_WORDS = (35, 95)         # was (40, 90)
PARAGRAPH_LONG_MAX = 115          # was 110: at most one paragraph per article may be longer than the max above
PARAGRAPH_SENTENCES = (2, 4)
SENTENCE_AVERAGE, SENTENCE_MAX = 22, 36   # was 20, 35
MAX_PARAGRAPH_RUN = 3
FAQ_ITEMS = (3, 5)
FAQ_ANSWER_WORDS = (29, 63)        # was (30, 60)
DENSITY_LOW, DENSITY_HIGH = 0.3, 2.625   # was 0.5, 2.5 (percent of the words; above DENSITY_HIGH is keyword stuffing)
KEYWORD_INTRO_WORDS = 105          # was 100
TITLE_CASE_RATIO = 0.63            # was 0.6
TITLE_SEO_MAX, TITLE_MAX = 63, 94  # was 60, 90 (characters; meta_title shares TITLE_SEO_MAX)
META_CHARS = (133, 168)            # was (140, 160): metaDescription and summary
MAX_INTERNAL_LINKS = 3
SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
FIXABLE = "fixable"
HOLD = "hold"


@dataclass(frozen=True)
class Issue:
    code: str
    kind: str
    field: str
    message: str

    def as_dict(self):
        return {"code": self.code, "kind": self.kind, "field": self.field, "message": self.message}


@dataclass(frozen=True)
class QualityResult:
    issues: tuple = ()

    @property
    def ok(self):
        return not self.issues

    @property
    def fixable(self):
        return tuple(issue for issue in self.issues if issue.kind == FIXABLE)

    @property
    def hold(self):
        return tuple(issue for issue in self.issues if issue.kind == HOLD)

    @property
    def verdict(self):
        return "hold" if self.hold else "fixable" if self.fixable else "pass"

    def as_dict(self):
        return {"verdict": self.verdict, "issues": [issue.as_dict() for issue in self.issues]}


def _day_and_instant(slot_at):
    moment = datetime.fromisoformat(slot_at)
    if moment.tzinfo is None:
        raise ValueError("slot without timezone")
    return moment.astimezone(VN_TZ).date(), moment.timestamp()


def _valid_slug(slug):
    return bool(slug) and len(slug) <= 60 and bool(SLUG.fullmatch(slug))


def words_of(text):
    return len(str(text).split())


def sentences_of(text):
    return [part for part in re.split(r"(?<=[.!?])\s+", str(text).strip()) if part]


def fold(text):
    return strip_accents(unicodedata.normalize("NFC", str(text))).casefold()


def _keyword_count(text, keyword):
    return len(re.findall(r"(?<!\w)" + re.escape(fold(keyword)) + r"(?!\w)", fold(text)))


def capital_ratio(text):
    """(share of words that start with a capital letter, number of words that start with a letter)."""
    letters = [word.lstrip("\"'“‘([") for word in str(text).split()]
    letters = [word for word in letters if word and word[0].isalpha()]
    return (sum(1 for word in letters if word[0].isupper()) / len(letters) if letters else 0.0), len(letters)


def _paragraphs(article):
    """(path, text) of the prose paragraphs of the sections, H3 blocks included."""
    found = []
    for index, section in enumerate(article.get("sections", [])):
        for i, text in enumerate(section.get("paragraphs", []) or []):
            found.append((f"sections[{index}].paragraphs[{i}]", text))
        for k, sub in enumerate(section.get("h3", []) or []):
            for i, text in enumerate(sub.get("paragraphs", []) if isinstance(sub, dict) else []):
                found.append((f"sections[{index}].h3[{k}].paragraphs[{i}]", text))
    return [(path, text) for path, text in found if isinstance(text, str)]


def _intro(article):
    intro = article.get("intro", [])
    return [intro] if isinstance(intro, str) else [text for text in intro if isinstance(text, str)]


def _headings(article):
    """(path, text) of every heading the reader sees besides the fixed ones: titles, H2, H3 and FAQ questions."""
    found = [("title", article.get("title", "")), ("title_seo", article.get("title_seo", ""))]
    for index, section in enumerate(article.get("sections", [])):
        found.append((f"sections[{index}].h2", section.get("h2", "")))
        for k, sub in enumerate(section.get("h3", []) or []):
            if isinstance(sub, dict):
                found.append((f"sections[{index}].h3[{k}].title", sub.get("title", "")))
    found += [(f"faq[{index}].q", item.get("q", "")) for index, item in enumerate(article.get("faq", []))]
    return [(path, text) for path, text in found if isinstance(text, str) and text.strip()]


def gate(article, *, category, slot_at=None, product_promo=False, existing_slugs=None, others=(),
         ungrounded=(), site_origin=SITE_ORIGIN, posts=None, today=None, content=None):
    """Check one article. `existing_slugs` None means the published list was not available (held, never assumed).

    `others`: dicts {slot_at, product_promo} of the other posts that already occupy slots.
    `ungrounded`: facts.check() output.
    `posts`: the published list ({title, publishedAt, ...}); None skips the title check. A title (or title_seo)
    whose accent-free word set matches a post of the last 90 days with Jaccard >= 0.6 is a fixable `title_similar`.
    `content`: the rendered HTML when known; an `<h1>` in it is a fixable `h1_in_content`."""
    issues = []

    def add(code, kind, field, message):
        issues.append(Issue(code, kind, field, message))

    if category not in CATEGORIES:
        add("category", HOLD, "category", "danh mục không hợp lệ")
    low, high = word_range(category)
    low, high = -(-low * 95 // 100), high * 105 // 100   # D-034 bổ sung 28/10: -5% / +5% on the body word range
    words = word_count(article)
    if not low <= words <= high:
        add("words", FIXABLE, "content", f"{words} từ, cần {low}-{high}")
    sections = article.get("sections", [])
    if not H2_RANGE[0] <= len(sections) <= H2_RANGE[1]:
        add("h2_count", HOLD, "sections", f"{len(sections)} mục H2, cần {H2_RANGE[0]}-{H2_RANGE[1]}")
    for field, label, limit in (("title_seo", "title_seo", TITLE_SEO_MAX), ("title", "title", TITLE_MAX),
                                 ("meta_title", "metaTitle", TITLE_SEO_MAX)):
        value = str(article.get(field, "")).strip()
        if field == "meta_title" and value == str(article.get("title_seo", "")).strip():
            continue   # a metaTitle copied from title_seo is judged once, as title_seo
        if not value or len(value) > limit:
            add(field, FIXABLE, field, f"{label} {len(value)} ký tự, cần 1-{limit}")
    for field, label in (("meta_description", "metaDescription"), ("summary", "summary")):
        value = str(article.get(field, "")).strip()
        if not META_CHARS[0] <= len(value) <= META_CHARS[1]:
            add(field, FIXABLE, field, f"{label} {len(value)} ký tự, cần {META_CHARS[0]}-{META_CHARS[1]}")
    if posts is not None:
        day = today or datetime.now(VN_TZ).date()
        recent = [title_tokens(post.get("title", "")) for post in recent_posts(posts, day)]
        for field in ("title", "title_seo"):
            tokens = title_tokens(str(article.get(field, "")))
            if tokens and any(jaccard(tokens, other) >= JACCARD_LIMIT for other in recent):
                add("title_similar", FIXABLE, field, f"{field} giống một bài đã đăng trong 90 ngày")
    slug = str(article.get("slug", ""))
    if not _valid_slug(slug):
        add("slug", HOLD, "slug", "slug phải là [a-z0-9-], tối đa 60 ký tự, không gạch đầu/cuối")
    elif existing_slugs is None:
        add("slug_unverified", HOLD, "slug", "chưa có danh sách bài đã đăng để kiểm slug trùng")
    elif slug in set(existing_slugs):
        add("slug_duplicate", HOLD, "slug", "slug trùng bài đã có")
    tags = article.get("hashtags", [])
    if not HASHTAGS[0] <= len(tags) <= HASHTAGS[1] or any(
            not isinstance(tag, str) or not tag or len(tag) > 50 for tag in tags):
        add("hashtags", HOLD, "hashtags", f"{len(tags)} hashtag, cần {HASHTAGS[0]}-{HASHTAGS[1]}, mỗi cái 1-50 ký tự")
    _keywords(article, add)
    _structure(article, add)
    _prose(article, add)
    slotted = [(index, slot_of(section)) for index, section in enumerate(sections) if slot_of(section) is not None]
    if not IMAGE_SLOTS[0] <= len(slotted) <= IMAGE_SLOTS[1]:
        add("image_slots", HOLD, "sections", f"{len(slotted)} vị trí ảnh thân bài, cần {IMAGE_SLOTS[0]}-{IMAGE_SLOTS[1]}")
    for index, slot in slotted:
        section = sections[index]
        alt = str(slot.get("alt") or section.get("image_alt") or "")
        if not ALT_WORDS[0] <= words_of(alt) <= ALT_WORDS[1]:
            add("image_alt", HOLD, f"sections[{index}].image_slot.alt", f"alt cần {ALT_WORDS[0]}-{ALT_WORDS[1]} từ")
        if slot and not str(slot.get("brief", "")).strip():
            add("image_brief", HOLD, f"sections[{index}].image_slot.brief", "vị trí ảnh thiếu mô tả (brief)")
    if any(slot_of(first) is not None and slot_of(second) is not None for first, second in zip(sections, sections[1:])):
        add("figure_adjacent", FIXABLE, "sections", "hai mục liên tiếp đều có ảnh, ảnh nằm sát nhau")
    if not cta_text(article).strip():
        add("cta", HOLD, "cta", "thiếu CTA cuối bài")
    _links_and_sources(article, add, site_origin)
    if slot_at is not None:
        try:
            day, instant = _day_and_instant(slot_at)
        except (TypeError, ValueError):
            add("slot", HOLD, "slot_at", "slot không phải ISO datetime có múi giờ")
        else:
            for other in others:
                try:
                    other_day, other_instant = _day_and_instant(other["slot_at"])
                except (KeyError, TypeError, ValueError):
                    continue
                if other_instant == instant:
                    add("slot_taken", HOLD, "slot_at", "slot đã có bài khác")
                elif product_promo and other.get("product_promo") and other_day == day:
                    add("product_promo", HOLD, "product_promo", "đã có bài giới thiệu sản phẩm trong ngày")
    if (isinstance(content, str) and re.search(r"<h1\b", content, re.IGNORECASE)) or any(
            re.search(r"<h1\b", text, re.IGNORECASE) for _path, text in article_texts(article)):
        add("h1_in_content", FIXABLE, "content", "nội dung có <h1>, tiêu đề bài là H1 do website hiển thị")
    for entry in ungrounded:
        add("facts", HOLD, entry.get("field", ""), f"số {entry.get('number')} chưa có căn cứ ({entry.get('reason')})")
    return QualityResult(tuple(issues))


def _keywords(article, add):
    """kw_title, kw_meta, kw_slug, kw_intro, kw_h2, kw_density (primary keyword, accent- and case-free)."""
    keyword = " ".join(str(article.get("primary_keyword", "")).split())
    if not keyword:
        add("primary_keyword", FIXABLE, "primary_keyword", "thiếu từ khóa chính")
        return
    secondary = [str(word) for word in article.get("secondary_keywords", []) if isinstance(word, str) and word.strip()]
    if not _keyword_count(article.get("title_seo", ""), keyword):
        add("kw_title", FIXABLE, "title_seo", f"metaTitle thiếu từ khóa chính '{keyword}'")
    if not _keyword_count(article.get("meta_description", ""), keyword):
        add("kw_meta", FIXABLE, "meta_description", f"meta description thiếu từ khóa chính '{keyword}'")
    slug = str(article.get("slug", ""))
    wanted = set(re.findall(r"[a-z0-9]+", fold(keyword)))
    if _valid_slug(slug) and not wanted <= set(slug.split("-")):   # an invalid slug is held already
        add("kw_slug", FIXABLE, "slug", f"slug thiếu từ khóa chính '{keyword}'")
    first = " ".join(" ".join(_intro(article)).split()[:KEYWORD_INTRO_WORDS])
    if not _keyword_count(first, keyword):
        add("kw_intro", FIXABLE, "intro", f"{KEYWORD_INTRO_WORDS} từ đầu thiếu từ khóa chính '{keyword}'")
    h2s = [section.get("h2", "") for section in article.get("sections", [])]
    if not any(_keyword_count(h2, word) for h2 in h2s for word in [keyword] + secondary):
        add("kw_h2", FIXABLE, "sections", "không H2 nào chứa từ khóa chính hoặc từ khóa phụ")
    body = " ".join(text for path, text in article_texts(article) if path.startswith(BODY_PREFIXES))
    total = max(1, word_count(article))
    density = _keyword_count(body, keyword) * 100 / total
    if density < DENSITY_LOW:
        add("kw_density", FIXABLE, "content", f"từ khóa chính chiếm {density:.2f}% số từ, cần ít nhất {DENSITY_LOW}%")
    elif density > DENSITY_HIGH:
        add("kw_density", FIXABLE, "content", f"từ khóa chính chiếm {density:.2f}% số từ, nhồi từ khóa (trên {DENSITY_HIGH}%)")


def _structure(article, add):
    """intro_len, takeaways, faq, title_case, wall_of_text."""
    intro = _intro(article)
    count = sum(words_of(text) for text in intro)
    if len(intro) != 2 or not INTRO_WORDS[0] <= count <= INTRO_WORDS[1]:
        add("intro_len", FIXABLE, "intro",
            f"mở bài {len(intro)} đoạn, {count} từ, cần 2 đoạn và {INTRO_WORDS[0]}-{INTRO_WORDS[1]} từ")
    lines = article.get("key_takeaways", [])
    lines = [text for text in lines if isinstance(text, str)] if isinstance(lines, list) else []
    long = [text for text in lines if words_of(text) > TAKEAWAY_MAX_WORDS]
    if not TAKEAWAYS[0] <= len(lines) <= TAKEAWAYS[1] or long:
        add("takeaways", FIXABLE, "key_takeaways",
            f"{len(lines)} dòng tóm tắt, cần {TAKEAWAYS[0]}-{TAKEAWAYS[1]} dòng, mỗi dòng tối đa {TAKEAWAY_MAX_WORDS} từ"
            + (f" ({len(long)} dòng dài hơn)" if long else ""))
    faq = [item for item in article.get("faq", []) if isinstance(item, dict)]
    bad = [index for index, item in enumerate(faq)
           if not FAQ_ANSWER_WORDS[0] <= words_of(item.get("a", "")) <= FAQ_ANSWER_WORDS[1]]
    if not FAQ_ITEMS[0] <= len(faq) <= FAQ_ITEMS[1] or bad:
        add("faq", FIXABLE, "faq" if not bad else f"faq[{bad[0]}].a",
            f"{len(faq)} câu FAQ, cần {FAQ_ITEMS[0]}-{FAQ_ITEMS[1]} câu, mỗi câu trả lời {FAQ_ANSWER_WORDS[0]}-"
            f"{FAQ_ANSWER_WORDS[1]} từ" + (f" ({len(bad)} câu sai độ dài)" if bad else ""))
    for path, text in _headings(article):
        ratio, size = capital_ratio(text)
        if size >= 3 and ratio >= TITLE_CASE_RATIO:
            add("title_case", FIXABLE, path, f"viết hoa chữ đầu mỗi từ ({ratio:.0%}), cần viết hoa chữ đầu câu")
            break
    run = longest = 0
    for kind, _data in layout(article):
        run = run + 1 if kind == "p" else 0
        longest = max(longest, run)
    if longest > MAX_PARAGRAPH_RUN:
        add("wall_of_text", FIXABLE, "content", f"{longest} đoạn văn liền nhau không có ngắt, tối đa {MAX_PARAGRAPH_RUN}")


def _prose(article, add):
    """para_len, sentence_len, cliche."""
    paragraphs = _paragraphs(article)
    low, high = PARAGRAPH_WORDS
    problems, long_ones = [], []
    for path, text in paragraphs:
        count, sentences = words_of(text), len(sentences_of(text))
        if count > PARAGRAPH_LONG_MAX or count < low:
            problems.append((path, f"{count} từ"))
        elif count > high:
            long_ones.append((path, f"{count} từ"))
        if not PARAGRAPH_SENTENCES[0] <= sentences <= PARAGRAPH_SENTENCES[1]:
            problems.append((path, f"{sentences} câu"))
    if len(long_ones) > 1:
        problems += long_ones[1:]
    if problems:
        add("para_len", FIXABLE, problems[0][0],
            f"{len(problems)} đoạn ngoài chuẩn (đầu tiên: {problems[0][1]}); cần {low}-{high} từ "
            f"(tối đa 1 đoạn tới {PARAGRAPH_LONG_MAX}) và {PARAGRAPH_SENTENCES[0]}-{PARAGRAPH_SENTENCES[1]} câu")
    prose = _intro(article) + [text for _path, text in paragraphs] + [
        item.get("a", "") for item in article.get("faq", []) if isinstance(item, dict)] + [cta_text(article)]
    lengths = [words_of(sentence) for text in prose for sentence in sentences_of(text)]
    if lengths:
        average, longest = sum(lengths) / len(lengths), max(lengths)
        if average > SENTENCE_AVERAGE or longest > SENTENCE_MAX:
            add("sentence_len", FIXABLE, "content",
                f"câu trung bình {average:.1f} từ (tối đa {SENTENCE_AVERAGE}), câu dài nhất {longest} từ "
                f"(tối đa {SENTENCE_MAX})")
    found = []
    for path, text in article_texts(article):
        folded = unicodedata.normalize("NFC", text).casefold()
        found += [(path, phrase) for phrase in CLICHE_PHRASES if re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", folded)]
        if CLICHE_NOT_ONLY.search(folded):
            found.append((path, "không chỉ… mà còn"))
    if found:
        add("cliche", FIXABLE, found[0][0], "cụm sáo rỗng bị cấm: " + ", ".join(sorted({phrase for _p, phrase in found})))


def _links_and_sources(article, add, site_origin):
    host = urlparse(site_origin).hostname
    urls = {str(link.get("url", "")) for link in article.get("links", []) if isinstance(link, dict)
            and urlparse(str(link.get("url", ""))).scheme == "https" and urlparse(str(link.get("url", ""))).hostname == host}
    if not urls:
        add("internal_links", HOLD, "links", f"0 link nội bộ {host}, cần 2-{MAX_INTERNAL_LINKS}")
    elif len(urls) == 1:
        add("internal_links", FIXABLE, "links", f"1 link nội bộ {host}, cần 2-{MAX_INTERNAL_LINKS}")
    elif len(urls) > MAX_INTERNAL_LINKS:
        add("internal_links", FIXABLE, "links", f"{len(urls)} link nội bộ {host}, tối đa {MAX_INTERNAL_LINKS}")
    sources = [item for item in article.get("sources", []) if isinstance(item, dict)
               and str(item.get("url", "")).startswith("https://") and urlparse(str(item["url"])).hostname]
    if needs_sources(article) and not sources:
        add("sources_section", HOLD, "sources", "bài có số liệu luật, thuế, phí hoặc giờ học nhưng thiếu mục Nguồn tham khảo")
