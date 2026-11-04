"""Scratch cover candidate preflight. Staging evidence never authorizes Create."""
import base64, hashlib, json, os, tempfile
from pathlib import Path
from .localization import digest
from .store import verify_snapshot, sha
from .scratch_cover_ipc import load_policy, request, validate_response, validate_request, Hold

class CoverHold(ValueError): pass

def _snapshot(store, article):
    folder=verify_snapshot(store.root,article['id'])
    meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    media=json.loads((folder/'media-manifest.json').read_text(encoding='utf-8'))
    blocks=json.loads((folder/'content.json').read_text(encoding='utf-8'))
    present=meta.get('source_cover_present',True) # legacy snapshots required a hero
    if type(present) is not bool: raise CoverHold('invalid source cover evidence')
    separate=meta.get('source_cover_separate',False)
    if type(separate) is not bool: raise CoverHold('invalid media contract')
    if present and (not media or (not separate and (not blocks or blocks[0]!={'type':'image','media':0}))): raise CoverHold('source cover unavailable or inconsistent')
    refs=[b.get('media') for b in blocks if b.get('type')=='image']
    if refs != list(range(1 if separate and present else 0,len(media))): raise CoverHold('source media references inconsistent')
    return folder,meta,media,present

def prepare_cover(config,store,article,localized,requester=None):
    folder,meta,media,present=_snapshot(store,article)
    if present: return {'kind':'source'}
    cfg=config.get('scratch_generated_cover')
    if not isinstance(cfg,dict) or cfg.get('enabled') is not True: raise CoverHold('generated cover is disabled')
    try:
        policy=load_policy(cfg['policy_path'])
        locale=localized['locales']['Vietnamese']; title=locale['title']; summary=locale['summary']
        payload_sha=digest(localized); manifest_sha=sha(folder/'snapshot-manifest.json')
        source_id='ai_original' if meta.get('content_origin')=='ai_original' else 'scratch'
        data,evidence=(requester or request)(policy,title,summary,article['id'],manifest_sha,payload_sha,**({'source_id':source_id} if source_id!='scratch' else {}))
        req=evidence['request']; binding=evidence['request_sha256']
        expected={'version':'scratch-cover-request-v1','source_id':source_id,'article_id':article['id'],'source_manifest_sha':manifest_sha,'payload_sha':payload_sha,'title':title,'summary':summary,'nonce':req['nonce']}
        if req!=expected or hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()!=binding: raise Hold('request binding differs')
        original={k:v for k,v in evidence.items() if k not in ('request','request_sha256')}
        production_policy=None
        if policy['version']=='scratch-ai-ipc-production-v2':
            from .scratch_generated_policy import load_policy as load_generated_policy
            production_policy=load_generated_policy(policy['generated_policy_path'])
        checked,validated=validate_response({'status':'CANDIDATE_ONLY','evidence':original,'image_b64':base64.b64encode(data).decode(),'request_sha256':binding},binding,title,summary,production_policy=production_policy)
        if checked!=data or validated!=original: raise Hold('invalid generated evidence')
        revision=Path(article['localization_revision'])
        if revision.is_symlink() or revision.parent!=folder/'localization': raise Hold('invalid revision')
        destination=Path(tempfile.mkdtemp(prefix='generated-cover-',dir=revision))
        ext={'image/jpeg':'.jpg','image/png':'.png','image/webp':'.webp'}[original['image']['mime']]
        image=destination/('cover'+ext); image.write_bytes(data)
        artifact={'version':'scratch-generated-cover-v1','request':req,'request_sha256':binding,'evidence':original,'image_sha256':hashlib.sha256(data).hexdigest(),'attribution':'AI-generated cover image (Antigravity)' }
        evidence_path=destination/'evidence.json'
        evidence_path.write_text(json.dumps(artifact,ensure_ascii=False,sort_keys=True),encoding='utf-8')
        return {'kind':'generated','path':str(image),'evidence_path':str(evidence_path),'image_sha256':artifact['image_sha256'],'evidence_sha256':sha(evidence_path)}
    except Exception as exc:
        raise CoverHold('generated cover preflight failed: '+type(exc).__name__) from exc

def validate_cover(store,article,localized,cover,*,config=None,require_production=False):
    folder,meta,media,present=_snapshot(store,article)
    if present:
        if cover!={'kind':'source'}: raise CoverHold('source cover changed')
        return
    if not isinstance(cover,dict) or cover.get('kind')!='generated': raise CoverHold('generated cover missing')
    path=Path(cover['path']); ep=Path(cover['evidence_path']); revision=Path(article['localization_revision'])
    if path.is_symlink() or ep.is_symlink() or path.parent.is_symlink() or path.parent.parent!=revision or ep.parent!=path.parent or path.name not in ('cover.jpg','cover.png','cover.webp') or ep.name!='evidence.json': raise CoverHold('generated path invalid')
    if sha(ep)!=cover.get('evidence_sha256'): raise CoverHold('generated evidence changed')
    a=json.loads(ep.read_text(encoding='utf-8')); data=path.read_bytes(); req=a['request']; binding=a['request_sha256']
    vi=localized['locales']['Vietnamese']
    try: validate_request(req)
    except Hold as exc: raise CoverHold('generated request invalid') from exc
    expected_source='ai_original' if meta.get('content_origin')=='ai_original' else 'scratch'
    if (req.get('source_id')!=expected_source or req.get('article_id')!=article['id'] or req.get('source_manifest_sha')!=sha(folder/'snapshot-manifest.json') or req.get('payload_sha')!=digest(localized) or req.get('title')!=vi['title'] or req.get('summary')!=vi['summary'] or hashlib.sha256(json.dumps(req,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()!=binding or a.get('image_sha256')!=hashlib.sha256(data).hexdigest() or cover.get('image_sha256')!=a['image_sha256']): raise CoverHold('generated binding changed')
    try:
        production_policy=None
        if require_production:
            cfg=(config or {}).get('scratch_generated_cover',{})
            if cfg.get('enabled') is not True: raise CoverHold('production cover disabled')
            ipc=load_policy(cfg['policy_path'])
            if ipc['version']!='scratch-ai-ipc-production-v2': raise CoverHold('stage IPC cannot authorize Create')
            from .scratch_generated_policy import load_policy as load_generated_policy
            production_policy=load_generated_policy(ipc['generated_policy_path'])
            if expected_source not in production_policy['source_ids']:raise CoverHold('source not authorized')
        validate_response({'status':'CANDIDATE_ONLY','evidence':a['evidence'],'image_b64':base64.b64encode(data).decode(),'request_sha256':binding},binding,vi['title'],vi['summary'],production_policy=production_policy)
    except Exception as exc: raise CoverHold('generated evidence invalid') from exc
