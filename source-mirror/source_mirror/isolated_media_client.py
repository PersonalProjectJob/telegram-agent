"""Fail-closed client prototype for socket-activated isolated media validation."""
import base64
import hashlib
import json
import socket
import struct
from pathlib import Path

SOCKET = '/run/nailhub-source-mirror-media-validator.sock'
MAX_REQUEST = 6_000_000
MAX_REPLY = 8192


def _unique_pairs(items):
    result={}
    for key,value in items:
        if key in result: raise ValueError('duplicate validator key')
        result[key]=value
    return result


def validate(images, rights):
    """Return checked media digests, never an approval. Call off the event loop."""
    if (not isinstance(images,list) or not isinstance(rights,list) or not 1<=len(images)<=10
            or len(images)!=len(rights) or any(not isinstance(x,bytes) or not x for x in images+rights)):
        raise ValueError('invalid validator input')
    if sum(map(len,images+rights))>4_490_000: raise ValueError('validator request too large')
    payload={'images':[base64.b64encode(x).decode('ascii') for x in images],
             'rights':[base64.b64encode(x).decode('ascii') for x in rights]}
    request=json.dumps(payload,separators=(',',':')).encode('ascii')
    if len(request)>MAX_REQUEST: raise ValueError('validator request too large')
    path=Path(SOCKET)
    if path.is_symlink() or not path.is_socket() or not hasattr(socket,'SO_PEERCRED'):
        raise ValueError('validator unavailable')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(str(path))
        _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        # systemd owns the listening socket; verify this assumption in deployment.
        if uid!=0: raise PermissionError('untrusted validator peer')
        connection.sendall(request); connection.shutdown(socket.SHUT_WR)
        response=bytearray()
        while True:
            part=connection.recv(min(4096,MAX_REPLY+1-len(response)))
            if not part: break
            response.extend(part)
            if len(response)>MAX_REPLY: raise ValueError('validator response too large')
    try: result=json.loads(response,object_pairs_hook=_unique_pairs)
    except (ValueError,UnicodeDecodeError): raise ValueError('invalid validator response') from None
    if not isinstance(result,dict) or set(result)!={'ok','items'} or result['ok'] is not True or not isinstance(result['items'],list) or len(result['items'])!=len(images):
        raise ValueError('validator did not accept input')
    for item,image,evidence in zip(result['items'],images,rights):
        if (not isinstance(item,dict) or set(item)!={'image_sha256','rights_sha256','format'}
                or item['format'] not in ('PNG','JPEG','GIF','WEBP')
                or item['image_sha256']!=hashlib.sha256(image).hexdigest()
                or item['rights_sha256']!=hashlib.sha256(evidence).hexdigest()):
            raise ValueError('validator digest mismatch')
    return result['items']
