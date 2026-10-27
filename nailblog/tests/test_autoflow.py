"""Autoflow engine (S8): real State + real ApprovalStore on a temp SQLite, fakes for OnePost/Service/senders/clock."""
import ast
import asyncio
import inspect
import json
import re
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from nailblog import autoflow
from nailblog.approvals import ApprovalStore
from nailblog.config import AdminConfig, AutoflowConfig, ImageConfig, ScheduleConfig
from nailblog.onepost import OnePost
from nailblog.publication import AdminError
from nailblog.schedule import iso_slot
from nailblog.state import State
from nailblog.telegram import OwnerMediaSendError
from tests.helpers import OWNER, settings

VN = timezone(timedelta(hours=7))
GUIDS = {'hot-trend': 'guid-hot', 'nail-job': 'guid-job', 'nail-salon': 'guid-salon', 'did-you-know': 'guid-dyk'}


def at(day, hour, minute=0, second=0):
    return datetime(2026, 11, day, hour, minute, second, tzinfo=VN).timestamp()


def slot_text(day, hour):
    return iso_slot(datetime(2026, 11, day, hour, 0, tzinfo=VN))


class FakeOnePost:
    """Matches OnePost's call signatures (prepare/read/artifact/accept_images/save/approve + S7's retry/mark)."""

    def __init__(self, fx):
        self.fx, self.calls, self.runs = fx, [], {}
        self.prepare_error = None
        self.exhaust = False
        self.on_prepare = None
        self.on_artifact = None

    def n(self, name):
        return sum(1 for call in self.calls if call[0] == name)

    def args(self, name):
        return [call[1] for call in self.calls if call[0] == name]

    async def prepare(self, run_id, topic_id, out, *, before_dispatch=None):
        self.calls.append(('prepare', (run_id, topic_id, out), {
                                                               'before_dispatch': before_dispatch}))
        if self.on_prepare:
            self.on_prepare()
        if self.prepare_error is not None:
            raise self.prepare_error
        if run_id not in self.runs:
            topic = self.fx.state.get_topic(topic_id)
            folder = Path(out) / 'images'
            folder.mkdir(parents=True, exist_ok=True)
            paths = []
            for name in ('cover.webp', 'body-1.webp', 'body-2.webp'):
                Image.new('RGB', (64, 36), (10, 20, 30)).save(folder / name, 'WEBP')
                paths.append(str(folder / name))
            post = self.fx.state.add_post(topic['slot_at'], topic_id=topic_id, state='writing')
            self.runs[run_id] = {'phase': 'images_ready', 'rev': 0, 'post_id': post, 'paths': paths, 'retries': 0}
        return self.artifact(run_id)

    def read(self, run_id):
        run = self.runs[run_id]
        return {'document': json.dumps({'cover_path': run['paths'][0], 'image_paths': run['paths'][1:]})}

    def artifact(self, run_id):
        if self.on_artifact:
            self.on_artifact()
        run = self.runs.get(run_id)
        if run is None:
            raise AdminError('unknown_onepost_run')
        art = {'snapshot': f"{run_id}:{run['phase']}:{run['rev']}", 'run_id': run_id, 'phase': run['phase'],
               'post_id': run['post_id'], 'article': {'title': 'Tieu de', 'summary': 'Tom tat'}, 'words': 1200,
               'quality': {'verdict': 'PASS'}, 'facts': {'ungrounded': []}, 'images': [], 'image_quota_forecast': None}
        if run['phase'] in ('draft_verified', 'scheduled'):
            art.update(admin_id='admin-77', draft_fingerprint='f' * 64)
        return art

    def accept_images(self, run_id, snapshot):
        self.calls.append(('accept_images', (run_id, snapshot)))
        run = self.runs[run_id]
        if run['phase'] != 'images_ready' or self.artifact(run_id)['snapshot'] != snapshot:
            raise AdminError('image_approval_snapshot_changed')
        run['phase'] = 'images_approved'
        return self.artifact(run_id)

    async def save(self, run_id, category_id):
        self.calls.append(('save', (run_id, category_id)))
        run = self.runs[run_id]
        if run['phase'] != 'images_approved':
            raise AdminError('manual_image_acceptance_required')

        async def take():   # like the real save_remote_draft: takes the non-reentrant lock itself
            async with self.fx.lock:
                pass
        await asyncio.wait_for(take(), 1)
        self.fx.state.transition(run['post_id'], 'review', 'draft saved')
        run['phase'] = 'draft_verified'
        return self.artifact(run_id)

    def approve(self, run_id, snapshot):
        self.calls.append(('approve', (run_id, snapshot)))
        run = self.runs[run_id]
        if run['phase'] != 'draft_verified' or self.artifact(run_id)['snapshot'] != snapshot:
            raise AdminError('draft_approval_snapshot_changed')
        self.fx.state.transition(run['post_id'], 'scheduled', 'owner approved')
        run['phase'] = 'scheduled'
        return self.artifact(run_id)

    async def retry_images(self, run_id, reason):
        self.calls.append(('retry_images', (run_id, reason)))
        run = self.runs[run_id]
        if self.exhaust or run['retries'] >= 1:
            raise AdminError('image_retry_exhausted')
        run['retries'] += 1
        run['rev'] += 1
        return self.artifact(run_id)

    def mark_published(self, run_id):
        self.calls.append(('mark_published', (run_id,)))
        self.runs[run_id]['phase'] = 'published'
        return self.artifact(run_id)

    async def publish(self, run_id):
        raise AssertionError('autoflow must never publish')


