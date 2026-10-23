"""NailBlog deployment identity, reviewed snapshot and first-install safety."""
import json
import tempfile
import unittest
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


if __name__=='__main__':unittest.main()
