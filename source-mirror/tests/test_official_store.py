import copy
import hashlib
import json
import shutil
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
import unittest
from pathlib import Path
from unittest.mock import patch
import test_official_auto_evidence as fixture
from source_mirror.official_store import stage,reserve,release
from source_mirror.store import Store
from source_mirror.snapshot import save


class OfficialStoreTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.EvidenceTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.captured=self.f.capture();self.folder=Path(self.captured['path']);self.root=self.f.base.root
        self.store=Store(self.root);self.addCleanup(self.store.close);self.store.set_paused(False)
        payload=(self.folder/'payload.json').read_bytes();policy=self.f.result['media_policy']
        self.trusted=(payload,{'status':'REVALIDATED_NOT_PUBLISHABLE','record_sha256':self.captured['record_sha256']},
                      policy['category_mapping'],{'figure':self.f.fixture.image},policy)
        for target,value in (('source_mirror.official_store._live_inputs',self.trusted),('source_mirror.official_store.load_media_policy',policy)):
            p=patch(target,return_value=value);p.start();self.addCleanup(p.stop)
        p=patch('source_mirror.official_store.load_publish_policy',return_value={'enabled':True,'source_ids':['cdc-niosh-nail-technicians']});p.start();self.addCleanup(p.stop)
    def stage(self):return stage(self.store,self.folder,self.captured['record_sha256'])
    def test_stage_is_idempotent_and_invisible_to_existing_scheduler(self):
        aid=self.stage();self.assertEqual(self.stage(),aid);self.assertEqual(self.store.ready_candidates(),[])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        with self.assertRaisesRegex(ValueError,'disabled'):reserve(self.store,aid,'2026-11-01','08:00')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
    def test_test_only_enabled_reservation_shares_slot_and_unknown_blocks(self):
        aid=self.stage()
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True):
            attempt,_=reserve(self.store,aid,'2026-11-01','08:00');self.store.unknown(attempt)
            with self.assertRaisesRegex(ValueError,'unresolved'):reserve(self.store,aid,'2026-11-01','12:00')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],1)
        with self.assertRaises(ValueError):release(self.store,attempt,'')
        release(self.store,attempt,'verified no destination post')
        self.assertEqual(self.store.db.execute('SELECT state FROM articles WHERE id=?',(aid,)).fetchone()[0],'official_ready')
        with self.assertRaises(ValueError):release(self.store,attempt,'duplicate reconciliation')
    def test_standard_reconciliation_does_not_move_official_into_scratch_queue(self):
        aid=self.stage()
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True):
            attempt,_=reserve(self.store,aid,'2026-11-01','08:00')
        self.store.unknown(attempt);self.store.release(attempt,'verified no destination post')
        self.assertEqual(self.store.db.execute('SELECT state FROM articles WHERE id=?',(aid,)).fetchone()[0],'official_ready')
        self.assertEqual(self.store.ready_candidates(),[])
    def test_quota_and_changed_evidence_fail_before_intent(self):
        aid=self.stage()
        for i in range(4):
            url=f'https://example.com/{i}';meta={'source_url':url,'title':f'Article {i}'}
            sid=save(self.root,meta,[{'type':'paragraph','text':'Fixture'}],'<p>Fixture</p>',[]);self.store.add_ready(url,meta['title'])
            attempt,_=self.store.reserve('2026-11-01',article_id=sid);self.store.published(attempt,f'https://nailhub.ai/blog/{i}','2026-11-01')
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True):
            with self.assertRaisesRegex(ValueError,'quota'):reserve(self.store,aid,'2026-11-01','12:00')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
        (self.folder/'payload.json').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError,'evidence file changed'):self.stage()

    def test_new_revision_updates_only_ready_candidate_and_preserves_history(self):
        aid=self.stage();record=json.loads((self.folder/'record.json').read_text(encoding='utf-8'))
        record['checked_at_utc']='2026-10-30T00:00:00+00:00'
        from source_mirror.localization import packed
        raw=(packed(record)+'\n').encode();new_sha=hashlib.sha256(raw).hexdigest();new_folder=self.folder.with_name(new_sha)
        shutil.copytree(self.folder,new_folder);(new_folder/'record.json').write_bytes(raw)
        new_boundary=dict(self.trusted[1],record_sha256=new_sha)
        with patch('source_mirror.official_store._live_inputs',return_value=(self.trusted[0],new_boundary,*self.trusted[2:])):
            self.assertEqual(stage(self.store,new_folder,new_sha),aid)
            self.assertEqual(self.store.db.execute('SELECT record_sha FROM official_candidates WHERE article_id=?',(aid,)).fetchone()[0],new_sha)
            self.assertEqual(self.store.db.execute('SELECT count(*) FROM official_candidate_versions WHERE article_id=?',(aid,)).fetchone()[0],2)
            with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True):reserve(self.store,aid,'2026-11-01','08:00')
        with self.assertRaisesRegex(ValueError,'revision is locked'): self.stage()
        self.assertTrue(self.folder.is_dir())

    def test_cli_reconcile_restores_official_state(self):
        aid=self.stage()
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True):attempt,_=reserve(self.store,aid,'2026-11-01','08:00')
        self.store.unknown(attempt)
        config=self.root/'test-config.json';config.write_text(json.dumps({'data_root':str(self.root),'timezone':'Asia/Ho_Chi_Minh','slots':['08:00','12:00','16:00','20:00']}))
        from source_mirror.__main__ import main
        with patch.object(sys,'argv',['source-mirror','--config',str(config),'reconcile',str(attempt),'--confirmed-not-created','--evidence','verified no destination']):main()
        self.assertEqual(self.store.db.execute('SELECT state FROM articles WHERE id=?',(aid,)).fetchone()[0],'official_ready')
        self.assertEqual(self.store.ready_candidates(),[])

    def test_two_connections_cannot_reserve_same_slot_or_duplicate_attempt(self):
        aid=self.stage();barrier=threading.Barrier(2)
        value=json.loads(self.trusted[0]);policy=self.trusted[4]
        def validated(*_):barrier.wait(timeout=10);return self.trusted[0],value,policy
        def attempt():
            store=Store(self.root)
            try:
                try:reserve(store,aid,'2026-11-01','08:00');return 'reserved'
                except ValueError:return 'held'
            finally:store.close()
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True), patch('source_mirror.official_store._validated',side_effect=validated):
            with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(lambda _:attempt(),range(2)))
        self.assertCountEqual(results,['reserved','held'])
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],1)
