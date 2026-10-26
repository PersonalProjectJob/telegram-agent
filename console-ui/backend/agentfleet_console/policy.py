"""Read-only effective systemd binding gate; never trusts browser host/path data."""
import re
import shlex
import sys
import os

from .errors import APIError
from .compatibility import _ancestors,Unverified

PROPERTIES=('LoadState','ExecStart','User','Group','SupplementaryGroups','DropInPaths','Environment','EnvironmentFiles','WorkingDirectory')

def verify_binding(binding,runner):
    """Unknown systemd output or environment configuration fails closed.

    EnvironmentFile contents may contain credentials, so are never opened.
    Such files make effective source/configuration provenance unverified.
    """
    def denied():raise APIError('binding_unverified','Effective worker configuration binding could not be verified.',409)
    if sys.platform!='linux':denied()
    try:
        result=runner(['systemctl','show',binding.unit,'--property='+','.join(PROPERTIES)],capture_output=True,text=True,timeout=5)
        output=result.stdout
        if result.returncode or not isinstance(output,str) or len(output.encode())>16384:denied()
        properties={}
        for line in output.splitlines():
            if '=' not in line:denied()
            key,value=line.split('=',1)
            if key not in PROPERTIES or key in properties:denied()
            properties[key]=value
        if set(properties)!=set(PROPERTIES) or properties['LoadState']!='loaded' or properties['DropInPaths'].strip():denied()
        if properties['User']!=binding.service_user or properties['Group']!=binding.service_group:denied()
        groups=shlex.split(properties['SupplementaryGroups'])
        if binding.group!=binding.service_group and binding.group not in groups:denied()
        # Python startup environment and cwd precede installed site-packages.
        # An opaque EnvironmentFile may redirect imports, so never trust it or
        # open its credential-bearing contents merely to enable an action.
        if properties['EnvironmentFiles'].strip():denied()
        environment=shlex.split(properties['Environment'])
        for variable in environment:
            if '=' not in variable:denied()
            name=variable.split('=',1)[0]
            if name.startswith(('PYTHON','LD_')):denied()
        if (properties['WorkingDirectory'] or '/')!=binding.working_directory.as_posix():denied()
        _ancestors(binding.working_directory)
        for candidate in (binding.working_directory/binding.package,binding.working_directory/(binding.package+'.py')):
            try:os.lstat(candidate)
            except FileNotFoundError:continue
            denied()
        # systemctl show uses a structured, single ExecStart record. Reject
        # multiple records, escapes and unknown formatting instead of guessing.
        command=properties['ExecStart']
        match=re.fullmatch(r'\{ path=([^;{}]+) ; argv\[\]=([^;{}]+) ; ignore_errors=(?:yes|no) ; [^{}]*\}',command)
        if not match:denied()
        executable,arguments=match.groups()
        if any(c in arguments for c in ('\\','\n','\r','$','`')):denied()
        expected=(binding.venv/'bin/python').as_posix()
        argv=shlex.split(arguments)
        prefix=[expected,'-m',binding.package,'run']
        if executable.strip()!=expected:denied()
        if argv==prefix+['--config',binding.config.as_posix()]:return
        if argv!=prefix or binding.config_env=='':denied()
        relevant=[v for v in environment if v.startswith(binding.config_env+'=')]
        if relevant!=[binding.config_env+'='+binding.config.as_posix()]:denied()
    except APIError:raise
    except (OSError,ValueError,AttributeError,TypeError,Unverified):denied()
