"""Offline same-client notification/state tests, not actual Telegram delivery evidence."""
import asyncio
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from nailhub.commands import make_owner_notice_sender, route_command
from nailhub.notices import VERSION, manual_notice, pending_messages, units
from nailhub.service import Service
from nailhub.state import State
from tests.nailhub_fixtures import OWNER, POST_ID, dm, settings

SECOND = '2fe2ceb9-3d2d-45bd-880f-c99da56de92b'


def hold(state, post_id=POST_ID, *, reason='invalid evidence span', fingerprint='original',
         title='Tìm thợ và cho thuê phòng wax', status='needs-human'):
    state.observe(post_id, fingerprint, {'fields': {'title': title}, 'content': 'PRIVATE ORIGINAL BODY'})
    state.mark(post_id, status, reason)
    return state.get(post_id)


class NoticeTests(unittest.IsolatedAsyncioTestCase):
    def service(self, directory):
        service = Service(settings(directory), browser_factory=Mock(side_effect=AssertionError('no browser')))
        service.notice_owner_id = OWNER
        service.notice_send = AsyncMock()
        return service

    async def test_backfill_two_holds_success_dedup_restart_and_owner_change(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            hold(service.state)
            hold(service.state, SECOND, reason='location conflicts with current field')
            await service.notify_manual(now=1000)
            self.assertEqual(service.notice_send.await_count, 2)
            texts = '\n'.join(call.args[0] for call in service.notice_send.await_args_list)
            self.assertIn(POST_ID, texts);self.assertIn(SECOND, texts)
            self.assertIn('Đối chiếu', texts);self.assertNotIn('PRIVATE ORIGINAL BODY', texts)
            await service.notify_manual(now=1010)
            self.assertEqual(service.notice_send.await_count, 2)
            restarted = self.service(directory)
            await restarted.notify_manual(now=1020)
            restarted.notice_send.assert_not_awaited()
            restarted.notice_owner_id = OWNER + 1
            await restarted.notify_manual(now=1030)
            self.assertEqual(restarted.notice_send.await_count, 2)

    async def test_changed_reason_fingerprint_state_and_version_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory);row = hold(service.state)
            first = service.state.notice_key(row, VERSION, OWNER)
            self.assertNotEqual(first, service.state.notice_key(row, VERSION+1, OWNER))
            await service.notify_manual(now=1000)
            service.state.mark(POST_ID, 'needs-human', 'location conflicts with current field')
            await service.notify_manual(now=1010)
            hold(service.state, fingerprint='changed source', reason='location conflicts with current field')
            await service.notify_manual(now=1020)
            service.state.mark(POST_ID, 'uncertain', 'location conflicts with current field')
            await service.notify_manual(now=1030)
            self.assertEqual(service.notice_send.await_count, 4)
            self.assertIn('chưa xác định kết quả', service.notice_send.await_args.args[0])

    async def test_delivery_failure_sanitized_throttled_retry_then_ack_only_after_success(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory);row = hold(service.state)
            key = service.state.notice_key(row, VERSION, OWNER)
            service.notice_send.side_effect = OSError('SECRET TOKEN PRIVATE HTTP BODY')
            with self.assertLogs('nailhub.service', level='WARNING') as caught:
                await service.notify_manual(now=1000)
            self.assertNotIn('SECRET', '\n'.join(caught.output))
            self.assertFalse(service.state.notice_due(key, 1299))
            self.assertTrue(service.state.notice_due(key, 1300))
            await service.notify_manual(now=1010)
            self.assertEqual(service.notice_send.await_count, 1)
            self.assertEqual(service.state.get(POST_ID)['state'], 'needs-human')
            self.assertIn(POST_ID, service.command('status'))
            service.state.finish_scan('scan complete; processed=1')
            self.assertIn('last=scan complete; processed=1',service.command('status'))
            async def success(text):
                self.assertTrue(service.state.notice_due(key, 1300))
                return SimpleNamespace(id=5678)
            service.notice_send.side_effect = success
            await service.notify_manual(now=1300)
            self.assertFalse(service.state.notice_due(key, 2000))
            with service.state.connection() as db:
                delivered=db.execute('SELECT * FROM manual_notices WHERE key=?',(key,)).fetchone()
                self.assertEqual(delivered['recipient'],OWNER);self.assertEqual(delivered['message_id'],5678)
            service.browser_factory.assert_not_called()

    async def test_send_timeout_uses_same_failure_retry_without_scan_or_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory);row = hold(service.state)
            async def timeout(awaitable, timeout):
                self.assertEqual(timeout, 20);awaitable.close();raise TimeoutError()
            with patch('nailhub.service.asyncio.wait_for', side_effect=timeout):
                await service.notify_manual(now=1000)
            self.assertFalse(service.state.notice_due(service.state.notice_key(row, VERSION, OWNER), 1010))
            service.browser_factory.assert_not_called()

    async def test_unbound_and_resolved_posts_send_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory);hold(service.state)
            service.notice_owner_id = None
            await service.notify_manual(now=1000)
            service.notice_send.assert_not_awaited()
            service.notice_owner_id = OWNER;service.state.mark(POST_ID, 'approved')
            await service.notify_manual(now=1010)
            service.notice_send.assert_not_awaited()

    async def test_current_state_recheck_and_batch_throttle(self):
        with tempfile.TemporaryDirectory() as directory:
            service=self.service(directory)
            for index in range(8):hold(service.state,f'11111111-1111-4111-8111-{index:012d}')
            async def delivered(text):
                service.state.mark('11111111-1111-4111-8111-000000000001','approved')
            service.notice_send.side_effect=delivered
            await service.notify_manual(now=1000)
            self.assertEqual(service.notice_send.await_count,5)
            self.assertNotIn('000000000001','\n'.join(call.args[0] for call in service.notice_send.await_args_list))
            await service.notify_manual(now=1001)
            self.assertEqual(service.notice_send.await_count,5)
            await service.notify_manual(now=1005)
            self.assertEqual(service.notice_send.await_count,7)

    async def test_paused_worker_backfills_before_any_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory);hold(service.state);service.state.pause(True)
            stop = asyncio.Event()
            async def delivered(text):stop.set()
            service.notice_send.side_effect = delivered
            service.scan = AsyncMock(side_effect=AssertionError('paused'))
            await asyncio.wait_for(service.worker(stop), timeout=2)
            service.notice_send.assert_awaited_once();service.scan.assert_not_awaited()

    async def test_same_active_client_exact_numeric_peer_plain_no_preview(self):
        result=SimpleNamespace(id=5678)
        client = SimpleNamespace(send_message=AsyncMock(return_value=result))
        sender = make_owner_notice_sender(client, (OWNER,))
        self.assertIs(await sender('clickable https://nailhub.ai/admin/postings?id='+POST_ID),result)
        client.send_message.assert_awaited_once_with(OWNER, 'clickable https://nailhub.ai/admin/postings?id='+POST_ID,
                                                    parse_mode=None, link_preview=False)
        for invalid in ((), (OWNER,OWNER+1), (True,), (-100,)):
            with self.assertRaises(ValueError):make_owner_notice_sender(client, invalid)

    async def test_status_pending_multiple_pages_plain_replies_no_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            service = self.service(directory)
            for index in range(30):
                hold(service.state, f'11111111-1111-4111-8111-{index:012d}', title='😀'*10000,
                     status='uncertain' if index%2 else 'needs-human', reason='UNKNOWN SECRET HTTP BODY')
            service.state.finish_scan('UNKNOWN SECRET HTTP BODY')
            response = service.command('status')
            self.assertIsInstance(response, list)
            self.assertGreater(len(response), 1)
            text = '\n'.join(response)
            self.assertEqual(text.count('https://nailhub.ai/admin/postings?id='), 30)
            self.assertNotIn('SECRET', text);self.assertNotIn('PRIVATE ORIGINAL', text)
            self.assertTrue(all(units(part)<=3500 for part in response))
            event = dm(raw_text='/nailhub pending')
            await route_command(event, service, (OWNER,))
            self.assertGreater(event.reply.await_count, 1)
            self.assertTrue(all(call.kwargs=={'parse_mode':None,'link_preview':False} for call in event.reply.await_args_list))
            with service.state.connection() as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM manual_notices').fetchone()[0],0)
            for rejected in (dm(raw_text='/nailhub pending',out=True),
                             dm(raw_text='/nailhub pending',is_private=False,is_group=True)):
                await route_command(rejected,service,(OWNER,));rejected.reply.assert_not_awaited()

    def test_additive_table_reopen_and_invalid_identity_safe_format(self):
        with tempfile.TemporaryDirectory() as directory:
            state = State(settings(directory).db_path);hold(state)
            with state.connection() as db:db.execute('DROP TABLE manual_notices')
            reopened = State(state.path)
            self.assertEqual(len(reopened.manual_posts()),1)
            self.assertIsNone(manual_notice({**reopened.manual_posts()[0],'id':'javascript:secret'}))
            self.assertIn('Không có tin',pending_messages([])[0])
