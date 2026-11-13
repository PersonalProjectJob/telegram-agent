"""Production state bootstrap tests; no production filesystem or network access."""
import os
import shutil
import sqlite3
import asyncio
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from nailblog.production_state import DATABASE_NAME, MARKER_NAME, ensure_production_state
from nailblog.state import State, StateError, state_for_config
from tests.helpers import settings


class EntrypointTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_production_state_starts_paused_and_disarmed_without_daily_jobs(self):
        from nailblog.service import Service
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live')
            with patch('nailblog.production_state.ensure_production_state'):
                service = Service(config)
            self.assertEqual((service.state.control()['paused'], service.state.armed_binding()), (1, ''))
            service.notice_send = lambda value: None
            with patch('nailblog.service.daily.run_due') as daily:
                service._start_jobs(None)
                daily.assert_not_called()
                self.assertIsNone(service._jobs)

    async def test_unpaused_production_daily_waits_for_loaded_binding(self):
        from nailblog.service import Service
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live')
            with patch('nailblog.production_state.ensure_production_state'):
                service = Service(config)
            service.notice_send = lambda value: None
            service.state.set_paused(False)
            with patch('nailblog.service.daily.run_due', new_callable=AsyncMock) as daily:
                service._start_jobs(None)
                self.assertIsNone(service._jobs)
                daily.assert_not_called()
                service.state.arm('wrong-binding')
                service._start_jobs(None)
                self.assertIsNone(service._jobs)
                service.state.arm(service.startup_binding)
                service._start_jobs(None)
                await service._jobs
                daily.assert_awaited_once()

    async def test_existing_control_is_not_overwritten_when_reopening(self):
        from nailblog.state import state_for_config
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live')
            existing = State(config.db_path)
            existing.arm('existing-binding')
            with patch('nailblog.production_state.ensure_production_state'):
                loaded = state_for_config(config)
            self.assertEqual((loaded.control()['paused'], loaded.armed_binding()), (0, 'existing-binding'))

    async def test_staging_state_initialization_is_unchanged(self):
        from nailblog.config import ContractConfig
        from nailblog.state import state_for_config
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live', contract=ContractConfig(environment='staging'))
            state = state_for_config(config)
            self.assertEqual((state.control()['paused'], state.armed_binding()), (0, ''))

    async def test_all_state_entrypoints_fail_before_constructing_state(self):
        from nailblog.__main__ import execute
        from nailblog.images.pipeline import ImagePipeline
        from nailblog.service import Service
        from nailblog.state import startup_gate
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live')
            self.assertEqual(config.contract.environment, 'production')
            with patch('nailblog.production_state.ensure_production_state', side_effect=StateError('blocked')) as guard, \
                    patch('nailblog.state.State') as constructor:
                for operation in (lambda: Service(config), lambda: startup_gate(config), lambda: ImagePipeline(config)):
                    with self.assertRaisesRegex(StateError, 'blocked'):
                        operation()
                with self.assertRaisesRegex(StateError, 'blocked'):
                    await execute('pause-install', config)
                self.assertEqual(guard.call_count, 4)
                constructor.assert_not_called()

    async def test_production_dry_run_refuses_before_opening_or_copying_database(self):
        from nailblog.__main__ import _autoflow_requeue, scratch
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live')
            with patch('nailblog.__main__.sqlite3.connect') as connect:
                with self.assertRaisesRegex(StateError, 'production_dry_run_unsupported'):
                    with scratch(config):
                        pass
                connect.assert_not_called()
            with patch('nailblog.service.Service') as service:
                with self.assertRaisesRegex(StateError, 'production_dry_run_unsupported'):
                    _autoflow_requeue(config, SimpleNamespace(dry_run=True), SimpleNamespace(now=None))
                service.assert_not_called()

    async def test_staging_image_pipeline_keeps_original_fixed_state_mode(self):
        from nailblog.config import ContractConfig
        from nailblog.images.pipeline import ImagePipeline
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory, mode='live', contract=ContractConfig(environment='staging'))
            config = replace(config, schedule=replace(config.schedule, image_mode='adaptive'))
            with patch('nailblog.state.State') as constructor:
                ImagePipeline(config, provider=object())
                constructor.assert_called_once_with(config.db_path, image_mode='fixed')


