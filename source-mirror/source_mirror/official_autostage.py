"""Disabled official preparation worker with POSIX process-group watchdog.

The worker never creates or publishes a native article. A timer must remain
disabled until independent review, policy provisioning and Linux validation.
"""
import os
import signal
import stat
import subprocess
import sys
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .official_deadline import maintenance,maintenance_seconds,require
from .official_sources import SOURCES,collect
from .official_auto_eligibility import load_runtime_policy,trusted_broker_call
from .agy_broker import MODEL,EFFORT
from .official_draft import create
from .official_draft_stager import stage_draft
from .official_staging_runner import require_quiet_slots
from .official_generation_state import GenerationState
from .official_source_meaning import differs_from_held
from .official_restage import expired_candidate,verify_new_candidate,begin_restage,finalize_restage
from .lock import worker_lock
from .store import Store,identity
from .localization import strict_json

AUTOSTAGE_VERIFIED=False
# Extracted-text comparison is an anti-retry filter, not proof that a page's
# changed material is semantically distinct. Keep new-content release disabled
# until the comparison is independently validated against the real sources.
HELD_CONTENT_CHANGE_VERIFIED=False
# Store and generation state use separate SQLite databases. Until staged
# revisions remain unreservable through an atomic finalization, restaging
# cannot authorize the existing scheduler to observe a new revision.
RESTAGING_VERIFIED=False
CONFIG=Path('/etc/nailhub-source-mirror/official-autostage.json')
DATA_ROOT=Path('/var/lib/nailhub-source-mirror')
MAX_SECONDS=1200

