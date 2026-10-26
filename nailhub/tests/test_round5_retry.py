"""Stop-aware retry pacing with real worker/verification/state APIs and a fake clock."""
import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from nailhub import service as workers, telegram
from nailhub.browser import posting_from_data
from nailhub.model_lock import LockUnavailable
from tests.nailhub_fixtures import OWNER, FakeBrowser, extracted, raw_post, settings
from tests.transport_fakes import FakeClock, FakeFloodWaitError, fake_telethon


class WorkerPacingTests(unittest.IsolatedAsyncioTestCase):
    async def test_exponential_lock_backoff_caps_at_interval_and_resets_after_model(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory,interval_seconds=300);post=posting_from_data(raw_post())
            second=posting_from_data(raw_post(id='22222222-2222-4222-8222-222222222222'))
            browser=FakeBrowser(post)
            model=SimpleNamespace(extract=Mock(side_effect=[*[LockUnavailable('PRIVATE') for _ in range(5)],extracted(post.content),LockUnavailable('PRIVATE')]))
            service=workers.Service(cfg,model=model,browser_factory=lambda config:browser)
            stop=asyncio.Event();service.state.enqueue();attempts=[]
            def after_wait(event,seconds):
                if len(clock.delays)==6:browser.post=second
                if len(clock.delays)==7:event.set()
                else:service.state.enqueue()
            clock=FakeClock(after_wait);real_scan=service.scan
            async def scan():
                attempts.append(clock.now)
                return await real_scan()
            with patch.object(service,'scan',side_effect=scan),patch.object(service,'notify_manual',new=AsyncMock()),patch('nailhub.service.wait_for_stop',side_effect=clock.wait),self.assertLogs('nailhub.service',level='ERROR'):
                await service.worker(stop)
            self.assertEqual(clock.delays,[30,60,120,240,300,1,30])
            self.assertEqual(attempts,[0,30,90,210,450,750,751])
            self.assertEqual(model.extract.call_count,7)
            self.assertEqual(service.state.get(post.id)['state'],'preview')
            self.assertEqual(service.state.get(second.id)['state'],'queued')
            self.assertEqual(service.state.status()['pending'],1)
            self.assertEqual(service.state.status()['running'],0)
            self.assertEqual(service._model_progress,1)

    async def test_failure_before_model_does_not_reset_lock_backoff(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory,interval_seconds=600);post=posting_from_data(raw_post());browser=FakeBrowser(post)
            browser.ready=AsyncMock(side_effect=[None,None,OSError('PRIVATE'),None])
            model=SimpleNamespace(extract=Mock(side_effect=LockUnavailable('PRIVATE')))
            service=workers.Service(cfg,model=model,browser_factory=lambda config:browser)
            stop=asyncio.Event();service.state.enqueue();attempts=[]
            def after_wait(event,seconds):
                if len(clock.delays)==4:event.set()
                else:service.state.enqueue()
            clock=FakeClock(after_wait);real_scan=service.scan
            async def scan():
                attempts.append(clock.now)
                return await real_scan()
            with patch.object(service,'scan',side_effect=scan),patch.object(service,'notify_manual',new=AsyncMock()),patch('nailhub.service.wait_for_stop',side_effect=clock.wait),self.assertLogs('nailhub.service',level='ERROR'):
                await service.worker(stop)
            self.assertEqual(clock.delays,[30,60,1,120])
            self.assertEqual(attempts,[0,30,90,91]);self.assertEqual(model.extract.call_count,3)
            self.assertEqual(service._model_progress,0)

    async def test_stop_interrupts_real_backoff_without_another_scan(self):
        with tempfile.TemporaryDirectory() as directory:
            post=posting_from_data(raw_post());browser=FakeBrowser(post)
            model=SimpleNamespace(extract=Mock(side_effect=LockUnavailable('PRIVATE')))
            service=workers.Service(settings(directory),model=model,browser_factory=lambda config:browser)
            stop=asyncio.Event();waiting=asyncio.Event();requests=[];service.state.enqueue()
            real_wait=workers.wait_for_stop
            async def wait(event,seconds):
                requests.append(seconds);waiting.set()
                return await real_wait(event,seconds)
            with patch('nailhub.service.wait_for_stop',side_effect=wait),patch.object(service,'notify_manual',new=AsyncMock()),self.assertLogs('nailhub.service',level='ERROR'):
                task=asyncio.create_task(service.worker(stop))
                try:
                    await asyncio.wait_for(waiting.wait(),1)
                    stop.set();await asyncio.wait_for(task,1)
                finally:
                    stop.set();task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(requests,[30]);self.assertEqual(model.extract.call_count,1)
            self.assertEqual(service.state.get(post.id)['state'],'queued')
            self.assertEqual(service.state.status()['pending'],1)


