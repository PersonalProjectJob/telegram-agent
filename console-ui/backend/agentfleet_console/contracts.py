"""Read-only release drift gate. Never infers schemas or accepts current changes.

Canonical worker bytes -> pinned compatibility manifest -> explicit Console
review record. Packaging calls the same checks on the bytes actually archived.
Installed identity policy deliberately keeps its separate exact-byte behavior.
"""
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import stat
import subprocess

from .schema import validate_descriptor

PINNED_COMMIT='6fdb7db7da22247052782e2450f861375e79668f'
PINNED_MANIFEST_SHA256='e2e795ac33e145c7a61307413709800a188cbbe9d54be168298fd3a88c089e3c'
BASE='console-ui/backend/agentfleet_console/'
MANIFEST=BASE+'workers/compatibility.json'
REVIEW=BASE+'workers/contract-review.json'
DESCRIPTORS=BASE+'workers/descriptors/'
REQUIRED_MODULES=(BASE+'registry.py',BASE+'schema.py',BASE+'worker_sources.py',BASE+'workers/adapters.py')
WORKER_ROOTS={'triage':'listener/src/tg_triage/','nailhub':'nailhub/src/nailhub/'}
PROMPTS='listener/prompts/'
# This source-only instruction file is not declared package data or shipped.
SOURCE_NOTES={'nailhub/src/nailhub/AGENTS.md'}
MAX_FILE_BYTES=1024*1024
MAX_TOTAL_BYTES=16*1024*1024
MAX_ENTRIES=1024
SHA=re.compile(r'[0-9a-f]{64}\Z')

class ContractError(ValueError):
    """Sanitized diagnostics contain paths/categories, never file contents."""

def _fail(message):raise ContractError(message)

def _path(relative):
    if not isinstance(relative,str) or not relative or len(relative)>300 or '\\' in relative or ':' in relative or any(ord(c)<32 for c in relative):_fail('Invalid contract path.')
    pure=PurePosixPath(relative)
    if pure.is_absolute() or pure.as_posix()!=relative or any(part in ('','.','..') for part in pure.parts):_fail('Invalid contract path.')
    return pure

def _text(data):
    if not isinstance(data,bytes) or len(data)>MAX_FILE_BYTES:_fail('Contract file is oversized or unreadable.')
    try:text=data.decode('utf-8')
    except UnicodeError:_fail('Contract text must be UTF-8.')
    if '\x00' in text or text.startswith('\ufeff'):_fail('Unsupported contract text encoding or line endings.')
    return text.replace('\r\n','\n').encode('utf-8')

def _pairs(items):
    result={}
    for key,value in items:
        if key in result:_fail('Duplicate contract JSON key.')
        result[key]=value
    return result

def _json(data):
    try:return json.loads(_text(data),object_pairs_hook=_pairs,parse_constant=lambda _: _fail('Non-finite contract JSON.'))
    except (ValueError,RecursionError):_fail('Malformed contract JSON.')

def _semantic(value):
    try:return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    except (ValueError,TypeError,RecursionError):_fail('Malformed contract JSON.')

def _sha(value):return isinstance(value,str) and SHA.fullmatch(value)

def _required(files,path):
    if path not in files:_fail('Required contract file is missing: '+path)
    return files[path]

def _worker_paths(manifest):
    paths={}
    for id,worker in manifest['workers'].items():
        for relative,spec in worker['files'].items():
            if relative.startswith('package/'):source=WORKER_ROOTS[id]+relative.removeprefix('package/')
            elif id=='triage' and relative.startswith('data/'):source=PROMPTS+relative.removeprefix('data/')
            else:_fail('Unsupported pinned worker inventory.')
            _path(source);paths[source]=spec
    return paths

