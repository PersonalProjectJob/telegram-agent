"""Staged local mirror of Published admin records; never publishes or authenticates.

The caller must hold the existing worker/browser lock during refresh and use the
same authenticated browser session as the publisher. This module is not wired
to the running service. A registry is not an atomic lock on external writers.
"""
import hashlib
import json
import os
import concurrent.futures
from datetime import datetime, timezone
from pathlib import Path
import re
import tempfile
import time
from urllib.parse import parse_qs, quote, urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .localization import QualityHold
from .original_duplicate_scan import _one
from .official_duplicate_scan import _response

VERSION = 'published-registry-v1'


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _directory(root):
    root = Path(root)
    if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
        raise ValueError('registry root must be an absolute non-symlink path')
    base = root / 'published-registry'
    for path in (base, base / 'generations'):
        if path.is_symlink():
            raise ValueError('registry directory must not be symlinked')
        path.mkdir(mode=0o700, exist_ok=True)
        if not path.is_dir():
            raise ValueError('registry path is not a directory')
        if os.name == 'posix':
            os.chmod(path, 0o700)
    return base


def _observe(page, deadline):
    """Capture limited metadata alongside the existing validated list scan."""
    metadata, errors, pages = {}, [], set()
    def watch(response):
        try:
            q = parse_qs(urlsplit(response.url).query)
            number = int(q.get('pageNumber', ['0'])[0])
            if not _response(response, number): return
            if number in pages: raise ValueError('duplicate admin page observation')
            pages.add(number)
            body = response.json()
            for item in body['items']:
                if item.get('status') != 'Published': continue
                ident = item['id']
                if ident in metadata: raise ValueError('duplicate admin identity')
                metadata[ident] = {'slug': item.get('slug'), 'published_at': item.get('publishedAt'),
                                   'source_url': item.get('externalSourceUrl'), 'is_public': item.get('isPublic')}
        except Exception as exc:
            errors.append(type(exc).__name__)
    page.on('response', watch)
    try: observation = _one(page, deadline)
    finally: page.remove_listener('response', watch)
    if errors: raise ValueError('admin metadata observation failed')
    expected_pages = observation[0][1]
    if pages != set(range(1, max(1, expected_pages) + 1)):
        raise ValueError('incomplete admin metadata observation')
    return observation, metadata


def _records(observation, metadata):
    try:
        (total, pages), raw = observation
    except (TypeError, ValueError) as exc:
        raise ValueError('invalid admin observation') from exc
    if type(total) is not int or total < 0 or type(pages) is not int or pages < 0 or not isinstance(raw, tuple):
        raise ValueError('invalid admin observation')
    if not isinstance(metadata, dict): raise ValueError('invalid admin metadata')
    result, ids = [], set()
    for ident, translations in raw:
        if not isinstance(ident, str) or not ident.strip() or ident in ids or not isinstance(translations, tuple) or not translations:
            raise ValueError('invalid published identity')
        ids.add(ident)
        titles, bodies = {}, {}
        for language, title, body in translations:
            if language not in ('VI', 'EN') or language in titles or not isinstance(title, str) or not title.strip() or not isinstance(body, str) or not body.strip():
                raise ValueError('invalid published translation')
            titles[language], bodies[language] = title, body
        info = metadata.get(ident)
        if not isinstance(info, dict) or set(info) != {'slug', 'published_at', 'source_url', 'is_public'}:
            raise ValueError('published metadata missing')
        slug, published_at, source_url, public = (info['slug'],info['published_at'],info['source_url'],info['is_public'])
        if not isinstance(slug, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9-]{0,240}',slug): slug = None
        if published_at is not None:
            try:
                parsed = datetime.fromisoformat(published_at.replace('Z','+00:00'))
                if parsed.tzinfo is None: published_at = None
            except (ValueError,AttributeError): published_at = None
        if source_url is not None:
            if not isinstance(source_url,str): source_url = None
            else:
                try:
                    parsed_url = urlsplit(source_url)
                    # Never persist credentials or query parameters. Do not invent
                    # an alternate source when a URL requires redaction.
                    if parsed_url.scheme not in ('http','https') or not parsed_url.hostname or parsed_url.username or parsed_url.password or parsed_url.query or parsed_url.fragment: source_url = None
                except ValueError: source_url = None
        result.append({'id': ident, 'status': 'Published', 'titles': titles,
                       'bodies': bodies, 'source_url': source_url,
                       'public_url': ('https://nailhub.ai/blog/'+quote(slug,safe='-')) if slug and public is True else None,
                       'published_at': published_at})
    if len(result) > total or ids != set(metadata):
        raise ValueError('published count exceeds observed total')
    return total, pages, result


