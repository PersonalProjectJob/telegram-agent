"""Autoflow end to end, Agy only (S4).

Real: State, ApprovalStore, OnePost, writer.write_agy + quality gate, AdminStore, Service (publish_pass, Draft save with
the duplicate scan), the autoflow engine and the /nailblog command router, all on a temporary SQLite database.
Fakes: the Agy `ask` (a scripted callable that records every call), the image pipeline, the Admin browser (counts every
Create/PATCH), the Telegram client and a frozen clock. No network, no agy.
"""
import asyncio
import copy
import hashlib
import inspect
import json
import random
import re
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

from nailblog import autoflow
from nailblog.agy_text import AgyTextError
from nailblog.approvals import ApprovalStore
from nailblog.autoflow import AutoflowRuns
from nailblog.commands import make_owner_notice_sender, route_command
from nailblog.config import PRODUCTION_MEDIA_ORIGIN, AdminConfig, AutoflowConfig, ContractConfig, ImageConfig
from nailblog.onepost import OnePost
from nailblog.publication import AdminError
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.telegram import make_owner_media_sender
from tests import fakes
from tests.helpers import OWNER, dm, settings
from tests.test_writer_agy import reply

SLOTS = ('06:00', '10:00', '15:00', '19:00')
DAY = '2026-10-28'
START = datetime(2026, 10, 27, 21, 0, tzinfo=VN_TZ)      # inside every slot's 24 h start window
GUID = '87654321-1234-1234-1234-123456789abc'
CATEGORY_IDS = {'hot-trend': GUID, 'nail-job': GUID.replace('87', '88'), 'nail-salon': GUID.replace('87', '89'),
                'did-you-know': GUID.replace('87', '8a')}
JUNK_REPAIR = 'chưa sửa gì'


def slot_ts(hhmm, day=DAY):
    return datetime.fromisoformat(f'{day}T{hhmm}:00+07:00').timestamp()


class FakeAsk:
    """The Agy `ask(prompt, schema, *, purpose, deadline=None)`: real contract, every call counted.

    Article N (from the topic title "số N") gets its own title and slug so the Admin duplicate scan sees four posts.
    `plan[n]` picks a behaviour: 'cliche' (fixable hold, the repair is useless), 'facts' (hold that can never be overridden),
    'unknown' (AgyTextError unknown on every ask), 'failed' (AgyTextError failed)."""

    def __init__(self):
        self.calls, self.plan, self.last = [], {}, 1

    def __call__(self, prompt, schema, *, purpose, deadline=None):
        self.calls.append(purpose)
        found = re.search(r'Chủ đề: [^\n]*số (\d)', prompt)
        if purpose.startswith('nailblog-write'):
            self.last = int(found.group(1))
        n, mode = self.last, self.plan.get(self.last)
        if mode in ('unknown', 'failed'):
            raise AgyTextError(mode, 'fixture')
        if purpose.startswith('nailblog-repair'):
            return {name: JUNK_REPAIR for name in schema['properties']}
        data = reply()
        data['title'] += f' số {n}'
        data['slug'] = f'Màu nail merlot mùa đông số {n}'
        if mode == 'cliche':
            data['sections'][0]['paragraphs'][1] = 'Màu này vô cùng hợp mùa đông. ' + data['sections'][0]['paragraphs'][1]
        elif mode == 'facts':
            data['sections'][0]['paragraphs'][1] = 'Một bộ gel thường có giá $75. ' + data['sections'][0]['paragraphs'][1]
        return data

    def writes(self):
        return [call for call in self.calls if call.startswith('nailblog-write')]


