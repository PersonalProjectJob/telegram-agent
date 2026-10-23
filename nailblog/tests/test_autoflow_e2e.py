"""S9: autoflow wired end to end.

Fakes: text model (service.draft), Gemini transport, admin adapter, Telegram client, frozen clock.
Real: State, ApprovalStore, PaidLedger, ImagePipeline, OnePost, autoflow engine, daily.run_due, service.publish_pass,
the /nailblog command router and the startup gate, all on a temporary SQLite database. No network.
"""
import asyncio
import base64
import contextlib
import copy
import io
import json
import os
import re
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

from nailblog import __main__ as cli
from nailblog import daily
from nailblog.approvals import ApprovalStore
from nailblog.autoflow import AutoflowRuns
from nailblog.commands import decide_approval, make_owner_notice_sender, parse, resume, route_command
from nailblog.config import (PRODUCTION_MEDIA_ORIGIN, AdminConfig, AutoflowConfig, BudgetConfig, ContractConfig,
                             ImageConfig)
from nailblog.images.pipeline import ImagePipeline
from nailblog.images.provider import GeminiImages
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State, startup_binding
from nailblog.telegram import make_owner_media_sender
from nailblog.usage import BudgetExceeded
from tests.fakes import make_article
from tests.helpers import OWNER, dm, load, settings, write_config
from tests.test_real_images import envelope, fixture_cover, fixture_image

OTHER = OWNER + 1
SLOT = datetime(2026, 10, 25, 19, 0, tzinfo=VN_TZ)
SLOT_TEXT = '2026-10-25T19:00+07:00'
START = SLOT - timedelta(hours=20)            # inside the 24h start window, long before the 16:00 image deadline
GUID = '87654321-1234-1234-1234-123456789abc'
CATEGORY_IDS = {'hot-trend': GUID, 'nail-job': GUID.replace('87', '88'), 'nail-salon': GUID.replace('87', '89'),
                'did-you-know': GUID.replace('87', '8a')}
REMOTE_ID = '12345678-1234-1234-1234-123456789abc'


class FakeGemini:
    """Transport with the real GeminiImages signature: ListModels, countTokens, one fresh PNG per generation."""

    def __init__(self):
        self.calls = []
        self.generated = 0

    def __call__(self, url, payload, headers, timeout, method='POST'):
        path = urlsplit(url).path
        if method == 'GET':
            self.calls.append('list')
            return {'models': [{'name': 'models/' + name, 'supportedGenerationMethods': ['generateContent']}
                               for name in ('image-test', 'review-test')]}
        model, action = path.rsplit('/', 1)[1].split(':')
        self.calls.append(action)
        if action == 'countTokens':
            return {'totalTokens': 100}
        self.generated += 1
        data = fixture_image(self.generated + 40)
        return envelope([{'inlineData': {'mimeType': 'image/png', 'data': base64.b64encode(data).decode()}}])


class FakeClient:
    """The Telethon client surface that make_owner_*_sender use. `log` is shared with the whole timeline."""

    def __init__(self, log):
        self.log, self.cards, self.texts, self.albums, self.next_id = log, [], [], [], 100

    def _id(self):
        self.next_id += 1
        return self.next_id

    async def send_file(self, owner, files):
        self.albums.append(len(files))
        return [SimpleNamespace(id=self._id()) for _ in files]

    async def send_message(self, owner, text, **_kw):
        if text.startswith('NailBlog duyệt bài + ảnh'):
            self.cards.append(('images', text))
            self.log.append('card:images')
        elif text.startswith('NailBlog duyệt Draft'):
            self.cards.append(('draft', text))
            self.log.append('card:draft')
        else:
            self.texts.append(text)
        return SimpleNamespace(id=self._id())


class Remote:
    """What the fake admin site holds, shared by every FakeAdmin the code constructs (and survives restarts)."""

    def __init__(self, log):
        self.log, self.record = log, None
        self.uploads = self.creates = self.patches = 0
        self.uploaded_files = 0
        self.fail_after_create = False


