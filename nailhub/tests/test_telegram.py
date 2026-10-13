"""Fakes mirror Telethon constructor/connect/start/get_me/send/disconnect calls."""
import asyncio
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.transport_fakes import fake_telethon

from nailhub import telegram
from nailhub._errors import ConfigError
from nailhub.config import load_config
from tests.nailhub_fixtures import settings


class TelegramTests(unittest.IsolatedAsyncioTestCase):
    async def test_login_callbacks_prompt_owner_and_disconnect(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=replace(settings(directory),telegram_session=str(Path(directory)/'nailhub.session'))
            calls=[]
            async def start(**callbacks):
                for name in ('phone','code_callback','password'):
                    calls.append(callbacks[name]())
            client=SimpleNamespace(start=AsyncMock(side_effect=start),disconnect=AsyncMock())
            with patch('nailhub.telegram.make_client',return_value=client),patch('nailhub.telegram.getpass.getpass',side_effect=['fake phone','fake code','fake 2fa']) as prompt:
                await telegram.login_telegram(cfg)
            self.assertEqual(calls,['fake phone','fake code','fake 2fa'])
            self.assertEqual(prompt.call_count,3);client.disconnect.assert_awaited_once()

    async def test_startup_failure_disconnects_and_cleans_handler(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=replace(settings(directory),telegram_session=str(Path(directory)/'nailhub.session'))
            client=SimpleNamespace(connect=AsyncMock(),get_me=AsyncMock(return_value=None),
                add_event_handler=Mock(),remove_event_handler=Mock(),disconnect=AsyncMock())
            with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),patch('nailhub.telegram.make_client',return_value=client):
                with self.assertRaises(ConfigError):await telegram.run(cfg)
            client.disconnect.assert_awaited_once();client.remove_event_handler.assert_called_once()

    async def test_shutdown_waits_worker_then_disconnects(self):
        client=SimpleNamespace(disconnect=AsyncMock());done=[]
        async def work():
            await asyncio.sleep(0.01);done.append(True)
        await telegram.graceful_shutdown(client,asyncio.create_task(work()))
        self.assertEqual(done,[True]);client.disconnect.assert_awaited_once()

    async def test_shutdown_deadline_forces_exit_after_disconnect(self):
        client=SimpleNamespace(disconnect=AsyncMock());task=asyncio.create_task(asyncio.Event().wait());force=Mock()
        with self.assertRaises(SystemExit):await telegram.graceful_shutdown(client,task,timeout=0.001,force_exit=force)
        client.disconnect.assert_awaited_once();force.assert_called_once_with(1)
        task.cancel();await asyncio.gather(task,return_exceptions=True)


class ClientConfigTests(unittest.TestCase):
    def test_constructor_uses_service_session_and_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=replace(settings(directory),telegram_session=str(Path(directory)/'nailhub.session'))
            calls=[]
            def factory(session,api_id,api_hash,*,connection_retries,auto_reconnect,catch_up,sequential_updates,base_logger):
                calls.append((session,api_id,api_hash,connection_retries,auto_reconnect,catch_up,sequential_updates));return object()
            with patch.dict('sys.modules',{'telethon':SimpleNamespace(TelegramClient=factory)}),patch.dict(os.environ,{'TG_API_ID':'123','TG_API_HASH':'FAKE_HASH'}):
                telegram.make_client(cfg)
            self.assertEqual(calls,[(cfg.telegram_session,123,'FAKE_HASH',-1,True,True,True)])

    def test_session_default_and_config_override(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'config.toml';base='[worker]\ndb_path="'+str(Path(directory)/'jobs.db').replace('\\','/')+'"\nprofile_path="'+str(Path(directory)/'profile').replace('\\','/')+'"\n'
            p.write_text(base,encoding='utf-8')
            self.assertEqual(load_config(p).telegram_session,'/var/lib/agentfleet/nailhub/nailhub.session')
            session=(Path(directory)/'other.session').as_posix()
            p.write_text(base+'[telegram]\nsession="'+session+'"\n',encoding='utf-8')
            self.assertEqual(load_config(p).telegram_session,session)
            p.write_text(base+'[telegram]\nsession="relative"\n',encoding='utf-8')
            with self.assertRaises(ConfigError):load_config(p)