class Fx:
    def __init__(self, root, *, slots=('06:00', '19:00'), topics=((3, 6, 'hot-trend'),), category_ids=None, schedule=None):
        self.root = Path(root)
        self.db = self.root / 'state.db'
        self.state = State(self.db)
        self.lock = asyncio.Lock()
        # The real config object (frozen dataclasses), so deadlines come from config.schedule like everywhere else.
        self.config = settings(
            self.root, mode='live', slots=tuple(slots),
            autoflow=AutoflowConfig(enabled=True, runs_dir=str(self.root / 'runs')),
            admin=AdminConfig(category_ids=dict(GUIDS if category_ids is None else category_ids)),
            image=ImageConfig(review_bypass=True), schedule=ScheduleConfig(**(schedule or {})))
        self.media, self.dms, self.dm_attempts = [], [], 0
        self.media_error = None
        self.dm_error = None
        self.state.arm('binding-1')
        self.approvals = ApprovalStore(self.state)
        self.onepost = FakeOnePost(self)
        self.now = at(2, 13)
        self.topic_ids = [self.state.add_topic('w', f'Chu de {n}', category, slot_at=slot_text(day, hour), state='approved')
                          for n, (day, hour, category) in enumerate(topics)]
        for topic_id in self.topic_ids:
            self.lock_topic(topic_id)      # S10a: only topics the daily decide step locked get a run
        self.topic = self.topic_ids[0] if self.topic_ids else None
        self.run_id = None
        self.publish_calls = 0

    def lock_topic(self, topic_id):
        """What the daily `decide` step leaves behind: the `chosen` label and the day-before decide marker."""
        slot = datetime.fromisoformat(self.state.get_topic(topic_id)['slot_at']).astimezone(VN)
        self.state.update_topic_meta(topic_id, chosen={'day': slot.date().isoformat(), 'slot': f'{slot:%H:%M}',
                                                       'origin': 'pick', 'auto': False})
        self.state.daily_mark((slot.date() - timedelta(days=1)).isoformat(), 'decide', 0.0)

    async def media_send(self, owner_id, files, caption):
        self.media.append((owner_id, list(files), caption))
        if self.media_error is not None:
            error, self.media_error = self.media_error, None
            raise error
        return [1000 + len(self.media) * 10 + i for i in range(len(files) + 1)]

    async def notice_send(self, text):
        self.dm_attempts += 1
        if self.dm_error is not None:
            raise self.dm_error
        self.dms.append(text)

    async def publish_pass(self, *args, **kwargs):
        self.publish_calls += 1
        raise AssertionError('autoflow must never call publish_pass')

    def service(self):
        return SimpleNamespace(state=self.state, config=self.config, clock=lambda: self.now, browser_lock=self.lock,
                               media_send=self.media_send, notice_send=self.notice_send, publish_pass=self.publish_pass)

    def configure(self, **values):
        self.config = replace(self.config, **values)

    def restart(self):
        """A new process on the same database: new State/ApprovalStore objects, same durable data."""
        self.state = State(self.db)
        self.approvals = ApprovalStore(self.state)

    async def tick(self, when=None, *, stop=None, restart=False):
        self.now = self.now + 60 if when is None else when
        if restart:
            self.restart()
        await autoflow.run_due(self.service(), stop, onepost=self.onepost, approvals=self.approvals, now=self.now)
        return self.row()

    async def ticks(self, count):
        for _ in range(count):
            await self.tick()
        return self.row()

    def row(self, topic=None):
        found = autoflow.AutoflowRuns(self.state).by_topic(topic or self.topic)
        if found:
            self.run_id = found['run_id']
        return found

    def pending(self, kind):
        return self.approvals.pending(kind)

    def decide(self, kind, verdict, reason=None, *, when=None, index=-1):
        request = [r for r in self.approvals.pending(kind)][index]
        result = self.approvals.decide(request['id'], verdict, reason, OWNER, OWNER, self.now if when is None else when)
        assert result['ok'], result
        return request

    def requests(self, kind):
        with self.state.connection() as db:
            return [dict(r) for r in db.execute('SELECT * FROM approval_requests WHERE kind=? ORDER BY id', (kind,))]

    def post(self):
        return self.state.get_post(self.onepost.runs[self.run_id]['post_id'])

    def publish_by_scheduler(self):
        post_id = self.onepost.runs[self.run_id]['post_id']
        self.state.transition(post_id, 'before_publish', 'claimed')
        self.state.transition(post_id, 'published', 'readback ok', evidence={'readback': 'live'})


# S7 adds these two to OnePost (not in this worktree yet); the agreed contract: retry_images async, mark_published sync.
S7_CONTRACT_ASYNC = {'retry_images': True, 'mark_published': False}


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Fakes must be sync/async exactly like the real OnePost, or the engine's await/no-await goes unseen."""
        for name in ('prepare', 'read', 'artifact', 'accept_images', 'save', 'approve', 'retry_images', 'mark_published'):
            real = getattr(OnePost, name, None)
            expected = inspect.iscoroutinefunction(real) if real is not None else S7_CONTRACT_ASYNC[name]
            self.assertIs(inspect.iscoroutinefunction(getattr(FakeOnePost, name)), expected, name)
        self.assertTrue(inspect.iscoroutinefunction(FakeOnePost.publish))

    def fixture(self, **kwargs):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Fx(directory.name, **kwargs)

    async def to_images_wait(self, fx):
        await fx.ticks(2)
        self.assertEqual(fx.row()['stage'], 'images_wait')

    async def to_draft_wait(self, fx):
        await self.to_images_wait(fx)
        fx.decide('images', 'ok')
        await fx.ticks(4)
        self.assertEqual(fx.row()['stage'], 'draft_wait')


