"""Local image and review checks before any upload capability is opened."""
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager, closing
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path

from .publication import AdminError, digest

RUBRIC = ('technical', 'anatomy', 'topic', 'brand', 'distinct')
_TEST_IMAGE_SCOPE=ContextVar('nailblog_test_image_scope',default=None)
_TEST_RUN='thinkpad-staging-photo-deadline-20261020-1745'
_TEST_ROOT='/var/lib/agentfleet/nailblog-oneflow-deadline-20261020'
_V2_ROOT=Path('/var/lib/agentfleet/nailblog-staging-20261021-r11')
_V2_RUN='staging-soak-20261021-r11-immediate'
_V2_QUOTE='hiện tại cho phép bypass chất lượng ảnh để chạy thật 1 flow trên STAGING, đến khi có update mới cho model tạo ảnh'
_V2_HEX=re.compile(r'[0-9a-f]{64}')


def _v2_need(value):
    if not value:raise AdminError('staging_test_image_v2_binding_invalid')


def _v2_path(path):
    path=Path(path)
    _v2_need(path.is_absolute() and '..' not in path.parts and path.is_relative_to(_V2_ROOT) and
             not any(p.is_symlink() for p in (path,*path.parents)))
    return path


def _v2_owner(info,root_owned,allowed_modes):
    return os.name!='posix' or (info.st_uid==(0 if root_owned else 992) and stat.S_IMODE(info.st_mode) in allowed_modes)


def _v2_file(path,pin=None,*,root_owned=True,allowed_modes=None):
    path=_v2_path(path);info=path.stat()
    _v2_need(path.is_file() and info.st_size<=2*1024*1024)
    modes=allowed_modes if allowed_modes is not None else ((0o600,0o640) if root_owned else (0o600,))
    _v2_need(_v2_owner(info,root_owned,modes))
    raw=path.read_bytes();actual=hashlib.sha256(raw).hexdigest()
    if pin is not None:_v2_need(isinstance(pin,str) and _V2_HEX.fullmatch(pin) and actual==pin)
    return raw,actual


def _v2_time(value):
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        _v2_need(result.utcoffset() is not None)
        return result.timestamp()
    except (ValueError,AttributeError,TypeError):raise AdminError('staging_test_image_v2_time_invalid') from None


