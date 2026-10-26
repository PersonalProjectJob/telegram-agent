"""Source-backed Playwright UI adapter. Network responses establish IDs; UI performs writes.

Endpoints are from the inspected NailHub source, not guessed. No direct API calls or DOM injection.
Contract evidence must be verified separately before live mode. Imports Playwright only on start.
"""

import json
import asyncio
import time
import os
import re
from decimal import Decimal
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlencode, urlparse

from .credentials import load_credentials
from .validation import (NeedsHuman, Posting, PHONE, SENTINEL_START_DATE, phone_key,
                         processing_start_date, publisher_branding_rewrite, unknown_business_name)
from .categories import (SUPPORTED_CATEGORIES, CATEGORY_LABELS, category_change_valid,
                         category_repair_valid)
from .publication import eligible, rewrite_valid, publication_valid, approval_readback, unchanged_valid_date

UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
DEEP_LINK_UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$', re.I)
LIST_PATH = '/api/v1/Admin/postings'
AI_PATH = '/api/v1/AI/AIEnhanced'
TYPES = {'Guaranteed': 'weekly', 'NoGuaranteed': 'hidden', 'Negotiable': 'negotiated',
         'Fixed': 'fixed', None: 'not_applicable'}
TRANSLATION_PATH = '/api/v1/Postings/GetTranslation'
PRESERVED = ('coverImage', 'images', 'videoUrls', 'specialNotes', 'isPublic', 'country', 'isUrgent',
             'pricePeriod', 'minPrice', 'maxPrice', 'priceNegotiable', 'priceCurrency', 'startDate', 'endDate',
             'isVIP', 'publishedAt', 'expiresAt', 'hashtags', 'extraInformation', 'yearsInOperation', 'workingHours',
             'numberOfOpening', 'workSchedule', 'skills', 'workingExperience', 'applyByDateTime')
LISTING_HEADERS_10 = ('Title','Business Name','Posted By','State','City','Zip Code',
                      'Category','Status','Created','Actions')
LISTING_HEADERS_12 = ('Title','Business Name','Posted By','State','City','Zip Code',
                      'Category','Schedule','Apply By','Status','Created','Actions')
LISTING_HEADERS_CHECKBOX_URGENT_12 = ('','Title','Business Name','Posted By','State','City',
                                     'Zip Code','Category','Urgent','Status','Created','Actions')
LISTING_TIMEZONE = timezone(timedelta(hours=7), 'Asia/Saigon')
MAX_POST_AGE_DAYS = 45


class OldPosting(Exception):
    """A verified listing date is outside the Owner's approval window."""

    def __init__(self, receipt, age_days):
        self.receipt, self.age_days = receipt, age_days
        super().__init__('posting is older than 45 days')


