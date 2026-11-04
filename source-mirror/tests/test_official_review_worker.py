import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

from source_mirror.official_sources import collect, SOURCES
from source_mirror.official_draft import source_snapshot
from source_mirror.official_review_worker import once, requeue_held, status

HTML = b'<html><head><title>Safety</title></head><body><main><p>Safety guidance.</p></main></body></html>'

def run_once(*args, **kwargs):
    kwargs.setdefault('clock', lambda: datetime(2026,10,29,5,30,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh')))
    return once(*args, **kwargs)


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.sha = hashlib.sha256(HTML).hexdigest()
        collect(self.root, SOURCES[0], lambda _: HTML)
        self.config = {'data_root':str(self.root), 'official_review':{'enabled':True, 'socket_path':str(self.root/'review.sock')}}
        self.calls = []

    def draft(self, root, source_id, source_sha, config, inference=None):
        self.calls.append('draft')
        folder = root/'official-drafts'/source_id/source_sha; folder.mkdir(parents=True,exist_ok=True)
        path = folder/'test.json'; path.write_text(json.dumps({'source':source_snapshot(root,source_id,source_sha)}),encoding='utf-8')
        return {'path':str(path),'sha256':'b'*64,'status':'HOLD'}

    def send(self, path, artifact, digest):
        self.calls.append('send'); return {'state':'pending','sha256':digest}

    def test_disabled_and_idempotent_submission(self):
        self.assertEqual(run_once({'official_review':{'enabled':False}},self.draft,self.send),[])
        self.assertEqual(run_once(self.config,self.draft,self.send)[0]['state'],'submitted')
        self.assertEqual(run_once(self.config,self.draft,self.send),[])
        self.assertEqual(self.calls,['draft','send'])

    def test_send_timeout_and_interrupted_send_never_repeat(self):
        def timeout(*_): self.calls.append('send'); raise TimeoutError()
        self.assertEqual(run_once(self.config,self.draft,timeout)[0]['state'],'unknown')
        self.assertEqual(run_once(self.config,self.draft,self.send),[])
        with closing(sqlite3.connect(self.root/'official-review-worker.sqlite3', isolation_level=None)) as db:
            db.execute("UPDATE jobs SET state='sending'")
        self.assertEqual(run_once(self.config,self.draft,self.send),[])
        with closing(sqlite3.connect(self.root/'official-review-worker.sqlite3', isolation_level=None)) as db:
            self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0],'unknown')
        self.assertEqual(self.calls,['draft','send'])

    def test_failed_generation_and_changed_source_are_held(self):
        def fail(*_): raise ValueError('private detail')
        self.assertEqual(run_once(self.config,fail,self.send)[0]['state'],'held')
        self.assertEqual(run_once(self.config,self.draft,self.send),[])
        self.assertEqual(self.calls,[])

    def test_actual_generator_mqm_and_transport_handoff(self):
        from source_mirror.localization import MODEL, EFFORT
        self.config['localization'] = {'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':'/test'}
        candidate = {'title_vi':'An toàn làm việc','text_vi':'Hướng dẫn an toàn.','uncertainties':[]}
        review = {'verdict':'PASS','critical':0,'major':0,'minor':0,'issues':[],'uncertainties':[],
                  'glossary_ok':True,'accuracy_ok':True,'technical_clarity_ok':True}
        responses = iter([candidate,review])
        def infer(*_): return {'status':'SUCCESS','response':json.dumps(next(responses),ensure_ascii=False)}
        def checked_send(path, artifact, digest):
            data = Path(artifact).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(),digest)
            self.assertEqual(json.loads(data)['status'],'HOLD')
            self.calls.append('send')
            return {'state':'pending','sha256':digest}
        with patch('source_mirror.official_review_worker.call',side_effect=infer):
            self.assertEqual(run_once(self.config,send=checked_send)[0]['state'],'submitted')
        self.assertEqual(self.calls,['send'])

    def test_window_blocks_outside_even_if_manually_called(self):
        for hour, minute in [(5,29),(5,40),(8,0),(12,0),(16,0),(20,0)]:
            with self.subTest(hour=hour,minute=minute):
                self.assertEqual(run_once(self.config,self.draft,self.send,clock=lambda: datetime(2026,10,29,hour,minute,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))),[])
        self.assertEqual(self.calls,[])

    def test_explicit_requeue_only_held_never_unknown(self):
        run_once(self.config,lambda *a,**k: (_ for _ in ()).throw(TimeoutError()),self.send)
        self.assertEqual(status(self.root)[0]['reason'],'inference_or_source_unavailable')
        requeue_held(self.root,SOURCES[0]['id'],self.sha)
        self.assertEqual(run_once(self.config,self.draft,self.send)[0]['state'],'submitted')
        with self.assertRaises(ValueError): requeue_held(self.root,SOURCES[0]['id'],self.sha)
        with closing(sqlite3.connect(self.root/'official-review-worker.sqlite3', isolation_level=None)) as db:
            db.execute("UPDATE jobs SET state='unknown'")
        with self.assertRaises(ValueError): requeue_held(self.root,SOURCES[0]['id'],self.sha)

    def test_second_inference_cannot_begin_after_cutoff(self):
        from source_mirror.localization import MODEL, EFFORT
        self.config['localization'] = {'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':'/test'}
        times = iter([datetime(2026,10,29,5,30,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))]*4 + [datetime(2026,10,29,5,50,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))])
        candidate = {'title_vi':'An toàn làm việc','text_vi':'Hướng dẫn an toàn.','uncertainties':[]}
        with patch('source_mirror.official_review_worker.call',return_value={'status':'SUCCESS','response':json.dumps(candidate,ensure_ascii=False)}) as call:
            self.assertEqual(once(self.config,send=self.send,clock=lambda: next(times))[0]['state'],'held')
            self.assertLessEqual(call.call_count,1)
        self.assertNotIn('send',self.calls)


if __name__ == '__main__': unittest.main()
