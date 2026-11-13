"""Offline regressions for GATE 0b findings; real SQLite stores and transport boundaries."""
import asyncio
import copy
import json
import queue
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from nailblog import autoflow, daily, net
from nailblog.admin import Admin, public_webp
from nailblog.admin_state import AdminStore
from nailblog.approvals import ApprovalStore
from nailblog.commands import decide_approval, preflight
from nailblog.config import AutoflowConfig
from nailblog.images import ImageError
from nailblog.images.subscription import (TEXT_PROTOCOL, Worker, atomic, heartbeat_payload, text_config_identity, text_job_id)
from nailblog.images.subscription_io import Directory
from nailblog.notices import Outbox, media_key, outbox_media_sender
from nailblog.onepost import OnePost
from nailblog.publication import AdminError, digest, STAGING_MEDIA_ORIGIN
from nailblog.service import Service
from nailblog.state import State
from nailblog.telegram import make_owner_media_sender
from tests.helpers import OWNER, write_config
from tests.test_admin_phase2 import config, payload, document, SLOT, POST_ID
from tests.test_admin_duplicates import FakePage, FakeResponse
from tests.test_autoflow import Fx
from tests.test_publish_timeout import FakeAdmin, add_post
from tests.test_subscription import config as subscription_config, request


class BoundPublishTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = config(self.temp.name)
        self.state = State(self.c.db_path)
        self.service = Service(self.c, state=self.state, clock=lambda: SLOT.timestamp())
        self.state.arm(self.service.startup_binding)
        self.events, self.dms = [], []
        self.service.notice_send = AsyncMock(side_effect=lambda text: self.dms.append(text))

    def admin(self):
        return patch('nailblog.admin.Admin', side_effect=lambda cfg: FakeAdmin(self.events))

    def enable_autoflow(self):
        self.c = replace(self.c, autoflow=AutoflowConfig(enabled=True, runs_dir=self.temp.name))
        self.service = Service(self.c, state=self.state, clock=lambda: SLOT.timestamp())
        self.state.arm(self.service.startup_binding)
        self.service.notice_send = AsyncMock(side_effect=lambda text: self.dms.append(text))

    def linked(self, *, approve, image_status="approved"):
        topic = self.state.add_topic('w', 'Tiêu đề bài', 'nail-salon', SLOT.isoformat(), state='approved')
        n = add_post(self.state, self.c)
        with self.state.connection() as db:
            db.execute('UPDATE posts SET topic_id=? WHERE id=?', (topic, n))
        onepost = OnePost(self.service)
        with self.state.connection() as db:
            db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document,post_id) VALUES(?,?,?,?,?)',
                       ('run1', '{}', 'scheduled', json.dumps({'article': {'title': 'Tiêu đề bài'}}), n))
        art = onepost.artifact('run1')
        art.pop('snapshot')
        art['phase'] = 'draft_verified'
        snapshot = digest(art)
        runs = autoflow.AutoflowRuns(self.state)
        runs.create('run1', topic, SLOT.isoformat(), self.temp.name)
        runs.update('run1', stage='scheduled', ctx={'draft': {'snapshot': snapshot},
                                                   'images': {'snapshot': 'image-snapshot'}})
        store = ApprovalStore(self.state)
        if image_status != 'missing':
            image_id = store.request('images', 'run1', 'image-snapshot', SLOT.timestamp() + 100, SLOT.timestamp() - 10)
            if image_status in ('approved', 'rejected'):
                verdict = 'ok' if image_status == 'approved' else 'reject'
                self.assertTrue(store.decide(image_id, verdict, 'owner decision', OWNER, OWNER, SLOT.timestamp())['ok'])
            elif image_status == 'superseded':
                store.request('images', 'run1', 'replacement-image-snapshot', SLOT.timestamp() + 100, SLOT.timestamp())
                self.assertEqual(store.get(image_id)['status'], 'superseded')
        ident = store.request('draft', 'run1', snapshot, SLOT.timestamp() + 100, SLOT.timestamp() - 10)
        if approve:
            self.assertTrue(store.decide(ident, 'ok', None, OWNER, OWNER, SLOT.timestamp())['ok'])
        return n

    async def test_legacy_scheduled_post_never_patches_and_has_one_durable_notice(self):
        self.enable_autoflow()
        n = add_post(self.state, self.c)
        with self.admin():
            for _ in range(2):
                self.assertEqual(await self.service.publish_pass(), [])
        self.assertEqual(self.events, [])
        self.assertEqual(len(self.dms), 1)
        self.assertIn(f'#{n}', self.dms[0])
        self.assertEqual(self.state.get_post(n)['state'], 'scheduled')
        self.assertEqual(Outbox(self.state).pending_count(), 0)

    async def test_missing_owner_transport_keeps_notice_queued_for_later_delivery(self):
        self.enable_autoflow()
        add_post(self.state, self.c)
        self.service.notice_send = None
        with self.admin():
            await self.service.publish_pass()
        self.assertEqual(Outbox(self.state).pending_count(), 1)
        self.assertEqual(self.events, [])

    async def test_linked_run_with_pending_draft_approval_never_patches(self):
        self.enable_autoflow()
        n = self.linked(approve=False)
        with self.admin():
            await self.service.publish_pass()
        self.assertEqual(self.events, [])
        self.assertEqual(self.state.get_post(n)['state'], 'scheduled')

    async def assert_image_status_blocks_publish(self, status):
        self.enable_autoflow()
        n = self.linked(approve=True, image_status=status)
        with self.admin():
            await self.service.publish_pass()
        self.assertEqual(self.events, [])
        self.assertEqual(self.state.get_post(n)['state'], 'scheduled')

    async def test_missing_image_approval_cannot_publish_even_with_draft_ok(self):
        await self.assert_image_status_blocks_publish('missing')

    async def test_rejected_image_approval_cannot_publish_even_with_draft_ok(self):
        await self.assert_image_status_blocks_publish('rejected')

    async def test_superseded_image_approval_cannot_publish_even_with_draft_ok(self):
        await self.assert_image_status_blocks_publish('superseded')

    async def test_matching_owner_image_and_draft_ok_allow_one_publish(self):
        self.enable_autoflow()
        n = self.linked(approve=True)
        with self.admin():
            self.assertEqual(await self.service.publish_pass(), [n])
            self.assertEqual(await self.service.publish_pass(), [])
        self.assertEqual([e for e in self.events if e[0] == 'publish'], [('publish', POST_ID)])

    async def test_changed_document_does_not_reuse_old_owner_ok(self):
        self.enable_autoflow()
        n = self.linked(approve=True)
        with self.state.connection() as db:
            saved = json.loads(db.execute('SELECT document FROM onepost_runs').fetchone()[0])
            saved['article']['title'] = 'Nội dung thay đổi'
            db.execute('UPDATE onepost_runs SET document=?', (json.dumps(saved),))
        with self.admin():
            await self.service.publish_pass()
        self.assertEqual(self.events, [])
        self.assertEqual(self.state.get_post(n)['state'], 'scheduled')

    async def test_unpaused_but_disarmed_or_wrong_binding_never_patches(self):
        add_post(self.state, self.c)
        with self.admin():
            for binding in ('', 'different-build'):
                self.state.arm(binding)
                self.state.set_paused(False)
                await self.service.publish_pass()
        self.assertEqual(self.events, [])

    async def test_admin_binding_mismatch_pauses_and_notifies_once(self):
        AdminStore(self.state, self.c)
        with self.state.connection() as db:
            db.execute("UPDATE admin_binding SET value='different-environment'")
        with self.admin():
            await self.service.publish_pass()
            await self.service.publish_pass()
        self.assertEqual((self.state.control()['paused'], self.state.armed_binding()), (1, ''))
        self.assertEqual(self.events, [])
        self.assertEqual(len(self.dms), 1)
        self.assertIn('state_environment_mismatch', self.dms[0])


class FlowSafetyTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_post_reserves_slot_even_when_other_topic_was_locked(self):
        for state in ('scheduled', 'before_publish', 'uncertain'):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as d:
                fx = Fx(d)
                fx.state.add_post(fx.state.get_topic(fx.topic)['slot_at'], state=state)
                await fx.ticks(2)
                self.assertEqual(fx.onepost.n('prepare'), 0)
                self.assertEqual(autoflow.AutoflowRuns(fx.state).rows(), [])

    async def test_legacy_onepost_conflict_is_paused_with_one_durable_dm(self):
        with tempfile.TemporaryDirectory() as d:
            c = replace(config(d), autoflow=AutoflowConfig(enabled=True))
            service = Service(c)
            OnePost(service)
            with service.state.connection() as db:
                for run in ('a', 'b'):
                    db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document) VALUES(?,?,?,?)',
                               (run, '{}', 'new', '{"topic_id":1}'))
            service.notice_send = AsyncMock()
            await daily._autoflow_pass(service, None)
            await daily._autoflow_pass(service, None)
            self.assertEqual((service.state.control()['paused'], service.state.armed_binding()), (1, ''))
            service.notice_send.assert_awaited_once()
            self.assertIn('legacy_topic_claim_conflict', service.notice_send.await_args.args[0])

    async def test_image_deadline_notice_persists_readiness(self):
        for ready in (True, False):
            with self.subTest(ready=ready), tempfile.TemporaryDirectory() as d:
                fx = Fx(d)
                env = autoflow._Env(fx.service(), None, fx.onepost, fx.approvals, fx.now)
                env.runs.create('r', fx.topic, fx.state.get_topic(fx.topic)['slot_at'], d)
                with patch('nailblog.images.subscription.SubscriptionImages.verify_models',
                           side_effect=None if ready else ImageError('cli_worker_not_ready')):
                    await env.finish(env.runs.get('r'), 'skipped', 'images_deadline_missed')
                self.assertEqual(json.loads(env.runs.get('r')['ctx'])['image_worker_ready'], ready)
                self.assertIn('worker ảnh ready' if ready else 'worker ảnh không ready', fx.dms[0])

    async def test_preflight_refuses_image_only_worker_when_text_not_ready(self):
        with tempfile.TemporaryDirectory() as d:
            c = replace(config(d), autoflow=AutoflowConfig(enabled=True),
                        subscription=subscription_config(d).subscription)
            service = Service(c)
            p = write_config(d)
            ready = Path(c.subscription.queue_dir)/'worker-ready.json'
            for text_ready in (False, True):
                atomic(ready, heartbeat_payload(c.subscription, service.clock(), text=text_ready))
                with patch('nailblog.commands._check_admin', AsyncMock(return_value=[])):
                    failed = await preflight(service, config_path=str(p))
                self.assertEqual('subscription_queue_unavailable' in failed, not text_ready)

    async def test_decision_names_card_slot_title_snapshot_and_real_hold_syntax(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fx(d)
            run_id = 'r'
            onepost = OnePost(Service(fx.config, state=fx.state))
            post = fx.state.add_post(fx.state.get_topic(fx.topic)['slot_at'])
            with fx.state.connection() as db:
                db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document,post_id) VALUES(?,?,?,?,?)',
                           (run_id, '{}', 'new', '{}', post))
            autoflow.AutoflowRuns(fx.state).create(run_id, fx.topic, fx.state.get_topic(fx.topic)['slot_at'], d)
            for kind, verdict in (('images', 'ok'), ('draft', 'reject')):
                ident = fx.approvals.request(kind, run_id, 'abcdef0123456789', fx.now + 60, fx.now)
                reply = decide_approval(fx.state, verdict, ident, 'lý do', sender_id=OWNER,
                                        owner_ids=(OWNER,), now=fx.now)[0]
                for expected in ('ảnh' if kind == 'images' else 'Draft', fx.state.get_topic(fx.topic)['slot_at'],
                                 fx.state.get_topic(fx.topic)['title'], 'abcdef01', f'/nailblog hold {post} <lý do>'):
                    self.assertIn(expected, reply)


