import asyncio
import tempfile
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from tg_triage import telegram_io
from tg_triage.nailhub.commands import route_command, verify_owner
from tg_triage.store import Store
from tests.helpers import config as listener_config
from tests.test_telegram_io import event as group_event
from tests.nailhub_fixtures import OWNER, dm, settings

class CommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_authorized_exact_commands_reply_same_dm(self):
        for command in ('scan','status','pending','pause','resume'):
            service=SimpleNamespace(command=Mock(return_value='ok'));event=dm(raw_text='/nailhub '+command)
            self.assertTrue(await route_command(event,service,(OWNER,)))
            service.command.assert_called_once_with(command);event.reply.assert_awaited_once_with('ok',parse_mode=None,link_preview=False)

    async def test_unauthorized_metadata_consumed_without_job_reply(self):
        variants=[{'sender_id':99},{'chat_id':99},{'out':True},{'out':None},
            {'is_private':False,'is_group':True,'chat_id':-100},{'is_channel':True},{'fwd_from':object()},
            {'message':SimpleNamespace(fwd_from=object())},{'sender_id':True},{'chat_id':str(OWNER)},{'is_private':None}]
        for value in variants:
            with self.subTest(value=value):
                service=SimpleNamespace(command=Mock());event=dm(**value)
                self.assertTrue(await route_command(event,service,(OWNER,)))
                service.command.assert_not_called();event.reply.assert_not_awaited()

    async def test_bot_self_chat_lookup_failure_empty_allowlist_denied(self):
        cases=[(dm(get_sender=AsyncMock(return_value=SimpleNamespace(id=OWNER,bot=True))),(OWNER,)),
               (dm(get_chat=AsyncMock(return_value=SimpleNamespace(id=OWNER,bot=False,is_self=True))),(OWNER,)),
               (dm(get_sender=AsyncMock(side_effect=OSError())),(OWNER,)),(dm(),())]
        for event,ids in cases:
            service=SimpleNamespace(command=Mock());self.assertTrue(await route_command(event,service,ids))
            service.command.assert_not_called();event.reply.assert_not_awaited()

    async def test_malformed_namespace_consumed_not_executed(self):
        for text in ('/nailhub scan now','/nailhub@other scan','/NAILHUB scan',' /nailhub scan','/nailhub'):
            service=SimpleNamespace(command=Mock());event=dm(raw_text=text)
            self.assertTrue(await route_command(event,service,(OWNER,)));service.command.assert_not_called()
        self.assertFalse(await route_command(dm(raw_text='ordinary DM'),SimpleNamespace(),(OWNER,)))

    async def test_username_numeric_binding_required(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory,owner_ids=(OWNER,));user=SimpleNamespace(id=OWNER,bot=False,username='SotaThao')
            client=SimpleNamespace(get_entity=AsyncMock(return_value=user))
            self.assertEqual(await verify_owner(client,cfg,SimpleNamespace(id=42)),(OWNER,))
            self.assertEqual(await verify_owner(client,cfg,SimpleNamespace(id=OWNER)),())
            client.get_entity=AsyncMock(return_value=SimpleNamespace(id=33,bot=False,username='SotaThao'))
            self.assertEqual(await verify_owner(client,cfg,SimpleNamespace(id=42)),())
            self.assertEqual(await verify_owner(client,replace(cfg,owner_ids=()),SimpleNamespace(id=42)),())

    async def test_namespace_before_group_filters_preserves_scope(self):
        store=Store(':memory:');self.addCleanup(store.close);queue=asyncio.Queue();service=SimpleNamespace(command=Mock())
        for incoming in (group_event(raw_text='/nailhub scan'),dm(),group_event(chat_id=-999)):
            if not await route_command(incoming,service,()):
                await telegram_io.enqueue_event(incoming,queue,listener_config().telegram.chat_ids,store)
        self.assertTrue(queue.empty());service.command.assert_not_called()
        await telegram_io.enqueue_event(group_event(),queue,listener_config().telegram.chat_ids,store)
        self.assertEqual(queue.qsize(),1)
        await telegram_io.enqueue_event(group_event(is_private=True),queue,listener_config().telegram.chat_ids,store)
        self.assertEqual(queue.qsize(),1)

    async def test_configured_builder_sees_dm_unverified_owner_starts_no_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory);store=Store(':memory:');self.addCleanup(store.close);builders=[];handlers=[]
            client=SimpleNamespace(add_event_handler=lambda h,b:(handlers.append(h),builders.append(b)),
                connect=AsyncMock(),get_me=AsyncMock(return_value=SimpleNamespace(bot=False,id=42)),
                disconnect=AsyncMock(),run_until_disconnected=AsyncMock(),remove_event_handler=Mock())
            fake=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
            with patch.dict('sys.modules',{'telethon':SimpleNamespace(events=SimpleNamespace(NewMessage=lambda **k:k))}),\
                 patch('tg_triage.telegram_io.make_client',return_value=client),\
                 patch('tg_triage.nailhub.config.load_optional',return_value=cfg),\
                 patch('tg_triage.nailhub.service.Service',return_value=fake):
                with self.assertRaises(RuntimeError):
                    await telegram_io.run(listener_config(),SimpleNamespace(store=store,process=lambda m:None))
                await handlers[0](dm())
            self.assertEqual(builders,[{}]);fake.worker.assert_not_called();fake.command.assert_not_called()
            self.assertFalse(hasattr(fake,'notice_send'))

    async def test_verified_lifecycle_binds_notices_to_the_one_existing_client(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory,owner_ids=(OWNER,));store=Store(':memory:');self.addCleanup(store.close)
            user=SimpleNamespace(id=OWNER,bot=False,username='SotaThao')
            client=SimpleNamespace(add_event_handler=Mock(),connect=AsyncMock(),
                get_me=AsyncMock(return_value=SimpleNamespace(bot=False,id=42)),
                get_entity=AsyncMock(return_value=user),send_message=AsyncMock(return_value=SimpleNamespace(id=5678)),
                disconnect=AsyncMock(),run_until_disconnected=AsyncMock(),remove_event_handler=Mock())
            fake=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
            with patch.dict('sys.modules',{'telethon':SimpleNamespace(events=SimpleNamespace(NewMessage=lambda **k:k))}),\
                 patch('tg_triage.telegram_io.make_client',return_value=client) as make_client,\
                 patch('tg_triage.nailhub.config.load_optional',return_value=cfg),\
                 patch('tg_triage.nailhub.service.Service',return_value=fake):
                with self.assertRaises(RuntimeError):
                    await telegram_io.run(listener_config(),SimpleNamespace(store=store,process=lambda m:None))
                make_client.assert_called_once();client.connect.assert_awaited_once()
                self.assertEqual(fake.notice_owner_id,OWNER)
                result=await fake.notice_send('manual notice')
                self.assertEqual(result.id,5678)
                client.send_message.assert_awaited_once_with(OWNER,'manual notice',parse_mode=None,link_preview=False)
