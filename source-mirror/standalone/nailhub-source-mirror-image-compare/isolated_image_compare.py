"""Separate-UID image comparison helper; results are heuristic, not approval."""
import base64
import hashlib
import io
import json
import math
import os
import socket
import struct
import sys
import warnings

MAX_REQUEST=6_000_000
MAX_IMAGE=4_000_000
MAX_PIXELS=25_000_000
# Provisional technical ceilings, not a validated policy for any real asset.
# A reviewed root-owned policy must choose limits at or below these values.
MAX_POLICY_RMS=1.0
MAX_POLICY_CHANNEL_DELTA=16
PEXELS_SOURCE='7dd7145d4f4f5104b918eae4a8b55d96130e6501fa1184d5135f11bd751a6f36'
PEXELS_OUTPUT='d015af9ab11bad99f20801e2291b7b418e4647900933c00d6d9f271b28a7f34e'


def _decode(value):
    if not isinstance(value,str) or len(value)>((MAX_IMAGE+2)//3)*4: raise ValueError('invalid encoded image')
    data=base64.b64decode(value,validate=True)
    if not data or len(data)>MAX_IMAGE or base64.b64encode(data).decode('ascii')!=value: raise ValueError('invalid image')
    return data


def compare(request):
    from PIL import Image,ImageChops,ImageStat
    if not isinstance(request,dict) or set(request)!={'original','output','policy'}: raise ValueError('invalid compare request')
    rule=request['policy']
    if not isinstance(rule,dict) or set(rule)!={'mode','max_rms','max_channel_delta','max_aspect_error','min_scale','resample'}: raise ValueError('explicit comparison policy required')
    rms=rule['max_rms'];maximum=rule['max_channel_delta'];aspect=rule['max_aspect_error'];scale=rule['min_scale']
    mode=rule['mode']
    if mode not in ('general','pexels-observed-pair'): raise ValueError('invalid comparison mode')
    rms_ceiling=3.0 if mode=='pexels-observed-pair' else MAX_POLICY_RMS
    delta_ceiling=48 if mode=='pexels-observed-pair' else MAX_POLICY_CHANNEL_DELTA
    if (type(rms) not in (int,float) or not math.isfinite(rms) or not 0<=rms<=rms_ceiling
            or type(maximum) is not int or not 0<=maximum<=delta_ceiling
            or type(aspect) not in (int,float) or not math.isfinite(aspect) or not 0<=aspect<=0.001
            or type(scale) not in (int,float) or not math.isfinite(scale) or not 0<scale<=1
            or rule['resample'] not in ('none','lanczos')):
        raise ValueError('invalid comparison policy')
    original=_decode(request['original']); output=_decode(request['output']); decoded=[]
    if mode=='pexels-observed-pair' and (hashlib.sha256(original).hexdigest()!=PEXELS_SOURCE or hashlib.sha256(output).hexdigest()!=PEXELS_OUTPUT or rule['resample']!='lanczos'):
        raise ValueError('comparison pair not allowlisted')
    with warnings.catch_warnings():
        warnings.simplefilter('error',Image.DecompressionBombWarning)
        for data in (original,output):
            with Image.open(io.BytesIO(data)) as image:
                if (image.format not in ('JPEG','PNG') or image.mode!='RGB' or not 1<=image.width<=6000 or not 1<=image.height<=6000
                        or image.width*image.height>MAX_PIXELS or getattr(image,'n_frames',1)!=1
                        or image.info.get('icc_profile')):
                    raise ValueError('unsupported image geometry or color profile')
                size=image.size; image.verify()
            with Image.open(io.BytesIO(data)) as image:
                if image.getexif().get(274,1)!=1: raise ValueError('unsupported image orientation')
                image.load();decoded.append(image.copy())
        original_size=decoded[0].size;output_size=decoded[1].size
        if mode=='pexels-observed-pair' and (original_size!=(3802,5695) or output_size!=(1281,1920)):
            raise ValueError('comparison pair dimensions changed')
        ow,oh=original_size;tw,th=output_size
        error=abs(tw*oh-ow*th)/(ow*th)
        if (tw>ow or th>oh or min(tw/ow,th/oh)<scale or error>aspect):
            raise ValueError('image dimensions, scale or aspect changed')
        if original_size!=output_size:
            if rule['resample']!='lanczos': raise ValueError('image dimensions or aspect changed')
            reference=decoded[0].resize(output_size,Image.Resampling.LANCZOS)
        else: reference=decoded[0]
        diff=ImageChops.difference(reference,decoded[1])
        channel_rms=ImageStat.Stat(diff).rms; max_delta=max(high for _,high in diff.getextrema())
        if any(value>rms for value in channel_rms) or max_delta>maximum: raise ValueError('image visual difference exceeds policy')
    return {'status':'VISUAL_QA_NOT_PUBLISHABLE','original_sha256':hashlib.sha256(original).hexdigest(),
            'output_sha256':hashlib.sha256(output).hexdigest(),'width':output_size[0],'height':output_size[1],
            'original_width':original_size[0],'original_height':original_size[1],'aspect_error':error,
            'rms':channel_rms,'max_channel_delta':max_delta,'policy':rule}


def _unique(items):
    value={}
    for key,item in items:
        if key in value:raise ValueError('duplicate JSON key')
        value[key]=item
    return value


def main():
    try:
        import resource
        uid=int(os.environ['SOURCE_MIRROR_COMPARE_ALLOWED_UID'])
        if uid<=0 or not hasattr(socket,'SO_PEERCRED'): raise ValueError('invalid peer policy')
        with socket.fromfd(0,socket.AF_UNIX,socket.SOCK_STREAM) as peer:
            _,actual,_=struct.unpack('3i',peer.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,struct.calcsize('3i')))
        if actual!=uid:raise PermissionError('peer mismatch')
        resource.setrlimit(resource.RLIMIT_CPU,(10,10));resource.setrlimit(resource.RLIMIT_AS,(512*1024*1024,512*1024*1024));resource.setrlimit(resource.RLIMIT_FSIZE,(0,0))
        raw=sys.stdin.buffer.read(MAX_REQUEST+1)
        if len(raw)>MAX_REQUEST:raise ValueError('request too large')
        result=compare(json.loads(raw,object_pairs_hook=_unique,parse_constant=lambda _:(_ for _ in ()).throw(ValueError('invalid number'))))
        print(json.dumps({'ok':True,'comparison':result},separators=(',',':'),allow_nan=False))
    except Exception:
        print('{"ok":false}');return 1
    return 0


if __name__=='__main__':sys.exit(main())
