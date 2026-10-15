"""Scoped configuration storage and API operations. No secret appears in public state."""
import copy
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'listener'/'src'))
sys.path.insert(0, str(ROOT/'nailhub'/'src'))
from tg_triage.ai_transport import complete, validate_endpoint, ProviderError, read_key

class APIError(Exception):
    def __init__(self, code, message, status=400, field=None):
        self.code,self.message,self.status,self.field=code,message,status,field

COMMON={'provider':('model','provider'),'model':('model','model'),'baseUrl':('model','base_url'),'timeout':('model','timeout_seconds'),'maxOutput':('model','num_predict'),'think':('model','think'),'cloudConsent':('model','cloud_consent')}
MAPS={'triage':{**COMMON,'mode':('','mode'),'confidence':('ollama','confidence_threshold'),'issueLimit':('github','max_issues_per_hour'),'window':('aggregation','window_minutes'),'maxCase':('aggregation','max_messages_per_case'),'maxWindow':('aggregation','max_messages_per_window'),'allGroups':('telegram','listen_all_groups'),'chatIds':('telegram','chat_ids'),'ignored':('telegram','ignored_command_prefixes'),'proxyHost':('telegram.proxy','host'),'proxyPort':('telegram.proxy','port'),'maxMB':('attachments','max_bytes'),'maxImages':('attachments','max_per_case'),'maxDocs':('attachments','max_documents_per_case'),'maxUploads':('attachments','max_per_hour')},'nailhub':{**COMMON,'mode':('worker','mode'),'interval':('worker','interval_seconds'),'batch':('worker','max_batch'),'pages':('worker','max_pages'),'headless':('worker','headless'),'rangePolicy':('worker','range_policy'),'salaryRange':('contract','salary_range'),'salaryPlaces':('contract','salary_places')}}
RANGES={'timeout':(1,1800),'maxOutput':(256,8192),'issueLimit':(1,10000),'window':(0,1440),'maxCase':(1,1000),'maxWindow':(1,1000),'proxyPort':(1,65535),'maxImages':(0,100),'maxDocs':(0,100),'maxUploads':(0,10000),'interval':(10,86400),'batch':(1,20),'pages':(1,500),'salaryPlaces':(0,2)}
BOOL={'think','cloudConsent','allGroups','headless','salaryRange'}

def secure_state_directory(path):
    """Provision a private state ACL before any credential or journal is written."""
    path=Path(path).resolve()
    if path in (Path(path.anchor),ROOT,Path.home().resolve()) or path in ROOT.parents:
        raise APIError('state_permissions','Choose a dedicated private console state directory.',503)
    path.mkdir(parents=True,exist_ok=True,mode=0o700)
    if os.name!='nt':
        if path.stat().st_uid!=os.getuid():raise APIError('state_permissions','Console state must be owned by the console user.',503)
        os.chmod(path,0o700)
        return
    encoded_path=base64.b64encode(str(path).encode()).decode()
    script="""$ErrorActionPreference='Stop'
$target=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PATH_BASE64'))
$ids=@([Security.Principal.WindowsIdentity]::GetCurrent().User.Value,'S-1-5-18','S-1-5-32-544')
$queue=New-Object Collections.Queue
$queue.Enqueue((Get-Item -LiteralPath $target))
while($queue.Count){
 $item=$queue.Dequeue()
 if($item.Attributes -band [IO.FileAttributes]::ReparsePoint){throw 'State reparse points are forbidden'}
 if($item.PSIsContainer){$acl=New-Object Security.AccessControl.DirectorySecurity;$inherit=[Security.AccessControl.InheritanceFlags]'ContainerInherit,ObjectInherit'}
 else{$acl=New-Object Security.AccessControl.FileSecurity;$inherit=[Security.AccessControl.InheritanceFlags]::None}
 $acl.SetAccessRuleProtection($true,$false)
 foreach($id in $ids){$sid=New-Object Security.Principal.SecurityIdentifier($id);$rule=New-Object Security.AccessControl.FileSystemAccessRule($sid,'FullControl',$inherit,'None','Allow');$acl.AddAccessRule($rule)}
 $item.SetAccessControl($acl)
 $actual=$item.GetAccessControl()
 if(-not $actual.AreAccessRulesProtected){throw 'ACL inheritance is enabled'}
 if($actual.Access.Count -ne $ids.Count){throw 'Unexpected ACL rule count'}
 foreach($rule in $actual.Access){if($rule.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value -notin $ids){throw 'Unexpected ACL principal'}}
 if($item.PSIsContainer){foreach($child in (Get-ChildItem -LiteralPath $item.FullName -Force)){$queue.Enqueue($child)}}
}
""".replace('PATH_BASE64',encoded_path)
    command=base64.b64encode(script.encode('utf-16-le')).decode()
    try:
        result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-EncodedCommand',command],capture_output=True,timeout=20)
        if result.returncode:raise OSError()
    except Exception:raise APIError('state_permissions','Cannot protect the state directory ACL. Use a private NTFS directory owned by your Windows account.',503) from None


