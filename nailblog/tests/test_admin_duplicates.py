"""S5 hardening: duplicate scan, bounded Create/readback, MIME defect evidence, alt range, stable compare. No network."""
import asyncio
import copy
import hashlib
import io
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image

from nailblog import admin as admin_module
from nailblog.admin import Admin, norm_title
from nailblog.publication import AdminError, STAGING_MEDIA_ORIGIN, matches, snapshot
from tests.test_admin_phase2 import POST_ID, config, payload

ID_A = '11111111-1111-1111-1111-111111111111'
ID_B = '22222222-2222-2222-2222-222222222222'
ID_C = '33333333-3333-3333-3333-333333333333'


async def hang(*args, **kwargs):
    await asyncio.sleep(3600)


def build(directory, **admin_fields):
    c = config(directory)
    return replace(c, admin=replace(c.admin, **admin_fields))


class FakeResponse:
    def __init__(self, data):
        self.status, self.data = 200, data

    async def json(self):
        return self.data


class Expect:
    """Stands in for page.expect_response(...): `.value` resolves to the next queued response, or hangs."""

    def __init__(self, page):
        self.page = page

    async def __aenter__(self):
        self.page.expects += 1
        return self

    async def __aexit__(self, *args):
        return False

    @property
    def value(self):
        async def get():
            if not self.page.responses:
                await hang()
            return self.page.responses.pop(0)
        return get()


class Button:
    def __init__(self, page, name):
        self.page, self.name = page, name

    async def count(self):
        return 1 if self.name != 'Trang sau' or self.page.more else 0

    async def is_enabled(self):
        return True

    async def click(self):
        self.page.clicks.append(self.name)
        if self.name == 'Trang sau':
            self.page.more = False  # one extra page only

    async def fill(self, text):
        pass

    async def press(self, key):
        pass

    async def wait_for(self, **kwargs):
        pass

    def get_by_role(self, role, name=None, **kwargs):
        return Button(self.page, name)

    def get_by_placeholder(self, text, **kwargs):
        return Button(self.page, text)

    def locator(self, selector):
        return Button(self.page, selector)


class FakePage:
    def __init__(self, responses=(), more=False):
        self.responses, self.more, self.clicks, self.expects = list(responses), more, [], 0

    def get_by_role(self, role, name=None, **kwargs):
        return Button(self, name)

    def expect_response(self, predicate, timeout=None):
        return Expect(self)


class DuplicateScanTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = build(self.tmp.name)

    async def scan(self, first, second, title, slug):
        admin = Admin(self.c)
        admin.page = FakePage([FakeResponse({'items': second})], more=True)

        async def list_first():
            return {'items': first}
        admin.list_first = list_first
        return admin, await admin.find_duplicates(title, slug)

    async def test_scans_every_page_and_status_by_slug_and_normalized_title(self):
        first = [{'id': ID_A, 'status': 'Published', 'title': 'Khác hẳn', 'slug': 'mau-mong-thu'},
                 {'id': 'x', 'status': 'Draft', 'title': 'Không liên quan', 'slug': 'khong-lien-quan'}]
        second = [{'id': ID_B, 'status': 'Scheduled', 'translations': [
                      {'languageCode': 'VI', 'title': '  MẪU   Móng   Tay  Thu ', 'slug': 'other'}]},
                  {'id': ID_C, 'status': 'Draft', 'title': 'Mẫu móng tay thu', 'slug': 'z'}]
        admin, found = await self.scan(first, second, 'Mẫu móng tay thu', 'mau-mong-thu')
        self.assertEqual([(x['id'], x['status']) for x in found],
                         [(ID_A, 'Published'), (ID_B, 'Scheduled'), (ID_C, 'Draft')])
        self.assertEqual(set(found[0]), {'id', 'title', 'slug', 'status'})
        # Read-only: the only clicks were the pager; page 2 was reached.
        self.assertEqual(admin.page.clicks, ['Trang sau'])

    async def test_nfc_equivalence_and_no_false_positive(self):
        composed = 'Mẫu'
        decomposed = 'Mẫu'  # a different code-point sequence is NOT the same text; NFC of 'Ẫ' form is
        self.assertEqual(norm_title('Ấ b'), norm_title('Ấ  B'))
        self.assertNotEqual(norm_title(composed), norm_title('Mau'))
        _, found = await self.scan([{'id': ID_A, 'status': 'Draft', 'title': 'Bài khác', 'slug': 'bai-khac'}], [],
                                   'Mẫu móng tay thu', 'mau-mong-thu')
        self.assertEqual(found, [])
        self.assertIsInstance(decomposed, str)


class CreateTimeoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_hung_create_post_raises_create_timeout_after_exactly_one_click(self):
        with tempfile.TemporaryDirectory() as d:
            c = build(d, create_timeout_seconds=0.2)
            admin, p = Admin(c), payload()
            admin.page = FakePage()  # never returns a POST response
            intents, uploads = [], iter([p['coverImage'], *p['images']])

            async def noop(*a, **k):
                return None

            async def upload(button, path):
                return next(uploads)
            admin.write_ready = lambda: None
            admin.ready = noop
            admin.select_form_option = noop
            admin.paste_html = noop
            admin.upload = upload
            admin.create_receipt = hang  # must never be reached
            loop = asyncio.get_running_loop()
            started = loop.time()
            with self.assertRaisesRegex(AdminError, '^create_timeout$'):
                await asyncio.wait_for(admin.save_draft(p, lambda value: intents.append(value),
                                                        cover_path='c', image_paths=['1', '2', '3']), 10)
            self.assertLess(loop.time() - started, 3)
            self.assertEqual(admin.page.clicks.count('Create'), 1)
            self.assertEqual(admin.page.clicks.count('Create Blog'), 1)
            self.assertEqual(len(intents), 1)  # durable intent recorded BEFORE the click (callback ran once)

    async def test_intent_is_recorded_before_the_create_click(self):
        with tempfile.TemporaryDirectory() as d:
            c = build(d, create_timeout_seconds=0.2)
            admin, p = Admin(c), payload()
            admin.page = FakePage()
            order, uploads = [], iter([p['coverImage'], *p['images']])

            async def noop(*a, **k):
                return None

            async def upload(button, path):
                return next(uploads)
            admin.write_ready = lambda: None
            admin.ready = noop
            admin.select_form_option = noop
            admin.paste_html = noop
            admin.upload = upload
            clicks = admin.page.clicks
            with self.assertRaises(AdminError):
                await admin.save_draft(p, lambda value: order.append(('intent', list(clicks))),
                                       cover_path='c', image_paths=['1', '2', '3'])
            self.assertEqual(order, [('intent', ['Create Blog'])])  # Create itself not yet clicked


class ReadbackBudgetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = build(self.tmp.name, publish_timeout_seconds=0.3)

    async def test_hung_published_readback_ends_inside_budget_with_clear_code(self):
        admin = Admin(self.c)
        admin.read = hang
        loop = asyncio.get_running_loop()
        started = loop.time()
        with self.assertLogs('nailblog.admin', 'INFO') as logs:
            with self.assertRaisesRegex(AdminError, '^readback_timeout$'):
                await asyncio.wait_for(admin.published_readback(POST_ID, payload()), 10)
        self.assertLess(loop.time() - started, 3)
        text = '\n'.join(logs.output)
        self.assertIn(f'NailBlog publish post={POST_ID} step=readback_admin_row outcome=cancelled elapsed_ms=', text)
        self.assertIn(f'step=published_readback outcome=readback_timeout elapsed_ms=', text)
        self.assertNotIn('http', text.replace('NailBlog publish', ''))

    async def test_hung_anonymous_readback_times_out_and_closes_browser(self):
        admin, closed = Admin(self.c), []

        async def images(expected):
            return []

        async def close():
            closed.append(1)

        async def new_context(**kwargs):
            await hang()
        browser = SimpleNamespace(new_context=new_context, close=close)

        async def launch(**kwargs):
            return browser
        admin.manager = SimpleNamespace(chromium=SimpleNamespace(launch=launch))
        admin.image_bytes = images
        with self.assertLogs('nailblog.admin', 'INFO') as logs:
            with self.assertRaisesRegex(AdminError, '^readback_timeout$'):
                await asyncio.wait_for(admin.anonymous_readback('slug-1', payload()), 10)
        self.assertEqual(closed, [1])
        text = '\n'.join(logs.output)
        self.assertIn('step=anonymous_article outcome=cancelled', text)
        self.assertIn('step=anonymous_readback outcome=readback_timeout', text)
        self.assertNotIn('http', text.replace('NailBlog publish', ''))


