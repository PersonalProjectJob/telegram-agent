"""Playwright staging UI adapter. Writes only originate from visible form controls."""
import json
import hashlib
import asyncio
import functools
import io
import ipaddress
import logging
import os
import re
import socket
import threading
import time
import unicodedata
from contextlib import asynccontextmanager
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

from ._runtime import ProcessLock
from .ai_transport import PinnedHTTPS, resolve
from .admin_security import Guard, load_credentials
from .publication import AdminError, Markup, binding, instant, matches, media_origin, safe_url, same_html, snapshot
from .schedule import VN_TZ
from .usage import write_atomic

BLOGS = '/api/v1/admin/blogs'
CATEGORIES = '/api/v1/admin/blog-categories'
SIGNIN = '/api/v1/authentication/signin'
UPLOAD = '/api/v1/Images/quick-upload'
LOG = logging.getLogger(__name__)
CLOSE_TIMEOUT_SECONDS = 15
CODE = re.compile(r'^[a-z0-9_]{1,80}$')
ID = re.compile(r'^[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$')


def public_webp(config, url, *, cover, approved_sha=None):
    """Anonymous, pinned, bounded GET; a wrong MIME requires approved byte provenance."""
    from PIL import Image
    safe_url(url, config)
    u = urlsplit(url)
    if u.scheme + '://' + u.netloc not in (config.contract.api_origin, media_origin(config)):
        raise AdminError('published_image_origin_invalid')
    if approved_sha is not None and (not isinstance(approved_sha, str) or not re.fullmatch('[0-9a-f]{64}', approved_sha)):
        raise AdminError('approved_image_hash_invalid')
    maximum = (200 if cover else 150) * 1024
    connection = response = watchdog = None
    expired = threading.Event()
    try:
        addresses = [row[4][0] for row in resolve(u.hostname)]
        if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
            raise AdminError('published_image_origin_invalid')
        connection = PinnedHTTPS(u.hostname, addresses[0], config.admin.timeout_seconds)
        connection.connect()
        upstream = connection.sock
        def expire():
            expired.set()
            try: upstream.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        watchdog = threading.Timer(config.admin.timeout_seconds, expire)
        watchdog.daemon = True
        watchdog.start()
        target = (u.path or '/') + ('?' + u.query if u.query else '')
        # No cookies, authorization, redirects or transport retry.
        connection.request('GET', target, headers={'Accept': 'image/webp'})
        response = connection.getresponse()
        if response.status != 200:
            raise AdminError('published_image_unavailable')
        content_type = (response.getheader('Content-Type') or '').split(';', 1)[0].strip().lower()
        if content_type not in ('image/webp', 'application/octet-stream'):
            raise AdminError('published_image_content_type_invalid')
        if content_type == 'application/octet-stream' and approved_sha is None:
            raise AdminError('approved_image_hash_required_for_mime')
        length = response.getheader('Content-Length')
        if length is not None and (not re.fullmatch(r'\d+', length) or not 0 < int(length) <= maximum):
            raise AdminError('published_image_size_invalid')
        chunks = []
        size = 0
        while size <= maximum:
            chunk = response.read1(min(65536, maximum + 1 - size))
            if not chunk: break
            chunks.append(chunk)
            size += len(chunk)
        if expired.is_set() or not 0 < size <= maximum or (length is not None and size != int(length)):
            raise AdminError('published_image_size_invalid')
        data = b''.join(chunks)
        actual_sha = hashlib.sha256(data).hexdigest()
        if approved_sha is not None and actual_sha != approved_sha:
            raise AdminError('published_image_bytes_changed')
        with Image.open(io.BytesIO(data)) as image:
            cli = getattr(getattr(config, 'image', None), 'provider', None) == 'subscription'
            allowed = (((1376, 768),) if cli else ((1600, 900),)) if cover else ((1200, 675), (1080, 1350))
            if image.format != 'WEBP' or image.size not in allowed:
                raise AdminError('published_image_format_or_dimensions_invalid')
            image.load()
            dimensions = list(image.size)
        return {'sha256': actual_sha, 'approved_sha_verified': approved_sha is not None,
                'bytes': size, 'dimensions': dimensions, 'content_type': content_type,
                'mime_defect': content_type != 'image/webp'}
    except AdminError:
        raise
    except Exception:
        raise AdminError('published_image_unavailable') from None
    finally:
        if watchdog: watchdog.cancel()
        if response: response.close()
        if connection: connection.close()