class DecisionIdentityTests(unittest.TestCase):
    def test_reply_names_retained_card_title_even_after_topic_and_article_change(self):
        from nailblog.approvals import build_preview
        with tempfile.TemporaryDirectory() as d:
            fx = Fx(d)
            service = Service(fx.config,state=fx.state)
            OnePost(service)
            with fx.state.connection() as db:
                db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document) VALUES(?,?,?,?)',
                           ('run1','{}','images_ready',json.dumps({'article':{'title':'Current changed article'}})))
            slot = fx.state.get_topic(fx.topic)['slot_at']
            autoflow.AutoflowRuns(fx.state).create('run1',fx.topic,slot,d)
            snapshot = 'abcdef0123456789'
            ident = fx.approvals.request('images','run1',snapshot,fx.now+60,fx.now)
            _,card,_ = build_preview('images',{'article':{'title':'Reviewed article title'},'slot_at':slot,
                                             'snapshot':snapshot},[],approval_id=ident)
            box = Outbox(fx.state)
            box.enqueue('card',card,fx.now,kind='media')
            box.sent('card',fx.now,[101])
            fx.approvals.record_messages(ident,[101])
            reply = decide_approval(fx.state,'ok',ident,None,sender_id=OWNER,owner_ids=(OWNER,),now=fx.now)[0]
            self.assertIn('Tiêu đề trên thẻ: Reviewed article title',reply)
            self.assertNotIn('Current changed article',reply)
            self.assertNotIn(fx.state.get_topic(fx.topic)['title'],reply)

    def test_fallback_article_title_is_explicitly_current_not_claimed_as_reviewed(self):
        with tempfile.TemporaryDirectory() as d:
            fx = Fx(d)
            OnePost(Service(fx.config,state=fx.state))
            with fx.state.connection() as db:
                db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document) VALUES(?,?,?,?)',
                           ('run1','{}','draft_verified',json.dumps({'article':{'title':'Generated article title'}})))
            autoflow.AutoflowRuns(fx.state).create('run1',fx.topic,fx.state.get_topic(fx.topic)['slot_at'],d)
            ident = fx.approvals.request('draft','run1','abcdef0123456789',fx.now+60,fx.now)
            reply = decide_approval(fx.state,'ok',ident,None,sender_id=OWNER,owner_ids=(OWNER,),now=fx.now)[0]
            self.assertIn('Tiêu đề bài hiện tại (chưa đối chiếu snapshot): Generated article title',reply)
            self.assertNotIn(fx.state.get_topic(fx.topic)['title'],reply)


class CreateSafetyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = config(self.temp.name)
        self.service = Service(self.c)
        self.service.state.arm(self.service.startup_binding)
        self.store = AdminStore(self.service.state, self.c)

    def claimed(self, *, local=False):
        p = payload()
        if local:
            for media in [p['coverImage'], *p['images']]:
                media['imageUrl'] = media['imageUrl'].replace('/images/', '/nailblog-local/')
            p['translations'][0]['content'] = p['translations'][0]['content'].replace('/images/', '/nailblog-local/')
        n = self.service.state.add_post(SLOT.isoformat(), draft=document(p))
        self.store.claim_create(n, p)
        return n, p

    def test_local_payload_proves_pre_post_and_claim_can_be_released(self):
        n, _ = self.claimed(local=True)
        self.service._note_create_claim(n)
        self.assertTrue(self.store.release_unmaterialized_create(n))
        self.assertIsNone(self.store.operation(n))
        self.assertEqual(self.service.state.control()['last_result'], 'create_claim_released_before_post')
        self.assertEqual(self.service.state.get_post(n)['state'], 'writing')

    def test_materialized_payload_keeps_claim_even_with_empty_receipt(self):
        n, _ = self.claimed()
        before = self.store.operation(n)
        self.assertFalse(self.store.release_unmaterialized_create(n))
        self.assertEqual(self.store.operation(n), before)

    async def test_known_admin_id_readback_mismatch_requires_manual_reconciliation(self):
        n, p = self.claimed()
        self.store.record_draft(n, {**p, 'id': POST_ID})
        wrong = copy.deepcopy(p)
        wrong.update(id=POST_ID)
        wrong['translations'][0]['title'] = 'Khác'
        admin = SimpleNamespace(ready=AsyncMock(), read=AsyncMock(return_value=wrong), save_draft=AsyncMock())
        context = AsyncMock()
        context.write_ready = lambda: None
        context.__aenter__.return_value = admin
        with patch('nailblog.admin.Admin', return_value=context):
            with self.assertRaisesRegex(AdminError, '^create_requires_manual_reconciliation$'):
                await self.service.save_remote_draft(n, p, cover_path='missing', image_paths=[])
        admin.save_draft.assert_not_awaited()

    async def test_fresh_post_readback_mismatch_never_reposts(self):
        p = payload()
        n = self.service.state.add_post(SLOT.isoformat(), draft=document(p))
        bad = copy.deepcopy(p)
        bad.update(id=POST_ID)
        bad['translations'][0]['title'] = 'Khác'
        async def create(value, before_create, **kwargs):
            before_create(value)
            return bad
        admin = SimpleNamespace(ready=AsyncMock(), guard=SimpleNamespace(),
                                find_duplicates=AsyncMock(return_value=[]), save_draft=AsyncMock(side_effect=create))
        context = AsyncMock()
        context.write_ready = lambda: None
        context.__aenter__.return_value = admin
        with patch('nailblog.admin.Admin', return_value=context), \
                patch('nailblog.admin_images.validate_files', return_value=document()['image_hashes']):
            with self.assertRaisesRegex(AdminError, '^create_requires_manual_reconciliation$'):
                await self.service.save_remote_draft(n, p, cover_path='cover', image_paths=['1','2','3'])
        self.assertIsNotNone(self.store.operation(n))
        admin.save_draft.assert_awaited_once()


