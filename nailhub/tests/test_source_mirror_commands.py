import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from nailhub.source_mirror_commands import route, notifications
import asyncio


def event(sender=42, forwarded=False):
    person = SimpleNamespace(id=sender, bot=False, is_self=False)
    return SimpleNamespace(raw_text='/mirror pause', sender_id=sender, chat_id=sender,
        is_private=True, is_group=False, is_channel=False, out=False, fwd_from=None,
        message=SimpleNamespace(id=77, fwd_from=object() if forwarded else None),
        get_sender=AsyncMock(return_value=person), get_chat=AsyncMock(return_value=person), reply=AsyncMock())


class SourceMirrorCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_only_and_forwarded_messages_are_ignored(self):
        for item in (event(sender=99), event(forwarded=True)):
            with patch('nailhub.source_mirror_commands.submit') as submit:
                self.assertTrue(await route(item, [42], '/unused'))
                submit.assert_not_called(); item.reply.assert_not_called()

    async def test_owner_response_distinguishes_hold_and_replay(self):
        item = event()
        with patch('nailhub.source_mirror_commands.submit', return_value={'state':'HOLD','publication_enabled':False,'replayed':False}):
            self.assertTrue(await route(item, [42], '/unused'))
        self.assertIn('chưa đăng bài', item.reply.call_args.args[0])
        item = event()
        with patch('nailhub.source_mirror_commands.submit', return_value={'state':'running','publication_enabled':False,'replayed':True}):
            await route(item, [42], '/unused')
        self.assertIn('đã xử lý', item.reply.call_args.args[0])

    async def test_nailblog_slash_remains_unclaimed(self):
        item=event(); item.raw_text='/nailblog https://example.com/x'
        self.assertFalse(await route(item,[42],'/unused'))

    async def test_status_describes_state_not_a_resume_action(self):
        item=event(); item.raw_text='/mirror status'
        with patch('nailhub.source_mirror_commands.submit',return_value={'state':'running','publication_enabled':False,'replayed':False,'queue':{},'articles':{'ready':3,'held':2},'published_today':1}):
            await route(item,[42],'/unused')
        self.assertIn('đang hoạt động',item.reply.call_args.args[0])
        self.assertNotIn('đã tiếp tục',item.reply.call_args.args[0])
        self.assertIn('Hàng đợi lệnh:',item.reply.call_args.args[0])
        self.assertIn('Bài sẵn: 3; bài giữ: 2; đã đăng hôm nay: 1',item.reply.call_args.args[0])

    async def test_notification_sends_only_to_owner_and_acks_notice_id(self):
        stop=asyncio.Event(); calls=[]
        def call(path, request):
            calls.append(request)
            if request['operation']=='claim':
                return {'notice':{'notice_id':9,'message_id':77,'payload':{'state':'published'}}}
            if request['operation']=='sent': stop.set()
            return {'notice':None}
        client=SimpleNamespace(send_message=AsyncMock())
        with patch('nailhub.source_mirror_commands.call', side_effect=call):
            await notifications(client,42,'/unused',stop)
        self.assertEqual(client.send_message.call_args.args[0],42)
        self.assertEqual(calls[-1]['message_id'],9)
        self.assertEqual(calls[-1]['operation'],'sent')

    async def test_failed_notification_is_unknown_not_resent(self):
        stop=asyncio.Event(); calls=[]
        def call(path, request):
            calls.append(request)
            if request['operation']=='claim': return {'notice':{'notice_id':9,'message_id':77,'payload':{'state':'held'}}}
            if request['operation']=='unknown': stop.set()
            return {'notice':None}
        client=SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError()))
        with patch('nailhub.source_mirror_commands.call',side_effect=call):
            await notifications(client,42,'/unused',stop)
        self.assertEqual(calls[-1]['operation'],'unknown')
        self.assertEqual(client.send_message.await_count,1)
