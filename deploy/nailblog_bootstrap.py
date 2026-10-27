"""Reviewable first-install plan for NailBlog; prints commands unless --apply is explicit.

No service is started/enabled, no Telegram login is automated, and no existing quota
database is replaced. Provision private configuration/proofs and migrate the complete
Cloudflare ledger separately before enabling generation.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path


def pinned_requirements(playwright_version, telethon_version, pillow_version, tzdata_version):
    versions = {'telethon':telethon_version, 'playwright':playwright_version,
                'Pillow':pillow_version, 'tzdata':tzdata_version}
    if any(not isinstance(v,str) or not re.fullmatch(r'[0-9]+(?:\.[0-9]+){1,3}',v) for v in versions.values()):
        raise ValueError('Exact reviewed numeric dependency versions required')
    return [name+'=='+version for name,version in versions.items()]


def plan(config, env_file, credential_file, *, playwright_version, telethon_version, pillow_version,
         tzdata_version, browser_source, repo):
    for path in (config, env_file, credential_file):
        if (not path.is_absolute() or path.is_symlink() or not path.is_file() or
                (os.name == 'posix' and path.stat().st_mode & 0o077)):
            raise ValueError('Private regular input files mode0600 required')
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', playwright_version):
        raise ValueError('Exact observed Playwright version required')
    requirements = pinned_requirements(playwright_version,telethon_version,pillow_version,tzdata_version)
    data = tomllib.loads(config.read_text(encoding='utf-8'))
    expected = {'db_path':'/var/lib/agentfleet/nailblog/nailblog.sqlite3'}
    if any(data.get('service', {}).get(key) != value for key,value in expected.items()):
        raise ValueError('NailBlog configuration must use its own database')
    if (data.get('telegram', {}).get('session') != '/var/lib/agentfleet/nailblog/nailblog.session' or
            data.get('admin', {}).get('profile_path') != '/var/lib/agentfleet/nailblog/browser' or
            data.get('admin', {}).get('credential_file') != '/etc/nailblog/private/admin-credentials.json'):
        raise ValueError('Separate NailBlog session/profile/credential paths required')
    if set(json.loads(credential_file.read_text(encoding='utf-8'))) != {'email','password'}:
        raise ValueError('Admin credential schema invalid')
    if browser_source != Path('/var/lib/agentfleet/nailhub/browsers') or browser_source.is_symlink() or not browser_source.is_dir():
        raise ValueError('Observed existing browser cache required')
    if any(Path(p).exists() for p in ('/opt/agentfleet/nailblog','/etc/nailblog',
                                     '/var/lib/agentfleet/nailblog','/etc/systemd/system/tg-nailblog.service')):
        raise ValueError('First-install only; use guarded deployment for an existing service')
    commands = [
        ['groupadd','--system','tgnailblog'],
        ['useradd','--system','--gid','agentfleet','--home-dir','/var/lib/agentfleet/nailblog','--shell','/usr/sbin/nologin','tgnailblog'],
        ['install','-d','-m','0755','/opt/agentfleet/nailblog'],
        ['install','-d','-m','0700','-o','tgnailblog','-g','agentfleet','/etc/nailblog','/etc/nailblog/private',
         '/var/lib/agentfleet/nailblog','/var/lib/agentfleet/nailblog/browser'],
        ['install','-m','0600','-o','tgnailblog','-g','agentfleet',str(config),'/etc/nailblog/nailblog.toml'],
        ['install','-m','0600','-o','tgnailblog','-g','agentfleet',str(credential_file),'/etc/nailblog/private/admin-credentials.json'],
        ['install','-m','0600',str(env_file),'/etc/agentfleet/tg-nailblog.env'],
        ['python3','-m','venv','/opt/agentfleet/nailblog/.venv'],
        ['/opt/agentfleet/nailblog/.venv/bin/python','-m','pip','install','--no-index','--find-links','/var/cache/agentfleet/wheels',
         *requirements],
        ['cp','-a',str(browser_source),'/var/lib/agentfleet/nailblog/browsers'],
        ['chown','-R','tgnailblog:agentfleet','/var/lib/agentfleet/nailblog/browsers'],
        ['install','-m','0644',str(repo/'systemd/tg-nailblog.service'),'/etc/systemd/system/tg-nailblog.service'],
        ['systemctl','daemon-reload'],
    ]
    return commands


def run_command(command, runner=subprocess.run):
    """Root-owned runtime code must remain readable to the unprivileged service."""
    readable = command[:3] == ['python3','-m','venv'] or command[:4] == [
        '/opt/agentfleet/nailblog/.venv/bin/python','-m','pip','install']
    previous = os.umask(0o022) if readable else None
    try:
        return runner(command,check=True,capture_output=True)
    finally:
        if previous is not None:
            os.umask(previous)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('config','env-file','credential-file','repo','browser-source'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--playwright-version',required=True)
    parser.add_argument('--telethon-version',required=True)
    parser.add_argument('--pillow-version',required=True)
    parser.add_argument('--tzdata-version',required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args(argv)
    commands=plan(args.config,args.env_file,args.credential_file,playwright_version=args.playwright_version,
                  telethon_version=args.telethon_version,pillow_version=args.pillow_version,tzdata_version=args.tzdata_version,
                  browser_source=args.browser_source,repo=args.repo)
    if args.apply:
        if os.name != 'posix' or os.geteuid() != 0:
            parser.error('Apply requires root on the confirmed THINKPAD')
        if subprocess.run(['getent','passwd','tgnailblog'],capture_output=True).returncode == 0:
            parser.error('Existing NailBlog identity requires reviewed recovery, not first-install provisioning')
        # First verify offline wheels can resolve BEFORE any OS/user/config mutation.
        subprocess.run([sys.executable,'-m','pip','download','--no-index','--find-links','/var/cache/agentfleet/wheels',
                        '--dest','/var/cache/agentfleet/wheels',
                        *pinned_requirements(args.playwright_version,args.telethon_version,args.pillow_version,args.tzdata_version)],
                       check=True,capture_output=True)
        for command in commands:
            if command[0] in ('groupadd','useradd'):
                kind = 'group' if command[0] == 'groupadd' else 'passwd'
                if subprocess.run(['getent',kind,'tgnailblog'],capture_output=True).returncode == 0:
                    continue
            run_command(command)
    print(json.dumps({'applied':args.apply,'commands':commands,'service_started':False,
                      'telegram_login':'Owner interactive login required','quota_migration':'complete account ledger required'},indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())