def listing_date_receipt(raw, visible_created, *, observed_at=None):
    """Bind the displayed Created day to the same identified listing response row."""
    created = raw.get('created')
    if (not isinstance(created, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?', created)
            or not isinstance(visible_created, str)
            or not re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', visible_created)):
        raise NeedsHuman('listing Created date is missing or outside verified format')
    try:
        raw_day = datetime.fromisoformat(created).date()
        visible_day = datetime.strptime(visible_created, '%m/%d/%Y').date()
    except ValueError:
        raise NeedsHuman('listing Created date is invalid') from None
    if raw_day.year == 1 or raw_day != visible_day:
        raise NeedsHuman('listing Created date differs from identified response')
    post = posting_from_data(raw)
    shared = {'id': post.id, 'status': post.status, 'category': post.category,
              'content': post.content, 'title': post.fields['title']}
    # Listing DTOs omit protected detail metadata. Compare only fields the
    # identified listing response actually returned; never hash it as detail.
    for remote, local in (('businessName','salon'), ('address','address'),
                          ('city','city'), ('state','state'), ('zipCode','zip')):
        if remote in raw:
            shared[local] = post.fields[local]
    return {'id': post.id, 'source_hash': post.fingerprint, 'created': created,
            'visible_created': visible_created,
            'shared_source': shared,
            'observed_at': observed_at or datetime.now(LISTING_TIMEZONE).isoformat()}


def listing_age_days(post, receipt, *, today=None):
    """Compare the verified visible calendar day with today's Asia/Saigon day."""
    if (not isinstance(receipt, dict) or receipt.get('id') != post.id
            or not isinstance(receipt.get('source_hash'), str)
            or not re.fullmatch(r'[0-9a-f]{64}', receipt['source_hash'])):
        raise NeedsHuman('listing Created proof does not match current posting source')
    shared = receipt.get('shared_source')
    actual = {'id': post.id, 'status': post.status, 'category': post.category,
              'content': post.content, 'title': post.fields.get('title', '')}
    for local in ('salon','address','city','state','zip'):
        actual[local] = post.fields.get(local, '')
    allowed = {'id','status','category','content','title','salon','address','city','state','zip'}
    if (not isinstance(shared, dict) or not {'id','status','category','content','title'}.issubset(shared)
            or not set(shared).issubset(allowed)
            or any(actual.get(key) != value for key, value in shared.items())):
        raise NeedsHuman('listing Created proof differs from shared detail source')
    shown = receipt.get('visible_created')
    if not isinstance(shown, str) or not re.fullmatch(r'\d{1,2}/\d{1,2}/\d{4}', shown):
        raise NeedsHuman('listing Created proof is unavailable')
    try:
        created_day = datetime.strptime(shown, '%m/%d/%Y').date()
    except ValueError:
        raise NeedsHuman('listing Created proof is invalid') from None
    raw_created = receipt.get('created')
    observed_at = receipt.get('observed_at')
    if (not isinstance(raw_created, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?', raw_created)
            or not isinstance(observed_at, str)):
        raise NeedsHuman('listing Created proof lacks source date or observation time')
    try:
        raw_day = datetime.fromisoformat(raw_created).date()
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        raise NeedsHuman('listing Created proof has invalid source date or observation time') from None
    if raw_day != created_day or observed.utcoffset() != timedelta(hours=7):
        raise NeedsHuman('listing Created proof differs from source or Asia/Saigon observation')
    if created_day.year == 1:
        raise NeedsHuman('listing Created proof is a sentinel date')
    current_day = today if today is not None else datetime.now(LISTING_TIMEZONE).date()
    if not isinstance(current_day, date) or created_day > current_day:
        raise NeedsHuman('listing Created day is in the future or processing date is invalid')
    return (current_day - created_day).days


def same_form_value(key, value, source):
    """Allow contact phone formatting only; malformed or changed numbers still hold."""
    if key not in ('phone_primary', 'phone_other'):
        return value == source
    def phones(text):
        matches = list(PHONE.finditer(text))
        if (text.strip() and not matches) or re.sub(r'[\s,;/|]+', '', PHONE.sub('', text)):
            raise NeedsHuman('phone field contains unrecognized data')
        return tuple(phone_key(match.group()) for match in matches)
    return phones(value) == phones(source)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)
    def handle_starttag(self, tag, attrs):
        if tag in ('p', 'br', 'div', 'li'):
            self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('p', 'div', 'li'):
            self.parts.append('\n')


def canonical_text(text):
    """Canonicalize visible paragraph spacing without interpreting literal text as HTML."""
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def listing_title(text):
    """PostingManagement formatTitle uses JavaScript UTF-16 length/slice(0,20)."""
    encoded = text.encode('utf-16-le', errors='surrogatepass')
    return encoded[:40].decode('utf-16-le', errors='surrogatepass')+'...' if len(encoded)>40 else text


def plain(html):
    parser = _Text()
    parser.feed(html)
    return canonical_text(''.join(parser.parts))


def posting_from_data(data):
    if (not isinstance(data, dict) or not isinstance(data.get('id'), str) or not UUID.fullmatch(data['id'])
            or any(not isinstance(data.get(k), str) for k in ('title', 'content', 'status', 'postingCategory'))):
        raise NeedsHuman('unverified posting response identity/schema')
    contact = data.get('contactInfo') or {}
    if not isinstance(contact, dict):
        raise NeedsHuman('unverified contact response')
    fields = {key: str(data.get(remote) or '') for key, remote in {
        'title': 'title', 'salon': 'businessName', 'address': 'address', 'city': 'city',
        'state': 'state', 'zip': 'zipCode'}.items()}
    fields.update(phone_primary=str(contact.get('phone') or data.get('phone') or ''),
                  phone_other=str(contact.get('mobilePhone') or ''),
                  email=str(contact.get('email') or contact.get('emailAddress') or ''),
                  salary_type=TYPES.get(data.get('priceType'), str(data.get('priceType') or '')),
                  salary_amount=str(data.get('price') or '0'),
                  _preserved=json.dumps({**{k: data[k] for k in PRESERVED if k in data},
                                         **({'contactName': contact['name']} if 'name' in contact else {})}, sort_keys=True, ensure_ascii=False),
                  _unknown_preserved=json.dumps([k for k in PRESERVED if k not in data]
                                               + ([] if 'name' in contact else ['contactName'])))
    return Posting(data['id'], data['status'], data['postingCategory'], plain(data['content']), fields)


def equivalent_fields(actual, expected):
    if set(actual) != set(expected):
        return False
    for key, value in expected.items():
        if key == 'salary_amount':
            try:
                if Decimal(actual[key] or '0') != Decimal(value or '0'):
                    return False
            except Exception:
                return False
        elif actual[key] != value:
            return False
    return True


def matches_expected(post, expected, *, status):
    actual_fields = dict(post.fields)
    if (status == 'Approved' and (approval_readback(expected) or
            (expected.get('status') == 'Draft' and not expected.get('audit', {}).get('publication_workflow')))
            and not expected.get('audit', {}).get('category_only_repair')
            and '_preserved' in actual_fields and '_preserved' in expected.get('fields', {})):
        actual = json.loads(actual_fields['_preserved'])
        wanted = json.loads(expected['fields']['_preserved'])
        # AdminChangePostingStatus owns this one transition. All other facts remain exact.
        if actual.get('publishedAt') != wanted.get('publishedAt'):
            try:
                timestamp = actual['publishedAt']
                if not isinstance(timestamp, str) or not timestamp:
                    return False
                new = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
                old = wanted.get('publishedAt')
                if old and new < datetime.fromisoformat(old.replace('Z', '+00:00')):
                    return False
            except (ValueError, TypeError, KeyError):
                return False
            actual['publishedAt'] = wanted.get('publishedAt')
            actual_fields['_preserved'] = json.dumps(actual, sort_keys=True, ensure_ascii=False)
    return (post.id == expected.get('id') and post.category == expected.get('category')
            and post.status == status and post.content == expected.get('content')
            and equivalent_fields(actual_fields, expected.get('fields', {})))


def prepare_save_payload(data, original, expected):
    """Restore only omitted source-backed command fields; conflicting included values are rejected."""
    if not isinstance(data, dict):
        raise NeedsHuman('invalid UI save payload')
    result = dict(data)
    preserved = json.loads(expected['fields']['_preserved'])
    original_preserved = json.loads(original.fields.get('_preserved', '{}'))
    start_repair = expected.get('audit', {}).get('metadata_changes', {}).get('startDate')
    if original_preserved.get('startDate') != preserved.get('startDate'):
        target = preserved.get('startDate')
        if (original_preserved.get('startDate') != SENTINEL_START_DATE
                or not isinstance(start_repair, dict)
                or start_repair.get('old') != SENTINEL_START_DATE or start_repair.get('new') != target
                or not isinstance(target, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T00:00:00', target)
                or target != processing_start_date()):
            raise NeedsHuman('unauthorized start date change')
    if not set(preserved).issubset(set(PRESERVED) | {'contactName'}):
        raise NeedsHuman('unverified protected command schema')
    # Missing GET fields remain UNKNOWN and are never synthesized as null or sent by this normalizer.
    # The acceptance scope is preservation of the data this UI/API actually exposes.
    for key in preserved:
        if (key == 'startDate' and preserved[key] is not None
                and original_preserved.get(key) == preserved[key]
                and ((expected.get('audit', {}).get('category_only_repair') and category_repair_valid(original, expected))
                     or (unchanged_valid_date(original, expected)
                         and (rewrite_valid(original, expected) or publication_valid(original, expected))))):
            if key in result and result[key] not in (None, preserved[key]):
                raise NeedsHuman('UI changed an untouched field')
            # AdminUpdate uses request.StartDate ?? posting.StartDate. Category-only
            # repair retains the exact stored date without revalidating its past day.
            result[key] = None
        elif key not in result:
            result[key] = preserved[key]
        elif (key == 'startDate' and start_repair and original_preserved.get(key) == SENTINEL_START_DATE
              and result[key] in (None, SENTINEL_START_DATE)):
            result[key] = preserved[key]
        elif key == 'specialNotes' and preserved[key] is None and result[key] == '':
            # PostingDetailsDialog maps null to an empty form value; preserve source null.
            result[key] = None
        elif result[key] != preserved[key]:
            raise NeedsHuman('UI changed an untouched field')
    if not safe_save_payload(result, original, expected):
        raise NeedsHuman('UI save payload differs from validated proposal')
    return result


def safe_save_payload(data, original, expected):
    """Verify exactly the identified UI submission, including untouched media and date facts."""
    if (not isinstance(data, dict) or not category_change_valid(original, expected)
            or data.get('postingCategory') != expected.get('category')
            or data.get('status') != original.status or data.get('title') != expected['fields']['title']
            or plain(data.get('content', '')) != expected['content']):
        return False
    original_metadata = json.loads(original.fields.get('_preserved', '{}'))
    expected_metadata = json.loads(expected['fields'].get('_preserved', '{}'))
    if original_metadata.get('isPublic') != expected_metadata.get('isPublic'):
        if not publication_valid(original, expected):
            return False
    if original.status == 'Approved' and not (category_repair_valid(original, expected)
            or rewrite_valid(original, expected) or publication_valid(original, expected)):
        return False
    mapping = {'salon': 'businessName', 'address': 'address', 'city': 'city', 'state': 'state',
               'zip': 'zipCode', 'phone_primary': 'phone', 'phone_other': 'mobilePhone', 'email': 'email'}
    if any(str(data.get(remote) or '') != expected['fields'][key] for key, remote in mapping.items()):
        return False
    target = expected['category']
    allowed = {'JobPosting': ('Guaranteed', 'NoGuaranteed', 'Negotiable'),
               'SalonForSale': ('Fixed', 'Negotiable'), 'Portfolio': (None,)}
    if data.get('priceType') not in allowed[target] or TYPES.get(data.get('priceType')) != expected['fields']['salary_type']:
        return False
    if target == 'Portfolio' and (data.get('priceType') is not None or data.get('price') is not None):
        return False
    try:
        if Decimal(str(data.get('price') or 0)) != Decimal(expected['fields']['salary_amount'] or '0'):
            return False
    except Exception:
        return False
    preserved = json.loads(expected['fields']['_preserved'])
    for key, value in preserved.items():
        sent = data.get(key)
        if key == 'startDate' and sent is None and value == json.loads(original.fields['_preserved']).get('startDate'):
            continue  # Backend explicitly retains startDate when omitted/null.
        if key == 'priceNegotiable' and sent is None:
            sent = False  # C# nonnullable bool default, established from inspected source.
        if sent != value:
            return False
    return True


class Browser:
    def __init__(self, config, *, page=None):
        self.config, self.page = config, page
        self.manager = self.context = None
        self.current = None
        self.age_receipts = {}
        self.check_action = lambda: None

    async def __aenter__(self):
        if self.page is None:
            from playwright.async_api import async_playwright
            path = Path(self.config.profile_path)
            path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name == 'posix':
                stat = path.stat()
                if stat.st_uid != os.getuid() or stat.st_mode & 0o077:
                    raise NeedsHuman('browser profile requires service ownership and mode 0700')
            self.manager = await async_playwright().start()
            try:
                self.context = await self.manager.chromium.launch_persistent_context(
                    str(path), headless=self.config.headless, viewport={'width': 1440, 'height': 1000})
                self.page = await self.context.new_page()
            except BaseException:
                await self.manager.stop()
                self.manager = None
                raise
        self.page.set_default_timeout(30000)
        if self.config.mode == 'preview':
            await self.page.route('**/*', self._preview_guard)
        return self

    async def __aexit__(self, *args):
        try:
            if self.context:
                await self.context.close()
        finally:
            if self.manager:
                await self.manager.stop()

    async def _preview_guard(self, route):
        request = route.request
        url = urlparse(request.url)
        if (request.method not in ('GET', 'HEAD', 'OPTIONS')
                and (url.path.startswith(LIST_PATH) or url.path == AI_PATH)):
            await route.abort()
        else:
            await route.continue_()

    async def one(self, selector, *, visible=True):
        if not isinstance(selector, str) or not selector:
            raise NeedsHuman('required selector not configured')
        item = self.page.locator(selector)
        if await item.count() > 1:
            raise NeedsHuman('selector missing or ambiguous')
        # Network completion precedes React render. Wait without choosing .first or a row index.
        try:
            await item.wait_for(state='visible' if visible else 'attached', timeout=30000)
        except Exception:
            raise NeedsHuman('required selector readiness could not be established') from None
        if await item.count() != 1:
            raise NeedsHuman('selector missing or ambiguous')
        if visible and not await item.is_visible():
            raise NeedsHuman('required control is not visible')
        return item

    async def control(self, name):
        return await self.one(self.config.selectors.get(name))

    async def action_click(self, name):
        item = await self.control(name)
        self.check_action()
        await item.click()

    async def authenticated(self):
        parsed = urlparse(self.page.url)
        if parsed.scheme != 'https' or parsed.hostname != 'nailhub.ai' or parsed.path != '/admin/postings':
            raise NeedsHuman('login_required: admin route unavailable')
        await self.control('authenticated')

    def _response(self, response, path, method='GET'):
        parsed = urlparse(response.url)
        return (parsed.scheme + '://' + parsed.netloc == self.config.api_origin
                and parsed.path == path and response.request.method == method)

    async def _json(self, response):
        if response.status != 200:
            raise NeedsHuman('admin response did not verify success')
        data = await response.json()
        return data

    async def login(self):
        """Only Owner-authorized local file; unexpected auth/MFA is a hard failure."""
        credentials = load_credentials(self.config.credential_file)
        # Navigate home, then use inspected login UI. No token/session DOM injection.
        await self.page.goto('https://nailhub.ai/', wait_until='domcontentloaded')
        await (await self.control('login_open')).click()
        await (await self.control('login_email')).fill(credentials['email'])
        await (await self.control('login_password')).fill(credentials['password'])
        del credentials
        dialog = await self.control('login_dialog')
        async with self.page.expect_response(lambda r: self._response(r, '/api/v1/authentication/signin', 'POST'), timeout=60000) as received:
            await (await self.control('login_submit')).click()
        if (await received.value).status != 200:
            raise NeedsHuman('login_required: credentials or additional authentication rejected')
        await dialog.wait_for(state='hidden')
        await self.page.wait_for_load_state('networkidle')
        await self.page.goto(self.config.url, wait_until='networkidle')
        await self.authenticated()

    async def _navigate(self, url, response_path):
        async with self.page.expect_response(lambda r: self._response(r, response_path), timeout=60000) as received:
            await self.page.goto(url, wait_until='domcontentloaded')
        data = await self._json(await received.value)
        await self.page.wait_for_load_state('networkidle')
        await self.authenticated()
        return data

    async def ready(self):
        # Login can be attempted once per scan; missing/expired credentials fail closed.
        await self.page.goto(self.config.url, wait_until='networkidle')
        try:
            await self.authenticated()
        except NeedsHuman:
            if not self.config.credential_file:
                raise
            await self.login()

    async def candidates(self):
        await self.ready()
        data = await self._navigate(self.config.url, LIST_PATH)
        results, seen = [], set()
        self.age_receipts = {}
        for page_number in range(1, self.config.max_pages + 1):
            if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                    or data.get('pageNumber') != page_number or type(data.get('hasNextPage')) is not bool):
                raise NeedsHuman('unverified listing response')
            for post, _, receipt in await self._verified_listing_rows(data):
                if post.id in seen:
                    raise NeedsHuman('duplicate posting identity across pages')
                seen.add(post.id)
                self.age_receipts[post.id] = receipt
                if eligible(post):
                    results.append(post)
            if not data['hasNextPage']:
                return results
            if page_number == self.config.max_pages:
                raise NeedsHuman('listing exceeds configured page bound; increase after review')
            async with self.page.expect_response(lambda r: self._response(r, LIST_PATH)) as received:
                next_button = await self.control('next_page')
                if not await next_button.is_enabled():
                    raise NeedsHuman('pagination readiness mismatch')
                await next_button.click()
            data = await self._json(await received.value)
            await self.page.wait_for_load_state('networkidle')
        return results

    async def _verified_listing_rows(self, data):
        """Bind every response item and Created date to its visible table row."""
        table = await self.one('table')
        headers = tuple(await table.locator('thead th').all_inner_texts())
        if headers not in (LISTING_HEADERS_10, LISTING_HEADERS_12, LISTING_HEADERS_CHECKBOX_URGENT_12):
            raise NeedsHuman('listing table columns differ from verified contract')
        core_offset = 1 if headers == LISTING_HEADERS_CHECKBOX_URGENT_12 else 0
        status_index, created_index = (7, 8) if headers == LISTING_HEADERS_10 else (9, 10)
        rows = table.locator('tbody > tr')
        items = data['items']
        if items:
            await rows.nth(len(items) - 1).wait_for(state='visible', timeout=30000)
        if await rows.count() != len(items):
            raise NeedsHuman('listing table rows differ from identified response')
        bound = []
        compact = lambda text: ' '.join(str(text).split())
        for index, raw in enumerate(items):
            post = posting_from_data(raw)
            row = rows.nth(index)
            cells = await row.locator('td').all_inner_texts()
            expected = [listing_title(raw['title']), raw.get('businessName') or '-',
                raw.get('postedByUserName') or raw.get('postedByName') or '-',
                raw.get('state') or '-', raw.get('city') or '-', raw.get('zipCode') or '-',
                raw['postingCategory'], raw['status']]
            visible = [*cells[core_offset:core_offset + 7], cells[status_index]] if len(cells) == len(headers) else []
            if [compact(value) for value in visible] != [compact(value) for value in expected]:
                raise NeedsHuman('listing visible row differs from identified response')
            receipt = listing_date_receipt(raw, cells[created_index].strip())
            bound.append((post, row, receipt))
        return bound

    async def verify_listing_age(self, post, *, fresh=False):
        """Return source-bound proof or reject an old, unknown, or changed posting."""
        if fresh:
            page = await self.page.context.new_page()
            try:
                reader = Browser(self.config, page=page)
                receipt = await reader.listing_receipt(post.id)
            finally:
                await page.close()
        else:
            receipt = self.age_receipts.get(post.id)
        age_days = listing_age_days(post, receipt)
        if age_days > MAX_POST_AGE_DAYS:
            raise OldPosting(receipt, age_days)
        return receipt

    async def listing_receipt(self, post_id):
        """Fresh identified listing-date read, stopping at the target row."""
        if not isinstance(post_id, str) or not UUID.fullmatch(post_id):
            raise NeedsHuman('stable UUID identity required for listing Created proof')
        await self.ready()
        data = await self._navigate(self.config.url, LIST_PATH)
        seen = set()
        for page_number in range(1, self.config.max_pages + 1):
            if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                    or data.get('pageNumber') != page_number or type(data.get('hasNextPage')) is not bool):
                raise NeedsHuman('unverified listing response')
            for item, _, receipt in await self._verified_listing_rows(data):
                if item.id in seen:
                    raise NeedsHuman('duplicate posting identity across pages')
                seen.add(item.id)
                if item.id == post_id:
                    return receipt
            if not data['hasNextPage']:
                raise NeedsHuman('identified posting is missing from verified listing')
            if page_number == self.config.max_pages:
                raise NeedsHuman('listing exceeds configured page bound; increase after review')
            async with self.page.expect_response(lambda response: self._response(response, LIST_PATH)) as received:
                next_button = await self.control('next_page')
                if not await next_button.is_enabled():
                    raise NeedsHuman('pagination readiness mismatch')
                await next_button.click()
            data = await self._json(await received.value)
            await self.page.wait_for_load_state('networkidle')
        raise NeedsHuman('identified posting is missing from verified listing')

    async def read(self, post_id):
        if not isinstance(post_id, str) or not UUID.fullmatch(post_id):
            raise NeedsHuman('stable UUID identity required')
        if DEEP_LINK_UUID.fullmatch(post_id):
            data = await self._navigate(self.config.url + '?' + urlencode({'id': post_id}), LIST_PATH + '/' + post_id)
        else:
            data = await self._read_via_listing(post_id)
        post = posting_from_data(data)
        if post.id != post_id:
            raise NeedsHuman('detail response identity mismatch')
        await (await self.control('detail')).wait_for(state='visible')
        title = await (await self.control('detail_title')).inner_text()
        content = await (await self.control('detail_content')).inner_text()
        if ' '.join(title.split()) != ' '.join(post.fields['title'].split()) or ' '.join(content.split()) != ' '.join(post.content.split()):
            raise NeedsHuman('visible detail does not match identified response')
        self.current = post
        return post

    async def _read_via_listing(self, post_id):
        """Actual frontend rejects sequential GUID deep links; use its verified row View UI."""
        data = await self._navigate(self.config.url, LIST_PATH)
        seen = set()
        for page_number in range(1, self.config.max_pages+1):
            if (not isinstance(data, dict) or not isinstance(data.get('items'), list)
                    or data.get('pageNumber') != page_number or type(data.get('hasNextPage')) is not bool):
                raise NeedsHuman('unverified listing response')
            bound = await self._verified_listing_rows(data)
            for identified, _, _ in bound:
                if identified.id in seen:
                    raise NeedsHuman('duplicate posting identity across pages')
                seen.add(identified.id)
            target = None
            for identified, row, receipt in bound:
                if identified.id == post_id:
                    target = row
                    self.age_receipts[post_id] = receipt
            if target is not None:
                button = target.get_by_role('button', name='View', exact=True)
                await button.wait_for(state='visible')
                if await button.count() != 1 or await button.get_attribute('title') != 'View Posting Detail':
                    raise NeedsHuman('listing View control is missing or ambiguous')
                async with self.page.expect_response(lambda response: self._response(response, LIST_PATH+'/'+post_id)) as received:
                    await button.click()
                detail = await self._json(await received.value)
                await self.page.wait_for_load_state('networkidle')
                await self.authenticated()
                return detail  # read() checks exact returned ID plus visible title/content.
            if not data['hasNextPage']:
                raise NeedsHuman('identified posting is missing from verified listing')
            if page_number == self.config.max_pages:
                raise NeedsHuman('listing exceeds configured page bound; increase after review')
            async with self.page.expect_response(lambda response: self._response(response, LIST_PATH)) as received:
                next_button = await self.control('next_page')
                if not await next_button.is_enabled():
                    raise NeedsHuman('pagination readiness mismatch')
                await next_button.click()
            data = await self._json(await received.value)
            await self.page.wait_for_load_state('networkidle')
        raise NeedsHuman('identified posting is missing from verified listing')

    def _live(self, post):
        if self.config.mode != 'live' or not self.config.verified or not self.config.verification_ref:
            raise NeedsHuman('live requires VERIFIED authenticated contract')
        if not eligible(post):
            raise NeedsHuman('only identified Draft or unpublished Approved is mutable')

    async def _field(self, key):
        return await self.one(self.config.fields.get(key))

    async def _fill(self, key, value):
        # State/city widgets require inspected explicit option mapping, never guessed first match.
        item = await self._field(key)
        await item.fill(value)

    async def selected_location(self, key, value):
        """Read the visible react-select label, excluding hidden ARIA announcements."""
        widget = await self._field(key)
        labels = widget.locator('.react-select__single-value')
        pattern = re.compile(r'^.+ - ' + re.escape(value) + r'$' if key == 'state'
                             else r'^' + re.escape(value) + r'$')
        await labels.filter(has_text=pattern).wait_for(state='visible')
        if await labels.count() != 1 or not pattern.fullmatch((await labels.inner_text()).strip()):
            raise NeedsHuman('location widget has not retained original state/city',
                details={'kind': 'automation', 'field': key, 'source': value,
                         'action': 'Chọn lại giá trị nguồn và xác nhận nhãn đang hiển thị giữ đúng giá trị.'})

    async def select_location(self, post, changes):
        """Inspected react-select contract: exact state option, then dependent city."""
        if not any(key in changes for key in ('state', 'city')):
            return
        target = {**post.fields, **changes}
        keys = ('state', 'city') if 'state' in changes else ('city',)
        for key in keys:
            value = target[key]
            widget = await self._field(key)
            await widget.get_by_role('combobox').fill(value)
            name = re.compile(r'^.+ - ' + re.escape(value) + r'$') if key == 'state' else value
            option = self.page.get_by_role('option', name=name, exact=True)
            await option.wait_for(state='visible')
            if await option.count() != 1:
                raise NeedsHuman('location selection needs verified state/city option contract',
                    details={'kind': 'automation', 'field': key, 'current': post.fields.get(key, ''),
                             'source': value, 'action': 'Mở form và chọn đúng tùy chọn thành phố/tiểu bang từ nguồn.'})
            await option.click()
        for key in ('state', 'city'):
            await self.selected_location(key, target[key])

    async def _open_edit(self, post):
        fresh = await self.read(post.id)
        if fresh.fingerprint != post.fingerprint:
            raise NeedsHuman('post changed since original snapshot')
        await self.action_click('edit')
        await (await self.control('edit_dialog')).wait_for(state='visible')
        for accordion in ('contact_section', 'posting_section'):
            if accordion in self.config.selectors:
                item = await self.control(accordion)
                if await item.get_attribute('aria-expanded') == 'false':
                    await item.click()
        # Validate existing form facts before editing so a blank or default widget cannot erase data.
        for key in ('salon', 'address', 'zip', 'phone_primary', 'phone_other', 'email', 'title'):
            actual = await (await self._field(key)).input_value()
            if not same_form_value(key, actual, post.fields.get(key, '')):
                raise NeedsHuman('edit form differs from original field', details={'kind': 'automation',
                    'field': key if key != 'title' else 'automation', 'current': actual,
                    'source': post.fields.get(key, ''), 'action': 'Đối chiếu form với bản đọc mới; tải lại trước khi sửa hoặc lưu.'})
        for key in ('state', 'city'):
            # react-select options load asynchronously. Wait for the source value, never invent one.
            if post.fields.get(key):
                await self.selected_location(key, post.fields[key])
    async def select_category(self, post, target):
        if target == post.category:
            return
        if target not in SUPPORTED_CATEGORIES:
            raise NeedsHuman('unsupported category selection')
        trigger = await self.one('[role=dialog] #posting-category')
        await trigger.click()
        option = self.page.get_by_role('option', name=CATEGORY_LABELS[target], exact=True)
        await option.wait_for(state='visible')
        if await option.count() != 1:
            raise NeedsHuman('category option is missing or ambiguous')
        async with self.page.expect_response(lambda response: self._response(response, TRANSLATION_PATH, 'POST')) as received:
            await option.click()
        translation = await self._json(await received.value)
        titles = translation.get('translations') if isinstance(translation, dict) else None
        if not isinstance(titles, list) or any(not isinstance(value, str) for value in titles):
            raise NeedsHuman('category title translation completion unverified')
        title = await self._field('title')
        translated_title = titles[0] if titles else post.fields['title']
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            # Category change remounts the trigger and asynchronously changes title.
            current = await self.one('[role=dialog] #posting-category')
            if ((await current.inner_text()).strip() == CATEGORY_LABELS[target]
                    and await title.input_value() == translated_title):
                await title.fill(post.fields['title'])
                return
            await asyncio.sleep(.05)
        raise NeedsHuman('category selection or title translation completion unverified')

    async def _apply_changes(self, post, changes):
        await self.select_location(post, changes)
        for key, value in changes.items():
            if key == 'salary_type':
                if value == 'not_applicable':
                    continue  # Portfolio submit contract nulls both pay fields.
                await (await self._field(key)).click()
                if value == 'fixed':
                    item = self.page.get_by_role('option', name='Nhập giá', exact=True)
                    await item.wait_for(state='visible')
                    if await item.count() != 1:
                        raise NeedsHuman('sale price option is missing or ambiguous')
                    await item.click()
                else:
                    await (await self.one(self.config.options.get(value))).click()
            elif key == 'salary_amount':
                if changes['salary_type'] in ('weekly', 'fixed'):
                    await self._fill(key, value)
            elif key in ('state', 'city', 'category'):
                continue
            else:
                await self._fill(key, value)

    async def edit_and_rewrite(self, post, changes):
        self._live(post)
        await self.verify_listing_age(post, fresh=True)
        await self._open_edit(post)
        await self.select_category(post, changes.get('category', post.category))
        await self._apply_changes(post, changes)
        async with self.page.expect_response(lambda r: self._response(r, AI_PATH, 'POST'), timeout=self.config.timeout_seconds * 1000) as response:
            await self.action_click('ai')
        await self._json(await response.value)
        await (await self.control('ai')).wait_for(state='visible')
        if not await (await self.control('ai')).is_enabled():
            raise NeedsHuman('AI completion unverified')
        title = await (await self._field('title')).input_value()
        editor = await self.control('editor')
        content = await editor.inner_text()
        current_salon = post.fields.get('salon', '').strip()
        if ((not current_salon or unknown_business_name(current_salon))
                and changes.get('salon', '').strip().casefold() == 'nailhub.ai'):
            branded = publisher_branding_rewrite(
                post.fields.get('title', '') + '\n' + post.content, content,
                changes.get('category', post.category))
            if branded != content:
                self.check_action()
                await editor.fill(branded)
                if canonical_text(await editor.inner_text()) != canonical_text(branded):
                    raise NeedsHuman('publisher branding edit was not retained by the AI editor')
                content = branded
        return title, canonical_text(content)

    async def assert_fresh(self, post):
        """Read via a second UI page, preserving the unsaved editor. Backend has no proven CAS."""
        page = await self.page.context.new_page()
        try:
            reader = Browser(self.config, page=page)
            fresh = await reader.read(post.id)
            if fresh.fingerprint != post.fingerprint:
                raise NeedsHuman('remote post changed during inference; no Save permitted')
            self.check_action()
        finally:
            await page.close()

    async def save(self, post, expected, *, _category_repair=False):
        if _category_repair:
            if (self.config.mode != 'live' or not self.config.verified or not self.config.verification_ref
                    or not category_repair_valid(post, expected)):
                raise NeedsHuman('approved category repair is not authorized')
        else:
            self._live(post)
            await self.verify_listing_age(post, fresh=True)
        await self.authenticated()
        await self.assert_fresh(post)
        # Abort a UI request which would silently drop a protected field, before it reaches NailHub.
        # Inspecting a request is read-only; its body is never changed or injected.
        route_pattern = self.config.api_origin + LIST_PATH + '/' + post.id
        async def guard(route):
            await self.guard_save(route, post, expected)
        await self.page.route(route_pattern, guard)
        try:
            async with self.page.expect_response(lambda r: self._response(r, LIST_PATH + '/' + post.id, 'PUT'),
                                                 timeout=self.config.timeout_seconds * 1000) as received:
                await self.action_click('save')
            response = await received.value
            if response.status not in (200, 204):
                raise NeedsHuman('save outcome unverified')
        finally:
            await self.page.unroute(route_pattern, guard)
        # Navigation issues fresh GET, bypassing SPA query cache. No automatic re-save.
        return await self.read(post.id)

    async def category_only_repair(self, post, expected):
        if (self.config.mode != 'live' or not self.config.verified or not self.config.verification_ref
                or not category_repair_valid(post, expected)):
            raise NeedsHuman('approved category repair is not authorized')
        await self._open_edit(post)
        editor = await self.control('editor')
        if canonical_text(await editor.inner_text()) != post.content:
            raise NeedsHuman('category-only editor differs from fresh content')
        await self.select_category(post, expected['category'])
        await self._apply_changes(post, {key: expected['fields'][key] for key in ('salary_type', 'salary_amount')})
        if (await (await self._field('title')).input_value() != post.fields['title']
                or canonical_text(await editor.inner_text()) != post.content):
            raise NeedsHuman('category-only edit changed protected title/content')
        return await self.save(post, expected, _category_repair=True)

    async def publish(self, post, expected):
        self._live(post)
        if not publication_valid(post, expected):
            raise NeedsHuman('publication transition is not authorized')
        await self.verify_listing_age(post, fresh=True)
        await self._open_edit(post)
        editor = await self.control('editor')
        if canonical_text(await editor.inner_text()) != post.content:
            raise NeedsHuman('publication editor differs from fresh content')
        checkbox = await self.one('[role=dialog] #isPublic')
        if await checkbox.get_attribute('aria-checked') != 'false':
            raise NeedsHuman('publication checkbox differs from fresh source')
        self.check_action()
        await checkbox.click()
        if await checkbox.get_attribute('aria-checked') != 'true':
            raise NeedsHuman('publication checkbox completion unverified')
        if (await (await self._field('title')).input_value() != post.fields['title']
                or canonical_text(await editor.inner_text()) != post.content):
            raise NeedsHuman('publication edit changed protected title/content')
        return await self.save(post, expected)

    async def guard_save(self, route, post, expected):
        request = route.request
        parsed = urlparse(request.url)
        if (request.method != 'PUT' or parsed.scheme + '://' + parsed.netloc != self.config.api_origin
                or parsed.path != LIST_PATH + '/' + post.id or parsed.query):
            await route.continue_()
            return
        try:
            normalized = prepare_save_payload(request.post_data_json, post, expected)
            await self.assert_fresh(post)
            self.check_action()
        except Exception:
            await route.abort()
            return
        # The actual UI Save, not a new API call: restore only omitted whitelisted source facts.
        await route.continue_(post_data=json.dumps(normalized, ensure_ascii=False))

    async def approve(self, post):
        self._live(post)
        if post.status != 'Draft':
            raise NeedsHuman('Approve requires identified Draft')
        fresh = await self.read(post.id)
        if fresh.fingerprint != post.fingerprint:
            raise NeedsHuman('saved post changed before approval')
        await self.action_click('approve')
        await (await self.control('approve_dialog')).wait_for(state='visible')
        await self.verify_listing_age(post, fresh=True)
        self.check_action()
        async with self.page.expect_response(lambda r: self._response(r, LIST_PATH + '/' + post.id + '/status', 'PATCH')) as received:
            await self.action_click('approve_confirm')
        response = await received.value
        if response.status not in (200, 204):
            raise NeedsHuman('approval outcome unverified')
        return await self.read(post.id)
