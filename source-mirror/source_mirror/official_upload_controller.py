"""Canary upload observer; Create remains blocked and no publication is enabled."""
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from .localization import strict_json, packed
from .official_native_guard import _live_inputs
from .official_upload_evidence import bind, verify_request


def _sync_directory_chain(directory,root):
    """Persist directory entries up through the existing state root."""
    if os.name=='nt': raise ValueError('durable directory sync unavailable on Windows')
    directory=Path(directory); root=Path(root)
    try: directory.relative_to(root)
    except ValueError: raise ValueError('upload intent outside state root') from None
    current=directory
    while True:
        if current.is_symlink() or not current.is_dir(): raise ValueError('unsafe upload intent directory chain')
        fd=os.open(current,os.O_RDONLY)
        try: os.fsync(fd)
        finally: os.close(fd)
        if current==root: break
        current=current.parent
    fd=os.open(root.parent,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)


class UploadObserver:
    """Accept one armed upload and the response for that exact request object.

    This must be installed in the dedicated locked canary's Playwright route;
    it is not an authorization to publish or a proven CDN transformation.
    """
    def __init__(self,root,evidence_path,record_sha):
        self.root=Path(root).resolve(); self.folder=Path(evidence_path); self.record_sha=record_sha
        self.frozen=_live_inputs(self.root,self.folder,self.record_sha)
        self.pending=None; self.armed=None; self.closed=False; self.observation=None

    def arm(self,media_id,role='gallery'):
        if self.closed or self.pending is not None or self.armed is not None: raise ValueError('upload observer already used')
        latest=_live_inputs(self.root,self.folder,self.record_sha)
        if (latest[0],latest[2],latest[3],latest[4])!=(self.frozen[0],self.frozen[2],self.frozen[3],self.frozen[4]):
            raise ValueError('trusted evidence changed before upload')
        if not isinstance(media_id,str) or media_id not in latest[3] or role not in ('cover','gallery'): raise ValueError('official source media unavailable')
        for intent_path in (self._intent_path(media_id,role),self._intent_path(media_id,None)):
            if intent_path.exists() or intent_path.is_symlink(): raise ValueError('prior upload requires reconciliation')
        self.armed=(media_id,latest[3][media_id],role)

    def _intent_path(self,media_id,role):
        directory=self.folder/'upload-intents'
        if directory.is_symlink(): raise ValueError('unsafe upload intent directory')
        value=self.record_sha+'\x00'+media_id+('' if role is None else '\x00'+role)
        identity=hashlib.sha256(value.encode('utf-8')).hexdigest()
        return directory/(identity+'.json')

    def route(self,route):
        request=route.request
        try:
            url=urlsplit(request.url)
            if (self.closed or self.armed is None or self.pending is not None or request.method!='POST'
                    or url.scheme!='https' or url.hostname!='api.nailhub.ai' or url.port not in (None,443)
                    or url.username or url.password or url.path!='/api/v1/Images/quick-upload' or url.query or url.fragment):
                raise ValueError('upload request not armed or endpoint mismatch')
            latest=_live_inputs(self.root,self.folder,self.record_sha)
            if (latest[0],latest[2],latest[3],latest[4])!=(self.frozen[0],self.frozen[2],self.frozen[3],self.frozen[4]):
                raise ValueError('trusted evidence changed at upload')
            content_type=request.headers.get('content-type',''); body=request.post_data_buffer
            verify_request(body,content_type,self.armed[1])
            path=self._intent_path(self.armed[0],self.armed[2]); path.parent.mkdir(exist_ok=True,mode=0o700)
            if path.parent.is_symlink(): raise ValueError('unsafe upload intent directory')
            _sync_directory_chain(path.parent,self.root)
            intent=(packed({'status':'UPLOAD_OUTCOME_REQUIRES_RECONCILIATION','record_sha256':self.record_sha,
                            'media_id':self.armed[0],'role':self.armed[2],'request_sha256':hashlib.sha256(body).hexdigest()})+'\n').encode('utf-8')
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as stream: stream.write(intent); stream.flush(); os.fsync(stream.fileno())
            _sync_directory_chain(path.parent,self.root)
            self.pending=(request,body,content_type,self.armed[0],self.armed[1],self.armed[2]); self.armed=None
            route.continue_()
        except BaseException:
            self.closed=True; self.armed=None; self.pending=None; route.abort('blockedbyclient'); raise

    def response(self,response,*,bounded_reader=None):
        if self.closed or self.pending is None: raise ValueError('upload response without pending request')
        request,body,content_type,media_id,source,role=self.pending
        try:
            if response.request is not request or response.status not in (200,201): raise ValueError('upload response does not match request')
            if bounded_reader is not None:
                data=bounded_reader.read(response)
            else:
                declared=response.headers.get('content-length','')
                if not declared.isdecimal() or int(declared)>65536: raise ValueError('upload response size unavailable')
                data=response.body()
            if not isinstance(data,bytes) or len(data)>65536: raise ValueError('upload response too large')
            receipt=strict_json(data.decode('utf-8')); binding=bind(body,content_type,source,receipt)
            # Revalidate source, rights and broker authority after network I/O.
            latest=_live_inputs(self.root,self.folder,self.record_sha)
            if (latest[0],latest[2],latest[3],latest[4])!=(self.frozen[0],self.frozen[2],self.frozen[3],self.frozen[4]):
                raise ValueError('trusted evidence changed after upload')
            value={'version':'official-observed-upload-v1','status':'CANARY_ONLY_NOT_PUBLISHABLE',
                   'record_sha256':self.record_sha,'media_id':media_id,'role':role,'binding':strict_json(binding.decode('utf-8'))}
            encoded=(packed(value)+'\n').encode('utf-8'); sha=hashlib.sha256(encoded).hexdigest()
            directory=self.folder/'upload-observations'
            if directory.is_symlink(): raise ValueError('unsafe upload evidence path')
            directory.mkdir(exist_ok=True,mode=0o700)
            path=directory/(sha+'.json')
            try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes()!=encoded: raise ValueError('upload observation changed')
            else:
                with os.fdopen(fd,'wb') as stream: stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
            self.observation=value
            return {'status':'CANARY_ONLY_NOT_PUBLISHABLE','observation_sha256':sha,'path':str(path)}
        finally:
            self.closed=True; self.pending=None; self.armed=None
