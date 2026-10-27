"""Actual POSIX flock/marker contract; no provider calls or runtime /run writes."""
import os
import multiprocessing
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from nailblog import model_lock as shared
from nailblog.subscription_lock import SubscriptionModelLock, SUBSCRIPTION_MODEL_LOCK, live_foreground_readonly


class AdapterTests(unittest.TestCase):
    def test_worker_alias_is_separate_and_canonical_module_is_not_monkeypatched(self):
        from nailblog.images import subscription
        self.assertIs(subscription.MODEL_LOCK,SUBSCRIPTION_MODEL_LOCK)
        self.assertIsNot(shared.MODEL_LOCK,SUBSCRIPTION_MODEL_LOCK)
        self.assertIs(type(shared.MODEL_LOCK),shared.ModelLock)
        self.assertIs(SubscriptionModelLock.enter,shared.ModelLock.enter)
        self.assertEqual(shared.live_foreground.__module__,'nailblog.model_lock')


@unittest.skipUnless(os.name=='posix','POSIX actual shared flock/readonly runtime directory')
class PosixAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.root.chmod(0o700)
        self.file=self.root/shared.LOCK_FILE;self.file.write_bytes(b'\0');self.file.chmod(0o660)
        self.lock=SubscriptionModelLock(self.root,poll_seconds=.01)

    def waiter(self,lock=None):
        stop=threading.Event();entered=threading.Event();release=threading.Event();errors=[]
        def run():
            try:
                with (lock or self.lock).enter(background=True,stop=stop):
                    entered.set();release.wait(2)
            except BaseException as error: errors.append(error)
        thread=threading.Thread(target=run);thread.start()
        def cleanup():
            stop.set();release.set();thread.join(2)
        self.addCleanup(cleanup)
        return stop,entered,release,errors,thread

    def assert_resources_released(self):
        self.assertTrue(shared._PROCESS_LOCK.acquire(blocking=False))
        shared._PROCESS_LOCK.release()
        fd=shared._open_lock(self.root)
        try:
            self.assertTrue(shared._lock(fd,blocking=False))
            shared._unlock(fd)
        finally:os.close(fd)

    def test_stale_marker_retained_in_nonwritable_directory_and_same_inode_acquired(self):
        child=subprocess.Popen([sys.executable,'-c','pass']);child.wait(timeout=5)
        stale=self.root/(shared.FOREGROUND_PREFIX+str(child.pid)+'.stale');stale.touch()
        malformed=self.root/(shared.FOREGROUND_PREFIX+'999999999999999999999999.bad');malformed.touch()
        inode=self.file.stat().st_ino
        self.root.chmod(0o555)
        try:
            with patch.object(Path,'unlink',side_effect=AssertionError('worker must never delete markers')):
                with self.lock.enter(background=True):
                    self.assertEqual(self.file.stat().st_ino,inode)
            self.assertTrue(stale.exists());self.assertTrue(malformed.exists())
        finally:self.root.chmod(0o700)
        self.assert_resources_released()

    def test_live_foreground_marker_cannot_be_overtaken(self):
        marker=self.root/(shared.FOREGROUND_PREFIX+str(os.getpid())+'.live');marker.touch()
        stop,entered,release,errors,thread=self.waiter()
        self.assertFalse(entered.wait(.15))
        self.assertTrue(marker.exists())
        # Trusted foreground owner removes its own marker; worker never does.
        marker.unlink()
        self.assertTrue(entered.wait(1));release.set();thread.join(1)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assert_resources_released()

    def test_actual_cross_process_flock_exclusion_on_existing_shared_inode(self):
        code='import fcntl,sys; f=open(sys.argv[1],"r+b"); fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.readline(); fcntl.flock(f,fcntl.LOCK_UN)'
        child=subprocess.Popen([sys.executable,'-c',code,str(self.file)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
        def cleanup_child():
            if child.poll() is None:child.kill()
            child.wait(timeout=5)
            child.stdin.close();child.stdout.close()
        self.addCleanup(cleanup_child)
        self.assertEqual(child.stdout.readline().strip(),'locked')
        stop,entered,release,errors,thread=self.waiter()
        self.assertFalse(entered.wait(.15))
        child.stdin.write('release\n');child.stdin.flush();child.wait(timeout=5)
        self.assertTrue(entered.wait(1));release.set();thread.join(1)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
        self.assert_resources_released()

    def test_cancellation_while_foreground_live_is_bounded_and_retains_marker(self):
        marker=self.root/(shared.FOREGROUND_PREFIX+str(os.getpid())+'.live');marker.touch()
        stop,entered,release,errors,thread=self.waiter()
        self.assertFalse(entered.wait(.05));stop.set();thread.join(.5)
        self.assertFalse(thread.is_alive());self.assertFalse(entered.is_set())
        self.assertEqual(len(errors),1);self.assertIsInstance(errors[0],shared.LockStopped)
        self.assertTrue(marker.exists());self.assert_resources_released()

    def test_priority_recheck_after_os_lock_releases_resources_before_waiting(self):
        marker=self.root/(shared.FOREGROUND_PREFIX+str(os.getpid())+'.race')
        original=shared._lock;created=threading.Event()
        def announce_after_lock(fd,**kwargs):
            result=original(fd,**kwargs)
            if result and not created.is_set():marker.touch();created.set()
            return result
        with patch.object(shared,'_lock',side_effect=announce_after_lock):
            stop,entered,release,errors,thread=self.waiter()
            self.assertTrue(created.wait(1));self.assertFalse(entered.wait(.1))
            # Scanner must release both OS/thread locks while yielding priority.
            self.assert_resources_released()
            marker.unlink();self.assertTrue(entered.wait(1));release.set();thread.join(1)
            self.assertEqual(errors,[])

    def test_stop_immediately_after_os_acquire_releases_all_resources(self):
        stop=threading.Event();original=shared._lock
        def cancelled_after_lock(fd,**kwargs):
            result=original(fd,**kwargs);stop.set();return result
        with patch.object(shared,'_lock',side_effect=cancelled_after_lock):
            with self.assertRaises(shared.LockStopped):
                with self.lock.enter(background=True,stop=stop):self.fail('must not enter')
        self.assert_resources_released()

    def test_after_fork_adapter_uses_fresh_canonical_process_mutex(self):
        shared._PROCESS_LOCK.acquire()
        def child_run():
            stop=threading.Event();timer=threading.Timer(.5,stop.set);timer.start()
            try:
                with self.lock.enter(background=True,stop=stop):pass
            finally:timer.cancel()
        child=multiprocessing.get_context('fork').Process(target=child_run)
        try:
            child.start();child.join(2)
            self.assertFalse(child.is_alive())
            self.assertEqual(child.exitcode,0)
        finally:
            shared._PROCESS_LOCK.release()
            if child.is_alive():child.kill();child.join(2)
        self.assert_resources_released()

    def test_background_only_and_scan_io_errors_keep_canonical_error_contract(self):
        with self.assertRaisesRegex(ValueError,'background only'):
            with self.lock.enter():self.fail('must not enter')
        missing=SubscriptionModelLock(self.root/'missing')
        with self.assertRaises(shared.LockUnavailable):
            with missing.enter(background=True):self.fail('must not enter')
        with patch('nailblog.subscription_lock.MAX_DIRECTORY_ENTRIES',0):
            with self.assertRaises(shared.LockUnavailable):
                with self.lock.enter(background=True):self.fail('inspection must be bounded')
        self.assert_resources_released()