class FakePipeline:
    """Stands in for ImagePipeline: one distinct WEBP set per run (cover 1600x900, three 1200x675) + a bypass document."""

    def __init__(self):
        self.runs = []

    def __call__(self, config, **_kw):
        return self

    async def run(self, article, out, run_id, ceiling=None, *, subscription_enabled=False, **_kw):
        self.runs.append((run_id, subscription_enabled))
        out.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(4):
            rng = random.Random(f'{run_id}-{i}')
            tiny = Image.new('L', (9, 8))
            tiny.putdata([rng.randrange(256) for _ in range(72)])
            size = (1600, 900) if i == 0 else (1200, 675)
            path = out / ('cover.webp' if i == 0 else f'body-{i}.webp')
            tiny.resize(size, Image.NEAREST).convert('RGB').save(path, 'WEBP', quality=70)
            paths.append(str(path))
        hashes = [hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths]
        slots = [s for s in article['sections'] if s.get('image_slot')]
        images = [{'src': Path(p).name, 'alt': s['image_slot']['alt'], 'caption': s['image_slot'].get('caption', ''),
                   'width': 1200, 'height': 675} for p, s in zip(paths[1:], slots)]
        document = {'article': article, 'cover_path': paths[0], 'image_paths': paths[1:], 'image_hashes': hashes,
                    'image_reviews': {h: None for h in hashes}, 'images': images,
                    'review_bypass': {'enabled': True, 'hashes': hashes}}
        (out / 'document.json').write_text(json.dumps(document), encoding='utf-8')
        return document


class Remote:
    """What the fake admin site holds; shared by every FakeAdmin the code constructs, so it survives restarts."""

    def __init__(self):
        self.records, self.creates, self.patches, self.scans = {}, 0, 0, 0
        self.create_mode = 'ok'          # ok | timeout_recorded | timeout_lost (one-shot)