def _check(files):
    manifest=_json(_required(files,MANIFEST))
    if _semantic(manifest)!=PINNED_MANIFEST_SHA256:_fail('Pinned compatibility manifest changed; independent contract review is required.')
    if manifest.get('gitCommit')!=PINNED_COMMIT:_fail('Pinned Git provenance changed.')
    expected=_worker_paths(manifest)
    observed={path for path in files if path.startswith(tuple(WORKER_ROOTS.values())) or path.startswith(PROMPTS)}
    observed={path for path in observed if path not in SOURCE_NOTES and '__pycache__' not in PurePosixPath(path).parts and not path.endswith('.pyc')}
    if observed!=set(expected):_fail('Worker inventory has extra or missing files; independent contract review is required.')
    for path,spec in expected.items():
        canonical=_text(_required(files,path))
        if len(canonical)!=spec['size'] or hashlib.sha256(canonical).hexdigest()!=spec['sha256']:_fail('Worker source drift requires independent contract review: '+path)
    review=_json(_required(files,REVIEW))
    if not isinstance(review,dict) or set(review)!={'schemaVersion','owner','gitCommit','manifestSha256','reviewedModules','descriptors'} or type(review['schemaVersion']) is not int or review['schemaVersion']!=1 or review['owner']!='AlexChu' or review['gitCommit']!=PINNED_COMMIT or review['manifestSha256']!=PINNED_MANIFEST_SHA256:_fail('Invalid explicit contract review record.')
    modules=review['reviewedModules'];descriptors=review['descriptors']
    if not isinstance(modules,dict) or not set(REQUIRED_MODULES)<=set(modules) or len(modules)>128:_fail('Invalid reviewed module inventory.')
    for path,digest in modules.items():
        _path(path)
        if not path.startswith(BASE) or not path.endswith('.py') or not _sha(digest):_fail('Invalid reviewed module entry.')
        if hashlib.sha256(_text(_required(files,path))).hexdigest()!=digest:_fail('Console adapter contract changed; independent review is required: '+path)
    if not isinstance(descriptors,dict) or not 1<=len(descriptors)<=16:_fail('Invalid reviewed descriptor inventory.')
    actual={path for path in files if path.startswith(DESCRIPTORS)}
    if actual!=set(descriptors):_fail('Descriptor inventory changed; independent contract review is required.')
    workers={}
    for path,record in descriptors.items():
        _path(path)
        if not path.startswith(DESCRIPTORS) or '/' in path.removeprefix(DESCRIPTORS) or not path.endswith('.json') or not isinstance(record,dict) or set(record)!={'id','contractRevision','sha256'} or not _sha(record['sha256']):_fail('Invalid reviewed descriptor entry.')
        raw=_json(_required(files,path))
        try:public=validate_descriptor(raw)
        except (ValueError,TypeError,KeyError,RecursionError):_fail('Unsupported worker descriptor.')
        id=public['id'];revision=public['contractRevision']
        if id in workers or path!=DESCRIPTORS+id+'.json' or record['id']!=id or record['contractRevision']!=revision or record['sha256']!=_semantic(raw):_fail('Descriptor changed without explicit contract review: '+path)
        if id in manifest['workers'] and revision!=manifest['workers'][id]['contractRevision']:_fail('Descriptor and worker compatibility revisions disagree.')
        workers[id]={'contractRevision':revision,'files':len(manifest['workers'].get(id,{}).get('files',{}))}
    if not set(manifest['workers'])<=set(workers):_fail('A pinned worker descriptor is missing.')
    return {'schemaVersion':1,'status':'verified','gitCommit':PINNED_COMMIT,'workers':workers}

def check_archive(files):
    """Validate a mapping of repository-relative names to the archived raw bytes.

    ZIP entry kind/duplicates and the shipping allowlist remain packager-owned.
    This checker rejects path tricks and extra watched files independently.
    """
    if not isinstance(files,dict) or len(files)>MAX_ENTRIES:_fail('Contract archive mapping exceeds supported limits.')
    total=0
    for path,data in files.items():
        _path(path)
        if not isinstance(data,bytes) or len(data)>MAX_FILE_BYTES:_fail('Contract archive entry exceeds supported limits.')
        total+=len(data)
        if total>MAX_TOTAL_BYTES:_fail('Contract archive exceeds supported limits.')
    return _check(files)

def _regular(path,directory=False):
    info=path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&getattr(stat,'FILE_ATTRIBUTE_REPARSE_POINT',0x400):_fail('Links and reparse points are forbidden in contract source.')
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):_fail('Contract source must contain regular files and directories.')
    return info

