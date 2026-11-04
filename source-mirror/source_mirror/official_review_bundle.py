"""Build a local review bundle with actual media; never sends or approves it."""
import hashlib
import io
import json
import zipfile
import warnings
from urllib.parse import urlsplit
from PIL import Image

from .official_payload import build

MAX_BUNDLE = 4_000_000
MAX_DIMENSION = 6000
MAX_PIXELS = 25_000_000
MAX_RIGHTS_TEXT = 256_000


def review_bundle(draft_bytes, editorial, media, images, rights_evidence, allow_official_tax=False):
    payload = build(draft_bytes,media,editorial,allow_official_tax=allow_official_tax)
    ids=[m['id'] for m in media]
    if not isinstance(images,dict) or not isinstance(rights_evidence,dict) or set(images)!=set(ids) or set(rights_evidence)!=set(ids):
        raise ValueError('exact media and rights evidence required')
    files=[]
    for index,item in enumerate(media,1):
        image=images[item['id']]; evidence=rights_evidence[item['id']]
        if (not isinstance(image,bytes) or not image or len(image)>MAX_BUNDLE or not isinstance(evidence,bytes) or not evidence
                or hashlib.sha256(image).hexdigest()!=item['sha256']
                or hashlib.sha256(evidence).hexdigest()!=item['rights_evidence_sha256']):
            raise ValueError('media or rights evidence mismatch')
        if len(evidence)>MAX_RIGHTS_TEXT: raise ValueError('rights evidence too large')
        try: evidence_text=evidence.decode('utf-8')
        except UnicodeDecodeError: raise ValueError('rights evidence must be UTF-8 text') from None
        if any(ord(char)<32 and char not in '\n\r\t' for char in evidence_text):
            raise ValueError('rights evidence contains control characters')
        parsed=urlsplit(item['provenance_url'])
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('invalid media provenance')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(image)) as picture:
                    fmt=picture.format
                    if (fmt not in ('PNG','JPEG','GIF','WEBP') or picture.width<1 or picture.height<1
                            or picture.width>MAX_DIMENSION or picture.height>MAX_DIMENSION
                            or picture.width*picture.height>MAX_PIXELS or getattr(picture,'n_frames',1)!=1):
                        raise ValueError('unsupported image dimensions or frames')
                    picture.verify()
                with Image.open(io.BytesIO(image)) as picture:
                    picture.load()
        except (Image.DecompressionBombWarning, Image.DecompressionBombError, OSError, SyntaxError) as exc:
            raise ValueError('invalid or unsafe image') from exc
        ext={'PNG':'png','JPEG':'jpg','GIF':'gif','WEBP':'webp'}[fmt]
        files.append((f'media/{index:02d}.{ext}',image))
        files.append((f'rights/{index:02d}.txt',evidence))
    payload_sha=hashlib.sha256(payload).hexdigest()
    draft_sha=hashlib.sha256(draft_bytes).hexdigest()
    description=('SOURCE MIRROR — ĐỀ XUẤT CHỜ DUYỆT, KHÔNG CẤP QUYỀN ĐĂNG\n'
                 'Bản này chưa xác thực chuyên gia hoặc quyền ảnh. Mở từng ảnh trong media và bằng chứng trong rights.\n'
                 'SHA256 draft: '+draft_sha+'\nSHA256 payload: '+payload_sha+'\n\n'
                 'PAYLOAD DỰ KIẾN (đầy đủ VI/EN, nguồn, category, thời gian, thẻ và thứ tự media):\n'
                 +json.dumps(json.loads(payload),ensure_ascii=False,indent=2)+'\n\n'
                 'NGUỒN VÀ BẰNG CHỨNG QUYỀN THEO THỨ TỰ MEDIA:\n'
                 +json.dumps(media,ensure_ascii=False,indent=2)+'\n').encode('utf-8')
    files=[('owner-review.txt',description),('payload.json',payload),*files]
    if sum(len(content) for _,content in files)>MAX_BUNDLE:
        raise ValueError('review bundle exceeds limit')
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_STORED) as archive:
        for name,data in files:
            info=zipfile.ZipInfo(name,(1980,1,1,0,0,0)); info.external_attr=0o600<<16
            archive.writestr(info,data)
    bundle=output.getvalue()
    if len(bundle)>MAX_BUNDLE: raise ValueError('review bundle exceeds limit')
    return {'status':'HOLD','payload_sha256':payload_sha,'draft_sha256':draft_sha,
            'bundle_sha256':hashlib.sha256(bundle).hexdigest(),'bundle':bundle}
