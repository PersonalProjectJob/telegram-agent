"""Offline registry isolation, HTTP routing and loader-backed fallback round trips."""
import copy
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from agentfleet_console.registry import Registry,default_registry
from agentfleet_console.store import Store,APIError
from agentfleet_console.server import ConsoleServer

class RegistryTests(unittest.TestCase):
    def registrations(self):return [(e['descriptor'],e['adapter']) for e in default_registry().entries.values()]
    def test_invalid_and_duplicate_services_are_isolated(self):
        registrations=self.registrations();bad=copy.deepcopy(registrations[0][0]);bad.update(id='broken',schemaVersion=99)
        registry=Registry(registrations+[(bad,registrations[0][1])])
        with tempfile.TemporaryDirectory() as directory:
            result=Store(directory,registry=registry).bootstrap()
        self.assertTrue(result['services']['triage']['available']);self.assertFalse(result['services']['broken']['available'])
        self.assertNotIn('broken',result['descriptors']);self.assertFalse(result['services']['broken']['canSave'])
        duplicate=Registry(registrations+[registrations[0]])
        self.assertNotIn('triage',duplicate.entries);self.assertIn('nailhub',duplicate.entries)
        with self.assertRaises(ValueError):Registry([(dict(bad,id='BadId'),registrations[0][1])])
    def test_public_contract_contains_no_private_mapping_or_bindings(self):
        with tempfile.TemporaryDirectory() as directory:result=Store(directory).bootstrap()
        public=json.dumps(result)
        self.assertEqual(result['contractVersion'],1)
        for forbidden in ('/etc/','/opt/','mapping','tg-triage.service','sha256','tglistener'):self.assertNotIn(forbidden,public)
    def test_non_object_builtin_json_is_isolated(self):
        original=Path.read_text
        for invalid in ([],None,'text',42):
            def read(path,*args,**kwargs):
                if path.name=='triage.json':return json.dumps(invalid)
                return original(path,*args,**kwargs)
            with self.subTest(raw=invalid),patch.object(Path,'read_text',read):registry=default_registry()
            self.assertIn('triage',registry.invalid);self.assertIn('nailhub',registry.entries)
    def test_fallback_roundtrip_matches_unchanged_worker_loader(self):
        from tg_triage.config import load_config
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory);service=store.public('triage');settings=service['settings']
            settings.update(provider='deepseek',baseUrl='https://api.deepseek.com',think=False,cloudConsent=True,fallback='ollama',fallbackModel='qwen-fixture',fallbackThink=False)
            result=store.save('triage',{'settings':settings,'expectedRevision':service['revision'],'apiKey':'fixture-key','contractRevision':service['contractRevision']})
            settings=result['service']['settings'];settings['fallback']='none'
            result=store.save('triage',{'settings':settings,'expectedRevision':result['service']['revision']})
            self.assertEqual(result['service']['settings']['fallbackModel'],'qwen-fixture')
            config=load_config(store.paths['triage'],validate_only=True)
            self.assertEqual(config.model.fallback,'none');self.assertEqual(config.model.fallback_model,'qwen-fixture');self.assertFalse(config.model.fallback_think)
            reload=Store(directory).public('triage')['settings'];self.assertEqual(reload,result['service']['settings'])
            nail=store.public('nailhub');bad={**nail['settings'],'fallback':'ollama'}
            with self.assertRaises(APIError):store.save('nailhub',{'settings':bad,'expectedRevision':nail['revision']})
    def test_stale_contract_before_effects_legacy_omitted_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory);service=store.public('triage');before={p.name:p.read_bytes() for p in Path(directory).rglob('*') if p.is_file()}
            for action,body in [('save',{'settings':service['settings'],'expectedRevision':0}),('test',{'settings':service['settings']}),('apply',{'expectedRevision':0})]:
                with self.subTest(action=action),self.assertRaises(APIError) as error:getattr(store,action)('triage',{**body,'contractRevision':'stale'})
                self.assertEqual(error.exception.status,409)
            self.assertEqual(before,{p.name:p.read_bytes() for p in Path(directory).rglob('*') if p.is_file()})
            store.save('triage',{'settings':service['settings'],'expectedRevision':0})
    def test_probe_capability_does_not_require_settings_mutation(self):
        descriptor,adapter=self.registrations()[0];descriptor=copy.deepcopy(descriptor);descriptor['capabilities']['settings']=False
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory,registry=Registry([(descriptor,adapter)]),transport=lambda *args:{'message':{'content':'{"ok":true}'}})
            service=store.public('triage');self.assertFalse(service['canSave'])
            self.assertEqual(store.test('triage',{'settings':service['settings']})['status'],'success')
            with self.assertRaises(APIError) as error:store.save('triage',{'settings':service['settings'],'expectedRevision':0})
            self.assertEqual(error.exception.status,404)
    def test_generic_http_routes_and_capabilities(self):
        with tempfile.TemporaryDirectory() as directory:
            store=Store(directory);server=ConsoleServer(('127.0.0.1',0),store);threading.Thread(target=server.serve_forever,daemon=True).start()
            try:
                def request(method,path,body=None):
                    connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
                    connection.request(method,path,json.dumps(body) if body is not None else None,{'Content-Type':'application/json','X-CSRF-Token':server.csrf})
                    response=connection.getresponse();result=response.status,json.loads(response.read());connection.close();return result
                self.assertEqual(request('GET','/api/services/triage/usage')[1],request('GET','/api/usage')[1])
                for path in ('/api/services/nailhub/usage','/api/services/missing/usage','/api/services/Bad/usage'):self.assertEqual(request('GET',path)[0],404)
                self.assertEqual(request('POST','/api/services/missing/save',{})[0],404)
                status,body=request('POST','/api/services/triage/apply',{'contractRevision':'stale','expectedRevision':0});self.assertEqual(status,409);self.assertEqual(body['error']['code'],'contract_conflict')
            finally:server.shutdown();server.server_close()

if __name__=='__main__':unittest.main()