class PaginationTests(unittest.IsolatedAsyncioTestCase):
    async def scan(self, rows, *, more=False):
        with tempfile.TemporaryDirectory() as d:
            admin = Admin(config(d))
            admin.page = FakePage(more=more)
            admin.list_first = AsyncMock(return_value={'items': rows})
            return await admin.find_duplicates('title', 'slug')

    async def test_no_paging_metadata_short_first_page_without_next_ui_is_complete(self):
        self.assertEqual(await self.scan([{'id':'a','title':'different','slug':'different'}]), [])

    async def test_no_paging_metadata_full_first_page_or_next_ui_fails_closed(self):
        for rows, more in (([{'id':str(i),'title':'different','slug':'different'} for i in range(10)],False),
                           ([{'id':str(i),'title':'different','slug':'different'} for i in range(20)],False),
                           ([{'id':'a','title':'different','slug':'different'}],True)):
            with self.assertRaisesRegex(AdminError, '^duplicate_scan_incomplete$'):
                await self.scan(rows, more=more)

    async def test_id_only_row_is_not_sufficient_to_prove_nonduplicate(self):
        with self.assertRaisesRegex(AdminError, '^duplicate_scan_bad_row$'):
            await self.scan([{'id':'a','status':'Draft'}])


class AlbumCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_caption_timeout_after_album_return_does_not_resend_album(self):
        with tempfile.TemporaryDirectory() as d:
            state = State(Path(d)/'state.db')
            client = SimpleNamespace(send_file=AsyncMock(return_value=[SimpleNamespace(id=11)]))
            async def hanging_caption(*args):
                await asyncio.sleep(3600)
            clock = [1000]
            sender = outbox_media_sender(state, make_owner_media_sender(client, (OWNER,), raw_send=hanging_caption),
                                         clock=lambda: clock[0])
            with patch('nailblog.notices.SEND_TIMEOUT_SECONDS', 0.05):
                with self.assertRaises(TimeoutError):
                    await sender(OWNER, [b'jpeg'], 'caption')
            key = media_key(OWNER, [b'jpeg'], 'caption')
            self.assertEqual(json.loads(Outbox(state).get(key)['album_ids']), [11])
            restarted = outbox_media_sender(State(state.path), make_owner_media_sender(
                client, (OWNER,), raw_send=AsyncMock(return_value=[12])), clock=lambda: 1400)
            self.assertEqual(await restarted(OWNER, [b'jpeg'], 'caption'), [11, 12])
            client.send_file.assert_awaited_once()


