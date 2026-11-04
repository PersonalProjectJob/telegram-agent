"""Isolated candidate producer primitives; not an installed service or broker."""
import base64
import hashlib
import json
import os
import re
import signal
import selectors
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from .illustration_plan import make_request

MODEL='gemini-3.8-flash-medium'
MAX_STREAM=2_000_000
class ProducerHold(ValueError):pass
class CancelUnproven(ProducerHold):pass

def _canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def _sha(data):return hashlib.sha256(data).hexdigest()
def _is_hash(value):return isinstance(value,str) and re.fullmatch('[a-f0-9]{64}',value) is not None

def request(item,article_id,manifest_sha,payload_sha,plan_sha,policy_sha,nonce):
    try:return make_request(item,article_id,manifest_sha,payload_sha,plan_sha,policy_sha,nonce)
    except ValueError as exc:raise ProducerHold('invalid request') from exc

def sign_evidence(private_key,req,image,session):
    """Only call after peer/policy checks and trusted transcript-bound extraction."""
    if not isinstance(private_key,Ed25519PrivateKey) or not isinstance(image,bytes) or not image:raise ProducerHold('invalid producer output')
    if not isinstance(session,dict) or set(session)!={'parent_session_sha256','child_session_sha256'} or any(not _is_hash(x) for x in session.values()) or len(set(session.values()))!=2:raise ProducerHold('invalid provider sessions')
    body={'version':'body-image-evidence-v1','request_sha256':_sha(_canonical(req)),'image_sha256':_sha(image),
          'parent_session_sha256':session['parent_session_sha256'],'child_session_sha256':session['child_session_sha256'],
          'model':MODEL,'provider':'Antigravity','purpose':'inline_illustration'}
    return {'body':body,'signature':base64.b64encode(private_key.sign(_canonical(body))).decode('ascii')}

def verify_evidence(public_key,req,image,evidence):
    if not isinstance(public_key,Ed25519PublicKey) or not isinstance(evidence,dict) or set(evidence)!={'body','signature'}:raise ProducerHold('invalid evidence')
    body=evidence['body']
    expected={'version','request_sha256','image_sha256','parent_session_sha256','child_session_sha256','model','provider','purpose'}
    if not isinstance(body,dict) or set(body)!=expected or body['version']!='body-image-evidence-v1' or body['request_sha256']!=_sha(_canonical(req)) or body['image_sha256']!=_sha(image) or body['model']!=MODEL or body['provider']!='Antigravity' or body['purpose']!='inline_illustration' or any(not _is_hash(body[k]) for k in ('parent_session_sha256','child_session_sha256')) or body['parent_session_sha256']==body['child_session_sha256']:raise ProducerHold('evidence binding changed')
    try:public_key.verify(base64.b64decode(evidence['signature'],validate=True),_canonical(body))
    except (InvalidSignature,ValueError,TypeError):raise ProducerHold('signature invalid') from None
    return True

def _kill_group(process,grace=2):
    """Terminate local process group, but never assert the provider stopped remotely."""
    pgid=process.pid
    try:os.killpg(pgid,signal.SIGTERM)
    except ProcessLookupError:pass
    try:process.wait(timeout=grace)
    except subprocess.TimeoutExpired:pass
    try:os.killpg(pgid,signal.SIGKILL)
    except ProcessLookupError:pass
    try:process.wait(timeout=grace)
    except subprocess.TimeoutExpired:raise CancelUnproven('producer process did not exit') from None
    try:os.killpg(pgid,0)
    except ProcessLookupError:return
    raise CancelUnproven('local child group still present')