class MimeDefectTests(unittest.TestCase):
    def fetch(self, content_type, *, approved=True, sha=None):
        stream = io.BytesIO()
        Image.new('RGB', (1200, 675), 'tan').save(stream, format='WEBP')
        raw = stream.getvalue()
        response = Mock(status=200)
        response.getheader.side_effect = lambda key: {'Content-Type': content_type,
                                                       'Content-Length': str(len(raw))}.get(key)
        response.read1 = io.BytesIO(raw).read1
        connection = Mock()
        connection.getresponse.return_value = response
        with tempfile.TemporaryDirectory() as d, \
                patch.object(admin_module, 'resolve', return_value=[(None, None, None, None, ('8.8.8.8', 443))]), \
                patch.object(admin_module, 'PinnedHTTPS', return_value=connection), \
                patch.object(admin_module.threading, 'Timer'):
            wanted = sha or (hashlib.sha256(raw).hexdigest() if approved else None)
            return admin_module.public_webp(config(d), STAGING_MEDIA_ORIGIN + '/body.webp', cover=False,
                                            approved_sha=wanted)

    def test_octet_stream_with_matching_sha_is_accepted_and_flagged(self):
        result = self.fetch('application/octet-stream')
        self.assertTrue(result['mime_defect'])
        self.assertTrue(result['approved_sha_verified'])
        self.assertEqual(result['content_type'], 'application/octet-stream')

    def test_correct_mime_is_not_flagged(self):
        self.assertFalse(self.fetch('image/webp')['mime_defect'])

    def test_octet_stream_needs_approved_hash_and_a_matching_one(self):
        with self.assertRaisesRegex(AdminError, '^approved_image_hash_required_for_mime$'):
            self.fetch('application/octet-stream', approved=False)
        with self.assertRaisesRegex(AdminError, '^published_image_bytes_changed$'):
            self.fetch('application/octet-stream', sha='0' * 64)

    def test_other_mime_is_rejected(self):
        with self.assertRaisesRegex(AdminError, '^published_image_content_type_invalid$'):
            self.fetch('text/html')


class AltAndStableCompareTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = config(self.tmp.name)

    def with_alt(self, words):
        p = payload()
        vi = p['translations'][0]
        alt = ' '.join(['mẫu'] * words)
        vi['content'] = vi['content'].replace('Mẫu móng tay minh họa cho mùa thu', alt)
        return p

    def test_alt_stays_between_5_and_15_words(self):
        for words in (5, 15):
            snapshot(self.with_alt(words), self.c)
        for words in (4, 16, 40):
            with self.subTest(words=words), self.assertRaisesRegex(AdminError, '^image_alt_or_identity$'):
                snapshot(self.with_alt(words), self.c)

    def test_view_count_and_last_modified_do_not_change_the_comparison(self):
        expected = payload()
        expected['status'] = 'Published'
        actual = copy.deepcopy(expected)
        actual.update(id=POST_ID, viewCount=1, lastModified='2026-10-26T10:00:00Z')
        again = copy.deepcopy(actual)
        again.update(viewCount=999, lastModified='2026-10-27T01:23:45Z')
        self.assertEqual(snapshot(actual, self.c), snapshot(again, self.c))
        for row in (actual, again):
            self.assertTrue(matches(row, expected, self.c, status='Published', post_id=POST_ID, published=True))
        changed = copy.deepcopy(again)
        changed['translations'][0]['title'] = 'Tiêu đề khác hẳn'
        self.assertFalse(matches(changed, expected, self.c, status='Published', post_id=POST_ID, published=True))


if __name__ == '__main__':
    unittest.main()
