"""Numeric grounding (BB-05): every number the article asserts must be backed by a claim whose quote
appears verbatim in the snippet of its source.

A number is a money amount (USD or VND), a percentage, an hours/duration figure, a date or a year. A plain count
such as "10 mẫu nail" is not a factual claim. A range ("20 đến 30 giờ", "3-5 ngày", "50 - 80 đô", "$50–$80") is
ONE assertion with two ends, and both ends need support. A number is grounded when some claim satisfies ALL of:
  1. claim.text occurs verbatim (whitespace-normalised) in the article and contains the number;
  2. claim.source_id names a supplied source;
  3. claim.quote occurs verbatim (whitespace-normalised) in that source's snippet, as one contiguous piece
     (no ellipses, at least 3 words), so separate sentences cannot be stitched together;
  4. the quote contains every figure of the number, a currency / percent marker for money / percentages, and the
     same UNIT for every figure of a duration or money amount (hours are not minutes, USD is not VND).
Image slot texts (`image_slot` brief, alt and caption; the legacy `image_alt` and `cover_alt`) are scanned like the body.
"""

import re

from .writer import article_texts

MIN_QUOTE_WORDS = 3
_NUM = r"\d+(?:[.,]\d+)?"
_RANGE = r"\s?(?:-|–|—|đến|tới|to)\s?"
_UNIT = r"(?:giờ|tiếng|phút|ngày|tuần|tháng|năm|hours?|hrs?|minutes?|mins?|days?|weeks?|months?|years?)"
_MONEY_SUFFIX = (r"(?:USD|usd|đô la|đô|dollars?|triệu(?:\s?đồng)?|nghìn(?:\s?đồng)?|ngàn(?:\s?đồng)?|tỷ(?:\s?đồng)?"
                 r"|VND|vnd|đồng|k|K|đ|₫)(?!\w)")
_DOLLAR = r"(?:US)?\$\s?\d[\d.,]*(?:\s?(?:[kKmM]\b|triệu|nghìn|ngàn|tỷ))?"
_MONEY_RANGE_DOLLAR = (r"(?:US)?\$\s?\d[\d.,]*[kKmM]?" + _RANGE
                       + r"(?:US)?\$?\s?\d[\d.,]*(?:\s?(?:[kKmM]\b|triệu|nghìn|ngàn|tỷ))?")
