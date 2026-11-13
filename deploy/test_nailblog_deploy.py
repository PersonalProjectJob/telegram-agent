"""NailBlog deployment identity, reviewed snapshot and first-install safety."""
import json
import os
import re
import tempfile
import unittest
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from deploy.agentfleet_deploy import DEFAULTS, Deployment, DeploymentError
from deploy.build_wheel import (TEST_ROOT_FILES, build_snapshot_stage, snapshot_digest, verify_stage)
from deploy.nailblog_bootstrap import plan, run_command, pinned_requirements
from deploy import test_deploy as legacy


class NailBlogDeploymentTests(unittest.TestCase):
    def fixture(self, root):
        root=Path(root)
        (root/'nailblog/src/nailblog').mkdir(parents=True)
        (root/'nailblog/tests').mkdir()
        (root/'nailblog/pyproject.toml').write_text('[project]\nname="nailblog"\nversion="0.1.0"\nrequires-python=">=3.11"\ndependencies=[]\n')
        (root/'nailblog/src/nailblog/__init__.py').write_text('VERSION="fixture"\n')
        (root/'nailblog/tests/test_fixture.py').write_text('FIXTURE=True\n')
        for name in TEST_ROOT_FILES['nailblog']:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture\n')
        return root

    def test_reviewed_dirty_snapshot_keeps_commit_null_and_exact_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.fixture(Path(tmp)/'repo');expected=snapshot_digest(root,'nailblog')
            manifest=build_snapshot_stage('nailblog',Path(tmp)/'stage',repo=root,reviewed_sha256=expected)
            self.assertIsNone(manifest['commit'])
            self.assertEqual(manifest['provenance'],'reviewed_worktree_snapshot')
            self.assertEqual(manifest['source_revision'],'snapshot:'+expected)
            self.assertEqual(verify_stage(Path(tmp)/'stage','nailblog'),manifest)
            (Path(tmp)/'stage/source/nailhub/src/nailhub/model_lock.py').write_text('tampered')
            with self.assertRaises(ValueError):verify_stage(Path(tmp)/'stage','nailblog')

    def test_snapshot_source_edit_after_review_refused_before_stage(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.fixture(Path(tmp)/'repo');expected=snapshot_digest(root,'nailblog')
            (root/'nailblog/src/nailblog/__init__.py').write_text('CHANGED=True')
            with self.assertRaisesRegex(ValueError,'Reviewed source snapshot changed'):
                build_snapshot_stage('nailblog',Path(tmp)/'stage',repo=root,reviewed_sha256=expected)
            self.assertFalse((Path(tmp)/'stage').exists())

    def test_reviewed_snapshot_preserves_deployed_policies_and_excludes_private_files(self):
        import shutil
        with tempfile.TemporaryDirectory() as tmp:
            root=self.fixture(Path(tmp)/'repo');deploy=root/'nailblog/deploy';deploy.mkdir()
            policies=('nailblog-image-only.md','nailblog-text-only.md')
            source=Path(__file__).resolve().parents[1]/'nailblog/deploy'
            for name in policies:shutil.copyfile(source/name,deploy/name)
            (deploy/'private').mkdir();(deploy/'private/admin.json').write_text('PRIVATE-FIXTURE')
            (deploy/'worker.env').write_text('PRIVATE-FIXTURE')
            expected=snapshot_digest(root,'nailblog');stage=Path(tmp)/'stage'
            manifest=build_snapshot_stage('nailblog',stage,repo=root,reviewed_sha256=expected)
            self.assertEqual(manifest['source_revision'],'snapshot:'+expected)
            self.assertIsNone(manifest['commit'])
            for name in policies:
                relative='nailblog/deploy/'+name
                self.assertIn(relative,manifest['source_files'])
                self.assertEqual((stage/'source'/relative).read_bytes(),(source/name).read_bytes())
            self.assertFalse((stage/'source/nailblog/deploy/private').exists())
            self.assertFalse((stage/'source/nailblog/deploy/worker.env').exists())
            self.assertEqual(verify_stage(stage,'nailblog'),manifest)
            (deploy/policies[0]).write_text('changed-after-review')
            with self.assertRaisesRegex(ValueError,'Reviewed source snapshot changed'):
                build_snapshot_stage('nailblog',Path(tmp)/'other-stage',repo=root,reviewed_sha256=expected)
            (stage/'source/nailblog/deploy'/policies[1]).write_text('tampered')
            with self.assertRaisesRegex(ValueError,'Staged source differs'):
                verify_stage(stage,'nailblog')

    def test_nailblog_own_defaults_service_schema_and_ready_label(self):
        self.assertEqual(DEFAULTS['nailblog']['unit'],'tg-nailblog')
        case=legacy.DeployTests();case.setUp()
        try:
            settings=case.setup_service('nailhub')
            settings.config.write_text('[service]\ndb_path="'+(settings.state_dir/'nailblog.sqlite3').as_posix()+'"\n')
            settings=replace(settings,service='nailblog')
            self.assertEqual(settings.database(),settings.state_dir/'nailblog.sqlite3')
            deployment=Deployment(settings)
            self.assertFalse(deployment.check_journal(['INFO NailHub ready owner_verified=1'],{}))
            self.assertTrue(deployment.check_journal(['INFO NailBlog ready owner_verified=1'],{}))
            with self.assertRaises(DeploymentError):deployment.check_journal([' ERROR failed'],{})
        finally:case.doCleanups()

    def test_unit_owns_profile_session_and_starts_paused(self):
        text=(Path(__file__).resolve().parents[1]/'systemd/tg-nailblog.service').read_text()
        for exact in ('User=tgnailblog','Group=agentfleet','NAILBLOG_CONFIG=/etc/nailblog/nailblog.toml',
                      'PLAYWRIGHT_BROWSERS_PATH=/var/lib/agentfleet/nailblog/browsers',
                      'python -m nailblog startup-gate','python -m nailblog run'):
            self.assertIn(exact,text)
        self.assertNotIn(' -m nailhub ',text)

    def test_every_unit_command_is_a_registered_nailblog_cli_command(self):
        # ExecStartPre runs a command the CLI must register, or the service can never start.
        root=Path(__file__).resolve().parents[1]
        unit=(root/'systemd/tg-nailblog.service').read_text()
        main=(root/'nailblog/src/nailblog/__main__.py').read_text(encoding='utf-8')
        choices=re.search(r"parser\.add_argument\('command', choices=\((.*?)\) \+ PIPELINES\)",main,re.S).group(1)
        registered=set(re.findall(r"'([a-z-]+)'",choices))
        used=re.findall(r'-m nailblog ([a-z-]+)',unit)
        self.assertIn('startup-gate',used)
        self.assertTrue(set(used)<=registered,sorted(set(used)-registered))
        self.assertIn("command == 'startup-gate'",main)

    def test_bootstrap_is_reviewable_and_never_starts_another_unit_or_copies_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=root/'config.toml';env=root/'env';credential=root/'admin.json'
            config.write_text('[service]\ndb_path="/var/lib/agentfleet/nailblog/nailblog.sqlite3"\n'
                              '[telegram]\nsession="/var/lib/agentfleet/nailblog/nailblog.session"\n'
                              '[admin]\nprofile_path="/var/lib/agentfleet/nailblog/browser"\n'
                              'credential_file="/etc/nailblog/private/admin-credentials.json"\n')
            env.write_text('FIXTURE_ONLY=1\n');credential.write_text('{"email":"fixture@example.test","password":"fixture-only"}')
            for p in (config,env,credential):p.chmod(0o600)
            with patch.object(Path,'is_dir',return_value=True):
                commands=plan(config,env,credential,playwright_version='1.63.0',
                              telethon_version='1.45.0',pillow_version='13.0.0',tzdata_version='2026.5',
                              browser_source=Path('/var/lib/agentfleet/nailhub/browsers'),repo=root)
            self.assertIn(['groupadd','--system','tgnailblog'],commands)
            self.assertIn(['systemctl','daemon-reload'],commands)
            self.assertFalse(any(c[0]=='systemctl' and c[1] in ('start','enable','restart','stop') for c in commands))
            self.assertFalse(any('nailhub.session' in str(c) or 'jobs.sqlite3' in str(c) for c in commands))
            self.assertFalse(any('fixture-only' in str(c) for c in commands))
            pip=[c for c in commands if c[:4]==['/opt/agentfleet/nailblog/.venv/bin/python','-m','pip','install']][0]
            self.assertEqual(pip[-4:],['telethon==1.45.0','playwright==1.63.0','Pillow==13.0.0','tzdata==2026.5'])

    def test_dependency_pins_refuse_ranges_and_shell_text(self):
        self.assertEqual(pinned_requirements('1.63.0','1.45.0','13.0.0','2026.5'),
                         ['telethon==1.45.0','playwright==1.63.0','Pillow==13.0.0','tzdata==2026.5'])
        for version in ('>=10','13.0.0; echo x','latest',''):
            with self.assertRaises(ValueError):pinned_requirements('1.63.0','1.45.0',version,'2026.5')

    def test_runtime_venv_install_uses_readable_umask_and_restores_on_failure(self):
        from unittest.mock import Mock
        for cmd in (['python3','-m','venv','/opt/agentfleet/nailblog/.venv'],
                    ['/opt/agentfleet/nailblog/.venv/bin/python','-m','pip','install','fixture']):
            with patch('deploy.nailblog_bootstrap.os.umask',return_value=0o077) as umask:
                with self.assertRaises(RuntimeError):run_command(cmd,runner=Mock(side_effect=RuntimeError('fixture')))
                self.assertEqual([c.args[0] for c in umask.call_args_list],[0o022,0o077])
        with patch('deploy.nailblog_bootstrap.os.umask') as umask:
            run_command(['install','-m','0600','private-source','private-target'],runner=Mock())
            umask.assert_not_called()


class CutoverCheckTests(unittest.TestCase):
    SECRET='SECRET-VALUE-MUST-NOT-APPEAR';BINDING='BINDING-MUST-NOT-APPEAR'

    def build(self, root):
        import sqlite3
        root=Path(root);units=root/'units';(units/'tg-nailblog.service.d').mkdir(parents=True)
        (units/'tg-nailblog.service').write_text('[Service]\nExecStart=/opt/x/python -m nailblog run\n')
        (units/'tg-nailblog.service.d/zz-daily4-reporting.conf').write_text('[Service]\nExecStart=\nExecStart=/opt/x/report_bridge.py\n')
        (units/'nailblog-daily4.timer').write_text('[Timer]\nOnCalendar=daily\n')
        (units/'unrelated.service').write_text('[Service]\nExecStart=/bin/true\n')
        db=root/'main.sqlite3'
        with closing(sqlite3.connect(db)) as c:
            c.executescript("CREATE TABLE control(id INTEGER PRIMARY KEY,paused INTEGER,image_mode TEXT,last_result TEXT,armed_binding TEXT NOT NULL DEFAULT '');"
                            "INSERT INTO control VALUES(1,1,'subscription','paused','BINDING-MUST-NOT-APPEAR');"
                            "CREATE TABLE onepost_runs(run_id TEXT PRIMARY KEY,binding TEXT,phase TEXT,document TEXT,post_id INTEGER,image_approval TEXT);"
                            "CREATE TABLE onepost_topic_claims(topic_id INTEGER PRIMARY KEY,run_id TEXT UNIQUE);"
                            "INSERT INTO onepost_runs VALUES('r1','b','done','{\"topic_id\": 7}',1,'');"
                            "INSERT INTO onepost_runs VALUES('r2','b','text','{\"topic_id\": 8}',2,'');"
                            "INSERT INTO onepost_topic_claims VALUES(7,'r1');")
        ledger=root/'ledger.sqlite3'
        with closing(sqlite3.connect(ledger)) as c:
            c.executescript("CREATE TABLE paid_intents(id TEXT PRIMARY KEY,status TEXT,reserved INTEGER,at REAL);"
                            "INSERT INTO paid_intents VALUES('a','settled',10,1.0);INSERT INTO paid_intents VALUES('b','uncertain',5,2.0);")
        queue=root/'queue';job=queue/'job-1';job.mkdir(parents=True);(job/'request.json').write_text('{"prompt":"%s"}'%self.SECRET)
        (job/'receipt.json').write_text('{"status":"complete","protocol":"nailblog-agy-text-v1","job_id":"job-1","secret":"%s"}'%self.SECRET)
        (queue/'job-2').mkdir()
        private=root/'private';private.mkdir();(private/'job-1.json').write_text('{"day":"2026-10-27","kind":"text","run_id":"r1","x":"%s"}'%self.SECRET)
        config=root/'nailblog.toml'
        config.write_text('[service]\nslots=["a","b","c","d"]\n[admin.category_ids]\nhot-trend="a1b2c3d4-e5f6-7890-abcd-ef1234567801"\n'
                          'nail-job="11111111-2222-3333-4444-555555555555"\n')
        env=root/'env';env.write_text('OTHER=1\nNAILBLOG_GEMINI_IMAGE_KEY=%s\n'%self.SECRET)
        proc=root/'proc';(proc/'123').mkdir(parents=True);(proc/'123/cmdline').write_bytes(b'/snap/bin/agy\0--prompt\0'+self.SECRET.encode()+b'\0')
        (proc/'123/comm').write_text('agy\n');(proc/'456').mkdir();(proc/'456/cmdline').write_bytes(b'/bin/sleep\0')
        return ['--unit-dir',str(units),'--db',str(db),'--ledger',str(ledger),'--queue',str(queue),'--private-dir',str(private),
                '--config',str(config),'--env-file',str(env),'--proc-root',str(proc),'--lock',str(root/'ollama.lock'),'--no-systemctl']

    def snapshot(self, root):
        import hashlib
        return {str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(root).rglob('*')) if p.is_file()}

    def run_check(self, argv, systemctl=None):
        import contextlib, io
        from deploy import nailblog_cutover_check as check
        out=io.StringIO()
        with contextlib.redirect_stdout(out):
            code=check.main(argv,systemctl=systemctl) if systemctl else check.main(argv)
        self.assertEqual(code,0)
        return out.getvalue()

    def test_inventory_reports_every_section_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv=self.build(tmp);before=self.snapshot(tmp)
            text=self.run_check(argv);data=json.loads(text)
            self.assertEqual(self.snapshot(tmp),before)
            self.assertNotIn(self.SECRET,text);self.assertNotIn(self.BINDING,text)
            self.assertTrue(data['systemd']['report_bridge_present'])
            self.assertEqual([d['name'] for d in data['systemd']['timers']],['nailblog-daily4.timer'])
            self.assertEqual([d['name'] for d in data['systemd']['units']],['tg-nailblog.service'])
            self.assertEqual(data['database']['control'],[{'paused':1,'image_mode':'subscription','last_result':'paused','armed':True}])
            self.assertEqual([r['run_id'] for r in data['database']['onepost_missing_claims']],['r2'])
            self.assertEqual(data['database']['onepost_missing_claims'][0]['topic_id'],8)
            jobs={j['job']:j for j in data['queue']['jobs']}
            self.assertTrue(jobs['job-1']['marker']['exists']);self.assertTrue(jobs['job-1']['receipt']['exists'])
            self.assertEqual(jobs['job-1']['receipt']['fields']['status'],'complete')
            self.assertFalse(jobs['job-2']['marker']['exists']);self.assertFalse(jobs['job-2']['receipt']['exists'])
            self.assertEqual(data['ledger']['paid_intents_rows'],2)
            self.assertRegex(data['ledger']['paid_intents_sha256'],'^[0-9a-f]{64}$')
            self.assertEqual([p['pid'] for p in data['processes']['agy']],[123])
            self.assertEqual(data['config']['category_ids']['pattern_suspect'],['hot-trend'])
            self.assertEqual(data['config']['category_ids']['slugs_missing'],['nail-salon','did-you-know'])
            self.assertFalse(data['config']['category_verification']['pass'])
            self.assertIs(data['env']['gemini_image_key_present'],True)

    def test_ledger_digest_is_stable_and_follows_content(self):
        import sqlite3
        from deploy import nailblog_cutover_check as check
        with tempfile.TemporaryDirectory() as tmp:
            argv=self.build(tmp);ledger=argv[argv.index('--ledger')+1]
            first=check.ledger_digest(ledger)['paid_intents_sha256'];self.assertEqual(first,check.ledger_digest(ledger)['paid_intents_sha256'])
            with closing(sqlite3.connect(ledger)) as c:c.execute("UPDATE paid_intents SET status='settled' WHERE id='b'");c.commit()
            self.assertNotEqual(first,check.ledger_digest(ledger)['paid_intents_sha256'])

    def test_missing_paths_and_systemctl_stub(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv=self.build(tmp);before=self.snapshot(tmp)
            calls=[]
            def stub(args):
                calls.append(args)
                if args[0]=='list-units':return 'nailblog-staging-a.service loaded failed failed x'
                if args[0]=='list-timers':return ''
                if args[0]=='is-active':return 'failed' if args[1]=='nailblog-staging-a.service' else 'inactive'
                return 'enabled'
            data=json.loads(self.run_check([a for a in argv if a!='--no-systemctl'],stub))
            self.assertEqual(data['systemd']['failed'],['nailblog-staging-a.service'])
            self.assertEqual(data['systemd']['units'][0]['enabled'],'enabled')
            self.assertTrue(all(c[0] in ('is-enabled','is-active','list-units','list-timers') for c in calls))
            self.assertEqual(self.snapshot(tmp),before)
            empty=json.loads(self.run_check(['--unit-dir',tmp+'/none','--db',tmp+'/none.db','--ledger',tmp+'/none.db','--queue',tmp+'/none',
                                             '--config',tmp+'/none.toml','--env-file',tmp+'/none.env','--proc-root',tmp+'/none','--no-systemctl']))
            self.assertFalse(empty['database']['exists']);self.assertFalse(empty['queue']['exists'])
            self.assertIsNone(empty['env']['gemini_image_key_present'])

    def test_script_refuses_mutating_systemctl_subcommands(self):
        from deploy import nailblog_cutover_check as check
        for verb in ('restart','stop','mask','enable','disable','reset-failed'):
            with self.assertRaises(ValueError):check.run_systemctl([verb,'tg-nailblog'])

    def test_service_units_match_agy_only_target(self):
        text=(Path(__file__).resolve().parents[1]/'systemd/tg-nailblog.service').read_text()
        self.assertNotIn('ollama.service',text);self.assertNotIn('cloudflare-quota',text)
        self.assertRegex(text,r'(?m)^TimeoutStopSec=660$')
        self.assertIn('ExecStartPre=/opt/agentfleet/nailblog/.venv/bin/python -m nailblog startup-gate',text)
        for keep in ('NoNewPrivileges=yes','ProtectSystem=strict','ProtectHome=yes','UMask=0077'):self.assertIn(keep,text)
        self.assertNotIn('/ledger',text)
        worker=(Path(__file__).resolve().parents[1]/'nailblog/deploy/tg-nailblog-subscription.service').read_text()
        self.assertGreaterEqual(int(re.search(r'(?m)^TimeoutStopSec=(\d+)$',worker).group(1)),630)



    def test_cleanly_closed_wal_db_gets_no_sidecars_and_keeps_its_sha(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            argv=self.build(tmp);db=Path(argv[argv.index('--db')+1]);ledger=Path(argv[argv.index('--ledger')+1])
            for path in (db,ledger):
                with closing(sqlite3.connect(path)) as c:
                    c.execute('PRAGMA journal_mode=WAL');c.commit()
                for suffix in ('-wal','-shm'):
                    self.assertFalse(Path(str(path)+suffix).exists(),suffix)   # closed cleanly: no sidecars
            before=self.snapshot(tmp);listing=sorted(os.listdir(tmp))
            data=json.loads(self.run_check(argv))
            self.assertEqual(data['database']['control'][0]['paused'],1)
            self.assertEqual(self.snapshot(tmp),before)
            for path in (db,ledger):
                self.assertFalse(Path(str(path)+'-wal').exists());self.assertFalse(Path(str(path)+'-shm').exists())
            self.assertEqual(sorted(os.listdir(tmp)),listing)

    def test_every_sqlite_open_is_read_only_and_on_a_private_copy(self):
        import sqlite3
        from urllib.parse import urlparse, unquote
        real=sqlite3.connect;seen=[]
        def spy(target,*args,**kwargs):
            seen.append(target);return real(target,*args,**kwargs)
        with tempfile.TemporaryDirectory() as tmp:
            argv=self.build(tmp);originals={Path(argv[argv.index(f)+1]).resolve() for f in ('--db','--ledger')}
            with patch('deploy.nailblog_cutover_check.sqlite3.connect',spy):
                self.run_check(argv)
            self.assertGreaterEqual(len(seen),2)
            for uri in seen:
                self.assertTrue(str(uri).startswith('file:'),uri)
                self.assertIn('mode=ro',str(uri))
                opened=Path(unquote(urlparse(str(uri)).path.lstrip('/') if os.name=='nt' else urlparse(str(uri)).path)).resolve()
                self.assertNotIn(opened,originals)
                self.assertIn('nailblog-cutover-',str(opened))

    def test_control_row_is_whitelisted_and_never_prints_the_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv=self.build(tmp);text=self.run_check(argv);control=json.loads(text)['database']['control'][0]
            self.assertEqual(set(control),{'paused','image_mode','last_result','armed'})
            self.assertIs(control['armed'],True);self.assertNotIn(self.BINDING,text)


    def test_bootstrap_no_longer_creates_cloudflare_quota_dir(self):
        text=(Path(__file__).resolve().parent/'nailblog_bootstrap.py').read_text()
        self.assertNotIn('cloudflare-quota',text)

    def test_runtime_and_all_unit_roots_fail_closed_for_legacy_lanes(self):
        from deploy import nailblog_cutover_check as check
        with tempfile.TemporaryDirectory() as tmp:
            etc,transient,lib=(Path(tmp)/p for p in ('etc','transient','lib'))
            for root in (etc,transient,lib):root.mkdir()
            (etc/'nailblog-daily4.service').write_text('[Service]\nExecStart=/old/daily4\n')
            (transient/'nailblog-makeup-old.service').write_text('[Service]\nExecStart=/old/makeup\n')
            (lib/'nailblog-staging-old.timer').write_text('[Timer]\nOnCalendar=daily\n')
            calls=[]
            def stub(args):
                calls.append(args)
                if args[0]=='list-units':return 'nailblog-makeup-old.service loaded active running x\nnailblog-staging-runtime.service loaded active running x'
                if args[0]=='list-timers':return 'Tue 2026-10-27 x nailblog-staging-old.timer nailblog-staging-runtime.service'
                if args[0]=='is-active':return 'active' if 'makeup' in args[1] or 'runtime' in args[1] else 'inactive'
                return 'masked' if 'daily4' in args[1] else 'transient'
            data=check.inventory_units(etc,stub,(transient,lib))
            self.assertFalse(data['legacy_gate']['pass'])
            units={u['name']:u for u in data['units']+data['timers']}
            self.assertEqual(units['nailblog-makeup-old.service']['path'],str(transient/'nailblog-makeup-old.service'))
            self.assertEqual(units['nailblog-staging-old.timer']['path'],str(lib/'nailblog-staging-old.timer'))
            self.assertIsNone(units['nailblog-staging-runtime.service']['path'])
            self.assertTrue(units['nailblog-daily4.service']['masked'])
            self.assertIn('runtime_timer_present:nailblog-staging-old.timer',data['legacy_gate']['reasons'])
            self.assertIn(['list-units','--all','--plain','--no-legend','*nailblog*'],calls)
            self.assertIn(['list-timers','--all','--plain','--no-legend'],calls)
            self.assertFalse(check.inventory_units(etc,None)['legacy_gate']['pass'])
            (transient/'nailblog-makeup-old.service').unlink()   # transient must disappear before PASS
            def safe(args):return '' if args[0].startswith('list-') else 'inactive' if args[0]=='is-active' else 'masked'
            self.assertTrue(check.inventory_units(etc,safe,(transient,lib))['legacy_gate']['pass'])
            def unknown(args):return None if args[0]=='list-timers' else safe(args)
            self.assertFalse(check.inventory_units(etc,unknown)['legacy_gate']['pass'])

    def test_legacy_requires_persistent_mask_or_verified_removal(self):
        from deploy import nailblog_cutover_check as check
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'nailblog-daily4.service';path.write_text('[Service]\nExecStart=/old/daily4\n')
            for enabled in ('disabled','static','masked-runtime','masked'):
                with self.subTest(enabled=enabled):
                    def stub(args):
                        if args[0].startswith('list-'):return ''
                        return 'inactive' if args[0]=='is-active' else enabled
                    data=check.inventory_units(tmp,stub)
                    self.assertEqual(data['legacy_gate']['pass'],enabled=='masked')
                    self.assertEqual(data['units'][0]['persistent_masked'],enabled=='masked')
                    if enabled!='masked':
                        self.assertIn('legacy_not_persistently_masked:'+path.name,data['legacy_gate']['reasons'])
            path.unlink()
            self.assertTrue(check.inventory_units(tmp,stub)['legacy_gate']['pass'])

    def test_every_unit_query_fails_closed_and_preserves_errors(self):
        import subprocess
        from deploy import nailblog_cutover_check as check
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'tg-nailblog-subscription.service').write_text('[Service]\nExecStart=/worker\n')
            for result,error in ((None,'unavailable'),('unexpected','invalid_result'),
                                 ({'error':'TimeoutExpired'},'TimeoutExpired'),
                                 ({'error':'command_failed','returncode':1},'command_failed')):
                for query in ('is-active','is-enabled'):
                    with self.subTest(result=result,query=query):
                        def stub(args):
                            if args[0].startswith('list-'):return ''
                            if args[0]==query:return result
                            return 'active' if args[0]=='is-active' else 'enabled'
                        data=check.inventory_units(tmp,stub)
                        self.assertFalse(data['legacy_gate']['pass'])
                        self.assertEqual(data['query_errors'][0]['query'],query)
                        self.assertEqual(data['query_errors'][0]['unit'],'tg-nailblog-subscription.service')
                        self.assertEqual(data['query_errors'][0]['error'],error)
            with patch('deploy.nailblog_cutover_check.subprocess.run',side_effect=subprocess.TimeoutExpired('systemctl',20)):
                self.assertEqual(check.run_systemctl(['is-active','tg-nailblog-subscription.service']),{'error':'TimeoutExpired'})
            with patch('deploy.nailblog_cutover_check.subprocess.run',return_value=subprocess.CompletedProcess([],1,'','private stderr')):
                self.assertEqual(check.run_systemctl(['is-enabled','tg-nailblog-subscription.service']),{'error':'command_failed','returncode':1})

    def test_unfinished_approved_posts_conflicts_binding_and_queue_are_private(self):
        import hashlib,sqlite3
        from deploy import nailblog_cutover_check as check
        with tempfile.TemporaryDirectory() as tmp:
            argv=self.build(tmp);db=Path(argv[argv.index('--db')+1])
            (Path(tmp)/'units/tg-nailblog.service').write_text('ExecStart=/old/bridge --private='+self.SECRET+'\n')
            with closing(sqlite3.connect(db)) as c:
                c.executescript("CREATE TABLE posts(id INTEGER,topic_id INTEGER,state TEXT,slot_at TEXT,draft_json TEXT);"
                                "CREATE TABLE autoflow_runs(topic_id INTEGER);"
                                "CREATE TABLE admin_operations(post_id INTEGER,approval TEXT,publish_day TEXT);"
                                "CREATE TABLE admin_binding(id INTEGER,value TEXT);")
                for i,state in enumerate(('scheduled','before_publish','uncertain','approved','review','writing','published','held'),1):
                    c.execute('INSERT INTO posts VALUES(?,?,?,?,?)',(i,i,state,'2026-10-28T06:00:00+07:00',self.SECRET))
                c.execute('INSERT INTO autoflow_runs VALUES(1)')
                c.execute('INSERT INTO admin_operations VALUES(?,?,?)',(1,self.SECRET,''))
                c.execute('INSERT INTO admin_operations VALUES(?,?,?)',(7,self.SECRET,'2026-10-27'))
                c.execute('INSERT INTO admin_binding VALUES(1,?)',(self.BINDING,))
                c.execute('INSERT INTO onepost_runs VALUES(?,?,?,?,?,?)',('r3','b','done','{"topic_id": 8}',3,''))
                c.commit()
            job=Path(argv[argv.index('--queue')+1])/'job-1'
            (job/'request.json').write_text(json.dumps({'kind':'text','expires_at':123,'prompt':self.SECRET}))
            sample=Path(tmp)/'sample.json'
            sample.write_text(json.dumps({'items':[{'content':self.SECRET}],'pagination':{'page':1,'totalPages':2}}))
            before=self.snapshot(tmp)
            text=self.run_check(argv+['--admin-listing-sample',str(sample)]);data=json.loads(text)
            self.assertEqual(self.snapshot(tmp),before)
            self.assertNotIn(self.SECRET,text);self.assertNotIn(self.BINDING,text)
            posts=data['database']['unfinished_posts']
            self.assertEqual([p['state'] for p in posts],['scheduled','before_publish','uncertain','approved','review','writing'])
            self.assertTrue(posts[0]['autoflow_member']);self.assertFalse(posts[1]['autoflow_member'])
            self.assertEqual(data['database']['approved_unpublished_admin_operations'],
                             [{'post_id':1,'state':'scheduled','slot_at':'2026-10-28T06:00:00+07:00'}])
            self.assertEqual(data['database']['legacy_topic_claim_conflict'],[{'topic_id':8,'run_ids':['r2','r3']}])
            self.assertEqual(data['database']['admin_binding'],[{'id':1,'sha256':hashlib.sha256(self.BINDING.encode()).hexdigest()}])
            self.assertEqual(data['queue']['jobs'][0]['request'],{'kind':'text','expires_at':123})
            self.assertEqual(data['queue']['jobs'][0]['kind'],'text')
            self.assertEqual(data['queue']['jobs'][0]['expires_at'],123)
            self.assertEqual(data['admin_listing_sample']['object_keys'][1],{'path':'$.pagination','keys':['page','totalPages']})
            sample.write_text('[]');self.assertEqual(check.inspect_listing_sample(sample)['error'],'listing_not_object')
            sample.write_text('invalid');self.assertEqual(check.inspect_listing_sample(sample)['error'],'unreadable_json')

    def test_seeded_category_ids_require_fresh_authenticated_actual_options(self):
        import time
        from deploy import nailblog_cutover_check as check
        ids=dict(zip(check.CATEGORIES,('f1b2c3d4-e5f6-7890-abcd-ef1234567806','c1b2c3d4-e5f6-7890-abcd-ef1234567803',
                                      'b1b2c3d4-e5f6-7890-abcd-ef1234567802','a1b2c3d4-e5f6-7890-abcd-ef1234567801')))
        contract={'environment':'staging','site_origin':'https://staging.nailhub.ai','api_origin':'https://api-staging.nailhub.ai'}
        report={**contract,'authenticated':True,'writes_performed':False,'editor_roundtrip':True,
                'categories':[{'label':s.replace('-',' ').title(),'id':i} for s,i in ids.items()]}
        with tempfile.TemporaryDirectory() as tmp:
            probe=Path(tmp)/'probe.json';config=Path(tmp)/'config.toml'
            from deploy.nailblog_config_migrate import toml_text
            config.write_text(toml_text({'contract':contract,'admin':{'category_ids':ids}}))
            probe.write_text(json.dumps(report));before=self.snapshot(tmp)
            result=check.inspect_config(config,probe)
            self.assertEqual(result['category_ids']['pattern_suspect'],['did-you-know'])
            self.assertTrue(result['category_verification']['pass'])
            self.assertEqual(self.snapshot(tmp),before)
            self.assertFalse(check.inspect_config(config)['category_verification']['pass'])
            changed={**report,'categories':report['categories'][:-1]}
            probe.write_text(json.dumps(changed));self.assertFalse(check.inspect_config(config,probe)['category_verification']['pass'])
            for changed in ({**report,'authenticated':False},{**report,'writes_performed':True},
                            {**report,'api_origin':'https://api.nailhub.ai'},
                            {**report,'categories':report['categories']+[report['categories'][0]]}):
                probe.write_text(json.dumps(changed));self.assertFalse(check.inspect_config(config,probe)['category_verification']['pass'])
            probe.write_text(json.dumps(report));old=time.time()-86401;os.utime(probe,(old,old))
            self.assertEqual(check.inspect_config(config,probe)['category_verification']['error'],'stale_admin_probe')


class ConfigMigrationTests(unittest.TestCase):
    def old_config(self):
        return '''[service]
mode="live"
db_path="/var/lib/agentfleet/nailblog/nailblog.sqlite3"
owner_ids=[123]
owner_username="SotaThao"
slots=["06:00","19:00"]
[contract]
environment="staging"
site_origin="https://staging.nailhub.ai"
api_origin="https://api-staging.nailhub.ai"
verified=true
verification_ref="/etc/nailblog/probe.json"
[admin]
profile_path="/var/lib/agentfleet/nailblog/browser"
credential_file="/etc/nailblog/private/admin.json"
daily_publish_limit=2
[admin.category_ids]
hot-trend="a1b2c3d4-e5f6-7890-abcd-ef1234567801"
[budget]
daily_usd=2.0
ledger_db_path="/legacy/unchanged.sqlite3"
[cloudflare]
account_id="retired"
[text_model]
provider="ollama"
url="http://localhost:11434"
num_predict=1000
[image]
provider="gemini"
key_file="/old/key"
key_source="env"
review_bypass=false
[subscription]
queue_dir="/var/lib/nailblog-subscription/queue"
private_dir="/private/worker"
max_daily_jobs=17
[autoflow]
enabled=true
run_usd=1.0
runs_dir="/var/lib/agentfleet/nailblog/runs"
[daily.slot_categories]
"06:00"=["hot-trend"]
"19:00"=["nail-salon"]
'''

    def test_migration_loads_real_schema_preserves_identity_and_never_overwrites(self):
        import tomllib
        from nailblog.config import load_config,REMOVED_KEYS,REMOVED_TABLES
        from deploy.nailblog_config_migrate import migrate
        with tempfile.TemporaryDirectory() as tmp:
            old,new=Path(tmp)/'old.toml',Path(tmp)/'new.toml';old.write_text(self.old_config())
            original=old.read_bytes();migrate(old,new);config=load_config(new)
            self.assertEqual(old.read_bytes(),original)
            self.assertEqual(config.slots,('06:00','10:00','15:00','19:00'))
            self.assertEqual(config.mode,'preview');self.assertFalse(config.autoflow.enabled)
            self.assertEqual(config.owner_ids,(123,));self.assertEqual(config.budget.ledger_db_path,'/legacy/unchanged.sqlite3')
            self.assertEqual(config.text_model.provider,'agy');self.assertEqual(config.image.provider,'subscription')
            self.assertFalse(config.image.review_bypass);self.assertEqual(config.admin.category_ids,{})
            self.assertEqual(config.subscription.max_daily_jobs,17)
            before=tomllib.loads(original.decode());after=tomllib.loads(new.read_text())
            self.assertEqual(before['contract'],after['contract']);self.assertEqual(before['subscription'],after['subscription'])
            self.assertEqual(before['admin']['credential_file'],after['admin']['credential_file'])
            for name in REMOVED_TABLES:self.assertNotIn(name,after)
            for section,keys in REMOVED_KEYS.items():
                for key in keys:self.assertNotIn(key,after.get(section,{}))
            output=new.read_bytes()
            with self.assertRaises(FileExistsError):migrate(old,new)
            with self.assertRaises(FileExistsError):migrate(old,old)
            self.assertEqual(new.read_bytes(),output);self.assertEqual(old.read_bytes(),original)

    def test_invalid_or_unknown_config_does_not_publish_output(self):
        from deploy.nailblog_config_migrate import migrate
        with tempfile.TemporaryDirectory() as tmp:
            old,new=Path(tmp)/'old.toml',Path(tmp)/'new.toml'
            for text in (self.old_config().replace('owner_ids=[123]','owner_ids=[]'),
                         self.old_config()+'\n[unknown]\nvalue="must fail"\n'):
                old.write_text(text)
                with self.assertRaises(ValueError):migrate(old,new)
                self.assertFalse(new.exists())

    def test_manual_rollback_no_start_preserves_default_and_database(self):
        for no_start in (False,True):
            with self.subTest(no_start=no_start):
                case=legacy.DeployTests();case.setUp()
                try:
                    settings=case.setup_service();runner=legacy.FakeCommands(settings)
                    deploy=Deployment(settings,runner=runner);deploy.system('stop');backup=deploy.backup()
                    database=settings.database().read_bytes();original=settings.config.read_bytes()
                    settings.config.write_text('changed');runner.actions.clear()
                    deploy.rollback(backup,{'ActiveState':'active'},no_start=no_start)
                    self.assertEqual(settings.config.read_bytes(),original)
                    self.assertEqual(settings.database().read_bytes(),database)
                    self.assertEqual(runner.actions,[('stop',settings.unit)] if no_start else [('stop',settings.unit),('start',settings.unit)])
                finally:case.doCleanups()

    def test_no_start_requires_manual_rollback(self):
        from deploy.agentfleet_deploy import main
        with self.assertRaises(SystemExit) as caught:
            main(['--service','nailblog','--stage','unused','--no-start'])
        self.assertEqual(caught.exception.code,2)

    def test_failed_deployment_can_leave_automatic_rollback_stopped(self):
        case=legacy.DeployTests();case.setUp()
        try:
            settings=replace(case.setup_service(),rollback_no_start=True)
            runner=legacy.FakeCommands(settings);runner.fail_install=True
            with self.assertRaises(DeploymentError):Deployment(settings,runner=runner).deploy()
            self.assertNotIn(('start',settings.unit),runner.actions)
            self.assertEqual(runner.states[settings.unit]['ActiveState'],'inactive')
            result=json.loads((settings.stage/'deployment.json').read_text())
            self.assertTrue(result['rollback']['restored']);self.assertFalse(result['start_issued'])
        finally:case.doCleanups()


if __name__=='__main__':unittest.main()
