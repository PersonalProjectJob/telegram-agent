import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from tests.transport_fakes import fake_telethon

from nailhub import telegram
from nailhub.__main__ import execute
from nailhub._errors import ConfigError
from nailhub.commands import route_command, verify_owner
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
            cfg=settings(directory,owner_ids=(OWNER,),owner_username='@tEaMoWnEr');user=SimpleNamespace(id=OWNER,bot=False,username='TeamOwner')
            client=SimpleNamespace(get_entity=AsyncMock(return_value=user))
            self.assertEqual(await verify_owner(client,cfg,SimpleNamespace(id=42,bot=False)),(OWNER,))
            self.assertEqual([c.args[0] for c in client.get_entity.await_args_list],['tEaMoWnEr',OWNER])
            self.assertEqual(await verify_owner(client,cfg,SimpleNamespace(id=OWNER,bot=False)),())
            client.get_entity=AsyncMock(return_value=SimpleNamespace(id=33,bot=False,username='TeamOwner'))
            self.assertEqual(await verify_owner(client,cfg,SimpleNamespace(id=42,bot=False)),())
            self.assertEqual(await verify_owner(client,replace(cfg,owner_ids=()),SimpleNamespace(id=42,bot=False)),())

    async def test_invalid_direct_config_and_listener_metadata_deny_without_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory,owner_ids=(OWNER,),owner_username='TeamOwner')
            variants=[replace(cfg,owner_username=value) for value in ('',None,True,123,[], '@','bad name','@@TeamOwner')]
            variants += [replace(cfg,owner_ids=value) for value in ((),None,OWNER,str(OWNER),(True,),[0],[-1],[1.0],[OWNER,99],['123'])]
            client=SimpleNamespace(get_entity=AsyncMock())
            for invalid in variants:
                with self.subTest(config=invalid):
                    self.assertEqual(await verify_owner(client,invalid,SimpleNamespace(id=42,bot=False)),())
            for me in (None,SimpleNamespace(),SimpleNamespace(id=42),SimpleNamespace(id=True,bot=False),
                       SimpleNamespace(id='42',bot=False),SimpleNamespace(id=0,bot=False),
                       SimpleNamespace(id=42,bot=True),SimpleNamespace(id=OWNER,bot=False)):
                with self.subTest(me=me):self.assertEqual(await verify_owner(client,cfg,me),())
            client.get_entity.assert_not_awaited()

    async def test_resolved_owner_metadata_must_be_same_non_bot_human(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory,owner_ids=(OWNER,),owner_username='TeamOwner')
            human=dict(id=OWNER,bot=False,username='TeamOwner')
            variants=[None,SimpleNamespace(),*[SimpleNamespace(**{**human,**change}) for change in (
                {'id':OWNER+1},{'id':str(OWNER)},{'bot':True},{'bot':None},{'is_self':True},
                {'username':None},{'username':123},{'username':[]},{'username':''},{'username':'OtherOwner'})]]
            for invalid in variants:
                for entities in ((invalid,SimpleNamespace(**human)),(SimpleNamespace(**human),invalid)):
                    with self.subTest(entities=entities):
                        client=SimpleNamespace(get_entity=AsyncMock(side_effect=entities))
                        self.assertEqual(await verify_owner(client,cfg,SimpleNamespace(id=42,bot=False)),())

    async def test_verified_non_personal_owner_routes_by_numeric_id_only(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory,owner_ids=(OWNER,),owner_username='TeamOwner')
            user=SimpleNamespace(id=OWNER,bot=False,username='TEAMOWNER')
            client=SimpleNamespace(get_entity=AsyncMock(return_value=user))
            ids=await verify_owner(client,cfg,SimpleNamespace(id=42,bot=False))
            service=SimpleNamespace(command=Mock(return_value='ok'))
            # Metadata usernames (including a changed or absent username) grant no authority.
            for username in ('TeamOwner','SomeoneElse',None):
                peer=SimpleNamespace(id=OWNER,bot=False,username=username)
                event=dm(get_sender=AsyncMock(return_value=peer),get_chat=AsyncMock(return_value=peer))
                self.assertTrue(await route_command(event,service,ids))
                event.reply.assert_awaited_once()
            self.assertEqual(service.command.call_count,3)
            impostor=SimpleNamespace(id=99,bot=False,username='TeamOwner')
            event=dm(sender_id=99,chat_id=99,get_sender=AsyncMock(return_value=impostor),get_chat=AsyncMock(return_value=impostor))
            await route_command(event,service,ids)
            self.assertEqual(service.command.call_count,3);event.reply.assert_not_awaited()

    async def test_absent_or_non_string_message_metadata_and_malformed_allowlist_safe(self):
        for text in (None,123,[],{}):
            service=SimpleNamespace(command=Mock());event=dm(raw_text=text)
            self.assertFalse(await route_command(event,service,(OWNER,)))
            service.command.assert_not_called();event.reply.assert_not_awaited()
        for ids in (None,OWNER,str(OWNER),(True,),[OWNER,99],['123'],()):
            service=SimpleNamespace(command=Mock());event=dm()
            self.assertTrue(await route_command(event,service,ids))
            service.command.assert_not_called();event.reply.assert_not_awaited()
        for method in ('get_sender','get_chat'):
            for peer in (None,SimpleNamespace(),SimpleNamespace(id=OWNER,bot=None),
                         SimpleNamespace(id=str(OWNER),bot=False),SimpleNamespace(id=OWNER,bot=False,is_self=True)):
                service=SimpleNamespace(command=Mock());event=dm(**{method:AsyncMock(return_value=peer)})
                self.assertTrue(await route_command(event,service,(OWNER,)))
                service.command.assert_not_called();event.reply.assert_not_awaited()

    async def test_cli_preview_without_binding_reaches_preview_service(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory)
            self.assertEqual((cfg.owner_username,cfg.owner_ids),('',()))
            service=SimpleNamespace(scan=AsyncMock(return_value='preview complete'),command=Mock(return_value='status'))
            with patch('nailhub.__main__.Service',return_value=service),patch('builtins.print'):
                await execute('preview',cfg)
            service.scan.assert_awaited_once();service.command.assert_called_once_with('status')

    async def test_namespace_before_group_filters_preserves_scope(self):
        # Same scope assertions, with the eligible workload now an Owner DM.
        jobs=[];service=SimpleNamespace(command=Mock(side_effect=lambda c:jobs.append(c) or 'ok'))
        denied=telegram.make_handler(service,())
        incoming=(dm(is_private=False,is_group=True,chat_id=-100,raw_text='/nailhub scan'),
                  dm(),dm(is_private=False,is_group=True,chat_id=-999,raw_text='ordinary group'))
        for event in incoming:
            await denied(event)
            event.reply.assert_not_awaited()
        self.assertFalse(jobs);service.command.assert_not_called()
        authorized=telegram.make_handler(service,(OWNER,))
        event=dm()
        await authorized(event)
        self.assertEqual(len(jobs),1)
        service.command.assert_called_once_with('scan')
        event.reply.assert_awaited_once_with('ok',parse_mode=None,link_preview=False)
        # Ordinary private and group messages have no side effects or storage.
        for event in (dm(raw_text='ordinary DM'),dm(raw_text='ordinary group',is_private=False,is_group=True),
                      dm(sender_id=99,chat_id=99)):
            await authorized(event)
            event.reply.assert_not_awaited()
        self.assertEqual(len(jobs),1)

    async def test_configured_builder_sees_dm_unverified_owner_starts_no_worker(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=replace(settings(directory),telegram_session=str(Path(directory)/'nailhub.session'));builders=[];handlers=[]
            client=SimpleNamespace(add_event_handler=lambda h,b:(handlers.append(h),builders.append(b)),
                connect=AsyncMock(),get_me=AsyncMock(return_value=SimpleNamespace(bot=False,id=42)),
                disconnect=AsyncMock(),run_until_disconnected=AsyncMock(),remove_event_handler=Mock())
            fake=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
            with patch.dict('sys.modules',{'telethon':fake_telethon(lambda **k:k)}),\
                 patch('nailhub.telegram.make_client',return_value=client),\
                 patch('nailhub.telegram.Service',return_value=fake):
                with self.assertRaises(ConfigError):
                    await telegram.run(cfg)
                await handlers[0](dm())
            self.assertEqual(builders,[{}]);fake.worker.assert_not_called();fake.command.assert_not_called()
            self.assertFalse(hasattr(fake,'notice_send'))
            fake.scheduler.assert_not_called()
            client.disconnect.assert_awaited_once()

    async def test_verified_lifecycle_binds_notices_to_the_one_existing_client(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=replace(settings(directory,owner_ids=(OWNER,),owner_username='TeamOwner'),telegram_session=str(Path(directory)/'nailhub.session'))
            user=SimpleNamespace(id=OWNER,bot=False,username='TeamOwner')
            client=SimpleNamespace(add_event_handler=Mock(),connect=AsyncMock(),
                get_me=AsyncMock(return_value=SimpleNamespace(bot=False,id=42)),
                get_entity=AsyncMock(return_value=user),send_message=AsyncMock(return_value=SimpleNamespace(id=5678)),
                disconnect=AsyncMock(),run_until_disconnected=AsyncMock(),remove_event_handler=Mock())
            fake=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
            with patch.dict('sys.modules',{'telethon':fake_telethon(lambda **k:k)}),\
                 patch('nailhub.telegram.make_client',return_value=client) as make_client,\
                 patch('nailhub.telegram.Service',return_value=fake):
                with self.assertRaises(RuntimeError):
                    await telegram.run(cfg)
                make_client.assert_called_once();client.connect.assert_awaited_once()
                self.assertEqual(fake.notice_owner_id,OWNER)
                result=await fake.notice_send('manual notice')
                self.assertEqual(result.id,5678)
                client.send_message.assert_awaited_once_with(OWNER,'manual notice',parse_mode=None,link_preview=False)
                fake.worker.assert_awaited_once();fake.scheduler.assert_awaited_once()
