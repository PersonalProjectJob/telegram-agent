"""Portable closure gates and actual offline extracted CLI/fixture launches."""
import hashlib
import inspect
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import zipfile

import test_package
package = test_package.package
from test_integration import FixtureAdapter, fixture_descriptor

BASE = 'console-ui/backend/agentfleet_console/'
ADAPTER = BASE+'workers/fixture_adapter.py'
DESCRIPTOR = BASE+'workers/descriptors/fixture.json'


class PackageClosureTests(unittest.TestCase):
    setUp = test_package.PackagingTests.setUp
    archive = test_package.PackagingTests.archive
    start = test_package.PackagingTests.start
    request = test_package.PackagingTests.request

    def test_drift_fails_before_output_directory_creation(self):
        source=self.source/'listener/src/tg_triage/usage.py'
        source.write_bytes(source.read_bytes()+b'\n# unreviewed worker change\n')
        output=self.base/'never created'/'release.zip'
        with self.assertRaisesRegex(package.PackageError,'review'):
            package.build(output,root=self.source)
        self.assertFalse(output.parent.exists())
        self.assertTrue(source.read_bytes().endswith(b'# unreviewed worker change\n'))

    def test_omitted_worker_module_or_descriptor_fails_before_output(self):
        for missing in ('listener/src/tg_triage/usage.py',BASE+'workers/descriptors/triage.json'):
            with self.subTest(missing=missing),patch.object(package,'FILES',tuple(p for p in package.FILES if p!=missing)):
                output=self.base/'omission output'/'release.zip'
                with self.assertRaises(package.PackageError):package.build(output,root=self.source)
                self.assertFalse(output.parent.exists())

    def test_package_import_alias_requires_submodule_but_allows_exported_symbols(self):
        contents={p:package.source_bytes(self.source,p) for p in package.FILES}
        imports=contents[BASE+'worker_sources.py']
        for statement in (b'\nfrom agentfleet_console.workers import fixture_adapter\n',b'\nfrom .workers import fixture_adapter\n'):
            contents[BASE+'worker_sources.py']=imports+statement
            with self.subTest(statement=statement),self.assertRaisesRegex(package.PackageError,'fixture_adapter'):
                package.validate_closure(contents)
        contents[ADAPTER]=b'"""Offline fixture module."""\n'
        package.validate_closure(contents)
        contents.pop(ADAPTER)
        contents[BASE+'worker_sources.py']=imports+b'\nfrom agentfleet_console.workers import EXPORTED\nfrom .workers.adapters import LegacyAdapter\n'
        contents[BASE+'workers/__init__.py']+=b'\nEXPORTED = 1\n'
        package.validate_closure(contents)

    def test_archive_checker_rejects_drift_even_with_rewritten_raw_checksums(self):
        original=self.archive();tampered=self.base/'self-consistent-but-drifted.zip'
        with zipfile.ZipFile(original) as source:
            contents={item.filename:source.read(item.filename) for item in source.infolist()}
            name=package.PREFIX+'listener/src/tg_triage/usage.py'
            contents[name]+=b'\n# archived drift\n'
            manifest=json.loads(contents[package.PREFIX+package.MANIFEST])
            manifest['files']['listener/src/tg_triage/usage.py']={'sha256':hashlib.sha256(contents[name]).hexdigest(),'size':len(contents[name])}
            contents[package.PREFIX+package.MANIFEST]=json.dumps(manifest).encode()
            with zipfile.ZipFile(tampered,'w') as target:
                for item in source.infolist():target.writestr(item,contents[item.filename])
        with self.assertRaisesRegex(package.PackageError,'drift'):package.verify(tampered)

    def test_package_init_imports_cannot_justify_missing_modules_or_cycles(self):
        baseline={p:package.source_bytes(self.source,p) for p in package.FILES}
        initializer=BASE+'workers/__init__.py'
        for statement in (b'\nfrom . import missing_adapter\n',b'\nfrom . import missing_adapter\nfrom . import missing_adapter\n'):
            contents=dict(baseline);contents[initializer]+=statement
            with self.subTest(statement=statement),self.assertRaisesRegex(package.PackageError,'missing_adapter'):
                package.validate_closure(contents)
            contents[BASE+'workers/missing_adapter.py']=b'"""Explicitly included local module."""\n'
            package.validate_closure(contents)
        contents=dict(baseline)
        contents[BASE+'export_a/__init__.py']=b'from ..export_b import MISSING\n'
        contents[BASE+'export_b/__init__.py']=b'from ..export_a import MISSING\n'
        with self.assertRaisesRegex(package.PackageError,'MISSING'):package.validate_closure(contents)
        contents=dict(baseline)
        contents[initializer]+=b'\nfrom .adapters import LegacyAdapter\nEXPORTED = 1\n'
        contents[BASE+'worker_sources.py']+=b'\nfrom .workers import LegacyAdapter, EXPORTED\n'
        package.validate_closure(contents)

    def test_extracted_worker_help_runs_without_site_packages_or_jobs(self):
        with zipfile.ZipFile(self.archive()) as archive:archive.extractall(self.base/'worker help path with spaces')
        root=self.base/'worker help path with spaces'/'agentfleet-console'
        before={str(p.relative_to(root)) for p in root.rglob('*')}
        code="""import runpy,sys
from pathlib import Path
root=Path(sys.argv[1]);module=sys.argv[2]
sys.path.insert(0,str(root/('listener/src' if module=='tg_triage' else 'nailhub/src')))
sys.argv=[module,'--help']
runpy.run_module(module,run_name='__main__')
"""
        for module in ('tg_triage','nailhub'):
            result=subprocess.run([sys.executable,'-E','-S','-B','-c',code,str(root),module],cwd=self.base,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('usage:',result.stdout)
        self.assertEqual(before,{str(p.relative_to(root)) for p in root.rglob('*')})
        self.assertFalse((root/'.agentfleet').exists())

    def fixture_source(self,name,omit=None,metadata=True):
        root=self.base/name;shutil.copytree(self.source,root)
        descriptor=fixture_descriptor()
        (root/DESCRIPTOR).write_text(json.dumps(descriptor),encoding='utf-8')
        adapter='import copy\nfrom pathlib import Path\nimport tomllib\nfrom .adapters import Binding\n\n'+inspect.getsource(FixtureAdapter)
        (root/ADAPTER).write_text(adapter,encoding='utf-8')
        path=root/(BASE+'workers/adapters.py')
        text=path.read_text(encoding='utf-8').replace("def builtins():\n    return {", "def builtins():\n    from .fixture_adapter import FixtureAdapter\n    return {\n        'fixture':FixtureAdapter(ROOT/'fixture-binding'),",1)
        path.write_text(text,encoding='utf-8')
        review_path=root/(BASE+'workers/contract-review.json')
        review=json.loads(review_path.read_text(encoding='utf-8'))
        for relative in (BASE+'workers/adapters.py',ADAPTER):
            canonical=(root/relative).read_bytes().replace(b'\r\n',b'\n')
            review['reviewedModules'][relative]=hashlib.sha256(canonical).hexdigest()
        review['descriptors'][DESCRIPTOR]={'id':'fixture','contractRevision':descriptor['contractRevision'],'sha256':hashlib.sha256(json.dumps(descriptor,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()}
        review_path.write_text(json.dumps(review),encoding='utf-8')
        path=root/'console-ui/package.py';text=path.read_text(encoding='utf-8')
        extras=tuple(relative for relative in (DESCRIPTOR,ADAPTER) if relative!=omit)
        text=text.replace('MAX_FILE_BYTES =', 'FILES += '+repr(extras)+'\nMAX_FILE_BYTES =',1)
        if metadata:text=text.replace('PACKAGE_REGISTRATIONS = {', "PACKAGE_REGISTRATIONS = {\n    'fixture': {'descriptor':"+repr(DESCRIPTOR)+",'adapter':"+repr(ADAPTER)+"},",1)
        path.write_text(text,encoding='utf-8')
        return root

    def test_explicit_third_registration_missing_assets_then_package_load_save_reload(self):
        for index,(omit,metadata) in enumerate(((DESCRIPTOR,True),(ADAPTER,True),(None,False))):
            root=self.fixture_source('negative fixture '+str(index),omit,metadata)
            output=self.base/('fixture rejected '+str(index))/'release.zip'
            result=subprocess.run([sys.executable,'-E','-S','-B',str(root/'console-ui/package.py'),'--output',str(output)],capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,2,result.stdout+result.stderr)
            self.assertIn('missing' if omit else 'registration',result.stderr.lower())
            self.assertFalse(output.parent.exists())
        root=self.fixture_source('reviewed third fixture')
        output=self.base/'reviewed fixture release.zip'
        result=subprocess.run([sys.executable,'-E','-S','-B',str(root/'console-ui/package.py'),'--output',str(output)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        extracted=self.base/'third fixture extract with spaces'
        with zipfile.ZipFile(output) as archive:archive.extractall(extracted)
        extracted=extracted/'agentfleet-console'
        result=subprocess.run([sys.executable,'-E','-S','-B',str(extracted/'console-ui/package.py'),'--verify',str(output)],capture_output=True,text=True,timeout=20)
        self.assertEqual(result.returncode,0,result.stderr)
        process,port,stop=self.start(extracted)
        bootstrap=self.request(port,'/api/bootstrap')
        self.assertEqual(set(bootstrap['services']),{'triage','nailhub','fixture'})
        self.assertEqual(bootstrap['descriptors']['fixture']['providers'],[])
        service=bootstrap['services']['fixture'];settings={'name':'Packaged fixture saved','enabled':False,'tags':['retained','offline']}
        result=self.request(port,'/api/services/fixture/save',{'settings':settings,'expectedRevision':service['revision'],'contractRevision':service['contractRevision']},bootstrap['csrfToken'])
        revision=result['service']['revision'];stop()
        process,port,stop=self.start(extracted)
        reloaded=self.request(port,'/api/bootstrap')['services']['fixture']
        self.assertEqual(reloaded['settings'],settings);self.assertEqual(reloaded['revision'],revision)
        stop()


if __name__=='__main__':unittest.main()