def _atomic_write(path, data):
    if path.is_symlink():
        raise ValueError('registry file must not be symlinked')
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.registry-', delete=False) as f:
            name = f.name
            if os.name == 'posix':
                os.fchmod(f.fileno(), 0o600)
            f.write(data); f.flush(); os.fsync(f.fileno())
        os.replace(name, path); name = None
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)
    finally:
        if name is not None:
            os.unlink(name)


def _save_observation(root, observation, metadata, *, clock=None, checks=None):
    total, pages, records = _records(observation, metadata)
    if checks is not None:
        if not isinstance(checks, dict) or set(checks) != {r['id'] for r in records}:
            raise ValueError('incomplete public HTTP verification')
        records = [dict(record, http_status=200) for record in records if checks[record['id']] == 200]
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    if now.tzinfo is None:
        raise ValueError('timezone-aware clock required')
    at = now.astimezone(timezone.utc).isoformat()
    payload = {'version': VERSION, 'observed_at_utc': at,
               'admin_total_count': total, 'admin_total_pages': pages,
               'published_count': len(records), 'records': records,
               'scope': 'published-only', 'metadata_note': 'public URL derived from observed public slug; not individually verified' if checks is None else 'public URLs GET checked; only HTTP 200 retained'}
    if checks is not None:
        payload['http_verification'] = {'checked_at_utc': at, 'checked': len(checks),
                                        'excluded': [{'id': key, 'status': status} for key, status in checks.items() if status != 200]}
    digest = hashlib.sha256(_encoded(payload)).hexdigest()
    data = _encoded({'sha256': digest, 'snapshot': payload}) + b'\n'
    base = _directory(root)
    generation = now.astimezone(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '-' + digest + '.json'
    _atomic_write(base / 'generations' / generation, data)
    _atomic_write(base / 'current.json', data)
    return {'published_count': len(records), 'sha256': digest,
            'observed_at_utc': at, 'generation': generation}


def refresh_from_admin(root, page, *, clock=None, scanner=_observe):
    """Store two consistent, complete admin observations; caller holds lock.

    _one validates pagination and filters to Published. Failure does not replace
    current.json. A successful scan is observational, not a server transaction.
    """
    deadline = time.monotonic() + 90
    first, second = scanner(page, deadline), scanner(page, deadline)
    if first != second:
        raise QualityHold('admin list changed between scans')
    observation, metadata = first
    return _save_observation(root, observation, metadata, clock=clock)


class _PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url = urlsplit(newurl)
        if url.scheme != 'https' or url.hostname != 'nailhub.ai' or url.username or url.password or url.port not in (None, 443):
            raise ValueError('public redirect outside Nailhub')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _public_status(record, deadline_monotonic):
    """Read only the final HTTP status; timeout/error never counts as 200."""
    url = record.get('public_url')
    try:
        parsed = urlsplit(url or '')
        if parsed.scheme != 'https' or parsed.hostname != 'nailhub.ai' or not parsed.path.startswith('/blog/') or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None,443):
            return None
        opener = build_opener(_PublicRedirect())
        for attempt in range(2):
            try:
                remaining = deadline_monotonic - time.monotonic()
                if remaining <= 0: return None
                request = Request(url, headers={'User-Agent':'Mozilla/5.0 NailhubRegistryHttpCheck/1.0','Accept':'text/html'}, method='GET')
                with opener.open(request, timeout=min(15, remaining)) as response:
                    return response.status
            except HTTPError as exc:
                if exc.code >= 500 and attempt == 0: continue
                return exc.code
            except (OSError, ValueError):
                if attempt: return None
    except (TypeError, ValueError):
        return None
    return None