class FakeAdmin:
    def __init__(self, remote, config):
        self.remote, self.config = remote, config
        self.guard = SimpleNamespace()
        self.file_hashes = {}
        self.approved_image_hashes = None

    def write_ready(self):
        if getattr(self.remote, 'contract_error', False):
            raise RuntimeError('contract')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def ready(self):
        if getattr(self.remote, 'ready_error', False):
            raise RuntimeError('admin')
        return {}

    async def save_draft(self, payload, before_create, *, cover_path, image_paths):
        remote = self.remote
        remote.uploads += 1                                    # one upload pass (cover + body images)
        remote.uploaded_files += 1 + len(image_paths)
        remote.log.append('upload')
        uploaded = copy.deepcopy(payload)
        uploaded['translations'][0]['content'] = uploaded['translations'][0]['content'].replace(
            '/nailblog-local/', '/uploaded/')
        for image in [uploaded['coverImage'], *uploaded['images']]:
            image['imageUrl'] = image['imageUrl'].replace('/nailblog-local/', '/uploaded/')
        before_create(uploaded)
        remote.creates += 1
        remote.log.append('create')
        remote.record = {**uploaded, 'id': REMOTE_ID, 'status': 'Draft'}
        if remote.fail_after_create:
            remote.fail_after_create = False
            raise TimeoutError('fixture: Create outcome unknown')
        return copy.deepcopy(remote.record)

    async def read(self, admin_id):
        return copy.deepcopy(self.remote.record)

    async def find_drafts(self, payload):
        return [copy.deepcopy(self.remote.record)] if self.remote.record else []

    async def publish(self, admin_id, expected, before_click):
        before_click()                                          # the durable claim, then the single PATCH
        self.remote.patches += 1
        self.remote.log.append('patch')
        self.remote.record = {**self.remote.record, 'status': 'Published'}

    async def published_readback(self, admin_id, expected):
        return {'readback': {'admin_id': admin_id, 'status': 'Published'}}


