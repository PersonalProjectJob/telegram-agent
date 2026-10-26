"""Canonical package inventory and no-write installed denial, using TEMP only."""
import copy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

CONSOLE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(CONSOLE/'backend'))
from agentfleet_console.compatibility import Compatibility,MANIFEST_PATH
from agentfleet_console.registry import Registry,default_registry
from agentfleet_console.store import Store,APIError,atomic,patch_toml
from agentfleet_console.policy import verify_binding,PROPERTIES

def unit_output(binding,explicit_config=True,**changes):
    interpreter=(binding.venv/'bin/python').as_posix()
    argv=interpreter+' -m '+binding.package+' run'
    if explicit_config:argv+=' --config '+binding.config.as_posix()
    values={'LoadState':'loaded','ExecStart':'{ path='+interpreter+' ; argv[]='+argv+' ; ignore_errors=no ; start_time=[n/a] ; stop_time=[n/a] ; pid=0 ; code=(null) ; status=0/0 }','User':binding.service_user,'Group':binding.service_group,'SupplementaryGroups':binding.group,'DropInPaths':'','Environment':'','EnvironmentFiles':'','WorkingDirectory':binding.working_directory.as_posix()}
    if binding.config_env:values['Environment']=binding.config_env+'='+binding.config.as_posix()
    values.update(changes)
    return '\n'.join(key+'='+values[key] for key in PROPERTIES)

class CompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest=json.loads(MANIFEST_PATH.read_text())
        cls.payloads={}
        for id,package,prefix in [('triage','tg_triage','listener'),('nailhub','nailhub','nailhub')]:
            files=cls.manifest['workers'][id]['files'];cls.payloads[id]={}
            for relative,spec in files.items():
                source=prefix+'/src/'+package+'/'+relative.removeprefix('package/') if relative.startswith('package/') else 'listener/prompts/'+relative.removeprefix('data/')
                raw=subprocess.check_output(['git','show',cls.manifest['gitCommit']+':'+source],cwd=CONSOLE.parent)
                assert len(raw)==spec['size'] and hashlib.sha256(raw).hexdigest()==spec['sha256']
                cls.payloads[id][relative]=raw
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        raw=default_registry().entries['triage'];self.adapter=copy.copy(raw['adapter'])
        self.adapter.binding=replace(self.adapter.binding,venv=self.root/'venv',config=self.root/'etc'/'config.toml',private=self.root/'etc'/'private',working_directory=self.root/'cwd')
        self.binding=self.adapter.binding;self.binding.private.mkdir(parents=True);self.binding.working_directory.mkdir()
        self.registry=Registry([(raw['descriptor'],self.adapter)]);self.entry=self.registry.entries['triage'];self.package=self.binding.venv/'lib/python3.11/site-packages/tg_triage'
        for relative,data in self.payloads['triage'].items():
            target=self.package/relative.removeprefix('package/') if relative.startswith('package/') else self.binding.venv/self.binding.data/relative.removeprefix('data/')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        text,updates=self.adapter.initial(self.root);self.binding.config.write_text(patch_toml(text,updates),encoding='utf-8')
        self.policy=Compatibility()
    def check(self):return self.policy.check('triage',self.entry)
    def snapshot(self):return {str(p.relative_to(self.root)):p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
    def test_manifest_complete_canonical_inventory_and_prompts(self):
        for id,prefix,package in [('triage','listener','tg_triage'),('nailhub','nailhub','nailhub')]:
            names=subprocess.check_output(['git','ls-tree','-r','--name-only',self.manifest['gitCommit'],prefix+'/src/'+package],text=True,cwd=CONSOLE.parent).splitlines()
            expected={'package/'+name.split('/src/'+package+'/')[1] for name in names if name.endswith('.py')}
            if id=='triage':expected|={'data/'+name for name in ('classify.md','case.md','topics.md')}
            self.assertEqual(expected,set(self.manifest['workers'][id]['files']))
        self.assertEqual(self.check()['state'],'verified')
    def test_modified_missing_extra_and_prompt_change_denied(self):
        target=self.package/'config.py';raw=target.read_bytes()
        for mutation in ('modified','missing','extra','prompt'):
            with self.subTest(mutation=mutation):
                if mutation=='modified':target.write_bytes(raw+b'\n')
                elif mutation=='missing':target.unlink()
                elif mutation=='extra':(self.package/'unexpected.py').write_text('')
                else:(self.binding.venv/self.binding.data/'case.md').write_text('changed')
                self.assertEqual(self.check()['state'],'mismatch')
                target.write_bytes(raw);(self.package/'unexpected.py').unlink(missing_ok=True)
    def test_cache_ignored_but_ambiguity_and_reparse_denied(self):
        cache=self.package/'__pycache__';cache.mkdir();(cache/'any.pyc').write_bytes(b'cache')
        self.assertEqual(self.check()['state'],'verified')
        other=self.binding.venv/'lib/python3.12/site-packages/tg_triage';other.mkdir(parents=True)
        self.assertEqual(self.check()['state'],'unverified');other.rmdir()
        if os.name=='nt':
            junction=self.package/'redirect'
            result=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(self.binding.private)],capture_output=True)
            self.assertEqual(result.returncode,0)
            self.addCleanup(lambda:os.rmdir(junction) if junction.exists() else None)
        else:
            junction=self.package/'redirect';junction.symlink_to(self.binding.private,target_is_directory=True)
        self.assertEqual(self.check()['state'],'unverified')
    def test_unreadable_file_and_binding_are_unverified(self):
        original=os.open
        def denied(path,*args,**kwargs):
            if Path(path).name=='config.py':raise PermissionError()
            return original(path,*args,**kwargs)
        with patch('agentfleet_console.compatibility.os.open',side_effect=denied):self.assertEqual(self.check()['state'],'unverified')
        self.binding.config.unlink();self.assertEqual(self.check()['state'],'unverified')
    def test_constructor_bootstrap_mismatch_never_replay_or_mutate_installed(self):
        state=self.root/'state';state.mkdir();pending=state/'triage.pending.json'
        pending.write_text(json.dumps({'config':'mode="live"','meta':{},'beforeFingerprint':hashlib.sha256(self.binding.config.read_bytes()).hexdigest()}))
        (self.package/'config.py').write_text('tampered')
        before=self.snapshot();store=Store(state,installed=True,registry=self.registry)
        result=store.bootstrap();self.assertFalse(result['services']['triage']['canSave'])
        self.assertEqual(result['services']['triage']['compatibility']['state'],'mismatch')
        self.assertEqual(before,self.snapshot());self.assertFalse(self.binding.config.with_name('config.toml.console.lock').exists())
        for action in ('save','apply'):
            with self.subTest(action=action),patch('agentfleet_console.store.atomic') as write,patch.object(store,'runner') as runner,self.assertRaises(APIError):
                getattr(store,action)('triage',{'settings':result['services']['triage']['settings'],'expectedRevision':0})
            write.assert_not_called();runner.assert_not_called();self.assertEqual(before,self.snapshot())
        with self.assertRaises(APIError):store.recover('triage')
        self.assertEqual(before,self.snapshot())
    def test_action_rechecks_identity_after_status_and_under_lock(self):
        store=Store(self.root/'state',installed=True,registry=self.registry,runner=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding)))
        with patch('agentfleet_console.policy.sys.platform','linux'):service=store.public('triage')
        self.assertEqual(service['compatibility']['state'],'verified')
        (self.package/'config.py').write_text('tamper-after-status')
        with patch('agentfleet_console.store.atomic') as write,self.assertRaises(APIError):store.save('triage',{'settings':service['settings'],'expectedRevision':0})
        write.assert_not_called()
        results=[{'state':'verified','reason':'test'},{'state':'mismatch','reason':'changed'}]
        with patch('agentfleet_console.policy.sys.platform','linux'),patch.object(store.compatibility,'check',side_effect=results) as check,patch('agentfleet_console.store.atomic') as write,self.assertRaises(APIError) as error:
            store.save('triage',{'settings':service['settings'],'expectedRevision':0})
        self.assertEqual(error.exception.code,'compatibility_required');self.assertEqual(check.call_count,2);write.assert_not_called()
    def test_incompatible_contract_revision_cannot_use_manifest(self):
        entry=copy.deepcopy(self.entry);entry['public']['contractRevision']='new-contract'
        self.assertEqual(self.policy.check('triage',entry)['state'],'unverified')
    def test_effective_unit_binding_accepts_exact_command_and_rejects_swaps(self):
        calls=[]
        def runner(command,**kwargs):calls.append(command);return types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding))
        with patch('agentfleet_console.policy.sys.platform','linux'):verify_binding(self.binding,runner)
        self.assertEqual(calls[0][:3],['systemctl','show',self.binding.unit]);self.assertNotIn('restart',calls[0])
        baseline=unit_output(self.binding)
        bad_outputs=[baseline.replace(self.binding.config.as_posix(),'/tmp/other.toml'),baseline.replace((self.binding.venv/'bin/python').as_posix(),'/tmp/python'),baseline.replace('-m tg_triage run','-m other run'),unit_output(self.binding,DropInPaths='/etc/systemd/system/override.conf'),unit_output(self.binding,User='root'),unit_output(self.binding,Group='root'),unit_output(self.binding,Environment='PYTHONPATH=/tmp/unreviewed'),unit_output(self.binding,Environment='PYTHONHOME=/tmp/other'),unit_output(self.binding,Environment='LD_PRELOAD=/tmp/inject.so'),unit_output(self.binding,EnvironmentFiles='/etc/secret.env (ignore_errors=no)'),unit_output(self.binding,WorkingDirectory='/tmp/other'),baseline+'\nUser=root',baseline.replace('ExecStart={','ExecStart=unknown {'),baseline.replace(' run',' run ; /bin/sh'),baseline+'\nUnexpected=secret','x'*16385]
        for output in bad_outputs:
            with self.subTest(output=output[:80]),patch('agentfleet_console.policy.sys.platform','linux'),self.assertRaises(APIError) as error:
                verify_binding(self.binding,lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=output))
            self.assertEqual(error.exception.code,'binding_unverified');self.assertNotIn('/tmp/',error.exception.message)
        shadow=self.binding.working_directory/self.binding.package;shadow.mkdir()
        with patch('agentfleet_console.policy.sys.platform','linux'),self.assertRaises(APIError):verify_binding(self.binding,runner)
        shadow.rmdir()
    def test_nailhub_envfile_is_ambiguous_without_explicit_config(self):
        binding=replace(default_registry().entries['nailhub']['adapter'].binding,working_directory=self.binding.working_directory)
        for explicit,envfiles,success in [(False,'',True),(False,'/etc/agentfleet/tg-nailhub.env (ignore_errors=no)',False),(True,'/etc/agentfleet/tg-nailhub.env (ignore_errors=no)',False)]:
            runner=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=unit_output(binding,explicit_config=explicit,EnvironmentFiles=envfiles))
            with self.subTest(explicit=explicit,envfiles=envfiles),patch('agentfleet_console.policy.sys.platform','linux'):
                if success:verify_binding(binding,runner)
                else:
                    with self.assertRaises(APIError):verify_binding(binding,runner)
    def test_binding_mismatch_blocks_pending_recovery_and_save(self):
        state=self.root/'state';state.mkdir();pending=state/'triage.pending.json'
        pending.write_text(json.dumps({'config':'mode="live"','meta':{},'beforeFingerprint':hashlib.sha256(self.binding.config.read_bytes()).hexdigest()}))
        store=Store(state,installed=True,registry=self.registry,runner=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding,User='root')))
        before=self.snapshot();settings=store.public('triage')['settings']
        with patch('agentfleet_console.policy.sys.platform','linux'),patch('agentfleet_console.store.atomic') as write:
            for action in (lambda:store.recover('triage'),lambda:store.save('triage',{'settings':settings,'expectedRevision':0})):
                with self.assertRaises(APIError) as error:action()
                self.assertEqual(error.exception.code,'binding_unverified')
            write.assert_not_called()
        after=self.snapshot();after.pop('etc/config.toml.console.lock',None)
        self.assertEqual(before,after);self.assertTrue(pending.exists())
    def test_candidate_validation_source_change_is_denied_before_transaction(self):
        runner=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding))
        store=Store(self.root/'state',installed=True,registry=self.registry,runner=runner);settings=store.public('triage')['settings']
        before=self.binding.config.read_bytes()
        def validation(path):self.adapter.loader(path);(self.package/'config.py').write_text('changed-during-validation')
        with patch('agentfleet_console.policy.sys.platform','linux'),patch.object(self.adapter,'validate_candidate',side_effect=validation),patch('agentfleet_console.store.atomic') as write,self.assertRaises(APIError) as error:
            store.save('triage',{'settings':settings,'expectedRevision':0})
        self.assertEqual(error.exception.code,'compatibility_required');write.assert_not_called()
        self.assertEqual(before,self.binding.config.read_bytes());self.assertFalse((store.directory/'triage.pending.json').exists());self.assertFalse(store.meta_path('triage').exists())
    def test_installed_fixture_save_apply_and_final_binding_recheck(self):
        calls=[];revision=None;binding_reads=0;deny_at=None
        def runner(command,**kwargs):
            nonlocal binding_reads
            calls.append(command)
            if command[1]=='show' and command[-1].startswith('--property=LoadState'):
                binding_reads+=1
                return types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding,User='root' if deny_at==binding_reads else self.binding.service_user))
            if 'restart' in command:(self.root/'agentfleet-ready.json').write_text(json.dumps({'revision':revision,'pid':88,'readyAt':__import__('time').time(),'mode':'dry-run'}))
            return types.SimpleNamespace(returncode=0,stdout='active\n88\n')
        store=Store(self.root/'state',installed=True,registry=self.registry,runner=runner);settings=store.public('triage')['settings'];settings['timeout']=601
        fakegrp=types.SimpleNamespace(getgrnam=lambda name:types.SimpleNamespace(gr_gid=0))
        # The fixture writer preserves the real atomic transaction while omitting
        # OS chown: no production group or systemctl invocation occurs in tests.
        with patch.dict(sys.modules,{'grp':fakegrp}),patch('agentfleet_console.policy.sys.platform','linux'),patch('agentfleet_console.store.atomic',side_effect=lambda path,data,group=None:atomic(path,data)),patch.object(store,'capability',return_value=True):
            saved=store.save('triage',{'settings':settings,'expectedRevision':0});revision=saved['service']['revision']
            self.assertEqual(saved['service']['settings']['timeout'],601)
            binding_reads=0;deny_at=3;before=store.meta_path('triage').read_bytes()
            with self.assertRaises(APIError) as error:store.apply('triage',{'expectedRevision':revision})
            self.assertEqual(error.exception.code,'binding_unverified');self.assertFalse(any('restart' in cmd for cmd in calls));self.assertEqual(before,store.meta_path('triage').read_bytes())
            deny_at=None;applied=store.apply('triage',{'expectedRevision':revision})
        self.assertEqual(applied['service']['appliedRevision'],revision)
        self.assertEqual([cmd for cmd in calls if 'restart' in cmd],[['systemctl','--no-ask-password','restart','tg-triage.service']])
    def test_installed_external_edit_reload_allows_guarded_save_without_observation_writes(self):
        runner=lambda *a,**k:types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding))
        store=Store(self.root/'state',installed=True,registry=self.registry,runner=runner)
        fakegrp=types.SimpleNamespace(getgrnam=lambda name:types.SimpleNamespace(gr_gid=0))
        with patch.dict(sys.modules,{'grp':fakegrp}),patch('agentfleet_console.policy.sys.platform','linux'),patch('agentfleet_console.store.atomic',side_effect=lambda path,data,group=None:atomic(path,data)):
            service=store.public('triage');saved=store.save('triage',{'settings':service['settings'],'expectedRevision':0})['service']
            self.binding.config.write_text(self.binding.config.read_text()+'\n# external edit one\n',encoding='utf-8')
            before=self.snapshot();observed=store.bootstrap()['services']['triage'];self.assertEqual(before,self.snapshot())
            self.assertNotEqual(observed['revision'],saved['revision']);self.assertIsNone(observed['appliedRevision'])
            with self.assertRaises(APIError):store.save('triage',{'settings':observed['settings'],'expectedRevision':saved['revision']})
            self.assertEqual(before,self.snapshot())
            updated=store.save('triage',{'settings':observed['settings'],'expectedRevision':observed['revision']})['service']
            self.assertIn('# external edit one',self.binding.config.read_text())
            self.assertNotEqual(updated['revision'],observed['revision'])
            self.binding.config.write_text(self.binding.config.read_text()+'\n# external edit two\n',encoding='utf-8');observed=store.public('triage')
            self.binding.config.write_text(self.binding.config.read_text()+'\n# changed after reload\n',encoding='utf-8');before=self.snapshot()
            with self.assertRaises(APIError):store.save('triage',{'settings':observed['settings'],'expectedRevision':observed['revision']})
            self.assertEqual(before,self.snapshot())
    def test_bootstrap_effective_binding_disables_actions_with_sanitized_reason(self):
        for changes in ({'EnvironmentFiles':'/etc/secret-runtime.env (ignore_errors=no)'},{'Environment':'PYTHONPATH=/tmp/unreviewed-source'},{'WorkingDirectory':'/tmp/other-source'}):
            calls=[]
            def runner(command,**kwargs):calls.append(command);return types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding,**changes))
            store=Store(self.root/'state',installed=True,registry=self.registry,runner=runner)
            pending=store.directory/'triage.pending.json';pending.write_text('{"config":"must not recover"}')
            before=self.snapshot()
            with self.subTest(changes=changes),patch('agentfleet_console.policy.sys.platform','linux'),patch('agentfleet_console.store.atomic') as write:
                result=store.bootstrap();service=result['services']['triage']
            self.assertEqual(service['compatibility']['state'],'unverified');self.assertFalse(service['canSave']);self.assertFalse(service['canApply']);self.assertFalse(result['host']['canApply'])
            self.assertEqual(service['compatibility']['reason'],'Effective worker configuration binding could not be verified.')
            self.assertNotIn('/etc/',json.dumps(result));self.assertNotIn('/tmp/',json.dumps(result))
            self.assertEqual(len(calls),1);self.assertEqual(calls[0][:3],['systemctl','show',self.binding.unit]);write.assert_not_called()
            self.assertEqual(before,self.snapshot());self.assertFalse(self.binding.config.with_name('config.toml.console.lock').exists())
    def test_bootstrap_verified_binding_uses_single_readonly_snapshot_for_flags(self):
        calls=[]
        def runner(command,**kwargs):calls.append(command);return types.SimpleNamespace(returncode=0,stdout=unit_output(self.binding))
        store=Store(self.root/'state',installed=True,registry=self.registry,runner=runner);before=self.snapshot();exists=Path.exists
        def systemd(path):return True if path.as_posix()=='/run/systemd/system' else exists(path)
        with patch('agentfleet_console.policy.sys.platform','linux'),patch.object(Path,'exists',systemd),patch('agentfleet_console.store.atomic') as write:
            result=store.bootstrap();service=result['services']['triage']
        self.assertEqual(service['compatibility']['state'],'verified');self.assertTrue(service['canSave']);self.assertTrue(service['canApply']);self.assertTrue(result['host']['canApply'])
        self.assertEqual(len(calls),1);write.assert_not_called();self.assertEqual(before,self.snapshot())
        self.assertFalse(self.binding.config.with_name('config.toml.console.lock').exists())
    def test_bootstrap_hash_mismatch_skips_binding_runner_and_keeps_every_file(self):
        store=Store(self.root/'state',installed=True,registry=self.registry,runner=lambda *a,**k:self.fail('Hash mismatch must not inspect systemd'))
        pending=store.directory/'triage.pending.json';pending.write_text('{"config":"must not recover"}')
        (self.package/'config.py').write_text('tampered-source');before=self.snapshot()
        with patch('agentfleet_console.store.atomic') as write:
            result=store.bootstrap();service=result['services']['triage']
        self.assertEqual(service['compatibility']['state'],'mismatch');self.assertFalse(service['canSave']);self.assertFalse(service['canApply']);write.assert_not_called()
        self.assertEqual(before,self.snapshot());self.assertFalse(self.binding.config.with_name('config.toml.console.lock').exists())

if __name__=='__main__':unittest.main()