class FakeAdmin:
    """The Admin surface the service uses, with the S5 contract for find_duplicates and create_timeout."""

    def __init__(self, remote, config):
        self.remote, self.config = remote, config
        self.guard = SimpleNamespace()
        self.file_hashes, self.approved_image_hashes = {}, None

    def write_ready(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def ready(self):
        return {}

    async def find_duplicates(self, title, slug, *, exclude_id=None):
        self.remote.scans += 1
        return [{'id': r['id'], 'title': r['title'], 'slug': r['slug'], 'status': r['status']}
                for r in self.remote.records.values()
                if r['id'] != exclude_id and (r['title'] == title or (slug and r['slug'] == slug))]

    async def save_draft(self, payload, before_create, *, cover_path, image_paths):
        remote = self.remote
        uploaded = copy.deepcopy(payload)
        uploaded['translations'][0]['content'] = uploaded['translations'][0]['content'].replace('/nailblog-local/', '/uploaded/')
        for image in [uploaded['coverImage'], *uploaded['images']]:
            image['imageUrl'] = image['imageUrl'].replace('/nailblog-local/', '/uploaded/')
        before_create(uploaded)
        remote.creates += 1                                        # the one POST
        mode, remote.create_mode = remote.create_mode, 'ok'
        if mode != 'timeout_lost':
            record = {**uploaded, 'id': f'remote-{remote.creates}', 'status': 'Draft',
                      'title': uploaded['translations'][0]['title'], 'slug': self.slug_of(payload)}
            remote.records[record['id']] = record
        if mode != 'ok':
            raise AdminError('create_timeout')
        return copy.deepcopy(record)

    @staticmethod
    def slug_of(payload):
        return 'slug-of-' + re.sub(r'\W+', '-', payload['translations'][0]['title']).strip('-').lower()

    async def read(self, admin_id):
        return copy.deepcopy(self.remote.records[admin_id])

    async def publish(self, admin_id, expected, before_click):
        before_click()                                              # the durable claim, then the single PATCH
        self.remote.patches += 1
        self.remote.records[admin_id] = {**self.remote.records[admin_id], 'status': 'Published'}

    async def published_readback(self, admin_id, expected):
        return {'readback': {'admin_id': admin_id, 'status': 'Published'}}


class RecordingStore(ApprovalStore):
    """The real store; remembers the override arguments the engine passes (and forwards them when the store takes them)."""

    def __init__(self, state, log):
        super().__init__(state)
        self.log = log

    def request(self, kind, run_id, snapshot, deadline, now, *, requires_override=False, issues=None):
        self.log.append({'kind': kind, 'run_id': run_id, 'requires_override': requires_override, 'issues': issues})
        extra = {'requires_override': requires_override, 'issues': issues} \
            if 'requires_override' in inspect.signature(ApprovalStore.request).parameters else {}
        return super().request(kind, run_id, snapshot, deadline, now, **extra)


class FakeClient:
    """The Telethon client surface the Owner senders use."""

    def __init__(self):
        self.cards, self.texts, self.next_id = [], [], 100

    def _id(self):
        self.next_id += 1
        return self.next_id

    async def send_file(self, owner, files):
        return [SimpleNamespace(id=self._id()) for _ in files]

    async def send_message(self, owner, text, **_kw):
        found = re.search(r'/nailblog ok (\d+)', text)
        if found:
            self.cards.append(('draft' if 'duyệt Draft' in text else 'images', int(found.group(1)), text))
        else:
            self.texts.append(text)
        return SimpleNamespace(id=self._id())


class Harness:
    def __init__(self, case, *, slots=SLOTS):
        self.case = case
        temp = tempfile.TemporaryDirectory()
        case.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.now = START.timestamp()
        self.stop = asyncio.Event()
        self.client, self.remote, self.pipeline, self.ask = FakeClient(), Remote(), FakePipeline(), FakeAsk()
        self.requests = []
        self.config = settings(
            temp.name, mode='live', slots=SLOTS,
            contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN),
            image=ImageConfig(review_bypass=True),
            admin=AdminConfig(category_ids=dict(CATEGORY_IDS), publish_timeout_seconds=30, daily_publish_limit=4),
            autoflow=AutoflowConfig(enabled=True, runs_dir=str(self.root / 'runs')))
        for name, target in (('nailblog.onepost.ImagePipeline', self.pipeline),
                             ('nailblog.admin.Admin', lambda config: FakeAdmin(self.remote, config))):
            patcher = patch(name, side_effect=target)
            patcher.start()
            case.addCleanup(patcher.stop)
        patcher = patch.object(OnePost, 'provenance', staticmethod(lambda document, directory, *, config=None: 'provenance'))
        patcher.start()
        case.addCleanup(patcher.stop)
        self.service = self.new_service()
        self.service.save_cache([], [])
        self.service.state.set_paused(False)
        self.service.state.arm(self.service.startup_binding)
        self.state.daily_mark('2026-10-27', 'decide', 0.0)         # what the daily decide step leaves behind
        self.topics = []
        for index, hhmm in enumerate(slots):
            self.topics.append(self.state.add_topic(
                'daily:' + DAY, f'Chủ đề merlot số {SLOTS.index(hhmm) + 1}', 'nail-salon', f'{DAY}T{hhmm}+07:00', 0.5,
                {'sources': [fakes.SOURCE], 'product_promo': False,
                 'chosen': {'day': DAY, 'slot': hhmm, 'origin': 'pick', 'auto': False}}, state='approved'))

    def new_service(self):
        """A fresh process on the same database: new Service/State objects and bound senders."""
        service = Service(self.config, clock=lambda: self.now)
        service.notice_send = make_owner_notice_sender(self.client, (OWNER,))
        service.media_send = make_owner_media_sender(self.client, (OWNER,))
        service.notice_owner_id = OWNER
        service.text = self.ask
        return service

    def restart(self):
        self.service = self.new_service()

    @property
    def state(self):
        return self.service.state

    def rows(self):
        return AutoflowRuns(self.state).rows()

    def stages(self):
        return [row['stage'] for row in self.rows()]

    async def tick(self, seconds=60, *, restart=False):
        """One scheduler pass in service.scheduler's order: publish_pass, then one engine pass."""
        if restart:
            self.restart()
        self.now += seconds
        await self.service.publish_pass(stop=self.stop)
        await autoflow.run_due(self.service, self.stop, onepost=OnePost(self.service),
                               approvals=RecordingStore(self.state, self.requests), now=self.now)

    async def until(self, wanted, *, limit=25, restart=False):
        for _ in range(limit):
            if wanted():
                return
            await self.tick(restart=restart)
        self.case.fail(f'condition never reached; stages={self.stages()} '
                       f'reasons={[r["terminal_reason"] for r in self.rows()]}')

    async def until_stage(self, stage, **kw):
        await self.until(lambda: self.rows() and all(s == stage for s in self.stages()), **kw)

    def cards(self, kind):
        return [(request, text) for name, request, text in self.client.cards if name == kind]

    async def owner(self, text):
        event = dm(raw_text=text)
        await route_command(event, self.service, (OWNER,))
        return [call.args[0] for call in event.reply.await_args_list]

    async def ok_all(self, kind):
        for request, _text in self.cards(kind):
            await self.owner(f'/nailblog ok {request}')
            self.case.assertEqual(self.approval(request)['status'], 'approved')

    def approval(self, request):
        return ApprovalStore(self.state).get(request)

    def counts(self):
        return self.remote.creates, self.remote.patches

    def posts(self):
        return self.state.list_posts()


