"""Offline API, persistence, providers and apply tests; never call a paid upstream."""
import copy
import contextlib
import http.client
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import subprocess
import threading
import types
import time
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
from backend import Store, APIError, patch_toml
from run import ConsoleServer
from tg_triage.ai_transport import complete, ProviderError, validate_endpoint

class BackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name,transport=lambda *args:{'message':{'content':'{"ok":true}'}})
    def draft(self,id='triage'):return self.store.public(id)['settings']
    def save(self,id='triage',**kwargs):return self.store.save(id,{'settings':self.draft(id),'expectedRevision':self.store.public(id)['revision'],**kwargs})
    def test_isolation_persistence_and_stale_revision(self):
        before=self.store.paths['nailhub'].read_bytes();self.save()
        self.assertEqual(before,self.store.paths['nailhub'].read_bytes())
        self.assertEqual(Store(self.temp.name).public('triage')['revision'],self.store.public('triage')['revision'])
        with self.assertRaises(APIError) as e:self.store.save('triage',{'settings':self.draft(),'expectedRevision':0})
        self.assertEqual(e.exception.status,409)
    def test_same_server_save_fails_before_client_timeout_when_apply_holds_lock(self):
        settings=self.draft();server=ConsoleServer(('127.0.0.1',0),self.store)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        held=threading.Event();release=threading.Event()
        def holding_apply():
            with self.store.locks['triage']:
                held.set();release.wait(15)
        thread=threading.Thread(target=holding_apply);thread.start();self.assertTrue(held.wait(2))
        try:
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            started=time.monotonic()
            connection.request('POST','/api/services/triage/save',json.dumps({'settings':settings,'expectedRevision':0}),{'Content-Type':'application/json','X-CSRF-Token':server.csrf})
            response=connection.getresponse();body=json.loads(response.read());connection.close()
            self.assertEqual(response.status,409);self.assertEqual(body['error']['code'],'service_busy')
            self.assertLess(time.monotonic()-started,7);self.assertEqual(self.store.meta('triage')['revision'],0)
        finally:release.set();thread.join(2)
        self.assertEqual(self.store.public('triage')['revision'],0)

    def test_processes_serialize_expected_revision(self):
        code="""import sys,time
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from backend import Store,APIError
s=Store(sys.argv[2]);d=s.public('triage')['settings']
Path(sys.argv[3]).write_text('ready')
while not Path(sys.argv[4]).exists():time.sleep(.01)
try:s.save('triage',{'settings':d,'expectedRevision':0});print('saved')
except APIError as e:print(e.code)
"""
        start=self.store.directory/'start';processes=[]
        try:
            for index in range(2):
                ready=self.store.directory/('ready'+str(index))
                processes.append(subprocess.Popen([sys.executable,'-c',code,str(Path(__file__).resolve().parent),str(self.store.directory),str(ready),str(start)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True))
            deadline=time.monotonic()+10
            while not all((self.store.directory/('ready'+str(i))).exists() for i in range(2)):
                if time.monotonic()>deadline:self.fail('child setup deadline')
                time.sleep(.02)
            start.write_text('go')
            results=[process.communicate(timeout=15) for process in processes]
            self.assertEqual(sorted(out.strip() for out,err in results),['revision_conflict','saved'])
            self.assertTrue(all(not err for out,err in results))
        finally:
            for process in processes:
                if process.poll() is None:process.kill();process.wait()

    def test_external_drift_rejected_even_before_first_save(self):
        self.store.paths['triage'].write_text(self.store.paths['triage'].read_text()+'\n# external change\n')
        with self.assertRaises(APIError) as e:self.store.save('triage',{'settings':self.draft(),'expectedRevision':0})
        self.assertEqual(e.exception.status,409)
        self.save()
    def test_unknown_multiline_fields_preserved(self):
        text='mode="dry-run"\n[model]\nthink=true\nunknown = [\n "a",\n "b"\n]\n[protected]\nowner="SotaThao"\n'
        result=patch_toml(text,{('model','think'):False,('model','base_url'):'http://127.0.0.1:11434'})
        self.assertIn('unknown = [\n "a",\n "b"\n]',result);self.assertIn('owner="SotaThao"',result)
    def test_array_table_unknown_data_preserved(self):
        text='[model]\nthink = true\n[[audit]]\nthink = true\n'
        result=patch_toml(text,{('model','think'):False})
        self.assertIn('[[audit]]\nthink = true',result)
    def test_secret_scope_rotation_and_no_public_leak(self):
        d=self.draft();d.update(provider='openai',baseUrl='https://api.openai.com/v1',think=False)
        self.store.save('triage',{'settings':d,'expectedRevision':0,'apiKey':'test-secret-value'})
        self.assertTrue(self.store.public('triage')['hasKey'])
        self.assertNotIn('test-secret-value',json.dumps(self.store.bootstrap()))
        self.assertNotIn('test-secret-value',self.store.paths['triage'].read_text())
        changed=copy.deepcopy(d);changed.update(provider='deepseek',baseUrl='https://api.deepseek.com')
        with self.assertRaises(APIError) as e:self.store.test('triage',{'settings':changed})
        self.assertEqual(e.exception.code,'key_required')
    def test_pending_transaction_recovers_complete_config(self):
        first=self.save();meta=self.store.meta('triage');raw=self.store.paths['triage'].read_text()
        self.store.paths['triage'].write_text('invalid config')
        pending=self.store.directory/'triage.pending.json';pending.write_text(json.dumps({'config':raw,'meta':meta,'beforeFingerprint':hashlib.sha256(b'invalid config').hexdigest()}))
        recovered=Store(self.temp.name)
        self.assertEqual(first['service']['revision'],recovered.public('triage')['revision']);self.assertFalse(pending.exists())
    def test_probe_retains_service_lock_priority(self):
        with patch('tg_triage.model_lock.ModelLock.enter',return_value=contextlib.nullcontext()) as enter:
            self.store.test('nailhub',{'settings':self.draft('nailhub')})
        self.assertTrue(enter.call_args.kwargs['background'])
        self.assertIsInstance(enter.call_args.kwargs['stop'],threading.Event)
    def test_foreground_model_lock_wait_can_cancel_before_http(self):
        from tg_triage.model_lock import ModelLock,LockStopped
        lock=ModelLock(self.store.directory/'cancel-lock');stop=threading.Event();outcome=[]
        def waiter():
            try:
                with lock.enter(stop=stop):outcome.append('entered')
            except LockStopped:outcome.append('stopped')
        with lock.enter():
            thread=threading.Thread(target=waiter);thread.start();stop.set();thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(outcome,['stopped'])

    def test_probe_does_not_save_or_require_cloud_consent(self):
        before=self.store.paths['triage'].read_bytes();result=self.store.test('triage',{'settings':self.draft()})
        self.assertEqual(result['status'],'success');self.assertEqual(before,self.store.paths['triage'].read_bytes())
    def test_probe_rejects_numeric_truth_instead_of_boolean(self):
        self.store.transport=lambda *args:{'message':{'content':'{"ok":1}'}}
        result=self.store.test('triage',{'settings':self.draft()})
        self.assertEqual(result['status'],'error');self.assertEqual(result['code'],'invalid_json')
    def test_local_apply_unavailable_and_no_applied_claim(self):
        self.save()
        with self.assertRaises(APIError) as e:self.store.apply('triage',{'expectedRevision':self.store.public('triage')['revision']})
        self.assertEqual(e.exception.code,'apply_unavailable');self.assertIsNone(self.store.public('triage')['appliedRevision'])
    def test_fixed_unit_apply_requires_exact_receipt_and_pid(self):
        self.save();revision=self.store.public('triage')['revision'];commands=[]
        def runner(command,**kwargs):
            commands.append(command)
            if 'restart' in command:
                (self.store.directory/'agentfleet-ready.json').write_text(json.dumps({'revision':revision,'pid':88,'readyAt':time.time(),'mode':'dry-run'}))
            return types.SimpleNamespace(returncode=0,stdout='active\n88\n')
        self.store.runner=runner
        with patch.object(self.store,'capability',return_value=True):result=self.store.apply('triage',{'expectedRevision':revision})
        self.assertEqual(result['service']['appliedRevision'],revision)
        self.assertTrue(all('tg-triage.service' in cmd for cmd in commands))
        self.assertFalse(any('tg-nailhub.service' in cmd for cmd in commands))
    def test_failed_restart_keeps_applied_unknown(self):
        self.save();self.store.runner=lambda *a,**k:types.SimpleNamespace(returncode=1,stdout='secret failure body')
        with patch.object(self.store,'capability',return_value=True),self.assertRaises(APIError) as e:self.store.apply('triage',{'expectedRevision':self.store.public('triage')['revision']})
        self.assertEqual(e.exception.code,'restart_failed');self.assertNotIn('secret',e.exception.message);self.assertIsNone(self.store.public('triage')['appliedRevision'])
    def test_mismatched_receipts_never_claim_applied(self):
        self.save();revision=self.store.public('triage')['revision']
        for mismatch in ({'revision':'old'},{'pid':99},{'readyAt':0},{'mode':'live'}):
            def runner(command,**kwargs):
                if 'restart' in command:
                    receipt={'revision':revision,'pid':88,'readyAt':time.time(),'mode':'dry-run',**mismatch}
                    (self.store.directory/'agentfleet-ready.json').write_text(json.dumps(receipt))
                return types.SimpleNamespace(returncode=0,stdout='active\n88\n')
            self.store.runner=runner
            ticks=iter(range(1000))
            with self.subTest(mismatch=mismatch),patch.object(self.store,'capability',return_value=True),patch('backend.time.monotonic',side_effect=lambda:next(ticks)),patch('backend.time.sleep'),self.assertRaises(APIError) as error:
                self.store.apply('triage',{'expectedRevision':revision})
            self.assertEqual(error.exception.code,'readiness_failed');self.assertIsNone(self.store.public('triage')['appliedRevision'])
    def test_fractional_mib_preserves_exact_bytes(self):
        path=self.store.paths['triage']
        path.write_text(patch_toml(path.read_text(),{('attachments','max_bytes'):1234567}))
        d=self.draft();self.assertEqual(d['maxMB']*1048576,1234567)
        self.save()
        import tomllib
        self.assertEqual(tomllib.loads(path.read_text())['attachments']['max_bytes'],1234567)
    def test_live_proxy_schema_validation_does_not_need_console_credentials(self):
        d=self.draft();d.update(mode='live',proxyHost='proxy.example')
        with patch.dict('os.environ',{},clear=True):
            result=self.store.save('triage',{'settings':d,'expectedRevision':0})
        self.assertEqual(result['service']['settings']['mode'],'live')
    def test_pending_cannot_overwrite_new_external_config(self):
        raw=self.store.paths['triage'].read_text();meta=self.store.meta('triage')
        pending=self.store.directory/'triage.pending.json';pending.write_text(json.dumps({'config':raw,'meta':meta,'beforeFingerprint':'old'}))
        new=raw+'\n# newer external write\n';self.store.paths['triage'].write_text(new)
        self.store.recover('triage');self.assertEqual(self.store.paths['triage'].read_text(),new)

    def test_protected_fields_cannot_enter_settings(self):
        d=self.draft('nailhub');d['verified']=True
        with self.assertRaises(APIError):self.store.save('nailhub',{'settings':d,'expectedRevision':0})
    def test_http_security_and_assets(self):
        server=ConsoleServer(('127.0.0.1',0),self.store);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def req(method,path,body=None,headers=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5);connection.request(method,path,body,headers or {});response=connection.getresponse();result=(response.status,response.read());connection.close();return result
        status,body=req('GET','/api/bootstrap');self.assertEqual(status,200);token=json.loads(body)['csrfToken']
        self.assertEqual(req('GET','/api/bootstrap',headers={'Host':'attacker.invalid'})[0],403)
        self.assertEqual(req('GET','/../config/listener.example.toml')[0],404)
        self.assertEqual(req('POST','/api/services/triage/test','{}',{'Content-Type':'application/json'})[0],403)
        self.assertEqual(req('POST','/api/services/triage/test','{}',{'Content-Type':'application/json','X-CSRF-Token':token,'Origin':'https://attacker.invalid'})[0],403)
        self.assertEqual(req('POST','/api/services/triage/test','{}',{'Content-Type':'application/json','X-CSRF-Token':token,'Content-Length':'65537'})[0],413)
        status,body=req('POST','/api/services/triage/test',json.dumps({'settings':self.draft()}),{'Content-Type':'application/json','X-CSRF-Token':token});self.assertEqual(status,200);self.assertEqual(json.loads(body)['status'],'success')

class ProviderTests(unittest.TestCase):
    def test_real_http_failures_are_bounded_and_redacted(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Upstream(BaseHTTPRequestHandler):
            scenario='auth'
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                scenario=self.scenario
                if scenario=='partial_timeout':
                    self.send_response(200);self.send_header('Content-Length','1000');self.end_headers()
                    self.wfile.write(b'{"message":{"content":"{\\"ok\\":true}"}}');self.wfile.flush();time.sleep(2);return
                if scenario=='timeout':time.sleep(2)
                code={'auth':401,'model':404,'rate':429}.get(scenario,200)
                raw=b'SECRET-UPSTREAM-BODY' if code!=200 else b'{bad JSON'
                if scenario=='oversize':raw=b'x'*(1024*1024+2)
                try:
                    self.send_response(code);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Upstream);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        for scenario,expected in [('auth','auth_failed'),('model','model_not_found'),('rate','rate_limited'),('json','invalid_json'),('oversize','response_too_large'),('timeout','timeout'),('partial_timeout','timeout')]:
            Upstream.scenario=scenario
            with self.subTest(scenario=scenario),self.assertRaises(ProviderError) as error:
                complete('ollama',f'http://127.0.0.1:{server.server_port}','model',[],{},timeout=1)
            self.assertEqual(error.exception.code,expected);self.assertNotIn('SECRET',str(error.exception))

    def test_dns_rebinding_is_blocked_before_tls_connection(self):
        public=[(2,1,6,'',('8.8.8.8',443))];private=[(2,1,6,'',('169.254.169.254',443))]
        with patch('tg_triage.ai_transport.resolve',side_effect=[public,public,private]),patch('tg_triage.ai_transport.PinnedHTTPS') as connection,self.assertRaises(ProviderError) as error:
            complete('compatible','https://public.example/v1','model',[],{},key='test-key')
        self.assertEqual(error.exception.code,'invalid_endpoint');connection.assert_not_called()

    def test_local_image_input_above_cloud_budget_is_supported(self):
        messages=[{'role':'user','content':'image case','images':['a'*200000]}]
        calls=[]
        def upstream(*args):
            calls.append(args);return {'message':{'content':'{"ok":true}'}}
        self.assertEqual(complete('ollama','http://127.0.0.1:11434','model',messages,{},context=8192,transport=upstream),{'ok':True})
        self.assertEqual(len(calls[0][1]['messages'][0]['images'][0]),200000)
        self.assertEqual(calls[0][1]['options']['num_ctx'],8192)

    def test_real_loopback_transport_and_redirect_refusal(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        class Upstream(BaseHTTPRequestHandler):
            redirect=False
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                if self.redirect:
                    self.send_response(302);self.send_header('Location','https://api.openai.com/v1/chat/completions');self.end_headers();return
                raw=b'{"message":{"content":"{\\"ok\\":true}"}}'
                self.send_response(200);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Upstream);threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url='http://127.0.0.1:'+str(server.server_port)
        self.assertEqual(complete('ollama',url,'model',[],{},timeout=2),{'ok':True})
        Upstream.redirect=True
        with self.assertRaises(ProviderError):complete('ollama',url,'model',[],{},timeout=2)

    def test_identical_transport_packages(self):
        root=Path(__file__).resolve().parents[1]
        self.assertEqual((root/'listener/src/tg_triage/ai_transport.py').read_bytes(),(root/'nailhub/src/nailhub/ai_transport.py').read_bytes())
    def test_all_protocols_request_and_normalization(self):
        for provider,base in [('ollama','http://127.0.0.1:11434'),('anthropic','https://api.anthropic.com'),('openai','https://api.openai.com/v1'),('deepseek','https://api.deepseek.com'),('compatible','https://public.example/v1')]:
            calls=[]
            def upstream(url,body,headers,timeout,name):
                calls.append((url,body,headers))
                if name=='anthropic':return {'content':[{'type':'tool_use','name':'result','input':{'ok':True}}]}
                if name=='ollama':return {'message':{'content':'{"ok":true}'}}
                return {'choices':[{'message':{'content':'{"ok":true}'}}]}
            with patch('tg_triage.ai_transport.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]):
                self.assertEqual(complete(provider,base,'test-model',[{'role':'system','content':'JSON only'},{'role':'user','content':'probe'}],{'type':'object'},key='test-key',transport=upstream),{'ok':True})
            self.assertEqual(calls[0][1]['model'],'test-model')
            if provider=='anthropic':self.assertTrue(calls[0][0].endswith('/v1/messages'));self.assertEqual(calls[0][2]['x-api-key'],'test-key')
            elif provider!='ollama':self.assertTrue(calls[0][0].endswith('/chat/completions'));self.assertEqual(calls[0][2]['Authorization'],'Bearer test-key')
    def test_ssrf_and_invalid_urls_refused(self):
        for url in ('http://example.com','https://user:key@example.com','https://example.com?key=x','https://example.com:8443','https://example.com#x'):
            with self.subTest(url=url),self.assertRaises(ProviderError):validate_endpoint('compatible',url)
        for address in ('127.0.0.1','10.0.0.1','169.254.169.254','::1'):
            with patch('tg_triage.ai_transport.socket.getaddrinfo',return_value=[(2,1,6,'',(address,443))]),self.assertRaises(ProviderError):validate_endpoint('compatible','https://public.example')
    def test_invalid_json_and_thinking_fail_closed(self):
        with self.assertRaises(ProviderError):complete('openai','https://api.openai.com/v1','m',[],{},key='key',think=True)
        with self.assertRaises(ProviderError):complete('openai','https://api.openai.com/v1','m',[],{},key='key',transport=lambda *a:{'choices':[{'message':{'content':'[]'}}]})
    def test_cloud_runtime_uses_provider_and_redacts_triage_only(self):
        from dataclasses import replace
        from tg_triage.config import load_config
        from tg_triage.provider import build_provider
        from nailhub.config import NailHubConfig
        from nailhub.model import NailHubModel
        with tempfile.TemporaryDirectory() as directory:
            s=Store(directory);d=s.public('triage')['settings'];d.update(provider='openai',baseUrl='https://api.openai.com/v1',think=False,cloudConsent=True)
            s.save('triage',{'settings':d,'apiKey':'test-key','expectedRevision':0})
            config=load_config(s.paths['triage'],{'TG_API_ID':'1','TG_API_HASH':'0'*32});provider=build_provider(config)
            with patch('tg_triage.ai_transport.complete',return_value={'ok':True}) as call:
                result=provider._ask({'messages':[{'role':'user','content':'test@example.com +1 555 123 4567'}],'format':{}},json.loads)
            self.assertEqual(result,{'ok':True});args=call.call_args.args
            self.assertEqual(args[0],'openai');self.assertEqual(args[2],d['model'])
            self.assertNotIn('test@example.com',args[3][0]['content']);self.assertNotIn('555',args[3][0]['content'])
            keyfile=Path(directory)/'key.json';keyfile.write_text(json.dumps({'scope':'deepseek|https://api.deepseek.com','key':'test-key'}));keyfile.chmod(0o600)
            cfg=NailHubConfig('db','profile',provider='deepseek',base_url='https://api.deepseek.com',model='test-model',key_file=str(keyfile),cloud_consent=True)
            with patch('nailhub.ai_transport.complete',return_value={'ok':True}) as call:
                result=NailHubModel(cfg).ask({},'prompt',{'source':'test@example.com'})
            self.assertEqual(result,{'ok':True});self.assertEqual(call.call_args.args[0],'deepseek');self.assertIn('test@example.com',call.call_args.args[3][1]['content'])

    def test_cloud_runtime_consent_and_image_boundary(self):
        from tg_triage.config import load_config
        from tg_triage.provider import build_provider
        with tempfile.TemporaryDirectory() as directory:
            s=Store(directory);d=s.public('triage')['settings'];d.update(provider='openai',baseUrl='https://api.openai.com/v1',think=False)
            s.save('triage',{'settings':d,'apiKey':'test-key','expectedRevision':0})
            config=load_config(s.paths['triage'],{'TG_API_ID':'1','TG_API_HASH':'0'*32});provider=build_provider(config)
            self.assertIsNone(provider.classify('test@example.com +1 555 123 4567'))
            self.assertFalse(provider.supports_vision);self.assertTrue(provider.rejects_images)
        from nailhub.config import NailHubConfig
        from nailhub.model import NailHubModel
        from nailhub.validation import NeedsHuman
        with self.assertRaises(NeedsHuman):NailHubModel(NailHubConfig('db','profile',provider='openai')).ask({},'prompt',{})

if __name__=='__main__':unittest.main()
