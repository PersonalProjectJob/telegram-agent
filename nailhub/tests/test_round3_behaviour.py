"""Real handler, real OS singleton and transient-state regressions; no network."""
import asyncio
import io
import logging
import multiprocessing
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tests.transport_fakes import fake_telethon, FakeFloodWaitError, FakeClock

from nailhub import __main__ as cli, telegram
from nailhub._errors import ConfigError
from nailhub._runtime import AlreadyRunning, ProcessLock
from nailhub.browser import posting_from_data
from nailhub.config import load_config
from nailhub.model import NailHubModel
from nailhub.model_lock import ModelLock, LockStopped, LockUnavailable, FOREGROUND_PREFIX
from nailhub.service import Service
from tests.nailhub_fixtures import OWNER, dm, raw_post, settings

ROOT=Path(__file__).resolve().parents[2]


def try_session(directory,results):
    try:
        with ProcessLock(directory):results.put('acquired')
    except AlreadyRunning:results.put('blocked')


class TransientTests(unittest.IsolatedAsyncioTestCase):
    async def test_stop_while_waiting_keeps_analyzing_and_recovery_requeues(self):
        with tempfile.TemporaryDirectory() as directory:
            post=posting_from_data(raw_post());cfg=settings(directory)
            lock=ModelLock(directory);stop=threading.Event();model=NailHubModel(cfg,opener=Mock(side_effect=AssertionError('HTTP must not start')))
            model.stop=stop;service=Service(cfg,model=model)
            service.state.observe(post.id,post.fingerprint,post.snapshot())
            marker=Path(directory)/(FOREGROUND_PREFIX+str(os.getpid())+'.test');marker.touch()
            timer=threading.Timer(0.03,stop.set);timer.start()
            try:
                with patch('nailhub.model.MODEL_LOCK',lock):
                    with self.assertRaises(LockStopped):await service.process(SimpleNamespace(verify_listing_age=AsyncMock()),post)
            finally:timer.cancel();marker.unlink()
            self.assertEqual(service.state.get(post.id)['state'],'analyzing')
            model.opener.assert_not_called()
            service.state.recover_queue()
            self.assertEqual(service.state.get(post.id)['state'],'queued')

    async def test_unusable_lock_directory_preserves_state_and_logs_error(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory);post=posting_from_data(raw_post());bad=Path(directory)/'not-directory';bad.write_text('file')
            model=NailHubModel(cfg,opener=Mock());service=Service(cfg,model=model)
            service.state.observe(post.id,post.fingerprint,post.snapshot())
            with patch('nailhub.model.MODEL_LOCK',ModelLock(bad)),self.assertLogs('nailhub.service',level='ERROR') as logs:
                with self.assertRaises(LockUnavailable):await service.process(SimpleNamespace(verify_listing_age=AsyncMock()),post)
            self.assertEqual(service.state.get(post.id)['state'],'analyzing')
            self.assertTrue(any('model_lock' in record.getMessage() for record in logs.records))
            model.opener.assert_not_called()


    async def test_worker_requeues_lock_unavailable_in_process_then_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory);post=posting_from_data(raw_post());service=Service(cfg,model=NailHubModel(cfg,opener=Mock()))
            stop=asyncio.Event();service.state.observe(post.id,post.fingerprint,post.snapshot());service.state.enqueue()
            bad=Path(directory)/'not-directory';bad.write_text('file')
            calls=[]
            async def scan():
                calls.append(service.state.get(post.id)['state'])
                if len(calls)==1:
                    await service.process(SimpleNamespace(verify_listing_age=AsyncMock()),post)
                stop.set()
                return 'retry reached'
            clock=FakeClock()
            with patch('nailhub.service.wait_for_stop',side_effect=clock.wait),patch('nailhub.model.MODEL_LOCK',ModelLock(bad)),patch.object(service,'scan',side_effect=scan),patch.object(service,'notify_manual',new=AsyncMock()),self.assertLogs('nailhub.service',level='ERROR') as logs:
                await asyncio.wait_for(service.worker(stop),3)
            self.assertEqual(calls,['queued','queued']);self.assertEqual(clock.delays,[30,1])
            self.assertEqual(service.state.get(post.id)['state'],'queued')
            self.assertEqual(service.state.status()['running'],0)
            self.assertEqual(service.state.status()['last_result'],'retry reached')
            self.assertTrue(any('model_lock' in r.getMessage() and 'requeued' in r.getMessage() for r in logs.records))
            self.assertNotIn('PRIVATE',' '.join(logs.output));service.model.opener.assert_not_called()