class Base(unittest.IsolatedAsyncioTestCase):
    def harness(self, **kwargs):
        return Harness(self, **kwargs)


class FourSlotsTests(Base):
    async def test_four_slots_publish_four_posts_each_with_exactly_one_create_and_one_patch(self):
        h = self.harness()
        await h.until_stage('images_wait')
        self.assertEqual(h.counts(), (0, 0))                                   # no Draft before the images ok
        self.assertEqual(len(h.ask.writes()), 4)
        self.assertEqual(len(h.ask.calls), 4)                                  # one `ask` per article, nothing else
        self.assertEqual(sorted(run for run, _ in h.pipeline.runs), sorted(r['run_id'] + '-images' for r in h.rows()))
        self.assertEqual(len(h.cards('images')), 4)
        for _ in range(5):                                                     # waiting for the Owner changes nothing
            await h.tick(300)
        self.assertEqual((h.counts(), len(h.cards('images')), len(h.ask.calls)), ((0, 0), 4, 4))
        await h.ok_all('images')
        await h.until_stage('draft_wait')
        self.assertEqual(h.counts(), (4, 0))                                   # four Creates, no publish before the Draft ok
        self.assertEqual(len(h.cards('draft')), 4)
        for _ in range(5):
            await h.tick(60)
        self.assertEqual(h.counts(), (4, 0))
        await h.ok_all('draft')
        await h.until_stage('scheduled')
        self.assertEqual((h.counts(), sorted(p['state'] for p in h.posts())), ((4, 0), ['scheduled'] * 4))
        for done, hhmm in enumerate(SLOTS, start=1):
            h.now = slot_ts(hhmm) + 60
            await h.tick(0)
            await h.tick(60)
            self.assertEqual(h.counts(), (4, done), hhmm)
        self.assertEqual((h.stages(), [p['state'] for p in h.posts()]), (['published'] * 4, ['published'] * 4))
        for _ in range(3):
            await h.tick(300)
        self.assertEqual((h.counts(), len(h.ask.calls), len(h.cards('images')), len(h.cards('draft'))), ((4, 4), 4, 4, 4))
        self.assertEqual(sorted(r['status'] for r in h.remote.records.values()), ['Published'] * 4)
        self.assertEqual(len({r['title'] for r in h.remote.records.values()}), 4)

    async def test_restart_before_every_pass_repeats_no_ask_post_patch_or_card(self):
        h = self.harness(slots=('06:00',))
        await h.until_stage('images_wait', restart=True)
        await h.ok_all('images')
        await h.until_stage('draft_wait', restart=True)
        await h.ok_all('draft')
        await h.until_stage('scheduled', restart=True)
        h.now = slot_ts('06:00') + 60
        for _ in range(3):
            await h.tick(0, restart=True)
        self.assertEqual(h.stages(), ['published'])
        for _ in range(3):
            await h.tick(300, restart=True)
        self.assertEqual(h.counts(), (1, 1))
        self.assertEqual((len(h.ask.calls), len(h.cards('images')), len(h.cards('draft')), len(h.pipeline.runs)), (1, 1, 1, 1))

    async def test_restart_between_the_ok_and_each_step_repeats_nothing(self):
        h = self.harness(slots=('10:00',))
        await h.until_stage('images_wait')
        await h.ok_all('images')
        h.restart()                                                            # crash right after the Owner ok
        await h.until_stage('draft_wait', restart=True)
        h.restart()
        await h.ok_all('draft')
        h.restart()
        await h.until_stage('scheduled', restart=True)
        self.assertEqual((h.counts(), len(h.ask.calls), len(h.pipeline.runs)), ((1, 0), 1, 1))


