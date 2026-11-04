"""Experimental bounded CDP response capture; not wired to production.

This owns no request permissions. The existing Playwright request guard must
remain installed. Only a native quick-upload response can enter this stream.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

from .localization import strict_json

UPLOAD_ENDPOINT='https://api.nailhub.ai/api/v1/Images/quick-upload'
EXPECTED_ORIGIN='https://nailhub.ai'
PREFLIGHT_HEADERS=frozenset({'authorization','content-type'})
LIMIT=65_536
MARKER='x-source-mirror-bounded-receipt'


class UploadResponseStream:
    def __init__(self,context,page,evidence_dir=None):
        self.context=context;self.records={};self.failures=[];self.evidence_dir=evidence_dir
        if evidence_dir is not None:
            if os.name!='posix' or not hasattr(os,'O_NOFOLLOW'):raise ValueError('private receipt evidence requires POSIX')
            directory=Path(evidence_dir)
            if not directory.is_absolute() or directory!=directory.resolve() or directory.is_symlink():raise ValueError('unsafe receipt evidence directory')
            directory.mkdir(mode=0o700,exist_ok=True)
            info=directory.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:
                raise ValueError('receipt evidence directory must be private and owned')
        self.session=context.new_cdp_session(page)
        context.on('response',self._observed)
        self.session.on('Fetch.requestPaused',self._paused)
        self.session.send('Fetch.enable',{'patterns':[{'urlPattern':UPLOAD_ENDPOINT,'requestStage':'Response'}]})

    def _paused(self,event):
        ident=event['requestId'];stream=None;data=bytearray();token=secrets.token_hex(16)
        metadata={'requestId':ident,'networkId':event.get('networkId'),'status':event.get('responseStatusCode'),'stage':'headers'}
        try:
            request=event.get('request',{})
            if request.get('url')==UPLOAD_ENDPOINT and request.get('method')=='OPTIONS':
                headers={key.lower():value for key,value in request.get('headers',{}).items()}
                names=headers.get('access-control-request-headers','').split(',')
                requested={name.strip().lower() for name in names if name.strip()}
                if (headers.get('origin')!=EXPECTED_ORIGIN or headers.get('access-control-request-method')!='POST'
                        or not requested<=PREFLIGHT_HEADERS or event.get('responseStatusCode') not in (200,204)):
                    raise ValueError('unexpected upload preflight')
                # Preserve Chromium's own CORS enforcement. Never manufacture
                # an allow response/header, stream an OPTIONS body, or grant POST.
                self.session.send('Fetch.continueResponse',{'requestId':ident})
                return
            if (request.get('url')!=UPLOAD_ENDPOINT or request.get('method')!='POST'
                    or event.get('responseStatusCode') not in (200,201) or not isinstance(event.get('networkId'),str)):
                raise ValueError('unexpected upload response identity')
            self._persist(token,metadata)
            headers=event.get('responseHeaders',[])
            types=[h['value'] for h in headers if h.get('name','').lower()=='content-type']
            if len(types)!=1 or types[0].split(';',1)[0].strip().lower()!='application/json':
                raise ValueError('upload JSON content type required')
            stream=self.session.send('Fetch.takeResponseBodyAsStream',{'requestId':ident})['stream']
            while True:
                part=self.session.send('IO.read',{'handle':stream,'size':min(8192,LIMIT+1-len(data))})
                value=part.get('data')
                if not isinstance(value,str):raise ValueError('invalid response stream chunk')
                chunk=base64.b64decode(value,validate=True) if part.get('base64Encoded') is True else value.encode('utf-8')
                data.extend(chunk)
                if len(data)>LIMIT:raise ValueError('upload response exceeds hard limit')
                if part.get('eof') is True:break
                if not chunk:raise ValueError('empty response stream without EOF')
            raw=bytes(data)
            metadata.update({'stage':'body_captured','bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()})
            self._persist(token,metadata,raw)
            if not isinstance(strict_json(raw.decode('utf-8')),dict):raise ValueError('upload JSON object required')
            # CDP exposes a decoded body. Avoid encoding it again using a stale
            # server compression/framing header; preserve the other headers.
            filtered=[h for h in headers if h.get('name','').lower() not in {'content-length','content-encoding','transfer-encoding',MARKER}]
            filtered.extend([{'name':'Content-Length','value':str(len(raw))},{'name':MARKER,'value':token}])
            record={'requestId':ident,'networkId':event['networkId'],'url':request['url'],'method':'POST',
                    'status':event['responseStatusCode'],'content_type':types[0],'body':raw,'sha256':hashlib.sha256(raw).hexdigest(),'used':False}
            self.records[token]=record
            self.session.send('Fetch.fulfillRequest',{'requestId':ident,'responseCode':record['status'],
                              'responseHeaders':filtered,'body':base64.b64encode(raw).decode('ascii')})
        except Exception as exc:
            self.failures.append(type(exc).__name__)
            metadata.update({'stage':'HOLD','error_class':type(exc).__name__,'captured_bytes':min(len(data),LIMIT)})
            try:self._persist(token,metadata,bytes(data[:LIMIT]) if data else None)
            except Exception:self.failures.append('EvidencePersistenceError')
            finally:
                try:self.session.send('Fetch.failRequest',{'requestId':ident,'errorReason':'BlockedByClient'})
                except Exception:pass
        finally:
            if stream is not None:
                try:self.session.send('IO.close',{'handle':stream})
                except Exception:pass

    def _persist(self,token,metadata,body=None):
        if self.evidence_dir is None:return
        directory=Path(self.evidence_dir)
        stage=metadata['stage']
        if stage not in ('headers','body_captured','HOLD'):raise ValueError('unknown receipt evidence stage')
        directory_fd=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            info=os.fstat(directory_fd)
            if info.st_uid!=os.geteuid() or stat.S_IMODE(info.st_mode)!=0o700:raise ValueError('receipt evidence permissions changed')
            for suffix,data in [('json',json.dumps(metadata).encode()),('receipt',body)]:
                if data is None:continue
                name=token+'.'+stage+'.'+suffix
                fd=os.open(name,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=directory_fd)
                with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
            os.fsync(directory_fd)
        finally:os.close(directory_fd)

    def _observed(self,response):
        record=self.records.get(response.headers.get(MARKER))
        if record is not None and record.get('response') is None:
            record['response']=response;record['request']=response.request

    def read(self,response):
        """Return captured bytes only for this same fulfilled native response.

        UploadObserver must additionally require response.request is pending.
        Never fall back to Response.body() or trust Content-Length as a bound.
        """
        token=response.headers.get(MARKER);record=self.records.get(token)
        if (not isinstance(record,dict) or record['used'] or response.url!=record['url']
                or record.get('response') is not response or record.get('request') is not response.request
                or response.request.method!=record['method'] or response.status!=record['status']
                or response.headers.get('content-type')!=record['content_type']):
            raise ValueError('bounded upload response identity mismatch')
        record['used']=True
        return record['body']

    def close(self):
        self.context.remove_listener('response',self._observed)
        self.session.send('Fetch.disable');self.session.detach()