@unittest.skipUnless(os.name == 'posix', 'production state guard requires POSIX')
class ProductionStateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'prod'
        self.root.mkdir(mode=0o700)
        self.db = self.root / DATABASE_NAME

    def check(self):
        ensure_production_state(self.db, root=self.root)

    def test_new_state_and_restart_keep_same_binding(self):
        self.check()
        State(self.db)
        before = (self.root / MARKER_NAME).read_bytes()
        self.check()
        self.assertEqual((self.root / MARKER_NAME).read_bytes(), before)

    def test_real_config_bootstrap_restart_and_active_wal(self):
        config = replace(settings(self.temp.name, mode='live'), db_path=str(self.db))
        with patch('nailblog.production_state.PRODUCTION_ROOT', self.root):
            state = state_for_config(config)
            self.assertEqual((state.control()['paused'], state.armed_binding()), (1, ''))
            state.set_paused(False)
            state.arm('binding')
            db = sqlite3.connect(self.db)
            try:
                db.execute('PRAGMA wal_autocheckpoint=0')
                db.execute("INSERT INTO tiktok_tags(added_at, tag, views) VALUES(1, 'test', 1)")
                db.commit()
                self.assertTrue((self.root / (DATABASE_NAME + '-wal')).exists())
                reopened = state_for_config(config)
                self.assertEqual((reopened.control()['paused'], reopened.armed_binding()), (0, 'binding'))
                self.assertEqual(db.execute('SELECT count(*) FROM tiktok_tags').fetchone()[0], 1)
            finally:
                db.close()

    def test_real_config_refuses_interrupted_bootstrap_before_state_schema(self):
        config = replace(settings(self.temp.name, mode='live'), db_path=str(self.db))
        original = os.open
        def fail_marker(path, flags, *args, **kwargs):
            if path == MARKER_NAME:
                raise OSError('simulated marker failure')
            return original(path, flags, *args, **kwargs)
        with patch('nailblog.production_state.PRODUCTION_ROOT', self.root), \
                patch('nailblog.production_state.os.open', side_effect=fail_marker):
            with self.assertRaises(StateError):
                state_for_config(config)
        with sqlite3.connect(self.db) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sqlite_master WHERE name='control'").fetchone()[0], 0)
        with patch('nailblog.production_state.PRODUCTION_ROOT', self.root):
            with self.assertRaises(StateError):
                state_for_config(config)

    def test_symlink_runs_rejected_before_database_or_artifact_writes(self):
        from nailblog.config import AutoflowConfig
        from nailblog.onepost import OnePost
        from nailblog.publication import AdminError
        other = Path(self.temp.name) / 'staging-runs'
        other.mkdir(mode=0o700)
        (self.root / 'runs').symlink_to(other, target_is_directory=True)
        config = replace(settings(self.temp.name, mode='live'), db_path=str(self.db),
                         autoflow=AutoflowConfig(enabled=True, runs_dir=str(self.root / 'runs')))
        with patch('nailblog.production_state.PRODUCTION_ROOT', self.root):
            with self.assertRaises(StateError):
                state_for_config(config)
            self.assertFalse(self.db.exists())
            workflow = object.__new__(OnePost)
            workflow.config = config
            with patch.object(OnePost, 'active'):
                with self.assertRaisesRegex(AdminError, 'production_runs_invalid'):
                    asyncio.run(workflow.prepare('run-1', 1, self.root / 'runs' / 'run-1'))
        self.assertEqual(list(other.iterdir()), [])
        self.assertFalse(self.db.exists())

    def test_direct_prepare_rejects_staging_runs_even_when_autoflow_disabled(self):
        from nailblog.config import AutoflowConfig
        from nailblog.onepost import OnePost
        from nailblog.publication import AdminError
        other = Path(self.temp.name) / 'staging-runs'
        other.mkdir(mode=0o700)
        config = replace(settings(self.temp.name, mode='live'), db_path=str(self.db),
                         autoflow=AutoflowConfig(enabled=False, runs_dir=str(other)))
        workflow = object.__new__(OnePost)
        workflow.config = config
        with patch('nailblog.production_state.PRODUCTION_ROOT', self.root), patch.object(OnePost, 'active'):
            with self.assertRaisesRegex(AdminError, 'production_runs_invalid'):
                asyncio.run(workflow.prepare('run-1', 1, other / 'run-1'))
        self.assertFalse(self.db.exists())
        self.assertEqual(list(other.iterdir()), [])

    def test_copied_staging_db_refused_before_any_sqlite_open(self):
        staging = Path(self.temp.name) / 'staging.sqlite3'
        staging.write_bytes(b'not a production state')
        shutil.copyfile(staging, self.db)
        self.db.chmod(0o600)
        before = self.db.read_bytes()
        with self.assertRaises(StateError):
            self.check()
        self.assertEqual((self.db.read_bytes(), staging.read_bytes()), (before, before))
        self.assertFalse((self.root / MARKER_NAME).exists())

    def test_replacement_database_and_hardlink_are_refused(self):
        self.check()
        replacement = Path(self.temp.name) / 'replacement'
        replacement.write_bytes(b'old staging state')
        replacement.chmod(0o600)
        os.replace(replacement, self.db)
        with self.assertRaises(StateError):
            self.check()
        self.db.unlink()
        self.db.write_bytes(b'linked')
        self.db.chmod(0o600)
        os.link(self.db, Path(self.temp.name) / 'other-link')
        with self.assertRaises(StateError):
            self.check()

    def test_sidecar_without_database_blocks_bootstrap(self):
        (self.root / (DATABASE_NAME + '-wal')).write_bytes(b'preserve')
        with self.assertRaises(StateError):
            self.check()
        self.assertFalse(self.db.exists())

    def test_copying_staging_over_bound_inode_is_refused(self):
        self.check()
        staging = Path(self.temp.name) / 'staging.sqlite3'
        State(staging)
        with staging.open('rb') as source, self.db.open('wb') as target:
            shutil.copyfileobj(source, target)
        before = self.db.read_bytes()
        with self.assertRaises(StateError):
            self.check()
        self.assertEqual(self.db.read_bytes(), before)

    def test_uncheckpointed_wal_with_active_connection_keeps_binding(self):
        self.check()
        State(self.db)
        db = sqlite3.connect(self.db)
        try:
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute("INSERT INTO tiktok_tags(added_at, tag, views) VALUES(1, 'test', 1)")
            db.commit()
            self.assertTrue((self.root / (DATABASE_NAME + '-wal')).exists())
            self.check()
            self.assertEqual(db.execute('SELECT count(*) FROM tiktok_tags').fetchone()[0], 1)
        finally:
            db.close()

    def test_interrupted_bootstrap_refuses_orphan_database(self):
        original = os.open
        def fail_marker(path, flags, *args, **kwargs):
            if path == MARKER_NAME:
                raise OSError('simulated interrupted marker creation')
            return original(path, flags, *args, **kwargs)
        with patch('nailblog.production_state.os.open', side_effect=fail_marker):
            with self.assertRaises(StateError):
                self.check()
        self.assertTrue(self.db.exists())
        before = self.db.read_bytes()
        with self.assertRaises(StateError):
            self.check()
        self.assertEqual(self.db.read_bytes(), before)

    def test_in_place_overwrite_with_active_wal_is_not_accepted(self):
        self.check()
        State(self.db)
        staging = Path(self.temp.name) / 'staging.sqlite3'
        State(staging)
        db = sqlite3.connect(self.db)
        try:
            db.execute('PRAGMA wal_autocheckpoint=0')
            db.execute("INSERT INTO tiktok_tags(added_at, tag, views) VALUES(1, 'test', 1)")
            db.commit()
            self.assertTrue((self.root / (DATABASE_NAME + '-wal')).exists())
            with staging.open('rb') as source, self.db.open('wb') as target:
                shutil.copyfileobj(source, target)
            with self.assertRaises(StateError):
                self.check()
        finally:
            db.close()

    def test_symlink_database_and_root_are_refused(self):
        other = Path(self.temp.name) / 'other'
        other.write_bytes(b'preserve')
        self.db.symlink_to(other)
        with self.assertRaises(StateError):
            self.check()
        self.assertEqual(other.read_bytes(), b'preserve')
        link = Path(self.temp.name) / 'link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(StateError):
            ensure_production_state(link / DATABASE_NAME, root=link)