def atomic(path, data, group=None):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(dir=path.parent,prefix='.agentfleet-')
    try:
        if group is not None:os.chown(temp,-1,group)
        os.chmod(temp,0o640 if group is not None else 0o600)
        with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)

def value(data, section, key, default=None):
    for part in section.split('.') if section else []:data=data.get(part,{})
    return data.get(key,default)

def patch_toml(text, updates):
    """Replace complete TOML values only; retain unknown sections, keys and comments."""
    lines=text.splitlines(keepends=True);section='';out=[];seen=set();i=0
    while i<len(lines):
        line=lines[i];header=re.match(r'^\s*\[([^\[\]]+)\]\s*(?:#.*)?$',line.strip())
        if line.lstrip().startswith('['):section=header.group(1).strip() if header else None
        match=re.match(r'^\s*([A-Za-z_][\w-]*)\s*=',line)
        target=(section,match.group(1)) if match else None
        if target in updates:
            old=line
            while True:
                try:tomllib.loads(old);break
                except tomllib.TOMLDecodeError:
                    i+=1
                    if i>=len(lines):raise APIError('invalid_config','Cannot edit configuration safely.')
                    old+=lines[i]
            out.append(target[1]+' = '+json.dumps(updates[target],ensure_ascii=False)+'\n');seen.add(target)
        else:out.append(line)
        i+=1
    for (section,key),v in updates.items():
        if (section,key) in seen:continue
        if section:
            # append under existing section, before the following header
            result=''.join(out);m=re.search(r'^\['+re.escape(section)+r'\]\s*(?:#.*)?$',result,re.M)
            if m:
                end=result.find('\n',m.end());pos=(end+1 if end!=-1 else len(result));result=result[:pos]+key+' = '+json.dumps(v)+'\n'+result[pos:];out=[result];continue
            out.append('\n['+section+']\n'+key+' = '+json.dumps(v)+'\n')
        else:out.insert(0,key+' = '+json.dumps(v)+'\n')
    result=''.join(out)
    original=tomllib.loads(text);expected=copy.deepcopy(original)
    for (section,key),v in updates.items():
        target=expected
        for part in section.split('.') if section else []:target=target.setdefault(part,{})
        target[key]=v
    if tomllib.loads(result)!=expected:raise APIError('unsupported_toml','Cannot preserve this configuration syntax safely.')
    return result

