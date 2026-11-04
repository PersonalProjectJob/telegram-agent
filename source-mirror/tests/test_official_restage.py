import unittest
import hashlib
import json
import shutil
import os
import signal
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
import test_official_store as fixture
from source_mirror.official_restage import expired_candidate,verify_new_candidate,begin_restage,finalize_restage
from source_mirror.official_store import reserve,stage
from source_mirror.localization import packed
from source_mirror.official_sources import SOURCES
from source_mirror.official_generation_state import GenerationState
from source_mirror.official_autostage import _prepare_snapshot,execute_child
from contextlib import nullcontext

class RestageTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.OfficialStoreTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.aid=self.f.stage();self.item=SOURCES[0];self.zone=ZoneInfo('Asia/Ho_Chi_Minh')
    def new_revision(self,target):
        value=json.loads((self.f.folder/'payload.json').read_bytes());value['published_at']=target.isoformat()
        payload=(packed(value)+'\n').encode();record=json.loads((self.f.folder/'record.json').read_bytes())
        record['payload_sha256']=hashlib.sha256(payload).hexdigest();record['files']['payload.json']=record['payload_sha256']
        raw=(packed(record)+'\n').encode();sha=hashlib.sha256(raw).hexdigest();folder=self.f.folder.with_name(sha)
        shutil.copytree(self.f.folder,folder);(folder/'record.json').write_bytes(raw);(folder/'payload.json').write_bytes(payload)
        boundary=dict(self.f.trusted[1],record_sha256=sha)
        return payload,sha,folder,boundary
    def test_only_expired_intact_candidate_is_returned(self):
        now=datetime(2026,11,1,10,0,tzinfo=self.zone)
        result=expired_candidate(self.f.store,self.item,now)
        self.assertEqual(result['source_sha'],self.f.folder.parent.name)
        self.assertIsNone(expired_candidate(self.f.store,self.item,datetime(2026,11,1,7,0,tzinfo=self.zone)))
        (self.f.folder/'payload.json').write_bytes(b'tampered')
        with self.assertRaises(ValueError):expired_candidate(self.f.store,self.item,now)
    def test_unknown_attempt_blocks_restage(self):
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True):
            attempt,_=reserve(self.f.store,self.aid,'2026-11-01','08:00')
        self.f.store.unknown(attempt)
        with self.assertRaisesRegex(ValueError,'unresolved'):expired_candidate(self.f.store,self.item,datetime(2026,11,1,10,0,tzinfo=self.zone))
    def test_new_slot_uses_distinct_durable_phase_and_rechecks_evidence(self):
        now=datetime(2026,11,1,10,0,tzinfo=self.zone)
        old=expired_candidate(self.f.store,self.item,now);source=old['source_sha']
        state=GenerationState(self.f.root);self.addCleanup(state.close)
        state.claim(self.item['id'],source);state.finish(self.item['id'],source,draft_sha='a'*64)
        state.claim('stage:'+self.item['id'],source);state.finish('stage:'+self.item['id'],source,draft_sha='a'*64)
        settings={'categories':{self.item['id']:'Safety'}}
        with patch('source_mirror.official_autostage.datetime',wraps=datetime) as clock, \
             patch('source_mirror.official_autostage.RESTAGING_VERIFIED',True), \
             patch('source_mirror.official_autostage.policy',return_value=settings), \
             patch('source_mirror.official_autostage.stage_draft',return_value={'status':'STAGED_NOT_PUBLISHABLE','record_sha256':'c'*64}) as stage, \
             patch('source_mirror.official_autostage.verify_new_candidate') as verify, \
             patch('source_mirror.official_autostage.begin_restage'),patch('source_mirror.official_autostage.finalize_restage'):
            clock.now.return_value=now
            result=_prepare_snapshot(self.f.root,self.item,source,settings,state,old)
            self.assertEqual(result['status'],'STAGED_NOT_PUBLISHABLE')
            self.assertEqual(stage.call_args.args[-1],'12:00');self.assertTrue(stage.call_args.kwargs['restage'])
            verify.assert_called_once()
            with self.assertRaisesRegex(ValueError,'already attempted'):_prepare_snapshot(self.f.root,self.item,source,settings,state,old)
            self.assertEqual(stage.call_count,1)
    def test_committed_revision_must_match_new_slot_and_not_have_intent(self):
        now=datetime(2026,11,1,10,0,tzinfo=self.zone);target=datetime(2026,11,1,12,0,tzinfo=self.zone)
        old=expired_candidate(self.f.store,self.item,now)
        payload,sha,folder,boundary=self.new_revision(target)
        begin_restage(self.f.store,self.item,old,now)
        with patch('source_mirror.official_store._live_inputs',return_value=(payload,boundary,*self.f.trusted[2:])):
            stage(self.f.store,folder,sha,restage=True)
        verify_new_candidate(self.f.store,self.item,old,sha,target,now)
        with self.assertRaises(ValueError):verify_new_candidate(self.f.store,self.item,old,sha,datetime(2026,11,1,16,0,tzinfo=self.zone),now)
        with self.assertRaises(ValueError):finalize_restage(self.f.store,self.item,old,sha,datetime(2026,11,1,16,0,tzinfo=self.zone),now)
        self.assertEqual(self.f.store.db.execute('SELECT state FROM articles WHERE id=?',(self.aid,)).fetchone()[0],'official_preparing')
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True), \
             patch('source_mirror.official_store._live_inputs',return_value=(payload,boundary,*self.f.trusted[2:])):
            with self.assertRaisesRegex(ValueError,'in progress'):stage(self.f.store,folder,sha)
            with self.assertRaisesRegex(ValueError,'not ready'):reserve(self.f.store,self.aid,'2026-11-01','12:00')
            self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
            finalize_restage(self.f.store,self.item,old,sha,target,now)
            reserve(self.f.store,self.aid,'2026-11-01','12:00')
        with self.assertRaisesRegex(ValueError,'reconciliation'):verify_new_candidate(self.f.store,self.item,old,sha,target,now)
    def test_sigkill_after_new_revision_commit_cannot_reserve(self):
        if os.name!='posix':self.skipTest('POSIX SIGKILL required')
        from source_mirror.store import Store
        now=datetime(2026,11,1,10,0,tzinfo=self.zone);target=datetime(2026,11,1,12,0,tzinfo=self.zone)
        old=expired_candidate(self.f.store,self.item,now);payload,sha,folder,boundary=self.new_revision(target)
        read_fd,write_fd=os.pipe();pid=os.fork()
        if pid==0:
            os.close(read_fd)
            try:
                self.f.store.close();child=Store(self.f.root)
                begin_restage(child,self.item,old,now)
                with patch('source_mirror.official_store._live_inputs',return_value=(payload,boundary,*self.f.trusted[2:])):
                    stage(child,folder,sha,restage=True)
                os.write(write_fd,b'1');time.sleep(60)
            finally:os._exit(2)
        os.close(write_fd)
        try:
            self.assertEqual(os.read(read_fd,1),b'1')
        finally:
            os.close(read_fd);os.kill(pid,signal.SIGKILL);os.waitpid(pid,0)
        self.assertEqual(self.f.store.db.execute('SELECT state FROM articles WHERE id=?',(self.aid,)).fetchone()[0],'official_preparing')
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True), \
             patch('source_mirror.official_store._live_inputs',return_value=(payload,boundary,*self.f.trusted[2:])):
            with self.assertRaisesRegex(ValueError,'not ready'):reserve(self.f.store,self.aid,'2026-11-01','12:00')
        self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
    def test_changed_refetched_html_holds_before_restage_inference(self):
        now=datetime(2026,11,1,10,0,tzinfo=self.zone);old=expired_candidate(self.f.store,self.item,now);source=old['source_sha']
        self.f.store.db.execute("INSERT INTO slot_attempts VALUES ('2026-11-01','08:00',3)")
        state=GenerationState(self.f.root)
        state.claim(self.item['id'],source);state.finish(self.item['id'],source,draft_sha='a'*64)
        state.claim('stage:'+self.item['id'],source);state.finish('stage:'+self.item['id'],source,draft_sha='a'*64);state.close()
        settings={'version':'official-autostage-v1','enabled':True,'source_ids':[self.item['id']],'categories':{self.item['id']:'Safety'}}
        with patch('source_mirror.official_autostage.AUTOSTAGE_VERIFIED',True),patch('source_mirror.official_autostage.RESTAGING_VERIFIED',True),patch('source_mirror.official_autostage.DATA_ROOT',self.f.root), \
             patch('source_mirror.official_autostage.policy',return_value=settings),patch('source_mirror.official_autostage.maintenance',return_value=nullcontext()), \
             patch('source_mirror.official_autostage.require'),patch('source_mirror.official_autostage.datetime',wraps=datetime) as clock, \
             patch('source_mirror.official_autostage.collect',return_value={'snapshot':str(self.f.root/'queue'/('b'*64))}), \
             patch('source_mirror.official_autostage.create') as create,patch('source_mirror.official_autostage.stage_draft') as stage:
            clock.now.return_value=now
            self.assertEqual(execute_child()['status'],'RESTAGE_SOURCE_CHANGED_HOLD')
            create.assert_not_called();stage.assert_not_called()