def _validate_v2(config,scope):
    g=scope['grant'];croot=_V2_ROOT/'continuation-image-waiver-v2'
    raw,_=_v2_file(scope['grant_path'],scope['grant_sha'])
    _v2_need(json.loads(raw)==g and Path(scope['grant_path'])==croot/'image-waiver.json')
    _v2_need(g.get('kind')=='nailblog_staging_test_image_waiver_v2' and g.get('owner')=='AlexChu' and
             g.get('decision')=='allow_model_image_quality_for_one_staging_flow' and g.get('owner_quote')==_V2_QUOTE)
    _v2_need(config is not None and config.mode=='live' and config.contract.environment=='staging' and config.contract.verified is True)
    _v2_need(g.get('run_id')==_V2_RUN and g.get('image_run_id')==_V2_RUN+'-images' and
             config.db_path==g.get('config_db_path')==str(_V2_ROOT/'nailblog.sqlite3') and
             g.get('image_directory')==str(_V2_ROOT/_V2_RUN/'images'))
    origins={'site_origin':'https://staging.nailhub.ai','api_origin':'https://api-staging.nailhub.ai',
             'media_origin':'https://vlink-storages-test.sgp1.cdn.digitaloceanspaces.com'}
    _v2_need(all(g.get(k)==v==getattr(config.contract,k) for k,v in origins.items()))
    _v2_need(tuple(g.get('config_slots',[]))==config.slots==('17:24',) and
             g.get('allowed_body_positions')==['body-1','body-2','body-3'] and
             g.get('waived_criteria')==list(RUBRIC) and g.get('automatic_flow_verdict')=='HELD' and g.get('model_quality_verdict')=='FAIL')
    created=_v2_time(g.get('created_at_utc'));expires=_v2_time(g.get('expires_at'))
    _v2_need(created<=time.time()<expires and 0<expires-created<=5400)
    slot=_v2_time(g.get('slot_at'));_v2_need(datetime.fromtimestamp(slot).astimezone().date().isoformat()=='2026-10-21')
    owner_path=croot/'owner-image-quality-waiver.json';_v2_need(g.get('owner_decision_path')==str(owner_path))
    owner=json.loads(_v2_file(owner_path,g.get('owner_decision_sha256'))[0])
    _v2_need(owner.get('kind')=='nailblog_owner_image_quality_waiver_v2' and owner.get('owner')=='AlexChu' and
             owner.get('quote')==_V2_QUOTE and owner.get('scope')=='one_r11_staging_flow' and _v2_time(owner.get('observed_at'))<=created)
    control_path=croot/'trial-config.json';_v2_need(g.get('control_path')==str(control_path))
    control=json.loads(_v2_file(control_path,g.get('control_sha256'))[0])
    _v2_need(control.get('root')==str(_V2_ROOT) and control.get('owner')=='AlexChu' and control.get('environment')=='staging' and
             [r.get('run_id') for r in control.get('runs',[])]==[_V2_RUN] and control.get('image_waiver_receipt_path')==str(croot/'image-waiver.json'))
    _v2_need(g.get('immediate_policy_path')==control.get('immediate_policy_path')==str(croot/'immediate-policy.json') and g.get('immediate_policy_sha256')==control.get('immediate_policy_sha256'))
    _v2_file(croot/'immediate-policy.json',g.get('immediate_policy_sha256'))
    controller=croot/'staging-soak-telegram-runner-v2.py'
    _v2_need(g.get('controller_path')==control.get('controller_path')==str(controller) and g.get('controller_sha256')==control.get('controller_sha256'))
    _v2_file(controller,g.get('controller_sha256'))
    toml=_V2_ROOT/'nailblog.toml'
    _v2_need(g.get('toml_sha256')==control.get('nailblog_config_sha256') and control.get('nailblog_config')==str(toml))
    _v2_file(toml,g.get('toml_sha256'))
    from .config import load_config
    _v2_need(load_config(str(toml))==config)
    from .staging_immediate import _runtime
    _v2_need(control.get('runtime_hashes')==g.get('runtime_hashes'));_runtime(g.get('runtime_hashes'))
    for key,leaf in (('telegram_owner_journal_path','telegram-owner-journal.json'),('native_transport_readiness_path','native-transport-readiness.json'),('telegram_owner_decisions_dir','telegram-owner-decisions')):
        _v2_need(control.get(key)==str(croot/leaf))
    article_path=_V2_ROOT/_V2_RUN/'article.json'
    _v2_need(g.get('article_artifact_path')==str(article_path))
    retained=json.loads(_v2_file(article_path,g.get('article_artifact_sha256'),root_owned=False)[0])
    _v2_need(retained.get('article')==scope['article'] and digest(scope['article'])==g.get('article_digest') and
             retained.get('slot_at')==g.get('slot_at') and retained.get('quality')=={'verdict':'pass','issues':[]} and retained.get('facts')=={'ungrounded':[]})
    with closing(sqlite3.connect('file:'+config.db_path+'?mode=ro',uri=True)) as db:
        row=db.execute('SELECT phase,document FROM onepost_runs WHERE run_id=?',(_V2_RUN,)).fetchone()
    _v2_need(row is not None and row[0] in ('images_pending','images_ready','images_approved','draft_verified','scheduled','published'))
    saved=json.loads(row[1]);_v2_need(saved.get('article')==scope['article'] and saved.get('quality')==retained['quality'] and saved.get('facts')==retained['facts'])
    archive_path=_v2_path(g.get('original_manifest_archive_path',''));expected_name='manifest-before-test-waiver-'+str(g.get('original_manifest_sha256'))+'.json'
    _v2_need(archive_path==Path(g['image_directory'])/expected_name)
    before=json.loads(_v2_file(archive_path,g.get('original_manifest_sha256'),root_owned=False)[0])
    _v2_need(before.get('run_id')==g['image_run_id'] and before.get('binding')==g.get('image_binding') and before.get('status')=='held' and before.get('positions')=={})
    _v2_need(set(before.get('candidates',{}))=={'cover-r0-c0','cover-r1-c0'})
    cover=before['candidates'].get(g.get('cover_candidate'),{})
    _v2_need(g.get('cover_candidate') in ('cover-r0-c0','cover-r1-c0') and cover.get('sha')==g.get('cover_sha') and
             cover.get('status')=='rejected' and set(cover.get('scores',{}))==set(RUBRIC) and all(type(v) is bool for v in cover['scores'].values()) and cover.get('reason'))
    current_path=Path(g['image_directory'])/'manifest.json';current=json.loads(_v2_file(current_path,root_owned=False,allowed_modes=(0o600,0o640))[0])
    _v2_need(current.get('run_id')==g['image_run_id'] and current.get('binding')==g['image_binding'] and
             all(current.get('candidates',{}).get(k)==v for k,v in before['candidates'].items()))
    _v2_need(set(current.get('positions',{}))<=set(['cover',*g['allowed_body_positions']]))
    for position,selected in current.get('positions',{}).items():
        candidates=current.get('candidates',{})
        if selected.get('status')=='operator_waived':
            source=selected.get('source_candidate');candidate=candidates.get(source,{})
            _v2_need(isinstance(source,str) and source.startswith(position+'-r') and candidate.get('status')=='rejected' and selected=={**candidate,'status':'operator_waived','source_candidate':source})
            audit=current.get('test_image_waiver',{});event=audit.get('entries',{}).get(position,{})
            _v2_need(audit.get('grant_sha256')==scope['grant_sha'] and event=={'candidate':source,'sha':candidate.get('sha'),'scores':candidate.get('scores'),'reason':candidate.get('reason'),'model_status':'rejected','intent':candidate.get('intent'),'review_intent':candidate.get('review_intent')})
        else:
            _v2_need(selected.get('status')=='approved' and len([name for name,value in candidates.items() if name.startswith(position+'-r') and value==selected])==1 and
                     set(selected.get('scores',{}))==set(RUBRIC) and all(v is True for v in selected['scores'].values()))
        if position=='cover':_v2_need(selected.get('sha')==g['cover_sha'] and selected.get('source_candidate')==g['cover_candidate'])
    for name,entry in current.get('candidates',{}).items():
        if entry.get('file'):
            file=_v2_path(Path(g['image_directory'])/entry['file']);data=_v2_file(file,entry.get('sha'),root_owned=False)[0]
            _v2_need(file.parent==Path(g['image_directory']) and entry.get('intent')==digest({'binding':g['image_binding'],'candidate':name,'operation':'generate'}))
            if entry.get('review_intent'):_v2_need(entry['review_intent']==digest({'binding':g['image_binding'],'candidate':name,'operation':'review','sha':entry['sha']}))
    scope['control']=control