def _run_limited(args,workdir,timeout,max_output=MAX_STREAM,clock=time.monotonic,guard=None):
    """Cap combined stdout/stderr in memory and bound waiting by monotonic time."""
    if os.name!='posix' or not 0<timeout<=65 or not 0<max_output<=18_000_000:raise ProducerHold('invalid subprocess budget')
    proc=subprocess.Popen(args,cwd=workdir,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    selector=selectors.DefaultSelector();selector.register(proc.stdout,selectors.EVENT_READ,'out');selector.register(proc.stderr,selectors.EVENT_READ,'err')
    chunks={'out':bytearray(),'err':bytearray()};deadline=clock()+timeout
    try:
        while selector.get_map():
            if guard is not None and not guard():
                _kill_group(proc);raise CancelUnproven('body yielded to cover; provider cancellation unproven')
            remaining=deadline-clock()
            if remaining<=0:
                _kill_group(proc)
                raise CancelUnproven('local group terminated; detached/provider work unproven')
            for key,_ in selector.select(min(remaining,0.2)):
                data=os.read(key.fileobj.fileno(),65536)
                if not data:selector.unregister(key.fileobj);continue
                chunks[key.data].extend(data)
                if sum(map(len,chunks.values()))>max_output:
                    _kill_group(proc)
                    raise CancelUnproven('output limit exceeded; detached/provider work unproven')
        remaining=deadline-clock()
        if remaining<=0:
            _kill_group(proc);raise CancelUnproven('deadline elapsed')
        try:code=proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            _kill_group(proc);raise CancelUnproven('process deadline elapsed') from None
        try:os.killpg(proc.pid,0)
        except ProcessLookupError:pass
        else:
            _kill_group(proc);raise CancelUnproven('producer left local child processes')
        if code:raise ProducerHold('local generation failed')
        return bytes(chunks['out'])
    except BaseException:
        if proc.poll() is None:
            try:_kill_group(proc)
            except CancelUnproven:pass
        raise
    finally:
        selector.close()
        for stream in (proc.stdout,proc.stderr):stream.close()

def invoke(req,workdir,*,timeout=65,binary='/opt/nailhub-source-mirror-agy/agy',clock=time.monotonic,guard=None):
    """Run a local CLI; group exit alone does not prove its cgroup is empty."""
    if os.name!='posix' or type(timeout) not in (int,float) or not 0<timeout<=65:raise ProducerHold('invalid local deadline')
    workdir=Path(workdir)
    if not workdir.is_absolute() or workdir.is_symlink() or workdir.resolve()!=workdir or not workdir.is_dir():raise ProducerHold('unsafe workspace')
    if not isinstance(req,dict) or req.get('prompt_sha256')!=_sha(req.get('prompt','').encode()):raise ProducerHold('request prompt changed')
    args=[binary,'--model',MODEL,'--effort','medium','--sandbox','--mode','accept-edits','--output-format','stream-json','--print-timeout',str(int(timeout))+'s','--print',req['prompt']]
    start=clock();raw=_run_limited(args,workdir,timeout,clock=clock,guard=guard)
    try:events=[json.loads(x) for x in raw.splitlines()]
    except ValueError:raise ProducerHold('invalid stream') from None
    init=[x for x in events if x.get('event')=='init'];result=[x for x in events if x.get('event')=='result']
    if len(init)!=1 or len(result)!=1 or result[0].get('result',{}).get('status')!='SUCCESS':raise ProducerHold('incomplete generation')
    try:parent=str(uuid.UUID(init[0]['conversation_id']))
    except (KeyError,ValueError,AttributeError,TypeError):raise ProducerHold('invalid session') from None
    return {'parent':parent,'events':events,'elapsed_seconds':max(0,clock()-start)}

def extract_image(parent,brain,*,timeout=20,clock=time.monotonic,sleep=time.sleep,runner=_run_limited):
    """Extract in a child process so even a stalled parser cannot exceed the budget."""
    try:
        if str(uuid.UUID(parent))!=parent:raise ValueError()
    except (ValueError,TypeError,AttributeError):raise ProducerHold('invalid session') from None
    brain=Path(brain)
    if not brain.is_absolute() or brain.is_symlink() or brain.resolve()!=brain or not brain.is_dir():raise ProducerHold('unsafe brain directory')
    if not 0<timeout<=20:raise ProducerHold('invalid extraction timeout')
    child_dir=brain/parent/'.system_generated'/'subagents';deadline=clock()+timeout
    while clock()<deadline:
        if child_dir.is_symlink():raise ProducerHold('unsafe subagent directory')
        children=list(child_dir.glob('*.json')) if child_dir.is_dir() else []
        if len(children)>1:raise ProducerHold('ambiguous subagents')
        if len(children)==1:
            try:child=str(uuid.UUID(children[0].stem))
            except (ValueError,TypeError,AttributeError):raise ProducerHold('invalid child') from None
            remaining=deadline-clock()
            if remaining<=0:raise ProducerHold('extraction deadline elapsed')
            program="import sys,json,base64;sys.path.insert(0,'/opt/nailhub-source-mirror');from source_mirror.ai_cover_stage import extract;b,i=extract(sys.argv[1],sys.argv[2],sys.argv[3]);print(json.dumps({'image':base64.b64encode(b).decode('ascii'),'info':i}))"
            raw=runner(['/usr/bin/python3','-B','-c',program,str(brain),parent,child],brain,min(remaining,20),max_output=18_000_000,clock=clock)
            try:
                value=json.loads(raw);data=base64.b64decode(value['image'],validate=True);info=value['info']
            except (ValueError,KeyError,TypeError):raise ProducerHold('invalid extraction response') from None
            if not isinstance(info,dict) or info.get('sha256')!=_sha(data):raise ProducerHold('image extraction changed')
            return data,info
        sleep(min(0.2,max(0,deadline-clock())))
    raise ProducerHold('subagent artifact unavailable')
