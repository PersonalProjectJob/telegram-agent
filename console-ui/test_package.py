"""Source archive tests. Every upstream remains offline; no credentials or service apply."""
import hashlib
import http.client
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agentfleet portable ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base/'source copy'
        for relative in package.FILES:
            target = self.source/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(package.ROOT/relative, target)

    def archive(self, name='console.zip'):
        path = self.base/name
        result = package.build(path, root=self.source)
        self.assertEqual(result['sha256'], hashlib.sha256(path.read_bytes()).hexdigest())
        return path

    def test_allowlist_excludes_state_credentials_tests_and_cache(self):
        excluded = ('.env', '.agentfleet/secrets/triage-key.json', 'config/listener.toml',
                    'config/nailhub.toml', 'secrets/key.json', 'profiles/session.sqlite',
                    'listener/tests/test_fake.py', 'console-ui/test_private.py',
                    'console-ui/__pycache__/run.pyc', '.context/private-evidence.json', '.git/config')
        for relative in excluded:
            path=self.source/relative;path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('PRIVATE-SENTINEL-NOT-PACKAGED', encoding='utf-8')
        archive=self.archive()
        with zipfile.ZipFile(archive) as bundle:
            names=bundle.namelist()
            self.assertEqual(set(names),{package.PREFIX+p for p in package.FILES}|{package.PREFIX+package.MANIFEST})
            self.assertTrue(all('\\' not in name and '..' not in Path(name).parts for name in names))
            self.assertFalse(any(b'PRIVATE-SENTINEL-NOT-PACKAGED' in bundle.read(name) for name in names))
        self.assertEqual(package.verify(archive)['schema'],package.SCHEMA)

    def test_uncommitted_bytes_and_reproducible_archive(self):
        asset=self.source/'console-ui/app.js'
        asset.write_bytes(asset.read_bytes()+b'\n// packaging-current-source-regression\n')
        first=self.archive('first.zip');second=self.archive('second.zip')
        self.assertEqual(first.read_bytes(),second.read_bytes())
        with zipfile.ZipFile(first) as bundle:
            self.assertEqual(bundle.read(package.PREFIX+'console-ui/app.js'),asset.read_bytes())
        self.assertFalse((self.source/'.git').exists())

    def test_output_cannot_overwrite_source_existing_files_or_link_parent(self):
        asset=self.source/'console-ui/app.js';original=asset.read_bytes()
        for output in (asset,self.source/'console.zip',self.source/'dist'/'not-a-zip.py'):
            with self.subTest(output=output),self.assertRaises(package.PackageError):package.build(output,root=self.source)
        self.assertEqual(asset.read_bytes(),original)
        archive=self.archive();before=archive.read_bytes()
        with self.assertRaises(package.PackageError):package.build(archive,root=self.source)
        self.assertEqual(before,archive.read_bytes())
        link=self.base/'linked-output'
        try:link.symlink_to(self.base,target_is_directory=True)
        except OSError:return  # Windows symlink creation may require Developer Mode.
        with self.assertRaises(package.PackageError):package.build(link/'other.zip',root=self.source)

    def test_source_file_link_rejected(self):
        asset=self.source/'console-ui/app.js';asset.unlink()
        try:asset.symlink_to(package.ROOT/'console-ui/app.js')
        except OSError:self.skipTest('OS does not permit test symlinks')
        with self.assertRaises(package.PackageError):self.archive()
        self.assertFalse((self.base/'console.zip').exists())

    @unittest.skipUnless(os.name=='nt','Windows directory junction regression')
    def test_windows_junction_source_and_output_are_rejected(self):
        junction=self.source/'junction-source'
        result=subprocess.run(['cmd','/d','/c','mklink','/J',str(junction),str(self.source/'console-ui')],capture_output=True,timeout=5)
        if result.returncode:self.skipTest('OS does not permit directory junction creation')
        with self.assertRaises(package.PackageError):package.source_bytes(self.source,'junction-source/run.py')
        with self.assertRaises(package.PackageError):package.build(junction/'archive.zip',root=self.source)

    def test_traversal_paths_and_malformed_manifest_are_rejected(self):
        for relative in ('../README.md','/README.md','C:/README.md','console-ui\\run.py'):
            with self.subTest(relative=relative),self.assertRaises(package.PackageError):package.source_bytes(self.source,relative)
        archive=self.archive()
        malformed=self.base/'malformed.zip'
        with zipfile.ZipFile(archive) as source,zipfile.ZipFile(malformed,'w') as target:
            for item in source.infolist():
                target.writestr(item,b'[]' if item.filename==package.PREFIX+package.MANIFEST else source.read(item.filename))
        with self.assertRaises(package.PackageError):package.verify(malformed)
        traversal=self.base/'traversal.zip'
        with zipfile.ZipFile(archive) as source,zipfile.ZipFile(traversal,'w') as target:
            for item in source.infolist():target.writestr(item,source.read(item.filename))
            target.writestr('../outside.txt','not extracted')
        with self.assertRaises(package.PackageError):package.verify(traversal)

    def test_checksum_tampering_rejected(self):
        archive=self.archive();tampered=self.base/'tampered.zip'
        with zipfile.ZipFile(archive) as source,zipfile.ZipFile(tampered,'w') as target:
            for item in source.infolist():
                data=source.read(item.filename)
                if item.filename==package.PREFIX+'console-ui/app.js':data+=b'\n// changed\n'
                target.writestr(item,data)
        with self.assertRaises(package.PackageError):package.verify(tampered)

    def test_missing_required_file_prevents_output(self):
        (self.source/'nailhub/src/nailhub/validation.py').unlink()
        with self.assertRaises(package.PackageError):self.archive()
        self.assertFalse((self.base/'console.zip').exists())

    def start(self, extracted, use_launcher=False):
        if use_launcher:
            command=['cmd','/d','/c','call',str(extracted/'start-console.cmd'),'--port','0','--no-open'] if os.name=='nt' else ['sh',str(extracted/'start-console.sh'),'--port','0','--no-open']
        else:
            # -S disables every site package; -E ignores PYTHONPATH/PYTHONHOME.
            command=[sys.executable,'-E','-S',str(extracted/'console-ui/run.py'),'--port','0','--no-open']
        process=subprocess.Popen(command,cwd=self.base,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        def stop():
            if process.poll() is None:
                if use_launcher and os.name=='nt':
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],capture_output=True,timeout=10)
                else:process.terminate()
            try:process.communicate(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.communicate(timeout=5)
        self.addCleanup(stop)
        lines=queue.Queue()
        threading.Thread(target=lambda:lines.put(process.stdout.readline()),daemon=True).start()
        try:line=lines.get(timeout=30)
        except queue.Empty:self.fail('Extracted launcher did not print a URL within30s')
        if not line.startswith('AgentFleet console: http://127.0.0.1:'):
            stop();self.fail('Extracted launcher failed: '+line)
        port=int(line.strip().rsplit(':',1)[1])
        return process,port,stop

    def request(self,port,path,body=None,token=None):
        connection=http.client.HTTPConnection('127.0.0.1',port,timeout=10)
        headers={'Content-Type':'application/json','X-CSRF-Token':token} if body is not None else {}
        connection.request('POST' if body is not None else 'GET',path,json.dumps(body) if body is not None else None,headers)
        response=connection.getresponse();raw=response.read();connection.close()
        self.assertEqual(response.status,200,raw.decode(errors='replace'))
        return json.loads(raw) if path.startswith('/api/') else raw

    def test_extract_launch_without_git_or_dependencies_save_both_services_and_reload(self):
        archive=self.archive()
        with zipfile.ZipFile(archive) as bundle:bundle.extractall(self.base/'extracted folder')
        extracted=self.base/'extracted folder'/'agentfleet-console'
        process,port,stop=self.start(extracted)
        bootstrap=self.request(port,'/api/bootstrap');token=bootstrap['csrfToken']
        for asset in ('index.html','app.js','api.js','styles.css','tokens.css'):
            self.assertTrue(self.request(port,'/'+asset))
        revisions={}
        for id in ('triage','nailhub'):
            service=bootstrap['services'][id];draft=service['settings']
            draft['timeout']=123 if id=='triage' else 124
            result=self.request(port,'/api/services/'+id+'/save',{'settings':draft,'expectedRevision':service['revision']},token)
            revisions[id]=result['service']['revision']
        stop()
        process,port,stop=self.start(extracted)
        reloaded=self.request(port,'/api/bootstrap')
        for id in ('triage','nailhub'):
            self.assertEqual(reloaded['services'][id]['revision'],revisions[id])
            self.assertEqual(reloaded['services'][id]['settings']['timeout'],123 if id=='triage' else 124)
        self.assertFalse((extracted/'.git').exists())
        self.assertFalse((extracted/'.venv').exists())
        stop()

    def test_platform_launcher_handles_relocated_path_with_spaces(self):
        with zipfile.ZipFile(self.archive()) as bundle:bundle.extractall(self.base/'another folder')
        extracted=self.base/'another folder'/'agentfleet-console'
        process,port,stop=self.start(extracted,use_launcher=True)
        self.assertEqual(self.request(port,'/api/bootstrap')['host']['mode'],'local')
        stop()


if __name__=='__main__':unittest.main()