def active_test_image_v2(config):
    scope=_test_scope(config)
    return scope if scope is not None and scope['grant'].get('kind')=='nailblog_staging_test_image_waiver_v2' else None


def test_image_cover_sha(config):
    scope=_test_scope(config)
    return scope['grant']['cover_sha'] if scope is not None else None


def test_image_original_archive(config):
    scope=active_test_image_v2(config)
    if scope is None:return None
    g=scope['grant']
    return {'file':Path(g['original_manifest_archive_path']).name,'sha256':g['original_manifest_sha256']}


@contextmanager
def _staging_test_image_waiver_v2(config,path,receipt_sha):
    raw,_=_v2_file(path,receipt_sha);g=json.loads(raw)
    retained=json.loads(_v2_file(g.get('article_artifact_path',''),g.get('article_artifact_sha256'),root_owned=False)[0])
    _v2_need(_TEST_IMAGE_SCOPE.get() is None)
    scope={'grant':g,'grant_sha':receipt_sha,'grant_path':str(path),'article':retained.get('article')}
    token=_TEST_IMAGE_SCOPE.set(scope)
    try:_validate_v2(config,scope);yield
    finally:_TEST_IMAGE_SCOPE.reset(token)


def _test_scope(config):
    scope=_TEST_IMAGE_SCOPE.get()
    if scope is None:return None
    g=scope['grant']
    if g.get('kind')=='nailblog_staging_test_image_waiver_v2':
        _validate_v2(config,scope)
        return scope
    if (config is None or config.mode!='live' or config.contract.environment!='staging' or
            not config.contract.verified or config.db_path!=g['config_db_path'] or
            config.slots!=('17:45',) or
            any(getattr(config.contract,k)!=g[k] for k in ('site_origin','api_origin','media_origin')) or
            not time.time()<datetime.fromisoformat(g['expires_at']).timestamp()):
        raise AdminError('staging_test_image_scope_config_mismatch')
    return scope


def check_test_image_scope(config):
    """Recheck capability expiry/config after a queued dispatch obtains its lock."""
    _test_scope(config)