def policy():
    if os.name!='posix' or not CONFIG.is_absolute():raise ValueError('autostage policy unavailable')
    for part in (CONFIG,*CONFIG.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid!=0 or info.st_mode&0o022:raise ValueError('untrusted autostage policy')
    if not CONFIG.is_file() or CONFIG.stat().st_size>16384:raise ValueError('invalid autostage policy')
    return validate_settings(strict_json(CONFIG.read_text(encoding='utf-8')))

def validate_settings(value):
    if not isinstance(value,dict):raise ValueError('invalid autostage policy')
    if set(value)=={'version','enabled','source_id','category'}:
        value={'version':value['version'],'enabled':value['enabled'],'source_ids':[value['source_id']],'categories':{value['source_id']:value['category']}}
    if set(value)=={'version','enabled','source_ids','category'} and isinstance(value['source_ids'],list) and len(value['source_ids'])==1:
        value={'version':value['version'],'enabled':value['enabled'],'source_ids':value['source_ids'],'categories':{value['source_ids'][0]:value['category']}}
    if (set(value)!={'version','enabled','source_ids','categories'}
            or value['version']!='official-autostage-v1' or type(value['enabled']) is not bool
            or not isinstance(value['source_ids'],list) or not 1<=len(value['source_ids'])<=len(SOURCES)
            or any(not isinstance(source,str) or source not in {item['id'] for item in SOURCES} for source in value['source_ids'])
            or len(set(value['source_ids']))!=len(value['source_ids'])
            or not isinstance(value['categories'],dict) or set(value['categories'])!=set(value['source_ids'])
            or any(not isinstance(category,str) or not category.strip() for category in value['categories'].values())):raise ValueError('invalid autostage policy')
    return value

def execute_child():
    if not AUTOSTAGE_VERIFIED:raise ValueError('official autostage disabled')
    settings=policy()
    if settings['enabled'] is not True:raise ValueError('official autostage policy disabled')
    root=DATA_ROOT
    if not root.is_absolute() or root!=root.resolve() or root.is_symlink():raise ValueError('unsafe state root')
    with maintenance(),worker_lock(root):
        require(900,active=True)
        store=Store(root)
        try:
            require_quiet_slots(store,datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
            published=set();restage={};now=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
            for item in SOURCES:
                if item['id'] not in settings['source_ids']:continue
                existing=store.db.execute('SELECT state FROM articles WHERE id=?',(identity(item['url']),)).fetchone()
                if existing and existing['state']=='published':published.add(item['id'])
                elif existing and RESTAGING_VERIFIED is True:
                    expired=expired_candidate(store,item,now)
                    if expired is not None:restage[item['id']]=expired
        finally:store.close()
        state=GenerationState(root)
        try:
            selected=state.select_source(settings['source_ids'],published,{source:info['source_sha'] for source,info in restage.items()})
            probing=selected is None
            if probing:selected=state.select_probe(settings['source_ids'],published)
        finally:state.close()
        if selected is None:return {'status':'NO_ELIGIBLE_SOURCES'}
        item=next(x for x in SOURCES if x['id']==selected)
        require(900,active=True)
        capture=collect(root,item)
        source_sha=Path(capture['snapshot']).name
        if item['id'] in restage and source_sha!=restage[item['id']]['source_sha']:
            return {'status':'RESTAGE_SOURCE_CHANGED_HOLD'}
        state=GenerationState(root)
        try:
            held=state.held_snapshots(item['id'])
            if held and not differs_from_held(root,item['id'],source_sha,held):
                state.clear_probe(item['id'])
                return {'status':'SOURCE_UNCHANGED_CONTENT_HOLD'}
            if held and HELD_CONTENT_CHANGE_VERIFIED is not True:
                state.clear_probe(item['id'])
                return {'status':'SOURCE_CONTENT_CHANGE_HELD'}
            if probing:
                state.record_probe(item['id'],source_sha)
                return {'status':'SOURCE_PROBED_NOT_PUBLISHABLE'}
            return _prepare_snapshot(root,item,source_sha,settings,state,restage.get(item['id']))
        finally:state.close()

def _prepare_snapshot(root,item,source_sha,settings,state,restage):
        generation,draft_sha=state.claim(item['id'],source_sha)
        if generation=='held':raise ValueError('source generation held')
        if generation=='generate':
          try:
            draft_sha=_find_or_create_draft(root,item,source_sha)
          except BaseException:
            state.finish(item['id'],source_sha)
            raise
          state.finish(item['id'],source_sha,draft_sha=draft_sha)
        phase='stage:'+item['id']
        staging,_=state.claim(phase,source_sha)
        if staging=='held':raise ValueError('source staging held')
        now=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
        next_hours=[hour for hour in (8,12,16,20) if now.hour<hour]
        slot=f'{next_hours[0]:02d}:00' if next_hours else '08:00'
        restaging=False
        if staging=='ready':
            if RESTAGING_VERIFIED is not True:return {'status':'ALREADY_STAGED'}
            if not restage or restage['source_sha']!=source_sha:return {'status':'ALREADY_STAGED'}
            store=Store(root)
            try:
                if expired_candidate(store,item,now)!=restage:raise ValueError('restage evidence changed')
            finally:store.close()
            target_day=(now if next_hours else now+timedelta(days=1)).date().isoformat()
            phase='restage:'+item['id']+':'+target_day+':'+slot
            staging,_=state.claim(phase,source_sha)
            if staging!='generate':raise ValueError('restage already attempted or held')
            restaging=True
        try:
            if policy()!=settings:raise ValueError('autostage policy changed')
            if restaging:
                store=Store(root)
                try:begin_restage(store,item,restage,datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
                finally:store.close()
                result=stage_draft(root,item['id'],source_sha,draft_sha,settings['categories'][item['id']],slot,restage=True,lock_held=True)
                current=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
                expected=datetime.fromisoformat(target_day+'T'+slot+':00+07:00')
                store=Store(root)
                try:verify_new_candidate(store,item,restage,result.get('record_sha256'),expected,current)
                finally:store.close()
            else:
                result=stage_draft(root,item['id'],source_sha,draft_sha,settings['categories'][item['id']],slot,lock_held=True)
        except BaseException as error:
            print('official stage held: '+type(error).__name__,file=sys.stderr,flush=True)
            state.finish(phase,source_sha)
            raise
        state.finish(phase,source_sha,draft_sha=draft_sha)
        if restaging:
            store=Store(root)
            try:finalize_restage(store,item,restage,result.get('record_sha256'),expected,datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
            finally:store.close()
        return result

def _find_or_create_draft(root,item,source_sha):
        directory=root/'official-drafts'/item['id']/source_sha
        if directory.is_symlink():raise ValueError('unsafe draft directory')
        drafts=[]
        if directory.is_dir():
            with os.scandir(directory) as entries:
                for count,entry in enumerate(entries,1):
                    if count>100:raise ValueError('draft list too large')
                    if entry.is_file(follow_symlinks=False) and len(entry.name)==69 and entry.name.endswith('.json'):
                        name=entry.name[:-5]
                        if all(c in '0123456789abcdef' for c in name):drafts.append(name)
        if len(drafts)>1:raise ValueError('multiple drafts require reconciliation')
        if not drafts:
            broker=load_runtime_policy()
            config={'localization':{'enabled':True,'model':MODEL,'effort':EFFORT,'socket_path':broker['socket_path']}}
            def infer(prompt,kind=None):
                require(235,active=True)
                value=trusted_broker_call(broker,prompt,structured_kind=kind) if kind is not None else trusted_broker_call(broker,prompt)
                if load_runtime_policy()!=broker:raise ValueError('runtime policy changed')
                return value
            kwargs={'structured_inference':infer} if broker.get('version')=='official-runtime-policy-v2' else {'inference':infer}
            drafts=[create(root,item['id'],source_sha,config,allow_official_tax=True,**kwargs)['sha256']]
        return drafts[0]

def watchdog(argv,seconds):
    """Kill and reap a dedicated POSIX process group on timeout."""
    if os.name!='posix' or not isinstance(argv,list) or not argv or not isinstance(seconds,(float,int)) or not 0<seconds<=MAX_SECONDS:
        raise ValueError('hard watchdog unavailable')
    child=subprocess.Popen(argv,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
    import threading
    diagnostic=bytearray()
    stop_reader=threading.Event();os.set_blocking(child.stderr.fileno(),False)
    def drain():
        while not stop_reader.is_set():
            try:chunk=os.read(child.stderr.fileno(),4096)
            except BlockingIOError:
                stop_reader.wait(0.05);continue
            if not chunk:break
            if len(diagnostic)<4096:diagnostic.extend(chunk[:4096-len(diagnostic)])
    reader=threading.Thread(target=drain,daemon=True);reader.start()
    try:
        try:code=child.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait()
            raise TimeoutError('official preparation deadline exceeded') from None
        if code!=0:
            stop_reader.wait(0.1);stop_reader.set()
            reader.join(timeout=1)
            allowed={'ValueError','QualityHold','BlockingIOError','TimeoutError','PermissionError','OSError','RuntimeError','ConnectionError','FileNotFoundError'}
            for line in diagnostic.decode('utf-8',errors='replace').splitlines():
                for prefix in ('official stage held: ','official autostage held: '):
                    if line.startswith(prefix) and line[len(prefix):] in allowed:print(line,file=sys.stderr,flush=True)
            raise ValueError('official preparation held')
    finally:
        if child.poll() is None:
            try:os.killpg(child.pid,signal.SIGKILL)
            except ProcessLookupError:pass
            child.wait()
        stop_reader.set();reader.join(timeout=1)
        if not reader.is_alive():child.stderr.close()

def once():
    if not AUTOSTAGE_VERIFIED:raise ValueError('official autostage disabled')
    settings=policy()
    if settings['enabled'] is not True:raise ValueError('official autostage policy disabled')
    seconds=min(MAX_SECONDS,maintenance_seconds(datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))-60)
    if seconds<840:raise ValueError('official preparation time unavailable')
    watchdog([sys.executable,'-m','source_mirror.official_autostage','--child'],seconds)

if __name__=='__main__':
    try:
        if sys.argv[1:]==['--child']:execute_child()
        elif not sys.argv[1:]:once()
        else:raise ValueError('invalid arguments')
    except Exception as error:
        print('official autostage held: '+type(error).__name__,file=sys.stderr);raise SystemExit(1)