class GateTests(Base):
    def assert_idle(self, fx):
        self.assertEqual(fx.onepost.calls, [])
        self.assertEqual((fx.media, fx.dms, fx.dm_attempts), ([], [], 0))
        with fx.state.connection() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='autoflow_runs'").fetchone())

    async def test_disabled_unarmed_paused_preview_do_nothing(self):
        for name in ('disabled', 'unarmed', 'paused', 'preview'):
            with self.subTest(name):
                fx = self.fixture()
                if name == 'disabled':
                    fx.configure(autoflow=replace(fx.config.autoflow, enabled=False))
                elif name == 'unarmed':
                    fx.state.disarm()
                elif name == 'paused':
                    fx.state.set_paused(True)
                else:
                    fx.configure(mode='preview')
                for _ in range(3):   # run_due directly: Fx.tick() reads the table, which would create it
                    fx.now += 60
                    await autoflow.run_due(fx.service(), None, onepost=fx.onepost, approvals=fx.approvals, now=fx.now)
                self.assert_idle(fx)

    async def test_enabled_live_armed_unpaused_runs(self):
        fx = self.fixture()
        await fx.tick()
        self.assertEqual(fx.onepost.n('prepare'), 1)

    async def test_topics_outside_the_window_or_state_get_no_run(self):
        fx = self.fixture(topics=((5, 6, 'hot-trend'), (2, 15, 'nail-job')))   # >24h ahead; image deadline already passed
        fx.state.add_topic('w', 'proposed only', 'hot-trend', slot_at=slot_text(3, 19), state='proposed')
        posted = fx.state.add_topic('w', 'has post', 'hot-trend', slot_at=slot_text(4, 6), state='approved')
        fx.state.add_post(slot_text(4, 6), topic_id=posted, state='writing')
        await fx.ticks(2)
        self.assertEqual(fx.onepost.calls, [])
        self.assertEqual(autoflow.AutoflowRuns(fx.state).rows(), [])

    async def test_run_id_and_directory(self):
        fx = self.fixture()
        row = await fx.tick()
        self.assertEqual(row['run_id'], f'auto-20261103-0600-t{fx.topic}')
        self.assertEqual(fx.onepost.args('prepare')[0], (row['run_id'], fx.topic, str(Path(fx.config.autoflow.runs_dir) / row['run_id'])))
        self.assertEqual((row['failures'], row['next_attempt_at'], row['terminal_reason']), (0, 0, ''))

    async def test_stop_event_blocks_dispatch(self):
        fx = self.fixture()
        stop = asyncio.Event()
        await fx.tick(stop=stop)
        hook = fx.onepost.calls[0][2]['before_dispatch']
        self.assertIs(hook(), True)
        stop.set()
        self.assertIs(hook(), False)
        before = len(fx.onepost.calls)
        await fx.tick(stop=stop)
        self.assertEqual(len(fx.onepost.calls), before)


class HappyPathTests(Base):
    async def walk(self, restart):
        fx = self.fixture()
        op = fx.onepost
        row = await fx.tick(restart=restart)
        self.assertEqual((row['stage'], op.n('prepare')), ('images_send', 1))
        call = op.calls[0]
        self.assertEqual(sorted(call[2]), ['before_dispatch'])        # no paid/ceiling arguments any more
        self.assertTrue(callable(call[2]['before_dispatch']))
        row = await fx.tick(restart=restart)
        self.assertEqual(row['stage'], 'images_wait')
        self.assertEqual(len(fx.media), 1)
        owner, files, card = fx.media[0]
        request = fx.pending('images')[0]
        self.assertEqual((owner, len(files)), (OWNER, 3))
        self.assertTrue(all(f.startswith(b'\xff\xd8') for f in files))
        self.assertIn(f"/nailblog ok {request['id']}", card)
        self.assertIn(f"/nailblog reject {request['id']} <lý do>", card)
        self.assertIn('Slot: ' + slot_text(3, 6), card)
        self.assertIn('ảnh chưa qua model chấm (bypass)', card)
        self.assertEqual(json.loads(fx.approvals.get(request['id'])['message_ids']), [1010, 1011, 1012, 1013])
        stored = request['snapshot']
        for _ in range(3):   # nothing is decided: nothing may advance
            await fx.tick(restart=restart)
        self.assertEqual((op.n('accept_images'), op.n('save'), op.n('approve'), len(fx.media)), (0, 0, 0, 1))
        fx.decide('images', 'ok')
        row = await fx.tick(restart=restart)
        self.assertEqual((row['stage'], op.args('accept_images'), op.n('save')), ('saving', [(fx.run_id, stored)], 0))
        row = await fx.tick(restart=restart)
        self.assertEqual((row['stage'], op.args('save'), op.n('approve')), ('draft_send', [(fx.run_id, 'guid-hot')], 0))
        self.assertEqual(fx.lock.locked(), False)
        row = await fx.tick(restart=restart)
        self.assertEqual((row['stage'], len(fx.media)), ('draft_wait', 2))
        _, files, card = fx.media[1]
        draft = fx.pending('draft')[0]
        self.assertEqual(files, [])
        self.assertIn('admin_id: admin-77', card)
        self.assertIn(f"/nailblog ok {draft['id']}", card)
        for _ in range(2):
            await fx.tick(restart=restart)
        self.assertEqual(op.n('approve'), 0)
        fx.decide('draft', 'ok')
        row = await fx.tick(restart=restart)
        self.assertEqual((row['stage'], op.args('approve')), ('scheduled', [(fx.run_id, draft['snapshot'])]))
        await fx.ticks(2)
        self.assertEqual((fx.row()['stage'], op.n('mark_published')), ('scheduled', 0))   # the scheduler has not published
        fx.publish_by_scheduler()
        row = await fx.tick(restart=restart)
        self.assertEqual((row['stage'], op.args('mark_published')), ('published', [(fx.run_id,)]))
        await fx.ticks(3)
        counts = {name: op.n(name) for name in ('prepare', 'accept_images', 'save', 'approve', 'retry_images', 'mark_published')}
        self.assertEqual(counts, {'prepare': 1, 'accept_images': 1, 'save': 1, 'approve': 1, 'retry_images': 0,
                                  'mark_published': 1})
        self.assertEqual((len(fx.media), fx.dms, fx.publish_calls), (2, [], 0))

    async def test_full_flow(self):
        await self.walk(restart=False)

    async def test_full_flow_with_a_restart_before_every_pass(self):
        await self.walk(restart=True)

    async def test_missing_category_guid_holds_before_any_save(self):
        fx = self.fixture(category_ids={'nail-job': 'guid-job'})
        await self.to_images_wait(fx)
        fx.decide('images', 'ok')
        row = await fx.ticks(2)
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('save'), len(fx.dms)), ('held', 'category_unmapped', 0, 1))

    async def test_post_held_while_scheduled_stops_the_run(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok')
        await fx.tick()
        fx.state.transition(fx.post()['id'], 'held', 'manual hold')
        row = await fx.tick()
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('mark_published'), len(fx.dms)), ('held', 'post_held', 0, 1))

    async def test_never_publishes(self):
        tree = ast.parse(Path(autoflow.__file__).read_text(encoding='utf-8'))
        names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        self.assertFalse(names & {'publish_pass', 'publish', 'browser_lock'})
        fx = self.fixture()
        await self.walk_to_scheduled(fx)
        self.assertEqual(fx.publish_calls, 0)

    async def walk_to_scheduled(self, fx):
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok')
        await fx.ticks(3)
        self.assertEqual(fx.row()['stage'], 'scheduled')


