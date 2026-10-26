"""HTTP proof that source registration adds a worker without core route changes.

Every server uses TEMP state. The providerless adapter never opens network, executes
service commands, or touches installed workers. Browser runners may import
fixture_registry and start ConsoleServer against their own TemporaryDirectory.
"""
import copy
import hashlib
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import tomllib
import unittest

CONSOLE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CONSOLE/'backend'))
from agentfleet_console.registry import Registry, default_registry
from agentfleet_console.server import ConsoleServer
from agentfleet_console.store import Store
from agentfleet_console.workers.adapters import Binding


def fixture_descriptor():
    fields = [
        {'id':'name','type':'string','label':'Tên fixture','default':'Fixture worker','maxLength':80,'widget':'text'},
        {'id':'enabled','type':'boolean','label':'Bật fixture','default':True,'widget':'toggle'},
        {'id':'tags','type':'list','itemType':'string','maxItems':4,'maxLength':40,'label':'Nhãn fixture','default':['offline'],'widget':'textarea','visibleWhen':{'equals':{'field':'enabled','value':True}}},
    ]
    for field in fields:
        field.update(group='settings',section='basic',help='Dữ liệu thử nghiệm local, không chạy worker.')
    return {
        'id':'fixture','displayName':'Fixture Worker','description':'Worker fixture thứ ba; chỉ lưu cấu hình local.',
        'schemaVersion':1,'contractRevision':'fixture-contract-1',
        'capabilities':{'settings':True,'probe':False,'usage':False},
        'groups':[{'id':'settings','label':'Thiết lập','sections':[{'id':'basic','title':'Fixture settings','description':'Kiểm chứng đăng ký worker không sửa router hoặc renderer.'}]}],
        'providers':[], 'fields':fields,
        'mapping':{field['id']:{'section':'fixture','key':field['id'],'codec':'identity'} for field in fields},
    }


class FixtureAdapter:
    def __init__(self, directory):
        self.binding=Binding(directory/'installed-fixture.toml',directory/'private','fixture-never-run.service','fixture',directory/'venv','fixture')

    def initial(self,directory):
        return '[fixture]\nname = "Fixture worker"\nenabled = true\ntags = ["offline"]\n\n[protected]\nsentinel = "keep-me"\n', {}

    def settings(self,descriptor,data):
        return {field['id']:copy.deepcopy(data['fixture'][field['id']]) for field in descriptor['fields']}

    def updates(self,descriptor,settings):
        return {(entry['section'],entry['key']):copy.deepcopy(settings[name]) for name,entry in descriptor['mapping'].items()}

    def validate(self,settings):
        pass

    def validate_candidate(self,path):
        data=tomllib.loads(path.read_text(encoding='utf-8'))
        if data['protected']['sentinel']!='keep-me':
            raise ValueError('Protected fixture sentinel changed.')

    def db_path(self,data):
        return None

    def receipt(self,data):
        raise AssertionError('Providerless local fixture must not read readiness receipts.')

    def probe(self,*args):
        raise AssertionError('Probe capability is disabled.')


def fixture_registry(directory):
    registry=default_registry()
    registrations=[(entry['descriptor'],entry['adapter']) for entry in registry.entries.values()]
    return Registry(registrations+[(fixture_descriptor(),FixtureAdapter(Path(directory)))])


class RegistryHTTPIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory(prefix='agentfleet registry http ')
        self.addCleanup(temp.cleanup)
        self.directory=Path(temp.name)
        self.registry=fixture_registry(self.directory)
        self.store=Store(self.directory,registry=self.registry)
        self.server=ConsoleServer(('127.0.0.1',0),self.store)
        thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        thread.start()
        def stop():
            self.server.shutdown();self.server.server_close();thread.join(timeout=5)
        self.addCleanup(stop)
        self.port=self.server.server_port
        self.bootstrap=self.request('/api/bootstrap')

    def request(self,path,body=None,status=200,csrf=True):
        headers={'Content-Type':'application/json'}
        if body is not None and csrf:
            headers['X-CSRF-Token']=self.server.csrf
        connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        try:
            connection.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
            response=connection.getresponse();raw=response.read()
            self.assertEqual(response.status,status,raw.decode(errors='replace'))
            return json.loads(raw) if path.startswith('/api/') else raw
        finally:
            connection.close()

    def body(self,settings=None):
        service=self.bootstrap['services']['fixture']
        return {'settings':copy.deepcopy(settings if settings is not None else service['settings']),
                'expectedRevision':service['revision'],'contractRevision':service['contractRevision']}

    def state_hashes(self):
        return {str(path.relative_to(self.directory)):hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.directory.rglob('*') if path.is_file()}

    def test_third_worker_descriptor_and_primitive_list_save_reload(self):
        self.assertEqual(set(self.bootstrap['services']),{'triage','nailhub','fixture'})
        public=self.bootstrap['descriptors']['fixture']
        self.assertEqual(public['displayName'],'Fixture Worker')
        self.assertEqual(public['providers'],[])
        self.assertNotIn('mapping',public)
        self.assertNotIn('unit',json.dumps(public))
        self.assertTrue(self.bootstrap['services']['fixture']['canSave'])
        settings={'name':'Local fixture saved','enabled':False,'tags':['retain-hidden','two']}
        result=self.request('/api/services/fixture/save',self.body(settings))
        self.assertEqual(result['service']['settings'],settings)
        self.assertFalse((self.directory/'secrets').exists())
        reloaded=Store(self.directory,registry=self.registry).bootstrap()
        self.assertEqual(reloaded['services']['fixture']['settings'],settings)
        self.assertEqual(reloaded['services']['fixture']['revision'],result['service']['revision'])
        self.assertEqual(tomllib.loads((self.directory/'fixture.toml').read_text())['protected']['sentinel'],'keep-me')
        self.assertEqual(self.request('/api/bootstrap')['services']['fixture']['settings'],settings)

    def test_stale_contract_and_unknown_worker_reject_without_state_effect(self):
        before=self.state_hashes()
        body=self.body();body['contractRevision']='stale-contract'
        response=self.request('/api/services/fixture/save',body,status=409)
        self.assertEqual(response['error']['code'],'contract_conflict')
        for action in ('save','apply','test'):
            self.request('/api/services/not_registered/'+action,{},status=404)
        self.request('/api/services/not_registered/usage',status=404)
        self.assertEqual(self.state_hashes(),before)

    def test_providerless_capability_csrf_and_hidden_values_authoritative_validation(self):
        before=self.state_hashes()
        self.request('/api/services/fixture/test',self.body(),status=404)
        self.request('/api/services/fixture/usage',status=404)
        self.request('/api/services/fixture/save',self.body(),status=403,csrf=False)
        invalid=self.body({'name':'Hidden invalid','enabled':False,'tags':['a','b','c','d','e']})
        response=self.request('/api/services/fixture/save',invalid,status=400)
        self.assertEqual(response['error']['field'],'tags')
        self.assertEqual(self.state_hashes(),before)

    def test_legacy_optional_contract_and_stale_revision_are_preserved(self):
        body=self.body();body.pop('contractRevision')
        result=self.request('/api/services/fixture/save',body)
        self.assertNotEqual(result['service']['revision'],body['expectedRevision'])
        before=self.state_hashes()
        response=self.request('/api/services/fixture/save',body,status=409)
        self.assertEqual(response['error']['code'],'revision_conflict')
        self.assertEqual(self.state_hashes(),before)


if __name__=='__main__':
    unittest.main()