class FloodWaitTests(unittest.IsolatedAsyncioTestCase):
    def config(self,directory):
        return replace(settings(directory,owner_ids=(OWNER,),owner_username='TeamOwner'),telegram_session=str(Path(directory)/'nailhub.session'))

    def client(self,entities):
        return SimpleNamespace(add_event_handler=Mock(),remove_event_handler=Mock(),connect=AsyncMock(),
            get_me=AsyncMock(return_value=SimpleNamespace(id=42,bot=False)),get_entity=AsyncMock(side_effect=entities),
            disconnect=AsyncMock(),run_until_disconnected=AsyncMock(side_effect=asyncio.Event().wait),send_message=AsyncMock())

    async def test_real_owner_verification_floodwait_retries_seconds_capped_at_fifteen_minutes(self):
        for seconds in ((60,120),(3601,)):
            with self.subTest(seconds=seconds),tempfile.TemporaryDirectory() as directory:
                owner=SimpleNamespace(id=OWNER,bot=False,username='TeamOwner')
                client=self.client([*[FakeFloodWaitError(capture=s) for s in seconds],owner,owner])
                started=asyncio.Event();stops=[]
                async def work(stop):started.set();await stop.wait()
                service=SimpleNamespace(worker=AsyncMock(side_effect=work),scheduler=AsyncMock(side_effect=work),command=Mock())
                def capture_stop(stop):stops.append(stop);return lambda:None
                def check_wait(stop,delay):
                    service.worker.assert_not_called();service.scheduler.assert_not_called()
                    self.assertFalse(hasattr(service,'notice_owner_id'))
                clock=FakeClock(check_wait)
                with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),patch('nailhub.telegram.make_client',return_value=client),patch('nailhub.telegram.Service',return_value=service),patch('nailhub.telegram.install_signal_handlers',side_effect=capture_stop),patch('nailhub.telegram.wait_for_stop',side_effect=clock.wait),self.assertLogs('nailhub.telegram',level='INFO') as logs:
                    task=asyncio.create_task(telegram.run(self.config(directory)))
                    try:
                        await asyncio.wait_for(started.wait(),1)
                        stops[0].set();await asyncio.wait_for(task,1)
                    finally:
                        if stops:stops[0].set()
                        task.cancel();await asyncio.gather(task,return_exceptions=True)
                self.assertEqual(clock.delays,[min(s,900) for s in seconds])
                self.assertEqual(client.get_entity.await_count,len(seconds)+2)
                self.assertEqual([call.args[0] for call in client.get_entity.await_args_list],['TeamOwner']*(len(seconds)+1)+[OWNER])
                client.connect.assert_awaited_once();client.get_me.assert_awaited_once();client.disconnect.assert_awaited_once()
                self.assertEqual(service.notice_owner_id,OWNER)
                service.worker.assert_awaited_once();service.scheduler.assert_awaited_once()
                self.assertIn('NailHub ready owner_verified=1',[r.getMessage() for r in logs.records])
                self.assertFalse(any(r.levelname=='ERROR' for r in logs.records))

    async def test_shutdown_interrupts_floodwait_and_keeps_owner_unverified(self):
        with tempfile.TemporaryDirectory() as directory:
            client=self.client([FakeFloodWaitError(capture=3601)]);stops=[];waiting=asyncio.Event();requests=[]
            service=SimpleNamespace(worker=AsyncMock(),scheduler=AsyncMock(),command=Mock())
            def capture_stop(stop):stops.append(stop);return lambda:None
            real_wait=workers.wait_for_stop
            async def wait(stop,seconds):
                requests.append(seconds);waiting.set();return await real_wait(stop,seconds)
            with patch.dict('sys.modules',{'telethon':fake_telethon(lambda:object())}),patch('nailhub.telegram.make_client',return_value=client),patch('nailhub.telegram.Service',return_value=service),patch('nailhub.telegram.install_signal_handlers',side_effect=capture_stop),patch('nailhub.telegram.wait_for_stop',side_effect=wait),self.assertLogs('nailhub.telegram',level='WARNING'):
                task=asyncio.create_task(telegram.run(self.config(directory)))
                try:
                    await asyncio.wait_for(waiting.wait(),1)
                    stops[0].set();await asyncio.wait_for(task,1)
                finally:
                    if stops:stops[0].set()
                    task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(requests,[900]);client.get_entity.assert_awaited_once_with('TeamOwner')
            service.worker.assert_not_called();service.scheduler.assert_not_called()
            self.assertFalse(hasattr(service,'notice_owner_id'));client.disconnect.assert_awaited_once()
