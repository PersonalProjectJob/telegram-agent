"""Release drift tests mutate TEMP copies only; no worker or metadata writes."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import types
from unittest.mock import patch

CONSOLE=Path(__file__).resolve().parents[1]
ROOT=CONSOLE.parent
sys.path.insert(0,str(CONSOLE/'backend'))
from agentfleet_console.contracts import BASE,MANIFEST,REVIEW,DESCRIPTORS,REQUIRED_MODULES,WORKER_ROOTS,PROMPTS,PINNED_COMMIT,ContractError,check_source,check_archive

def semantic(raw):return hashlib.sha256(json.dumps(raw,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

class DriftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files={path:(ROOT/path).read_bytes() for path in (MANIFEST,REVIEW,*REQUIRED_MODULES)}
        for relative in (*WORKER_ROOTS.values(),PROMPTS,DESCRIPTORS):
            for path in (ROOT/relative).rglob('*'):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix!='.pyc':cls.files[path.relative_to(ROOT).as_posix()]=path.read_bytes()
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'source with spaces';self.root.mkdir()
        for relative,raw in self.files.items():
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    def snapshot(self):return {path.relative_to(self.root).as_posix():path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
    def test_current_source_and_pinned_git_provenance(self):
        result=check_source(ROOT)
        self.assertEqual(result['gitCommit'],PINNED_COMMIT);self.assertEqual(result['workers']['triage']['files'],23);self.assertEqual(result['workers']['nailhub']['files'],21)
    def test_no_git_source_and_archive_are_readonly_and_need_no_subprocess(self):
        before=self.snapshot()
        with patch('agentfleet_console.contracts.subprocess.run',side_effect=AssertionError('No Git means no subprocess')):
            result=check_source(self.root);archived=check_archive(copy.deepcopy(self.files))
        self.assertEqual(result,archived);self.assertEqual(before,self.snapshot())
    def test_checkout_provenance_failure_does_not_fall_back_or_write(self):
        (self.root/'.git').write_text('gitdir: missing-fixture-repository\n');before=self.snapshot()
        with patch('agentfleet_console.contracts.subprocess.run',return_value=types.SimpleNamespace(returncode=1,stdout=b'',stderr=b'SECRET')) as command,self.assertRaises(ContractError) as error:check_source(self.root)
        self.assertNotIn('SECRET',str(error.exception));self.assertEqual(command.call_count,1);self.assertEqual(before,self.snapshot())
    def test_third_descriptor_requires_explicit_review_but_no_worker_package(self):
        files=copy.deepcopy(self.files);raw=json.loads(files[DESCRIPTORS+'triage.json']);raw.update(id='fixture',contractRevision='fixture-reviewed-v1')
        relative=DESCRIPTORS+'fixture.json';files[relative]=json.dumps(raw).encode()
        with self.assertRaises(ContractError):check_archive(files)
        review=json.loads(files[REVIEW]);review['descriptors'][relative]={'id':'fixture','contractRevision':raw['contractRevision'],'sha256':semantic(raw)}
        module=BASE+'workers/fixture.py';files[module]=b'"""Trusted fixture adapter only."""\n';review['reviewedModules'][module]=hashlib.sha256(files[module]).hexdigest();files[REVIEW]=json.dumps(review).encode()
        result=check_archive(files);self.assertEqual(result['workers']['fixture'],{'contractRevision':'fixture-reviewed-v1','files':0})
    def test_worker_config_receipt_usage_provider_and_prompt_drift_fail(self):
        paths=['listener/src/tg_triage/config.py','listener/src/tg_triage/receipt.py','listener/src/tg_triage/usage.py','listener/src/tg_triage/provider.py','nailhub/src/nailhub/config.py','nailhub/src/nailhub/receipt.py','nailhub/src/nailhub/model.py','listener/prompts/case.md']
        for relative in paths:
            path=self.root/relative;original=path.read_bytes();path.write_bytes(original+b'\n# fixture drift\n');before=self.snapshot()
            with self.subTest(path=relative),self.assertRaises(ContractError):check_source(self.root)
            self.assertEqual(before,self.snapshot());path.write_bytes(original)
    def test_exact_worker_extra_missing_and_archive_missing_usage(self):
        extra=self.root/'listener/src/tg_triage/extra.py';extra.write_text('# fixture')
        with self.assertRaises(ContractError):check_source(self.root)
        extra.unlink();missing=self.root/'listener/src/tg_triage/usage.py';missing.unlink()
        with self.assertRaises(ContractError):check_source(self.root)
        files=copy.deepcopy(self.files);files.pop('listener/src/tg_triage/usage.py')
        with self.assertRaises(ContractError):check_archive(files)
    def test_windows_crlf_canonicalization_preserves_exact_semantics(self):
        for path in self.root.rglob('*'):
            if path.is_file() and path.suffix in ('.py','.md'):
                raw=path.read_bytes().replace(b'\r\n',b'\n');path.write_bytes(raw.replace(b'\n',b'\r\n'))
        self.assertEqual(check_source(self.root)['status'],'verified')
    def test_descriptor_and_shared_modules_require_explicit_review(self):
        relative=DESCRIPTORS+'triage.json';path=self.root/relative;raw=json.loads(path.read_text(encoding='utf-8'));raw['fields'][0]['label']='Fixture reviewed change';path.write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
        with self.assertRaises(ContractError):check_source(self.root)
        record_path=self.root/REVIEW;review=json.loads(record_path.read_text());review['descriptors'][relative]['sha256']=semantic(raw);record_path.write_text(json.dumps(review))
        self.assertEqual(check_source(self.root)['status'],'verified')
        module=self.root/REQUIRED_MODULES[0];module.write_bytes(module.read_bytes()+b'\n# unreviewed\n')
        with self.assertRaises(ContractError):check_source(self.root)
    def test_descriptor_formatting_is_semantic_but_revision_disagreement_fails(self):
        path=self.root/(DESCRIPTORS+'triage.json');raw=json.loads(path.read_text(encoding='utf-8'));path.write_text(json.dumps(raw,separators=(',',':'),ensure_ascii=False),encoding='utf-8')
        self.assertEqual(check_source(self.root)['status'],'verified')
        raw['contractRevision']='unreviewed-new';path.write_text(json.dumps(raw),encoding='utf-8')
        review_path=self.root/REVIEW;review=json.loads(review_path.read_text());review['descriptors'][DESCRIPTORS+'triage.json'].update(contractRevision='unreviewed-new',sha256=semantic(raw));review_path.write_text(json.dumps(review))
        with self.assertRaises(ContractError):check_source(self.root)
    def test_pinned_manifest_cannot_accept_mutated_worker_even_with_rehashed_record(self):
        files=copy.deepcopy(self.files);relative='listener/src/tg_triage/config.py';files[relative]+=b'\n# changed\n'
        manifest=json.loads(files[MANIFEST]);canonical=files[relative].replace(b'\r\n',b'\n');manifest['workers']['triage']['files']['package/config.py']={'size':len(canonical),'sha256':hashlib.sha256(canonical).hexdigest()}
        files[MANIFEST]=json.dumps(manifest).encode();review=json.loads(files[REVIEW]);review['manifestSha256']=semantic(manifest);files[REVIEW]=json.dumps(review).encode()
        with self.assertRaises(ContractError):check_archive(files)
    def test_json_path_type_size_and_duplicate_failures_are_sanitized(self):
        for relative,raw in [('..//secret',b'SECRET'),('/absolute',b'SECRET'),('bad\\path',b'SECRET'),('bad:drive',b'SECRET'),(MANIFEST,b'{broken SECRET'),(REVIEW,b'{"schemaVersion":1,"schemaVersion":1}'),(REVIEW,b'null'),(REVIEW,b'x'*(1024*1024+1)),(REVIEW,'not bytes')]:
            files=copy.deepcopy(self.files);files[relative]=raw
            with self.subTest(path=relative),self.assertRaises(ContractError) as error:check_archive(files)
            self.assertNotIn('SECRET',str(error.exception))
    def test_review_module_validation_precedes_any_target_read(self):
        path=self.root/REVIEW;review=json.loads(path.read_text());review['reviewedModules']={'../../secret.py':'0'*64};path.write_text(json.dumps(review));before=self.snapshot()
        with self.assertRaises(ContractError):check_source(self.root)
        self.assertEqual(before,self.snapshot())
        review['reviewedModules']={BASE+str(i)+'.py':'0'*64 for i in range(129)};path.write_text(json.dumps(review))
        with self.assertRaises(ContractError):check_source(self.root)
    def test_unreadable_source_and_symlink_or_junction_fail(self):
        original=os.open
        def denied(path,*args,**kwargs):
            if Path(path).name=='config.py':raise PermissionError('SECRET')
            return original(path,*args,**kwargs)
        with patch('agentfleet_console.contracts.os.open',side_effect=denied),self.assertRaises(ContractError) as error:check_source(self.root)
        self.assertNotIn('SECRET',str(error.exception))
        junction=self.root/'listener/src/tg_triage/redirect';target=self.root/'listener/prompts'
        if os.name=='nt':
            result=subprocess.run(['cmd','/c','mklink','/J',str(junction),str(target)],capture_output=True);self.assertEqual(result.returncode,0)
            self.addCleanup(lambda:os.rmdir(junction) if junction.exists() else None)
        else:junction.symlink_to(target,target_is_directory=True)
        with self.assertRaises(ContractError):check_source(self.root)
    def test_cli_no_git_copy_writes_no_caches_or_state(self):
        for relative in (BASE+'__init__.py',BASE+'errors.py',BASE+'contracts.py','console-ui/scripts/check-contracts.py'):
            path=self.root/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,path)
        before=self.snapshot();result=subprocess.run([sys.executable,'-E','-S',str(self.root/'console-ui/scripts/check-contracts.py')],cwd=self.root,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout)['status'],'verified');self.assertEqual(before,self.snapshot())
        self.assertFalse(list(self.root.rglob('__pycache__')))

if __name__=='__main__':unittest.main()
