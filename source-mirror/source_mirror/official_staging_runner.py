"""Opt-in offline-input staging; never grants publication or sends messages.

Execution uses the existing trusted factual/media checks and shared Store.
No scheduler enables this entry point, and its window is not a hard watchdog.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
from contextlib import nullcontext
import sys
from zoneinfo import ZoneInfo
from .localization import strict_json
from .lock import worker_lock
from .official_prepare import prepare
from .official_store import stage
from .official_deadline import maintenance,require,SLOTS
from .store import Store

def _read(base,name,limit):
    if not isinstance(name,str) or not name or '\\' in name:raise ValueError('invalid input name')
    relative=Path(name)
    if relative.is_absolute() or any(part in ('.','..') for part in relative.parts):raise ValueError('unsafe input name')
    path=base/relative
    if any((base.joinpath(*relative.parts[:i])).is_symlink() for i in range(len(relative.parts)+1)):
        raise ValueError('symlinked input')
    if not path.is_file() or path.stat().st_size>limit:raise ValueError('input unavailable or too large')
    data=path.read_bytes()
    if len(data)>limit:raise ValueError('input too large')
    return data

def require_quiet_slots(store,now):
    day=now.date().isoformat();minute=now.strftime('%H:%M')
    if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone():
        raise ValueError('unresolved publication attempt')
    for slot in SLOTS:
        if slot>minute:break
        done=store.db.execute('SELECT 1 FROM slots WHERE day=? AND slot=?',(day,slot)).fetchone()
        tries=store.db.execute('SELECT count FROM slot_attempts WHERE day=? AND slot=?',(day,slot)).fetchone()
        if not done and (not tries or tries[0]<3):raise ValueError('prior scheduled slot remains pending')

def require_future_time(editorial):
    try:published=datetime.fromisoformat(editorial['published_at'].replace('Z','+00:00'))
    except (TypeError,KeyError,AttributeError,ValueError):raise ValueError('publication time unavailable') from None
    now=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
    if published.tzinfo is None or published<=now:raise ValueError('staging target slot has passed')

def run(root,manifest_name,*,restage=False,lock_held=False):
    root=Path(root)
    if not root.is_absolute() or root!=root.resolve() or root.is_symlink():raise ValueError('unsafe state root')
    base=root/'official-inputs'
    if base.is_symlink() or not base.is_dir():raise ValueError('input directory unavailable')
    manifest=strict_json(_read(base,manifest_name,256_000).decode('utf-8'))
    if not isinstance(manifest,dict) or set(manifest)!={'source_id','source_sha256','draft','editorial','media'}:
        raise ValueError('invalid staging manifest')
    draft=_read(base,manifest['draft'],4_000_000)
    editorial=strict_json(_read(base,manifest['editorial'],256_000).decode('utf-8'))
    entries=manifest['media']
    if not isinstance(entries,list) or not 1<=len(entries)<=10:raise ValueError('invalid staging media')
    images={};rights={};descriptors=[]
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)!={'descriptor','image','rights'} or not isinstance(entry['descriptor'],dict):
            raise ValueError('invalid staging media')
        descriptor=entry['descriptor']; media_id=descriptor.get('id')
        if not isinstance(media_id,str) or not media_id or media_id in images:raise ValueError('invalid staging media id')
        images[media_id]=_read(base,entry['image'],20_000_000)
        rights[media_id]=_read(base,entry['rights'],256_000)
        descriptors.append(descriptor)
    if type(lock_held) is not bool:raise ValueError('invalid lock contract')
    if lock_held:
        from .official_scratch_publish import require_owned_worker_lock
        require_owned_worker_lock(root)
    with maintenance(),(nullcontext() if lock_held else worker_lock(root)):
        require(300,active=True)
        store=Store(root)
        try:
            require_quiet_slots(store,datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
            require_future_time(editorial)
            if isinstance(editorial,dict) and set(editorial)=={'category','published_at'}:
                from .official_editorial_generator import generate
                require(235,active=True)
                editorial=generate(root,manifest['source_id'],manifest['source_sha256'],draft,editorial)
                require(300,active=True)
            result=prepare(root,manifest['source_id'],manifest['source_sha256'],draft,editorial,descriptors,images,rights)
            require(300,active=True)
            require_future_time(editorial)
            article_id=stage(store,result['path'],result['record_sha256'],restage=restage)
        finally:store.close()
    return {'status':'STAGED_NOT_PUBLISHABLE','article_id':article_id,'record_sha256':result['record_sha256']}

if __name__=='__main__':
    parser=argparse.ArgumentParser(description='Prepare and stage official inputs without publishing')
    parser.add_argument('--root',required=True);parser.add_argument('--manifest',required=True)
    args=parser.parse_args()
    try:print(json.dumps(run(Path(args.root),args.manifest)))
    except Exception as error:
        print('official staging held: '+type(error).__name__,file=sys.stderr);raise SystemExit(1)
