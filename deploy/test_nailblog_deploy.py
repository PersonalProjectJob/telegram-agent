"""NailBlog deployment identity, reviewed snapshot and first-install safety."""
import json
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
    SECRET='SECRET-VALUE-MUST-NOT-APPEAR'

    def build(self, root):
        import sqlite3
        root=Path(root);units=root/'units';(units/'tg-nailblog.service.d').mkdir(parents=True)
        (units/'tg-nailblog.service').write_text('[Service]\nExecStart=/opt/x/python -m nailblog run\n')
        (units/'tg-nailblog.service.d/zz-daily4-reporting.conf').write_text('[Service]\nExecStart=\nExecStart=/opt/x/report_bridge.py\n')
        (units/'nailblog-daily4.timer').write_text('[Timer]\nOnCalendar=daily\n')
        (units/'unrelated.service').write_text('[Service]\nExecStart=/bin/true\n')
        db=root/'main.sqlite3'
        with closing(sqlite3.connect(db)) as c:
            c.executescript("CREATE TABLE control(id INTEGER PRIMARY KEY,paused INTEGER,image_mode TEXT,last_result TEXT);"
                            "INSERT INTO control VALUES(1,1,'subscription','paused');"
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
            self.assertNotIn(self.SECRET,text)
            self.assertTrue(data['systemd']['report_bridge_present'])
            self.assertEqual([d['name'] for d in data['systemd']['timers']],['nailblog-daily4.timer'])
            self.assertEqual([d['name'] for d in data['systemd']['units']],['tg-nailblog.service'])
            self.assertEqual(data['database']['control'],[{'id':1,'paused':1,'image_mode':'subscription','last_result':'paused'}])
            self.assertEqual([r['run_id'] for r in data['database']['onepost_missing_claims']],['r2'])
            self.assertEqual(data['database']['onepost_missing_claims'][0]['topic_id'],8)
            jobs={j['job']:j for j in data['queue']['jobs']}
            self.assertTrue(jobs['job-1']['marker']['exists']);self.assertTrue(jobs['job-1']['receipt']['exists'])
            self.assertEqual(jobs['job-1']['receipt']['fields']['status'],'complete')
            self.assertFalse(jobs['job-2']['marker']['exists']);self.assertFalse(jobs['job-2']['receipt']['exists'])
            self.assertEqual(data['ledger']['paid_intents_rows'],2)
            self.assertRegex(data['ledger']['paid_intents_sha256'],'^[0-9a-f]{64}$')
            self.assertEqual([p['pid'] for p in data['processes']['agy']],[123])
            self.assertEqual(data['config']['category_ids']['placeholder'],['hot-trend'])
            self.assertEqual(data['config']['category_ids']['slugs_missing'],['nail-salon','did-you-know'])
            self.assertFalse(data['config']['category_ids']['all_placeholder'])
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
                return 'nailblog-staging-a.service loaded failed failed x' if args[0]=='list-units' else 'enabled'
            data=json.loads(self.run_check([a for a in argv if a!='--no-systemctl'],stub))
            self.assertEqual(data['systemd']['failed'],['nailblog-staging-a.service'])
            self.assertEqual(data['systemd']['units'][0]['enabled'],'enabled')
            self.assertTrue(all(c[0] in ('is-enabled','is-active','list-units') for c in calls))
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


if __name__=='__main__':unittest.main()
