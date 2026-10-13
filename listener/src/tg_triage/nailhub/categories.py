"""Source-backed category projection; enums verified against NailHub's real form/API."""
import re
from .validation import NeedsHuman, folded

SUPPORTED_CATEGORIES = ('JobPosting', 'SalonForSale', 'Portfolio')
CATEGORY_LABELS = dict(zip(SUPPORTED_CATEGORIES, ('Cần thợ nail', 'Sang nhượng tiệm', 'Tìm việc')))
APPROVED_CATEGORY_REPAIRS = frozenset(('987c7202-2124-4e6d-90d0-05e55dff1f33',
                                     '03908741-40a8-4579-b941-374a1a9c54d2'))
SALE = re.compile(r'\b(?:ban|sang(?:\s+nhuong)?|chuyen\s+nhuong|nhuong\s+lai)\s+(?:tiem|salon)\b'
                  r'|\b(?:nail\s+)?(?:salon|business)\s+for\s+(?:sale|transfer)\b'
                  r'|\bselling\s+(?:(?:a|my|our)\s+)?(?:nail\s+)?salon\b')
NEGATIVE_SALE = re.compile(r'\b(?:not|never|no\s+longer)\s+(?:currently\s+)?for\s+(?:sale|transfer)\b'
                           r'|\b(?:khong|chua)\s+(?:(?:can|muon)\s+)?(?:ban|sang(?:\s+nhuong)?)\s+(?:tiem|salon)\b'
                           r'|\b(?:tiem|salon)\s+(?:khong|chua)\s+(?:ban|sang)\b')
RECRUIT = re.compile(r'\b(?:can(?:\s+tim)?|tuyen(?:\s+dung)?|tim)\s+tho\b'
                     r'|\b(?:hiring|recruiting|looking\s+for|need(?:ed)?|wanted)\s+(?:(?:a|nail|more)\s+)*(?:technicians?|workers?|staff)\b'
                     r'|\bnail\s+technicians?\s+wanted\b')
SEEKER = re.compile(r'\b(?:toi|em|minh)\s+(?:dang\s+)?(?:can\s+)?(?:tim|kiem)\s+(?:viec|cho\s+lam)\b'
                    r'|\bi(?:\s+am|[’\x27]m)?\s+(?:am\s+)?(?:looking\s+for|seeking|need)\s+(?:a\s+)?job\b')


def _quote(raw, match):
    boundaries, offset = {}, 0
    for index, char in enumerate(raw):
        normalized = folded(char)
        if normalized:
            boundaries.setdefault(offset, index)
        offset += len(normalized)
    boundaries[offset] = len(raw)
    return raw[boundaries[match.start()]:boundaries[match.end()]]


def category_proposal(post):
    if post.category not in SUPPORTED_CATEGORIES:
        raise NeedsHuman('unsupported status or category')
    found = {'JobPosting': [], 'SalonForSale': [], 'Portfolio': []}
    negative_sale = False
    for field, raw in (('title', post.fields.get('title', '')), ('content', post.content)):
        text = folded(raw)
        negative_sale |= bool(NEGATIVE_SALE.search(text))
        for category, regex in (('SalonForSale', SALE), ('JobPosting', RECRUIT), ('Portfolio', SEEKER)):
            for match in regex.finditer(text):
                prefix = re.split(r'[\n;.!?]', text[:match.start()])[-1]
                if re.search(r'\b(?:not|never|khong|chua)\b', prefix):
                    continue
                if category == 'SalonForSale' and re.match(r'sang\s+tiem\b', match.group()):
                    suffix = re.split(r'[\n;.!?]', text[match.end():])[0]
                    if re.search(r'\b(?:lam|lam\s+viec|khac\s+lam)\b', suffix):
                        continue  # Moving to another workplace is not selling a business.
                found[category].append({'field': field, 'quote': _quote(raw, match)})
        if field == 'title':
            match = re.match(r'\s*(?:(?:tho\s+nail\s+)?can\s+)?tim\s+viec\b', text)
            if match:
                found['Portfolio'].append({'field': field, 'quote': _quote(raw, match)})
    if found['SalonForSale'] and not negative_sale:
        target = 'SalonForSale'
    elif ((found['JobPosting'] and found['Portfolio']) or (found['SalonForSale'] and negative_sale)
          or not any(found.values())):
        raise NeedsHuman('category intent is ambiguous', details={'kind': 'data', 'field': 'category',
            'current': CATEGORY_LABELS[post.category], 'source': 'Mục đích tuyển thợ / bán tiệm / tìm việc chưa rõ hoặc mâu thuẫn',
            'evidence': post.fields.get('title', ''),
            'action': 'Đối chiếu tiêu đề và nội dung gốc; xác nhận mục đích chính rồi chọn đúng Danh mục.'})
    else:
        target = 'Portfolio' if found['Portfolio'] else 'JobPosting'
    return {'current': post.category, 'target': target, 'source_hash': post.fingerprint,
            'evidence': found[target][:4], 'policy': 'Owner: original intent; explicit salon sale precedes recruitment'}


def category_change_valid(original, expected):
    target = expected.get('category')
    if target == original.category:
        return target in SUPPORTED_CATEGORIES
    try:
        context = expected.get('audit', {})
        if context.get('category_only_repair'):
            return category_repair_valid(original, expected)
        audit = context.get('category_change')
        return audit == category_proposal(original) and target == audit['target']
    except (NeedsHuman, KeyError, TypeError):
        return False


def category_repair_expected(post, source):
    """Exactly the two Owner-authorized Approved records; no general Approved path."""
    if post.id not in APPROVED_CATEGORY_REPAIRS or post.status != 'Approved' or post.category != 'JobPosting':
        raise NeedsHuman('approved category repair is not authorized')
    if (source.id != post.id or source.status != 'Draft' or source.category not in SUPPORTED_CATEGORIES):
        raise NeedsHuman('approved category repair original source is unavailable')
    change = category_proposal(source)
    change = {**change, 'current': post.category, 'mutation_hash': post.fingerprint}
    if change['target'] != 'SalonForSale':
        raise NeedsHuman('approved category repair source does not establish sale')
    fields = dict(post.fields)
    if fields['salary_type'] == 'weekly':
        fields['salary_type'] = 'fixed'
    elif fields['salary_type'] != 'negotiated':
        raise NeedsHuman('approved category repair price type is unsupported')
    return {**post.snapshot(), 'category': change['target'], 'fields': fields,
            'audit': {'category_change': change, 'category_only_repair': True,
                      'category_original_source': source.snapshot()}}


def category_repair_valid(post, expected):
    try:
        from .validation import Posting
        source = Posting(**expected['audit']['category_original_source'])
        return expected == category_repair_expected(post, source)
    except (NeedsHuman, KeyError, TypeError):
        return False
