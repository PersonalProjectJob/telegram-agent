"""Source-registered descriptor + adapter pairs. Invalid services are isolated."""
import copy
import json
from pathlib import Path

from .errors import APIError
from .schema import WORKER_ID, validate_descriptor

class Registry:
    def __init__(self,registrations):
        self.entries={};self.invalid={}
        for descriptor,adapter in registrations:
            id=descriptor.get('id') if isinstance(descriptor,dict) else None
            if not isinstance(id,str) or not WORKER_ID.fullmatch(id):raise ValueError('Invalid registry id.')
            if id in self.entries or id in self.invalid:
                self.entries.pop(id,None);self.invalid[id]='Duplicate worker registration.';continue
            if len(self.entries)+len(self.invalid)>=16:raise ValueError('Worker registry exceeds its limit.')
            try:
                public=validate_descriptor(descriptor)
                if adapter is None:raise ValueError()
                self.entries[id]={'descriptor':copy.deepcopy(descriptor),'public':public,'adapter':adapter}
            except (ValueError,TypeError,KeyError,RecursionError):self.invalid[id]='Worker contract is unsupported or invalid.'

    def ids(self):return (*self.entries,*self.invalid)
    def require(self,id,capability=None):
        if not isinstance(id,str) or not WORKER_ID.fullmatch(id) or id not in self.ids():raise APIError('not_found','Worker not found.',404)
        if id in self.invalid:raise APIError('contract_unavailable',self.invalid[id],503)
        entry=self.entries[id]
        if capability and not entry['public']['capabilities'][capability]:raise APIError('capability_unavailable','Worker does not expose this capability.',404)
        return entry
    def public(self):return {id:copy.deepcopy(entry['public']) for id,entry in self.entries.items()}
    def default_usage(self):return next((id for id,e in self.entries.items() if e['public']['capabilities']['usage']),None)

def default_registry():
    from .workers.adapters import builtins
    directory=Path(__file__).parent/'workers'/'descriptors'
    registrations=[]
    for id,adapter in builtins().items():
        try:
            path=directory/(id+'.json')
            if path.stat().st_size>256*1024:raise ValueError()
            raw=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):raw={'id':id}
        # File name and trusted adapter registration own the id.
        if not isinstance(raw,dict) or raw.get('id')!=id:raw={'id':id}
        registrations.append((raw,adapter))
    return Registry(registrations)
