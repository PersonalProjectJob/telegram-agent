"""Trusted bindings and legacy behavior. No request selects paths or executable code."""
import copy
from dataclasses import dataclass
import math
from pathlib import Path
import re
import threading
import time

from ..errors import APIError
from ..paths import ROOT
from ..worker_sources import complete, validate_endpoint, ProviderError, load_triage, load_nailhub, ModelLock, LockStopped

def value(data,section,key,default=None):
    for part in section.split('.') if section else []:data=data.get(part,{})
    return data.get(key,default)

@dataclass(frozen=True)
class Binding:
    config: Path
    private: Path
    unit: str
    group: str
    venv: Path
    package: str
    data: str = ''
    service_user: str = ''
    service_group: str = ''
    config_env: str = ''
    working_directory: Path = Path('/')

class LegacyAdapter:
    """Mapping is descriptor-owned; the adapter preserves runtime-only checks."""
    def __init__(self,binding,template,initial_paths,db_section,legacy_url,loader,background=False,triage_rules=False):
        self.binding=binding;self.template=ROOT/'config'/template
        self.initial_paths=initial_paths;self.db_section=db_section;self.legacy_url=legacy_url
        self.loader=loader;self.background=background;self.triage_rules=triage_rules

    def initial(self,directory):
        return self.template.read_text(encoding='utf-8'),{(sec,key):str(directory/relative) for sec,key,relative in self.initial_paths}

    def settings(self,descriptor,data):
        result={};defaults={f['id']:f['default'] for f in descriptor['fields']}
        for key,entry in descriptor['mapping'].items():
            sec,prop,codec=entry['section'],entry['key'],entry['codec']
            v=value(data,sec,prop,copy.deepcopy(defaults[key]))
            if key=='baseUrl' and value(data,sec,prop) is None:v=value(data,self.legacy_url,'url',v)
            if codec=='negative_ids':v='\n'.join(str(x) for x in (v or []))
            if codec=='lines':v='\n'.join(v or [])
            if codec=='mib_bytes' and value(data,sec,prop) is not None:v=v/1048576
            result[key]=v
        return result

    def updates(self,descriptor,settings):
        updates={}
        for name,entry in descriptor['mapping'].items():
            v=settings[name];codec=entry['codec']
            if codec=='negative_ids':v=[] if settings.get('allGroups') else [int(x) for x in re.split(r'[,\s]+',v.strip())]
            if codec=='lines':v=[x for x in v.splitlines() if x]
            if codec=='mib_bytes':v=int(v*1048576)
            updates[entry['section'],entry['key']]=v
        return updates

    def validate(self,settings):
        try:validate_endpoint(settings['provider'],settings['baseUrl'])
        except ProviderError:raise APIError('invalid_endpoint','Endpoint is not allowed.',field='baseUrl') from None
        if not settings['model'].strip():raise APIError('invalid_model','Model ID is required.',field='model')
        if settings['provider']!='ollama' and settings['think']:raise APIError('thinking_unsupported','Thinking is supported only by Ollama.',field='think')
        if self.triage_rules:
            if not settings['fallbackModel'].strip():raise APIError('invalid_model','Fallback model ID is required.',field='fallbackModel')
            if not settings['allGroups'] and not re.fullmatch(r'-\d+(?:\s*[,\n]\s*-\d+)*',settings['chatIds'].strip()):raise APIError('invalid_field','Group IDs must be negative integers.',field='chatIds')
            mb=settings['maxMB']
            if not math.isfinite(mb) or not float(mb*1048576).is_integer():raise APIError('invalid_field','Expected an exact file size in bytes.',field='maxMB')
        elif settings['think']:raise APIError('thinking_unsupported','This worker requires thinking to remain off.',field='think')

    def validate_candidate(self,path):
        self.loader(path)

    def db_path(self,data):return value(data,self.db_section,'db_path')
    def receipt(self,data):return Path(self.db_path(data)).parent/'agentfleet-ready.json'

    def probe(self,settings,key,directory,installed,transport):
        start=time.monotonic()
        try:
            options={'transport':transport} if transport else {}
            model_lock=ModelLock() if installed else ModelLock(directory/'model-lock')
            stop=threading.Event();timer=threading.Timer(30,stop.set);timer.daemon=True;timer.start()
            with model_lock.enter(background=self.background,stop=stop):
                result=complete(settings['provider'],settings['baseUrl'],settings['model'],[{'role':'user','content':'Return JSON {"ok": true}. This is a synthetic connection probe.'}],{'type':'object','properties':{'ok':{'type':'boolean'}},'required':['ok'],'additionalProperties':False},key=key,timeout=max(1,min(settings['timeout'],30-(time.monotonic()-start))),max_output=256,think=settings['think'],**options)
            if result!={'ok':True} or type(result.get('ok')) is not bool:raise ProviderError('invalid_json')
            return {'status':'success','code':'connected','message':'Provider returned the expected JSON.','elapsedMs':int((time.monotonic()-start)*1000)}
        except ProviderError as error:return {'status':'error','code':error.code,'message':'Provider probe failed: '+error.code.replace('_',' ')+'.','elapsedMs':int((time.monotonic()-start)*1000)}
        except LockStopped:return {'status':'error','code':'timeout','message':'Shared model lock wait timed out.','elapsedMs':int((time.monotonic()-start)*1000)}
        except OSError:return {'status':'error','code':'model_lock_unavailable','message':'Shared model lock is unavailable.','elapsedMs':int((time.monotonic()-start)*1000)}
        finally:
            if 'timer' in locals():timer.cancel()

def builtins():
    return {
        'triage':LegacyAdapter(Binding(Path('/etc/tg-triage/listener.toml'),Path('/etc/tg-triage/private'),'tg-triage.service','tglistener',Path('/opt/agentfleet/listener/.venv'),'tg_triage','share/tg-triage/prompts','tglistener','tglistener'),'listener.example.toml',[('storage','db_path','triage.sqlite3'),('telegram','session_path','triage.session')],'storage','ollama',lambda path:load_triage(path,validate_only=True),triage_rules=True),
        'nailhub':LegacyAdapter(Binding(Path('/etc/nailhub/nailhub.toml'),Path('/etc/nailhub/private'),'tg-nailhub.service','tgnailhub',Path('/opt/agentfleet/nailhub/.venv'),'nailhub','', 'tgnailhub','agentfleet','NAILHUB_CONFIG',Path('/opt/agentfleet/nailhub')),'nailhub.example.toml',[('worker','db_path','nailhub.sqlite3'),('worker','profile_path','profile')],'worker','model',lambda path:load_nailhub(str(Path(path).resolve())),background=True),
    }