def logged(name, *, post=False, budget=False):
    """Log one line per step: post id, step name, outcome code and elapsed ms; never URLs, payloads or API text."""
    def wrap(function):
        @functools.wraps(function)
        async def run(self, *args, **kwargs):
            if post and args:
                self.step_post = str(args[0])
            started, outcome = time.monotonic(), 'ok'
            try:
                if budget:
                    try:
                        async with asyncio.timeout(self.config.admin.publish_timeout_seconds):
                            return await function(self, *args, **kwargs)
                    except TimeoutError:
                        raise AdminError('readback_timeout') from None
                return await function(self, *args, **kwargs)
            except asyncio.CancelledError:
                outcome = 'cancelled'
                raise
            except AdminError as error:
                outcome = str(error) if CODE.fullmatch(str(error)) else 'AdminError'
                raise
            except BaseException as error:
                outcome = 'error_' + type(error).__name__
                raise
            finally:
                LOG.info('NailBlog publish post=%s step=%s outcome=%s elapsed_ms=%d',
                         getattr(self, 'step_post', '-'), name, outcome, (time.monotonic() - started) * 1000)
        return run
    return wrap


def norm_title(value):
    """Comparable Vietnamese title: NFC, casefold, collapsed whitespace."""
    return ' '.join(unicodedata.normalize('NFC', value if isinstance(value, str) else '').casefold().split())