class Harness:
    def __init__(self, case, *, daily_usd=2.0, run_usd=1.0, enabled=True, mode='live'):
        self.case = case
        self.temp = tempfile.TemporaryDirectory()
        case.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = START.timestamp()
        self.log = []
        self.client = FakeClient(self.log)
        self.remote = Remote(self.log)
        self.gemini = FakeGemini()
        self.text_calls = 0
        self.text_refusals = 0
        self.text_budget_refused = False
        self.stop = asyncio.Event()
        self.config = settings(
            self.temp.name, mode=mode,
            contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN),
            image=ImageConfig(provider='gemini', model_body='image-test', model_cover='image-test',
                              review_model='review-test', review_bypass=True, key_source='env'),
            budget=BudgetConfig(daily_usd, {'image-test': (0.5, 3.0), 'review-test': (0.3, 2.5)}, {},
                                {'image-test': 60}),
            admin=AdminConfig(category_ids=dict(CATEGORY_IDS), publish_timeout_seconds=30),
            autoflow=AutoflowConfig(enabled=enabled, run_usd=run_usd, runs_dir=str(self.root / 'runs')))
        for name, target in (('nailblog.onepost.ImagePipeline', self.pipeline),
                             ('nailblog.admin.Admin', lambda config: FakeAdmin(self.remote, config)),
                             ('nailblog.trends.refresh', lambda *a, **k: ([], ''))):
            patcher = patch(name, side_effect=target)
            patcher.start()
            case.addCleanup(patcher.stop)
        self.service = self.new_service()
        self.service.save_cache([], [])
        self.service.state.set_paused(False)
        self.service.state.arm('e2e-binding')
        self.topic = self.service.state.add_topic(
            'daily:2026-10-25', 'Màu nail merlot', 'nail-salon', SLOT_TEXT, 0.5,
            {'sources': [], 'product_promo': False,
             'chosen': {'day': '2026-10-25', 'slot': '19:00', 'origin': 'pick', 'auto': False}}, state='approved')

    def pipeline(self, config, **kw):
        provider = GeminiImages(config, transport=self.gemini, key_reader=lambda *a, **k: 'fixture-key')
        return ImagePipeline(config, provider=provider, cover_renderer=fixture_cover, clock=lambda: self.now, **kw)

    def new_service(self):
        """A fresh process on the same database: new Service, State object, locks and bound senders."""
        service = Service(self.config, clock=lambda: self.now)
        service.notice_send = make_owner_notice_sender(self.client, (OWNER,))
        service.media_send = make_owner_media_sender(self.client, (OWNER,))
        service.notice_owner_id = OWNER
        service.text = SimpleNamespace()   # the daily steps never reach the model here

        async def draft(topic, model, *, posts, today):
            if self.text_budget_refused:       # Service.model_call refuses a paid text call at the cap, pre-dispatch
                self.text_refusals += 1
                raise BudgetExceeded('daily USD budget including reservations reached')
            self.text_calls += 1
            self.log.append('text')
            model.before_dispatch()         # one text dispatch, as TextModel does
            return SimpleNamespace(article=make_article(), repaired=())
        service.draft = draft
        return service

    def restart(self):
        self.service = self.new_service()

    @property
    def state(self):
        return self.service.state

    def row(self):
        rows = AutoflowRuns(self.state).rows()
        return rows[0] if rows else None

    def stage(self):
        row = self.row()
        return row['stage'] if row else None

    async def tick(self, seconds=60, *, restart=False):
        """One scheduler pass in service.scheduler's order: publish_pass, then the daily/autoflow job."""
        if restart:
            self.restart()
        self.now += seconds
        await self.service.publish_pass(stop=self.stop)
        await daily.run_due(self.service, self.stop)

    async def until(self, stage, *, limit=14, restart=False):
        for _ in range(limit):
            if self.stage() == stage:
                return
            await self.tick(restart=restart)
        self.case.fail(f'never reached {stage}; at {self.stage()} reason={(self.row() or {}).get("terminal_reason")}')

    async def owner(self, text, sender=OWNER):
        event = dm(raw_text=text, sender_id=sender, chat_id=sender)
        await route_command(event, self.service, (OWNER,))
        return [call.args[0] for call in event.reply.await_args_list]

    def card(self, kind):
        return [text for name, text in self.client.cards if name == kind][-1]

    async def answer(self, kind, verdict, reason='ảnh chưa đẹp', sender=OWNER):
        request = re.search(r'/nailblog ok (\d+)', self.card(kind)).group(1)
        text = f'/nailblog ok {request}' if verdict == 'ok' else f'/nailblog reject {request} {reason}'
        self.log.append(f'{verdict}:{kind}')
        return request, await self.owner(text, sender)

    def dms(self, needle):
        return [text for text in self.client.texts if needle in text]


class Base(unittest.IsolatedAsyncioTestCase):
    def harness(self, **kwargs):
        return Harness(self, **kwargs)