class StartupTests(unittest.IsolatedAsyncioTestCase):
    def make_config(self,directory):
        return replace(settings(directory,owner_ids=(OWNER,)),telegram_session=str(Path(directory)/'session/nailhub.session'))

    async def test_registered_handler_verified_owner_and_ignored_variants(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=self.make_config(directory);disconnected=asyncio.Event();registered=asyncio.Event()
            user=SimpleNamespace(id=OWNER,bot=False,username='SotaThao')
            handlers=[]
            def add(handler,builder):handlers.append(handler);registered.set()
            client=SimpleNamespace(add_event_handler=Mock(side_effect=add),remove_event_handler=Mock(),connect=AsyncMock(),
                get_me=AsyncMock(return_value=SimpleNamespace(id=42,bot=False)),get_entity=AsyncMock(return_value=user),
                run_until_disconnected=AsyncMock(side_effect=disconnected.wait),disconnect=AsyncMock(),send_message=AsyncMock())
            async def work(stop):await stop.wait()
            fake=SimpleNamespace(worker=AsyncMock(side_effect=work),scheduler=AsyncMock(side_effect=work),command=Mock(return_value='ok'))
            with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),\
                 patch('nailhub.telegram.make_client',return_value=client),patch('nailhub.telegram.Service',return_value=fake),\
                 patch('nailhub.telegram.make_handler',wraps=telegram.make_handler) as factory,\
                 self.assertLogs('nailhub.telegram',level='INFO') as logs:
                task=asyncio.create_task(telegram.run(cfg))
                try:
                    await asyncio.wait_for(registered.wait(),1)
                    for _ in range(30):
                        if hasattr(fake,'notice_owner_id'):break
                        await asyncio.sleep(0.01)
                    incoming=dm(raw_text='/nailhub status')
                    await handlers[0](incoming)
                    fake.command.assert_called_once_with('status')
                    incoming.reply.assert_awaited_once_with('ok',parse_mode=None,link_preview=False)
                    for rejected in (dm(sender_id=99,chat_id=99),dm(is_group=True,is_private=False),dm(fwd_from=object()),dm(raw_text='ordinary')):
                        await handlers[0](rejected);rejected.reply.assert_not_awaited()
                    fake.command.assert_called_once_with('status')
                    self.assertEqual(len(handlers),1);factory.assert_called_once()
                    await asyncio.sleep(0.01)
                finally:
                    disconnected.set()
                    with self.assertRaises(RuntimeError):await task
            self.assertIn('NailHub ready owner_verified=1',[r.getMessage() for r in logs.records])

    async def test_owner_empty_or_exception_is_error_and_cli_exit_one(self):
        for result in ((),OSError('private exception')):
            with self.subTest(result=type(result).__name__),tempfile.TemporaryDirectory() as directory:
                cfg=self.make_config(directory)
                client=SimpleNamespace(add_event_handler=Mock(),remove_event_handler=Mock(),connect=AsyncMock(),
                    get_me=AsyncMock(return_value=SimpleNamespace(id=42,bot=False)),disconnect=AsyncMock())
                fake=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
                verify=AsyncMock(side_effect=result) if isinstance(result,Exception) else AsyncMock(return_value=result)
                with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),\
                     patch('nailhub.telegram.make_client',return_value=client),patch('nailhub.telegram.Service',return_value=fake),\
                     patch('nailhub.telegram.verify_owner',verify),self.assertLogs('nailhub.telegram',level='ERROR') as logs:
                    with self.assertRaises(RuntimeError):await telegram.run(cfg)
                self.assertTrue(any('Owner verification failed' in r.getMessage() for r in logs.records))
                fake.worker.assert_not_called();fake.scheduler.assert_not_called();fake.command.assert_not_called()
                client.disconnect.assert_awaited_once()
        # main is synchronous; run it in a thread so it owns its asyncio.run loop.
        with tempfile.TemporaryDirectory() as directory:
            cfg=self.make_config(directory)
            with patch('nailhub.__main__.load_config',return_value=cfg),patch('nailhub.__main__.execute',AsyncMock(side_effect=RuntimeError('unverified'))),redirect_stdout(io.StringIO()):
                self.assertEqual(await asyncio.to_thread(cli.main,['run']),1)

    async def test_deterministic_startup_failures_exit_two_without_workers(self):
        for kind in ('empty_owner_ids','not_logged_in','bot_session'):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as directory:
                cfg=self.make_config(directory)
                if kind=='empty_owner_ids':cfg=replace(cfg,owner_ids=())
                me=None if kind=='not_logged_in' else SimpleNamespace(id=42,bot=kind=='bot_session')
                client=SimpleNamespace(add_event_handler=Mock(),remove_event_handler=Mock(),connect=AsyncMock(),get_me=AsyncMock(return_value=me),disconnect=AsyncMock())
                fake=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
                with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),patch('nailhub.telegram.make_client',return_value=client),patch('nailhub.telegram.Service',return_value=fake),patch('nailhub.__main__.load_config',return_value=cfg),patch('nailhub.telegram.install_signal_handlers',return_value=lambda:None),self.assertLogs('nailhub.telegram',level='ERROR') as logs,redirect_stdout(io.StringIO()):
                    self.assertEqual(await asyncio.to_thread(cli.main,['run']),2)
                fake.worker.assert_not_called();fake.scheduler.assert_not_called()
                client.disconnect.assert_awaited_once();client.remove_event_handler.assert_called_once()
                self.assertTrue(any('operator action required' in r.getMessage() for r in logs.records))
                if kind=='empty_owner_ids':client.connect.assert_not_awaited()

    async def test_transient_connect_and_verification_failures_exit_one(self):
        for phase,error in (('connect',OSError('PRIVATE')),('connect',FakeFloodWaitError(capture=60)),('verify',RuntimeError('PRIVATE'))):
            with self.subTest(phase=phase,error=type(error).__name__),tempfile.TemporaryDirectory() as directory:
                cfg=self.make_config(directory)
                client=SimpleNamespace(add_event_handler=Mock(),remove_event_handler=Mock(),connect=AsyncMock(side_effect=error if phase=='connect' else None),get_me=AsyncMock(return_value=SimpleNamespace(id=42,bot=False)),disconnect=AsyncMock())
                fake=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
                with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),patch('nailhub.telegram.make_client',return_value=client),patch('nailhub.telegram.Service',return_value=fake),patch('nailhub.telegram.verify_owner',new=AsyncMock(side_effect=error)),patch('nailhub.__main__.load_config',return_value=cfg),patch('nailhub.telegram.install_signal_handlers',return_value=lambda:None),self.assertLogs('nailhub.telegram',level='ERROR') as logs,redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(await asyncio.to_thread(cli.main,['run']),1)
                fake.worker.assert_not_called();fake.scheduler.assert_not_called();client.disconnect.assert_awaited_once()
                self.assertNotIn('PRIVATE',output.getvalue()+' '.join(logs.output))

    async def test_lock_taken_before_client_creation_for_run_and_login(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=self.make_config(directory);order=[]
            class Lock:
                def __init__(self,path):self.path=path
                def __enter__(self):order.append('lock');return self
                def __exit__(self,*args):order.append('unlock')
            def make(config):
                self.assertEqual(order[-1],'lock');order.append('client');raise RuntimeError('stop before transport')
            for operation in (telegram.run,telegram.login_telegram):
                order.clear()
                with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),patch('nailhub.telegram.ProcessLock',Lock),patch('nailhub.telegram.make_client',side_effect=make):
                    with self.assertRaises(RuntimeError):await operation(cfg)
                self.assertEqual(order,['lock','client','unlock'])


