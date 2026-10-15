"""TEMP-only deployment tests. Fakes mirror systemctl show/stop/start, pip and journalctl argv."""
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path
from contextlib import closing, nullcontext, redirect_stdout
from unittest.mock import Mock, patch

from deploy.agentfleet_deploy import Deployment, DeploymentError, GuardError, Settings, guard_environment, main as deploy_main
from deploy.build_wheel import build_stage, sha256, stage_archive, verify_record, verify_stage, inventory, TEST_ROOT_FILES, OPTIONAL_TEST_ROOT_FILES

COMMIT = 'a' * 40
ROOT = Path(__file__).resolve().parents[1]


def fixture_archive(service):
    package = 'tg_triage' if service == 'listener' else 'nailhub'
    distribution = 'tg-triage' if service == 'listener' else 'nailhub'
    dependencies = '["telethon>=1.45,<2","playwright>=1.48,<2"]' if service == 'nailhub' else '["telethon>=1.45,<2"]'
    files = {'pyproject.toml': ('[project]\nname="' + distribution + '"\nversion="1.0.0"\n'
              'description="Offline fixture"\nrequires-python=">=3.11"\ndependencies=' + dependencies + '\n').encode(),
             'src/' + package + '/__init__.py': b'VERSION = "STAGED"\n',
             'src/' + package + '/__main__.py': b'print("fixture")\n',
             'tests/__init__.py': b'',
             'tests/test_smoke.py': b'import unittest\nclass Smoke(unittest.TestCase):\n    def test_smoke(self): self.assertEqual(1+1,2)\n'}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT, pax_headers={'comment': COMMIT}) as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(service + '/' + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
    return stream.getvalue()


def install_dist(site, name, package, content=b'ORIGINAL\n', requires=()):
    site.mkdir(parents=True, exist_ok=True)
    info = site / (name.replace('-', '_') + '-1.0.0.dist-info')
    info.mkdir(exist_ok=True)
    source = site / package / '__init__.py'
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(content)
    (info / 'METADATA').write_text('Metadata-Version: 2.1\nName: ' + name + '\nVersion: 1.0.0\n' +
                                   ''.join('Requires-Dist: ' + value + '\n' for value in requires), encoding='utf-8')
    (info / 'top_level.txt').write_text(package + '\n', encoding='utf-8')
    rows = [[path.relative_to(site).as_posix(), '', ''] for path in [source, info/'METADATA', info/'top_level.txt']]
    rows.append([(info / 'RECORD').relative_to(site).as_posix(), '', ''])
    with (info / 'RECORD').open('w', newline='', encoding='utf-8') as stream:
        csv.writer(stream).writerows(rows)


class FakeCommands:
    def __init__(self, settings):
        self.settings = settings
        self.states = {settings.unit: {'MainPID': 101, 'NRestarts': 0, 'ActiveState': 'active'},
                       settings.other_unit: {'MainPID': 202, 'NRestarts': 0, 'ActiveState': 'active'}}
        self.calls = []
        self.actions = []
        self.fail_install = False
        self.bad_installed_bytes = False
        self.other_change = None
        self.own_restart = False
        self.errors = ''
        self.fail_tests = False
        self.starts = 0
        self.ready = True
        self.on_start = None
        self.on_stop = None
        self.stops = 0

    def __call__(self, args, *, cwd=None, env=None):
        args = [str(x) for x in args]
        self.calls.append((args, cwd, env))
        s = self.settings
        if args[0] == s.systemctl:
            verb, unit = args[1:3]
            if verb == 'show':
                assert args[3:] == ['--property=MainPID','--property=NRestarts','--property=ActiveState']
                return subprocess.CompletedProcess(args, 0, '\n'.join(key+'='+str(value) for key,value in self.states[unit].items())+'\n', '')
            assert args == [s.systemctl, verb, s.unit], 'A fake rejects mutation of the OTHER unit'
            self.actions.append((verb, unit))
            if verb == 'stop':
                self.stops += 1
                if self.on_stop and self.stops == 1:self.on_stop()
                self.states[unit]['MainPID'] = 0
                self.states[unit]['ActiveState'] = 'inactive'
                self.states[unit]['NRestarts'] = 0
            elif verb == 'start':
                self.states[unit]['NRestarts'] = 0
                self.starts += 1
                self.states[unit]['MainPID'] = 300+self.starts
                self.states[unit]['ActiveState'] = 'active'
                if self.starts == 1:
                    if self.on_start:self.on_start()
                    if self.other_change:
                        self.states[s.other_unit][self.other_change] += 1
                    if self.own_restart:
                        self.states[unit]['NRestarts'] += 1
            else:
                raise AssertionError('Unexpected systemctl verb')
            return subprocess.CompletedProcess(args, 0, '', '')
        if args[0] == s.journalctl:
            assert args[:4] == [s.journalctl, '-u', s.unit, '--since']
            assert args[5:] == ['--no-pager','--output=cat']
            assert args[4].endswith(' UTC')
            ready='2026-10-13 12:00:00 INFO NailHub ready owner_verified=1\n' if s.service=='nailhub' and self.ready and self.starts else ''
            return subprocess.CompletedProcess(args, 0, self.errors+ready, '')
        assert args[0] == s.python
        if args[1:3] == ['-m','unittest']:
            assert args[3:] == ['discover','-s','tests','-t','.']
            assert cwd == s.stage/'source'/s.service
            assert env['PYTHONPATH']=='src' and env['PYTHONDONTWRITEBYTECODE']=='1'
            if self.fail_tests:
                raise subprocess.CalledProcessError(1,args,stderr='fixture test failure')
            # Execute the staged offline fixture's actual unittest command, without pip/network.
            return subprocess.run([sys.executable,*args[1:]],cwd=cwd,env=env,check=True,capture_output=True,text=True)
        assert args[1:7] == ['-m','pip','install','--no-index','--no-deps','--force-reinstall']
        assert args[7] == str(s.stage/verify_stage(s.stage,s.service)['wheel'])
        self.actions.append(('pip',s.unit))
        site = Path(s.site_paths[0])
        if self.fail_install:
            (site/s.package/'__init__.py').write_bytes(b'BROKEN INSTALL\n')
            raise subprocess.CalledProcessError(1,args,stderr='fake install failed')
        # Mirror pip's RECORD-based uninstall, then wheel/data installation and RECORD rewrite.
        removed_parents=set()
        for record in site.glob(s.package+'-*.dist-info/RECORD'):
            for row in csv.reader(io.StringIO(record.read_text(encoding='utf-8'))):
                target = site/row[0]
                if target.is_file():target.unlink()
                removed_parents.update(p for p in target.parents if p.is_relative_to(site) and p!=site)
        # pip also removes empty package directories after RECORD-based uninstall.
        for parent in sorted(removed_parents,key=lambda p:len(p.parts),reverse=True):
            try:parent.rmdir()
            except OSError:pass
        records=[]
        with zipfile.ZipFile(args[7]) as archive:
            for name in archive.namelist():
                parts=Path(name).parts
                if parts[0].endswith('.data'):
                    target=s.prefix.joinpath(*parts[2:])
                else:
                    target=site/name
                target.parent.mkdir(parents=True,exist_ok=True)
                target.write_bytes(archive.read(name))
                records.append([os.path.relpath(target,site).replace(os.sep,'/'),'',''])
            record=next(site/name for name in archive.namelist() if name.endswith('.dist-info/RECORD'))
        with record.open('w',newline='',encoding='utf-8') as stream:csv.writer(stream).writerows(records)
        if self.bad_installed_bytes:
            (site/s.package/'__init__.py').write_bytes(b'BAD HASH\n')
        return subprocess.CompletedProcess(args,0,'','')


class DeployTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='agentfleet-deploy-test-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def setup_service(self, service='listener'):
        stage=self.root/(service+'-stage')
        stage_archive(service,stage,fixture_archive(service),COMMIT)
        code=self.root/(service+'-code');prefix=code/'.venv';site=prefix/'lib'/'site-packages'
        package='tg_triage' if service=='listener' else 'nailhub'
        install_dist(site,package,package)
        install_dist(site,'telethon','telethon',content=b'# offline dependency fixture\n')
        if service == 'nailhub':
            install_dist(site,'playwright','playwright',requires=('pyee>=13','greenlet>=3'))
            install_dist(site,'pyee','pyee');install_dist(site,'greenlet','greenlet')
        config=self.root/(service+'.toml');state=self.root/(service+'-state');state.mkdir()
        db=state/'jobs.sqlite3' if service=='nailhub' else state/'triage.sqlite3'
        config.write_text('[worker]\ndb_path='+json.dumps(str(db))+'\n',encoding='utf-8')
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('CREATE TABLE sample(value TEXT)');conn.execute('INSERT INTO sample VALUES("original")')
        return Settings(service,stage,code,config,state,self.root/'backups',str(prefix/'bin'/'python'),(site,),prefix,
                        'tg-triage' if service=='listener' else 'tg-nailhub',
                        'tg-nailhub' if service=='listener' else 'tg-triage',health_wait=0,test_paths=True)

    def test_guard_refuses_incident_stray_nested_directory_before_any_change(self):
        s=self.setup_service();stray=Path(s.site_paths[0])/'tg_triage'/'nailhub';stray.mkdir()
        (stray/'__init__.py').write_text('# exact mixed-install incident',encoding='utf-8')
        runner=FakeCommands(s)
        with self.assertRaisesRegex(GuardError,'unrecorded package file.*nailhub'):
            Deployment(s,runner=runner).deploy()
        self.assertEqual(runner.actions,[]);self.assertFalse(s.backup_root.exists())
        self.assertTrue((stray/'__init__.py').exists())

    def test_guard_refuses_even_empty_unrecorded_package_directory(self):
        s=self.setup_service();stray=Path(s.site_paths[0])/'tg_triage'/'nailhub';stray.mkdir()
        with self.assertRaisesRegex(GuardError,'unrecorded package directory'):
            guard_environment(s.site_paths,s.package,['telethon'])

    def test_guard_unknown_top_level_package_with_or_without_distribution(self):
        for has_metadata in (False,True):
            with self.subTest(has_metadata=has_metadata):
                folder=self.root/str(has_metadata);folder.mkdir();site=folder/'site'
                install_dist(site,'tg-triage','tg_triage');install_dist(site,'telethon','telethon')
                if has_metadata:install_dist(site,'unknown','unknown')
                else:(site/'unknown').mkdir()
                with self.assertRaisesRegex(GuardError,'unknown top-level importable.*unknown'):
                    guard_environment((site,),'tg_triage',['telethon'])

    def test_guard_active_transitive_dependencies_allowed(self):
        s=self.setup_service();site=Path(s.site_paths[0])
        install_dist(site,'telethon','telethon',requires=('rsa>=4','pyaes; python_version >= "3.11"'))
        install_dist(site,'rsa','rsa',requires=('pyasn1>=0.4',));install_dist(site,'pyaes','pyaes');install_dist(site,'pyasn1','pyasn1')
        result=guard_environment((site,),s.package,['telethon>=1.45,<2'])
        self.assertEqual(result['distributions'],['pyaes','pyasn1','rsa','telethon','tg-triage'])

    def test_guard_unused_extra_does_not_whitelist_unknown_package(self):
        s=self.setup_service();site=Path(s.site_paths[0])
        install_dist(site,'telethon','telethon',requires=('unrelated; extra == "testing"',))
        install_dist(site,'unrelated','unrelated')
        with self.assertRaisesRegex(GuardError,'unrelated'):guard_environment((site,),s.package,['telethon'])

    def test_failed_install_restores_code_venv_config_sqlite_and_own_unit(self):
        s=self.setup_service();runner=FakeCommands(s);runner.fail_install=True
        with self.assertRaises(DeploymentError):Deployment(s,runner=runner).deploy()
        self.assertEqual((Path(s.site_paths[0])/s.package/'__init__.py').read_bytes(),b'ORIGINAL\n')
        self.assertEqual(runner.states[s.unit]['ActiveState'],'active')
        self.assertEqual(runner.states[s.other_unit],{'MainPID':202,'NRestarts':0,'ActiveState':'active'})
        with closing(sqlite3.connect(s.database())) as db, db:self.assertEqual(db.execute('SELECT value FROM sample').fetchall(),[('original',)])
        result=json.loads((s.stage/'deployment.json').read_text(encoding='utf-8'))
        self.assertFalse(result['success']);self.assertTrue(result['rollback']['restored'])
        self.assertTrue(Path(result['backup_path']).is_dir())
        self.assertEqual(runner.actions,[('stop',s.unit),('pip',s.unit),('stop',s.unit),('start',s.unit)])

    def test_other_pid_changed_reports_warning_without_rollback(self):
        s=self.setup_service();runner=FakeCommands(s);runner.other_change='MainPID'
        result=Deployment(s,runner=runner).deploy()
        self.assertTrue(result['success']);self.assertTrue(result['warnings'])
        self.assertEqual(result['other_before']['MainPID'],202);self.assertEqual(result['other_after']['MainPID'],203)
        self.assertNotIn('rollback',result)
        self.assertEqual(runner.actions,[('stop',s.unit),('pip',s.unit),('start',s.unit)])
        self.assertEqual(json.loads((s.stage/'deployment.json').read_text())['warnings'],result['warnings'])

    def test_other_restart_changed_is_success_with_warning(self):
        s=self.setup_service();runner=FakeCommands(s);runner.other_change='NRestarts'
        result=Deployment(s,runner=runner).deploy()
        self.assertTrue(result['success']);self.assertEqual(result['other_after']['NRestarts'],1)
        self.assertTrue(result['warnings']);self.assertNotIn('rollback',result)

    def test_own_restart_changed_is_failure(self):
        s=self.setup_service();runner=FakeCommands(s);runner.own_restart=True
        with self.assertRaisesRegex(DeploymentError,'NRestarts changed'):Deployment(s,runner=runner).deploy()

    def test_journal_error_triggers_rollback(self):
        s=self.setup_service();runner=FakeCommands(s);runner.errors='2026-10-13 12:00:00 ERROR fixture error\n'
        with self.assertRaisesRegex(DeploymentError,'error journal'):Deployment(s,runner=runner).deploy()

    def test_wrong_installed_bytes_trigger_rollback(self):
        s=self.setup_service();runner=FakeCommands(s);runner.bad_installed_bytes=True
        with self.assertRaisesRegex(DeploymentError,'Installed bytes differ'):Deployment(s,runner=runner).deploy()
        self.assertEqual((Path(s.site_paths[0])/s.package/'__init__.py').read_bytes(),b'ORIGINAL\n')

    def test_package_test_failure_is_before_guard_backup_and_stop(self):
        s=self.setup_service();runner=FakeCommands(s);runner.fail_tests=True
        with self.assertRaises(subprocess.CalledProcessError):Deployment(s,runner=runner).deploy()
        self.assertEqual(runner.actions,[]);self.assertFalse(s.backup_root.exists())

    def test_success_for_both_services_has_provenance_and_other_unit_unchanged(self):
        for service in ('listener','nailhub'):
            with self.subTest(service=service):
                s=self.setup_service(service);runner=FakeCommands(s)
                result=Deployment(s,runner=runner).deploy()
                self.assertTrue(result['success']);self.assertEqual(result['commit'],COMMIT)
                self.assertEqual(result['other_before'],result['other_after'])
                self.assertEqual(result['journal_error_lines'],0)
                self.assertEqual(result['after']['NRestarts'],0)
                self.assertTrue(result['installed']['bytes_match'])
                self.assertEqual(runner.actions,[('stop',s.unit),('pip',s.unit),('start',s.unit)])
                self.assertTrue((Path(result['backup_path'])/'deployment.json').is_file())

    def test_backup_restore_uses_sqlite_api_and_handles_wal(self):
        s=self.setup_service();runner=FakeCommands(s);deploy=Deployment(s,runner=runner)
        original_config=s.config.read_bytes();deploy.system('stop');backup=deploy.backup()
        with closing(sqlite3.connect(s.database())) as conn, conn:
            conn.execute('INSERT INTO sample VALUES("after-backup")')
        s.config.write_text('changed',encoding='utf-8')
        Path(str(s.database())+'-wal').write_bytes(b'stale WAL fixture')
        deploy.rollback(backup,{'ActiveState':'active'},restore_db=True)
        self.assertEqual(s.config.read_bytes(),original_config)
        self.assertFalse(Path(str(s.database())+'-wal').exists())
        with closing(sqlite3.connect(s.database())) as conn, conn:
            self.assertEqual(conn.execute('SELECT value FROM sample').fetchall(),[('original',)])
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchall(),[('ok',)])

    def test_rollback_refuses_backup_from_other_service_before_stop(self):
        listener=self.setup_service('listener');nailhub=self.setup_service('nailhub')
        controller=Deployment(listener,runner=FakeCommands(listener));controller.system('stop');backup=controller.backup()
        runner=FakeCommands(nailhub)
        with self.assertRaisesRegex(DeploymentError,'different service'):
            Deployment(nailhub,runner=runner).rollback(backup,{'ActiveState':'active'})
        self.assertEqual(runner.actions,[])

    def test_rollback_refuses_corrupt_database_backup_before_stop(self):
        s=self.setup_service();runner=FakeCommands(s);deploy=Deployment(s,runner=runner);deploy.system('stop');backup=deploy.backup()
        (backup/'sqlite.backup').write_bytes(b'corrupted')
        with self.assertRaisesRegex(DeploymentError,'SQLite checksum'):
            deploy.rollback(backup,{'ActiveState':'active'},restore_db=True)
        self.assertEqual(runner.actions,[('stop',s.unit)])

    def test_first_install_inactive_target_starts_and_keeps_other_unit(self):
        s=self.setup_service('nailhub');runner=FakeCommands(s)
        runner.states[s.unit]={'MainPID':0,'NRestarts':0,'ActiveState':'inactive'}
        result=Deployment(s,runner=runner).deploy()
        self.assertTrue(result['success']);self.assertEqual(result['other_before'],result['other_after'])

    def test_pip_gets_readable_umask_and_restores_private_umask(self):
        s=self.setup_service();base=FakeCommands(s);observed=[]
        def runner(args,**kwargs):
            if list(args)[1:3]==['-m','pip']:
                value=os.umask(0o022);os.umask(value);observed.append(value)
            return base(args,**kwargs)
        # Windows does not implement POSIX directory permission bits; simulate only that OS primitive.
        state=[0o077]
        def windows_umask(value):
            previous=state[0];state[0]=value;return previous
        context=patch('deploy.agentfleet_deploy.os.umask',side_effect=windows_umask) if os.name=='nt' else nullcontext()
        with context:
            previous=os.umask(0o077)
            try:
                Deployment(s,runner=runner).deploy()
                after=os.umask(0o077);os.umask(after)
                self.assertEqual(observed,[0o022]);self.assertEqual(after,0o077)
            finally:os.umask(previous)

    def test_guard_refuses_recorded_files_removed_by_new_wheel(self):
        s=self.setup_service();site=Path(s.site_paths[0]);record=next(site.glob('tg_triage-*.dist-info/RECORD'))
        removed=['tg_triage/nailhub/__init__.py','tg_triage/model_gate.py']
        with record.open('a',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream)
            for name in removed:
                target=site/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'legacy code')
                from deploy.build_wheel import record_hash
                writer.writerow([name,record_hash(target.read_bytes()),str(target.stat().st_size)])
        runner=FakeCommands(s)
        with self.assertRaises(GuardError) as caught:Deployment(s,runner=runner).deploy()
        for name in removed:self.assertIn('recorded file absent from new wheel: '+name,str(caught.exception))
        self.assertEqual(runner.actions,[]);self.assertFalse(s.backup_root.exists())

    def record_legacy_paths(self,s,paths):
        from deploy.build_wheel import record_hash
        site=Path(s.site_paths[0]);record=next(site.glob(s.package+'-*.dist-info/RECORD'))
        with record.open('a',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream)
            for name in paths:
                target=site/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'# legacy code\n')
                writer.writerow([name,record_hash(target.read_bytes()),str(target.stat().st_size)])

    def test_allow_removed_exact_record_path_does_not_allow_other_removal(self):
        from dataclasses import replace
        s=self.setup_service();paths=['tg_triage/nailhub/__init__.py','tg_triage/model_gate.py']
        self.record_legacy_paths(s,paths);runner=FakeCommands(s)
        for allowed in ((paths[0],),('tg_triage/*',),('tg_triage/nailhub',)):
            with self.subTest(allowed=allowed),self.assertRaises(GuardError) as caught:
                Deployment(replace(s,allow_removed=allowed),runner=runner).deploy()
            self.assertIn('recorded file absent from new wheel: '+paths[1],str(caught.exception))
            self.assertEqual(runner.actions,[])
        self.assertFalse(s.backup_root.exists())

    def test_allow_removed_is_printed_and_recorded_in_deployment_json(self):
        from dataclasses import replace
        s=self.setup_service();paths=['tg_triage/nailhub/__init__.py','tg_triage/model_gate.py']
        self.record_legacy_paths(s,paths);s=replace(s,allow_removed=tuple(paths));runner=FakeCommands(s)
        with redirect_stdout(io.StringIO()) as output:result=Deployment(s,runner=runner).deploy()
        self.assertTrue(result['success'])
        self.assertEqual(result['allowed_removed'],sorted(paths))
        self.assertEqual(result['guard']['allowed_removed'],sorted(paths))
        self.assertEqual(result['installed_guard']['allowed_removed'],[])
        for name in paths:
            self.assertIn('ALLOW-REMOVED: '+name,output.getvalue())
            self.assertFalse((Path(s.site_paths[0])/name).exists())
        self.assertEqual(json.loads((s.stage/'deployment.json').read_text())['allowed_removed'],sorted(paths))
        self.assertEqual(json.loads((Path(result['backup_path'])/'deployment.json').read_text())['allowed_removed'],sorted(paths))

    def test_allow_removed_never_bypasses_unrecorded_or_unknown_package(self):
        s=self.setup_service();site=Path(s.site_paths[0]);stray=site/'tg_triage/stray.py';stray.write_text('# unrecorded')
        unknown=site/'unknown';unknown.mkdir();(unknown/'__init__.py').write_text('# unknown')
        manifest=verify_stage(s.stage,s.service)
        with self.assertRaises(GuardError) as caught:
            guard_environment(s.site_paths,s.package,['telethon'],new_wheel=s.stage/manifest['wheel'],prefix=s.prefix,
                              allow_removed=('tg_triage/stray.py','unknown/__init__.py'))
        self.assertIn('unrecorded package file',str(caught.exception));self.assertIn('unknown top-level importable',str(caught.exception))

    def record_bytecode(self,s,source_relative):
        import py_compile
        from deploy.build_wheel import record_hash
        site=Path(s.site_paths[0]);source=site/source_relative
        # Real compiler outputs with pip RECORD path/hash/size shapes.
        bytecodes=[Path(py_compile.compile(str(source),doraise=True)),
                   Path(py_compile.compile(str(source),doraise=True,optimize=1)),
                   Path(py_compile.compile(str(source),cfile=str(source.with_suffix('.pyc')),doraise=True))]
        record=next(site.glob(s.package+'-*.dist-info/RECORD'))
        with record.open('a',newline='',encoding='utf-8') as stream:
            writer=csv.writer(stream)
            for bytecode in bytecodes:
                writer.writerow([bytecode.relative_to(site).as_posix(),record_hash(bytecode.read_bytes()),str(bytecode.stat().st_size)])
        return [p.relative_to(site).as_posix() for p in bytecodes]

    def test_allow_removed_source_also_allows_and_reports_recorded_bytecode(self):
        from dataclasses import replace
        s=self.setup_service();source='tg_triage/retired.py'
        self.record_legacy_paths(s,[source]);bytecodes=self.record_bytecode(s,source)
        s=replace(s,allow_removed=(source,));runner=FakeCommands(s)
        with redirect_stdout(io.StringIO()) as output:result=Deployment(s,runner=runner).deploy()
        expected=sorted([source,*bytecodes])
        self.assertTrue(result['success']);self.assertEqual(result['allowed_removed'],expected)
        self.assertTrue(any('/__pycache__/' in name for name in bytecodes))
        self.assertEqual(json.loads((s.stage/'deployment.json').read_text())['allowed_removed'],expected)
        for name in expected:
            self.assertIn('ALLOW-REMOVED: '+name,output.getvalue())
            self.assertFalse((Path(s.site_paths[0])/name).exists())

    def test_source_allowlist_does_not_allow_unrelated_recorded_bytecode(self):
        from dataclasses import replace
        s=self.setup_service();retired='tg_triage/retired.py';other='tg_triage/other.py'
        self.record_legacy_paths(s,[retired,other]);allowed_cache=self.record_bytecode(s,retired);other_cache=self.record_bytecode(s,other)
        runner=FakeCommands(s)
        with self.assertRaises(GuardError) as caught:Deployment(replace(s,allow_removed=(retired,)),runner=runner).deploy()
        self.assertIn('recorded file absent from new wheel: '+other,str(caught.exception))
        for name in other_cache:self.assertIn('recorded file absent from new wheel: '+name,str(caught.exception))
        for name in allowed_cache:self.assertNotIn('recorded file absent from new wheel: '+name,str(caught.exception))
        self.assertEqual(runner.actions,[]);self.assertFalse(s.backup_root.exists())

    def test_bytecode_allowlist_does_not_implicitly_allow_source(self):
        from dataclasses import replace
        s=self.setup_service();source='tg_triage/retired.py'
        self.record_legacy_paths(s,[source]);bytecodes=self.record_bytecode(s,source);runner=FakeCommands(s)
        with self.assertRaisesRegex(GuardError,'recorded file absent from new wheel: '+source):
            Deployment(replace(s,allow_removed=tuple(bytecodes)),runner=runner).deploy()
        self.assertEqual(runner.actions,[])

    def test_cli_repeated_allow_removed_reaches_settings_without_test_paths(self):
        from types import SimpleNamespace
        fake_os=SimpleNamespace(name='posix',geteuid=lambda:0,umask=Mock())
        with patch('deploy.agentfleet_deploy.os',fake_os),patch('deploy.agentfleet_deploy.Deployment') as deployment,redirect_stdout(io.StringIO()):
            deployment.return_value.deploy.return_value={'success':True}
            self.assertEqual(deploy_main(['--service','listener','--stage',str(self.root),'--allow-removed','tg_triage/retired.py','--allow-removed','tg_triage/retired_data.json']),0)
        settings=deployment.call_args.args[0]
        self.assertEqual(settings.allow_removed,('tg_triage/retired.py','tg_triage/retired_data.json'))
        self.assertFalse(settings.test_paths)

    def test_manual_start_resets_preexisting_restart_count(self):
        s=self.setup_service();runner=FakeCommands(s);runner.states[s.unit]['NRestarts']=7
        result=Deployment(s,runner=runner).deploy()
        self.assertTrue(result['success']);self.assertEqual(result['before']['NRestarts'],7)
        self.assertEqual(result['after']['NRestarts'],0)

    def test_record_guard_allows_pip_receipts_and_retained_bytecode(self):
        import py_compile
        s=self.setup_service();site=Path(s.site_paths[0]);info=next(site.glob('tg_triage-*.dist-info'))
        bytecode=Path(py_compile.compile(str(site/'tg_triage/__init__.py'),doraise=True))
        extra=[bytecode]
        for name in ('INSTALLER','REQUESTED','direct_url.json'):
            p=info/name;p.write_text('pip' if name=='INSTALLER' else '{}',encoding='utf-8');extra.append(p)
        with (info/'RECORD').open('a',newline='',encoding='utf-8') as stream:
            csv.writer(stream).writerows([[p.relative_to(site).as_posix(),'',''] for p in extra])
        manifest=verify_stage(s.stage,s.service)
        guard_environment(s.site_paths,s.package,['telethon'],new_wheel=s.stage/manifest['wheel'],prefix=s.prefix)

    def test_tmpfiles_precreates_shared_lock(self):
        lines=(ROOT/'systemd/agentfleet-tmpfiles.conf').read_text(encoding='utf-8').splitlines()
        self.assertIn('d /run/agentfleet 2775 root agentfleet -',lines)
        self.assertIn('f /run/agentfleet/ollama.lock 0660 root agentfleet -',lines)

    def test_sqlite_backup_happens_after_stop(self):
        s=self.setup_service();runner=FakeCommands(s)
        def finish_writer():
            with closing(sqlite3.connect(s.database())) as db,db:db.execute('INSERT INTO sample VALUES("final-before-stop")')
        runner.on_stop=finish_writer
        result=Deployment(s,runner=runner).deploy()
        with closing(sqlite3.connect(Path(result['backup_path'])/'sqlite.backup')) as db:
            self.assertEqual(db.execute('SELECT value FROM sample').fetchall(),[('original',),('final-before-stop',)])

    def test_failure_after_start_preserves_database_bytes(self):
        s=self.setup_service();runner=FakeCommands(s);runner.errors=' INFO ordinary\n ERROR runtime failed\n'
        latest=[]
        def live_writer():
            with closing(sqlite3.connect(s.database())) as db,db:db.execute('INSERT INTO sample VALUES("new-live-data")')
            latest.append(s.database().read_bytes())
        runner.on_start=live_writer
        with self.assertRaises(DeploymentError):Deployment(s,runner=runner).deploy()
        self.assertEqual(s.database().read_bytes(),latest[0])
        result=json.loads((s.stage/'deployment.json').read_text())
        self.assertTrue(result['start_issued']);self.assertFalse(result['rollback']['database_restored'])

    def test_failure_before_start_restores_database(self):
        s=self.setup_service();runner=FakeCommands(s)
        class FailingBeforeStart(Deployment):
            def verify_installed(self,manifest):
                super().verify_installed(manifest)
                # Instrument a pre-start state change to distinguish restoring from preserving.
                with closing(sqlite3.connect(self.settings.database())) as db,db:db.execute('INSERT INTO sample VALUES("pre-start-change")')
                raise DeploymentError('injected before start')
        with self.assertRaises(DeploymentError):FailingBeforeStart(s,runner=runner).deploy()
        with closing(sqlite3.connect(s.database())) as db:self.assertEqual(db.execute('SELECT value FROM sample').fetchall(),[('original',)])
        result=json.loads((s.stage/'deployment.json').read_text())
        self.assertFalse(result['start_issued']);self.assertTrue(result['rollback']['database_restored'])

    def test_manual_rollback_preserves_db_without_restore_flag(self):
        s=self.setup_service();runner=FakeCommands(s);deploy=Deployment(s,runner=runner)
        deploy.system('stop');backup=deploy.backup()
        with closing(sqlite3.connect(s.database())) as db,db:db.execute('INSERT INTO sample VALUES("keep-this")')
        latest=s.database().read_bytes();result=deploy.rollback(backup,{'ActiveState':'active'})
        self.assertFalse(result['database_restored']);self.assertEqual(s.database().read_bytes(),latest)

    def test_ready_line_any_priority_and_missing_timeout(self):
        from dataclasses import replace
        s=replace(self.setup_service('nailhub'),ready_timeout=3)
        runner=FakeCommands(s);runner.ready=False
        now=[0]
        def sleep(seconds):now[0]+=seconds
        with self.assertRaisesRegex(DeploymentError,'ready owner_verified=1 missing'):
            Deployment(s,runner=runner,sleep=sleep,clock=lambda:now[0]).deploy()
        self.assertEqual(now[0],3)
        self.assertTrue(all('--priority=err' not in call[0] for call in runner.calls))

    def test_traceback_is_detected_without_journald_priority(self):
        s=self.setup_service();runner=FakeCommands(s);runner.errors='Traceback (most recent call last):\n'
        with self.assertRaisesRegex(DeploymentError,'error journal'):Deployment(s,runner=runner).deploy()

    def test_health_wait_production_default_and_path_binding(self):
        from dataclasses import replace
        self.assertGreaterEqual(Settings.__dataclass_fields__['health_wait'].default,30)
        s=replace(self.setup_service('nailhub'),test_paths=False,unit='tg-triage',other_unit='tg-nailhub')
        with self.assertRaisesRegex(DeploymentError,'--test-paths'):s.validate()
        import contextlib
        error=io.StringIO()
        with contextlib.redirect_stderr(error),self.assertRaises(SystemExit) as caught:
            deploy_main(['--service','nailhub','--stage',str(s.stage),'--unit','tg-triage'])
        self.assertEqual(caught.exception.code,2);self.assertIn('--test-paths',error.getvalue())

    def test_backup_requires_inactive_unit(self):
        s=self.setup_service();runner=FakeCommands(s)
        with self.assertRaisesRegex(DeploymentError,'Stop the target'):
            Deployment(s,runner=runner).backup()
        self.assertFalse(s.backup_root.exists())

    def test_cutover_order_pins_legacy_playwright_and_probes_before_enable(self):
        text=(ROOT/'deploy/README.md').read_text(encoding='utf-8')
        tokens=['## 1. Pause legacy','## 2. Remove the legacy','systemctl restart tg-triage.service',
                '## 3. Prove the legacy','## 4. Only now','mv /etc/tg-triage/nailhub.toml',
                '"playwright==$PLAYWRIGHT_VERSION"','-m nailhub login-telegram','-m nailhub probe',
                'systemctl enable --now tg-nailhub.service','## 5. Deploy the listener later']
        positions=[text.index(token) for token in tokens]
        self.assertEqual(positions,sorted(positions))
        self.assertIn("version('playwright')==os.environ['PLAYWRIGHT_VERSION']",text)
        self.assertIn('runuser',text);self.assertIn('is_relative_to(root)',text)
        self.assertNotIn('--test-paths',text)
        self.assertNotIn('Enable the existing listener only',(ROOT/'nailhub/README.md').read_text(encoding='utf-8'))


    def test_cutover_journal_filters_new_mainpid_and_docs_match_logging_streams(self):
        import ast
        text=(ROOT/'deploy/README.md').read_text(encoding='utf-8')
        capture='TRIAGE_PID=$(systemctl show tg-triage.service -p MainPID --value)'
        journal='journalctl -u tg-triage.service --since "$CUTOVER_SINCE" "_PID=$TRIAGE_PID"'
        self.assertGreater(text.index(capture),text.index('systemctl restart tg-triage.service'))
        self.assertGreater(text.index(journal),text.index(capture))
        self.assertIn('Listener logging writes to stdout; NailHub\nlogging writes to stderr',text)
        streams={}
        for service,package in (('listener','tg_triage'),('nailhub','nailhub')):
            tree=ast.parse((ROOT/service/'src'/package/'__main__.py').read_text(encoding='utf-8'))
            call=next(n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)
                      and isinstance(n.func.value,ast.Name) and n.func.value.id=='logging' and n.func.attr=='basicConfig')
            stream=next((k.value for k in call.keywords if k.arg=='stream'),None)
            if stream is None:streams[service]='stderr'
            else:
                self.assertIsInstance(stream,ast.Attribute);self.assertEqual(stream.value.id,'sys')
                streams[service]=stream.attr
        self.assertEqual(streams,{'listener':'stdout','nailhub':'stderr'})
        nh=(ROOT/'nailhub/README.md').read_text(encoding='utf-8')
        self.assertIn('Logging uses stderr',nh);self.assertIn('listener uses stdout',nh)

