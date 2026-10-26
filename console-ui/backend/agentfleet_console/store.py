"""Scoped configuration storage and API operations. No secret appears in public state."""
import copy
import base64
import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import threading
import time
import tomllib

from .paths import ROOT
from .errors import APIError
from .worker_sources import ProviderError, read_key
from .registry import default_registry
from .schema import validate_settings
from .compatibility import Compatibility, require_compatible
from .policy import verify_binding

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


USAGE_FILE='agentfleet-usage.json'
USAGE_MAX_BYTES=512*1024
USAGE_STALE_SECONDS=86400
USAGE_MAX_MODELS=200
USAGE_MAX_DAILY=60
USAGE_MAX_COUNT=10**15
USAGE_WINDOWS=('today','7d','30d')
USAGE_COUNTS=('calls','errors','fallbacks','promptTokens','completionTokens','cacheHitTokens')

class UsageInvalid(Exception):
    """Internal marker: the usage summary does not match the schema. Never shown to the browser."""

def _usage_count(v):
    if type(v) is not int or not 0<=v<=USAGE_MAX_COUNT:raise UsageInvalid()
    return v
def _usage_money(v):
    if type(v) not in (int,float) or not 0<=v<=1e12 or not math.isfinite(v):raise UsageInvalid()  # compare first: isfinite(10**400) overflows
    return float(v)
def _usage_text(v,limit,minimum=0):
    if not isinstance(v,str) or not minimum<=len(v)<=limit or any(ord(c)<32 or ord(c)==127 for c in v):raise UsageInvalid()
    return v
def _usage_object(v):
    if not isinstance(v,dict):raise UsageInvalid()
    return v
def _usage_window(raw):
    raw=_usage_object(raw);out={k:_usage_count(raw.get(k)) for k in (*USAGE_COUNTS,'ok')};out['cost']=_usage_money(raw.get('cost'))
    models=raw.get('byModel')
    if not isinstance(models,list) or len(models)>USAGE_MAX_MODELS:raise UsageInvalid()
    out['byModel']=[]
    for item in models:
        item=_usage_object(item);row={'provider':_usage_text(item.get('provider'),100),'model':_usage_text(item.get('model'),200)}
        row.update({k:_usage_count(item.get(k)) for k in USAGE_COUNTS});row['cost']=_usage_money(item.get('cost'));out['byModel'].append(row)
    return out
def validate_usage(raw,now):
    """Rebuild the runtime usage summary from whitelisted, bounded fields; raise UsageInvalid on any deviation."""
    raw=_usage_object(raw)
    if type(raw.get('schema')) is not int or raw['schema']!=1:raise UsageInvalid()
    generated=raw.get('generatedAt')
    if type(generated) not in (int,float) or not 0<=generated<=now+USAGE_STALE_SECONDS or not math.isfinite(generated):raise UsageInvalid()
    windows=_usage_object(raw.get('windows'))
    daily=raw.get('daily')
    if not isinstance(daily,list) or len(daily)>USAGE_MAX_DAILY:raise UsageInvalid()
    out={'schema':1,'generatedAt':float(generated),'currency':_usage_text(raw.get('currency'),16,1),'windows':{k:_usage_window(windows.get(k)) for k in USAGE_WINDOWS},'daily':[]}
    for item in daily:
        item=_usage_object(item);date=_usage_text(item.get('date'),10,10)
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date):raise UsageInvalid()
        try:datetime.date.fromisoformat(date)
        except ValueError:raise UsageInvalid() from None
        out['daily'].append({'date':date,'calls':_usage_count(item.get('calls')),'errors':_usage_count(item.get('errors')),'fallbacks':_usage_count(item.get('fallbacks')),'cost':_usage_money(item.get('cost'))})
    return out

def _reject_json_constant(name):raise UsageInvalid()