def refresh_for_decision(root, page, *, deadline_monotonic, clock=None, scanner=_observe, http_check=_public_status):
    """Refresh complete Admin observations and retain only public HTTP 200.

    Caller holds worker/browser lock. No stale fallback is allowed. The
    monotonic deadline is fixed by the caller; it does not cancel in-flight IO.
    Returns the freshly written records and metadata, not a cached decision.
    """
    if type(deadline_monotonic) not in (int,float) or deadline_monotonic <= time.monotonic():
        raise QualityHold('registry deadline unavailable')
    scan_deadline = min(deadline_monotonic, time.monotonic() + 90)
    first, second = scanner(page, scan_deadline), scanner(page, scan_deadline)
    if first != second: raise QualityHold('admin list changed between scans')
    observation, metadata = first
    _, _, records = _records(observation, metadata)
    if time.monotonic() >= deadline_monotonic: raise QualityHold('registry deadline elapsed')
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=6)
    futures = [pool.submit(http_check, record, deadline_monotonic) for record in records]
    try:
        remaining = max(0, deadline_monotonic-time.monotonic())
        _, pending = concurrent.futures.wait(futures, timeout=remaining)
        if pending or time.monotonic() >= deadline_monotonic:
            raise QualityHold('registry HTTP verification deadline elapsed')
        statuses = [future.result() for future in futures]
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
    # Guard against article/status changes during slow public checks: no new
    # timestamp can make an earlier Admin observation fresh.
    final = scanner(page, min(deadline_monotonic, time.monotonic()+90))
    if final != first: raise QualityHold('admin list changed during HTTP verification')
    if time.monotonic() >= deadline_monotonic: raise QualityHold('registry deadline elapsed')
    if any(status is not None and (type(status) is not int or not 100 <= status <= 599) for status in statuses):
        raise ValueError('invalid HTTP verification status')
    checks = {record['id']: status for record, status in zip(records, statuses)}
    summary = _save_observation(root, observation, metadata, clock=clock, checks=checks)
    # Exclude non-200 as requested, but network failures/server outages do not
    # prove absence from Published. Never use such a partial registry to approve
    # an article: this could turn an outage into an empty duplicate list.
    if any(status is None or status in (403, 408, 429) or status >= 500 for status in statuses):
        raise QualityHold('registry HTTP response unavailable; excluded records require retry')
    fresh_records, _ = read_current(root, max_age_seconds=60, clock=clock)
    return fresh_records, summary


def read_current(root, *, max_age_seconds, clock=None):
    """Read a recent integrity-checked snapshot; refuse stale/malformed data."""
    if type(max_age_seconds) is not int or not 1 <= max_age_seconds <= 86400:
        raise ValueError('invalid registry freshness limit')
    base = _directory(root); path = base / 'current.json'
    if path.is_symlink() or not path.is_file(): raise ValueError('registry unavailable')
    envelope = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(envelope, dict) or set(envelope) != {'sha256', 'snapshot'}:
        raise ValueError('invalid registry envelope')
    payload = envelope['snapshot']; digest = envelope['sha256']
    if not isinstance(payload, dict) or payload.get('version') != VERSION or payload.get('scope') != 'published-only' or not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest) or hashlib.sha256(_encoded(payload)).hexdigest() != digest:
        raise ValueError('registry integrity mismatch')
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    observed = datetime.fromisoformat(payload['observed_at_utc'])
    if now.tzinfo is None or observed.tzinfo is None or not 0 <= (now - observed).total_seconds() <= max_age_seconds:
        raise ValueError('registry stale or clock invalid')
    records = payload.get('records')
    if not isinstance(records, list) or payload.get('published_count') != len(records) or any(not isinstance(r,dict) or r.get('status') != 'Published' for r in records):
        raise ValueError('invalid registry records')
    verification = payload.get('http_verification')
    if verification is not None:
        if not isinstance(verification, dict) or not isinstance(verification.get('excluded'), list):
            raise ValueError('invalid HTTP verification evidence')
        for row in verification['excluded']:
            if not isinstance(row,dict) or 'status' not in row:
                raise ValueError('invalid HTTP verification evidence')
            status = row['status']
            if status is None or (type(status) is int and (status in (403,408,429) or status >= 500)):
                raise ValueError('registry HTTP response unavailable')
    return records, {'observed_at_utc': payload['observed_at_utc'], 'sha256': digest,
                     'published_count': len(records)}
