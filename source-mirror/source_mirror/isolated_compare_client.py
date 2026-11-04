"""Client for separately sandboxed image comparison; not a publish decision."""
import base64
import hashlib
import json
import socket
import struct
from pathlib import Path
from .localization import strict_json,packed

SOCKET='/run/nailhub-source-mirror-image-compare.sock'
MAX_REQUEST=6_000_000
MAX_REPLY=8192


def compare(original,output,policy):
    if not isinstance(original,bytes) or not isinstance(output,bytes) or not original or not output: raise ValueError('invalid image bytes')
    request=json.dumps({'original':base64.b64encode(original).decode('ascii'),'output':base64.b64encode(output).decode('ascii'),'policy':policy},separators=(',',':'),allow_nan=False).encode()
    if len(request)>MAX_REQUEST: raise ValueError('comparison request too large')
    path=Path(SOCKET)
    if path.is_symlink() or not path.is_socket() or not hasattr(socket,'SO_PEERCRED'): raise ValueError('comparison service unavailable')
    with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
        connection.settimeout(20);connection.connect(str(path))
        _,uid,_=struct.unpack('3i',connection.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        if uid!=0: raise PermissionError('untrusted comparison peer')
        connection.sendall(request);connection.shutdown(socket.SHUT_WR);reply=bytearray()
        while True:
            part=connection.recv(min(4096,MAX_REPLY+1-len(reply)))
            if not part:break
            reply.extend(part)
            if len(reply)>MAX_REPLY:raise ValueError('comparison response too large')
    result=strict_json(reply.decode('utf-8'))
    if not isinstance(result,dict) or set(result)!={'ok','comparison'} or result['ok'] is not True or not isinstance(result['comparison'],dict):
        raise ValueError('comparison service rejected input')
    value=result['comparison'];expected={'status','original_sha256','output_sha256','width','height','original_width','original_height','aspect_error','rms','max_channel_delta','policy'}
    if (set(value)!=expected or value['status']!='VISUAL_QA_NOT_PUBLISHABLE'
            or value['original_sha256']!=hashlib.sha256(original).hexdigest() or value['output_sha256']!=hashlib.sha256(output).hexdigest()
            or packed(value['policy'])!=packed(policy) or type(value['width']) is not int or type(value['height']) is not int
            or type(value['original_width']) is not int or type(value['original_height']) is not int
            or not 1<=value['original_width']<=6000 or not 1<=value['original_height']<=6000 or value['original_width']*value['original_height']>25_000_000
            or value['width']>value['original_width'] or value['height']>value['original_height']
            or type(value['aspect_error']) not in (int,float) or not 0<=value['aspect_error']<=policy['max_aspect_error']
            or min(value['width']/value['original_width'],value['height']/value['original_height'])<policy['min_scale']
            or ((value['width'],value['height'])!=(value['original_width'],value['original_height']) and policy['resample']!='lanczos')
            or not 1<=value['width']<=6000 or not 1<=value['height']<=6000 or value['width']*value['height']>25_000_000
            or not isinstance(value['rms'],list) or len(value['rms'])!=3
            or any(type(x) not in (int,float) or not 0<=x<=policy['max_rms'] for x in value['rms'])
            or type(value['max_channel_delta']) is not int or not 0<=value['max_channel_delta']<=policy['max_channel_delta']):
        raise ValueError('comparison result mismatch')
    return value