_MONEY_RANGE_SUFFIX = r"\d[\d.,]*" + _RANGE + r"\d[\d.,]*\s?" + _MONEY_SUFFIX
_MONEY_SINGLE_SUFFIX = r"\d[\d.,]*\s?" + _MONEY_SUFFIX
# Order matters: earlier patterns consume their span so a date is not also read as a duration, and a range is
# read as one assertion before its ends are read alone.
PATTERNS = (
    ("date", r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"),
    ("date", r"(?:ngày\s+)?\b\d{1,2}\s+tháng\s+\d{1,2}(?:\s*(?:,|năm)?\s*\d{4})?\b"),
    ("date", r"\b\d{1,2}/\d{1,2}\b"),
    ("date", r"\btháng\s+\d{1,2}\b"),
    ("money", _MONEY_RANGE_DOLLAR),
    ("money", _MONEY_RANGE_SUFFIX),
    ("duration", r"\b" + _NUM + _RANGE + _NUM + r"\s?" + _UNIT + r"\b"),
    ("money", _DOLLAR + "|" + _MONEY_SINGLE_SUFFIX),
    ("percent", r"\d+(?:[.,]\d+)?\s?%|\d+(?:[.,]\d+)?\s?(?:phần trăm|percent)\b"),
    ("duration", r"\b" + _NUM + r"\s?" + _UNIT + r"\b"),
    ("year", r"\b(?:19|20)\d{2}\b"),
)
_GROUP = re.compile(r"\d+(?:[.,]\d+)*")
_THOUSANDS = re.compile(r"\d{1,3}(?:[.,]\d{3})+")
_JOINED = re.compile(r"…|\.\.\.")
_PERCENT_MARK = re.compile(r"%|phần trăm|percent", re.IGNORECASE)
_USD_MARK = re.compile(r"\$|usd|đô|dollar", re.IGNORECASE)
_DURATION_CLASS = {"giờ": "hour", "tiếng": "hour", "hour": "hour", "hours": "hour", "hr": "hour", "hrs": "hour",
                   "phút": "minute", "minute": "minute", "minutes": "minute", "min": "minute", "mins": "minute",
                   "ngày": "day", "day": "day", "days": "day", "tuần": "week", "week": "week", "weeks": "week",
                   "tháng": "month", "month": "month", "months": "month", "năm": "year", "year": "year",
                   "years": "year"}
# (figure, unit) pairs read from a text: single "N unit" and both ends of "N-M unit".
_DURATION_SINGLE = re.compile(r"(?<![\d.,])(" + _NUM + r")\s?(" + _UNIT + r")\b", re.IGNORECASE)
_DURATION_RANGE = re.compile(r"(?<![\d.,])(" + _NUM + ")" + _RANGE + "(" + _NUM + r")\s?(" + _UNIT + r")\b",
                             re.IGNORECASE)
_MONEY_SYMBOL = re.compile(r"(?:US)?\$\s?(\d[\d.,]*)", re.IGNORECASE)
_MONEY_SYMBOL_RANGE = re.compile(r"(?:US)?\$\s?(\d[\d.,]*)[kKmM]?" + _RANGE + r"(?:US)?\$?\s?(\d[\d.,]*)",
                                 re.IGNORECASE)
_MONEY_SUFFIXED = re.compile(r"(?<![\d.,$])(\d[\d.,]*)\s?(" + _MONEY_SUFFIX + ")")
_MONEY_SUFFIXED_RANGE = re.compile(r"(?<![\d.,$])(\d[\d.,]*)" + _RANGE + r"(\d[\d.,]*)\s?(" + _MONEY_SUFFIX + ")")


def normalize(text):
    return " ".join(str(text).split())


def find_numbers(text):
    """[(kind, literal)] for each asserted number, left to right, without overlaps."""
    text = normalize(text)
    taken, found = [], []
    for kind, pattern in PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            span = match.span()
            if any(span[0] < end and start < span[1] for start, end in taken):
                continue
            taken.append(span)
            found.append((span[0], kind, match.group(0).strip().rstrip(".,")))
    return [(kind, literal) for _, kind, literal in sorted(found)]


def figures(text):
    """Digit groups of `text` with thousands separators removed and decimal commas unified."""
    result = set()
    for group in _GROUP.findall(text):
        group = group.rstrip(".,")
        result.add(re.sub(r"[.,]", "", group) if _THOUSANDS.fullmatch(group) else group.replace(",", "."))
    return result


def _figure(group):
    return next(iter(figures(group)), "")


def _currency(suffix):
    return "usd" if _USD_MARK.search(suffix) else "vnd"


def unit_pairs(kind, text):
    """{(figure, unit)} the text states: hour/day/... for a duration, usd/vnd for money. Both ends of a range."""
    text = normalize(text)
    pairs = set()
    if kind == "duration":
        for match in _DURATION_SINGLE.finditer(text):
            pairs.add((_figure(match.group(1)), _DURATION_CLASS[match.group(2).casefold()]))
        for match in _DURATION_RANGE.finditer(text):
            unit = _DURATION_CLASS[match.group(3).casefold()]
            pairs |= {(_figure(match.group(1)), unit), (_figure(match.group(2)), unit)}
    elif kind == "money":
        for match in _MONEY_SYMBOL.finditer(text):
            pairs.add((_figure(match.group(1)), "usd"))
        for match in _MONEY_SYMBOL_RANGE.finditer(text):
            pairs |= {(_figure(match.group(1)), "usd"), (_figure(match.group(2)), "usd")}
        for match in _MONEY_SUFFIXED.finditer(text):
            pairs.add((_figure(match.group(1)), _currency(match.group(2))))
        for match in _MONEY_SUFFIXED_RANGE.finditer(text):
            unit = _currency(match.group(3))
            pairs |= {(_figure(match.group(1)), unit), (_figure(match.group(2)), unit)}
    return pairs


def _snippets(sources):
    if isinstance(sources, dict):
        return {key: normalize(value) for key, value in sources.items()}
    return {item["id"]: normalize(item.get("snippet", "")) for item in sources}


def alt_texts(article):
    """Image slot texts (brief, alt, caption) also state facts; they are scanned like the body."""
    parts = []
    for index, section in enumerate(article.get("sections", [])):
        parts.append((f"sections[{index}].image_alt", section.get("image_alt", "")))
        slot = section.get("image_slot")
        if isinstance(slot, dict):
            parts += [(f"sections[{index}].image_slot.{name}", slot.get(name, "")) for name in ("brief", "alt", "caption")]
    parts.append(("cover_alt", article.get("cover_alt", "")))
    return [(path, text) for path, text in parts if isinstance(text, str) and text]


def _claim_problem(claim, kind, literal, snippets, whole):
    """None when `claim` grounds the number, else a short reason code."""
    if not isinstance(claim, dict) or not all(isinstance(claim.get(k), str) for k in ("text", "source_id", "quote")):
        return "malformed_claim"
    if claim["source_id"] not in snippets:
        return "unknown_source"
    text, quote = normalize(claim["text"]), normalize(claim["quote"])
    if text not in whole:
        return "claim_not_in_article"
    if _JOINED.search(quote) or len(quote.split()) < MIN_QUOTE_WORDS:
        return "quote_joined_or_too_short"
    if quote not in snippets[claim["source_id"]]:
        return "quote_not_in_source"
    if not figures(literal) <= figures(quote):
        return "quote_missing_number"
    if kind == "percent" and not _PERCENT_MARK.search(quote):
        return "quote_missing_percent"
    if kind in ("money", "duration"):
        have = unit_pairs(kind, quote)
        if kind == "money" and not have:
            return "quote_missing_currency"
        if not unit_pairs(kind, literal) <= have:
            return "quote_wrong_unit"
    return None


def check(article, sources):
    """Numbers without grounding: [{number, field, reason}]. Any entry means the article must be held."""
    snippets = _snippets(sources)
    claims = article.get("claims") if isinstance(article.get("claims"), list) else []
    texts = article_texts(article) + alt_texts(article)
    whole = normalize(" ".join(text for _, text in texts))
    ungrounded = []
    for path, text in texts:
        for kind, literal in find_numbers(text):
            reason, grounded = "no_claim", False
            for claim in claims:
                if not isinstance(claim, dict) or literal.casefold() not in normalize(claim.get("text", "")).casefold():
                    continue
                problem = _claim_problem(claim, kind, literal, snippets, whole)
                if problem is None:
                    grounded = True
                    break
                reason = problem
            if not grounded:
                ungrounded.append({"number": literal, "field": path, "reason": reason})
    return ungrounded
