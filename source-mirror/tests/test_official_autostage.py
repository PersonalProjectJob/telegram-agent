import os
import sys
import tempfile
import io
import time
import signal
import unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import nullcontext,redirect_stderr
from datetime import datetime
from zoneinfo import ZoneInfo
from source_mirror.official_autostage import once,watchdog,execute_child,validate_settings

class AutostageTests(unittest.TestCase):
    def setUp(self):
        comparator=patch('source_mirror.official_autostage.differs_from_held',return_value=False)
        comparator.start();self.addCleanup(comparator.stop)
    def test_source_categories_are_explicit_and_complete(self):
        first='irs-tip-reporting';second='sba-business-plan'
        value={'version':'official-autostage-v1','enabled':True,'source_ids':[first,second],'categories':{first:'Tax',second:'Business'}}
        self.assertEqual(validate_settings(value)['categories'][first],'Tax')
        for bad in (dict(value,categories={first:'Tax'}),dict(value,categories={first:'Tax',second:''}),
                    {'version':value['version'],'enabled':True,'source_ids':[first,second],'category':'Safety'}):
            with self.subTest(value=bad),self.assertRaises(ValueError):validate_settings(bad)
        old={'version':value['version'],'enabled':True,'source_id':first,'category':'Tax'}
        self.assertEqual(validate_settings(old)['categories'],{first:'Tax'})
    def test_disabled_by_default_without_io(self):
        with patch('source_mirror.official_autostage.policy') as policy:
            with self.assertRaisesRegex(ValueError,'disabled'):once()
            with self.assertRaisesRegex(ValueError,'disabled'):execute_child()
            policy.assert_not_called()
    def test_nonzero_child_fails_closed(self):
        if os.name!='posix':self.skipTest('POSIX process groups required')
        with self.assertRaisesRegex(ValueError,'held'):watchdog([sys.executable,'-c','raise SystemExit(2)'],5)
    @unittest.skipUnless(os.name=='posix','POSIX process groups required')
    def test_diagnostic_preserves_only_fixed_stage_and_exception_class(self):
        output=io.StringIO();command="import sys; print('official stage held: BlockingIOError',file=sys.stderr); print('secret-value',file=sys.stderr); sys.exit(2)"
        with redirect_stderr(output),self.assertRaisesRegex(ValueError,'held'):watchdog([sys.executable,'-c',command],5)
        self.assertIn('official stage held: BlockingIOError',output.getvalue());self.assertNotIn('secret-value',output.getvalue())
    @unittest.skipUnless(os.name=='posix','POSIX process groups required')
    def test_detached_descendant_holding_stderr_cannot_hang_watchdog(self):
        with tempfile.TemporaryDirectory() as root:
            marker=Path(root)/'pid'
            code="import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],start_new_session=True); open(sys.argv[1],'w').write(str(p.pid));sys.exit(2)"
            started=time.monotonic()
            try:
                with self.assertRaisesRegex(ValueError,'held'):watchdog([sys.executable,'-c',code,str(marker)],2)
                self.assertLess(time.monotonic()-started,2)
            finally:
                if marker.exists():
                    try:os.kill(int(marker.read_text()),signal.SIGKILL)
                    except ProcessLookupError:pass
    def test_one_fixed_source_collects_generates_and_stages(self):
        from source_mirror.official_sources import SOURCES
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Safety'}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();snapshot=root/'official-review-queue'/item['id']/('a'*64)
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings), \
                 patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()),patch('source_mirror.official_autostage.require'), \
                 patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',return_value={'snapshot':str(snapshot)}) as collect, \
                 patch('source_mirror.official_autostage.load_runtime_policy',return_value={'socket_path':'/fake'}), \
                 patch('source_mirror.official_autostage.create',return_value={'sha256':'b'*64}) as create, \
                 patch('source_mirror.official_autostage.stage_draft',return_value={'status':'STAGED_NOT_PUBLISHABLE'}) as stage:
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                self.assertEqual(execute_child()['status'],'STAGED_NOT_PUBLISHABLE')
                collect.assert_called_once_with(root,item);create.assert_called_once();stage.assert_called_once_with(root,item['id'],'a'*64,'b'*64,'Safety','08:00',lock_held=True)
                self.assertEqual(execute_child()['status'],'NO_ELIGIBLE_SOURCES')
                self.assertEqual(create.call_count,1);self.assertEqual(stage.call_count,1)
    def test_quality_hold_never_regenerates_on_next_tick(self):
        from source_mirror.official_sources import SOURCES
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Safety'}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();snapshot=root/'official-review-queue'/item['id']/('a'*64)
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
                 patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',return_value={'snapshot':str(snapshot)}), \
                 patch('source_mirror.official_autostage.load_runtime_policy',return_value={'socket_path':'/fake'}), \
                 patch('source_mirror.official_autostage.create',side_effect=ValueError('uncertainty')) as create, \
                 patch('source_mirror.official_autostage.stage_draft') as stage:
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                with self.assertRaisesRegex(ValueError,'uncertainty'):execute_child()
                self.assertEqual(execute_child()['status'],'SOURCE_UNCHANGED_CONTENT_HOLD')
                self.assertEqual(create.call_count,1);stage.assert_not_called()
    def test_connect_failure_and_timeout_never_retried(self):
        from source_mirror.official_sources import SOURCES
        from source_mirror.official_auto_eligibility import BrokerNotConnected
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Safety'}}
        for failure,expected_calls in ((BrokerNotConnected('connect'),1),(TimeoutError('unknown outcome'),1)):
          with self.subTest(failure=type(failure).__name__),tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();snapshot=root/'official-review-queue'/item['id']/('a'*64)
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
                 patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',return_value={'snapshot':str(snapshot)}), \
                 patch('source_mirror.official_autostage.load_runtime_policy',return_value={'socket_path':'/fake'}), \
                 patch('source_mirror.official_autostage.create',side_effect=failure) as create:
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                with self.assertRaises((BrokerNotConnected,TimeoutError)):execute_child()
                for _ in range(2):self.assertEqual(execute_child()['status'],'SOURCE_UNCHANGED_CONTENT_HOLD')
                self.assertEqual(create.call_count,expected_calls)
    def test_published_source_stops_before_generation(self):
        from source_mirror.official_sources import SOURCES
        from source_mirror.store import Store,identity
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Safety'}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();snapshot=root/'official-review-queue'/item['id']/('a'*64)
            store=Store(root)
            store.db.execute("INSERT INTO articles VALUES (?,?,?,NULL,'published',NULL,?,?)",(identity(item['url']),item['url'],'title','2026-10-30T00:00:00Z','a'*64));store.close()
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
                 patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',return_value={'snapshot':str(snapshot)}) as collect, \
                 patch('source_mirror.official_autostage.create') as create,patch('source_mirror.official_autostage.stage_draft') as stage:
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                self.assertEqual(execute_child(),{'status':'NO_ELIGIBLE_SOURCES'})
                collect.assert_not_called();create.assert_not_called();stage.assert_not_called()
    def test_second_broker_connect_failure_does_not_repeat_first_call(self):
        from source_mirror.official_sources import SOURCES
        from source_mirror.official_auto_eligibility import BrokerNotConnected
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Safety'}}
        def create(*args,**kwargs):
            infer=kwargs['inference'];infer('first prompt');infer('second prompt')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();snapshot=root/'official-review-queue'/item['id']/('a'*64)
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
                 patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',return_value={'snapshot':str(snapshot)}), \
                 patch('source_mirror.official_autostage.load_runtime_policy',return_value={'socket_path':'/fake'}), \
                 patch('source_mirror.official_autostage.create',side_effect=create), \
                 patch('source_mirror.official_autostage.trusted_broker_call',side_effect=[{'status':'SUCCESS'},BrokerNotConnected('refused')]) as broker:
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                with self.assertRaises(BrokerNotConnected):execute_child()
                self.assertEqual(execute_child()['status'],'SOURCE_UNCHANGED_CONTENT_HOLD')
                self.assertEqual(broker.call_count,2)
    def test_held_refetch_only_then_new_snapshot_runs_next_tick(self):
        from source_mirror.official_sources import SOURCES
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Tax'}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();base=root/'official-review-queue'/item['id']
            captures=[{'snapshot':str(base/('a'*64))},{'snapshot':str(base/('b'*64))},{'snapshot':str(base/('b'*64))}]
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
                 patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',side_effect=captures), \
                 patch('source_mirror.official_autostage.load_runtime_policy',return_value={'socket_path':'/fake'}), \
                 patch('source_mirror.official_autostage.create',side_effect=[ValueError('uncertainty'),{'sha256':'c'*64}]) as create, \
                 patch('source_mirror.official_autostage.stage_draft',return_value={'status':'STAGED_NOT_PUBLISHABLE'}) as stage, \
                 patch('source_mirror.official_autostage.differs_from_held',return_value=True), \
                 patch('source_mirror.official_autostage.HELD_CONTENT_CHANGE_VERIFIED',True):
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                with self.assertRaisesRegex(ValueError,'uncertainty'):execute_child()
                self.assertEqual(execute_child()['status'],'SOURCE_PROBED_NOT_PUBLISHABLE');self.assertEqual(create.call_count,1)
                self.assertEqual(execute_child()['status'],'STAGED_NOT_PUBLISHABLE');self.assertEqual(create.call_count,2)
                stage.assert_called_once_with(root,item['id'],'b'*64,'c'*64,'Tax','08:00',lock_held=True)
    def test_analytics_changes_never_retry_held_generation(self):
        from source_mirror.official_sources import SOURCES,collect
        from source_mirror.official_source_meaning import differs_from_held
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Tax'}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve()
            source=b'<html><head><title>Tips</title><script>v1</script></head><body><main><p>Tip records.</p></main></body></html>'
            first=collect(root,item,lambda _:source);second=collect(root,item,lambda _:source.replace(b'v1',b'v2'))
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
                 patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',side_effect=[first,second]), \
                 patch('source_mirror.official_autostage.differs_from_held',wraps=differs_from_held), \
                 patch('source_mirror.official_autostage.load_runtime_policy',return_value={'socket_path':'/fake'}), \
                 patch('source_mirror.official_autostage.create',side_effect=ValueError('uncertainty')) as create:
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                with self.assertRaisesRegex(ValueError,'uncertainty'):execute_child()
                self.assertEqual(execute_child()['status'],'SOURCE_UNCHANGED_CONTENT_HOLD')
                self.assertEqual(create.call_count,1)
    def test_changed_content_remains_held_until_comparator_verified(self):
        from source_mirror.official_sources import SOURCES
        item=next(x for x in SOURCES if x['id']=='irs-tip-reporting')
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[item['id']],'categories':{item['id']:'Tax'}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory).resolve();base=root/'official-review-queue'/item['id']
            captures=[{'snapshot':str(base/('a'*64))},{'snapshot':str(base/('b'*64))}]
            with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',root), \
                 patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
                 patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime') as clock, \
                 patch('source_mirror.official_autostage.collect',side_effect=captures),patch('source_mirror.official_autostage.differs_from_held',return_value=True), \
                 patch('source_mirror.official_autostage.load_runtime_policy',return_value={'socket_path':'/fake'}), \
                 patch('source_mirror.official_autostage.create',side_effect=ValueError('uncertainty')) as create:
                clock.now.return_value=datetime(2026,10,30,5,0,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
                with self.assertRaises(ValueError):execute_child()
                self.assertEqual(execute_child()['status'],'SOURCE_CONTENT_CHANGE_HELD')
                self.assertEqual(create.call_count,1)
    def test_timeout_kills_process_group_and_releases_lock(self):
        if os.name!='posix':self.skipTest('POSIX process groups required')
        import fcntl
        with tempfile.TemporaryDirectory() as directory:
            lock=Path(directory)/'lock';marker=Path(directory)/'ready'
            child=('import fcntl,sys,time; f=open(sys.argv[1],"w"); '
                   'fcntl.flock(f,fcntl.LOCK_EX); open(sys.argv[2],"w").write("ready"); time.sleep(30)')
            with self.assertRaises(TimeoutError):watchdog([sys.executable,'-c',child,str(lock),str(marker)],2)
            self.assertTrue(marker.is_file())
            with lock.open('r+') as handle:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
