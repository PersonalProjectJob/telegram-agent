"""S8: tests for the LOW findings of the S4/S4b verification (F1, F3, F4, F6, F8). Real sqlite, no network."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nailblog import __main__ as cli
from nailblog import approvals as approvals_module
from nailblog import notices
from nailblog import override_policy
from nailblog import service as service_module
from nailblog import text as text_module
from nailblog import writer as writer_module
from nailblog import admin_state as admin_state_module
from nailblog.admin_state import AdminStore
from nailblog.approvals import ApprovalStore
from nailblog.autoflow import is_hold_code
from nailblog.notices import Outbox
from nailblog.onepost import OnePost
from nailblog.publication import AdminError
from nailblog.service import Service
from nailblog.text import TextModel
from tests.helpers import settings
from tests import test_onepost as _fixture   # module import: unittest must not collect WorkflowTests again

OnePostFixture = _fixture.WorkflowTests

SAVE = '87654321-1234-1234-1234-123456789abc'
CLICHE = {'code': 'cliche', 'kind': 'fixable', 'field': 'sections.0', 'message': 'sáo rỗng'}


class OnePostApproveAfterOverrideTests(unittest.IsolatedAsyncioTestCase):
    """F1: OnePost.approve refuses a Draft that changed after the Owner overrode it, even with an approved
    override row for the OLD snapshot. Reuses the real OnePost fixture of test_onepost (setUp and helpers only)."""
    setUp = OnePostFixture.setUp
    write_document = OnePostFixture.write_document
    prepare = OnePostFixture.prepare
    accepted = OnePostFixture.accepted
    fake_admin = OnePostFixture.fake_admin

    async def verified_draft_with_override(self):
        await self.accepted()
        fake, context = self.fake_admin({})
        with patch('nailblog.admin.Admin', return_value=context):
            with self.assertRaises(TimeoutError):
                await self.workflow.save('one', SAVE)
            await OnePost(Service(self.config, clock=self.service.clock)).save('one', SAVE)
        run = self.workflow.read('one')
        saved = json.loads(run['document'])
        saved['quality'] = {'verdict': 'fixable', 'issues': [CLICHE]}
        self.workflow.store('one', 'draft_verified', saved)
        with self.service.state.connection() as db:
            row = db.execute('SELECT draft_json FROM posts WHERE id=?', (run['post_id'],)).fetchone()
            draft = json.loads(row['draft_json'])
            draft['quality'] = saved['quality']
            db.execute('UPDATE posts SET draft_json=? WHERE id=?', (json.dumps(draft, ensure_ascii=False), run['post_id']))
        self.post_id = run['post_id']
        self.approvals = ApprovalStore(self.service.state)
        self.images_snapshot = 'snap-images'
        self.draft_snapshot = self.workflow.artifact('one')['snapshot']
        now = self.now[0]
        for kind, snapshot in (('images', self.images_snapshot), ('draft', self.draft_snapshot)):
            rid = self.approvals.request(kind, 'one', snapshot, now + 600, now, requires_override=True,
                                         issues=[{**CLICHE, 'overridable': True}])
            self.assertTrue(self.approvals.decide(rid, 'ok', 'bài vẫn dùng được', 7, 7, now, override=True)['ok'])

    def approve(self, snapshot):
        return self.workflow.approve('one', snapshot, approvals=self.approvals, images_snapshot=self.images_snapshot)

    async def test_control_the_overridden_draft_is_approved_while_it_is_unchanged(self):
        await self.verified_draft_with_override()
        approved = self.approve(self.draft_snapshot)
        self.assertEqual(approved['phase'], 'scheduled')
        self.assertEqual(self.service.state.get_post(self.post_id)['state'], 'scheduled')

    async def test_a_draft_changed_after_the_override_is_refused_even_with_the_old_approved_row(self):
        await self.verified_draft_with_override()
        with self.service.state.connection() as db:   # the Draft is edited AFTER the Owner said ok
            draft = json.loads(db.execute('SELECT draft_json FROM posts WHERE id=?', (self.post_id,)).fetchone()[0])
            draft['summary'] = draft['summary'] + ' (sửa sau khi duyệt)'
            db.execute('UPDATE posts SET draft_json=? WHERE id=?', (json.dumps(draft, ensure_ascii=False), self.post_id))
        self.assertTrue(self.approvals.is_approved('one', 'draft', self.draft_snapshot))   # old row still approved
        new_snapshot = self.workflow.artifact('one')['snapshot']
        self.assertNotEqual(new_snapshot, self.draft_snapshot)
        with self.assertRaises(AdminError) as old:
            self.approve(self.draft_snapshot)           # the Owner's snapshot no longer describes the Draft
        with self.assertRaises(AdminError) as new:
            self.approve(new_snapshot)                  # the new snapshot was never approved by the Owner
        self.assertEqual(str(old.exception), 'draft_approval_snapshot_changed')
        self.assertEqual(str(new.exception), 'document_payload_mismatch')   # the edit no longer matches the claimed payload
        self.assertEqual(self.service.state.get_post(self.post_id)['state'], 'review')
        self.assertEqual(self.workflow.read('one')['phase'], 'draft_verified')

    async def test_a_new_quality_issue_after_the_override_is_refused_even_with_the_old_approved_row(self):
        await self.verified_draft_with_override()
        extra = {'code': 'para_len', 'kind': 'fixable', 'field': 'sections.1', 'message': 'đoạn dài'}
        run = self.workflow.read('one')
        saved = json.loads(run['document'])
        saved['quality'] = {'verdict': 'fixable', 'issues': [CLICHE, extra]}
        self.workflow.store('one', 'draft_verified', saved)
        with self.service.state.connection() as db:
            draft = json.loads(db.execute('SELECT draft_json FROM posts WHERE id=?', (self.post_id,)).fetchone()[0])
            draft['quality'] = saved['quality']
            db.execute('UPDATE posts SET draft_json=? WHERE id=?', (json.dumps(draft, ensure_ascii=False), self.post_id))
        self.assertTrue(self.approvals.is_approved('one', 'draft', self.draft_snapshot))
        new_snapshot = self.workflow.artifact('one')['snapshot']
        self.assertNotEqual(new_snapshot, self.draft_snapshot)
        with self.assertRaisesRegex(AdminError, '^draft_approval_snapshot_changed$'):
            self.approve(self.draft_snapshot)
        with self.assertRaisesRegex(AdminError, '^quality_pass_required$'):
            self.approve(new_snapshot)
        self.assertEqual(self.service.state.get_post(self.post_id)['state'], 'review')


class CreateReconciliationReadbackTests(unittest.IsolatedAsyncioTestCase):
    """F3: exactly one Draft candidate whose read-back does not match the claim goes straight to manual
    reconciliation (a hold code, DM, no retry), never to a transient failure that retries up to too_many_failures."""
    setUp = OnePostFixture.setUp
    write_document = OnePostFixture.write_document
    prepare = OnePostFixture.prepare
    accepted = OnePostFixture.accepted
    fake_admin = OnePostFixture.fake_admin

    async def test_a_single_draft_candidate_that_mismatches_needs_a_human_and_is_not_retried(self):
        await self.accepted()
        remote = {}
        fake, context = self.fake_admin(remote, timeout=AdminError('create_timeout'))
        with patch('nailblog.admin.Admin', return_value=context):
            with self.assertRaisesRegex(AdminError, '^create_unknown_reconciling$'):
                await self.workflow.save('one', SAVE)
            altered = copy.deepcopy(remote)
            altered['translations'][0]['summary'] = 'Một bản nháp khác hẳn trên site.'
            remote.clear()
            remote.update(altered)
            for _ in range(3):
                with self.assertRaisesRegex(AdminError, '^create_requires_manual_reconciliation$'):
                    await self.workflow.save('one', SAVE)
        fake.save_draft.assert_awaited_once()                   # one POST in the whole story
        post = self.service.state.get_post(self.workflow.read('one')['post_id'])
        self.assertFalse(post['admin_id'])                      # nothing was adopted

    def test_the_autoflow_engine_holds_this_code_instead_of_retrying(self):
        self.assertTrue(is_hold_code('create_requires_manual_reconciliation'))
        self.assertFalse(is_hold_code('draft_readback_mismatch'))   # the old, retried, outcome


class TextModelSeamTests(unittest.IsolatedAsyncioTestCase):
    """F4: no `**_ignored`; the transport seam is test-only and production never passes it."""

    def test_unknown_keyword_arguments_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TypeError):
                TextModel(settings(directory), transport=object())
            with self.assertRaises(TypeError):
                TextModel(settings(directory), key_reader=object())
            with self.assertRaises(TypeError):
                TextModel(settings(directory), lambda: None)         # the old positional `call`

    def test_the_seam_is_named_as_test_only_and_documented(self):
        self.assertIn('_test_transport', TextModel.__init__.__code__.co_varnames)
        self.assertIn('TEST', TextModel.__init__.__doc__)
        self.assertIn('TEST', text_module._ask_over_transport.__doc__)
        self.assertFalse(hasattr(cli.Runtime(), 'transport'))
        self.assertIsNone(cli.Runtime()._test_transport)

    async def test_the_production_cli_never_passes_a_test_transport(self):
        seen = []

        class Recorder:
            def __init__(self, *args, **kwargs):
                seen.append((args, kwargs))
                raise RuntimeError('stop after construction')
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory)
            for command, args in (('preview', SimpleNamespace(topic=1, out=str(Path(directory) / 'o'), dry_run=False)),
                                  ('plan-week', SimpleNamespace(dry_run=True, topic=None, out=None))):
                with self.subTest(command=command), patch('nailblog.text.TextModel', Recorder):
                    with self.assertRaises(RuntimeError):
                        await cli.execute(command, config, args)      # runtime=None: what main() always passes
        self.assertEqual(len(seen), 2)
        for args, kwargs in seen:
            self.assertEqual(kwargs, {})
            self.assertEqual(len(args), 1)


class MainRuntimeTests(unittest.TestCase):
    def test_main_hands_no_runtime_to_execute(self):
        from tests.helpers import write_config
        with tempfile.TemporaryDirectory() as directory:
            path = write_config(directory)
            with patch.object(cli, 'execute', AsyncMock()) as execute:
                self.assertEqual(cli.main(['status', '--config', str(path)]), 0)
        self.assertIsNone(execute.await_args.args[3])          # runtime=None: execute() builds a plain Runtime()


class OutboxSendTimeoutTests(unittest.IsolatedAsyncioTestCase):
    """F6: a hung Telegram send is a failed send (backoff) after the timeout and must not stall the scheduler."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    async def test_send_once_times_out_into_backoff(self):
        service = Service(settings(self.temp.name))

        async def hang(text, random_id=None):
            await asyncio.sleep(3600)
        with patch.object(notices, 'SEND_TIMEOUT_SECONDS', 0.05), self.assertLogs('nailblog.notices', 'WARNING'):
            sent = await asyncio.wait_for(notices.send_once(service.state, hang, 'k-hang', 'hello', now=1000.0), 5)
        row = Outbox(service.state).get('k-hang')
        self.assertFalse(sent)
        self.assertEqual((row['status'], row['attempts'], row['last_error']), ('pending', 1, 'TimeoutError'))
        self.assertGreater(row['next_attempt_at'], 1000.0)

    async def test_a_hung_send_does_not_stop_the_scheduler_from_running_publish_pass(self):
        service = Service(settings(self.temp.name, mode='live'))
        service.publish_pass = AsyncMock(return_value=None)
        calls = []

        async def hang(text, random_id=None):
            calls.append(text)
            await asyncio.sleep(3600)
        service.notice_send = hang
        Outbox(service.state).enqueue('k-queued', 'DM đang chờ', 0.0)
        stop = asyncio.Event()
        with patch.object(notices, 'SEND_TIMEOUT_SECONDS', 0.05), patch.object(service_module, 'TICK_SECONDS', 0.05), \
                self.assertLogs('nailblog.notices', 'WARNING'):
            task = asyncio.create_task(service.scheduler(stop))
            for _ in range(100):
                await asyncio.sleep(0.05)
                if service.publish_pass.await_count >= 3:
                    break
            stop.set()
            try:
                await asyncio.wait_for(task, 3)
            finally:
                task.cancel()
        self.assertGreaterEqual(service.publish_pass.await_count, 3)   # without the timeout it would stay at 1
        self.assertEqual(len(calls), 1)                                # backoff: the hung message is not retried every tick
        self.assertEqual(Outbox(service.state).get('k-queued')['last_error'], 'TimeoutError')


class NeverOverridableTests(unittest.TestCase):
    """F8: one constant, imported by writer, approvals and admin_state."""

    def test_a_single_definition(self):
        self.assertEqual(override_policy.NEVER_OVERRIDABLE, frozenset({'facts', 'unexpected_han', 'medical_claim'}))
        self.assertIs(writer_module.NEVER_OVERRIDABLE, override_policy.NEVER_OVERRIDABLE)
        self.assertIs(approvals_module.NEVER_OVERRIDABLE, override_policy.NEVER_OVERRIDABLE)
        self.assertIs(admin_state_module.NEVER_OVERRIDABLE, override_policy.NEVER_OVERRIDABLE)
        self.assertFalse(hasattr(writer_module, 'NEVER_OVERRIDE'))

    def test_the_card_issue_filter_follows_the_shared_constant(self):
        for code in override_policy.NEVER_OVERRIDABLE:
            self.assertIn(code, approvals_module.NEVER_OVERRIDABLE)


if __name__ == '__main__':
    unittest.main()
