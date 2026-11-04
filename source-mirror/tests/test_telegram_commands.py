import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from source_mirror.telegram_commands import route


def event(sender=42, forwarded=False):
    person = SimpleNamespace(id=sender, bot=False, is_self=False)
    return SimpleNamespace(raw_text='/source_mirror pause', sender_id=sender, chat_id=sender,
        is_private=True, is_group=False, is_channel=False, out=False, fwd_from=None,
        message=SimpleNamespace(id=77, fwd_from=object() if forwarded else None),
        get_sender=AsyncMock(return_value=person), get_chat=AsyncMock(return_value=person), reply=AsyncMock())


class SourceMirrorCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_only_and_forwarded_messages_are_ignored(self):
        for item in (event(sender=99), event(forwarded=True)):
            with patch('source_mirror.telegram_commands.submit') as submit:
                self.assertTrue(await route(item, [42], '/unused'))
                submit.assert_not_called(); item.reply.assert_not_called()

    async def test_owner_response_distinguishes_hold_and_replay(self):
        item = event()
        with patch('source_mirror.telegram_commands.submit', return_value={'state':'HOLD','publication_enabled':False,'replayed':False}):
            self.assertTrue(await route(item, [42], '/unused'))
        self.assertIn('chưa đăng bài', item.reply.call_args.args[0])
        item = event()
        with patch('source_mirror.telegram_commands.submit', return_value={'state':'running','publication_enabled':False,'replayed':True}):
            await route(item, [42], '/unused')
        self.assertIn('đã xử lý', item.reply.call_args.args[0])