class NetworkSafetyTests(unittest.TestCase):
    def test_private_or_loopback_dns_cannot_open_image_connection(self):
        with tempfile.TemporaryDirectory() as d:
            for ip in ('10.0.0.1', '127.0.0.1'):
                with self.subTest(ip=ip), patch('nailblog.admin.resolve', return_value=[(0,0,0,'',(ip,443))]), \
                        patch('nailblog.admin.PinnedHTTPS') as connect:
                    with self.assertRaisesRegex(AdminError, '^published_image_origin_invalid$'):
                        public_webp(config(d), STAGING_MEDIA_ORIGIN+'/image.webp', cover=False)
                    connect.assert_not_called()

    def test_dns_timeout_is_net_error(self):
        with patch('nailblog.net.queue.Queue') as queue_cls, patch('nailblog.net.threading.Thread'):
            queue_cls.return_value.get.side_effect = queue.Empty
            with self.assertRaisesRegex(net.NetError, '^dns_timeout$'):
                net.resolve('unresolved.example')


class WorkerReserveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = subscription_config(self.temp.name)
        self.cfg = replace(self.c.subscription, max_daily_jobs=12)
        self.private = Path(self.cfg.private_dir)
        self.worker = Worker(self.cfg, clock=lambda: 10)
        self.worker.generate = Mock(return_value={})
        self.worker.generate_text = Mock(return_value={'status':'complete'})

    def dispatch(self, *, text=False, label='image'):
        if text:
            schema = {'type':'object'}
            name = text_job_id(label, 'prompt', schema)
            req = {'protocol':TEXT_PROTOCOL,'kind':'text','job_id':name,'purpose':label,'prompt':'prompt',
                   'schema':schema,'attempt':0,'expires_at':1000,'config':text_config_identity(self.cfg)}
        else:
            req = request(self.cfg, prompt=label, run_id=label)
            name = req['job_id']
        path = Path(self.cfg.queue_dir)/name
        path.mkdir(mode=0o700)
        atomic(path/'request.json', req)
        with Directory(path) as job:
            return self.worker.process_job(name, job, self.private, self.private/(name+'.json'))

    def journal(self, n, *, kind=None, day='1970-01-01'):
        for i in range(n):
            entry = {'day':day,'run_id':'old'+str(i),'status':'dispatched'}
            if kind is not None:
                entry['kind'] = kind
            atomic(self.private/(str(i)+'.json'), entry)

    def test_image_reserve_counts_failed_unknown_and_legacy_dispatches(self):
        self.journal(3)  # legacy images have no kind
        self.assertEqual(self.dispatch(label='last-image')['status'], 'complete')
        refused = self.dispatch(label='image-over-reserve')
        self.assertEqual(refused['status'], 'rejected')
        self.assertEqual(refused['error'], 'cli_daily_job_cap')
        self.worker.generate.assert_called_once()
        # Eight text dispatches can still use the reserved slots, then the shared cap stops everything.
        for i in range(8):
            self.assertEqual(self.dispatch(text=True, label='text'+str(i))['status'], 'complete')
        self.assertEqual(self.dispatch(text=True, label='too-many')['error'], 'cli_daily_job_cap')
        self.assertEqual(self.worker.generate_text.call_count, 8)

    def test_text_dispatches_do_not_consume_image_reserve_and_previous_days_do_not_count(self):
        self.journal(3, kind='text')
        for i in range(4, 14):
            atomic(self.private/(str(i)+'.json'), {'day':'1969-12-31','run_id':'old','status':'dispatched'})
        self.assertEqual(self.dispatch(label='image')['status'], 'complete')
        self.assertEqual(self.worker.generate.call_count, 1)


if __name__ == '__main__':
    unittest.main()