class HappyPathTests(Base):
    async def test_topic_to_published_with_exactly_one_upload_create_and_patch(self):
        h = self.harness()
        await h.until('images_wait')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (0, 0, 0))   # no Draft before images ok
        self.assertEqual(h.text_calls, 1)
        self.assertEqual(h.client.albums, [4])
        request, replies = await h.answer('images', 'ok')
        self.assertIn('[recorded]', replies[0])
        await h.until('draft_wait')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 0))   # no publish before Draft ok
        self.assertEqual(h.remote.uploaded_files, 4)
        await h.answer('draft', 'ok')
        await h.until('scheduled')
        self.assertEqual(h.remote.patches, 0)
        post = h.state.list_posts()[0]
        self.assertEqual(post['state'], 'scheduled')
        h.now = SLOT.timestamp() + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual(h.stage(), 'published')
        self.assertEqual(h.state.get_post(post['id'])['state'], 'published')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 1))
        for _ in range(3):   # nothing more happens once published
            await h.tick(300)
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 1))
        self.assertEqual([name for name, _ in h.client.cards], ['images', 'draft'])
        order = [e for e in h.log if e in ('card:images', 'ok:images', 'upload', 'create', 'card:draft', 'ok:draft', 'patch')]
        self.assertEqual(order, ['card:images', 'ok:images', 'upload', 'create', 'card:draft', 'ok:draft', 'patch'])
        self.assertEqual(h.text_calls, 1)
        self.assertEqual(sum(1 for call in h.gemini.calls if call == 'generateContent'), 4)
        self.assertEqual(h.dms('hết ngân sách'), [])

    async def test_restart_before_every_pass_repeats_no_post_patch_or_card(self):
        h = self.harness()
        await h.until('images_wait', restart=True)
        await h.answer('images', 'ok')
        await h.until('draft_wait', restart=True)
        await h.answer('draft', 'ok')
        await h.until('scheduled', restart=True)
        h.now = SLOT.timestamp() + 60
        for _ in range(3):
            await h.tick(0, restart=True)
        self.assertEqual(h.stage(), 'published')
        for _ in range(3):
            await h.tick(300, restart=True)
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 1))
        self.assertEqual([name for name, _ in h.client.cards], ['images', 'draft'])
        self.assertEqual(h.client.albums, [4])
        self.assertEqual(h.text_calls, 1)
        self.assertEqual(sum(1 for call in h.gemini.calls if call == 'generateContent'), 4)

    async def test_crash_after_create_then_restart_finds_the_draft_and_never_creates_again(self):
        h = self.harness()
        await h.until('images_wait')
        await h.answer('images', 'ok')
        h.remote.fail_after_create = True
        await h.until('saving')
        await h.tick()
        self.assertEqual(h.stage(), 'saving')
        self.assertEqual(h.remote.creates, 1)
        self.assertEqual(h.row()['failures'], 1)
        h.restart()
        await h.tick(400)       # past the 5 minute backoff
        await h.until('draft_wait')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 0))
        await h.answer('draft', 'ok')
        await h.until('scheduled')
        h.now = SLOT.timestamp() + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual(h.stage(), 'published')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 1))


