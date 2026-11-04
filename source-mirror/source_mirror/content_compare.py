"""Review candidate only: compare full article text, not title or summary.

No network, storage, or publishing integration. Near matches are review holds,
not proof of duplication. This is lexical and cannot guarantee paraphrase or
cross-language detection. Image-only differences are outside this comparison.
"""
import re
import unicodedata
from html.parser import HTMLParser

class ContentError(ValueError):
    pass

class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []
        self.paragraphs = []
        self.current = None
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in ('script', 'style'):
            raise ContentError('unsupported hidden content')
        if tag == 'p' and self.current is None:
            self.current = {'start': len(self.parts), 'links': 0, 'link_depth': 0, 'outside': []}
        if self.current is not None and tag == 'a':
            self.current['links'] += 1
            self.current['link_depth'] += 1
        if tag in ('p', 'div', 'li', 'br', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append(' ')
    def handle_endtag(self, tag):
        tag = tag.lower()
        if self.current is not None and tag == 'a':
            self.current['link_depth'] -= 1
        if tag == 'p' and self.current is not None:
            self.current['end'] = len(self.parts)
            self.paragraphs.append(self.current)
            self.current = None
        if tag in ('p', 'div', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append(' ')
    def handle_data(self, data):
        self.parts.append(data)
        if self.current is not None and self.current['link_depth'] == 0:
            self.current['outside'].append(data)

def article_words(markup):
    if not isinstance(markup, str) or not markup.strip():
        raise ContentError('body unavailable')
    parser = _Text()
    parser.feed(markup)
    parser.close()
    if parser.current is not None:
        raise ContentError('unclosed paragraph')
    # Remove only the last paragraph when it is exactly a known provenance
    # label and one link, with no following text. Do not strip other notices.
    if parser.paragraphs:
        p = parser.paragraphs[-1]
        outside = ' '.join(''.join(p['outside']).split()).casefold()
        tail = ''.join(parser.parts[p['end']:]).strip()
        if not tail and p['links'] == 1 and outside in ('source:', 'reference:', 'nguồn tham khảo:'):
            parser.parts = parser.parts[:p['start']]
    value = unicodedata.normalize('NFKC', ''.join(parser.parts)).casefold()
    words = tuple(re.findall(r'\w+|[^\w\s]', value, flags=re.UNICODE))
    if not words:
        raise ContentError('body has no text')
    return words

def compare(left_html, right_html):
    """Return exact, near_hold, or distinct; compare same-language bodies only."""
    left, right = article_words(left_html), article_words(right_html)
    if left == right:
        return {'decision': 'exact', 'score': 1.0}
    left = tuple(x for x in left if any(c.isalnum() for c in x))
    right = tuple(x for x in right if any(c.isalnum() for c in x))
    if not left or not right:
        return {'decision': 'near_hold', 'score': 0.0}
    width = min(8, len(left), len(right))
    def shingles(words):
        return {words[i:i+width] for i in range(len(words)-width+1)}
    a, b = shingles(left), shingles(right)
    score = len(a & b) / min(len(a), len(b))
    return {'decision': 'near_hold' if score >= 0.85 else 'distinct', 'score': score}




def has_exact_title(listing, title):
    """Check the complete title-search result; fail closed on truncated results."""
    if not isinstance(listing, dict) or not isinstance(listing.get('items'), list):
        raise ContentError('title search unavailable')
    items = listing['items']
    if type(listing.get('totalCount')) is not int or listing['totalCount'] != len(items):
        raise ContentError('title search incomplete')
    def normalize(value):
        return ' '.join(unicodedata.normalize('NFKC', value).casefold().split())
    target = normalize(title)
    if not target: raise ContentError('title unavailable')
    for item in items:
        if not isinstance(item, dict) or item.get('status') not in ('Published', 'Draft', 'Archived'):
            raise ContentError('title search status unavailable')
        if item['status'] != 'Published':
            continue
        translations = item.get('translations')
        if not isinstance(translations, list) or not translations: raise ContentError('title unavailable')
        for tr in translations:
            if not isinstance(tr, dict) or not isinstance(tr.get('title'), str): raise ContentError('title unavailable')
            if normalize(tr['title']) == target: return True
    return False
