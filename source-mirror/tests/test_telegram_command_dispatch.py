import subprocess
import os
import sys
import tempfile
from pathlib import Path
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from source_mirror.store import ZONE
from source_mirror.telegram_command_dispatch import budget_seconds, dispatch


class DispatchTests(unittest.TestCase):
    def test_cutoff_and_next_day(self):
        slots=['08:00','12:00','16:00','20:00']
        self.assertEqual(budget_seconds(slots,datetime(2026,10,30,15,57,50,tzinfo=ZONE)),10)
        self.assertEqual(budget_seconds(slots,datetime(2026,10,30,15,59,30,tzinfo=ZONE)),0)
        self.assertEqual(budget_seconds(slots,datetime(2026,10,30,20,30,tzinfo=ZONE)),900)

    def test_timeout_kills_group_and_does_not_run_near_slot(self):
        slots=['08:00','12:00','16:00','20:00']
        with patch('source_mirror.telegram_command_dispatch.os.name','posix'), \
             patch('source_mirror.telegram_command_dispatch.os.killpg',create=True) as kill, \
             patch('source_mirror.telegram_command_dispatch.signal.SIGKILL',9,create=True), \
             patch('source_mirror.telegram_command_dispatch.subprocess.Popen') as popen:
            self.assertEqual(dispatch('/config',slots,now=datetime(2026,10,30,15,59,30,tzinfo=ZONE)),'too_close_to_slot')
            self.assertEqual(dispatch('/config',slots,now=datetime(2026,10,30,15,50,tzinfo=ZONE)),'too_close_to_slot')
            popen.assert_not_called()
            proc=Mock(pid=123,returncode=None);proc.wait.side_effect=[subprocess.TimeoutExpired('x',300),0];popen.return_value=proc
            self.assertEqual(dispatch('/config',slots,now=datetime(2026,10,30,15,0,tzinfo=ZONE)),'timeout_unknown')
            self.assertEqual(kill.call_args.args[0],123)
            self.assertTrue(popen.call_args.kwargs['start_new_session'])

    @unittest.skipUnless(os.name=='posix', 'POSIX process group required')
    def test_real_sleeping_process_is_killed_on_timeout(self):
        real_popen=subprocess.Popen
        processes=[]
        def start(*args,**kwargs):
            proc=real_popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
            processes.append(proc)
            class Wrapper:
                pid=proc.pid
                @property
                def returncode(self): return proc.returncode
                def wait(self,timeout): return proc.wait(timeout=min(timeout,0.2) if proc.poll() is None else timeout)
            return Wrapper()
        with patch('source_mirror.telegram_command_dispatch.subprocess.Popen',side_effect=start):
            result=dispatch('/unused',['08:00','12:00','16:00','20:00'],now=datetime(2026,10,30,15,0,tzinfo=ZONE))
        self.assertEqual(result,'timeout_unknown')
        self.assertIsNotNone(processes[0].poll())

    @unittest.skipUnless(os.name=='posix', 'POSIX process group required')
    def test_killed_lock_holder_recovers_processing_as_unknown(self):
        from source_mirror.store import Store
        from source_mirror.lock import worker_lock
        from source_mirror.telegram_command_backend import execute
        from source_mirror.telegram_queue_worker import process_one
        with tempfile.TemporaryDirectory() as root:
            store=Store(root)
            try:
                execute(store,42,1,'/source_mirror resume')
                execute(store,42,2,'nailblog https://www.scratchmagazine.co.uk/nail_art/example/')
                aid='a'*64
                store.db.execute("INSERT INTO articles VALUES (?,?,?,NULL,'ready',NULL,?,?)",(aid,'https://www.scratchmagazine.co.uk/nail_art/example/','title','time','hash'))
                script=('import sys,time; from source_mirror.store import Store; from source_mirror.lock import worker_lock; '
                        's=Store(sys.argv[1]); lock=worker_lock(s.root); lock.__enter__(); '
                        's.db.execute("UPDATE telegram_crawl_queue SET state=\'processing\',article_id=?",("a"*64,)); '
                        's.db.execute("INSERT INTO attempts(article_id,reserved_day,state,created_at) VALUES (?,\'2026-10-30\',\'intent\',\'time\')",("a"*64,)); '
                        'print("ready",flush=True); time.sleep(30)')
                child=subprocess.Popen([sys.executable,'-c',script,root],stdout=subprocess.PIPE,start_new_session=True,text=True)
                try:
                    self.assertEqual(child.stdout.readline().strip(),'ready')
                    os.killpg(child.pid,9);child.wait(timeout=5)
                    with worker_lock(store.root):
                        self.assertIsNone(process_one({},store,lambda *_:self.fail('must not retry')))
                    self.assertEqual(store.db.execute('SELECT state FROM telegram_crawl_queue').fetchone()[0],'unknown')
                    self.assertEqual(store.db.execute('SELECT state FROM attempts').fetchone()[0],'intent')
                    self.assertEqual(store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],1)
                finally:
                    if child.poll() is None: os.killpg(child.pid,9);child.wait(timeout=5)
                    child.stdout.close()
            finally: store.close()