class WheelTests(unittest.TestCase):
    def test_record_and_reproducibility_for_both_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            for service in ('listener','nailhub'):
                a=Path(directory)/(service+'-a');b=Path(directory)/(service+'-b')
                first=stage_archive(service,a,fixture_archive(service),COMMIT)
                second=stage_archive(service,b,fixture_archive(service),COMMIT)
                self.assertEqual(first,second)
                data=(a/first['wheel']).read_bytes()
                self.assertTrue(verify_record(data));self.assertEqual(data,(b/second['wheel']).read_bytes())
                self.assertEqual(verify_stage(a,service),first)
                with zipfile.ZipFile(io.BytesIO(data)) as wheel:
                    metadata=next(n for n in wheel.namelist() if n.endswith('/METADATA'))
                    self.assertIn(b'Requires-Dist: telethon>=1.45,<2',wheel.read(metadata))
                    if service == 'nailhub':self.assertIn(b'Requires-Dist: playwright>=1.48,<2',wheel.read(metadata))
                    provenance=next(n for n in wheel.namelist() if n.endswith('/agentfleet.json'))
                    self.assertEqual(json.loads(wheel.read(provenance))['commit'],COMMIT)

    def test_actual_git_archive_commit_and_listener_prompt_data(self):
        with tempfile.TemporaryDirectory() as directory:
            a=Path(directory)/'a';b=Path(directory)/'b'
            manifest=build_stage('listener',a,repo=ROOT,commit='48bb761')
            repeat=build_stage('listener',b,repo=ROOT,commit='48bb761')
            self.assertEqual(manifest,repeat);self.assertEqual(verify_stage(a,'listener'),manifest)
            with zipfile.ZipFile(a/manifest['wheel']) as wheel:
                prompts=[n for n in wheel.namelist() if '.data/data/share/tg-triage/prompts/' in n]
                self.assertEqual(len(prompts),3)
                self.assertFalse(any('nailhub' in n for n in wheel.namelist()))
            for name in TEST_ROOT_FILES['listener']:
                expected=subprocess.run(['git','show','48bb761:'+name],cwd=ROOT,check=True,capture_output=True).stdout
                self.assertEqual((a/'source'/name).read_bytes(),expected)
            original=subprocess.run(['git','show','48bb761:listener/src/tg_triage/classifier.py'],cwd=ROOT,check=True,capture_output=True).stdout
            self.assertEqual((a/'source/listener/src/tg_triage/classifier.py').read_bytes(),original)

    def test_current_listener_wheel_packages_followup_prompt(self):
        from deploy.build_wheel import wheel_bytes
        name, data = wheel_bytes(ROOT/'listener', 'listener', COMMIT)
        self.assertTrue(verify_record(data))
        with zipfile.ZipFile(io.BytesIO(data)) as wheel:
            prompts = [n for n in wheel.namelist() if '.data/data/share/tg-triage/prompts/' in n]
            self.assertEqual(len(prompts), 4)
            followup = next(n for n in prompts if n.endswith('/followup.md'))
            self.assertEqual(wheel.read(followup), (ROOT/'listener/prompts/followup.md').read_bytes())

    def test_real_suites_from_real_staged_layout(self):
        # Pre-commit integration: archive actual worktree bytes, never smoke-test shapes.
        # Production build_stage obtains these same paths from one real git archive.
        tails={}
        with tempfile.TemporaryDirectory(prefix='agentfleet-real-stages-') as directory:
            for service in ('listener','nailhub'):
                files={service+'/'+name:data for name,data in inventory(ROOT/service).items()}
                for name in (*TEST_ROOT_FILES[service],*OPTIONAL_TEST_ROOT_FILES):
                    p=ROOT/name
                    if p.is_file():files[name]=p.read_bytes()
                stream=io.BytesIO()
                with tarfile.open(fileobj=stream,mode='w',format=tarfile.PAX_FORMAT,pax_headers={'comment':COMMIT}) as archive:
                    for name,data in sorted(files.items()):
                        member=tarfile.TarInfo(name);member.size=len(data);archive.addfile(member,io.BytesIO(data))
                stage=Path(directory)/service
                stage_archive(service,stage,stream.getvalue(),COMMIT)
                verify_stage(stage,service)
                spec=Settings(service,stage,Path(directory)/'unused-code',Path(directory)/'unused-config',
                              Path(directory)/'unused-state',Path(directory)/'unused-backups',sys.executable,(),
                              Path(sys.prefix),'unused-own','unused-other',health_wait=0)
                # Execute the production Step 2 implementation with the actual interpreter/runner.
                result=Deployment(spec).test_package()
                self.assertTrue(result['passed']);tails[service]=result['tail']
                count=int(next(line for line in result['tail'] if line.startswith('Ran ')).split()[1])
                self.assertGreaterEqual(count,582 if service=='listener' else 146)
                verify_stage(stage,service)
        print('REAL STAGED SUITES: '+json.dumps(tails,sort_keys=True))

    def test_record_detects_corrupt_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            stage=Path(directory)/'stage';m=stage_archive('listener',stage,fixture_archive('listener'),COMMIT)
            source=zipfile.ZipFile(stage/m['wheel']);stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as output:
                for name in source.namelist():output.writestr(name,b'changed' if name=='tg_triage/__init__.py' else source.read(name))
            source.close()
            with self.assertRaisesRegex(ValueError,'RECORD mismatch'):verify_record(stream.getvalue())

    def test_changed_staged_source_or_commit_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            stage=Path(directory)/'stage';stage_archive('listener',stage,fixture_archive('listener'),COMMIT)
            p=stage/'source/listener/src/tg_triage/__init__.py';p.write_bytes(b'tampered')
            with self.assertRaisesRegex(ValueError,'Staged source differs'):verify_stage(stage,'listener')
            m=json.loads((stage/'build.json').read_text(encoding='utf-8'));m['commit']='b'*40
            (stage/'build.json').write_text(json.dumps(m),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Git archive commit differs'):verify_stage(stage,'listener')

    def test_wheel_from_other_service_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            stage=Path(directory)/'stage';stage_archive('nailhub',stage,fixture_archive('nailhub'),COMMIT)
            with self.assertRaisesRegex(ValueError,'Stage service mismatch'):verify_stage(stage,'listener')


if __name__ == '__main__':
    unittest.main()