class ImageRejectTests(Base):
    async def test_reject_retries_exactly_once_then_holds(self):
        fx = self.fixture()
        op = fx.onepost
        await self.to_images_wait(fx)
        first = fx.decide('images', 'reject', 'tay bi meo')
        row = await fx.tick()
        self.assertEqual((op.args('retry_images'), row['stage']), ([(fx.run_id, 'tay bi meo')], 'images_send'))
        self.assertEqual(json.loads(row['ctx'])['retried'], 1)
        row = await fx.tick()
        self.assertEqual((row['stage'], len(fx.media)), ('images_wait', 2))
        second_requests = fx.requests('images')
        self.assertEqual([r['status'] for r in second_requests], ['rejected', 'pending'])
        self.assertNotEqual(second_requests[1]['snapshot'], first['snapshot'])
        self.assertIn(f"/nailblog ok {second_requests[1]['id']}", fx.media[1][2])
        fx.decide('images', 'reject', 'van xau')
        row = await fx.tick()
        self.assertEqual((row['stage'], row['terminal_reason']), ('held', 'owner_images_rejected'))
        self.assertEqual((fx.post()['state'], fx.post()['reason']), ('held', 'owner_images_rejected'))
        await fx.ticks(3)
        self.assertEqual((op.n('retry_images'), op.n('accept_images'), op.n('save'), op.n('approve'), len(fx.dms)), (1, 0, 0, 0, 1))

    async def test_retry_exhausted_holds_with_one_dm(self):
        fx = self.fixture()
        fx.onepost.exhaust = True
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'khong dat')
        row = await fx.tick()
        await fx.ticks(2)
        self.assertEqual((row['stage'], row['terminal_reason'], fx.post()['state'], fx.onepost.n('retry_images'), len(fx.dms)),
                         ('held', 'image_retry_interrupted', 'held', 1, 1))

    async def test_any_retry_failure_holds_and_retry_is_never_called_again(self):
        for error in (AdminError('image_retry_not_allowed'), AdminError('image_pipeline_failed'), RuntimeError('boom')):
            with self.subTest(type(error).__name__ + str(error)):
                fx = self.fixture()

                async def failing(run_id, reason, fx=fx, error=error):
                    fx.onepost.calls.append(('retry_images', (run_id, reason)))
                    fx.onepost.runs[run_id]['phase'] = 'images_pending'   # S7 stores this before the rerun
                    raise error
                fx.onepost.retry_images = failing
                await self.to_images_wait(fx)
                fx.decide('images', 'reject', 'khong dat')
                await fx.tick()
                await fx.ticks(3)
                await fx.tick(fx.now + 7200, restart=True)
                row = fx.row()
                self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('retry_images'), len(fx.dms)),
                                 ('held', 'image_retry_interrupted', 1, 1))
                self.assertEqual((fx.post()['state'], fx.post()['reason'], fx.onepost.n('prepare')),
                                 ('held', 'image_retry_interrupted', 1))

    async def test_restart_with_a_retry_left_images_pending_holds_without_retrying(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'khong dat')
        await fx.tick()
        fx.onepost.runs[fx.run_id]['phase'] = 'images_pending'
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='images_retry')
        row = await fx.tick(restart=True)
        self.assertEqual((row['stage'], fx.onepost.n('retry_images'), fx.onepost.n('prepare'), len(fx.dms)),
                         ('held', 1, 1, 1))

    async def test_restart_after_retry_finished_does_not_retry_again(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'khong dat')
        await fx.tick()
        self.assertEqual(fx.onepost.n('retry_images'), 1)
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='images_retry')   # crashed before the move was stored
        row = await fx.tick(restart=True)
        self.assertEqual((row['stage'], fx.onepost.n('retry_images')), ('images_send', 1))

    async def test_approved_snapshot_is_the_stored_one_and_a_changed_artifact_holds(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'ok')
        fx.onepost.runs[fx.run_id]['rev'] += 1    # the artifact changed after the card went out
        row = await fx.tick()
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('save'), len(fx.dms)),
                         ('held', 'image_approval_snapshot_changed', 0, 1))