class ConfigAndSingletonTests(unittest.TestCase):
    def test_run_configures_logging_like_listener(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=replace(settings(directory),telegram_session=str(Path(directory)/'nailhub.session'))
            with patch('nailhub.__main__.load_config',return_value=cfg),patch('nailhub.__main__.execute',AsyncMock()),patch('nailhub.__main__.logging.basicConfig') as configure:
                self.assertEqual(cli.main(['run']),0)
            configure.assert_called_once_with(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')

    def test_telegram_non_table_is_config_error_and_exit_two(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml'
            for value in ('[]','1','"text"'):
                path.write_text('telegram='+value+'\n[worker]\ndb_path='+__import__('json').dumps(str(Path(directory)/'jobs.db'))+'\nprofile_path='+__import__('json').dumps(str(Path(directory)/'profile'))+'\n')
                with self.assertRaises(ConfigError):load_config(path)
                with redirect_stdout(io.StringIO()):self.assertEqual(cli.main(['run','--config',str(path)]),2)

    def test_two_real_processes_cannot_own_the_same_session_directory(self):
        ctx=multiprocessing.get_context('spawn')
        with tempfile.TemporaryDirectory() as directory:
            results=ctx.Queue()
            with ProcessLock(directory):
                p=ctx.Process(target=try_session,args=(directory,results));p.start()
                self.assertEqual(results.get(timeout=5),'blocked');p.join(5);self.assertEqual(p.exitcode,0);p.close()
            p=ctx.Process(target=try_session,args=(directory,results));p.start()
            self.assertEqual(results.get(timeout=5),'acquired');p.join(5);self.assertEqual(p.exitcode,0);p.close()
            self.assertTrue((Path(directory)/'nailhub.lock').exists());results.close();results.join_thread()

    def test_unit_backoff_and_mixed_kill_mode(self):
        unit=(ROOT/'systemd/tg-nailhub.service').read_text(encoding='utf-8')
        for line in ('RestartSec=30','StartLimitIntervalSec=0','StartLimitBurst=5','RestartPreventExitStatus=2','KillMode=mixed'):
            self.assertIn(line,unit)