def verify_test_operation(document,config,*,run_id=None,slot_at=None,state=None,post_id=None):
    if not document.get('test_image_waiver'):return
    scope=_test_scope(config)
    if scope is None:raise AdminError('explicit_staging_test_image_capability_required')
    g=scope['grant']
    if run_id is not None and run_id!=g['run_id']:raise AdminError('test_image_outer_run_changed')
    if slot_at is not None and slot_at!=g['slot_at']:raise AdminError('test_image_slot_changed')
    if state is not None:
        if state.path!=config.db_path:raise AdminError('test_image_state_path_changed')
        with state.connection() as db:
            row=db.execute('SELECT run_id,document FROM onepost_runs WHERE post_id=?',(post_id,)).fetchone()
        if not row or row['run_id']!=g['run_id'] or json.loads(row['document']).get('article')!=scope['article']:
            raise AdminError('test_image_post_run_binding_changed')


@contextmanager
def staging_test_image_waiver(config,receipt_path,receipt_sha):
    """Explicit one-run capability. A document flag alone never grants an exception."""
    path=Path(receipt_path)
    if path==_V2_ROOT/'continuation-image-waiver-v2'/'image-waiver.json':
        with _staging_test_image_waiver_v2(config,path,receipt_sha):yield
        return
    if (not path.is_absolute() or not path.is_file() or any(p.is_symlink() for p in (path,*path.parents)) or
            path.stat().st_size>16384 or (os.name=='posix' and
            (path.stat().st_uid!=os.getuid() or path.stat().st_mode&0o077))):
        raise AdminError('private_test_image_grant_required')
    raw=path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=receipt_sha:raise AdminError('test_image_grant_sha_changed')
    g=json.loads(raw)
    if (g.get('kind')!='nailblog_staging_test_image_waiver_v1' or g.get('owner')!='AlexChu' or
            g.get('decision')!='Cho phép ảnh HELD cho TEST staging' or
            g.get('run_id')!=_TEST_RUN or g.get('image_run_id')!=_TEST_RUN+'-images' or
            g.get('config_db_path')!=_TEST_ROOT+'/nailblog.sqlite3' or
            g.get('image_directory')!=_TEST_ROOT+'/run/images' or
            g.get('site_origin')!='https://staging.nailhub.ai' or
            g.get('api_origin')!='https://api-staging.nailhub.ai' or
            g.get('media_origin')!='https://vlink-storages-test.sgp1.cdn.digitaloceanspaces.com' or
            g.get('image_binding')!='13eb05168c174954066b8ca2f31d443cf6546f1a9d3acd3576cf401e6167889c' or
            g.get('cover_sha')!='e5db193be40cdceed86c19855028b1e1b50a098871148cfd7cff152d319629b8' or
            g.get('allowed_body_positions')!=['body-1','body-2','body-3'] or
            g.get('slot_at')!='2026-10-20T17:45:00+07:00' or
            g.get('expires_at')!='2026-10-20T11:00:00+00:00'):
        raise AdminError('test_image_grant_scope_invalid')
    article_path=Path(config.db_path).parent/'article-input.json'
    if (not article_path.is_file() or any(p.is_symlink() for p in (article_path,*article_path.parents)) or
            article_path.stat().st_size>2*1024*1024 or (os.name=='posix' and
            (article_path.stat().st_uid!=os.getuid() or article_path.stat().st_mode&0o077))):
        raise AdminError('private_test_article_required')
    article_raw=article_path.read_bytes();article=json.loads(article_raw)
    if (hashlib.sha256(article_raw).hexdigest()!=g.get('article_input_sha256') or
            g.get('article_input_sha256')!='ca21a66d9cd56d49586623155bd1f7ade95b6f937cb18b6e5aeb1e82ba13bf1f' or
            digest(article)!=g.get('article_digest')):
        raise AdminError('test_image_grant_article_changed')
    if _TEST_IMAGE_SCOPE.get() is not None:raise AdminError('nested_test_image_grant_forbidden')
    token=_TEST_IMAGE_SCOPE.set({'grant':g,'grant_sha':receipt_sha,'article':article})
    try:
        _test_scope(config)
        yield
    finally:_TEST_IMAGE_SCOPE.reset(token)


def test_image_audit(config,article,run_id,binding,directory):
    scope=_test_scope(config)
    if scope is None:return None
    g=scope['grant']
    if (article!=scope['article'] or run_id!=g['image_run_id'] or binding!=g['image_binding'] or
            Path(directory).resolve()!=Path(g['image_directory']).resolve()):
        raise AdminError('test_image_run_binding_changed')
    return {'grant_sha256':scope['grant_sha'],'run_id':g['image_run_id'],'binding':binding,
            'article_digest':g['article_digest'],'automatic_flow_verdict':'HELD',
            'model_quality_verdict':'FAIL','waived_criteria':'image_quality_only','entries':{}}


