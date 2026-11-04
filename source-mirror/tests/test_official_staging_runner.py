import json
from datetime import datetime
import tempfile
import unittest
import os
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
from source_mirror.official_staging_runner import run

class StagingRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.base=self.root/'official-inputs';self.base.mkdir()
        clock=patch('source_mirror.official_staging_runner.datetime',wraps=datetime);mock_clock=clock.start();self.addCleanup(clock.stop)
        mock_clock.now.return_value=datetime(2026,10,30,5,30,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
        (self.base/'draft.json').write_bytes(b'draft')
        (self.base/'editorial.json').write_text('{"published_at":"2026-10-30T08:00:00+07:00","other":true}',encoding='utf-8')
        (self.base/'image.jpg').write_bytes(b'image')
        (self.base/'rights.txt').write_bytes(b'public rights')
        self.manifest={'source_id':'irs-tip-reporting','source_sha256':'a'*64,'draft':'draft.json','editorial':'editorial.json',
                       'media':[{'descriptor':{'id':'stock'},'image':'image.jpg','rights':'rights.txt'}]}
    def write(self): (self.base/'input.json').write_text(json.dumps(self.manifest),encoding='utf-8')
    @unittest.skipUnless(os.name=='posix','Linux flock ownership required')
    def test_autostage_owned_lock_reaches_runner_without_reacquiring(self):
        from source_mirror.lock import worker_lock
        self.write()
        with patch('source_mirror.official_staging_runner.maintenance',return_value=nullcontext()), \
             patch('source_mirror.official_staging_runner.require'), \
             patch('source_mirror.official_staging_runner.prepare',return_value={'path':'fixture','record_sha256':'f'*64}), \
             patch('source_mirror.official_staging_runner.Store') as store, \
             patch('source_mirror.official_staging_runner.stage',return_value='article'):
            store.return_value.db.execute.return_value.fetchone.return_value=None
            with worker_lock(self.root):pass
            with self.assertRaisesRegex(ValueError,'lock not owned'):run(self.root,'input.json',lock_held=True)
            with worker_lock(self.root):self.assertEqual(run(self.root,'input.json',lock_held=True)['status'],'STAGED_NOT_PUBLISHABLE')
    def test_calls_trusted_prepare_before_staging(self):
        self.write()
        with patch('source_mirror.official_staging_runner.maintenance',return_value=nullcontext()), \
             patch('source_mirror.official_staging_runner.require'), \
             patch('source_mirror.official_staging_runner.prepare',return_value={'path':'fixture','record_sha256':'f'*64}) as prepare, \
             patch('source_mirror.official_staging_runner.Store') as store, \
             patch('source_mirror.official_staging_runner.stage',return_value='article') as stage:
            store.return_value.db.execute.return_value.fetchone.return_value=None
            result=run(self.root,'input.json')
            self.assertEqual(result['status'],'STAGED_NOT_PUBLISHABLE')
            self.assertEqual(prepare.call_args.args[7],{'stock':b'public rights'})
            stage.assert_called_once();store.return_value.close.assert_called_once()
    def test_failed_evidence_does_not_stage(self):
        self.write()
        with patch('source_mirror.official_staging_runner.maintenance',return_value=nullcontext()), \
             patch('source_mirror.official_staging_runner.require'), \
             patch('source_mirror.official_staging_runner.prepare',side_effect=ValueError('held')), \
             patch('source_mirror.official_staging_runner.stage') as stage:
            with self.assertRaises(ValueError):run(self.root,'input.json')
            stage.assert_not_called()
    def test_generated_editorial_still_requires_trusted_prepare(self):
        (self.base/'editorial.json').write_text(json.dumps({'category':'Safety','published_at':'2026-11-01T08:00:00+07:00'}),encoding='utf-8')
        self.write()
        with patch('source_mirror.official_staging_runner.maintenance',return_value=nullcontext()), \
             patch('source_mirror.official_staging_runner.require'), \
             patch('source_mirror.official_editorial_generator.generate',return_value={'generated':True,'published_at':'2026-11-01T08:00:00+07:00'}) as generate, \
             patch('source_mirror.official_staging_runner.prepare',side_effect=ValueError('factual hold')) as prepare, \
             patch('source_mirror.official_staging_runner.stage') as stage:
            with self.assertRaisesRegex(ValueError,'factual hold'):run(self.root,'input.json')
            generate.assert_called_once();self.assertTrue(prepare.call_args.args[4]['generated']);stage.assert_not_called()
    def test_paths_and_duplicate_ids_rejected(self):
        for name in ('../secret','/absolute','nested\\escape'):
            self.manifest['draft']=name;self.write()
            with self.subTest(name=name),self.assertRaises(ValueError):run(self.root,'input.json')
        self.manifest['draft']='draft.json';self.manifest['media']*=2;self.write()
        with self.assertRaises(ValueError):run(self.root,'input.json')
    def test_pending_slot_stops_before_inference(self):
        from source_mirror.store import Store
        store=Store(self.root);store.close();self.write()
        from source_mirror import official_staging_runner as runner
        runner.datetime.now.return_value=datetime(2026,10,30,9,10,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
        with patch('source_mirror.official_staging_runner.maintenance',return_value=nullcontext()), \
             patch('source_mirror.official_staging_runner.require'),patch('source_mirror.official_staging_runner.prepare') as prepare:
            with self.assertRaisesRegex(ValueError,'slot remains pending'):run(self.root,'input.json')
            prepare.assert_not_called()
    def test_slot_passing_during_prepare_never_stages(self):
        self.write()
        from source_mirror import official_staging_runner as runner
        zone=ZoneInfo('Asia/Ho_Chi_Minh')
        runner.datetime.now.side_effect=[datetime(2026,10,30,5,30,tzinfo=zone),datetime(2026,10,30,5,30,tzinfo=zone),datetime(2026,10,30,8,1,tzinfo=zone)]
        with patch('source_mirror.official_staging_runner.maintenance',return_value=nullcontext()), \
             patch('source_mirror.official_staging_runner.require'), \
             patch('source_mirror.official_staging_runner.prepare',return_value={'path':'fixture','record_sha256':'f'*64}), \
             patch('source_mirror.official_staging_runner.stage') as stage:
            with self.assertRaisesRegex(ValueError,'slot has passed'):run(self.root,'input.json')
            stage.assert_not_called()