class PrepareFailureTests(Base):
    async def test_prepare_failure_never_publishes_and_the_one_post_run_stays_unpublishable(self):
        h = self.harness(slots=('06:00',))
        h.ask.plan[1] = 'failed'
        await h.until(lambda: h.stages() == ['held'])
        self.assertEqual(h.rows()[0]['terminal_reason'], 'too_many_failures:text_failed')
        h.now = slot_ts('06:00') + 60
        for _ in range(3):
            await h.tick(60)
        self.assertEqual((h.counts(), h.posts(), h.pipeline.runs), ((0, 0), [], []))
        self.assertEqual(h.remote.records, {})
        run_id = h.rows()[0]['run_id']
        with self.assertRaisesRegex(AdminError, 'approved_onepost_required'):
            await OnePost(h.service).publish(run_id)                           # a run that was never scheduled
        with self.assertRaisesRegex(AdminError, 'unknown_onepost_run'):
            OnePost(h.service).artifact('auto-never-prepared')
        self.assertEqual(h.counts(), (0, 0))

    async def test_unknown_text_gets_one_new_job_then_holds_with_one_dm_and_no_images(self):
        h = self.harness(slots=('06:00',))
        h.ask.plan[1] = 'unknown'
        await h.until(lambda: h.stages() == ['held'])
        self.assertEqual(h.ask.calls, ['nailblog-write', 'nailblog-write#retry1'])
        self.assertEqual(h.rows()[0]['terminal_reason'], 'text_unknown')
        for _ in range(4):
            await h.tick(600)
        self.assertEqual((len(h.ask.calls), h.pipeline.runs, h.counts(), len(h.client.cards)), (2, [], (0, 0), 0))
        self.assertEqual(len([t for t in h.client.texts if 'text_unknown' in t]), 1)

    async def test_a_hold_that_cannot_be_overridden_stops_before_images_with_one_dm(self):
        h = self.harness(slots=('06:00',))
        h.ask.plan[1] = 'facts'
        await h.until(lambda: h.stages() == ['held'])
        self.assertEqual(h.rows()[0]['terminal_reason'], 'text_quality_held')
        self.assertEqual((len(h.ask.calls), h.pipeline.runs, h.requests, h.client.cards), (1, [], [], []))
        self.assertEqual(len([t for t in h.client.texts if 'text_quality_held' in t]), 1)
        self.assertEqual(h.counts(), (0, 0))


