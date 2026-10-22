"""Staging publication contracts. No network or browser state is trusted implicitly."""
import hashlib
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit

from .config import (API_ORIGIN, SITE_ORIGIN, STAGING_API_ORIGIN, STAGING_SITE_ORIGIN,
                     STAGING_MEDIA_ORIGIN, PRODUCTION_MEDIA_ORIGIN)


class AdminError(RuntimeError):
    """A sanitized operation code, never a remote response or credential."""


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def binding(config):
    c = config.contract
    origins = {'staging': (STAGING_SITE_ORIGIN, STAGING_API_ORIGIN), 'production': (SITE_ORIGIN, API_ORIGIN)}
    if (c.site_origin, c.api_origin) != origins.get(c.environment):
        raise AdminError('environment_origin_mismatch')
    result = {'site_origin': c.site_origin, 'api_origin': c.api_origin, 'environment': c.environment}
    if c.media_origin:
        u = urlsplit(c.media_origin)
        if (u.scheme != 'https' or u.netloc != u.hostname or u.path or u.query or u.fragment or
                (c.environment == 'staging' and c.media_origin != STAGING_MEDIA_ORIGIN) or
                (c.environment == 'production' and c.media_origin != PRODUCTION_MEDIA_ORIGIN)):
            raise AdminError('environment_media_mismatch')
        result['media_origin'] = c.media_origin
    return result


def media_origin(config):
    binding(config)
    return config.contract.media_origin or (STAGING_MEDIA_ORIGIN if config.contract.environment == 'staging' else '')


def instant(value):
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (AttributeError, TypeError, ValueError):
        raise AdminError('invalid_timestamp') from None


def _minute_utc(value):
    """Canonical comparable instant: UTC, floored to the minute (the admin form's datetime-local precision)."""
    return instant(value).astimezone(timezone.utc).replace(second=0, microsecond=0)


def safe_url(value, config):
    if not isinstance(value, str) or '\\' in value or any(ord(c) < 33 or ord(c) == 127 for c in value):
        raise AdminError('invalid_media_url')
    u = urlsplit(value)
    if (u.scheme != 'https' or u.netloc not in (urlsplit(config.contract.api_origin).netloc,
                                             urlsplit(config.contract.site_origin).netloc,
                                             urlsplit(media_origin(config)).netloc)
            or u.username or u.password or u.fragment or 'placeholder' in value.lower()):
        raise AdminError('invalid_media_url')
    return value


