"""Deterministic source evidence and fact checks. Model suggestions grant no authority."""

import hashlib
import json
import re
import unicodedata
from urllib.parse import parse_qs, urlparse
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from datetime import datetime, timezone

SENTINEL_START_DATE = '0001-01-01T00:00:00'


def processing_start_date():
    # Backend validates against DateTime.UtcNow.Date; its DTO uses offset-free ISO.
    return datetime.now(timezone.utc).date().isoformat() + 'T00:00:00'


class NeedsHuman(ValueError):
    """A proposal cannot be established from source evidence."""
    def __init__(self, message, *, details=None):
        super().__init__(message)
        self.details = details


class EvidenceError(NeedsHuman):
    """Only invalid extraction evidence may receive one bounded model correction."""
    def __init__(self, message, *, path='evidence', details=None):
        super().__init__(message, details=details)
        self.path = path


def folded(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold())
                   if unicodedata.category(c) != 'Mn').replace('đ', 'd')


@dataclass(frozen=True)
class Posting:
    id: str
    status: str
    category: str
    content: str
    fields: dict = field(default_factory=dict)

    def snapshot(self):
        return {"id": self.id, "status": self.status, "category": self.category,
                "content": self.content, "fields": self.fields}

    @property
    def fingerprint(self):
        return hashlib.sha256(json.dumps(self.snapshot(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def evidence(original, span, *, maximum=500):
    if isinstance(span, dict) and set(span) == {'quote'}:
        quote = span['quote']
        if not isinstance(quote, str) or not 0 < len(quote) <= maximum or original.count(quote) != 1:
            raise EvidenceError('evidence quote must be exact, bounded and unique in original')
        return quote
    if not isinstance(span, dict) or set(span) != {'start', 'end'}:
        raise EvidenceError('invalid evidence span')
    start, end = span['start'], span['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(original) or end-start > maximum:
        raise EvidenceError('evidence outside original or too long')
    return original[start:end]


NUMBER = r'\d+(?:[.,]\d+)*'
AMOUNT = re.compile(rf'\s*\$?\s*({NUMBER})\s*([kK]?)\s*(?:[-–—]\s*\$?\s*({NUMBER})\s*([kK]?))?\s*')
RANGE_LITERAL = re.compile(rf'(?<![\d.,])\$?\s*{NUMBER}\s*[kK]?\s*'
                           rf'(?:[-–—]|tới|đến|toi|den|to)\s*\$?\s*{NUMBER}\s*[kK]?\+?', re.I)
MONEY = re.compile(rf'\$\s*{NUMBER}(?:\s*[kK])?|(?<!\w){NUMBER}\s*[kK](?!\w)')
PERIODS = {
    'week': r'(?:/\s*week\b|\b(?:per|a|each)\s+week\b|\bweekly\b|/\s*tuan\b|\b(?:moi|mot)\s+tuan\b|每周)',
    'month': r'(?:/\s*month\b|\b(?:per|a|each)\s+month\b|\bmonthly\b|/\s*thang\b|\b(?:moi|mot)\s+thang\b|每月)',
    'year': r'(?:/\s*year\b|\b(?:per|a|each)\s+year\b|\bannually\b|\byearly\b|/\s*nam\b)',
    'biweek': r'(?:\bbiweekly\b|\b(?:every|per)\s+(?:two|2)\s+weeks?\b|/\s*2\s*tuan\b|\bmoi\s*2\s*tuan\b)',
    'day': r'(?:/\s*day\b|\b(?:per|a|each)\s+day\b|\bdaily\b|/\s*ngay\b)',
    'hour': r'(?:/\s*(?:hour|hr)\b|\b(?:per|an|each)\s+hour\b|\bhourly\b|/\s*gio\b)',
}


def numeric_pay_clauses(text):
    """Bare amounts followed by a pay period count even without a $ or K marker.

    A schedule such as '5 days/week' has a unit between the number and period,
    so it is not a monetary clause. Phones and street numbers likewise do not
    directly precede these period expressions.
    """
    period = '|'.join(PERIODS.values())
    pattern = rf'(?<![\w.,]){AMOUNT.pattern}(?:USD\b|dollars?\b)?\s*(?:{period})'
    return list(re.finditer(pattern, folded(text), re.I))


def monetary_source_tokens(original):
    """Source money spans that require candidate accounting, including bare income."""
    tokens = [(m.start(), m.end()) for m in MONEY.finditer(original)]
    for line in re.finditer(r'[^\n]+', original):
        if re.search(r'\b(?:income|salary|thu\s+nhap|luong|bao\s+luong)\b|收入', folded(line.group())):
            tokens.extend((line.start()+m.start(), line.start()+m.end())
                          for m in re.finditer(r'\d{3,}(?:[.,]\d{3})*', line.group()))
    tokens.extend((m.start(), m.end()) for m in re.finditer(
        r'(?<!\w)\d{3,}(?:[.,]\d{3})*(?=\s*(?:/|per)\s*'
        r'(?:week|tuần|tuan|month|tháng|thang|day|ngày|ngay|hour|giờ|gio)\b)', original, re.I))
    tokens.extend((m.start(1), m.end(1)) for m in re.finditer(
        r'(?:\b(?:weekly|monthly|yearly|daily|hourly|mỗi\s+tuần|mỗi\s+tháng)\b|每周|每月)'
        r'\s*\$?\s*(\d{3,}(?:[.,]\d{3})*)', original, re.I))
    return tokens


def without_work_schedule(text):
    return re.sub(r'\b\d+(?:[.,]\d+)?\s*(?:days?|ngay|hours?|hrs?|gio)\s*'
                  r'(?:/|per|moi)\s*(?:weeks?|tuan|days?|ngay)\b', ' ', text)


def salary_context(original, position):
    """Qualification is taken from the original clause, never a cherry-picked quote."""
    boundaries = list(re.finditer(r'[\n;!?]|\.(?=\s|$)', original))
    start = max((m.end() for m in boundaries if m.end() <= position), default=0)
    end = min((m.start() for m in boundaries if m.start() > position), default=len(original))
    clause = original[start:end]
    if len(clause) > 500:
        raise NeedsHuman('salary clause too long to establish bounded qualifications')
    return clause


def number(value):
    """Unambiguous US/Vietnamese separators; Decimal never passes through float."""
    if not isinstance(value, str) or not re.fullmatch(NUMBER, value):
        raise NeedsHuman('invalid salary number')
    if ',' in value and '.' in value:
        decimal = ',' if value.rfind(',') > value.rfind('.') else '.'
        thousands = '.' if decimal == ',' else ','
        whole, fraction = value.rsplit(decimal, 1)
        if not re.fullmatch(rf'\d{{1,3}}(?:\{thousands}\d{{3}})+', whole) or not 1 <= len(fraction) <= 2:
            raise NeedsHuman('ambiguous number separators')
        value = whole.replace(thousands, '') + '.' + fraction
    elif ',' in value or '.' in value:
        sep = ',' if ',' in value else '.'
        parts = value.split(sep)
        if len(parts) >= 2 and len(parts[0]) <= 3 and all(len(p) == 3 for p in parts[1:]):
            value = ''.join(parts)
        elif len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            value = '.'.join(parts)
        else:
            raise NeedsHuman('ambiguous number separators')
    try:
        result = Decimal(value)
        if not result.is_finite() or not 0 < result <= Decimal('100000000'):
            raise NeedsHuman('salary out of bounds')
        return result
    except InvalidOperation:
        raise NeedsHuman('invalid decimal') from None


def amounts(text):
    match = AMOUNT.fullmatch(text) if isinstance(text, str) else None
    if not match:
        raise NeedsHuman('unrecognized amount or range')
    a, ka, b, kb = match.groups()
    ka = ka or (kb if b else '')  # 6–10K has K on both ends.
    low = number(a) * (1000 if ka else 1)
    high = number(b) * (1000 if kb else 1) if b else low
    if high < low:
        raise NeedsHuman('reversed salary range')
    return low, high, b is not None


def _range_to_dash(text):
    return re.sub(r'(?<=[0-9kK])\s*(?:[-–—]|toi|den|to)\s*(?=\$?\s*\d)', '-', folded(text), flags=re.I)


def _literal_candidate_amount(quote, requested):
    """Resolve a unique full source range, allowing only bounded presentation marks."""
    if requested in quote:
        return requested
    normalized = _range_to_dash(requested)
    try:
        parsed = amounts(normalized.rstrip('+'))
    except NeedsHuman:
        return None
    if not parsed[2]:
        return None
    income_quote = bool(re.search(r'\b(?:income|salary|thu\s+nhap|bao\s+luong|wages?)\b|收入', folded(quote)))
    matches = []
    for match in RANGE_LITERAL.finditer(quote):
        literal = match.group().strip()
        source = _range_to_dash(literal)
        same_presentation = source == normalized
        added_currency = (income_quote and '$' not in source and '$' in normalized
                          and normalized.replace('$', '') == source)
        if (same_presentation or added_currency) and amounts(source.rstrip('+')) == parsed:
            matches.append(literal)
    return matches[0] if len(matches) == 1 else None


def _candidate_amount_unit_suffix(requested, period):
    """Separate only a terminal declared pay unit from model amount formatting.

    The caller must still bind that exact unit immediately after the resolved
    literal amount in the cited source; this function grants no unit evidence.
    """
    if period not in PERIODS:
        return requested, None
    match = re.search(rf'(?:{PERIODS[period]})\s*$', folded(requested), re.I)
    if match is None:
        return requested, None
    amount = requested[:match.start()].strip()
    suffix = requested[match.start():].strip()
    return (amount, suffix) if amount and suffix else (requested, None)


SOURCE_WEEKLY_CHINESE_INCOME_REPEAT = re.compile(
    rf'(?m)^[ \t]*收入[ \t]*[:：][ \t]*每周[ \t]*'
    rf'(?P<amount>\$\s*{NUMBER}\s*[-–—]\s*\$\s*{NUMBER})'
    r'[ \t]*(?:（按技术水平而定）)?[ \t]*$')
SOURCE_WEEKLY_VIETNAMESE_INCOME_REPEAT = re.compile(
    rf'(?im)^[ \t]*income[ \t]+cao[ \t]*'
    rf'(?P<amount>{NUMBER}[ \t]*tới[ \t]*{NUMBER})'
    r'[ \t]*/[ \t]*tuần[ \t]*$')


def _source_weekly_income_repeat(original, workers, covered):
    """Audit one untranslated model omission with an independently literal weekly range.

    The two anchored source forms are the known translated income pair. Only a
    unique uncovered full range with the same weekly upper bound can be added;
    arbitrary money and a different upper bound remain uncovered.
    """
    if len(workers) != 1 or not workers[0][2] or workers[0][3] != 'week':
        return None
    matches = [match for pattern in (SOURCE_WEEKLY_CHINESE_INCOME_REPEAT,
                                     SOURCE_WEEKLY_VIETNAMESE_INCOME_REPEAT)
               for match in pattern.finditer(original)
               if not any(a <= match.start('amount') and match.end('amount') <= b
                          for a, b in covered)]
    if len(matches) != 1:
        return None
    match = matches[0]
    amount_start, amount_end = match.span('amount')
    clause = folded(match.group())
    if re.search(r'\b(?:rent|thue|tips?|commission|an chia|bonus|thuong|deposit|dat coc|'
                 r'revenue|doanh thu|business income|asking price|sale price|gia ban|gia sang|'
                 r'service|price)\b|租金|小费|佣金|押金|营业额|价格|服务', clause):
        return None
    literal = match.group('amount')
    try:
        low, high, is_range = amounts(_range_to_dash(literal))
    except NeedsHuman:
        return None
    if not is_range or high != workers[0][1]:
        return None
    return {'quote': match.group().strip(), 'amount_text': literal, 'period': 'week',
            'low': str(low), 'high': str(high), 'source_field': 'content',
            'amount_span': {'start': amount_start, 'end': amount_end}}, (amount_start, amount_end)


def extracted_amounts(text, period, source_span=None):
    """Permit a known period suffix or an exact evidenced amount/period/qualifier excerpt.

    This does not repair evidence: the full parsed endpoints must still equal the
    original amount/range, and the original clause must establish the same period.
    """
    if isinstance(text, str) and period in PERIODS:
        match = AMOUNT.match(text)
        if match:
            suffix = folded(text[match.end():].strip())
            if suffix and re.fullmatch(PERIODS[period], suffix):
                return amounts(match.group())
            # Some models include the original qualification after the period. Extract
            # only the leading amount when the ENTIRE excerpt is literally evidenced.
            # Full-range equality, conflicting amounts/periods and qualifications are
            # still validated against the original salary clause by salary().
            if (suffix and re.match(PERIODS[period], suffix)
                    and isinstance(source_span, str) and text.strip() in source_span):
                return amounts(match.group())
    return amounts(text)


def _schedule(original, value, period):
    if not isinstance(value, dict) or set(value) != {'days_per_week', 'hours_per_day', 'hours_per_week', 'evidence'}:
        raise NeedsHuman('explicit work schedule required')
    source = folded(evidence(original, value['evidence']))
    def supported(key, expression, maximum):
        raw = value[key]
        if not isinstance(raw, str) or not re.fullmatch(r'\d+(?:\.\d+)?', raw):
            raise NeedsHuman('invalid work schedule')
        amount = Decimal(raw)
        if not 0 < amount <= maximum or not re.search(expression.format(re.escape(raw)), source):
            raise NeedsHuman('schedule value lacks evidence')
        return amount
    days = supported('days_per_week', r'\b{}\s*(?:days?|ngay)\s*(?:/|per|moi)\s*(?:week|tuan)\b', 7)
    if period == 'day':
        return days
    if value['hours_per_week'] is not None:
        return supported('hours_per_week', r'\b{}\s*(?:hours?|hrs?|gio)\s*(?:/|per|moi)\s*(?:week|tuan)\b', 168)
    hours = supported('hours_per_day', r'\b{}\s*(?:hours?|hrs?|gio)\s*(?:/|per|moi)\s*(?:day|ngay)\b', 24)
    return days * hours


PAY_WORDS = re.compile(r'\b(?:bao luong|bao(?=\s*\$?\s*\d)|luong|thu nhap|salary|pay|income|wages?|compensation|guarantee(?:d)?|commission|an chia|tips?|negotiable|thuong luong|thoa thuan|usd|dollars?)(?![a-z])')


def without_service_prices(original):
    """Mask only labelled customer-price lists; preserve original evidence offsets.

    Generic 'giá' and unlabelled money are deliberately insufficient. A salary
    following the list remains visible to all existing pay/conflict guards.
    """
    service = (r'(?:gội\s+đầu(?:\s+dưỡng\s+sinh)?|goi\s+dau(?:\s+duong\s+sinh)?|'
               r'head\s+spa|hair\s+wash|shampoo|manicure|pedicure|wax(?:ing)?|lashes)')
    item = rf'\$\s*{NUMBER}(?:\s*[kK])?(?:\s*[-–—]\s*\$?\s*{NUMBER}(?:\s*[kK])?)?'
    expression = (rf'(?<!\w)(?:giá|gia|prices?)\s+{service}\s*:\s*'
                  rf'{item}(?:[ \t]*,[ \t]*{item})*(?![\w,]|\.\d)')
    price_list = re.compile(expression + '|' + unicodedata.normalize('NFD', expression), re.I)
    return price_list.sub(lambda match: ' ' * len(match.group()), original)


def multiple_source_salaries(original, *, audit_original=None):
    """Owner: highest of multiple explicit worker-pay clauses, each with local unit.

    No unit is borrowed from another clause, heading or work schedule. Unknown
    money remains subject to the existing ambiguity guard rather than discarded.
    """
    local_period = '|'.join(PERIODS.values()) + r'|/\s*2\s*weeks?\b|\btuan\b'
    local_amount = AMOUNT.pattern.replace('[-–—]', '[-–—/]')
    pattern = re.compile(r'(?<![\w.,])(?P<amount>' + local_amount +
                         r')\+?\s*(?:USD\b|dollars?\b)?\s*(?P<period>' + local_period + ')', re.I)
    matches = list(pattern.finditer(folded(original)))
    if not matches:
        return None
    if len(matches) == 1 and not re.search(r'[-–—/]', matches[0].group('amount')):
        return None  # Existing single-scalar policy remains unchanged.
    values = []
    for index, match in enumerate(matches):
        clause = salary_context(folded(original), match.start())
        prefix_start = max((boundary.end() for boundary in re.finditer(r'[\n;!?]|\.(?=\s|$)',
                           folded(original)[:match.start()])), default=0)
        if index:
            prefix_start = max(prefix_start, matches[index-1].end())
        prefix = folded(original)[prefix_start:match.start()]
        if re.search(r'\b(?:rent|thue|bonus|thuong|sale|sang tiem|deposit|dat coc|tips?|commission|an chia)\b', prefix):
            return None  # Never select money explicitly labelled as non-base pay.
        if not (PAY_WORDS.search(clause) or re.search(r'\beverything\s*:\s*$', prefix)
                or not re.search(r'[a-z]', clause[:clause.find(match.group())])):
            return None
        unit = match.group('period')
        period = next((key for key, regex in PERIODS.items() if re.fullmatch(regex, unit)), None)
        if re.fullmatch(r'/\s*2\s*weeks?', unit):
            period = 'biweek'
        if unit == 'tuan':
            period = 'week'
        if period not in ('week', 'month', 'year', 'biweek'):
            return None  # Day/hour still require explicit validated work schedule.
        following = folded(original)[match.end():].split('\n', 1)[0].split(';', 1)[0]
        following = pattern.split(following, maxsplit=1)[0]
        if any(re.search(regex, without_work_schedule(following)) for regex in PERIODS.values()):
            return None
        low, high, _ = amounts(match.group('amount').replace('/', '-'))
        def weekly(amount):
            return (amount * 12 / 52 if period == 'month' else amount / 52 if period == 'year'
                    else amount / 2 if period == 'biweek' else amount)
        values.extend((weekly(low), weekly(high)))
    # Folded-source offsets are used consistently for all monetary coverage.
    text = folded(original)
    if any(not any(m.start() <= money.start() and money.end() <= m.end() for m in matches)
           for money in MONEY.finditer(text)):
        return None
    # A bare salary without its own time unit cannot inherit a neighbouring unit.
    for label in re.finditer(r'\b(?:bao luong|bao|luong|thu nhap|salary|pay|income|wages?|compensation)'
                             r'\s*[:=]?\s*\$?\s*(?P<number>\d)', text):
        if not any(m.start() <= label.start('number') < m.end() for m in matches):
            return None
    result = {'mode': 'weekly', 'low': str(min(values)), 'high': str(max(values)), 'range': True,
              'rationale': 'Owner: highest explicit worker income across locally evidenced periods; floor weekly display to 100'}
    if len(matches) == 1:
        def original_offset(target):
            offset = 0
            for index, character in enumerate(original):
                normalized = folded(character)
                if offset == target and normalized:
                    return index
                offset += len(normalized)
            return len(original)
        raw_start = original_offset(matches[0].start('amount'))
        raw_end = original_offset(matches[0].end('amount'))
        audit_original = original if audit_original is None else audit_original
        clause = salary_context(audit_original, raw_start)
        result.update(source_amount=audit_original[raw_start:raw_end].strip(),
                      period=period, qualification_evidence=clause,
                      kind='potential' if re.search(r'\b(?:income|thu nhap|tuy|depending|potential)\b', folded(clause)) else 'guaranteed')
    return result


def source_sale_price(original):
    """Owner: explicit salon sale asking prices precede worker salary.

    Amount association is lexical and bounded, never the minimum of all money
    in a mixed posting. Revenue and wages do not become an asking price.
    """
    text = folded(original)
    if re.search(r'\b(?:not|never|no\s+longer)\s+(?:(?:currently|actually)\s+)?for\s+(?:sale|transfer)\b'
                 r'|\b(?:khong|chua)\s+(?:(?:can|muon)\s+)?(?:ban|sang(?:\s+nhuong)?)\s+tiem\b'
                 r'|\btiem\s+(?:khong|chua)\s+(?:ban|sang(?:\s+nhuong)?)\b', text):
        return None  # Contradictory business-sale intent cannot authorize an override.
    business = r'(?:tiem(?:\s+nails?)?|(?:nail\s+)?salon|business)'
    sale = (rf'(?:\b(?:ban|sang(?:\s+nhuong)?|chuyen\s+nhuong|nhuong\s+lai)\s+{business}\b'
            rf'|\b{business}\s+(?:for\s+sale|for\s+transfer)\b)')
    connector = r'\s*(?:(?:voi\s+gia|gia\s+ban|gia\s+sang|gia|voi|chi|la|asking\s+price|price|for|at)\s*)?[:=]?\s*'
    direct = re.compile(sale + connector + r'(?P<amount>' + AMOUNT.pattern + r')(?![\w,]|\.\d)')
    candidates = []
    positive_heads = []
    for head in re.finditer(sale, text):
        prefix = salary_context(text, head.start()).split(head.group(), 1)[0]
        if re.search(r'\b(?:khong|chua|not|never|no\s+longer)\b', prefix):
            continue
        positive_heads.append(head)
    for match in direct.finditer(text):
        if any(head.start() == match.start() for head in positive_heads):
            candidates.append(match)
    # A separate bare asking label is allowed only directly after a positive
    # business-sale heading/price clause, with no intervening prose to reassign it.
    asking = re.compile(r'\b(?:gia\s+(?:ban|sang\s+nhuong|sang|chuyen\s+nhuong)|asking\s+price|sale\s+price)'
                        + connector + r'(?P<amount>' + AMOUNT.pattern + r')(?![\w,]|\.\d)')
    for match in asking.finditer(text):
        if any(a.start() <= match.start() < a.end() for a in candidates):
            continue
        anchors = positive_heads + candidates
        if any(0 <= match.start()-anchor.end() <= 300 and
               not text[anchor.end():match.start()].strip(' \t\r\n.:;,-') for anchor in anchors):
            candidates.append(match)
    values = []
    for match in candidates:
        suffix = text[match.end():match.end()+60].lstrip()
        suffix = re.sub(r'^(?:usd|dollars?)\b\s*', '', suffix)
        if any(re.match(regex, suffix) for regex in PERIODS.values()):
            continue  # Periodic income/rent is not a literal asking price.
        if (re.match(r'(?:per\b|/|moi\b|each\b)\s*\w', suffix)
                or re.match(r'(?:for\s+)?(?:manicure|pedicure|service|dich\s+vu|goi\s+dau|wax|lashes)\b', suffix)):
            continue
        low, _, _ = amounts(match.group('amount'))
        values.append(low)
    if not values:
        return None
    price = str(min(values))
    return {'mode': 'weekly', 'low': price, 'high': price, 'range': False,
            'copy_no_period': True, 'sale_asking_price': True,
            'rationale': 'Owner: lowest explicit salon sale/transfer asking endpoint, literal value; no conversion or rounding'}


def source_salary_policy(original):
    """Owner's two source-only policies. None leaves the existing period parser in charge.

    Bare contact/address numbers never establish pay. Every monetary mention in
    the no-period case must belong to a pay clause and agree on the same scalar.
    """
    sale_price = source_sale_price(original)
    if sale_price is not None:
        return sale_price
    audit_original = original
    original = without_service_prices(original)
    source = folded(original)
    multiple = multiple_source_salaries(original, audit_original=audit_original)
    if multiple is not None:
        return multiple
    negotiated = {'mode': 'negotiated', 'low': None, 'high': None, 'range': False,
                  'rationale': 'Owner: absent or unclear numeric base salary defaults to negotiated'}
    if not PAY_WORDS.search(source) and not MONEY.search(original) and not numeric_pay_clauses(original):
        return negotiated
    mentions = list(MONEY.finditer(original))
    # Explicit bare numeric pay is permitted only immediately after a salary label.
    bare = re.compile(rf'\b(?:bao lương|bao luong|bao|lương|luong|thu nhập|thu nhap|salary|pay|income|wages?|compensation|guaranteed?)\s*[:=]?\s*(?:(?:từ|tu|from)\s+)?({NUMBER}\s*[kK]?)(?![\w,]|\.\d)', re.I)
    positions = [(m.start(), m.group()) for m in mentions]
    positions += [(m.start(1), original[m.start(1):m.end(1)]) for m in bare.finditer(original)
                  if not any(a <= m.start(1) < a+len(t) for a,t in positions)]
    # Preserve numeric pay-period clauses even without a currency/label, using
    # original offsets rather than offsets into accent-normalized text.
    for match in re.finditer(r'(?<![\w.,])' + AMOUNT.pattern, original):
        suffix = folded(original[match.end():match.end()+100].lstrip())
        suffix = re.sub(r'^(?:usd|dollars?)\b\s*', '', suffix)
        if any(re.match(pattern, suffix) for pattern in PERIODS.values()):
            if not any(a <= match.start() < a+len(t) for a,t in positions):
                positions.append((match.start(), match.group()))
    if not positions:
        return negotiated  # Vague pay/commission wording or unrelated non-monetary numbers.
    base_label = r'\b(?:bao luong|bao(?=\s*\$?\s*\d)|luong|thu nhap|salary|pay|income|wages?|compensation|guaranteed?)(?![a-z])'
    has_base = False
    for position, token in positions:
        clause = salary_context(original, position)
        context = folded(clause)
        prefix = folded(original[max(0, position-100):position])
        direct_base = re.search(r'\b(?:bao luong|bao|luong|thu nhap|salary|pay|income|wages?|compensation|guaranteed?)'
                               r'\s*[:=]?\s*(?:(?:tu|from|up to)\s+)?\$?\s*$', prefix)
        money_context = re.sub(r'\bthuong luong\b', ' ', context)
        if not direct_base and re.search(r'\b(?:rent|thue|bonus|thuong|sale|sang tiem|deposit|dat coc|tips?)\b', money_context):
            continue
        if direct_base or re.search(base_label, context) or (numeric_pay_clauses(clause) and not re.search(r'\b(?:commission|an chia)\b', context)):
            has_base = True
    if not has_base:
        return negotiated  # Rent, tips or pure commission never become numeric base salary.
    if any(re.search(pattern, without_work_schedule(source)) for pattern in PERIODS.values()):
        return None
    parsed = []
    covered_until = -1
    for position, token in sorted(positions):
        if position < covered_until:
            continue  # Second monetary marker of a FULL range, not competing pay.
        clause = salary_context(original, position)
        context = folded(clause)
        base_pay = re.search(r'\b(?:bao luong|bao(?=\s*\$?\s*\d)|luong|thu nhap|salary|pay|income|wages?|compensation|guaranteed?)(?![a-z])', context)
        alternative_commission = (re.search(r'\b(?:bao luong|guaranteed?)\b', context)
                                  and re.search(r'\b(?:hoac|or)\s+(?:an chia|commission)\b', context))
        if (not base_pay or re.search(r'\b(?:rent|thue|bonus|thuong|sale|sang tiem|deposit|dat coc|tips?)\b', context)
                or (re.search(r'\b(?:commission|an chia)\b', context) and not alternative_commission)):
            raise NeedsHuman('ambiguous salary source amount', details={'kind': 'data', 'field': 'salary_amount',
                'source': token, 'evidence': clause, 'action': 'Đối chiếu tiền lương với tiền thuê, thưởng hoặc giá bán; chọn mức lương chính.'})
        match = AMOUNT.match(original, position)
        low, high, is_range = amounts(match.group() if match else token)
        covered_until = match.end() if match else position + len(token)
        parsed.append((low, high, is_range))
    if len(set(parsed)) != 1:
        raise NeedsHuman('ambiguous salary source amount', details={'kind': 'data', 'field': 'salary_amount',
            'source': ', '.join(f'{a}-{b}' if ranged else format(a, 'f') for a,b,ranged in sorted(set(parsed))),
            'action': 'Nội dung nêu nhiều mức tiền khác nhau; xác nhận mức lương chính và đơn vị nếu có.'})
    low, high, is_range = parsed[0]
    return {'mode': 'weekly', 'low': format(low, 'f'), 'high': format(high, 'f'), 'range': is_range,
            'source_amount': f'{low}-{high}' if is_range else format(low, 'f'),
            'period': None, 'kind': 'source', 'copy_no_period': True,
            'rationale': 'Owner: explicit numeric salary without period copied unchanged; no conversion'}


def salary(original, value):
    policy = source_salary_policy(original)
    if policy is not None:
        return policy
    if not isinstance(value, dict) or set(value) != {'mode', 'kind', 'amount_text', 'period', 'evidence', 'schedule', 'rationale'}:
        raise NeedsHuman('invalid salary schema')
    rationale = value['rationale']
    if not isinstance(rationale, str) or len(rationale) > 240:
        raise NeedsHuman('invalid bounded rationale')
    mode = value['mode']
    pay_source = without_service_prices(original)
    source = folded(pay_source)
    if mode == 'hidden':
        if (value['kind'] != 'none' or value['amount_text'] is not None or value['period'] is not None
                or value['schedule'] is not None):
            raise NeedsHuman('hidden pay schema contains a salary claim')
        if (MONEY.search(original) or PAY_WORDS.search(source)
                or (re.search(r'\d', source) and any(re.search(p, source) for p in PERIODS.values()))):
            raise NeedsHuman('absence of salary is not established')
        return {'mode': mode, 'low': None, 'high': None, 'range': False, 'rationale': rationale}
    span = evidence(original, value['evidence'])
    context = folded(span)
    if mode == 'negotiated':
        if (value['kind'] != 'none' or value['amount_text'] is not None or value['period'] is not None
                or value['schedule'] is not None):
            raise NeedsHuman('negotiated pay schema contains a numeric salary claim')
        if (not re.search(r'\b(negotiable|thuong luong|thoa thuan)\b', context)
                or MONEY.search(original) or numeric_pay_clauses(original)):
            raise NeedsHuman('negotiated pay is not established')
        return {'mode': mode, 'low': None, 'high': None, 'range': False, 'rationale': rationale}
    if mode != 'weekly' or value['kind'] not in ('guaranteed', 'potential'):
        raise NeedsHuman('ambiguous pay, tips or commission require review')
    if re.search(r'\b(commission|tip[s]?)\b', context):
        raise NeedsHuman('cannot assume tips/commission are salary')
    raw = value['amount_text']
    if not isinstance(raw, str):
        raise NeedsHuman('invalid salary amount text')
    low, high, is_range = extracted_amounts(raw, value['period'], span)
    # Model formatting may change spaces, dash, thousands separators or K notation.
    # Only exact Decimal equality with the FULL parsed source amount/range permits it.
    numeric_span = AMOUNT.search(span)
    if numeric_span is None or amounts(numeric_span.group()) != (low, high, is_range):
        raise NeedsHuman('salary evidence does not support whole amount/range')
    source_raw = numeric_span.group().strip()
    start = original.index(span) if 'quote' in value['evidence'] else value['evidence']['start']
    raw_start = start + numeric_span.start() + len(numeric_span.group()) - len(numeric_span.group().lstrip())
    if not pay_source[raw_start:raw_start+len(source_raw)].strip():
        raise NeedsHuman('salary evidence selects a service price')
    original_clause = salary_context(pay_source, raw_start)
    qualification = folded(original_clause)
    if re.search(r'\b(commission|tip[s]?)\b', qualification):
        raise NeedsHuman('cannot assume tips/commission are salary')
    kind = ('potential' if re.search(r'\b(up to|potential|income|thu nhap|co the|len den|tuy|depending)\b', qualification)
            else value['kind'])
    # Owner explicitly chose the upper display for the income range example, retaining qualifiers.
    # A single potential amount is still not sufficient evidence of a guaranteed weekly salary.
    if kind == 'potential' and not is_range:
        raise NeedsHuman('single potential income requires review')
    # A model cannot select a scalar inside a source range or ignore another monetary clause.
    if MONEY.search(span[:numeric_span.start()] + span[numeric_span.end():]):
        raise NeedsHuman('another monetary amount in salary evidence requires review')
    end = start + len(span)
    if any(not (start <= m.start() and m.end() <= end) for m in MONEY.finditer(pay_source)):
        raise NeedsHuman('other monetary clause requires review')
    # Compare against the selected amount itself, not just its enclosing evidence:
    # one long quote can contain several conflicting bare salary clauses.
    clauses = numeric_pay_clauses(pay_source)
    selected_start = len(folded(pay_source[:raw_start]))
    selected_end = selected_start + len(folded(source_raw))
    if any(not (m.start() <= selected_start and selected_end <= m.end()) for m in clauses):
        raise NeedsHuman('another numeric salary clause requires review')
    if any(amounts(AMOUNT.search(m.group()).group()) != (low, high, is_range) for m in clauses):
        raise NeedsHuman('selected salary does not cover the original full amount/range')
    period = value['period']
    if period not in PERIODS or not re.search(PERIODS[period], context):
        raise NeedsHuman('salary period lacks source evidence')
    # A labeled work schedule (6 days/week) does not introduce a second pay period.
    pay_context = without_work_schedule(qualification)
    detected = {key for key, pattern in PERIODS.items() if re.search(pattern, pay_context)}
    if detected != {period}:
        raise NeedsHuman('ambiguous salary periods')
    factor = {'week': Decimal(1), 'month': Decimal(12)/52, 'year': Decimal(1)/52,
              'biweek': Decimal(1)/2}.get(period)
    if factor is None:
        factor = _schedule(original, value['schedule'], period)
    # Apply multiply before divide for the specified conversion, retaining Decimal precision.
    def convert(amount):
        if period == 'month':
            return amount * 12 / 52
        if period == 'year':
            return amount / 52
        if period == 'biweek':
            return amount / 2
        return amount * factor
    return {'mode': mode, 'low': str(convert(low)), 'high': str(convert(high)),
            'range': is_range, 'source_amount': source_raw, 'period': period, 'kind': kind,
            'qualification_evidence': original_clause, 'rationale': rationale}


def candidate_salary(original, candidates):
    """Validate every model-tagged money clause, then select worker base income.

    The model supplies semantic roles; literal amounts, local periods, coverage,
    conversion and the display range remain code-owned.
    """
    if not isinstance(candidates, list) or len(candidates) > 20:
        raise EvidenceError('invalid monetary candidate list', path='salary_candidates')
    allowed = {'worker', 'service', 'rent', 'tips', 'commission', 'revenue', 'sale', 'other'}
    covered = []
    workers = []
    for index, item in enumerate(candidates):
        path = f'salary_candidates[{index}]'
        if (not isinstance(item, dict) or set(item) != {'kind', 'amount_text', 'period', 'evidence', 'role', 'schedule'}
                or item['kind'] not in allowed or not isinstance(item['amount_text'], str)
                or not isinstance(item['role'], str) or len(item['role']) > 80):
            raise EvidenceError('invalid monetary candidate schema', path=path)
        try:
            quote = evidence(original, item['evidence'])
        except EvidenceError as error:
            raise EvidenceError(str(error), path=path+'.evidence') from None
        start = original.index(quote) if 'quote' in item['evidence'] else item['evidence']['start']
        requested, unit_suffix = _candidate_amount_unit_suffix(item['amount_text'].strip(), item['period'])
        amount_text = _literal_candidate_amount(quote, requested)
        if not amount_text:
            raise EvidenceError('candidate amount must be an exact original substring', path=path+'.amount_text')
        if unit_suffix is not None:
            if (quote.count(amount_text) != 1 or
                    not quote[quote.index(amount_text)+len(amount_text):].lstrip().startswith(unit_suffix)):
                raise EvidenceError('candidate amount unit must immediately follow source range',
                                    path=path+'.amount_text')
        normalized = _range_to_dash(amount_text)
        normalized = normalized.rstrip('+').strip()
        try:
            low, high, is_range = amounts(normalized)
        except NeedsHuman as error:
            raise EvidenceError(str(error), path=path+'.amount_text') from None
        amount_start = start + quote.index(amount_text)
        amount_end = amount_start + len(amount_text)
        for match in RANGE_LITERAL.finditer(original):
            range_start = match.start() + len(match.group()) - len(match.group().lstrip())
            range_end = match.end() - len(match.group()) + len(match.group().rstrip())
            if range_start < amount_end and amount_start < range_end:
                if amount_start > range_start or amount_end < range_end:
                    raise EvidenceError('candidate splits an original monetary range', path=path+'.amount_text')
                source_amount = _range_to_dash(match.group().strip()).rstrip('+')
                if amounts(source_amount) != (low, high, is_range):
                    raise EvidenceError('candidate range differs from original', path=path+'.amount_text')
        if item['role'] and item['role'] not in original:
            raise EvidenceError('candidate role lacks original literal', path=path+'.role')
        local_clause = salary_context(quote, quote.index(amount_text))
        local_text = folded(local_clause)
        original_clause = folded(without_work_schedule(salary_context(original, amount_start)))
        periods = {key for key, pattern in PERIODS.items() if re.search(pattern, without_work_schedule(local_text))}
        source_periods = {key for key, pattern in PERIODS.items() if re.search(pattern, original_clause)}
        period = item['period']
        if period is not None and period not in PERIODS:
            raise EvidenceError('unknown candidate period', path=path+'.period')
        if periods != ({period} if period else set()) or source_periods != periods:
            raise EvidenceError('candidate period lacks a unique local source unit', path=path+'.period')
        if item['kind'] == 'worker':
            if not without_service_prices(original)[amount_start:amount_end].strip():
                raise EvidenceError('worker candidate selects a recognized customer service price', path=path+'.kind')
            alternative_commission = (re.search(r'\b(?:bao luong|guaranteed?)\b', original_clause)
                                      and re.search(r'\b(?:hoac|or)\s+(?:an chia|commission)\b', original_clause))
            non_base = list(re.finditer(
                r'\b(?:rent|thue|bonus|thuong|tips?|commission|an chia|deposit|dat coc|'
                r'revenue|doanh thu|business income|asking price|sale price|gia ban|gia sang)\b',
                original_clause))
            if non_base and not (alternative_commission and all(
                    match.group() in ('commission', 'an chia') for match in non_base)):
                raise EvidenceError('worker candidate includes non-base pay', path=path+'.kind')
            workers.append((low, high, is_range, period, quote, item['schedule']))
        elif (re.search(r'\b(?:salary|income|thu\s+nhap|bao\s+luong|wages?)\b|收入', original_clause)
              and not re.search(r'\b(?:service|price|rent|tips?|commission|revenue|sale|bonus)\b', original_clause)):
            raise EvidenceError('income clause classified as non-worker money', path=path+'.kind')
        covered.append((amount_start, amount_end))
    source_repeat = _source_weekly_income_repeat(original, workers, covered)
    if source_repeat is not None:
        repeat_audit, repeat_span = source_repeat
        covered.append(repeat_span)
    # Every currency-marked clause and every bare amount in an income line must
    # have an independently classified candidate. A range must cover both ends.
    tokens = monetary_source_tokens(original)
    if any(not any(span_start <= a and b <= span_end for span_start, span_end in covered)
           for a, b in tokens):
        raise EvidenceError('monetary source clause omitted from candidates', path='salary_candidates')
    if not workers:
        return {'mode':'negotiated', 'low':None, 'high':None, 'range':False,
                'rationale':'Owner: no usable numeric worker base income'}
    if any(period is None for _, _, _, period, _, _ in workers):
        if len(workers) != 1:
            if all((low, high, is_range) == workers[0][:3] and period is None
                   for low, high, is_range, period, _, _ in workers):
                source_policy = source_salary_policy(original)
                if (source_policy is not None and source_policy.get('copy_no_period')
                        and Decimal(source_policy['low']) == workers[0][0]
                        and Decimal(source_policy['high']) == workers[0][1]):
                    return source_policy
            raise NeedsHuman('multiple worker amounts without local units require review',
                details={'kind':'data', 'field':'salary_amount', 'current':'',
                         'source':'Multiple worker amounts lack independent pay periods',
                         'action':'Confirm each source amount and its local pay period before choosing a weekly value.'})
        return source_salary_policy(original) or salary(original, {
            'mode':'ambiguous', 'kind':'ambiguous', 'amount_text':None, 'period':None,
            'evidence':None, 'schedule':None, 'rationale':'No source period'})
    values = []
    for low, high, is_range, period, quote, schedule in workers:
        factor = {'week':Decimal(1), 'month':Decimal(12)/52, 'year':Decimal(1)/52,
                  'biweek':Decimal(1)/2}.get(period)
        if factor is None:
            factor = _schedule(original, schedule, period)
        convert = lambda amount: (amount * 12 / 52 if period == 'month' else
                                  amount / 52 if period == 'year' else amount * factor)
        values.extend((convert(low), convert(high)))
    if source_repeat is not None:
        values.extend((Decimal(repeat_audit['low']), Decimal(repeat_audit['high'])))
    result = {'mode':'weekly', 'low':str(min(values)), 'high':str(max(values)),
            'range':min(values) != max(values),
            'source_clauses':[item[4] for item in workers],
            'rationale':'Owner: highest locally evidenced worker income; floor weekly display to 100'}
    if source_repeat is not None:
        result['source_repeats'] = [repeat_audit]
    return result


PHONE = re.compile(r'(?<!\w)(?:\+?1[\s.()-]*)?(?:\(?\d{3}\)?[\s.()-]*)\d{3}[\s.()-]*\d{4}(?:\s*(?:ext\.?|x|#)\s*\d+)?(?!\d)', re.I)


def phone_key(text):
    extension = re.search(r'(?:ext\.?|x|#)\s*(\d+)\s*$', text, re.I)
    body = text[:extension.start()] if extension else text
    digits = re.sub(r'\D', '', body)
    if len(digits) == 11 and digits.startswith('1'):
        digits = digits[1:]
    return digits, extension.group(1) if extension else ''


def dedup_phones(primary, other):
    """Preserve unknown phone syntax rather than deleting content we cannot parse."""
    def parse(value):
        matches = list(PHONE.finditer(value))
        residue = PHONE.sub('', value)
        if value.strip() and (not matches or re.sub(r'[\s,;/|]+', '', residue)):
            raise NeedsHuman('phone field contains unrecognized data')
        return [m.group().strip() for m in matches]
    seen = {phone_key(value) for value in parse(primary)}
    kept = []
    for value in parse(other):
        key = phone_key(value)
        if key not in seen:
            seen.add(key)
            kept.append(value)
    return '; '.join(kept)


FACT_KINDS = ('salon', 'contact', 'location', 'salary', 'schedule', 'benefits', 'requirements')
LOCATION_FIELDS = ('salon', 'address', 'city', 'state', 'zip', 'email', 'phone_primary', 'phone_other')


def source_state_repair(post):
    """Resolve only a current city's explicit address state/ZIP, before model evidence.

    An isolated prose city/state pair is insufficient. Every matching pair must
    agree, including mentions outside the explicit address, before any repair.
    """
    city = post.fields.get('city', '').strip()
    if not city:
        return None
    pattern = re.compile(r'(?<!\w)' + re.escape(city) +
        r'\s*,?\s+([A-Z]{2})(?![A-Za-z])(?:\s*(\d{5}(?:-?\d{4})?)(?!\d))?')
    pairs = list(pattern.finditer(post.content))
    if not pairs:
        return None
    explicit = []
    for match in pairs:
        prefix = post.content[max(0, match.start()-300):match.start()]
        # An explicit address label must be in this same bounded sentence/line.
        prefix = re.split(r'[\n;!?]|\.(?=\s|$)', prefix)[-1]
        if match.group(2) and re.search(r'\b(?:dia chi(?: tiem| salon)?|address)\s*:', folded(prefix)):
            explicit.append(match)
    if not explicit:
        return None
    states = {match.group(1) for match in pairs}
    zips = {match.group(2).replace('-', '') for match in pairs if match.group(2)}
    if len(states) != 1 or len(zips) != 1:
        raise NeedsHuman('location source address pairs conflict', details={'kind': 'data', 'field': 'state',
            'current': post.fields.get('state', ''),
            'source': '; '.join(dict.fromkeys(match.group() for match in pairs))[:220],
            'action': 'Nội dung nêu nhiều cặp địa chỉ không thống nhất; xác nhận tiểu bang và ZIP đúng của salon.'})
    match = explicit[0]
    return {'value': match.group(1), 'evidence': {'start': match.start(), 'end': match.end()}}


ADDRESS_PAIR = re.compile(
    r'(?<!\w)\d+[A-Za-z]?\s+[^\n,;]{1,180}?\b'
    r'(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Circle|Cir|Parkway|Pkwy|Place|Pl|Terrace|Ter|Highway|Hwy|Way)\.?[,]?'
    r'(?:\s+(?:#\s*[A-Za-z0-9-]+|(?:Suite|Ste|Unit|Apt)\.?\s+[A-Za-z0-9-]+))?'
    r'\s+(?P<city>[A-Za-z][A-Za-z .\x27’\-]{0,90}?)\s*,?\s+'
    r'(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-?\d{4})?)(?!\d)')


def current_address_repair(post):
    """Fill missing city/ZIP from this fingerprint-bound address, never model evidence."""
    address = post.fields.get('address', '').strip()
    if not address or len(address) > 500 or (post.fields.get('city', '').strip() and post.fields.get('zip', '').strip()):
        return {}
    match = ADDRESS_PAIR.fullmatch(address)
    if not match:
        return {}
    city, state, zip_code = (match.group(key).strip() for key in ('city', 'state', 'zip'))
    # Reject a swallowed street/unit fragment instead of guessing a city boundary.
    city_tail = re.sub(r'^St\.?\s+', '', city)
    if re.search(r'\b(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Court|Ct|Circle|Cir|Parkway|Pkwy|Place|Pl|Terrace|Ter|Highway|Hwy|Way)\.?\s+\w', city_tail, re.I):
        return {}
    for key, value in (('state', state), ('city', city), ('zip', zip_code)):
        current = post.fields.get(key, '').strip()
        equivalent = current.replace('-', '') == value.replace('-', '') if key == 'zip' else current == value
        if (key == 'state' and not equivalent) or (current and not equivalent):
            raise NeedsHuman('current address conflicts with contact field', details={'kind': 'data', 'field': key,
                'current': current, 'source': value, 'evidence': address,
                'action': 'Đối chiếu địa chỉ salon và trường đang có; xác nhận giá trị đúng trước khi tự điền phần còn thiếu.'})
    # Explicit original street addresses cannot contradict the trusted existing address tail.
    body_addresses = list(ADDRESS_PAIR.finditer(post.content))
    for body_match in body_addresses:
        other = (body_match.group('city').strip(), body_match.group('state'), body_match.group('zip').replace('-', ''))
        if other != (city, state, zip_code.replace('-', '')):
            raise NeedsHuman('original address conflicts with current address repair', details={'kind': 'data',
                'field': 'address', 'current': address, 'source': body_match.group(),
                'action': 'Nội dung gốc và địa chỉ hiện có không thống nhất; xác nhận địa chỉ salon trước khi bổ sung thành phố/ZIP.'})
    body_pairs = re.compile(r'(?<!\w)(?P<city>[A-Z][A-Za-z.\x27’\-]*(?:\s+[A-Z][A-Za-z.\x27’\-]*){0,6})'
                            r'\s*,?\s+(?P<state>[A-Z]{2})\s+(?P<zip>\d{5}(?:-?\d{4})?)(?!\d)')
    for body_match in body_pairs.finditer(post.content):
        if any(start.start() <= body_match.start() and body_match.end() <= start.end() for start in body_addresses):
            continue
        other = (body_match.group('city'), body_match.group('state'), body_match.group('zip').replace('-', ''))
        if other != (city, state, zip_code.replace('-', '')):
            raise NeedsHuman('original address conflicts with current address repair', details={'kind': 'data',
                'field': 'address', 'current': address, 'source': body_match.group(),
                'action': 'Nội dung gốc nêu thành phố/tiểu bang/ZIP khác; xác nhận địa chỉ salon trước khi tự điền.'})
    return {key: {'value': value, 'source_field': 'address', 'evidence': address}
            for key, value in (('city', city), ('zip', zip_code)) if not post.fields.get(key, '').strip()}


def current_address_state_repair(post):
    """A complete stored address can correct its own conflicting state field."""
    address = post.fields.get('address', '').strip()
    match = ADDRESS_PAIR.fullmatch(address)
    if not match or not post.fields.get('city') or not post.fields.get('zip'):
        return {}
    if (post.fields['city'].strip() != match.group('city').strip()
            or post.fields['zip'].replace('-', '') != match.group('zip').replace('-', '')):
        return {}
    state = match.group('state')
    if post.fields.get('state', '').strip() == state:
        return {}
    if source_state_repair(post) is not None:
        return {}  # Original address evidence has priority in the existing path.
    for other in ADDRESS_PAIR.finditer(post.content):
        if (other.group('city').strip(), other.group('state'), other.group('zip').replace('-', '')) != (
                match.group('city').strip(), state, match.group('zip').replace('-', '')):
            raise NeedsHuman('original address conflicts with current address state repair')
    pair = re.compile(r'(?<!\w)' + re.escape(match.group('city').strip()) +
                      r'\s*,?\s+([A-Z]{2})\s+(\d{5}(?:-?\d{4})?)(?!\d)')
    if any(other.group(1) != state or other.group(2).replace('-', '') != match.group('zip').replace('-', '')
           for other in pair.finditer(post.content)):
        raise NeedsHuman('original city/state pair conflicts with current address state repair')
    return {'state': {'value':state, 'source_field':'address', 'evidence':address}}


def source_store_name_repair(post):
    """Owner-confirmed BaoNail stores slug convention, bound to the literal URL."""
    if post.fields.get('salon', '').strip().casefold() != 'baonail.com':
        return {}
    matches = list(re.finditer(r'https://[^\s<>]+', post.content))
    names = []
    for match in matches:
        url = match.group().rstrip('.,;')
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname not in ('baonail.com', 'www.baonail.com') or parsed.path != '/index.php':
            continue
        query = parse_qs(parsed.query, keep_blank_values=True)
        stores = query.get('stores', [])
        if len(stores) != 1 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){1,5}', stores[0]):
            continue
        name = stores[0].replace('-', ' ')
        if len(name) > 30:
            raise NeedsHuman('source store name exceeds salon field limit')
        names.append((name, url))
    if not names:
        return {}
    if len({name.casefold() for name, _ in names}) != 1:
        raise NeedsHuman('source store names conflict')
    return {'salon': {'value':names[0][0], 'source_field':'content_url', 'evidence':names[0][1]}}


def category_price_policy(post, suggestion=None):
    from .categories import category_proposal
    target = category_proposal(post, suggestion)['target']
    if target == 'Portfolio':
        return {'mode': 'not_applicable', 'low': None, 'high': None, 'range': False,
                'rationale': 'Owner: seeker category has no salary/price fields in the inspected form'}
    if target == 'SalonForSale':
        return source_sale_price(post.content) or {'mode': 'negotiated', 'low': None, 'high': None, 'range': False,
                'rationale': 'Owner: no explicit salon asking price; negotiated, never worker salary or revenue'}
    return source_salary_policy(post.content)


def normalize_extraction(post, data):
    """Drop empty/no-op location suggestions, then validate literal evidence with safe paths.

    Current field equality proves only that no field change is requested. It does
    not establish that the value appears in original content or excuse conflicts.
    An empty suggestion never requests deletion and leaves current fields intact.
    """
    if not isinstance(data, dict):
        return data  # proposal() owns strict schema validation.
    result = dict(data)
    address_repair = {**current_address_repair(post), **current_address_state_repair(post),
                      **source_store_name_repair(post)}
    grounded_state = source_state_repair(post)
    from .categories import category_proposal
    target_category = category_proposal(post, data.get('category'))['target']
    model_worker_clauses = target_category == 'JobPosting' and isinstance(data.get('salary_candidates'), list)
    noncontact_source = PHONE.sub(' ', post.content)
    noncontact_source = ADDRESS_PAIR.sub(' ', noncontact_source)
    if (model_worker_clauses and not monetary_source_tokens(post.content)
            and not re.search(r'\d{3,}', noncontact_source)):
        no_pay = source_salary_policy(post.content)
        if no_pay is not None and no_pay['mode'] == 'negotiated':
            # No source money exists to support a generated candidate. Preserve
            # model location/category checks while using the established no-pay policy.
            result.pop('salary_candidates', None)
            model_worker_clauses = False
    source_policy = None if model_worker_clauses else category_price_policy(post, data.get('category'))
    if source_policy is not None:
        # Evidence/schema from an unused model salary cannot block an exact source policy.
        result['salary'] = {'mode': source_policy['mode'] if source_policy['mode'] == 'negotiated' else 'hidden',
                            'kind': 'none', 'amount_text': None, 'period': None,
                            'evidence': None, 'schedule': None, 'rationale': source_policy['rationale']}
    location = data.get('location')
    if isinstance(location, dict):
        location = dict(location)
        proposed_salon = location.get('salon')
        current_salon = post.fields.get('salon', '').strip()
        if (isinstance(proposed_salon, dict) and isinstance(proposed_salon.get('value'), str)
                and current_salon and current_salon.casefold() != 'baonail.com'
                and proposed_salon['value'].strip() != current_salon):
            name = proposed_salon['value'].strip()
            explicit_name = re.search(
                r'\b(?:name\s+of\s+(?:the\s+)?salon|salon\s+name|business\s+name|ten\s+tiem|tiem\s+ten)\s*:\s*'
                + re.escape(folded(name)) + r'(?!\w)', folded(post.content))
            # A descriptor or unsupported guess cannot displace a valid stored name.
            if (len(name) > 30 or re.search(r'\b(?:sang\s+vung|can\s+tho|hiring|for\s+sale)\b', folded(name))
                    or not (explicit_name or re.search(r'\b(?:nails?|spa|salon|lounge)\b', folded(name)))):
                location.pop('salon', None)
        for key in address_repair:
            location.pop(key, None)  # Independent trusted-address provenance, not a body quote.
        if grounded_state is not None:
            # Clear source evidence, not model formatting, controls this one field.
            location.pop('state', None)
            if grounded_state['value'] != post.fields.get('state', '').strip():
                location['state'] = grounded_state
        city_item = location.get('city')
        if isinstance(city_item, dict) and set(city_item) == {'value', 'evidence'}:
            proposed_city = city_item['value']
            stored_city = post.fields.get('city', '').strip()
            stored_state = post.fields.get('state', '').strip()
            if (isinstance(proposed_city, str) and proposed_city.strip() != stored_city
                    and proposed_city.strip().casefold() == stored_city.casefold()
                    and re.fullmatch(r'[A-Z]{2}', stored_state)):
                try:
                    city_quote = evidence(post.content, city_item['evidence'])
                except EvidenceError:
                    city_quote = ''
                if (re.search(r'(?<!\w)' + re.escape(proposed_city.strip()) + r'(?!\w)', city_quote)
                        and re.search(r'(?<!\w)' + re.escape(proposed_city.strip()) +
                                      r'\s*,?\s+' + re.escape(stored_state) + r'(?![A-Za-z])', post.content)):
                    location.pop('city')  # Same proven place; preserve the stored form-option spelling.
        result['location'] = {key:item for key,item in location.items()
            if not (key in LOCATION_FIELDS and isinstance(item, dict)
                    and set(item) == {'value', 'evidence'} and isinstance(item['value'], str)
                    and (not item['value'].strip() or
                         (key in post.fields and item['value'].strip() == post.fields[key].strip())))}
    def checked(span, path):
        try:
            evidence(post.content, span)
        except EvidenceError as error:
            raise EvidenceError(str(error), path=path) from None
    facts = result.get('facts')
    if isinstance(facts, dict):
        for kind in FACT_KINDS:
            spans = facts.get(kind)
            if isinstance(spans, list) and len(spans) <= 20:
                for index, span in enumerate(spans):
                    checked(span, f'facts.{kind}[{index}]')
    if isinstance(result.get('location'), dict):
        for key in LOCATION_FIELDS:
            item = result['location'].get(key)
            if isinstance(item, dict) and 'evidence' in item:
                checked(item['evidence'], f'location.{key}.evidence')
    pay = result.get('salary')
    if (source_policy is None and 'salary_candidates' not in result and isinstance(pay, dict)
            and pay.get('mode') != 'hidden' and pay.get('evidence') is not None):
        checked(pay['evidence'], 'salary.evidence')
    return result


def entity_facts(fields, protected):
    """Keep every recognized literal entity, without requiring its surrounding prose."""
    result = dict(protected)
    for kind, keys in (('salon', ('salon',)), ('location', ('address', 'city', 'state', 'zip'))):
        minimal = []
        for index, span in enumerate(protected[kind]):
            recognized = []
            for key in keys:
                value = fields.get(key, '').strip()
                if value:
                    recognized.extend(m.group() for m in re.finditer(
                        r'(?<!\w)' + re.escape(value) + r'(?!\w)', span, re.I))
            if not recognized:
                raise EvidenceError('entity evidence must identify a source-supported field value',
                                    path=f'facts.{kind}[{index}]')
            minimal.extend(value for value in recognized if value not in minimal)
        result[kind] = minimal
    return result


def require_contact(fields):
    """Required NailHub contact fields must be established before any browser mutation.

    Limits follow the inspected CreatePostForm/ContactInformationSection. US phone
    syntax is bounded locally; no lookup guesses an address or supplies a number.
    """
    for key, limit in (('salon', 30), ('address', 500), ('city', 255), ('state', 255), ('zip', 10)):
        value = fields.get(key, '')
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise NeedsHuman('required contact location incomplete or invalid; Owner review required',
                details={'kind': 'data', 'field': key, 'current': value,
                         'action': 'Bổ sung hoặc sửa trường này từ nội dung gốc hay nguồn salon xác thực.'})
    if (not re.fullmatch(r'[A-Za-z]{2}', fields['state'].strip())
            or not re.fullmatch(r'[0-9]{5}(?:-?[0-9]{4})?', fields['zip'].strip())):
        key = 'state' if not re.fullmatch(r'[A-Za-z]{2}', fields['state'].strip()) else 'zip'
        raise NeedsHuman('state or ZIP syntax invalid; Owner review required',
            details={'kind': 'data', 'field': key, 'current': fields[key],
                     'action': 'Điền mã tiểu bang 2 chữ hoặc ZIP 5/9 chữ số từ địa chỉ nguồn.'})
    primary = fields.get('phone_primary', '')
    if not isinstance(primary, str) or not PHONE.fullmatch(primary.strip()):
        raise NeedsHuman('required main phone missing or invalid; Owner review required',
            details={'kind': 'data', 'field': 'phone_primary', 'current': primary,
                     'action': 'Điền một số điện thoại liên lạc chính hợp lệ từ nguồn gốc.'})
    digits, _ = phone_key(primary)
    if len(digits) != 10 or digits[0] not in '23456789' or digits[3] not in '23456789':
        raise NeedsHuman('required main phone missing or invalid; Owner review required',
            details={'kind': 'data', 'field': 'phone_primary', 'current': primary,
                     'action': 'Kiểm tra mã vùng và số liên lạc chính của salon.'})


def source_only_extraction(post):
    """Failover only for no-pay posts with complete fields and no unresolved source corrections.

    A model outage cannot authorize ignoring an explicit address, new phone,
    email, city/state pair or salon name. Such sources still need extraction.
    """
    policy = source_salary_policy(post.content)
    if policy is None or policy['mode'] != 'negotiated':
        return None
    try:
        require_contact(post.fields)
    except NeedsHuman:
        return None
    text = post.content
    current = ' '.join(str(post.fields.get(key, '')) for key in LOCATION_FIELDS)
    known_phones = {phone_key(m.group()) for m in PHONE.finditer(current)}
    if any(phone_key(m.group()) not in known_phones for m in PHONE.finditer(text)):
        return None
    emails = re.findall(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', text)
    if any(email.casefold() != post.fields.get('email', '').casefold() for email in emails):
        return None
    # Do not silently skip an explicit address or named salon when the extractor is down.
    if (re.search(r'\b\d+\s+[\w .#-]+\b(?:st(?:reet)?|dr(?:ive)?|rd|road|ave(?:nue)?|blvd|lane|ln|way)\b', text, re.I)
            or re.search(r'\b[A-Z][a-z]+(?: [A-Z][a-z]+)*\s*,?\s+[A-Z]{2}\b', text)
            or re.search(r'\b[\w]+\s+(?:Nails?\s+(?:Spa|Salon)|Nail\s+Salon)\b', text, re.I)
            or re.search(r'\b(?:[A-Z][a-z]+|[A-Z]{2,})(?:\s+[A-Z][a-z]+)*\s+(?:Nails?|NAILS?)\b', text)):
        return None
    return {'salary': {'mode': 'negotiated', 'kind': 'none', 'amount_text': None, 'period': None,
                       'evidence': None, 'schedule': None, 'rationale': policy['rationale']},
            'location': {}, 'facts': {key: [] for key in FACT_KINDS},
            'rationale': 'Owner: source-only no-pay fallback; complete current contact, no unresolved explicit source correction'}


def proposal(post, data, config):
    from .categories import category_proposal
    category_change = category_proposal(post, data.get('category') if isinstance(data, dict) else None)
    target_category = category_change['target']
    if not isinstance(data, dict) or not {'salary', 'location', 'facts', 'rationale'} <= set(data) or set(data) - {
            'salary', 'location', 'facts', 'rationale', 'category', 'salary_candidates'}:
        raise NeedsHuman('invalid extraction schema')
    if not isinstance(data['rationale'], str) or len(data['rationale']) > 240:
        raise NeedsHuman('invalid extraction rationale')
    facts = data['facts']
    if not isinstance(facts, dict) or set(facts) != set(FACT_KINDS):
        raise NeedsHuman('invalid invariant evidence schema')
    protected = {}
    for kind, spans in facts.items():
        if not isinstance(spans, list) or len(spans) > 20:
            raise NeedsHuman('too many fact spans')
        protected[kind] = [evidence(post.content, span) for span in spans]
    location = data['location']
    if not isinstance(location, dict) or any(key not in LOCATION_FIELDS for key in location):
        raise NeedsHuman('unsupported location field')
    address_repair = {**current_address_repair(post), **current_address_state_repair(post),
                      **source_store_name_repair(post)}
    changes = {key: item['value'] for key, item in address_repair.items()}
    for key, item in location.items():
        if key in address_repair:
            continue  # This bounded repair has its own address-field provenance.
        if not isinstance(item, dict) or set(item) != {'value', 'evidence'} or not isinstance(item['value'], str):
            raise EvidenceError('invalid location evidence', path=f'location.{key}.evidence')
        value = item['value'].strip()
        span = evidence(post.content, item['evidence'])
        if not value or len(value) > 200 or not re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', span):
            raise EvidenceError('location not present in original', path=f'location.{key}.value',
                details={'kind': 'automation', 'field': key, 'current': post.fields.get(key, ''),
                         'source': value, 'action': 'Sửa giá trị model theo nguồn gốc; chưa đổi trường đang lưu.'})
        if key in ('state', 'city'):
            city_item = location.get('city', {})
            city = city_item.get('value', post.fields.get('city', ''))
            state_item = location.get('state', {})
            state = state_item.get('value', post.fields.get('state', ''))
            pair = r'(?<!\w)' + re.escape(city.strip()) + r'\s*,?\s+' + re.escape(state.strip()) + r'(?![A-Za-z])'
            if not re.fullmatch(r'[A-Z]{2}', state.strip()) or not re.search(pair, post.content):
                raise EvidenceError('location not present in original address pair', path=f'location.{key}.value',
                    details={'kind': 'automation', 'field': key, 'current': post.fields.get(key, ''),
                             'source': value, 'action': 'Trích xuất lại cặp thành phố/tiểu bang từ địa chỉ nguồn.'})
        if key in ('phone_primary', 'phone_other'):
            if not PHONE.fullmatch(value):
                raise EvidenceError('model phone value is invalid', path=f'location.{key}.value',
                    details={'kind': 'automation', 'field': key, 'current': post.fields.get(key, ''),
                             'source': value, 'action': 'Trích xuất lại số điện thoại từ nguồn gốc.'})
            digits, extension = phone_key(value)
            if extension:
                raise EvidenceError('model phone extension is unsupported by form', path=f'location.{key}.value',
                    details={'kind': 'automation', 'field': key, 'current': post.fields.get(key, ''),
                             'source': value, 'action': 'Trích xuất lại số điện thoại không mất phần mở rộng.'})
            value = digits
        current = post.fields.get(key, '').strip()
        if current != value:
            changes[key] = value
    protected = entity_facts({**post.fields, **changes}, protected)
    if 'phone_other' in post.fields:
        contact = {**post.fields, **changes}
        other = dedup_phones(contact.get('phone_primary', ''), contact['phone_other'])
        if other != contact['phone_other']:
            changes['phone_other'] = other
    try:
        require_contact({**post.fields, **changes})
    except NeedsHuman as error:
        key = (error.details or {}).get('field')
        if key in location and key in changes:
            raise EvidenceError('model proposed contact value is invalid', path=f'location.{key}.value',
                details={'kind':'automation', 'field':key, 'current':post.fields.get(key, ''),
                         'source':changes[key],
                         'action':'Sửa đề xuất model; giá trị đã lưu chưa thay đổi.'}) from None
        raise
    try:
        if target_category == 'JobPosting' and 'salary_candidates' in data:
            pay = candidate_salary(post.content, data['salary_candidates'])
        else:
            pay = category_price_policy(post, data.get('category')) or salary(post.content, data['salary'])
    except NeedsHuman as error:
        if not error.details:
            mention = MONEY.search(post.content)
            snippet = ''
            if mention:
                try:
                    snippet = salary_context(post.content, mention.start())[:220]
                except NeedsHuman:
                    # Diagnostic generation must not rethrow the source validation failure.
                    snippet = post.content[max(0, mention.start()-60):mention.start()+160]
            source_ambiguity = str(error).startswith(('single potential', 'another monetary', 'other monetary',
                'another numeric salary', 'ambiguous salary range', 'ambiguous salary periods',
                'cannot assume tips/commission are salary'))
            error.details = {'kind': 'data' if source_ambiguity else 'automation', 'field': 'salary_amount',
                'current': post.fields.get('salary_amount', ''),
                'source': snippet if source_ambiguity else 'Mức lương/đơn vị từ model chưa được xác nhận',
                'evidence': snippet,
                'action': 'Đối chiếu mức tiền, đơn vị thời gian và điều kiện trong nguồn; sửa trích xuất hoặc chọn giá trị đúng, không tự suy đoán đơn vị.'}
        raise
    changes['salary_type'] = pay['mode']
    if pay['mode'] == 'weekly':
        precision = Decimal(1).scaleb(-config.salary_places)
        low = format(Decimal(pay['low']).quantize(precision, rounding=ROUND_DOWN), 'f')
        high = format(Decimal(pay['high']).quantize(precision, rounding=ROUND_DOWN), 'f')
        if pay.get('copy_no_period') and not pay['range']:
            changes['salary_amount'] = pay['low']
            pay['display_policy'] = ('Owner: lowest explicit salon sale asking price, literal; no conversion or rounding'
                if pay.get('sale_asking_price') else 'Owner: copy source numeric amount unchanged; no time unit inferred')
        elif pay['range']:
            if config.range_policy != 'upper_floor_100':
                raise NeedsHuman('range display requires explicit Owner policy')
            displayed = (Decimal(pay['high']) / 100).to_integral_value(rounding=ROUND_DOWN) * 100
            if displayed <= 0:
                raise NeedsHuman('range rounding produces unusable zero salary')
            changes['salary_amount'] = format(displayed, 'f')
            pay['display_policy'] = ('Owner: source upper bound rounded down to nearest USD 100 without conversion'
                if pay.get('copy_no_period') else 'Owner: weekly upper bound rounded down to nearest USD 100; original range retained in content')
            pay['displayed_weekly'] = changes['salary_amount']
        else:
            changes['salary_amount'] = low
            pay['display_policy'] = 'single amount truncated to verified field precision'
    else:
        changes['salary_amount'] = ''
    if target_category == 'SalonForSale' and changes['salary_type'] == 'weekly':
        changes['salary_type'] = 'fixed'
    expected_fields = dict(post.fields)
    expected_fields.update(changes)
    metadata_changes = {}
    if '_preserved' in expected_fields:
        preserved = json.loads(expected_fields['_preserved'])
        if pay['mode'] == 'weekly' and target_category == 'JobPosting':
            preserved['pricePeriod'] = 'Weekly'
        if preserved.get('startDate') == SENTINEL_START_DATE:
            replacement = processing_start_date()
            end = preserved.get('endDate')
            if end is not None:
                try:
                    parsed_end = datetime.fromisoformat(end.replace('Z', '+00:00'))
                    if parsed_end.tzinfo is None:
                        parsed_end = parsed_end.replace(tzinfo=timezone.utc)
                    if parsed_end <= datetime.fromisoformat(replacement).replace(tzinfo=timezone.utc):
                        raise NeedsHuman('end date conflicts with Owner sentinel start date repair',
                            details={'kind': 'data', 'field': 'endDate', 'current': end, 'source': replacement,
                                     'action': 'Chỉnh ngày kết thúc sau ngày bắt đầu hiện tại; chỉ ngày mặc định bắt đầu được tự sửa.'})
                except (TypeError, ValueError, AttributeError):
                    raise NeedsHuman('end date conflicts with Owner sentinel start date repair',
                        details={'kind': 'data', 'field': 'endDate', 'current': end, 'source': replacement,
                                 'action': 'Kiểm tra định dạng và ngày kết thúc; không tự sửa ngày kết thúc.'}) from None
            metadata_changes['startDate'] = {'old': SENTINEL_START_DATE, 'new': replacement,
                'policy': 'Owner: exact default sentinel becomes processing date at UTC midnight'}
            preserved['startDate'] = replacement
        expected_fields['_preserved'] = json.dumps(preserved, sort_keys=True, ensure_ascii=False)
    field_corrections = {key: {'current': post.fields.get(key, ''), 'source': value,
                         'evidence': evidence(post.content, location[key]['evidence'])}
                         for key, value in changes.items() if key in location}
    field_corrections.update({key: {'current': post.fields.get(key, ''), 'source': item['value'],
        'evidence': item['evidence'], 'source_field': item['source_field'],
        'policy': 'Owner: bounded fingerprint-bound stored field or source URL correction'}
        for key, item in address_repair.items()})
    if target_category != post.category:
        changes['category'] = target_category
        field_corrections['category'] = {'current': post.category, 'source': target_category,
            'evidence': '; '.join(item['quote'] for item in category_change['evidence']),
            'policy': category_change['policy']}
    return {'changes': changes, 'category': target_category, 'category_change': category_change,
            'fields': expected_fields, 'salary': pay, 'facts': protected,
            'rationale': data['rationale'], 'metadata_changes': metadata_changes, 'field_corrections': field_corrections}


def invariant_facts(original, rewritten, facts, fields, *, subset=False):
    if not isinstance(rewritten, str) or not rewritten.strip() or len(rewritten) > 30000:
        return False
    compact = lambda value: ' '.join(folded(value).split())
    email_pattern = re.compile(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', re.I)
    def contacts(text):
        return ({phone_key(m.group()) for m in PHONE.finditer(text)},
                {m.group().casefold() for m in email_pattern.finditer(text)})
    source_phones, source_emails = contacts(original)
    result_phones, result_emails = contacts(rewritten)
    field_phones, field_emails = contacts(' '.join(str(fields.get(key, '')) for key in
                                                  ('phone_primary', 'phone_other', 'email')))
    if (not result_phones <= source_phones | field_phones or not result_emails <= source_emails | field_emails
            or (not subset and (not source_phones <= result_phones or not source_emails <= result_emails))):
        return False
    # The built-in AI receives existing structured contact/location fields. Exact
    # additions of those source values are permitted; arbitrary new numbers are not.
    def numeric_tokens(text):
        text = email_pattern.sub(' ', PHONE.sub(' ', text))
        text = compact(text)
        for key in LOCATION_FIELDS:
            value = compact(fields.get(key, ''))
            if value:
                text = re.sub(r'(?<!\w)' + re.escape(value) + r'(?!\w)',
                              lambda m: m.group() if text[:m.start()].rstrip().endswith('$') else ' ', text)
        pattern = rf'(?<![\w.,]){NUMBER}\s*[kK]?(?!\w)(?:\s*[-–—]\s*\$?\s*{NUMBER}\s*[kK]?(?!\w))?'
        values = []
        for match in re.finditer(pattern, text):
            try:
                low, high, is_range = amounts(match.group())
            except NeedsHuman:
                return None
            values.extend([low, high] if is_range else [low])
        return Counter(values)
    original_numbers, rewritten_numbers = numeric_tokens(original), numeric_tokens(rewritten)
    if original_numbers is None or rewritten_numbers is None:
        return False
    if not (rewritten_numbers <= original_numbers if subset else original_numbers == rewritten_numbers):
        return False
    result = compact(rewritten)
    # Names and locations use minimal value spans. Phrased salary/schedule/benefit claims are
    # checked semantically; exact whole-sentence matching would reject faithful translations.
    if any(compact(fact) not in result for kind in ('salon', 'location') for fact in facts.get(kind, [])):
        return False
    source_folded = folded(original)
    rewritten_folded = folded(rewritten)
    if subset:
        source_folded, rewritten_folded = map(without_work_schedule, (source_folded, rewritten_folded))
    for expression in PERIODS.values():
        source_count, result_count = (len(re.findall(expression, text)) for text in (source_folded, rewritten_folded))
        if (result_count > source_count if subset else source_count != result_count):
            return False
    guarantees = r'\b(guaranteed|bao dam|bao luong|cam ket)\b'
    if len(re.findall(guarantees, rewritten_folded)) > len(re.findall(guarantees, source_folded)):
        return False
    # Existing location/contact field facts present in the original must survive AI rewriting.
    source = compact(original)
    for key in LOCATION_FIELDS:
        value = compact(fields.get(key, ''))
        if not subset and value and value in source and value not in result:
            return False
    return True


def invariant_title(post, rewritten):
    """A short title can select source facts from the whole post without copying its body."""
    return invariant_facts(post.fields.get('title', '')+'\n'+post.content, rewritten, {}, post.fields, subset=True)