class Admin:
    def __init__(self, config):
        self.config, self.env = config, binding(config)
        self.guard = Guard(config)
        self.context = self.manager = self.lock = self.page = None
        self.file_hashes = {}
        self.approved_image_hashes = None
        self.step_post = '-'

    async def __aenter__(self):
        if self.config.mode == 'preview':
            raise AdminError('preview_browser_disabled')
        profile = Path(self.config.admin.profile_path)
        if not self.config.admin.profile_path or not profile.is_absolute() or profile.is_symlink():
            raise AdminError('private_profile_required')
        profile.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'posix' and (profile.stat().st_mode & 0o077 or profile.stat().st_uid != os.getuid()):
            raise AdminError('private_profile_required')
        self.lock = ProcessLock(profile)
        self.lock.__enter__()
        try:
            marker = profile / 'environment.json'
            if marker.exists() and json.loads(marker.read_text(encoding='utf-8')) != self.env:
                raise AdminError('profile_environment_mismatch')
            write_atomic(marker, self.env)
            from playwright.async_api import async_playwright
            self.manager = await async_playwright().start()
            self.context = await self.manager.chromium.launch_persistent_context(
                str(profile), headless=self.config.admin.headless, viewport={'width': 1440, 'height': 900},
                timezone_id='Asia/Ho_Chi_Minh', service_workers='block', permissions=['clipboard-read', 'clipboard-write'])
            await self.context.route('**/*', self.guard.route)
            self.page = await self.context.new_page()
            self.page.set_default_timeout(self.config.admin.timeout_seconds * 1000)
            return self
        except BaseException:
            await self.__aexit__()
            raise

    @asynccontextmanager
    async def step(self, name):
        """Sub-step log line (same shape as `logged`): never URLs, payloads or API text."""
        started, outcome = time.monotonic(), 'ok'
        try:
            yield
        except asyncio.CancelledError:
            outcome = 'cancelled'
            raise
        except AdminError as error:
            outcome = str(error) if CODE.fullmatch(str(error)) else 'AdminError'
            raise
        except BaseException as error:
            outcome = 'error_' + type(error).__name__
            raise
        finally:
            LOG.info('NailBlog publish post=%s step=%s outcome=%s elapsed_ms=%d',
                     self.step_post, name, outcome, (time.monotonic() - started) * 1000)

    async def bounded(self, awaitable, what):
        """Bounded shutdown step: a hung close must never hold the process or the profile lock."""
        try:
            await asyncio.wait_for(awaitable, CLOSE_TIMEOUT_SECONDS)
        except TimeoutError:
            LOG.warning('NailBlog publish post=%s step=%s outcome=close_timeout elapsed_ms=%d',
                        self.step_post, what, CLOSE_TIMEOUT_SECONDS * 1000)

    async def __aexit__(self, *args):
        try:
            if self.context:
                await self.bounded(self.context.close(), 'context_close')
        finally:
            try:
                if self.manager:
                    await self.bounded(self.manager.stop(), 'manager_stop')
            finally:
                if self.lock:
                    self.lock.__exit__()

    def response(self, r, path, method='GET'):
        u = urlsplit(r.url)
        return (u.scheme + '://' + u.netloc == self.config.contract.api_origin and
                u.path == path and r.request.method == method)

    async def json_response(self, r):
        if not 200 <= r.status < 300:
            raise AdminError('admin_http_error')
        try:
            data = await r.json()
        except Exception:
            raise AdminError('admin_invalid_json') from None
        if not isinstance(data, dict):
            raise AdminError('admin_invalid_schema')
        return data

    async def create_receipt(self, response, expected):
        """A successful Create may return only a JSON GUID, or no usable receipt.

        Resolve an unknown response by reading existing Drafts, never by issuing
        another Create. The uploaded payload, rather than the title alone, binds
        the result to this operation.
        """
        if not 200 <= response.status < 300:
            raise AdminError('admin_http_error')
        try:
            data = await response.json()
        except Exception:
            data = None
        post_id = data.get('id') if isinstance(data, dict) else data
        if isinstance(post_id, str) and ID.fullmatch(post_id):
            actual = await self.read(post_id)
        else:
            candidates = await self.find_drafts(expected)
            if len(candidates) != 1:
                raise AdminError('create_requires_manual_reconciliation')
            actual = candidates[0]
            post_id = actual.get('id') if isinstance(actual, dict) else None
        if (not isinstance(post_id, str) or not ID.fullmatch(post_id) or
                not matches(actual, expected, self.config, post_id=post_id)):
            raise AdminError('create_outcome_uncertain')
        return actual

    async def login(self):
        from playwright.async_api import TimeoutError as BrowserTimeout
        await self.page.goto(self.config.contract.site_origin + '/', wait_until='domcontentloaded')
        email = self.page.locator('#login-email:visible')
        opening = self.page.get_by_role('button', name=re.compile(r'^(Đăng nhập|Login|Sign In)$', re.I))
        try:
            # DOMContentLoaded precedes React hydration on a cold browser. Wait
            # for a rendered control; absence is never proof of authentication.
            await email.or_(opening).first.wait_for(state='visible', timeout=self.config.admin.timeout_seconds * 1000)
        except BrowserTimeout:
            # A session without login controls is accepted only by a NEW
            # authenticated listing request and the verified management heading.
            return await self.list_first()
        if not await email.is_visible():
            await opening.click()
        await email.wait_for(state='visible', timeout=self.config.admin.timeout_seconds * 1000)
        credentials = load_credentials(self.config.admin.credential_file)
        try:
            await self.page.locator('#login-email').fill(credentials['email'])
            await self.page.locator('#login-password').fill(credentials['password'])
            def valid(q):
                data = q.post_data_json
                return (data.get('email') == credentials['email'] and data.get('password') == credentials['password'])
            with self.guard.allow('POST', SIGNIN, valid, login=True):
                async with self.page.expect_response(lambda r: self.response(r, SIGNIN, 'POST')) as response:
                    await self.page.get_by_role('dialog').get_by_role(
                        'button', name=re.compile(r'^(Đăng nhập|Login|Sign In)$', re.I)).click()
                if (await response.value).status != 200:
                    raise AdminError('login_rejected_or_mfa')
                await self.page.get_by_role('dialog').wait_for(state='hidden')
        finally:
            credentials.clear()

    async def ready(self):
        await self.page.goto(self.config.contract.site_origin + '/admin/blogs', wait_until='domcontentloaded')
        try:
            await self.page.get_by_role('heading', name='Blog Management', exact=True).wait_for(timeout=5000)
        except Exception:
            existing = await self.login()
            if existing is not None:
                return existing
        return await self.list_first()

    async def list_first(self):
        def listing_request(request):
            url = urlsplit(request.url)
            return (request.method == 'GET' and url.scheme + '://' + url.netloc == self.config.contract.api_origin
                    and url.path == BLOGS)
        # Create triggers a background listing refresh. Observe a new request
        # before navigating, so a response from that old, canceled refresh cannot
        # be mistaken for this navigation's listing.
        async with self.page.expect_request(listing_request) as pending:
            await self.page.goto(self.config.contract.site_origin + '/admin/blogs', wait_until='domcontentloaded')
        request = await pending.value
        response = await request.response()
        if response is None:
            raise AdminError('admin_listing_response_missing')
        data = await self.json_response(response)
        await self.page.get_by_role('heading', name='Blog Management', exact=True).wait_for()
        if not isinstance(data.get('items'), list):
            raise AdminError('list_schema_changed')
        return data

    async def locate(self, post_id):
        if not ID.fullmatch(str(post_id)):
            raise AdminError('invalid_admin_id')
        data = await self.list_first()
        for _ in range(20):
            rows = data.get('items', [])
            for index, row in enumerate(rows):
                if row.get('id') == post_id:
                    locator = self.page.locator('tbody tr')
                    await locator.first.wait_for()
                    if await locator.count() != len(rows):
                        raise AdminError('listing_identity_changed')
                    return locator.nth(index)
            button = self.page.get_by_role('button', name='Trang sau', exact=True)
            if not await button.count() or not await button.is_enabled():
                break
            async with self.page.expect_response(lambda r: self.response(r, BLOGS)) as response:
                await button.click()
            data = await self.json_response(await response.value)
        raise AdminError('admin_post_not_found')

    async def read(self, post_id):
        row = await self.locate(post_id)
        async with self.page.expect_response(lambda r: self.response(r, BLOGS + '/' + post_id)) as response:
            await row.get_by_role('button', name='Edit', exact=True).click()
        result = await self.json_response(await response.value)
        dialog = self.page.get_by_role('dialog')
        await dialog.wait_for()
        await dialog.get_by_role('button', name='Cancel', exact=True).click()
        await dialog.wait_for(state='hidden')
        if result.get('id') != post_id:
            raise AdminError('readback_identity_mismatch')
        return result

    async def find_drafts(self, expected):
        data, ids = await self.list_first(), []
        for _ in range(20):
            ids += [x['id'] for x in data['items'] if x.get('status') == 'Draft']
            next_page = self.page.get_by_role('button', name='Trang sau', exact=True)
            if not await next_page.count() or not await next_page.is_enabled():
                break
            async with self.page.expect_response(lambda r: self.response(r, BLOGS)) as response:
                await next_page.click()
            data = await self.json_response(await response.value)
        else:
            raise AdminError('reconciliation_page_limit')
        results = []
        for post_id in set(ids):
            actual = await self.read(post_id)
            if matches(actual, expected, self.config):
                results.append(actual)
        return results

    async def find_duplicates(self, title, slug):
        """Read-only scan of EVERY listing page and status for the same slug or normalized Vietnamese title."""
        wanted_title, found, seen = norm_title(title), [], set()
        data = await self.list_first()
        for _ in range(200):
            for row in data['items']:
                if not isinstance(row, dict):
                    continue
                titles, slugs = [row.get('title')], [row.get('slug')]
                for item in row.get('translations') or []:
                    if isinstance(item, dict) and str(item.get('languageCode', '')).upper() == 'VI':
                        titles.append(item.get('title'))
                        slugs.append(item.get('slug'))
                hit = ((bool(slug) and slug in slugs) or
                       (bool(wanted_title) and any(norm_title(t) == wanted_title for t in titles)))
                if hit and row.get('id') not in seen:
                    seen.add(row.get('id'))
                    first = lambda xs: next((x for x in xs if isinstance(x, str) and x), '')
                    found.append({'id': row.get('id'), 'title': first(titles), 'slug': first(slugs),
                                  'status': row.get('status')})
            next_page = self.page.get_by_role('button', name='Trang sau', exact=True)
            if not await next_page.count() or not await next_page.is_enabled():
                return found
            async with self.page.expect_response(lambda r: self.response(r, BLOGS)) as response:
                await next_page.click()
            data = await self.json_response(await response.value)
        raise AdminError('duplicate_scan_page_limit')

    async def paste_html(self, content):
        # Clipboard + keyboard paste is a real Quill user interaction; no DOM/editor state injection.
        await self.page.evaluate('''async html => {
            await navigator.clipboard.write([new ClipboardItem({
                'text/html': new Blob([html], {type:'text/html'})})]);
        }''', content)
        editor = self.page.get_by_role('dialog').locator('.ql-editor[contenteditable="true"]')
        # Clicking the editor centre can land on a contenteditable=false figure and leave the caret outside.
        blocks = editor.locator('xpath=.//*[self::p or self::h1 or self::h2 or self::h3 or self::h4 or self::h5 '
                                'or self::h6 or self::li][not(ancestor::figure)]')
        if await blocks.count():
            await blocks.first.click()
        else:
            await editor.click()
        if not await self.page.evaluate("""() => {
            const editor = document.querySelector('[role=dialog] .ql-editor'), selection = getSelection();
            return !!editor && selection.rangeCount > 0 && editor.contains(selection.anchorNode)
                && editor.contains(selection.getRangeAt(0).startContainer);
        }"""):
            raise AdminError('editor_focus_lost')
        await editor.press('ControlOrMeta+a')
        await editor.press('ControlOrMeta+v')
        # The editor may already hold old HTML; wait (bounded) for the paste to produce the expected markup.
        await self.page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        for _ in range(50):
            rendered = await editor.inner_html()
            if same_html(rendered, content):
                return rendered
            await asyncio.sleep(0.1)
        raise AdminError('editor_roundtrip_loss')

    async def probe(self, *, screenshot_path=None):
        data = await self.ready()
        sample = await self.read(data['items'][0]['id']) if data['items'] else None
        await self.page.get_by_role('button', name='Create Blog', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.wait_for()
        categories = await dialog.locator('select').nth(0).locator('option').evaluate_all(
            '(xs) => xs.map(x => ({id:x.value,label:x.textContent}))')
        defaults = {'status': await dialog.locator('select').nth(1).input_value()}
        content = ('<h2>Kiểm tra nội dung tiếng Việt</h2><p>Nội dung mẫu cho bước kiểm tra trình soạn thảo.</p>'
                   '<figure><img src="' + self.config.contract.api_origin + '/probe.webp" '
                   'alt="Mẫu móng tay minh họa cho bài kiểm tra"><figcaption>Ảnh minh họa staging</figcaption></figure>')
        try:
            await self.paste_html(content)
            roundtrip = True
        except AdminError as error:
            if str(error) not in ('editor_roundtrip_loss', 'editor_focus_lost'):
                raise
            roundtrip = False
        rendered = await dialog.locator('.ql-editor[contenteditable="true"]').inner_html()
        if screenshot_path:
            await dialog.screenshot(path=str(screenshot_path))
        await dialog.get_by_role('button', name='Cancel', exact=True).click()
        return {**self.env, 'authenticated': True, 'admin_count_on_page': len(data['items']),
                'categories': categories, 'defaults': defaults, 'sample_id': sample.get('id') if sample else None,
                'sample_fields': sorted(sample) if sample else [], 'editor_roundtrip': roundtrip,
                'expected_editor_html': content, 'actual_editor_html': rendered,
                'writes_performed': False}

    def write_ready(self):
        if self.config.mode != 'live' or not self.config.contract.verified:
            raise AdminError('verified_contract_required')
        try:
            report = json.loads(Path(self.config.contract.verification_ref).read_text(encoding='utf-8'))
            if any(report.get(k) != v for k, v in self.env.items()) or report.get('authenticated') is not True or report.get('editor_roundtrip') is not True:
                raise ValueError()
        except Exception:
            raise AdminError('contract_report_mismatch_or_editor_loss') from None

    async def upload(self, button, path):
        self.write_ready()
        target = Path(path)
        if not target.is_file() or target.is_symlink() or target.suffix.lower() != '.webp':
            raise AdminError('local_webp_required')
        data = target.read_bytes()
        if self.file_hashes.get(str(target.resolve())) != hashlib.sha256(data).hexdigest():
            raise AdminError('image_bytes_changed')
        with self.guard.allow('POST', UPLOAD, lambda q: q.headers.get('content-type', '').startswith('multipart/form-data')):
            async with self.page.expect_response(lambda r: self.response(r, UPLOAD, 'POST')) as response:
                async with self.page.expect_file_chooser() as chooser:
                    await self.page.get_by_role('dialog').get_by_role('button', name=button, exact=True).click()
                await (await chooser.value).set_files({'name':target.name, 'mimeType':'image/webp', 'buffer':data})
            return await self.json_response(await response.value)

    async def select_form_option(self, index, value):
        """Radix's native select is aria-hidden; interact with its visible combobox."""
        dialog = self.page.get_by_role('dialog')
        native = dialog.locator('select').nth(index)
        options = await native.locator('option').evaluate_all(
            '(xs) => xs.map(x => ({value:x.value,label:x.textContent}))')
        labels = [option['label'] for option in options if option['value'] == value]
        if len(labels) != 1:
            raise AdminError('form_option_missing_or_ambiguous')
        await dialog.get_by_role('combobox').nth(index).click()
        await self.page.get_by_role('option', name=labels[0], exact=True).click()
        if await native.input_value() != value:
            raise AdminError('form_option_not_selected')

    async def save_draft(self, payload, before_create, *, cover_path, image_paths):
        self.write_ready()
        payload = deepcopy(payload)
        expected = snapshot(payload, self.config)
        if len(image_paths) != len(expected['images']):
            raise AdminError('image_path_count_mismatch')
        await self.ready()
        await self.page.get_by_role('button', name='Create Blog', exact=True).click()
        dialog = self.page.get_by_role('dialog')
        await dialog.wait_for()
        await self.select_form_option(1, 'Draft')
        await self.select_form_option(0, expected['categoryId'])
        vi = expected['translations'][0]
        for placeholder, key in (('Enter title in Vietnamese (5-200 characters)', 'title'),
                                 ('Enter summary in Vietnamese (max 500 characters)', 'summary'),
                                 ('SEO title (50-70 characters recommended)', 'metaTitle'),
                                 ('SEO description (120-160 characters recommended)', 'metaDescription')):
            await dialog.get_by_placeholder(placeholder, exact=True).fill(vi[key])
        await self.paste_html(vi['content'])
        await dialog.locator('#publishedAt').fill(instant(expected['publishedAt']).astimezone(VN_TZ).strftime('%Y-%m-%dT%H:%M'))
        uploaded = [await self.upload('Upload Cover Image', cover_path)]
        for path in image_paths:
            uploaded.append(await self.upload('Upload Images', path))
        payload['coverImage'], payload['images'] = uploaded[0], uploaded[1:]
        content = vi['content']
        for old, new in zip(expected['images'], uploaded[1:]):
            content = content.replace(old['imageUrl'], new['imageUrl'])
        payload['translations'][0]['content'] = content
        snapshot(payload, self.config)
        await self.paste_html(content)
        for tag in expected['hashtags']:
            field = dialog.get_by_placeholder('Add hashtag (2-50 characters each)', exact=True)
            await field.fill(tag)
            await field.press('Enter')
        before_create(payload)
        def valid(q):
            body = q.post_data_json
            return matches(body, payload, self.config) and not body.get('id')
        limit = self.config.admin.create_timeout_seconds
        from playwright.async_api import TimeoutError as BrowserTimeout
        with self.guard.allow('POST', BLOGS, valid):
            # Exactly one click, never retried: a timeout leaves the Create outcome UNKNOWN.
            try:
                async with asyncio.timeout(limit):
                    async with self.page.expect_response(lambda r: self.response(r, BLOGS, 'POST'),
                                                         timeout=limit * 1000) as response:
                        await dialog.get_by_role('button', name='Create', exact=True).click()
                    created_response = await response.value
            except (TimeoutError, BrowserTimeout):
                raise AdminError('create_timeout') from None
        return await self.create_receipt(created_response, payload)

    @logged('image_bytes')
    async def image_bytes(self, expected):
        images = [expected['coverImage'], *expected['images']]
        hashes = self.approved_image_hashes
        if hashes is not None and (not isinstance(hashes, list) or len(hashes) != len(images)):
            raise AdminError('approved_image_hashes_invalid')
        checks = []
        for index, image in enumerate(images):
            checks.append(await asyncio.to_thread(public_webp, self.config, image['imageUrl'],
                          cover=index == 0, approved_sha=hashes[index] if hashes is not None else None))
        return checks

    @logged('draft_image_decode', post=True)
    async def draft_image_decode(self, post_id, expected):
        """Use the real edit dialog's images, never an injected test DOM."""
        row = await self.locate(post_id)
        async with self.page.expect_response(lambda r: self.response(r, BLOGS + '/' + post_id)) as response:
            await row.get_by_role('button', name='Edit', exact=True).click()
        actual = await self.json_response(await response.value)
        dialog = self.page.get_by_role('dialog')
        await dialog.wait_for()
        try:
            if not matches(actual, expected, self.config, post_id=post_id):
                raise AdminError('draft_changed')
            for image in [expected['coverImage'], *expected['images']]:
                await dialog.locator('img[src=' + json.dumps(image['imageUrl']) + ']').first.scroll_into_view_if_needed()
                await self.page.wait_for_function('''url => Array.from(document.querySelectorAll('[role=dialog] img')).some(i =>
                    i.src === url && i.complete && i.naturalWidth > 0 && i.naturalHeight > 0)''', arg=image['imageUrl'])
            for image in Markup(expected['translations'][0]['content']).images:
                await self.page.wait_for_function('''wanted => Array.from(document.querySelectorAll('[role=dialog] img')).some(i =>
                    i.src === wanted.src && i.getAttribute('alt') === wanted.alt && i.closest('figure') &&
                    i.closest('figure').querySelector('figcaption')?.textContent.trim().length > 0)''', arg=image)
        finally:
            if await dialog.is_visible():
                await dialog.get_by_role('button', name='Cancel', exact=True).click()
                await dialog.wait_for(state='hidden')

    @logged('publish', post=True)
    async def publish(self, post_id, expected, before_click):
        self.write_ready()
        actual = await self.read(post_id)
        if not matches(actual, expected, self.config, post_id=post_id):
            raise AdminError('draft_changed')
        await self.image_bytes(expected)
        await self.draft_image_decode(post_id, expected)
        # Preflight may span several reads. Recheck content immediately before
        # the durable claim; its callback still enforces pause/approval/window.
        actual = await self.read(post_id)
        if not matches(actual, expected, self.config, post_id=post_id):
            raise AdminError('draft_changed')
        row = await self.locate(post_id)
        before_click()
        with self.guard.allow('PATCH', BLOGS + '/' + post_id + '/status',
                              lambda q: q.post_data_json == {'status': 'Published'}):
            async with self.page.expect_response(lambda r: self.response(r, BLOGS + '/' + post_id + '/status', 'PATCH')) as response:
                await row.get_by_role('combobox').click()
                await self.page.get_by_role('option', name='Published', exact=True).click()
            if not 200 <= (await response.value).status < 300:
                raise AdminError('publish_http_error')

    @logged('published_readback', post=True, budget=True)
    async def published_readback(self, post_id, expected):
        async with self.step('readback_admin_row'):
            admin = await self.read(post_id)
            if not matches(admin, expected, self.config, status='Published', post_id=post_id, published=True):
                raise AdminError('published_admin_mismatch')
        slug = admin.get('slug')
        if not isinstance(slug, str) or not re.fullmatch(r'[a-zA-Z0-9_-]+', slug):
            raise AdminError('public_slug_invalid')
        public_path = '/api/v1/blogs/' + slug
        async with self.step('readback_public_api'):
            async with self.page.expect_response(lambda r: self.response(r, public_path)) as response:
                result = await self.page.goto(self.config.contract.site_origin + '/blog/' + slug, wait_until='domcontentloaded')
            public = await self.json_response(await response.value)
            if not result or result.status != 200 or not matches(public, expected, self.config, status='Published', post_id=post_id, published=True):
                raise AdminError('published_public_mismatch')
            await self.page.get_by_role('heading', name=expected['translations'][0]['title'], exact=True).wait_for()
        public_evidence = await self.anonymous_readback(slug, expected)
        return {'readback': {'admin_id': post_id, 'slug': slug, 'publishedAt': admin['publishedAt'],
                             **self.env, 'snapshot': snapshot(admin, self.config), **public_evidence}}

    @logged('anonymous_readback', budget=True)
    async def anonymous_readback(self, slug, expected):
        """Prove actual unauthenticated page/images and a homepage card click, never inject DOM."""
        image_checks = await self.image_bytes(expected)
        async with self.step('anonymous_browser_launch'):
            browser = await self.manager.chromium.launch(headless=self.config.admin.headless)
        try:
            async with self.step('anonymous_article'):
                context = await browser.new_context(viewport={'width':1440,'height':900}, timezone_id='Asia/Ho_Chi_Minh',
                                                    service_workers='block')
                guard = Guard(self.config)
                await context.route('**/*', guard.route)
                page = await context.new_page()
                page.set_default_timeout(self.config.admin.timeout_seconds*1000)
                response = await page.goto(self.config.contract.site_origin+'/blog/'+slug,wait_until='domcontentloaded')
                if not response or response.status != 200:
                    raise AdminError('anonymous_article_unavailable')
                await page.get_by_role('heading',name=expected['translations'][0]['title'],exact=True).wait_for()
                expected_images = [expected['coverImage'],*expected['images']]
                for image in expected_images:
                    await page.locator('img[src='+json.dumps(image['imageUrl'])+']').first.scroll_into_view_if_needed()
                    await page.wait_for_function('''url => Array.from(document.images).some(img =>
                        img.src === url && img.complete && img.naturalWidth > 0 && img.naturalHeight > 0)''',
                        arg=image['imageUrl'])
                for image in Markup(expected['translations'][0]['content']).images:
                    await page.wait_for_function('''wanted => Array.from(document.images).some(img =>
                        img.src === wanted.src && img.getAttribute('alt') === wanted.alt &&
                        img.closest('figure') && img.closest('figure').querySelector('figcaption'))''', arg=image)
            async with self.step('anonymous_homepage'):
                response = await page.goto(self.config.contract.site_origin+'/',wait_until='domcontentloaded')
                if not response or response.status != 200:
                    raise AdminError('anonymous_homepage_unavailable')
            navigation = await self.homepage_card_click(page, slug, expected['translations'][0]['title'])
            return {'anonymous_homepage_click':True,'anonymous_image_count':len(expected_images),
                    'homepage_navigation':navigation,'image_byte_checks':image_checks}
        finally:
            await self.bounded(browser.close(), 'browser_close')

    @logged('homepage_card_click')
    async def homepage_card_click(self, page, slug, title):
        """Click the rendered article anchor or the actual clickable card heading."""
        anchors = page.locator('a[href="/blog/'+slug+'"]:visible')
        # React hydration may precede either implementation. Wait for a real
        # visible card, rather than interpreting an initial empty DOM as absence.
        headings = page.get_by_role('heading', name=title, exact=True).and_(page.locator(':visible'))
        await anchors.or_(headings).first.wait_for()
        if await anchors.count():
            card, implementation = anchors.first, 'anchor'
        else:
            card, implementation = headings.first, 'heading'
        await card.scroll_into_view_if_needed()
        await card.click()
        await page.wait_for_url(self.config.contract.site_origin+'/blog/'+slug)
        await page.get_by_role('heading', name=title, exact=True).wait_for()
        return implementation