def _read(root,relative):
    pure=_path(relative);path=root
    for index,part in enumerate(pure.parts):
        path=path/part;info=_regular(path,index<len(pure.parts)-1)
    if info.st_size>MAX_FILE_BYTES:_fail('Contract source file exceeds supported size.')
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
    with os.fdopen(os.open(path,flags),'rb') as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=(info.st_dev,info.st_ino) or not stat.S_ISREG(opened.st_mode):_fail('Contract source changed while reading.')
        raw=stream.read(MAX_FILE_BYTES+1)
        after=os.fstat(stream.fileno())
        if len(raw)>MAX_FILE_BYTES or (opened.st_size,opened.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):_fail('Contract source changed while reading.')
    return raw

def _scan(root,relative):
    # Bound traversal before reading; every visited entry is checked for links.
    _read_parent=root
    for part in _path(relative.rstrip('/')).parts:
        _read_parent=_read_parent/part;_regular(_read_parent,True)
    result=[];stack=[_read_parent];count=0
    while stack:
        directory=stack.pop()
        with os.scandir(directory) as entries:
            for child in entries:
                count+=1
                if count>MAX_ENTRIES:_fail('Contract source inventory exceeds supported limits.')
                path=Path(child.path);info=path.lstat()
                if stat.S_ISDIR(info.st_mode):
                    _regular(path,True)
                    if child.name!='__pycache__':stack.append(path)
                else:
                    _regular(path)
                    if not path.name.endswith('.pyc'):result.append(path.relative_to(root).as_posix())
    return result

def _git(root,args):
    try:
        result=subprocess.run(['git','--no-pager','--no-optional-locks','-C',str(root),*args],capture_output=True,timeout=10)
    except (OSError,subprocess.SubprocessError):_fail('Pinned Git provenance could not be verified.')
    if result.returncode or len(result.stdout)>MAX_TOTAL_BYTES:_fail('Pinned Git provenance could not be verified.')
    return result.stdout

def _provenance(root,files):
    manifest=_json(files[MANIFEST]);expected=_worker_paths(manifest)
    names=_git(root,['ls-tree','-r','-z','--name-only',PINNED_COMMIT,*WORKER_ROOTS.values(),PROMPTS])
    try:inventory={name.decode('utf-8') for name in names.split(b'\x00') if name}
    except UnicodeError:_fail('Pinned Git inventory is malformed.')
    inventory={path for path in inventory if path.endswith('.py') or path.startswith(PROMPTS)}
    if inventory!=set(expected):_fail('Pinned Git worker inventory does not match the trusted manifest.')
    for path,spec in expected.items():
        raw=_git(root,['show',PINNED_COMMIT+':'+path])
        if len(raw)!=spec['size'] or hashlib.sha256(raw).hexdigest()!=spec['sha256']:_fail('Pinned Git worker bytes do not match the trusted manifest.')

def check_source(root,*,verify_git=True):
    """Check current bytes, then pinned Git blobs only for an actual checkout.

    A no-Git extracted source tree uses the identical shipped metadata checks.
    No output files, metadata, caches, state, credentials or workers are written.
    """
    try:
        root=Path(os.path.abspath(root))
        for parent in (*reversed(root.parents),root):_regular(parent,True)
        files={MANIFEST:_read(root,MANIFEST),REVIEW:_read(root,REVIEW)}
        review=_json(files[REVIEW])
        if not isinstance(review,dict) or not isinstance(review.get('reviewedModules'),dict) or len(review['reviewedModules'])>128:_fail('Invalid explicit contract review record.')
        for path,digest in review['reviewedModules'].items():
            _path(path)
            if not path.startswith(BASE) or not path.endswith('.py') or not _sha(digest):_fail('Invalid reviewed module entry.')
        names=set(review['reviewedModules'])
        for relative in (*WORKER_ROOTS.values(),PROMPTS,DESCRIPTORS):names.update(_scan(root,relative))
        if len(names)>MAX_ENTRIES:_fail('Contract source inventory exceeds supported limits.')
        total=sum(len(raw) for raw in files.values())
        for path in names:
            raw=_read(root,path);total+=len(raw)
            if total>MAX_TOTAL_BYTES:_fail('Contract source exceeds supported limits.')
            files[path]=raw
        result=check_archive(files)
        marker=root/'.git'
        try:info=marker.lstat()
        except FileNotFoundError:info=None
        if verify_git and info is not None:
            if stat.S_ISDIR(info.st_mode):_regular(marker,True)
            else:_regular(marker)
            _provenance(root,files)
        return result
    except ContractError:raise
    except (OSError,ValueError,TypeError,KeyError,RecursionError):_fail('Contract source could not be read safely.')