class OverrideTests(Base):
    async def test_a_fixable_hold_still_gets_images_and_the_card_requires_an_override(self):
        h = self.harness(slots=('06:00',))
        h.ask.plan[1] = 'cliche'
        await h.until_stage('images_wait')
        self.assertEqual(h.ask.calls, ['nailblog-write', 'nailblog-repair'])   # at most two asks
        self.assertEqual(len(h.pipeline.runs), 1)
        first = h.requests[0]
        self.assertEqual((first['kind'], first['requires_override']), ('images', True))
        self.assertEqual([issue['code'] for issue in first['issues']], ['cliche'])
        self.assertTrue(all(issue['overridable'] and issue['kind'] == 'fixable' for issue in first['issues']))
        self.assertEqual(h.counts(), (0, 0))

    async def test_a_passing_article_asks_for_a_plain_ok(self):
        h = self.harness(slots=('06:00',))
        await h.until_stage('images_wait')
        self.assertEqual([r['requires_override'] for r in h.requests], [False])

    async def test_the_store_refusing_the_card_holds_the_run_with_one_dm(self):
        h = self.harness(slots=('06:00',))
        h.ask.plan[1] = 'cliche'

        def refuse(self, *args, **kwargs):
            from nailblog.approvals import ApprovalError
            raise ApprovalError('not_overridable')
        with patch.object(RecordingStore, 'request', refuse):
            await h.until(lambda: h.stages() == ['held'])
        self.assertEqual(h.rows()[0]['terminal_reason'], 'text_quality_held')
        self.assertEqual(len([t for t in h.client.texts if 'text_quality_held' in t]), 1)


class DraftCreateTests(Base):
    async def to_saving(self, h):
        await h.until_stage('images_wait')
        await h.ok_all('images')

    async def test_a_duplicate_on_the_site_holds_before_the_claim_with_zero_posts(self):
        h = self.harness(slots=('06:00',))
        h.remote.records['old'] = {'id': 'old', 'title': 'Màu nail merlot mùa đông: hướng dẫn chọn và giữ bền cho thợ nail số 1',
                                   'slug': 'x', 'status': 'Published'}
        await self.to_saving(h)
        await h.until(lambda: h.stages() == ['held'])
        self.assertEqual(h.rows()[0]['terminal_reason'], 'duplicate_on_site')
        self.assertEqual((h.counts(), h.remote.scans), ((0, 0), 1))
        with h.state.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM admin_operations').fetchone()[0], 0)
        await h.tick(3600)
        self.assertEqual((h.counts(), h.remote.scans), ((0, 0), 1))

    async def test_create_timeout_then_exactly_one_candidate_is_adopted_with_no_second_post(self):
        h = self.harness(slots=('06:00',))
        h.remote.create_mode = 'timeout_recorded'
        await self.to_saving(h)
        await h.until(lambda: h.stages() == ['draft_wait'])
        self.assertEqual(h.counts(), (1, 0))
        self.assertEqual(len(h.cards('draft')), 1)
        self.assertEqual(h.posts()[0]['admin_id'], 'remote-1')
        for _ in range(4):
            await h.tick(300)
        self.assertEqual(h.counts(), (1, 0))

    async def test_create_timeout_with_no_candidate_waits_then_needs_a_human_and_never_posts_again(self):
        h = self.harness(slots=('06:00',))
        h.remote.create_mode = 'timeout_lost'
        await self.to_saving(h)
        await h.until(lambda: h.remote.creates == 1 and h.stages() == ['saving'])
        row = h.rows()[0]
        self.assertEqual((row['failures'], h.counts()), (0, (1, 0)))           # reconciling is a wait, not a failure
        for _ in range(3):
            await h.tick(300)                                                  # 20 minutes in: still reconciling
        self.assertEqual((h.stages(), h.counts()), (['saving'], (1, 0)))
        self.assertEqual([t for t in h.client.texts if 'create_requires_manual' in t], [])
        await h.tick(2400)                                                     # past 30 minutes
        await h.tick(300)
        self.assertEqual((h.stages(), h.rows()[0]['terminal_reason']), (['held'], 'create_requires_manual_reconciliation'))
        self.assertEqual(len([t for t in h.client.texts if 'create_requires_manual_reconciliation' in t]), 1)
        await h.tick(3600)
        self.assertEqual(h.counts(), (1, 0))                                   # one POST in the whole story


if __name__ == '__main__':
    unittest.main()
