"""Standalone, resource-bounded media verifier prototype; no Telegram access.

Run in a separate restricted Linux process. This is not wired to transport and
its output is not an approval or proof of media rights.
"""
import base64
import hashlib
import io
import json
import os
import socket
import struct
import sys
import warnings

MAX_INPUT = 6_000_000
MAX_IMAGE = 4_000_000
MAX_RIGHTS = 256_000


def unique_pairs(items):
    result={}
    for key,value in items:
        if key in result: raise ValueError('duplicate JSON key')
        result[key]=value
    return result


def decode(value, limit):
    if not isinstance(value,str) or len(value)>((limit+2)//3)*4:
        raise ValueError('encoded data exceeds limit')
    raw=base64.b64decode(value,validate=True)
    if not raw or len(raw)>limit or base64.b64encode(raw).decode('ascii')!=value:
        raise ValueError('invalid encoded data')
    return raw


def verify(request):
    from PIL import Image
    if not isinstance(request,dict) or set(request)!={'images','rights'}:
        raise ValueError('invalid request')
    if (not isinstance(request['images'],list) or not isinstance(request['rights'],list)
            or not 1<=len(request['images'])<=10 or len(request['images'])!=len(request['rights'])):
        raise ValueError('invalid counts')
    output=[]
    for image_value, rights_value in zip(request['images'],request['rights']):
        image=decode(image_value,MAX_IMAGE); rights=decode(rights_value,MAX_RIGHTS)
        text=rights.decode('utf-8')
        if any(ord(c)<32 and c not in '\n\r\t' for c in text): raise ValueError('invalid text')
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image)) as picture:
                fmt=picture.format
                if (fmt not in ('PNG','JPEG','GIF','WEBP') or not 1<=picture.width<=6000 or not 1<=picture.height<=6000
                        or picture.width*picture.height>25_000_000 or getattr(picture,'n_frames',1)!=1):
                    raise ValueError('unsafe image')
                picture.verify()
            with Image.open(io.BytesIO(image)) as picture: picture.load()
        output.append({'image_sha256':hashlib.sha256(image).hexdigest(), 'rights_sha256':hashlib.sha256(rights).hexdigest(), 'format':fmt})
    return output


def main():
    try:
        import resource
        uid=int(os.environ['SOURCE_MIRROR_REVIEW_ALLOWED_UID'])
        if uid<=0 or not hasattr(socket,'SO_PEERCRED'): raise ValueError('invalid peer policy')
        with socket.fromfd(0,socket.AF_UNIX,socket.SOCK_STREAM) as peer:
            _,actual_uid,_=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        if actual_uid!=uid: raise PermissionError('peer mismatch')
        resource.setrlimit(resource.RLIMIT_CPU,(5,5))
        resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024))
        resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
        raw=sys.stdin.buffer.read(MAX_INPUT+1)
        if len(raw)>MAX_INPUT: raise ValueError('request too large')
        result=verify(json.loads(raw,object_pairs_hook=unique_pairs))
        print(json.dumps({'ok':True,'items':result},separators=(',',':')))
    except Exception:
        print('{"ok":false}')
        return 1
    return 0


if __name__=='__main__': sys.exit(main())
