"""Deterministic source evidence and fact checks. Model suggestions grant no authority."""

import hashlib
import json
import re
import unicodedata
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


class EvidenceError(NeedsHuman):
    """Only invalid extraction evidence may receive one bounded model correction."""
    def __init__(self, message, *, path='evidence'):
        super().__init__(message)
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
MONEY = re.compile(rf'\$\s*{NUMBER}(?:\s*[kK])?|(?<!\w){NUMBER}\s*[kK](?!\w)')
PERIODS = {
    'week': r'(?:/\s*week\b|\b(?:per|a|each)\s+week\b|\bweekly\b|/\s*tuan\b|\b(?:moi|mot)\s+tuan\b)',
    'month': r'(?:/\s*month\b|\b(?:per|a|each)\s+month\b|\bmonthly\b|/\s*thang\b|\b(?:moi|mot)\s+thang\b)',
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


def salary(original, value):
    if not isinstance(value, dict) or set(value) != {'mode', 'kind', 'amount_text', 'period', 'evidence', 'schedule', 'rationale'}:
        raise NeedsHuman('invalid salary schema')
    rationale = value['rationale']
    if not isinstance(rationale, str) or len(rationale) > 240:
        raise NeedsHuman('invalid bounded rationale')
    mode = value['mode']
    source = folded(original)
    if mode == 'hidden':
        if (value['kind'] != 'none' or value['amount_text'] is not None or value['period'] is not None
                or value['schedule'] is not None):
            raise NeedsHuman('hidden pay schema contains a salary claim')
        if (MONEY.search(original) or re.search(r'\b(salary|pay|luong|income|thu nhap|commission|tip[s]?|negotiable|thuong luong|thoa thuan)\b', source)
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
    original_clause = salary_context(original, raw_start)
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
    if any(not (start <= m.start() and m.end() <= end) for m in MONEY.finditer(original)):
        raise NeedsHuman('other monetary clause requires review')
    # Compare against the selected amount itself, not just its enclosing evidence:
    # one long quote can contain several conflicting bare salary clauses.
    clauses = numeric_pay_clauses(original)
    selected_start = len(folded(original[:raw_start]))
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
LOCATION_FIELDS = ('salon', 'address', 'city', 'state', 'zip', 'email')


def normalize_extraction(post, data):
    """Drop empty/no-op location suggestions, then validate literal evidence with safe paths.

    Current field equality proves only that no field change is requested. It does
    not establish that the value appears in original content or excuse conflicts.
    An empty suggestion never requests deletion and leaves current fields intact.
    """
    if not isinstance(data, dict):
        return data  # proposal() owns strict schema validation.
    result = dict(data)
    location = data.get('location')
    if isinstance(location, dict):
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
    if isinstance(pay, dict) and pay.get('mode') != 'hidden' and 'evidence' in pay:
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
            raise NeedsHuman('required contact location incomplete or invalid; Owner review required')
    if (not re.fullmatch(r'[A-Za-z]{2}', fields['state'].strip())
            or not re.fullmatch(r'[0-9]{5}(?:-?[0-9]{4})?', fields['zip'].strip())):
        raise NeedsHuman('state or ZIP syntax invalid; Owner review required')
    primary = fields.get('phone_primary', '')
    if not isinstance(primary, str) or not PHONE.fullmatch(primary.strip()):
        raise NeedsHuman('required main phone missing or invalid; Owner review required')
    digits, _ = phone_key(primary)
    if len(digits) != 10 or digits[0] not in '23456789' or digits[3] not in '23456789':
        raise NeedsHuman('required main phone missing or invalid; Owner review required')


def proposal(post, data, config):
    if not isinstance(data, dict) or set(data) != {'salary', 'location', 'facts', 'rationale'}:
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
    changes = {}
    for key, item in location.items():
        if not isinstance(item, dict) or set(item) != {'value', 'evidence'} or not isinstance(item['value'], str):
            raise NeedsHuman('invalid location evidence')
        value = item['value'].strip()
        if not value or len(value) > 200 or value != evidence(post.content, item['evidence']).strip():
            raise NeedsHuman('location not present in original')
        current = post.fields.get(key, '').strip()
        if current and current != value:
            raise NeedsHuman('location conflicts with current field')
        if not current:
            changes[key] = value
    protected = entity_facts({**post.fields, **changes}, protected)
    if 'phone_other' in post.fields:
        other = dedup_phones(post.fields.get('phone_primary', ''), post.fields['phone_other'])
        if other != post.fields['phone_other']:
            changes['phone_other'] = other
    require_contact({**post.fields, **changes})
    pay = salary(post.content, data['salary'])
    changes['salary_type'] = pay['mode']
    if pay['mode'] == 'weekly':
        precision = Decimal(1).scaleb(-config.salary_places)
        low = format(Decimal(pay['low']).quantize(precision, rounding=ROUND_DOWN), 'f')
        high = format(Decimal(pay['high']).quantize(precision, rounding=ROUND_DOWN), 'f')
        if pay['range']:
            if config.range_policy != 'upper_floor_100':
                raise NeedsHuman('range display requires explicit Owner policy')
            displayed = (Decimal(pay['high']) / 100).to_integral_value(rounding=ROUND_DOWN) * 100
            if displayed <= 0:
                raise NeedsHuman('range rounding produces unusable zero salary')
            changes['salary_amount'] = format(displayed, 'f')
            pay['display_policy'] = 'Owner: weekly upper bound rounded down to nearest USD 100; original range retained in content'
            pay['displayed_weekly'] = changes['salary_amount']
        else:
            changes['salary_amount'] = low
            pay['display_policy'] = 'single amount truncated to verified field precision'
    else:
        changes['salary_amount'] = ''
    expected_fields = dict(post.fields)
    expected_fields.update(changes)
    metadata_changes = {}
    if '_preserved' in expected_fields:
        preserved = json.loads(expected_fields['_preserved'])
        if pay['mode'] == 'weekly':
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
                        raise NeedsHuman('end date conflicts with Owner sentinel start date repair')
                except (TypeError, ValueError, AttributeError):
                    raise NeedsHuman('end date conflicts with Owner sentinel start date repair') from None
            metadata_changes['startDate'] = {'old': SENTINEL_START_DATE, 'new': replacement,
                'policy': 'Owner: exact default sentinel becomes processing date at UTC midnight'}
            preserved['startDate'] = replacement
        expected_fields['_preserved'] = json.dumps(preserved, sort_keys=True, ensure_ascii=False)
    return {'changes': changes, 'fields': expected_fields, 'salary': pay, 'facts': protected,
            'rationale': data['rationale'], 'metadata_changes': metadata_changes}


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
