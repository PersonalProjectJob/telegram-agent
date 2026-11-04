"""Atomic snapshot writer; never overwrites a completed article."""
import hashlib, json, os, shutil, tempfile, io
from PIL import Image
from pathlib import Path
from .store import snapshot_identity, sha, verify_snapshot

MAX_MEDIA=20*1024*1024
MAX_TOTAL=100*1024*1024

def save(root, metadata, blocks, html, media, evidence=None):
    root=Path(root).resolve(); aid=snapshot_identity(metadata)
    (root/'tmp').mkdir(parents=True,exist_ok=True); (root/'articles').mkdir(parents=True,exist_ok=True)
    target=root/'articles'/aid
    if target.exists():
        verify_snapshot(root,aid); return aid
    temp=Path(tempfile.mkdtemp(prefix='capture-',dir=root/'tmp'))
    try:
        if not metadata.get('title') or not blocks: raise ValueError('missing title or content')
        (temp/'media').mkdir(); entries=[]; total=0
        for i,item in enumerate(media):
            data=item['bytes']; total+=len(data)
            if not data or len(data)>MAX_MEDIA or total>MAX_TOTAL: raise ValueError('media size limit')
            # Validate signatures without accepting HTML disguised as an image.
            if data.startswith(b'\x89PNG\r\n\x1a\n'): ext='png'
            elif data.startswith(b'\xff\xd8\xff'): ext='jpg'
            elif data.startswith((b'GIF87a',b'GIF89a')): ext='gif'
            elif data[:4]==b'RIFF' and data[8:12]==b'WEBP': ext='webp'
            else: raise ValueError('unsupported image format')
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
            with Image.open(io.BytesIO(data)) as image:
                image.load()
            path=f'media/{i:04d}.{ext}'; (temp/path).write_bytes(data)
            entries.append({k:v for k,v in item.items() if k!='bytes'} | {'path':path})
        for name,value in [('metadata.json',metadata),('content.json',blocks),('media-manifest.json',entries)]:
            (temp/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        (temp/'content.html').write_text(html,encoding='utf-8')
        if evidence:
            (temp/'source').mkdir()
            (temp/'source'/'article.html').write_text(evidence['html'],encoding='utf-8')
            (temp/'source'/'screenshot.png').write_bytes(evidence['screenshot'])
        files={p.relative_to(temp).as_posix():sha(p) for p in temp.rglob('*') if p.is_file()}
        (temp/'snapshot-manifest.json').write_text(json.dumps({'version':1,'files':files},indent=2),encoding='utf-8')
        # Flush content before promotion. Directory fsync is supported on POSIX.
        for path in temp.rglob('*'):
            if path.is_file():
                with open(path, 'r+b') as handle: os.fsync(handle.fileno())
        if os.name != 'nt':
            for directory in (temp/'media', temp):
                descriptor=os.open(directory,os.O_RDONLY)
                try: os.fsync(descriptor)
                finally: os.close(descriptor)
        # Same-root rename; a completed target is never intentionally overwritten.
        os.rename(temp,target)
        if os.name != 'nt':
            descriptor=os.open(root/'articles',os.O_RDONLY)
            try: os.fsync(descriptor)
            finally: os.close(descriptor)
        verify_snapshot(root,aid)
        return aid
    finally:
        if temp.exists(): shutil.rmtree(temp)