class RejectTests(Base):
    async def test_image_reject_sends_exactly_one_more_preview_then_held_without_create(self):
        h = self.harness()
        await h.until('images_wait')
        first = h.card('images')
        await h.answer('images', 'reject', 'màu ảnh sai')
        for _ in range(6):
            if len(h.client.cards) == 2:
                break
            await h.tick()
        self.assertEqual(h.stage(), 'images_wait')
        self.assertEqual([name for name, _ in h.client.cards], ['images', 'images'])
        self.assertNotEqual(first, h.card('images'))
        self.assertEqual(h.client.albums, [4, 4])
        await h.answer('images', 'reject', 'vẫn sai')
        for _ in range(3):
            await h.tick()
        self.assertEqual(h.stage(), 'held')
        self.assertEqual(h.row()['terminal_reason'], 'owner_images_rejected')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (0, 0, 0))
        self.assertEqual([name for name, _ in h.client.cards], ['images', 'images'])
        self.assertEqual(len(h.dms('giữ lại')), 1)
        self.assertEqual(h.state.list_posts()[0]['state'], 'held')
        self.assertEqual(sum(1 for call in h.gemini.calls if call == 'generateContent'), 8)   # one retry, no more

    async def test_draft_reject_holds_the_post_and_never_patches(self):
        h = self.harness()
        await h.until('images_wait')
        await h.answer('images', 'ok')
        await h.until('draft_wait')
        await h.answer('draft', 'reject', 'tiêu đề chưa ổn')
        for _ in range(3):
            await h.tick()
        self.assertEqual(h.stage(), 'held')
        self.assertEqual(h.row()['terminal_reason'], 'owner_draft_rejected')
        self.assertEqual(h.state.list_posts()[0]['state'], 'held')
        h.now = SLOT.timestamp() + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 0))
        self.assertEqual(len(h.dms('giữ lại')), 1)

    async def test_wrong_sender_is_refused_and_nothing_advances(self):
        h = self.harness()
        await h.until('images_wait')
        request = re.search(r'/nailblog ok (\d+)', h.card('images')).group(1)
        self.assertEqual(await h.owner(f'/nailblog ok {request}', sender=OTHER), [])    # the router drops it silently
        store = ApprovalStore(h.state)
        refused = decide_approval(h.state, 'ok', int(request), '', sender_id=OTHER, owner_ids=(OWNER,), now=h.now)
        self.assertIn('[wrong_sender]', refused[0])
        self.assertEqual(store.get(int(request))['status'], 'pending')
        for _ in range(4):
            await h.tick()
        self.assertEqual(h.stage(), 'images_wait')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (0, 0, 0))
        self.assertEqual([name for name, _ in h.client.cards], ['images'])
        replies = await h.owner(f'/nailblog ok {request}')   # the real Owner still can
        self.assertIn('[recorded]', replies[0])

    async def test_ok_reject_replies_cover_the_store_codes_without_secrets(self):
        h = self.harness()
        await h.until('images_wait')
        request = re.search(r'/nailblog ok (\d+)', h.card('images')).group(1)
        self.assertIn('[reason_required]', (await h.owner(f'/nailblog reject {request}'))[0])
        self.assertIn('[unknown_request]', (await h.owner('/nailblog ok 99999'))[0])
        self.assertIn('[recorded]', (await h.owner(f'/nailblog ok {request}'))[0])
        self.assertIn('[already_decided]', (await h.owner(f'/nailblog ok {request}'))[0])
        self.assertIn('[already_decided_differently]', (await h.owner(f'/nailblog reject {request} đổi ý'))[0])
        self.assertEqual(parse('/nailblog reject 7 lý do dài'), ('reject', (7, 'lý do dài')))
        self.assertEqual(parse('/nailblog ok 7'), ('ok', (7,)))
        self.assertIsNone(parse('/nailblog ok'))
        self.assertIsNone(parse('/nailblog ok 7 8'))


class BudgetTests(Base):
    def capped(self):
        h = self.harness(daily_usd=0.5, run_usd=0.4)
        h.state.add_usage(h.now, 'gemini', 'image-test', 1, 0.45)   # 0.05 left, one image reserves about 0.125
        return h

    async def test_budget_cap_sends_one_dm_and_schedules_the_retry_for_the_next_vietnam_day(self):
        h = self.capped()
        for _ in range(5):
            await h.tick()
        self.assertEqual(h.stage(), 'new')
        self.assertEqual(len(h.dms('hết ngân sách')), 1)
        self.assertEqual((h.client.cards, h.gemini.generated), ([], 0))
        retry_at = h.row()['next_attempt_at']
        self.assertEqual(datetime.fromtimestamp(retry_at, VN_TZ), datetime(2026, 10, 25, 0, 0, tzinfo=VN_TZ))
        h.now = retry_at - 1                    # still the same Vietnam day: nothing new, no second DM
        await h.tick(0)
        self.assertEqual((len(h.dms('hết ngân sách')), h.gemini.generated, h.stage()), (1, 0, 'new'))

    async def test_budget_cap_retry_on_the_next_day_resumes_the_images(self):
        h = self.capped()
        for _ in range(3):
            await h.tick()
        h.now = h.row()['next_attempt_at'] + 60   # the next day: yesterday's spend no longer counts
        await h.until('images_wait')
        self.assertEqual(len(h.dms('hết ngân sách')), 1)
        self.assertEqual((h.text_calls, h.gemini.generated), (1, 4))
        await h.answer('images', 'ok')
        await h.until('draft_wait')
        await h.answer('draft', 'ok')
        await h.until('scheduled')
        h.now = SLOT.timestamp() + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual(h.stage(), 'published')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 1))

    async def test_text_stage_cap_before_the_first_call_resumes_next_day_and_publishes(self):
        h = self.harness()
        h.text_budget_refused = True
        for _ in range(4):
            await h.tick()
        self.assertEqual((h.stage(), h.text_calls, h.gemini.generated), ('new', 0, 0))
        self.assertGreaterEqual(h.text_refusals, 1)
        self.assertEqual(len(h.dms('hết ngân sách')), 1)
        with h.state.connection() as db:
            self.assertEqual(db.execute("SELECT phase FROM onepost_runs").fetchone()[0], "text_paused")
        h.text_budget_refused = False
        h.now = h.row()['next_attempt_at'] + 60
        await h.until('images_wait')
        self.assertEqual(h.text_calls, 1)
        await h.answer('images', 'ok')
        await h.until('draft_wait')
        await h.answer('draft', 'ok')
        await h.until('scheduled')
        h.now = SLOT.timestamp() + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual(h.stage(), 'published')
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (1, 1, 1))
        self.assertEqual(len(h.dms('hết ngân sách')), 1)


