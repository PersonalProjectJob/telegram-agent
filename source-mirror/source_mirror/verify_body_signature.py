"""Candidate worker verifier; uses system OpenSSL, no private key in worker."""
import base64,hashlib,json,os,re,stat,subprocess,tempfile
from pathlib import Path

class VerificationHold(ValueError):pass

PUBLIC_KEY_PATH=Path('/etc/nailhub-source-mirror-body-image/public-ed25519.pem')

def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()
def _hash(v):return isinstance(v,str) and re.fullmatch('[a-f0-9]{64}',v) is not None

def read_trusted_public_key(path):
    path=Path(path)
    if os.name!='posix' or not path.is_absolute():raise VerificationHold('invalid public key path')
    for x in (path,*path.parents):
        st=x.lstat()
        if stat.S_ISLNK(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022:raise VerificationHold('untrusted public key path')
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=0 or st.st_mode&0o022 or st.st_size>4096:raise VerificationHold('invalid public key')
        data=os.read(fd,4097)
        if not data or len(data)>4096:raise VerificationHold('invalid public key size')
        return data
    finally:os.close(fd)

def verify(public_key_pem,request,image,evidence,*,openssl='/usr/bin/openssl'):
    if not isinstance(public_key_pem,bytes) or not public_key_pem or len(public_key_pem)>4096 or not isinstance(image,bytes) or not image or len(image)>12_000_000:raise VerificationHold('invalid input')
    if not isinstance(evidence,dict) or set(evidence)!={'body','signature'}:raise VerificationHold('invalid evidence')
    b=evidence['body'];keys={'version','request_sha256','image_sha256','parent_session_sha256','child_session_sha256','model','provider','purpose'}
    if not isinstance(b,dict) or set(b)!=keys or b['version']!='body-image-evidence-v1' or b['request_sha256']!=hashlib.sha256(canonical(request)).hexdigest() or b['image_sha256']!=hashlib.sha256(image).hexdigest() or b['model']!='gemini-3.8-flash-medium' or b['provider']!='Antigravity' or b['purpose']!='inline_illustration' or any(not _hash(b[k]) for k in ('parent_session_sha256','child_session_sha256')) or b['parent_session_sha256']==b['child_session_sha256']:raise VerificationHold('binding changed')
    try:sig=base64.b64decode(evidence['signature'],validate=True)
    except (TypeError,ValueError):raise VerificationHold('invalid signature encoding') from None
    if len(sig)!=64:raise VerificationHold('invalid signature size')
    if openssl!='/usr/bin/openssl':raise VerificationHold('untrusted verifier binary')
    with tempfile.TemporaryDirectory(prefix='body-signature-') as tmp:
        root=Path(tmp);(root/'pub.pem').write_bytes(public_key_pem);(root/'message').write_bytes(canonical(b));(root/'sig').write_bytes(sig)
        try:r=subprocess.run([openssl,'pkeyutl','-verify','-pubin','-inkey',str(root/'pub.pem'),'-rawin','-in',str(root/'message'),'-sigfile',str(root/'sig')],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=3,check=False)
        except (OSError,subprocess.TimeoutExpired):raise VerificationHold('signature verifier unavailable') from None
        if r.returncode!=0:raise VerificationHold('signature verification failed')
    return True


def verify_trusted(request,image,evidence):
    """Public entry point: never accept a verification key from IPC or caller."""
    public_key_pem=read_trusted_public_key(PUBLIC_KEY_PATH)
    return verify(public_key_pem,request,image,evidence)
