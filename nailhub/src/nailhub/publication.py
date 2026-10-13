"""Owner-authorized publication transitions, bound to exact fresh source snapshots."""
import json
from datetime import datetime
from .categories import SUPPORTED_CATEGORIES
from .validation import NeedsHuman, Posting


def public_value(post):
    value = json.loads(post.fields.get('_preserved', '{}')).get('isPublic')
    return value if type(value) is bool else None


def eligible(post):
    return bool(post and type(public_value(post)) is bool and post.category in SUPPORTED_CATEGORIES and
                (post.status == 'Draft' or (post.status == 'Approved' and public_value(post) is False)))


def rewrite_audit(post):
    if post.status != 'Approved' or not eligible(post):
        raise NeedsHuman('unpublished Approved source is unavailable')
    return {'kind': 'approved_unpublished_rewrite', 'source_hash': post.fingerprint, 'source': post.snapshot()}


def rewrite_valid(post, expected):
    try:
        audit = expected['audit']['publication_workflow']
        return (audit == rewrite_audit(post) and expected['status'] == 'Approved'
                and json.loads(expected['fields']['_preserved']).get('isPublic') is False)
    except (KeyError, TypeError, ValueError, NeedsHuman):
        return False


def publication_expected(post, *, approve=False, audit=None):
    if (approve and post.status != 'Draft') or (not approve and
            (post.status != 'Approved' or public_value(post) is not False)):
        raise NeedsHuman('publication transition source is not eligible')
    if type(public_value(post)) is not bool:
        raise NeedsHuman('publication source visibility is unknown')
    if not approve and not verified_rewrite(post, audit or {}):
        raise NeedsHuman('publication requires verified rewrite Save audit')
    fields = dict(post.fields)
    metadata = json.loads(fields['_preserved']); metadata['isPublic'] = True
    fields['_preserved'] = json.dumps(metadata, sort_keys=True, ensure_ascii=False)
    transition = {'kind': 'approve' if approve else 'publish', 'source_hash': post.fingerprint,
                  'source': post.snapshot(), 'old': public_value(post), 'new': True}
    return {**post.snapshot(), 'status': 'Approved', 'fields': fields,
            'audit': {**(audit or {}), 'publication_transition': transition}}


def verified_rewrite(post, audit):
    try:
        workflow = audit['publication_workflow']
        source = Posting(**workflow['source'])
        receipt = audit['rewrite_save_verified']
        return (workflow == rewrite_audit(source) and post.id == source.id
                and post.status == 'Approved' and public_value(post) is False
                and receipt == {'source_hash': source.fingerprint, 'saved_hash': post.fingerprint})
    except (KeyError, TypeError, ValueError, NeedsHuman):
        return False


def publication_valid(post, expected, *, approve=False):
    try:
        audit = expected['audit']; transition = audit['publication_transition']
        other = {key: value for key, value in audit.items() if key != 'publication_transition'}
        return expected == publication_expected(post, approve=approve, audit=other)
    except (KeyError, TypeError, ValueError, NeedsHuman):
        return False


def approval_readback(expected):
    return publication_readback(expected, approve=True)


def publication_readback(expected, *, approve=False):
    try:
        transition = expected['audit']['publication_transition']
        source = Posting(**transition['source'])
        return publication_valid(source, expected, approve=approve)
    except (KeyError, TypeError, ValueError):
        return False


def unchanged_valid_date(post, expected):
    try:
        source = json.loads(post.fields['_preserved'])['startDate']
        wanted = json.loads(expected['fields']['_preserved'])['startDate']
        if source != wanted or not isinstance(source, str):
            return False
        datetime.fromisoformat(source.replace('Z', '+00:00'))
        return True
    except (KeyError, TypeError, ValueError):
        return False
