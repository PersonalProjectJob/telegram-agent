import asyncio
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from nailhub._errors import ConfigError
from nailhub.model_lock import ModelLock as ModelGate
from nailhub.browser import posting_from_data
from nailhub.config import load_config, load_optional
from nailhub.credentials import load_credentials
from nailhub.model import NailHubModel
from nailhub.state import State
from nailhub.validation import FACT_KINDS, NeedsHuman
from tests.helpers import FakeHTTP
from tests.nailhub_fixtures import extracted, raw_post, settings

class StateTests(unittest.TestCase):
    def test_concurrent_scan_and_post_claims_coalesce(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory)/'jobs.db')
            with ThreadPoolExecutor(8) as pool:self.assertEqual(sum(pool.map(lambda _:state.enqueue(),range(30))),1)
            with ThreadPoolExecutor(8) as pool:self.assertEqual(sum(pool.map(lambda _:state.claim_scan(),range(30))),1)
            self.assertFalse(state.enqueue());state.finish_scan('done');self.assertTrue(state.enqueue())
            self.assertTrue(state.observe('a','hash',{}))
            with ThreadPoolExecutor(8) as pool:self.assertEqual(sum(pool.map(lambda _:state.claim_post('a'),range(30))),1)

    def test_pause_recovery_unchanged_terminal_and_uncertain_locks(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'jobs.db';state=State(path);state.enqueue();state.pause(True)
            self.assertFalse(state.claim_scan());state=State(path);self.assertTrue(state.status()['paused'])
            state.pause(False);self.assertTrue(state.claim_scan());state.recover_queue();self.assertTrue(state.claim_scan())
            state.observe('a','h',{});state.mark('a','needs-human');self.assertFalse(state.observe('a','h',{}))
            self.assertTrue(state.observe('a','changed',{}));state.mark('a','uncertain')
            self.assertFalse(state.observe('a','changed-again',{}))
            state.observe('b','h',{});state.mark('b','before_save',expected={'id':'b'})
            self.assertEqual(state.interrupted()[0]['state'],'before_save')

    def test_preview_only_requeue_for_live(self):
        with tempfile.TemporaryDirectory() as directory:
            state=State(Path(directory)/'jobs.db');state.observe('a','h',{});state.mark('a','preview')
            self.assertFalse(state.observe('a','h',{}));self.assertTrue(state.observe('a','h',{},requeue_preview=True))
            state.mark('a','needs-human');self.assertFalse(state.observe('a','h',{},requeue_preview=True))

class ConfigModelTests(unittest.TestCase):
    def test_optional_absent_and_strict_config(self):
        self.assertIsNone(load_optional({}))
        with self.assertRaises(ConfigError):load_config('relative.toml')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'config.toml'
            base='[worker]\ndb_path='+json.dumps(str(Path(directory)/'a.db'))+'\nprofile_path='+json.dumps(str(Path(directory)/'profile'))+'\n'
            path.write_text(base);cfg=load_config(path);self.assertEqual(cfg.mode,'preview');self.assertEqual(cfg.owner_ids,())
            for extra in ('[model]\nthink=true','[model]\nmodel=""','mode="live"','owner_ids=[true]',
                          '[model]\nurl="http://example.com"','owner_username="Other"'):
                path.write_text(base+extra)
                with self.subTest(extra=extra):
                    with self.assertRaises(ConfigError):load_config(path)

    def test_model_owner_think_false_only_content_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            post=posting_from_data(raw_post());cfg=settings(directory)
            http=FakeHTTP({'message':{'thinking':'DO NOT STORE','content':json.dumps(extracted(post.content))}})
            data=NailHubModel(cfg,opener=http).extract(post);body=json.loads(http.calls[0][0].data)
            self.assertFalse(body['think']);self.assertEqual(body['model'],'qwen3.5:9b')
            self.assertEqual(body['options']['num_predict'],2048)
            self.assertEqual(body['options']['num_ctx'],8192)
            self.assertNotIn('_preserved',body['messages'][-1]['content'])
            self.assertNotIn('DO NOT STORE',json.dumps(data))
            for response in ({'message':{'content':'not JSON'}},{'message':{'content':'[]'}},OSError('secret')):
                with self.assertRaises(NeedsHuman):NailHubModel(cfg,opener=FakeHTTP(response)).extract(post)

    def test_extract_schema_requests_empty_facts_without_rejecting_old_valid_response(self):
        with tempfile.TemporaryDirectory() as directory:
            post=posting_from_data(raw_post());cfg=settings(directory)
            for old_response in (False,True):
                with self.subTest(old_response=old_response):
                    response=extracted(post.content)
                    if old_response:response['facts']['salary']=[{'quote':post.content}]
                    http=FakeHTTP({'message':{'content':json.dumps(response)}})
                    result=NailHubModel(cfg,opener=http).extract(post)
                    self.assertEqual(result,response)
                    body=json.loads(http.calls[0][0].data)
                    facts=body['format']['properties']['facts']
                    self.assertEqual(set(facts['required']),set(FACT_KINDS))
                    self.assertTrue(all(value['maxItems']==0 for value in facts['properties'].values()))
                    self.assertIn('ALL required facts category arrays MUST be []',body['messages'][0]['content'])
                    self.assertNotIn('facts must cover every factual claim',body['messages'][0]['content'])
                    self.assertEqual(json.loads(body['messages'][1]['content'])['original_content'],post.content)

    def test_semantic_all_facts_no_extra_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory);post=posting_from_data(raw_post())
            good={**{k:True for k in (*FACT_KINDS,'title','content','structured_mapping')},'rationale':'Faithful.'}
            intended={'fields':post.fields,'salary':{'mode':'weekly','source_amount':'$1200'}}
            for data,wanted in ((good,True),({**good,'benefits':False},False),
                                ({**good,'structured_mapping':False},False),({**good,'approve':True},False)):
                http=FakeHTTP({'message':{'content':json.dumps(data)}})
                model=NailHubModel(cfg,opener=http)
                self.assertEqual(model.verify(post,post.fields['title'],post.content,intended),wanted)
                body=json.loads(http.calls[0][0].data)
                self.assertIn('salary_derivation',body['messages'][-1]['content'])

    def test_extraction_one_literal_evidence_correction_with_safe_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory);post=posting_from_data(raw_post())
            invalid=extracted(post.content);invalid['facts']['salon']=[{'quote':'reconstructed private source'}]
            invalid['location']={'zip':{'value':'30301','evidence':{'quote':'30301'}}}
            corrected=extracted(post.content)
            responses=[invalid,corrected];calls=[]
            def opener(request,**kwargs):
                calls.append(json.loads(request.data))
                return FakeHTTP({'message':{'thinking':'PRIVATE THINKING','content':json.dumps(responses.pop(0))}})(request,**kwargs)
            with self.assertLogs('nailhub.model',level='INFO') as caught:
                result=NailHubModel(cfg,opener=opener).extract(post)
            self.assertEqual(result,corrected);self.assertEqual(len(calls),2)
            feedback=json.loads(calls[1]['messages'][-1]['content'])['validation_feedback']
            self.assertEqual(feedback['path'],'facts.salon[0]')
            self.assertNotIn('reconstructed private source',json.dumps(calls[1]))
            self.assertTrue(any('outcome=validated' in line for line in caught.output))
            self.assertNotIn('PRIVATE THINKING',' '.join(caught.output))
            self.assertNotIn(post.content,' '.join(caught.output))

    def test_evidence_correction_is_bounded_and_true_ambiguity_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg=settings(directory);post=posting_from_data(raw_post())
            invalid=extracted(post.content);invalid['facts']['salon']=[{'quote':'not original'}]
            http=FakeHTTP(*[{'message':{'content':json.dumps(invalid)}} for _ in range(2)])
            with self.assertRaises(NeedsHuman):NailHubModel(cfg,opener=http).extract(post)
            self.assertEqual(len(http.calls),2)
            post=posting_from_data(raw_post('Salary 1200/week or monthly'))
            data=extracted(post.content,'1200','week');http=FakeHTTP({'message':{'content':json.dumps(data)}})
            with self.assertRaises(NeedsHuman):NailHubModel(cfg,opener=http).extract(post)
            self.assertEqual(len(http.calls),1)

    def test_credentials_schema_and_symlink_no_values_in_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'credentials.json';path.write_text('{"email":"fake@example.invalid","password":"TEST"}')
            path.chmod(0o600);self.assertEqual(load_credentials(path)['password'],'TEST')
            path.write_text('{"email":"fake@example.invalid","password":"TEST","token":"secret"}')
            with self.assertRaises(NeedsHuman) as caught:load_credentials(path)
            self.assertNotIn('secret',str(caught.exception));self.assertNotIn('TEST',str(caught.exception))
            with patch('pathlib.Path.is_symlink',return_value=True):
                with self.assertRaises(NeedsHuman):load_credentials(path)

    def test_gate_serializes_waiting_foreground_precedes_background_exception_safe(self):
        gate=ModelGate();order=[];entered=threading.Event();release=threading.Event()
        def active():
            with gate.enter(background=True):entered.set();release.wait(3);order.append('active')
        def next_call(name,background):
            with gate.enter(background=background):order.append(name)
        a=threading.Thread(target=active);a.start();self.assertTrue(entered.wait(1))
        fg=threading.Thread(target=next_call,args=('foreground',False));fg.start();deadline=time.monotonic()+1
        while gate._foreground!=1 and time.monotonic()<deadline:time.sleep(.001)
        bg=threading.Thread(target=next_call,args=('background',True));bg.start();release.set()
        for t in (a,fg,bg):t.join(3);self.assertFalse(t.is_alive())
        self.assertEqual(order,['active','foreground','background'])
        with self.assertRaises(ValueError):
            with gate.enter():raise ValueError()
        with gate.enter(background=True):pass
