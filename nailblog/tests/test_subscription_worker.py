"""Offline adversarial worker tests; these never invoke agy or certify runtime."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
import stat
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from nailblog.images import ImageError
from nailblog.images.agy_native import native_call
from nailblog.images.subscription import Worker, invoke, atomic, read, POLICY, POLICY_SHA, AGENT_NAME
from nailblog.images.subscription_io import Directory
from tests.test_subscription import config, request, native_record, invocation_record
from tests.test_real_images import fixture_image


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.c = config(self.temp.name)
        self.private = Path(self.c.subscription.private_dir)
        self.worker = Worker(self.c.subscription, clock=lambda:10)

    def job(self, **kwargs):
        req = request(self.c.subscription, **kwargs)
        path = Path(self.c.subscription.queue_dir) / req['job_id']; path.mkdir(mode=0o700)
        atomic(path/'request.json', req)
        return req, Directory(path), self.private/(req['job_id']+'.json')

    def test_journal_precedes_runner_and_failure_is_retained_for_restart(self):
        req, job, marker = self.job()
        def fail(*_):
            self.assertEqual(json.loads(read(marker))['status'], 'dispatched')
            raise ImageError('cli_timeout_unknown')
        self.worker.generate = fail
        with job:
            result = self.worker.process_job(req['job_id'], job, self.private, marker)
            self.assertEqual(result['status'], 'unknown')
            self.assertEqual(result['error'], 'cli_timeout_unknown')
            self.assertEqual(json.loads(job.read('receipt.json')), result)
        self.assertEqual(json.loads(read(marker))['status'], 'dispatched')
        # Restart scanner recognises marker without interpreting a stale receipt.
        self.assertTrue(marker.exists())

    def test_restart_after_timeout_or_crash_scanner_never_dispatches_again(self):
        for error in (ImageError('cli_timeout_unknown'),KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                req,job,marker=self.job(prompt=type(error).__name__)
                job.close()
                self.worker.generate=Mock(side_effect=error)
                with Directory(self.c.subscription.queue_dir) as queue:
                    if isinstance(error,KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt): self.worker.scan(queue,self.private)
                    else:
                        self.assertEqual(self.worker.scan(queue,self.private)['status'],'unknown')
                    restarted=Worker(self.c.subscription,clock=lambda:10)
                    restarted.generate=Mock(side_effect=AssertionError('no retry after restart'))
                    self.assertIsNone(restarted.scan(queue,self.private))
                    restarted.generate.assert_not_called()
                self.worker.generate.assert_called_once()
                self.assertEqual(json.loads(read(marker))['status'],'dispatched')

    def test_failed_jobs_count_for_run_and_daily_caps_and_rejected_jobs_do_not_dispatch(self):
        for kind in ('run', 'daily'):
            with self.subTest(kind=kind):
                for old in self.private.glob('*.json'): old.unlink()
                count = self.c.subscription.max_images_per_run if kind == 'run' else self.c.subscription.max_daily_jobs
                for i in range(count):
                    atomic(self.private/(str(i)+'.json'), {'day':'1970-01-01', 'run_id':'run' if kind=='run' else 'other', 'status':'dispatched'})
                req, job, marker = self.job(prompt='cap '+kind)
                self.worker.generate = Mock(side_effect=AssertionError('no generation'))
                with job:
                    result = self.worker.process_job(req['job_id'], job, self.private, marker)
                self.assertEqual(result['status'], 'rejected')
                self.assertEqual(result['error'], 'cli_run_image_cap' if kind=='run' else 'cli_daily_job_cap')
                self.worker.generate.assert_not_called()

    def test_malformed_expired_cancelled_requests_terminal_without_runner(self):
        for kind in ('malformed', 'expired', 'cancelled'):
            with self.subTest(kind=kind):
                req, job, marker = self.job(prompt=kind)
                with job:
                    if kind == 'malformed': job.atomic('request.json', {'surprise':True})
                    if kind == 'expired': req['expires_at']=0; job.atomic('request.json',req)
                    if kind == 'cancelled': job.atomic('cancelled.json',{})
                    self.worker.generate = Mock()
                    result = self.worker.process_job(req['job_id'], job, self.private, marker)
                    self.assertEqual(result['status'], 'rejected')
                    self.assertEqual(json.loads(job.read('receipt.json')), result)
                    self.worker.generate.assert_not_called()
                self.assertTrue(marker.exists())

    def test_valid_job_after_malformed_job_is_still_processed(self):
        req, job, marker = self.job(prompt='malformed')
        with job:
            job.atomic('request.json', [])
            self.assertEqual(self.worker.process_job(req['job_id'],job,self.private,marker)['status'],'rejected')
        req, job, marker = self.job(prompt='valid')
        self.worker.generate = Mock(return_value={'proof':'offline'})
        with job:
            self.assertEqual(self.worker.process_job(req['job_id'],job,self.private,marker)['status'],'complete')
        self.worker.generate.assert_called_once()


@unittest.skipUnless(os.name == 'posix', 'POSIX descriptor/process/uid contract')
class PosixWorkerTests(unittest.TestCase):
    def test_atomic_queue_files_keep_narrow_group_read_under_producer_umask0077(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);root.chmod(0o2770)
            previous=os.umask(0o077)
            try:
                with Directory(root) as directory:
                    directory.atomic('request.json',{'protocol':'cross-user-request'})
                    directory.atomic('source.image',b'raw-image-bytes',raw=True)
                    for name in ('request.json','source.image'):
                        info=(root/name).stat()
                        self.assertEqual(stat.S_IMODE(info.st_mode),0o640)
                        self.assertEqual(info.st_gid,root.stat().st_gid)
                    self.assertEqual(json.loads(directory.read('request.json')),{'protocol':'cross-user-request'})
                    self.assertEqual(directory.read('source.image'),b'raw-image-bytes')
                    self.assertEqual(sorted(directory.names()),['request.json','source.image'])
                active=os.umask(0o077)
                self.assertEqual(active,0o077)  # atomic() did not weaken process umask
            finally:os.umask(previous)

    def test_directory_swap_cannot_redirect_worker_writes_or_reads(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); root.chmod(0o700)
            queue=root/'queue'; queue.mkdir(mode=0o700)
            outside=root/'outside'; outside.mkdir(mode=0o700)
            (queue/'job').mkdir(mode=0o700)
            atomic(queue/'job'/'request.json', {'original':True})
            with Directory(queue) as q, Directory('job', parent=q) as job:
                (queue/'job').rename(queue/'moved')
                (queue/'job').symlink_to(outside, target_is_directory=True)
                job.atomic('receipt.json', {'safe':True})
                self.assertEqual(json.loads(job.read('request.json')), {'original':True})
                self.assertEqual(list(outside.iterdir()), [])
                self.assertTrue((queue/'moved'/'receipt.json').exists())
            with self.assertRaises(ImageError): read(queue/'job'/'receipt.json')

    def test_read_validates_open_fd_after_leaf_swap_and_rejects_fifo_without_blocking(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); root.chmod(0o700)
            path=root/'x'; path.write_bytes(b'original'); path.chmod(0o600)
            real_open=os.open
            def swap(name, flags, *args, **kwargs):
                fd=real_open(name,flags,*args,**kwargs)
                if name=='x': path.chmod(0o666)
                return fd
            with Directory(root) as directory, patch('os.open', side_effect=swap):
                with self.assertRaises(ImageError): directory.read('x')
            os.mkfifo(root/'fifo',0o600)
            with Directory(root) as directory:
                with self.assertRaises(ImageError): directory.read('fifo')

    def test_process_output_flood_and_timeout_are_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            start=time.monotonic()
            with self.assertRaisesRegex(ImageError, 'output_limit_unknown'):
                invoke([sys.executable,'-c',"import os;\nwhile True: os.write(2,b'x'*65536)"],cwd=temp,timeout=5)
            self.assertLess(time.monotonic()-start,5)
            with self.assertRaisesRegex(ImageError,'timeout_unknown'):
                invoke([sys.executable,'-c','import time; time.sleep(5)'],cwd=temp,timeout=.1)

    def setup_worker(self, temp):
        c=config(temp); home=Path(temp)/'home'; home.mkdir(mode=0o700)
        private=Path(c.subscription.private_dir)
        worker=Worker(c.subscription, home=home, clock=lambda:10)
        native=home/'snap/antigravity-cli/common/.gemini/antigravity-cli'
        (native/'conversations').mkdir(parents=True,mode=0o700)
        # Parent creation honours the host umask (THINKPAD shell may use0002).
        # The native root contract is not group writable, just like the unit.
        native.chmod(0o700)
        (native/'brain').mkdir(mode=0o700)
        policy=home/'snap/antigravity-cli/common/.gemini/config/agents'
        policy.mkdir(parents=True,mode=0o700); atomic(policy/(AGENT_NAME+'.md'),POLICY,raw=True)
        req=request(c.subscription)
        path=Path(c.subscription.queue_dir)/req['job_id']; path.mkdir(mode=0o700);atomic(path/'request.json',req)
        return worker,req,Directory(path),private,native

    def test_expiry_or_cancel_during_model_lock_never_calls_image_runner(self):
        for kind in ('expiry','cancel','request_changed','global_policy_changed'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                worker,req,job,private,native=self.setup_worker(temp)
                calls=[]
                def runner(args,**_):
                    calls.append(args)
                    return b'1.2.11' if '--version' in args else (AGENT_NAME+'\n').encode()
                worker.runner=runner
                @contextmanager
                def delayed(**kwargs):
                    self.assertIsNotNone(kwargs['stop'])
                    if kind=='expiry': worker.clock=lambda:2000
                    elif kind=='cancel': job.atomic('cancelled.json',{})
                    elif kind=='global_policy_changed':
                        policy=worker.home/'snap/antigravity-cli/common/.gemini/config/agents'/(AGENT_NAME+'.md')
                        policy.write_bytes(POLICY.replace(b'generate_image',b'run_command___'))
                    else: job.atomic('request.json',{**req,'prompt':'changed'})
                    yield
                with job, patch('nailblog.images.subscription.MODEL_LOCK.enter',side_effect=delayed):
                    result=worker.process_job(req['job_id'],job,private,private/(req['job_id']+'.json'))
                self.assertEqual(result['status'],'unknown')
                if kind=='global_policy_changed': self.assertEqual(result['error'],'cli_tool_policy_changed')
                self.assertFalse(any('--print' in args for args in calls))

    def test_worker_verifies_fresh_native_pair_and_rejects_other_tools_or_sqlite_errors(self):
        for kind in ('valid','valid_final','extra_tool','tool_after_result','final_before_result','duplicate_final','permission','error','bad_status','reused','no_pair','denied'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temp:
                worker,req,job,private,native=self.setup_worker(temp)
                session='f6ae7cf1-c230-4bb1-a2e1-c1753523ffbb'
                if kind=='reused': (native/'conversations'/(session+'.db')).write_bytes(b'existing')
                @contextmanager
                def lock(**_): yield
                def runner(args,**_):
                    if '--version' in args: return b'1.2.11'
                    if args[-1]=='agent': return (AGENT_NAME+'\n').encode()
                    self.assertEqual(args[args.index('--agent')+1],AGENT_NAME)
                    self.assertNotIn('--mode',args)
                    self.assertEqual(json.loads(read(private/(req['job_id']+'.json')))['status'],'dispatched')
                    source=native/'brain'/session/'native.png'; source.parent.mkdir(mode=0o700)
                    source.write_bytes(fixture_image(size=(1376,768))); source.chmod(0o600)
                    dbpath=native/'conversations'/(session+'.db')
                    if dbpath.exists(): dbpath.unlink()
                    with sqlite3.connect(dbpath) as db:
                        db.execute('CREATE TABLE steps(idx INTEGER,step_type INTEGER,status INTEGER,step_format INTEGER,step_payload BLOB,permissions BLOB,error_details BLOB)')
                        db.execute('INSERT INTO steps VALUES(0,14,3,0,?,NULL,NULL)',(b'',))
                        db.execute('INSERT INTO steps VALUES(1,15,3,0,?,NULL,NULL)',(invocation_record(req),))
                        if kind!='no_pair':
                            db.execute('INSERT INTO steps VALUES(2,132,?,0,?,?,?)',(
                                7 if kind=='bad_status' else 3,native_record(req,session,source.as_uri()),
                                b'allow' if kind=='permission' else None,b'error' if kind=='error' else None))
                        if kind=='extra_tool': db.execute('INSERT INTO steps VALUES(3,15,3,0,?,NULL,NULL)',(invocation_record(req,tool=b'write_file'),))
                        if kind=='tool_after_result': db.execute('INSERT INTO steps VALUES(3,15,3,0,?,NULL,NULL)',(invocation_record(req,tool=b'write_file'),))
                        if kind in ('valid_final','final_before_result','duplicate_final'):
                            final=(Path(__file__).parent/'fixtures/subscription/agy-1.2.11-worker-step-3.bin').read_bytes()
                            db.execute('INSERT INTO steps VALUES(3,15,3,0,?,NULL,NULL)',(final,))
                            if kind=='final_before_result':db.execute('UPDATE steps SET step_payload=? WHERE idx=1',(final,))
                            if kind=='duplicate_final':db.execute('INSERT INTO steps VALUES(4,15,3,0,?,NULL,NULL)',(final,))
                    dbpath.chmod(0o600)
                    return json.dumps({'status':'SUCCESS','conversation_id':session,'denied_actions':['command'] if kind=='denied' else []}).encode()
                worker.runner=runner
                with job,patch('nailblog.images.subscription.MODEL_LOCK.enter',side_effect=lock):
                    result=worker.process_job(req['job_id'],job,private,private/(req['job_id']+'.json'))
                self.assertEqual(result['status'],'complete' if kind in ('valid','valid_final') else 'unknown', result)
                if kind in ('valid','valid_final'):
                    self.assertEqual(result['policy_sha'],POLICY_SHA)
                    self.assertEqual(result['native_columns'],{'permissions':None,'error_details':None})
                    self.assertEqual(result['conversation_snapshot']['method'],'private-stable-db-wal-v1')
                    if kind=='valid_final':self.assertIsNotNone(result['final_text_sha'])

    def test_agent_policy_tamper_and_empty_listing_never_mean_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            worker,req,job,private,native=self.setup_worker(temp)
            with job:
                worker.runner=Mock(return_value=b'')
                with self.assertRaisesRegex(ImageError,'not_recognised'): worker.verify_agent(private)
                policy=worker.home/'snap/antigravity-cli/common/.gemini/config/agents'/(AGENT_NAME+'.md')
                policy.write_bytes(POLICY.replace(b'generate_image',b'write_file'))
                with self.assertRaisesRegex(ImageError,'policy_changed'): worker.verify_agent(private)


class PolicyTests(unittest.TestCase):
    def test_worker_entry_loads_only_minimal_subscription_table(self):
        from nailblog import subscription_worker
        raw=b'[subscription]\nqueue_dir="/var/lib/nailblog-subscription/queue"\nprivate_dir="/home/startechai/snap/antigravity-cli/common/nailblog-worker"\n'
        with patch.object(subscription_worker,'read',return_value=raw) as reader, patch.object(subscription_worker,'Worker') as worker:
            self.assertEqual(subscription_worker.main(['--config','/etc/nailblog/subscription-worker.toml','--once']),0)
            worker.return_value.run_once.assert_called_once()
            cfg=worker.call_args.args[0]
            self.assertEqual(cfg.worker_user,'startechai')
            self.assertFalse(hasattr(cfg,'admin'))
            reader.assert_called_once_with(Path('/etc/nailblog/subscription-worker.toml'),owner=0,native=True)

    def test_packaged_deploy_agent_has_exact_pinned_permission_boundary(self):
        path=Path(__file__).parents[1]/'deploy/nailblog-image-only.md'
        self.assertEqual(path.read_bytes(),POLICY)
        self.assertEqual(hashlib.sha256(POLICY).hexdigest(),POLICY_SHA)

    def test_actual_invocation_fixture_and_nonimage_calls_fail_closed(self):
        # Exact real payload copied independently, never rewritten to fit parser.
        raw=(Path(__file__).parent/'fixtures/subscription/agy-1.2.11-generate-image-call.bin').read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'f1e56175b872a4a3c2938b6e27bec6d49bfa5ae8a3725e918b91e2505bd6049d')
        prompt='Photorealistic editorial cover image showing elegant autumn nail art, clean salon background, no text, no logos, no watermark'
        self.assertEqual(native_call(raw,prompt=prompt,image_name='cover'),'call_1748796')
        with self.assertRaises(ImageError): native_call(raw.replace(b'generate_image',b'write_file____'),prompt=prompt,image_name='cover')