class VisibilityTests(Base):
    async def test_flow_command_and_the_20_00_dm_show_stages(self):
        h = self.harness()
        self.assertEqual((await h.owner('/nailblog flow'))[0].split(':')[0], 'Autoflow')
        await h.until('images_wait')
        request = re.search(r'/nailblog ok (\d+)', h.card('images')).group(1)
        flow = (await h.owner('/nailblog flow'))[0]
        self.assertIn('[images_wait]', flow)
        self.assertIn(f'/nailblog ok {request}', flow)
        self.assertIn(f'/nailblog reject {request} <lý do>', flow)
        text, complete = await daily.preview_dm(h.service, None, date(2026, 10, 24))
        self.assertTrue(complete)
        self.assertIn('[images_wait]', text)
        self.assertIn('/nailblog flow', text)
        self.assertNotIn('Chế độ preview', text)
        self.assertIn('chưa có chủ đề', text)                 # the 06:00 slot has none

    async def test_write_step_creates_no_placeholder_post_when_autoflow_is_on(self):
        h = self.harness()
        with patch('nailblog.daily.write_slot', new=AsyncMock()) as write:
            self.assertEqual(await daily.write_articles(h.service, None, date(2026, 10, 24)), ('', True))
        write.assert_not_awaited()
        self.assertEqual(h.state.list_posts(), [])
        off = self.harness(enabled=False)
        with patch('nailblog.daily.write_slot', new=AsyncMock()) as write:
            await daily.write_articles(off.service, None, date(2026, 10, 24))
        write.assert_awaited_once()                             # today's behaviour is untouched

    async def test_autoflow_disabled_never_builds_onepost_or_calls_the_engine(self):
        h = self.harness(enabled=False)
        with patch('nailblog.autoflow.run_due', new=AsyncMock(side_effect=AssertionError('engine called'))), \
                patch('nailblog.onepost.OnePost', side_effect=AssertionError('OnePost built')):
            for _ in range(3):
                await h.tick()
        self.assertEqual((h.client.cards, h.gemini.calls), ([], []))
        with h.state.connection() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='autoflow_runs'").fetchone())

    async def test_an_engine_failure_never_stops_the_scheduler_job(self):
        h = self.harness()
        with patch('nailblog.autoflow.run_due', new=AsyncMock(side_effect=RuntimeError('boom'))):
            await h.tick()
        self.assertTrue(h.state.daily_get('2026-10-24', 'decide'))   # the daily steps still ran


