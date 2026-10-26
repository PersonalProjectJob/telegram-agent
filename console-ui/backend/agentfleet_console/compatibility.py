"""Read-only installed identity checks against canonical Git-blob package bytes.

Status never authorizes an action: callers check again inside the service lock.
No import, subprocess, pip RECORD trust, database or credential reads occur here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

from .errors import APIError

MANIFEST_PATH=Path(__file__).parent/'workers'/'compatibility.json'

class Unverified(Exception):pass
class Mismatch(Exception):pass

def _regular(path,directory=False):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):raise Unverified()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):raise Unverified()
    return info

def _ancestors(path):
    for parent in reversed((path,*path.parents)):_regular(parent,directory=True)

def _inventory(directory,expected):
    _ancestors(directory)
    found={};stack=[directory];max_files=len(expected)+128;count=0
    while stack:
        current=stack.pop()
        with os.scandir(current) as children:
            for child in children:
                count+=1
                if count>max_files:raise Mismatch()
                path=Path(child.path);info=path.lstat();relative=path.relative_to(directory).as_posix()
                if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0):raise Unverified()
                if stat.S_ISDIR(info.st_mode):
                    if child.name=='__pycache__':continue
                    stack.append(path);continue
                if not stat.S_ISREG(info.st_mode):raise Unverified()
                if path.suffix=='.pyc' or child.name=='RECORD':continue
                if relative not in expected:raise Mismatch()
                spec=expected[relative]
                if info.st_size!=spec['size']:raise Mismatch()
                flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
                fd=os.open(path,flags)
                with os.fdopen(fd,'rb') as stream:
                    opened=os.fstat(stream.fileno())
                    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino):raise Unverified()
                    digest=hashlib.sha256();remaining=spec['size']+1
                    while remaining:
                        part=stream.read(min(65536,remaining))
                        if not part:break
                        digest.update(part);remaining-=len(part)
                    after=os.fstat(stream.fileno())
                    if (after.st_size,after.st_mtime_ns)!=(opened.st_size,opened.st_mtime_ns):raise Unverified()
                if remaining!=1 or digest.hexdigest()!=spec['sha256']:raise Mismatch()
                found[relative]=True
    if set(found)!=set(expected):raise Mismatch()

class Compatibility:
    def __init__(self,manifest=None):
        # An alternate manifest is source/test injection only; never accepted from HTTP.
        self.manifest=manifest if manifest is not None else json.loads(MANIFEST_PATH.read_text(encoding='utf-8'))

    def check(self,id,entry):
        try:
            spec=self.manifest['workers'][id]
            if self.manifest['schemaVersion']!=1 or spec['contractRevision']!=entry['public']['contractRevision']:raise Unverified()
            binding=entry['adapter'].binding;lib=binding.venv/'lib';_ancestors(lib)
            candidates=[]
            with os.scandir(lib) as children:
                for index,child in enumerate(children):
                    if index>=64:raise Unverified()
                    if re.fullmatch(r'python\d+\.\d+',child.name):
                        _regular(Path(child.path),True)
                        candidate=Path(child.path)/'site-packages'/binding.package
                        if candidate.exists() or candidate.is_symlink():candidates.append(candidate)
                    if len(candidates)>1:raise Unverified()
            if len(candidates)!=1:raise Unverified()
            expected={k.removeprefix('package/'):v for k,v in spec['files'].items() if k.startswith('package/')}
            _inventory(candidates[0],expected)
            data={k.removeprefix('data/'):v for k,v in spec['files'].items() if k.startswith('data/')}
            if data:_inventory(binding.venv/binding.data,data)
            # Fixed host bindings must themselves be regular and non-redirected.
            _ancestors(binding.config.parent);_regular(binding.config)
            _ancestors(binding.private)
            return {'state':'verified','reason':'Installed worker files match the tested Console contract; effective service binding is checked on each action.'}
        except Mismatch:return {'state':'mismatch','reason':'Installed worker files differ from the tested Console contract.'}
        except (OSError,ValueError,KeyError,TypeError,Unverified):return {'state':'unverified','reason':'Installed worker identity or fixed host binding could not be verified.'}

def require_compatible(result):
    if result['state']!='verified':raise APIError('compatibility_required',result['reason'],409)