class Store:
    def __init__(self, state_dir, installed=False, runner=subprocess.run, transport=None, registry=None, compatibility=None):
        self.directory=Path(state_dir).resolve();secure_state_directory(self.directory)
        self.installed=installed;self.runner=runner;self.transport=transport
        self.registry=registry if registry is not None else default_registry()
        self.compatibility=compatibility if compatibility is not None else Compatibility()
        self.paths={id:entry['adapter'].binding.config if installed else self.directory/(id+'.toml') for id,entry in self.registry.entries.items()}
        self.locks={id:ServiceLock(path.with_name(path.name+'.console.lock')) for id,path in self.paths.items()}
        # Installed startup/observation never creates /etc locks, replays journals,
        # initializes metadata or mutates config. Only explicit guarded actions may.
        if installed:return
        for id,entry in self.registry.entries.items():
            with self.locks[id]:
                if not self.paths[id].exists():
                    text,updates=entry['adapter'].initial(self.directory)
                    atomic(self.paths[id],patch_toml(text,updates))
                self.recover(id)
                if not self.meta_path(id).exists():
                    raw,data=self.load(id);meta=self.meta(id)
                    meta['fingerprint']=hashlib.sha256(raw.encode()).hexdigest()
                    meta['revision']=value(data,'console','revision',0)
                    atomic(self.meta_path(id),json.dumps(meta))
    def entry(self,id,capability=None):return self.registry.require(id,capability)
    def adapter(self,id):return self.entry(id)['adapter']
    def compatibility_state(self,id):
        if not self.installed:return {'state':'local','reason':'Local configuration files; no installed service.'}
        return self.compatibility.check(id,self.entry(id))
    def observed_compatibility(self,id):
        """Read-only display snapshot; action guards never reuse this result."""
        result=self.compatibility_state(id)
        if self.installed and result['state']=='verified':
            try:verify_binding(self.adapter(id).binding,self.runner)
            except APIError as error:return {'state':'unverified','reason':error.message}
            return {'state':'verified','reason':'Installed worker files and effective service binding match the tested Console contract.'}
        return result
    def guard(self,id,body,capability='settings'):

        entry=self.entry(id,capability)
        if 'contractRevision' in body and body['contractRevision']!=entry['public']['contractRevision']:
            raise APIError('contract_conflict','Worker contract changed; reload before retrying.',409)
        if self.installed:require_compatible(self.compatibility_state(id))
        return entry
    def write_config(self,id,text):
        group=None
        if self.installed:
            import grp
            group=grp.getgrnam(self.adapter(id).binding.group).gr_gid
        atomic(self.paths[id],text,group)
    def meta_path(self,id):return self.directory/(id+'.json')
    def meta(self,id):
        p=self.meta_path(id)
        if p.exists():return json.loads(p.read_text(encoding='utf-8'))
        meta={'revision':0,'appliedRevision':None,'history':[],'fingerprint':None}
        if self.installed and self.paths[id].exists():
            raw,data=self.load(id);meta['revision']=value(data,'console','revision',0);meta['fingerprint']=hashlib.sha256(raw.encode()).hexdigest()
        return meta
    def observed_meta(self,meta,raw):
        """Expose drift without writing during installed observation.

        A deterministic fingerprint revision lets a reload authorize a later
        guarded Save, while a stale browser or another edit still conflicts.
        """
        fingerprint=hashlib.sha256(raw.encode()).hexdigest()
        if meta['fingerprint'] and fingerprint!=meta['fingerprint']:
            meta=copy.deepcopy(meta);meta['revision']='external:'+fingerprint
            meta['fingerprint']=fingerprint;meta['appliedRevision']=None;meta['mainPid']=None
        return meta
    def recover(self,id):
        self.entry(id)
        if not (self.directory/(id+'.pending.json')).exists():return
        if self.installed:
            require_compatible(self.compatibility_state(id))
            verify_binding(self.adapter(id).binding,self.runner)
        with self.locks[id]:self._recover(id)
    def _recover(self,id):
        p=self.directory/(id+'.pending.json')
        if p.exists():
            if self.installed:
                require_compatible(self.compatibility_state(id))
                verify_binding(self.adapter(id).binding,self.runner)
            pending=json.loads(p.read_text(encoding='utf-8'))
            current=hashlib.sha256(self.paths[id].read_bytes()).hexdigest() if self.paths[id].exists() else None
            candidate=hashlib.sha256(pending['config'].encode()).hexdigest()
            if self.installed:
                require_compatible(self.compatibility_state(id))
                verify_binding(self.adapter(id).binding,self.runner)
            if current in (pending.get('beforeFingerprint'),candidate):
                self.write_config(id,pending['config']);atomic(self.meta_path(id),json.dumps(pending['meta']))
            p.unlink()
    def load(self,id):
        try:
            raw=self.paths[id].read_text(encoding='utf-8');return raw,tomllib.loads(raw)
        except Exception:raise APIError('config_unavailable','Service configuration is unavailable.',503) from None
    def settings(self,id,data):return self.adapter(id).settings(self.entry(id)['descriptor'],data)
    def capability(self,id,compatibility=None):
        if not self.installed or sys.platform!='linux' or not Path('/run/systemd/system').exists():return False
        result=compatibility if compatibility is not None else self.compatibility_state(id)
        return result['state']=='verified'
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
        self.entry(id)
        if self.installed:return self._public(id)
        with self.locks[id]:
            self.reconcile(id)
            return self._public(id)
    def _public(self,id):
        entry=self.entry(id);raw,data=self.load(id);meta=self.meta(id);settings=self.settings(id,data)
        if self.installed:meta=self.observed_meta(meta,raw)
        scope=settings.get('provider','')+'|'+settings.get('baseUrl','');key_path=value(data,'model','key_file','')
        try:has=bool(read_key(key_path,scope,group_name=entry['adapter'].binding.group)) if key_path else False
        except ProviderError:has=False
        compatibility=self.observed_compatibility(id)
        return {'settings':settings,'revision':meta['revision'],'appliedRevision':meta['appliedRevision'],'hasKey':has,'keyScope':scope if has else '', 'status':{'state':('last_acknowledged' if meta.get('appliedRevision')==meta['revision'] and meta.get('mainPid') else 'unknown') if self.installed else 'local','mainPid':meta.get('mainPid'),'message':'Readiness must be proven by apply.' if self.installed else 'Local configuration files; no installed service.'},'canApply':self.capability(id,compatibility),'available':True,'canSave':entry['public']['capabilities']['settings'] and (not self.installed or compatibility['state']=='verified'),'contractRevision':entry['public']['contractRevision'],'compatibility':compatibility}
    def unavailable(self,id,reason):
        return {'available':False,'canSave':False,'canApply':False,'contractRevision':None,'settings':{},'revision':None,'appliedRevision':None,'hasKey':False,'keyScope':'','status':{'state':'unavailable','mainPid':None,'message':reason},'compatibility':{'state':'unverified','reason':reason}}
    def history(self):
        entries=[]
        for id in self.registry.entries:
            if self.installed:
                try:entries.extend(self.meta(id)['history'])
                except (APIError,OSError,ValueError,KeyError):continue
            else:
                with self.locks[id]:entries.extend(self.meta(id)['history'])
        return sorted(entries,key=lambda h:h['timestamp'],reverse=True)[:100]
    def bootstrap(self):
        services={id:self.unavailable(id,reason) for id,reason in self.registry.invalid.items()}
        for id in self.registry.entries:
            try:services[id]=self.public(id)
            except (APIError,OSError,ValueError,KeyError):services[id]=self.unavailable(id,'Service configuration is unavailable.')
        result={'contractVersion':1,'descriptors':self.registry.public(),'services':services,'history':self.history(),'host':{'mode':'installed' if self.installed else 'local','label':'Installed host' if self.installed else 'Local files','platform':sys.platform,'canApply':any(s['canApply'] for s in services.values())}}
        if len(json.dumps(result,ensure_ascii=False).encode())>1024*1024:raise APIError('contract_limit','Console contract exceeds its supported size.',503)
        return result
    def usage(self,id=None):
        """Read-only view of the selected worker runtime summary next to its DB. Never opens the DB itself."""
        try:
            id=id or self.registry.default_usage()
            if id is None:return {'status':'missing','usage':None}
            adapter=self.entry(id,'usage')['adapter']
            _,data=self.load(id);db_path=adapter.db_path(data)
            if not isinstance(db_path,str) or not db_path:return {'status':'missing','usage':None}
            path=Path(db_path).parent/USAGE_FILE
        except (APIError,ValueError,OSError,AttributeError):return {'status':'missing','usage':None}
        try:
            flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)|getattr(os,'O_CLOEXEC',0)
            fd=os.open(path,flags)
        except FileNotFoundError:return {'status':'missing','usage':None}
        except OSError:return {'status':'invalid','usage':None}
        try:
            with os.fdopen(fd,'rb') as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):return {'status':'invalid','usage':None}
                raw=stream.read(USAGE_MAX_BYTES+1)
        except OSError:return {'status':'invalid','usage':None}
        if len(raw)>USAGE_MAX_BYTES:return {'status':'invalid','usage':None}
        try:
            now=time.time();usage=validate_usage(json.loads(raw.decode('utf-8'),parse_constant=_reject_json_constant),now)
        except (UsageInvalid,ValueError,RecursionError,OverflowError):return {'status':'invalid','usage':None}
        return {'status':'stale' if now-usage['generatedAt']>USAGE_STALE_SECONDS else 'ok','usage':usage}
    def validate(self,id,settings):
        entry=self.entry(id);settings=validate_settings(entry['public'],settings)
        entry['adapter'].validate(settings)
        return settings
    def key(self,id,settings,provided):
        if provided is not None and (not isinstance(provided,str) or len(provided)>8192 or any(ord(c)<33 for c in provided)):raise APIError('invalid_key','Invalid credential.',field='apiKey')
        if not self.entry(id)['public']['providers'] or settings['provider']=='ollama':return ''
        if provided:return provided
        _,data=self.load(id);scope=settings['provider']+'|'+settings['baseUrl']
        path=value(data,'model','key_file','')
        try:key=read_key(path,scope,group_name=self.adapter(id).binding.group) if path else ''
        except ProviderError:key=''
        if not key:raise APIError('key_required','Enter a key for this provider and endpoint.',field='apiKey')
        return key
    def test(self,id,body):
        entry=self.guard(id,body,'probe');settings=self.validate(id,body.get('settings'))
        key=self.key(id,settings,body.get('apiKey') or None)
        return entry['adapter'].probe(settings,key,self.directory,self.installed,self.transport)
    def save(self,id,body):
        self.guard(id,body)
        if self.installed:verify_binding(self.adapter(id).binding,self.runner)
        with self.locks[id]:
            self.guard(id,body)
            if self.installed:verify_binding(self.adapter(id).binding,self.runner)
            self.recover(id);settings=self.validate(id,body.get('settings'));key=self.key(id,settings,body.get('apiKey') or None);raw,data=self.load(id);meta=self.meta(id)
            fingerprint=hashlib.sha256(raw.encode()).hexdigest()
            if self.installed:
                stored_revision=meta['revision'];meta=self.observed_meta(meta,raw)
                if meta['revision']!=stored_revision:
                    meta['history'].append({'service':id,'action':'external_change','revision':meta['revision'],'timestamp':time.time(),'message':'External configuration change detected.'})
            if body.get('expectedRevision')!=meta['revision'] or (meta['fingerprint'] and fingerprint!=meta['fingerprint']):raise APIError('revision_conflict','Configuration changed; reload before saving.',409)
            updates=self.adapter(id).updates(self.entry(id)['descriptor'],settings)
            revision=secrets.token_hex(16);updates['console','revision']=revision
            # Immutable key generations: secret write precedes config transaction. Old credentials stay valid on crash.
            if key:
                key_directory=self.adapter(id).binding.private if self.installed else self.directory/'secrets'
                group=None
                if self.installed:
                    import grp
                    group=grp.getgrnam(self.adapter(id).binding.group).gr_gid
                    info=key_directory.stat()
                    if info.st_gid!=group or info.st_mode & 0o027:raise APIError('credentials_permissions','Provision the private per-service credential directory.',503)
                secret=key_directory/(id+'-'+secrets.token_hex(16)+'.json')
                updates['model','key_file']=str(secret)
            elif self.entry(id)['public']['providers']:updates['model','key_file']=''
            candidate=patch_toml(raw,updates)
            # Runtime contract validation preserves existing protected fields and fail-closed live prerequisites.
            fd,tmp=tempfile.mkstemp(suffix='.toml');os.close(fd)
            try:
                Path(tmp).write_text(candidate,encoding='utf-8')
                self.adapter(id).validate_candidate(Path(tmp))
            except Exception:raise APIError('runtime_config_invalid','Runtime rejected settings or protected live prerequisites.',field='mode') from None
            finally:Path(tmp).unlink(missing_ok=True)
            self.guard(id,body)
            if self.installed:verify_binding(self.adapter(id).binding,self.runner)
            if key:atomic(secret,json.dumps({'scope':settings['provider']+'|'+settings['baseUrl'],'key':key}),group)
            meta['revision']=revision;meta['fingerprint']=hashlib.sha256(candidate.encode()).hexdigest();meta['history'].append({'service':id,'action':'save','revision':revision,'timestamp':time.time(),'message':'Service settings saved.'});meta['history']=meta['history'][-100:]
            pending=self.directory/(id+'.pending.json');atomic(pending,json.dumps({'config':candidate,'meta':meta,'beforeFingerprint':fingerprint}));self.write_config(id,candidate);atomic(self.meta_path(id),json.dumps(meta));pending.unlink()
            service=self.public(id)
        return {'service':service,'history':self.history()}
    def apply(self,id,body):
        service=self._apply(id,body)
        return {'service':service,'history':self.history()}
    def _apply(self,id,body):
        self.guard(id,body)
        if self.installed:verify_binding(self.adapter(id).binding,self.runner)
        with self.locks[id]:
            self.guard(id,body)
            if self.installed:verify_binding(self.adapter(id).binding,self.runner)
            self.recover(id)
            raw,data=self.load(id);meta=self.meta(id)
            if body.get('expectedRevision')!=meta['revision'] or meta['fingerprint']!=hashlib.sha256(raw.encode()).hexdigest():raise APIError('revision_conflict','Saved configuration changed; reload before applying.',409)
            if value(data,'console','revision')!=meta['revision']:raise APIError('revision_conflict','Save the externally changed configuration before applying.',409)
            if not self.capability(id):raise APIError('apply_unavailable','Installed runtime acknowledgement and systemd are required.',503)
            unit=self.adapter(id).binding.unit
            receipt=self.adapter(id).receipt(data)
            try:
                self.guard(id,body)
                if self.installed:verify_binding(self.adapter(id).binding,self.runner)
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