class ResumeAndGateTests(Base):
    def live(self, **kwargs):
        h = self.harness(**kwargs)
        self.config_file = h.root / 'nailblog.toml'
        self.config_file.write_text('[service]\nmode = "live"\n', encoding='utf-8')
        h.state.set_paused(True)
        h.state.disarm()
        patcher = patch.dict(os.environ, {'NAILBLOG_CONFIG': str(self.config_file)})
        patcher.start()
        self.addCleanup(patcher.stop)
        return h

    def gemini_ok(self, h):
        patcher = patch('nailblog.images.provider.GeminiImages', side_effect=lambda config: GeminiImages(
            config, transport=h.gemini, key_reader=lambda *a, **k: 'fixture-key'))
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_failed_preflight_keeps_it_paused_and_disarmed_and_lists_the_codes(self):
        h = self.live()
        replies = await h.owner('/nailblog resume')       # no captured Gemini key in this process
        self.assertIn('gemini_key_missing', replies[0])
        self.assertEqual((h.state.control()['paused'], h.state.armed_binding()), (1, ''))
        self.gemini_ok(h)
        h.remote.ready_error = h.remote.contract_error = True
        replies = (await h.owner('/nailblog resume'))[0]
        self.assertIn('contract_unverified', replies)
        self.assertIn('admin_not_ready', replies)
        self.assertNotIn('gemini_key_missing', replies)
        h.remote.ready_error = h.remote.contract_error = False
        h.state.add_usage(h.now, 'gemini', 'image-test', 1, 2.0)
        self.assertIn('budget_exhausted', (await h.owner('/nailblog resume'))[0])
        self.assertEqual((h.state.control()['paused'], h.state.armed_binding()), (1, ''))

    async def test_incomplete_categories_and_unavailable_models_fail(self):
        h = self.live()
        self.gemini_ok(h)
        h.service.config = replace(h.config, admin=replace(h.config.admin, category_ids={'nail-job': GUID}))
        self.assertIn('category_ids_incomplete', (await h.owner('/nailblog resume'))[0])
        h.service.config = h.config
        with patch('nailblog.images.provider.GeminiImages', side_effect=lambda config: GeminiImages(
                config, transport=lambda *a, **k: {'models': []}, key_reader=lambda *a, **k: 'k')):
            reply = (await h.owner('/nailblog resume'))[0]
        self.assertIn('gemini_models_unavailable:model_not_available', reply)
        self.assertEqual(h.state.armed_binding(), '')

    async def test_missing_config_digest_refuses_to_arm(self):
        h = self.live()
        self.gemini_ok(h)
        with patch.dict(os.environ):
            os.environ.pop('NAILBLOG_CONFIG')
            reply = (await h.owner('/nailblog resume'))[0]
        self.assertIn('config_digest_missing', reply)
        self.config_file.unlink()
        self.assertIn('config_digest_missing', (await h.owner('/nailblog resume'))[0])
        self.assertEqual((h.state.control()['paused'], h.state.armed_binding()), (1, ''))

    async def test_successful_preflight_arms_the_current_binding_and_unpauses(self):
        h = self.live()
        self.gemini_ok(h)
        reply = (await h.owner('/nailblog resume'))[0]
        self.assertIn('đã arm', reply)
        self.assertEqual(h.state.control()['paused'], 0)
        self.assertEqual(h.state.armed_binding(), startup_binding(h.config, config_path=str(self.config_file))[0])
        self.assertEqual(h.gemini.calls, ['list'])        # ListModels only: no generation, no spend
        self.assertEqual(h.remote.creates + h.remote.patches, 0)

    async def test_startup_gate_cli_pauses_and_disarms_a_new_config_then_resume_arms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_config(directory)
            def gate_sync():
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    code = cli.main(['startup-gate', '--config', str(path)])
                return code, json.loads(out.getvalue())

            async def gate():    # cli.main owns an event loop, so it runs outside this test's loop
                return await asyncio.to_thread(gate_sync)
            code, result = await gate()
            self.assertEqual((code, result['decision'], result['armed'], result['paused']), (0, 'paused_disarmed', False, True))
            config = load(directory)
            state = State(config.db_path)
            self.assertEqual((state.control()['paused'], state.armed_binding()), (1, ''))
            service = Service(config, state=state)
            self.assertIn('đã arm', (await resume(service, config_path=str(path)))[0])   # preview mode: digest only
            self.assertEqual((await gate())[1]['decision'], 'unchanged')
            self.assertEqual(State(config.db_path).control()['paused'], 0)
            path.write_text(path.read_text(encoding='utf-8') + '# edited\n', encoding='utf-8')
            self.assertEqual((await gate())[1]['decision'], 'paused_disarmed')
            self.assertEqual((State(config.db_path).control()['paused'], State(config.db_path).armed_binding()), (1, ''))

    async def test_pause_install_also_disarms(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load(directory)
            state = State(config.db_path)
            state.arm('armed')
            state.set_paused(False)
            with contextlib.redirect_stdout(io.StringIO()):
                await cli.execute('pause-install', config)
            self.assertEqual((state.control()['paused'], state.armed_binding()), (1, ''))


class SmallWiringTests(Base):
    def test_build_json_is_only_searched_in_the_package_directory(self):
        from nailblog.state import startup_binding as binding
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / 'pkg'
            package.mkdir()
            (package / 'a.py').write_text('X = 1\n', encoding='utf-8')
            (root / 'build.json').write_text('{}', encoding='utf-8')      # the parent (site-packages in production)
            self.assertEqual(binding(SimpleNamespace(mode='live'), config_path='', package_dir=package)[1], 'package')
            (package / 'build.json').write_text('{}', encoding='utf-8')
            self.assertEqual(binding(SimpleNamespace(mode='live'), config_path='', package_dir=package)[1], 'build.json')

    async def test_plan_week_passes_the_config_and_counts_the_configured_slots(self):
        for slots, needed in ((('06:00', '19:00'), 14), (('06:00', '13:00', '19:00'), 21)):
            with self.subTest(slots=slots), tempfile.TemporaryDirectory() as directory:
                config = replace(settings(directory), slots=slots)
                service = Service(config, clock=lambda: datetime(2026, 10, 23, 10, tzinfo=VN_TZ).timestamp())
                service.read_material = lambda *a, **k: ([], [], [])
                with patch('nailblog.service.propose', new=AsyncMock(return_value=([], []))) as propose:
                    outcome = await service.plan_week(None, dry_run=True)
                self.assertIs(propose.await_args.args[-1], config)
                self.assertIn(f'chưa đủ {needed} slot', outcome.text)

    async def test_telegram_binds_the_media_sender_next_to_the_notice_sender(self):
        from nailblog import telegram
        with tempfile.TemporaryDirectory() as directory:
            config = replace(settings(directory), telegram_session=str(Path(directory) / 's' / 'x.session'))
            seen = {}

            class Client:
                def __init__(self):
                    self.sent = []

                async def connect(self):
                    pass

                async def get_me(self):
                    return SimpleNamespace(id=42, bot=False)

                async def get_entity(self, who):
                    return SimpleNamespace(id=OWNER, bot=False, username='SotaThao')

                def add_event_handler(self, *a):
                    pass

                def remove_event_handler(self, *a):
                    pass

                async def run_until_disconnected(self):
                    return None

                async def disconnect(self):
                    pass

                async def send_message(self, owner, text, **kw):
                    self.sent.append(text)
                    return SimpleNamespace(id=1)

                async def send_file(self, owner, files):
                    self.sent.append(len(files))
                    return [SimpleNamespace(id=2)]

            class Recorded(Service):
                def __init__(self, *a, **k):
                    super().__init__(*a, **k)
                    seen['service'] = self

            client = Client()
            with patch('nailblog.telegram.make_client', return_value=client), patch('nailblog.telegram.Service', Recorded):
                with self.assertRaises(RuntimeError):
                    await telegram._run_locked(config)
            service = seen['service']
            self.assertIsNotNone(service.notice_send)
            self.assertIsNotNone(service.media_send)
            await service.media_send(OWNER, [b'jpeg'], 'card')
            self.assertEqual(client.sent, [1, 'card'])
            with self.assertRaises(ValueError):
                await service.media_send(OWNER + 1, [b'jpeg'], 'card')


if __name__ == '__main__':
    unittest.main()