class DraftRejectTests(Base):
    async def test_draft_reject_holds_post_and_never_approves(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'reject', 'sai tieu de')
        row = await fx.tick()
        self.assertEqual((row['stage'], row['terminal_reason']), ('held', 'owner_draft_rejected'))
        self.assertEqual((fx.post()['state'], fx.post()['reason']), ('held', 'owner_draft_rejected'))
        await fx.ticks(3)
        self.assertEqual((fx.onepost.n('approve'), fx.onepost.n('save'), fx.onepost.n('mark_published'), len(fx.dms), fx.publish_calls),
                         (0, 1, 0, 1, 0))


class ReconcilingTests(Base):
    async def test_create_unknown_reconciling_waits_without_counting_a_failure(self):
        fx = self.fixture()
        fx.onepost.prepare_error = AdminError('create_unknown_reconciling')
        row = await fx.tick()
        self.assertEqual((row['stage'], row['failures'], row['next_attempt_at']), ('new', 0, fx.now + 300))
        for _ in range(3):                         # passes inside the 300 s wait: no second attempt, no hold, no DM
            await fx.tick()
        self.assertEqual((fx.onepost.n('prepare'), fx.row()['stage'], len(fx.dms)), (1, 'new', 0))
        fx.onepost.prepare_error = None
        fx.now += 400
        row = await fx.tick()
        self.assertEqual((fx.onepost.n('prepare'), row['stage']), (2, 'images_send'))

    async def test_new_hold_codes_hold_once_with_one_dm(self):
        for code in ('text_quality_held', 'text_unknown', 'duplicate_on_site', 'unknown_onepost_run'):
            with self.subTest(code):
                fx = self.fixture()
                fx.onepost.prepare_error = AdminError(code)
                await fx.tick()
                await fx.ticks(4)
                row = fx.row()
                self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('prepare'), len(fx.dms)), ('held', code, 1, 1))

    async def test_transient_text_codes_back_off_and_keep_trying(self):
        for code in ('text_daily_cap', 'text_not_ready', 'duplicate_scan_incomplete'):
            with self.subTest(code):
                fx = self.fixture()
                fx.onepost.prepare_error = AdminError(code)
                row = await fx.tick()
                self.assertEqual((row['stage'], row['failures']), ('new', 1))
                self.assertGreater(row['next_attempt_at'], fx.now)


class ErrorTests(Base):
    async def test_reconciliation_errors_hold_once_and_are_never_retried(self):
        for code in ('text_requires_manual_reconciliation', 'candidate_requires_reconciliation', 'quality_pass_required',
                     'onepost_held', 'create_requires_manual_reconciliation'):
            with self.subTest(code):
                fx = self.fixture()
                fx.onepost.prepare_error = AdminError(code)
                row = await fx.tick()
                await fx.ticks(5)
                await fx.tick(at(2, 13) + 4 * 3600)
                row = fx.row()
                self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('prepare'), len(fx.dms)), ('held', code, 1, 1))
                self.assertIn(code, fx.dms[0])

    async def test_reconciliation_error_during_save_holds(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'ok')
        await fx.tick()
        fx.onepost.runs[fx.run_id]['phase'] = 'images_approved'

        async def boom(run_id, category_id):
            fx.onepost.calls.append(('save', (run_id, category_id)))
            raise AdminError('create_requires_manual_reconciliation')
        fx.onepost.save = boom
        row = await fx.tick()
        await fx.ticks(3)
        self.assertEqual((row['stage'], fx.onepost.n('save'), len(fx.dms)), ('held', 1, 1))

    async def test_transient_errors_back_off_then_hold_after_three(self):
        fx = self.fixture()
        fx.onepost.prepare_error = RuntimeError('boom')
        row = await fx.tick()
        self.assertEqual((row['failures'], row['next_attempt_at']), (1, fx.now + 300))
        await fx.tick(fx.now + 60)
        self.assertEqual(fx.onepost.n('prepare'), 1)
        row = await fx.tick(row['next_attempt_at'] + 1)
        self.assertEqual((row['failures'], fx.onepost.n('prepare')), (2, 2))
        self.assertAlmostEqual(row['next_attempt_at'], fx.now + 600)
        row = await fx.tick(row['next_attempt_at'] + 1)
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('prepare'), len(fx.dms)), ('held', 'too_many_failures:boom', 3, 1))
        await fx.tick(fx.now + 7200)
        self.assertEqual((fx.onepost.n('prepare'), len(fx.dms)), (3, 1))

    async def test_error_text_never_reaches_rows_dms_or_logs(self):
        fx = self.fixture()
        fx.onepost.prepare_error = RuntimeError('Bearer sk-SECRET123')
        with self.assertLogs('nailblog.autoflow', 'WARNING') as logs:
            await fx.tick()
            await fx.tick(fx.now + 301)
            await fx.tick(fx.now + 601)
        row = fx.row()
        self.assertEqual(row['terminal_reason'], 'too_many_failures:RuntimeError')
        blob = ' '.join(fx.dms) + json.dumps(row) + ' '.join(logs.output)
        self.assertNotIn('SECRET', blob)
        self.assertNotIn('Bearer', blob)

    async def test_failed_dm_is_retried_once_delivered_by_a_later_pass(self):
        fx = self.fixture()
        fx.onepost.prepare_error = AdminError('onepost_held')
        fx.dm_error = RuntimeError('telegram down')
        await fx.tick()
        self.assertEqual((fx.dms, fx.dm_attempts), ([], 1))
        fx.dm_error = None
        await fx.tick(fx.now + 400)
        await fx.ticks(3)
        self.assertEqual((len(fx.dms), fx.dm_attempts, fx.onepost.n('prepare')), (1, 2, 1))