class Markup(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.events, self.images = [], []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        # Blog-only Quill blot's EXACT internal DOM identity. Persisted HTML has
        # neither attribute. No other classes/attrs/semantic structure are ignored.
        if tag == 'figure' and values.get('class') == 'blog-image-figure' and values.get('contenteditable') == 'false':
            values.pop('class')
            values.pop('contenteditable')
        # Standalone BlogImage uses only this exact runtime identity. Its
        # title/loading and every other image attribute remain comparable.
        if tag == 'img' and values.get('class') == 'blog-standalone-image':
            values.pop('class')
        # Quill adds this exact safe outbound-link policy. Preserve href/text,
        # any extra rel token and all unknown targets/attributes.
        if tag == 'a' and values.get('rel') == 'noopener noreferrer':
            values.pop('rel')
            if values.get('target') == '_blank':
                values.pop('target')
        if tag in ('script', 'iframe', 'object', 'embed', 'style') or any(k.startswith('on') for k in values):
            raise AdminError('unsafe_markup')
        for key in ('src', 'href'):
            if key in values and not str(values[key]).startswith('https://'):
                raise AdminError('unsafe_markup_url')
        self.events.append(('open', tag, sorted(values.items())))
        if tag == 'img':
            self.images.append(values)

    def handle_endtag(self, tag):
        self.events.append(('close', tag))

    def handle_data(self, data):
        if data.strip():
            self.events.append(('text', re.sub(r'\s+', ' ', data)))


def same_html(left, right):
    def canonical(text):
        events = Markup(text).events
        # Quill needs one caret paragraph after a terminal atomic figure/list.
        # Only its exact attr-free empty form at top level is presentation-only.
        caret = [('open','p',[]),('open','br',[]),('close','p')]
        if (events[-3:] != caret or len(events) < 4 or
                events[-4] not in [('close','figure'),('close','ul'),('close','ol')]):
            return events
        stack = []
        void = {'area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'}
        for event in events[:-3]:
            if event[0] == 'open' and event[1] not in void:
                stack.append(event[1])
            elif event[0] == 'close':
                if not stack or stack.pop() != event[1]:
                    return events
        return events[:-3] if not stack else events
    return canonical(left) == canonical(right)


def media(value, config):
    if not isinstance(value, dict):
        raise AdminError('invalid_media')
    safe_url(value.get('imageUrl'), config)
    return {k: safe_url(value[k], config) if k.endswith('Url') and value.get(k) else value.get(k, '')
            for k in ('imageUrl', 'imageRelativePath', 'thumbnailUrl', 'thumbnailRelativePath')}


def snapshot(payload, config):
    """Strict source-backed comparable blog fields; server-generated media IDs are excluded."""
    binding(config)
    try:
        translations = payload['translations']
        if not isinstance(translations, list) or len(translations) != 1 or translations[0]['languageCode'].upper() != 'VI':
            raise AdminError('vi_only')
        vi = translations[0]
        fields = {k: vi[k] for k in ('title', 'summary', 'content', 'metaTitle', 'metaDescription')}
        limits = {'title': 200, 'summary': 500, 'metaTitle': 70, 'metaDescription': 160}
        if any(not isinstance(v, str) for v in fields.values()) or any(len(fields[k]) > n for k, n in limits.items()):
            raise AdminError('invalid_translation')
        if len(fields['title'].strip()) < 5 or not fields['content'].strip():
            raise AdminError('invalid_translation')
        parsed = Markup(fields['content'])
        images = [media(x, config) for x in payload['images']]
        if len(set(x['imageUrl'] for x in images)) != len(images):
            raise AdminError('duplicate_images')
        if not 3 <= len(images) <= 5 or len(parsed.images) != len(images):
            raise AdminError('real_images_required')
        for img, item in zip(parsed.images, images):
            if safe_url(img.get('src'), config) != item['imageUrl'] or not 5 <= len(img.get('alt', '').split()) <= 15:
                raise AdminError('image_alt_or_identity')
        if not re.fullmatch(r'[0-9a-fA-F-]{36}', payload['categoryId']):
            raise AdminError('invalid_category')
        tags = payload['hashtags']
        if not isinstance(tags, list) or not 3 <= len(tags) <= 5 or any(not isinstance(t, str) or not 2 <= len(t) <= 50 for t in tags):
            raise AdminError('invalid_hashtags')
        if payload.get('isPublic') is not True or payload.get('isFeatured') is not False:
            raise AdminError('invalid_visibility')
        if payload.get('videoUrls') or payload.get('externalSourceUrl') or payload.get('expiresAt'):
            raise AdminError('unsupported_fields')
        return {'categoryId': payload['categoryId'], 'publishedAt': _minute_utc(payload['publishedAt']).isoformat(),
                'isPublic': True, 'isFeatured': False, 'coverImage': media(payload['coverImage'], config),
                'images': images, 'hashtags': tags, 'translations': [{'languageCode': 'VI', **fields}]}
    except (KeyError, TypeError, ValueError):
        raise AdminError('invalid_blog_schema') from None


def matches(actual, expected, config, *, status='Draft', post_id=None, published=False):
    try:
        if actual.get('status') != status or (post_id and actual.get('id') != post_id):
            return False
        left, right = snapshot(actual, config), snapshot(expected, config)
        if published:
            if abs((instant(left.pop('publishedAt')) - instant(right.pop('publishedAt'))).total_seconds()) > 900:
                return False
        a, b = left['translations'][0], right['translations'][0]
        if not same_html(a.pop('content'), b.pop('content')):
            return False
        return left == right
    except (AdminError, AttributeError):
        return False


def row_fingerprint(row, config):
    return digest({'environment': binding(config), 'admin_id': row['admin_id'], 'slot_at': row['slot_at'],
                   'document': json.loads(row['draft_json'])})


def document_matches(row, payload, config):
    """Bind the reviewed writer document to exactly the fields sent through the admin UI."""
    document = json.loads(row['draft_json'])
    vi = snapshot(payload, config)['translations'][0]
    if document.get('admin_category_id') != payload['categoryId']:
        raise AdminError('document_category_mismatch')
    for local, remote in (('title', 'title'), ('summary', 'summary'), ('meta_title', 'metaTitle'),
                          ('meta_description', 'metaDescription')):
        if document.get(local) != vi[remote]:
            raise AdminError('document_payload_mismatch')
    if (document.get('hashtags') != payload['hashtags'] or
            not same_html(document.get('content_html', ''), vi['content'])):
        raise AdminError('document_payload_mismatch')
    if instant(row['slot_at']) != instant(payload['publishedAt']):
        raise AdminError('slot_payload_mismatch')
    return document


def approval_fingerprint(row, op, config):
    return digest({'row': row_fingerprint(row, config), 'payload': snapshot(json.loads(op['payload']), config),
                   'receipt': json.loads(op['receipt'])})