def test_image_entry(config,audit,position,entry):
    scope=_test_scope(config)
    if scope is None or not isinstance(audit,dict):return False
    g=scope['grant']
    if (audit.get('grant_sha256')!=scope['grant_sha'] or audit.get('run_id')!=g['image_run_id'] or
            audit.get('binding')!=g['image_binding'] or audit.get('article_digest')!=g['article_digest']):
        raise AdminError('test_image_audit_scope_changed')
    if position=='cover':return entry.get('sha')==g['cover_sha']
    return position in g['allowed_body_positions']


def _verify_test_reviews(document,hashes,config):
    scope=_test_scope(config)
    if scope is None:raise AdminError('image_review_required')
    original=scope['article'];actual=document.get('article',document)
    if any(actual.get(k)!=v for k,v in original.items()):raise AdminError('test_image_article_changed')
    audit=document.get('test_image_waiver')
    if (not isinstance(audit,dict) or audit.get('automatic_flow_verdict')!='HELD' or
            audit.get('model_quality_verdict')!='FAIL' or len(hashes)!=4 or
            not test_image_entry(config,audit,'cover',{'sha':hashes[0]})):
        raise AdminError('test_image_waiver_audit_required')
    for position,sha in zip(['cover','body-1','body-2','body-3'],hashes):
        scores=document['image_reviews'].get(sha,{})
        if set(scores)!=set(RUBRIC) or any(type(v) is not bool for v in scores.values()):
            raise AdminError('image_review_required')
        if all(scores.values()):continue
        entry=audit.get('entries',{}).get(position,{})
        if (entry.get('sha')!=sha or entry.get('scores')!=scores or entry.get('model_status')!='rejected' or
                not entry.get('reason') or not test_image_entry(config,audit,position,entry)):
            raise AdminError('test_image_waiver_entry_required')


def verify_reviews(document, hashes, *, config=None):
    if document.get('image_hashes') != hashes or len(set(hashes)) != len(hashes):
        raise AdminError('image_hashes_mismatch')
    reviews = document.get('image_reviews', {})
    if 'review_bypass' in document:
        # TEMPORARY Owner decision (nailblog/AGENTS.md): model review skipped, only while the config switch is ON.
        if document.get('test_image_waiver'):raise AdminError('review_bypass_test_waiver_conflict')
        if getattr(getattr(config,'image',None),'review_bypass',False) is not True:
            raise AdminError('image_review_bypass_disabled')
        audit = document['review_bypass']
        if (not isinstance(audit,dict) or audit.get('enabled') is not True or audit.get('hashes') != hashes or
                not isinstance(reviews,dict) or set(reviews) != set(hashes) or any(v is not None for v in reviews.values())):
            raise AdminError('image_review_bypass_audit_invalid')
        return
    if document.get('test_image_waiver'):
        _verify_test_reviews(document,hashes,config)
        return
    if any(not isinstance(reviews.get(h), dict) or any(reviews[h].get(k) is not True for k in RUBRIC) for h in hashes):
        raise AdminError('image_review_required')


def validate_files(cover_path, image_paths, *, config=None):
    from PIL import Image
    if not 3 <= len(image_paths) <= 5:
        raise AdminError('real_images_required')
    hashes, visual = [], []
    for index, path in enumerate([cover_path, *image_paths]):
        try:
            target = Path(path)
            if target.is_symlink() or not target.is_file() or target.suffix.lower() != '.webp':
                raise ValueError()
            data = target.read_bytes()
            if not 0 < len(data) <= (200 if index == 0 else 150) * 1024:
                raise ValueError()
            with Image.open(target) as img:
                img.load()
                cli = config is not None and config.image.provider == 'subscription' and config.contract.environment == 'staging'
                allowed = (((1376,768),) if cli else ((1600,900),)) if index == 0 else ((1200,675),(1080,1350))
                if img.format != 'WEBP' or img.size not in allowed:
                    raise ValueError()
                small = img.convert('L').resize((9,8))
                pixels = [small.getpixel((x,y)) for y in range(8) for x in range(9)]
                dhash = sum((pixels[y*9+x] > pixels[y*9+x+1]) << (y*8+x) for y in range(8) for x in range(8))
            if any((dhash ^ old).bit_count() <= 6 for old in visual):
                raise AdminError('duplicate_images')
            visual.append(dhash)
            hashes.append(hashlib.sha256(data).hexdigest())
        except AdminError:
            raise
        except Exception:
            raise AdminError('image_bytes_size_or_dimensions_invalid') from None
    return hashes