class ServiceLock:
    """Reentrant thread lock plus bounded advisory lock shared by console processes."""
    def __init__(self,path):
        self.path=Path(path);self.thread=threading.RLock();self.local=threading.local()
    def __enter__(self):
        if not self.thread.acquire(timeout=5):raise APIError('service_busy','Service configuration is busy; retry.',409)
        if getattr(self.local,'depth',0):
            self.local.depth+=1;return self
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            self.local.stream=self.path.open('a+b');os.chmod(self.path,0o600)
            if self.local.stream.tell()==0:self.local.stream.write(b'0');self.local.stream.flush()
            deadline=time.monotonic()+5
            while True:
                try:
                    if os.name=='nt':
                        import msvcrt
                        self.local.stream.seek(0);msvcrt.locking(self.local.stream.fileno(),msvcrt.LK_NBLCK,1)
                    else:
                        import fcntl
                        fcntl.flock(self.local.stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic()>=deadline:raise APIError('service_busy','Service configuration is busy; retry.',409)
                    time.sleep(.05)
            self.local.depth=1;return self
        except BaseException:
            if getattr(self.local,'stream',None):self.local.stream.close()
            self.thread.release();raise
    def __exit__(self,*args):
        self.local.depth-=1
        try:
            if not self.local.depth:
                if os.name=='nt':
                    import msvcrt
                    self.local.stream.seek(0);msvcrt.locking(self.local.stream.fileno(),msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.local.stream.fileno(),fcntl.LOCK_UN)
                self.local.stream.close()
        finally:self.thread.release()


class Store:
    def __init__(self, state_dir, installed=False, runner=subprocess.run, transport=None):
        self.directory=Path(state_dir).resolve();secure_state_directory(self.directory)
        self.installed=installed;self.runner=runner;self.transport=transport
        self.paths={'triage':Path('/etc/tg-triage/listener.toml'),'nailhub':Path('/etc/nailhub/nailhub.toml')} if installed else {k:self.directory/(k+'.toml') for k in MAPS}
        self.locks={id:ServiceLock(path.with_name(path.name+'.console.lock')) for id,path in self.paths.items()}
        for id in MAPS:
            with self.locks[id]:
                if not self.paths[id].exists() and not installed:
                    example=ROOT/'config'/('listener.example.toml' if id=='triage' else 'nailhub.example.toml')
                    text=example.read_text(encoding='utf-8')
                    # Local storage paths are usable by the current OS; never initialize live.
                    updates={('storage','db_path'):str(self.directory/'triage.sqlite3'),('telegram','session_path'):str(self.directory/'triage.session')} if id=='triage' else {('worker','db_path'):str(self.directory/'nailhub.sqlite3'),('worker','profile_path'):str(self.directory/'profile')}
                    atomic(self.paths[id],patch_toml(text,updates))
                self.recover(id)
                if self.paths[id].exists() and not self.meta_path(id).exists():
                    raw,data=self.load(id);meta=self.meta(id)
                    meta['fingerprint']=hashlib.sha256(raw.encode()).hexdigest()
                    meta['revision']=value(data,'console','revision',0)
                    atomic(self.meta_path(id),json.dumps(meta))
    def write_config(self,id,text):
        group=None
        if self.installed:
            import grp
            group=grp.getgrnam('tglistener' if id=='triage' else 'tgnailhub').gr_gid
        atomic(self.paths[id],text,group)
    def meta_path(self,id):return self.directory/(id+'.json')
    def meta(self,id):
        p=self.meta_path(id)
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'revision':0,'appliedRevision':None,'history':[],'fingerprint':None}
    def recover(self,id):
        p=self.directory/(id+'.pending.json')
        if p.exists():
            pending=json.loads(p.read_text(encoding='utf-8'))
            current=hashlib.sha256(self.paths[id].read_bytes()).hexdigest() if self.paths[id].exists() else None
            candidate=hashlib.sha256(pending['config'].encode()).hexdigest()
            if current in (pending.get('beforeFingerprint'),candidate):
                self.write_config(id,pending['config']);atomic(self.meta_path(id),json.dumps(pending['meta']))
            p.unlink()
    def load(self,id):
        try:
            raw=self.paths[id].read_text(encoding='utf-8');return raw,tomllib.loads(raw)
        except Exception:raise APIError('config_unavailable','Service configuration is unavailable.',503) from None
    def settings(self,id,data):
        result={}
        defaults={'provider':'ollama','model':'qwen3.5:9b','baseUrl':'http://127.0.0.1:11434','timeout':600,'maxOutput':2048,'think':id=='triage','cloudConsent':False,'window':2,'maxCase':20,'maxWindow':40,'maxMB':10485760,'maxImages':5,'maxDocs':5,'maxUploads':60,'allGroups':False,'ignored':[],'proxyHost':'','proxyPort':443}
        for key,(sec,prop) in MAPS[id].items():
            v=value(data,sec,prop,defaults.get(key))
            if key=='baseUrl' and value(data,sec,prop) is None:v=value(data,'ollama' if id=='triage' else 'model','url',v)
            if key=='chatIds':v='\n'.join(str(x) for x in (v or []))
            if key=='ignored':v='\n'.join(v or [])
            if key=='maxMB':v=v/1048576
            result[key]=v
        return result
    def capability(self,id):
        if not self.installed or sys.platform!='linux':return False
        marker=Path('/opt/agentfleet/listener/.venv/lib') if id=='triage' else Path('/opt/agentfleet/nailhub/.venv/lib')
        # Installed runtime must contain the acknowledgement implementation, not merely old binaries.
        return any(marker.glob('python*/site-packages/'+('tg_triage' if id=='triage' else 'nailhub')+'/receipt.py')) and Path('/run/systemd/system').exists()
    def reconcile(self,id):
        self.recover(id);raw,data=self.load(id);meta=self.meta(id)
        fingerprint=hashlib.sha256(raw.encode()).hexdigest()
        if meta['fingerprint'] and fingerprint!=meta['fingerprint']:
            meta['revision']=secrets.token_hex(16);meta['fingerprint']=fingerprint;meta['appliedRevision']=None
            meta['history'].append({'service':id,'action':'external_change','revision':meta['revision'],'timestamp':time.time(),'message':'External configuration change detected.'})
            meta['history']=meta['history'][-100:]
            atomic(self.meta_path(id),json.dumps(meta))
        return raw,data
    def public(self,id):
        with self.locks[id]:
            self.reconcile(id)
            return self._public(id)
    def _public(self,id):
        raw,data=self.load(id);meta=self.meta(id);settings=self.settings(id,data)
        scope=settings['provider']+'|'+settings['baseUrl'];key_path=value(data,'model','key_file','')
        try:has=bool(read_key(key_path,scope,group_name='tglistener' if id=='triage' else 'tgnailhub')) if key_path else False
        except ProviderError:has=False
        return {'settings':settings,'revision':meta['revision'],'appliedRevision':meta['appliedRevision'],'hasKey':has,'keyScope':scope if has else '', 'status':{'state':('last_acknowledged' if meta.get('appliedRevision')==meta['revision'] and meta.get('mainPid') else 'unknown') if self.installed else 'local','mainPid':meta.get('mainPid'),'message':'Readiness must be proven by apply.' if self.installed else 'Local configuration files; no installed service.'},'canApply':self.capability(id)}
    def history(self):
        entries=[]
        for id in MAPS:
            with self.locks[id]:entries.extend(self.meta(id)['history'])
        return sorted(entries,key=lambda h:h['timestamp'],reverse=True)[:100]
    def bootstrap(self):
        return {'services':{id:self.public(id) for id in MAPS},'history':self.history(),'host':{'mode':'installed' if self.installed else 'local','label':'Installed host' if self.installed else 'Local files','platform':sys.platform,'canApply':any(self.capability(id) for id in MAPS)}}
    def validate(self,id,settings):
        if not isinstance(settings,dict) or set(settings)!=set(MAPS[id]):raise APIError('invalid_settings','Expected complete service settings.')
        for key,v in settings.items():
            if key not in BOOL and key not in RANGES and key not in ('confidence','maxMB') and not isinstance(v,str):raise APIError('invalid_field','Expected text.',field=key)
            if key in BOOL and type(v) is not bool:raise APIError('invalid_field','Expected true or false.',field=key)
            if key in RANGES:
                lo,hi=RANGES[key]
                if type(v) is not int or not lo<=v<=hi:raise APIError('invalid_field','Number outside allowed range.',field=key)
            if isinstance(v,str) and (len(v)>8000 or any(ord(c)<32 and c not in '\n\r\t' for c in v)):raise APIError('invalid_field','Invalid text.',field=key)
        if 'maxMB' in settings:
            mb=settings['maxMB']
            if type(mb) not in (int,float) or not math.isfinite(mb) or not 0<mb<=100 or float(mb*1048576).is_integer() is False:
                raise APIError('invalid_field','Expected positive exact file size up to 100 MiB.',field='maxMB')
        try:validate_endpoint(settings['provider'],settings['baseUrl'])
        except ProviderError:raise APIError('invalid_endpoint','Endpoint is not allowed.',field='baseUrl') from None
        if not isinstance(settings['model'],str) or not settings['model'].strip() or len(settings['model'])>200:raise APIError('invalid_model','Model ID is required.',field='model')
        if settings['provider']!='ollama' and settings['think']:raise APIError('thinking_unsupported','Thinking is supported only by Ollama.',field='think')
        if settings['mode'] not in (('dry-run','live') if id=='triage' else ('preview','live')):raise APIError('invalid_field','Invalid mode.',field='mode')
        if id=='triage':
            if type(settings['confidence']) not in (float,int) or not 0<=settings['confidence']<=1:raise APIError('invalid_field','Invalid confidence.',field='confidence')
            if not settings['allGroups'] and not re.fullmatch(r'-\d+(?:\s*[,\n]\s*-\d+)*',settings['chatIds'].strip()):raise APIError('invalid_field','Group IDs must be negative integers.',field='chatIds')
        elif settings['rangePolicy'] not in ('hold','upper_floor_100'):raise APIError('invalid_field','Invalid range policy.',field='rangePolicy')
        return copy.deepcopy(settings)
    def key(self,id,settings,provided):
        if provided is not None and (not isinstance(provided,str) or len(provided)>8192 or any(ord(c)<33 for c in provided)):raise APIError('invalid_key','Invalid credential.',field='apiKey')
        if settings['provider']=='ollama':return ''
        if provided:return provided
        _,data=self.load(id);scope=settings['provider']+'|'+settings['baseUrl']
        path=value(data,'model','key_file','')
        try:key=read_key(path,scope,group_name='tglistener' if id=='triage' else 'tgnailhub') if path else ''
        except ProviderError:key=''
        if not key:raise APIError('key_required','Enter a key for this provider and endpoint.',field='apiKey')
        return key
    def test(self,id,body):
        settings=self.validate(id,body.get('settings'));key=self.key(id,settings,body.get('apiKey') or None);start=time.monotonic()
        try:
            options={'transport':self.transport} if self.transport else {}
            from tg_triage.model_lock import ModelLock, LockStopped
            model_lock=ModelLock() if self.installed else ModelLock(self.directory/'model-lock')
            stop=threading.Event();timer=threading.Timer(30,stop.set);timer.daemon=True;timer.start()
            with model_lock.enter(background=id=='nailhub',stop=stop):
                result=complete(settings['provider'],settings['baseUrl'],settings['model'],[{'role':'user','content':'Return JSON {"ok": true}. This is a synthetic connection probe.'}],{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False},key=key,timeout=max(1,min(settings['timeout'],30-(time.monotonic()-start))),max_output=256,think=settings['think'],**options)
            if result!={'ok':True} or type(result.get('ok')) is not bool:raise ProviderError('invalid_json')
            return {'status':'success','code':'connected','message':'Provider returned the expected JSON.','elapsedMs':int((time.monotonic()-start)*1000)}
        except ProviderError as e:return {'status':'error','code':e.code,'message':'Provider probe failed: '+e.code.replace('_',' ')+'.','elapsedMs':int((time.monotonic()-start)*1000)}
        except LockStopped:return {'status':'error','code':'timeout','message':'Shared model lock wait timed out.','elapsedMs':int((time.monotonic()-start)*1000)}
        except OSError:return {'status':'error','code':'model_lock_unavailable','message':'Shared model lock is unavailable.','elapsedMs':int((time.monotonic()-start)*1000)}
        finally:
            if 'timer' in locals():timer.cancel()
    def save(self,id,body):
        with self.locks[id]:
            self.recover(id);settings=self.validate(id,body.get('settings'));key=self.key(id,settings,body.get('apiKey') or None);raw,data=self.load(id);meta=self.meta(id)
            fingerprint=hashlib.sha256(raw.encode()).hexdigest()
            if body.get('expectedRevision')!=meta['revision'] or (meta['fingerprint'] and fingerprint!=meta['fingerprint']):raise APIError('revision_conflict','Configuration changed; reload before saving.',409)
            updates={}
            for name,(sec,k) in MAPS[id].items():
                v=settings[name]
                if name=='chatIds':v=[] if settings['allGroups'] else [int(x) for x in re.split(r'[,\s]+',v.strip())]
                if name=='ignored':v=[x for x in v.splitlines() if x]
                if name=='maxMB':v=int(v*1048576)
                updates[sec,k]=v
            revision=secrets.token_hex(16);updates['console','revision']=revision
            # Immutable key generations: secret write precedes config transaction. Old credentials stay valid on crash.
            if key:
                key_directory=Path('/etc/tg-triage/private' if id=='triage' else '/etc/nailhub/private') if self.installed else self.directory/'secrets'
                group=None
                if self.installed:
                    import grp
                    group=grp.getgrnam('tglistener' if id=='triage' else 'tgnailhub').gr_gid
                    info=key_directory.stat()
                    if info.st_gid!=group or info.st_mode & 0o027:raise APIError('credentials_permissions','Provision the private per-service credential directory.',503)
                secret=key_directory/(id+'-'+secrets.token_hex(16)+'.json')
                updates['model','key_file']=str(secret)
            else:updates['model','key_file']=''
            candidate=patch_toml(raw,updates)
            # Runtime contract validation preserves existing protected fields and fail-closed live prerequisites.
            fd,tmp=tempfile.mkstemp(suffix='.toml');os.close(fd)
            try:
                Path(tmp).write_text(candidate,encoding='utf-8')
                if id=='nailhub':
                    from nailhub.config import load_config
                    load_config(str(Path(tmp).resolve()))
                else:
                    from tg_triage.config import load_config
                    load_config(tmp,validate_only=True)
            except Exception:raise APIError('runtime_config_invalid','Runtime rejected settings or protected live prerequisites.',field='mode') from None
            finally:Path(tmp).unlink(missing_ok=True)
            if key:atomic(secret,json.dumps({'scope':settings['provider']+'|'+settings['baseUrl'],'key':key}),group)
            meta['revision']=revision;meta['fingerprint']=hashlib.sha256(candidate.encode()).hexdigest();meta['history'].append({'service':id,'action':'save','revision':revision,'timestamp':time.time(),'message':'Service settings saved.'});meta['history']=meta['history'][-100:]
            pending=self.directory/(id+'.pending.json');atomic(pending,json.dumps({'config':candidate,'meta':meta,'beforeFingerprint':fingerprint}));self.write_config(id,candidate);atomic(self.meta_path(id),json.dumps(meta));pending.unlink()
            service=self.public(id)
        return {'service':service,'history':self.history()}
    def apply(self,id,body):
        service=self._apply(id,body)
        return {'service':service,'history':self.history()}
    def _apply(self,id,body):
        with self.locks[id]:
            raw,data=self.load(id);meta=self.meta(id)
            if body.get('expectedRevision')!=meta['revision'] or meta['fingerprint']!=hashlib.sha256(raw.encode()).hexdigest():raise APIError('revision_conflict','Saved configuration changed; reload before applying.',409)
            if value(data,'console','revision')!=meta['revision']:raise APIError('revision_conflict','Save the externally changed configuration before applying.',409)
            if not self.capability(id):raise APIError('apply_unavailable','Installed runtime acknowledgement and systemd are required.',503)
            unit={'triage':'tg-triage.service','nailhub':'tg-nailhub.service'}[id]
            receipt=Path(value(data,'storage' if id=='triage' else 'worker','db_path')).parent/'agentfleet-ready.json'
            try:
                started=time.time()
                result=self.runner(['systemctl','--no-ask-password','restart',unit],capture_output=True,timeout=165)
                if result.returncode:raise APIError('restart_failed','Service restart failed; check host permissions and diagnostics.',503)
                deadline=time.monotonic()+30
                while time.monotonic()<deadline:
                    check=self.runner(['systemctl','show',unit,'--property=ActiveState,MainPID','--value'],capture_output=True,text=True,timeout=5)
                    parts=check.stdout.strip().splitlines()
                    ack=json.loads(receipt.read_text()) if receipt.exists() else {}
                    if 'active' in parts and str(ack.get('pid')) in parts and ack.get('revision')==meta['revision'] and ack.get('readyAt',0)>=started and ack.get('mode')==self.settings(id,data)['mode']:
                        meta['appliedRevision']=meta['revision'];meta['mainPid']=ack['pid'];meta['history'].append({'service':id,'action':'apply','revision':meta['revision'],'timestamp':time.time(),'message':'Authenticated runtime acknowledged this revision.'});atomic(self.meta_path(id),json.dumps(meta));return self.public(id)
                    time.sleep(.25)
                raise APIError('readiness_failed','Restart requested but runtime readiness was not proven.',503)
            except APIError:raise
            except Exception:raise APIError('apply_unavailable','Cannot verify service restart on this host.',503) from None