class ReconciliationCodeTests(Base):
    PATTERN = re.compile(r"""['"]([a-z][a-z_]*(?:requires_reconciliation|requires_manual_reconciliation|outcome_uncertain)[a-z_]*)['"]""")

    def package_codes(self):
        found = {}
        for path in sorted(Path(autoflow.__file__).parent.rglob('*.py')):
            if path.name == 'autoflow.py':       # the classifier must not vouch for itself
                continue
            for code in self.PATTERN.findall(path.read_text(encoding='utf-8')):
                found.setdefault(code, set()).add(path.name)
        return found

    def test_every_reconciliation_code_in_the_package_is_a_hold(self):
        found = self.package_codes()
        self.assertIn('create_outcome_uncertain', found)
        self.assertIn('create_requires_manual_reconciliation', found)
        missed = sorted(code for code in found if not autoflow.is_hold_code(code))
        self.assertEqual(missed, [], f'reconciliation codes that autoflow would retry: {missed}')

    def test_deadline_passed_and_unknown_suffix_codes_are_holds_but_plain_errors_are_not(self):
        for code in ('approval_deadline_passed', 'future_thing_requires_reconciliation', 'x_requires_manual_reconciliation'):
            self.assertTrue(autoflow.is_hold_code(code), code)
        for code in ('boom', 'rate_limited', 'RuntimeError', 'media_send_failed'):
            self.assertFalse(autoflow.is_hold_code(code), code)

    async def test_every_found_code_holds_without_a_retry_and_with_one_dm(self):
        for code in sorted(self.package_codes()):
            with self.subTest(code):
                fx = self.fixture()
                fx.onepost.prepare_error = AdminError(code)
                await fx.tick()
                await fx.ticks(4)
                await fx.tick(fx.now + 7200)
                row = fx.row()
                self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('prepare'), len(fx.dms)), ('held', code, 1, 1))


class ConfigDeadlineTests(Base):
    async def test_approve_deadline_hours_come_from_config_schedule(self):
        fx = self.fixture(schedule={'approve_deadline_hours': 1})      # default 2h would expire at 04:00
        await self.to_draft_wait(fx)
        self.assertEqual(fx.requests('draft')[0]['deadline'], at(3, 5))
        row = await fx.tick(at(3, 4, 30))
        self.assertEqual(row['stage'], 'draft_wait')
        row = await fx.tick(at(3, 5, 0, 1))
        self.assertEqual((row['stage'], row['terminal_reason']), ('skipped', 'draft_deadline_missed'))

    async def test_window_end_and_start_hours_come_from_config_schedule(self):
        fx = self.fixture(schedule={'window_end_hours': 2, 'window_start_hours': 12})   # image deadline 04:00, start 18:00
        await fx.tick(at(2, 13))
        self.assertEqual(fx.row(), None)                                  # before slot - 12h: no run yet
        await fx.tick(at(2, 18, 1))
        await self.to_images_wait(fx)
        self.assertEqual(fx.requests('images')[0]['deadline'], at(3, 4))
        row = await fx.tick(at(3, 3, 30))                                  # default 3h would have expired at 03:00
        self.assertEqual(row['stage'], 'images_wait')
        row = await fx.tick(at(3, 4, 0, 1))
        self.assertEqual((row['stage'], row['terminal_reason']), ('skipped', 'images_deadline_missed'))


class SafetyTests(Base):
    async def test_reject_after_the_image_deadline_never_retries(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'khong dat', when=at(3, 2, 59))
        row = await fx.tick(at(3, 3, 30))
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('retry_images'), len(fx.dms)),
                         ('skipped', 'images_deadline_missed', 0, 1))

    async def test_save_requires_the_owner_ok_on_the_stored_snapshot(self):
        fx = self.fixture()
        await fx.tick()
        fx.onepost.runs[fx.run_id]['phase'] = 'images_approved'          # an operator CLI accept on this very run
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='saving')
        row = await fx.tick()
        await fx.ticks(2)
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('save'), len(fx.dms)), ('held', 'images_not_approved', 0, 1))

    async def test_prepare_returning_an_already_accepted_run_does_not_save_either(self):
        fx = self.fixture()
        await fx.tick()
        run = fx.onepost.runs[fx.run_id]
        run['phase'] = 'images_approved'
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='new')
        await fx.ticks(3)
        self.assertEqual((fx.row()['stage'], fx.onepost.n('save')), ('held', 0))

    async def test_pause_refusal_from_retry_images_holds_nothing_and_retries_next_tick(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        real = fx.onepost.retry_images

        async def refused(run_id, reason):
            fx.onepost.calls.append(('retry_images', (run_id, reason)))
            raise AdminError('explicit_live_unpaused_required')       # active() runs before the retry is recorded
        fx.onepost.retry_images = refused
        fx.decide('images', 'reject', 'khong dat')
        row = await fx.tick()
        self.assertEqual((row['stage'], row['failures'], fx.post()['state'], len(fx.dms)), ('images_retry', 0, 'writing', 0))
        fx.onepost.retry_images = real
        row = await fx.tick()
        self.assertEqual((row['stage'], fx.onepost.n('retry_images')), ('images_send', 2))

    async def test_stop_before_retry_images_skips_the_tick(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'khong dat')
        stop = asyncio.Event()
        fx.onepost.on_artifact = stop.set                               # the stop arrives while the pass is running
        row = await fx.tick(stop=stop)
        fx.onepost.on_artifact = None
        self.assertEqual((row['stage'], fx.onepost.n('retry_images'), fx.post()['state'], len(fx.dms)), ('images_retry', 0, 'writing', 0))
        row = await fx.tick()
        self.assertEqual((row['stage'], fx.onepost.n('retry_images')), ('images_send', 1))

    async def test_pause_that_moved_the_run_past_images_ready_is_still_a_spent_retry(self):
        fx = self.fixture()
        await self.to_images_wait(fx)

        async def interrupted(run_id, reason):
            fx.onepost.calls.append(('retry_images', (run_id, reason)))
            fx.onepost.runs[run_id]['phase'] = 'images_pending'
            raise AdminError('explicit_live_unpaused_required')
        fx.onepost.retry_images = interrupted
        fx.decide('images', 'reject', 'khong dat')
        row = await fx.tick()
        self.assertEqual((row['stage'], row['terminal_reason'], fx.post()['state']), ('held', 'image_retry_interrupted', 'held'))

    async def test_uncertain_post_sends_one_dm_keeps_watching_then_publishes(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok')
        await fx.ticks(2)
        post_id = fx.post()['id']
        fx.state.transition(post_id, 'before_publish', 'claimed')
        fx.state.transition(post_id, 'uncertain', 'publish_readback_timeout')
        await fx.ticks(3)
        self.assertEqual((fx.row()['stage'], len(fx.dms), fx.onepost.n('mark_published')), ('scheduled', 1, 0))
        self.assertIn('chưa xác định', fx.dms[0])
        fx.state.transition(post_id, 'published', 'readback ok', evidence={'readback': 'live'})
        row = await fx.tick()
        self.assertEqual((row['stage'], fx.onepost.n('mark_published'), len(fx.dms)), ('published', 1, 1))

    async def test_uncertain_then_held_holds_the_run(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok')
        await fx.ticks(2)
        post_id = fx.post()['id']
        fx.state.transition(post_id, 'before_publish', 'claimed')
        fx.state.transition(post_id, 'uncertain', 'publish_interrupted')
        await fx.tick()
        fx.state.transition(post_id, 'held', 'owner hold')
        row = await fx.tick()
        self.assertEqual((row['stage'], row['terminal_reason'], len(fx.dms)), ('held', 'post_held', 2))

    async def test_each_row_is_judged_by_a_fresh_clock(self):
        fx = self.fixture(topics=((3, 6, 'hot-trend'), (3, 7, 'nail-job')))   # 07:00 slot: image deadline 04:00
        fx.onepost.on_prepare = lambda: setattr(fx, 'now', at(3, 4, 30)) if fx.onepost.n('prepare') == 1 else None
        await fx.tick(at(2, 13))
        second = fx.row(fx.topic_ids[1])
        self.assertEqual((fx.onepost.n('prepare'), second['stage'], second['terminal_reason']), (1, 'skipped', 'images_deadline_missed'))

    async def test_draft_ok_given_in_time_but_processed_late_is_not_approved(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok', when=at(3, 3, 59))
        row = await fx.tick(at(3, 4, 0, 5))
        await fx.ticks(2)
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('approve'), len(fx.dms)), ('held', 'approval_deadline_passed', 0, 1))


class DispatchStoppedTests(Base):
    async def test_dispatch_stopped_is_not_a_failure_and_resumes_next_tick(self):
        fx = self.fixture()
        fx.onepost.prepare_error = AdminError('dispatch_stopped')
        await fx.tick()
        await fx.tick()
        row = fx.row()
        self.assertEqual((row['stage'], row['failures'], row['next_attempt_at'], len(fx.dms), fx.onepost.n('prepare')),
                         ('new', 0, 0, 0, 2))
        fx.onepost.prepare_error = None
        row = await fx.tick()
        self.assertEqual((row['stage'], fx.onepost.n('prepare')), ('images_send', 3))


class DeadlineTests(Base):
    async def test_images_deadline_expires_request_and_skips_slot(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        row = await fx.tick(at(3, 3, 0, 1))      # image deadline is slot - 3h = 03:00
        self.assertEqual((row['stage'], row['terminal_reason']), ('skipped', 'images_deadline_missed'))
        self.assertEqual([r['status'] for r in fx.requests('images')], ['expired'])
        await fx.ticks(2)
        self.assertEqual((fx.onepost.n('accept_images'), fx.onepost.n('save'), len(fx.dms)), (0, 0, 1))
        late = fx.approvals.decide(fx.requests('images')[0]['id'], 'ok', None, OWNER, OWNER, fx.now)
        self.assertEqual((late['ok'], late['code']), (False, 'expired'))

    async def test_draft_deadline_expires_request_and_skips_slot(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        row = await fx.tick(at(3, 4, 0, 1))      # draft deadline is slot - 2h = 04:00
        self.assertEqual((row['stage'], row['terminal_reason']), ('skipped', 'draft_deadline_missed'))
        self.assertEqual([r['status'] for r in fx.requests('draft')], ['expired'])
        await fx.ticks(2)
        self.assertEqual((fx.onepost.n('approve'), len(fx.dms)), (0, 1))

    async def test_start_after_the_images_deadline_skips_without_spending(self):
        fx = self.fixture()
        fx.onepost.prepare_error = RuntimeError('boom')
        row = await fx.tick()
        row = await fx.tick(at(3, 3, 5))
        self.assertEqual((row['stage'], row['terminal_reason'], fx.onepost.n('prepare'), len(fx.dms)),
                         ('skipped', 'images_deadline_missed', 1, 1))


class RestartTests(Base):
    async def test_recorded_card_is_not_resent_after_a_crash_before_the_stage_move(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='images_send')
        row = await fx.tick(restart=True)
        self.assertEqual((row['stage'], len(fx.media), fx.onepost.n('prepare')), ('images_wait', 1, 1))
        self.assertEqual(len(fx.requests('images')), 1)

    async def test_restart_between_ok_and_accept_or_save_or_approve_repeats_nothing(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'ok')
        fx.restart()
        await fx.tick(restart=True)
        # crash after accept_images but before the move to `saving` was stored
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='images_wait')
        await fx.tick(restart=True)
        self.assertEqual((fx.onepost.n('accept_images'), fx.row()['stage']), (1, 'saving'))
        await fx.tick(restart=True)
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='saving')       # crash after save, before the move
        await fx.tick(restart=True)
        self.assertEqual((fx.onepost.n('save'), fx.row()['stage']), (1, 'draft_send'))
        await fx.tick(restart=True)
        fx.decide('draft', 'ok')
        await fx.tick(restart=True)
        autoflow.AutoflowRuns(fx.state).update(fx.run_id, stage='draft_wait')   # crash after approve, before the move
        await fx.tick(restart=True)
        self.assertEqual((fx.onepost.n('approve'), fx.row()['stage'], fx.onepost.n('prepare')), (1, 'scheduled', 1))

    async def test_media_error_records_sent_ids_and_resends_after_backoff(self):
        fx = self.fixture()
        await fx.tick()
        fx.media_error = OwnerMediaSendError([55, 56], 'TimeoutError')
        row = await fx.tick()
        request = fx.requests('images')[0]
        self.assertEqual((row['stage'], row['failures'], json.loads(request['message_ids'])), ('images_send', 1, [55, 56]))
        await fx.tick(fx.now + 60)
        self.assertEqual(len(fx.media), 1)
        row = await fx.tick(row['next_attempt_at'] + 1)
        self.assertEqual((row['stage'], len(fx.media), len(fx.requests('images'))), ('images_wait', 2, 1))
        self.assertNotEqual(json.loads(fx.requests('images')[0]['message_ids']), [55, 56])

    async def test_cancelled_send_is_not_recorded_and_the_card_is_sent_again(self):
        fx = self.fixture()
        await fx.tick()
        fx.media_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await fx.tick()
        self.assertEqual((fx.row()['stage'], fx.requests('images')[0]['message_ids']), ('images_send', '[]'))
        row = await fx.tick()
        self.assertEqual((row['stage'], len(fx.media), len(fx.pending('images'))), ('images_wait', 2, 1))


class BlackoutTests(Base):
    def test_blackout_window_is_twenty_minutes_before_any_slot(self):
        config = SimpleNamespace(slots=('06:00', '19:00', '00:10'))
        for moment, expected in ((at(3, 5, 39, 59), False), (at(3, 5, 40), True), (at(3, 5, 59, 59), True),
                                 (at(3, 6, 0), False), (at(3, 18, 40), True), (at(3, 12, 0), False),
                                 (at(3, 23, 50), True), (at(3, 23, 49), False)):
            self.assertIs(autoflow.in_slot_blackout(config, moment), expected, datetime.fromtimestamp(moment, VN))

    async def test_no_draft_is_saved_within_twenty_minutes_before_a_slot(self):
        fx = self.fixture(topics=((3, 19, 'nail-salon'),))
        fx.now = at(2, 20)
        await fx.ticks(2)
        fx.decide('images', 'ok', when=at(3, 5, 30))
        row = await fx.tick(at(3, 5, 41))                 # accepting images is not a browser job
        self.assertEqual((row['stage'], fx.onepost.n('accept_images')), ('saving', 1))
        await fx.tick(at(3, 5, 50))
        await fx.tick(at(3, 5, 59, 59))
        self.assertEqual((fx.onepost.n('save'), fx.row()['stage']), (0, 'saving'))
        row = await fx.tick(at(3, 6, 0))
        self.assertEqual((fx.onepost.n('save'), row['stage'], fx.onepost.args('save')[0][1]), (1, 'draft_send', 'guid-salon'))


class TableTests(Base):
    async def test_table_is_created_idempotently_with_the_planned_columns(self):
        fx = self.fixture()
        autoflow.AutoflowRuns(fx.state)
        autoflow.AutoflowRuns(fx.state)
        with fx.state.connection() as db:
            columns = [r['name'] for r in db.execute('PRAGMA table_info(autoflow_runs)')]
        self.assertEqual(columns[:9], ['run_id', 'topic_id', 'slot_at', 'out_dir', 'run_usd', 'stage', 'failures',
                                       'next_attempt_at', 'terminal_reason'])
        await fx.tick()
        with self.assertRaises(sqlite3.IntegrityError):
            with fx.state.connection() as db:
                db.execute("INSERT INTO autoflow_runs(run_id,topic_id,slot_at,out_dir,run_usd,stage) VALUES('x',?,'s','o',1,'new')",
                           (fx.topic,))


if __name__ == '__main__':
    unittest.main()
