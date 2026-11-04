"""Stage one already-generated official draft using root-owned media policy.

Not scheduled or enabled by default. This does not create rights or approve a
publication; prepare and Store stage revalidate all evidence independently.
"""
import hashlib
import json
import os
from datetime import datetime,timedelta,timezone
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo
from .official_auto_eligibility import load_media_policy
from .official_sources import SOURCES
from .official_stock_policy import is_candidate,require_license
from .official_staging_runner import _read,run

def _sha(value):
    return isinstance(value,str) and re.fullmatch(r'[0-9a-f]{64}',value) is not None

def discover(root):
    """List at most eight draft identities; eligibility is checked later."""
    root=Path(root)
    if not root.is_absolute() or root!=root.resolve() or root.is_symlink():raise ValueError('unsafe state root')
    drafts=root/'official-drafts'; result=[]; inspected=0
    if drafts.is_symlink():raise ValueError('unsafe draft directory')
    if not drafts.is_dir():return result
    for item in SOURCES:
        directory=drafts/item['id']
        if directory.is_symlink():raise ValueError('unsafe draft source directory')
        if not directory.is_dir():continue
        with os.scandir(directory) as snapshots:
            for snapshot in snapshots:
                inspected+=1
                if inspected>100:return result
                if not _sha(snapshot.name) or not snapshot.is_dir(follow_symlinks=False):continue
                with os.scandir(snapshot.path) as files:
                    for entry in files:
                        inspected+=1
                        if inspected>100:return result
                        if not entry.is_file(follow_symlinks=False) or not entry.name.endswith('.json') or not _sha(entry.name[:-5]):continue
                        result.append({'source_id':item['id'],'source_sha256':snapshot.name,'draft_sha256':entry.name[:-5]})
                        if len(result)==8:return result
    return result

class StagingWindowElapsed(ValueError): pass

def stage_draft(root,source_id,source_sha,draft_sha,category,slot,*,restage=False,lock_held=False,generated_request=None,scheduled_target=None):
    root=Path(root)
    if not root.is_absolute() or root!=root.resolve() or root.is_symlink():raise ValueError('unsafe state root')
    matches=[item for item in SOURCES if item['id']==source_id]
    if len(matches)!=1 or not _sha(source_sha) or not _sha(draft_sha):raise ValueError('invalid draft identity')
    from .official_draft import source_snapshot
    from .slot_policy import next_slot,allows
    slot_source=source_snapshot(root,source_id,source_sha)
    title=slot_source['metadata']['title'];context=slot_source.get('reference_text','');now=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
    if scheduled_target is None:
        selected,target=next_slot(title,now,context)
        if slot!=selected:raise ValueError('target must be next topic-appropriate scheduled slot')
    else:
        from datetime import datetime as DateTimeType
        if not isinstance(scheduled_target,DateTimeType) or scheduled_target.tzinfo is None:raise ValueError('scheduled target invalid')
        target=scheduled_target.astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
        if target.strftime('%H:%M')!=slot or target.second or target.microsecond or not allows(slot,title,context):raise ValueError('scheduled target invalid')
        if target.date() not in (now.date(),(now+timedelta(days=1)).date()):raise ValueError('scheduled target invalid')
    if target-now<timedelta(minutes=35):raise StagingWindowElapsed('staging window elapsed before generation')
    if generated_request is not None:
        if restage or not isinstance(generated_request,dict) or set(generated_request)!={'policy_path'}:raise ValueError('invalid generated request')
        policy=load_media_policy()
        if category not in policy['category_mapping'] or slot not in ('08:00','12:00','16:00','20:00'):raise ValueError('invalid editorial target')
        now=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
        if target-now<timedelta(minutes=35):raise StagingWindowElapsed('staging window elapsed before generation')
        draft=_read(root/'official-drafts',f'{source_id}/{source_sha}/{draft_sha}.json',4000000)
        if hashlib.sha256(draft).hexdigest()!=draft_sha:raise ValueError('draft changed')
        from .official_deadline import require
        from .official_editorial_generator import generate
        require(235,active=True)
        editorial=generate(root,source_id,source_sha,draft,{'category':category,'published_at':target.isoformat()})
        from .official_generated_media import load_policy as load_generated_policy
        generated_policy=load_generated_policy()
        if source_id not in generated_policy['source_ids']:raise ValueError('generated source not enabled')
        from .official_cover_subject import stage_trusted_cover
        require(560,active=True)  # One bounded retry may need two generation windows.
        generated=stage_trusted_cover(root,source_id,source_sha,draft_sha,policy_path=generated_request['policy_path'],editorial=editorial)
        image_sha=generated['cover']['image_sha256']; evidence=dict(generated['cover']['evidence'])
        if load_generated_policy()!=generated_policy:raise ValueError('generated policy changed during generation')
        evidence.update({'source_sha256':source_sha,'draft_sha256':draft_sha,'subject_sha256':generated['subject']['subject_sha256']})
        evidence_bytes=(json.dumps(evidence,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n').encode()
        evidence_sha=hashlib.sha256(evidence_bytes).hexdigest()
        descriptor={'id':'ai-cover','kind':'generated','sha256':image_sha,'provenance_url':'https://antigravity.google/','credit':'AI-generated illustration','rights_basis':'generated-ai','rights_evidence_sha256':evidence_sha,'alt':'AI-generated illustration related to the article'}
        image=_read(root/'generated-covers',image_sha+'.image',12000000)
        if hashlib.sha256(image).hexdigest()!=image_sha:raise ValueError('generated image changed')
        draft=_read(root/'official-drafts',f'{source_id}/{source_sha}/{draft_sha}.json',4000000)
        if hashlib.sha256(draft).hexdigest()!=draft_sha:raise ValueError('draft changed')
        base=root/'official-inputs'
        if base.is_symlink() or not base.is_dir():raise ValueError('input directory unavailable')
        with tempfile.TemporaryDirectory(prefix='.official-generated-',dir=base) as directory:
            folder=Path(directory);prefix=folder.name
            (folder/'draft.json').write_bytes(draft);(folder/'image').write_bytes(image);(folder/'evidence.txt').write_bytes(evidence_bytes)
            (folder/'editorial.json').write_text(json.dumps(editorial,ensure_ascii=False),encoding='utf-8')
            manifest={'source_id':source_id,'source_sha256':source_sha,'draft':prefix+'/draft.json','editorial':prefix+'/editorial.json','media':[{'descriptor':descriptor,'image':prefix+'/image','rights':prefix+'/evidence.txt'}]}
            (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
            return run(root,prefix+'/manifest.json',restage=False,lock_held=lock_held)
    item=matches[0]; policy=load_media_policy()
    if not isinstance(category,str) or category not in policy['category_mapping']:raise ValueError('category policy missing')
    entries=[x for x in policy['entries'] if isinstance(x,dict) and x.get('source_id')==source_id
             and x.get('source_url')==item['url'] and x.get('geographic_scope')==item['scope']
             and x.get('jurisdiction')=='WORLDWIDE']
    if len(entries)!=1:raise ValueError('unambiguous worldwide source media policy required')
    entry=entries[0]
    try: expiry=datetime.fromisoformat(entry['valid_until_utc'].replace('Z','+00:00'))
    except (KeyError,ValueError,AttributeError):raise ValueError('media policy expiry invalid') from None
    if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):raise ValueError('media policy expired')
    image_sha=entry.get('image_sha256');rights_sha=entry.get('rights_evidence_sha256')
    if not _sha(image_sha) or not _sha(rights_sha):raise ValueError('media policy hashes invalid')
    descriptor={'id':'stock','sha256':image_sha,'provenance_url':entry.get('provenance_url'),
                'credit':entry.get('credit'),'rights_basis':entry.get('rights_basis'),
                'rights_evidence_sha256':rights_sha,'alt':entry.get('alt')}
    if not is_candidate(descriptor):raise ValueError('reviewed stock media required')
    require_license(descriptor,entry)
    base=root/'official-inputs'
    if base.is_symlink() or not base.is_dir():raise ValueError('input directory unavailable')
    image_name='media/'+image_sha;rights_name='rights/'+rights_sha+'.txt'
    if hashlib.sha256(_read(base,image_name,20_000_000)).hexdigest()!=image_sha:raise ValueError('stock image changed')
    if hashlib.sha256(_read(base,rights_name,256_000)).hexdigest()!=rights_sha:raise ValueError('stock rights changed')
    drafts=root/'official-drafts';relative='/'.join((source_id,source_sha,draft_sha+'.json'))
    if drafts.is_symlink() or not drafts.is_dir():raise ValueError('draft directory unavailable')
    draft=_read(drafts,relative,4_000_000)
    if hashlib.sha256(draft).hexdigest()!=draft_sha:raise ValueError('draft changed')
    if slot not in ('08:00','12:00','16:00','20:00'):raise ValueError('slot unavailable')
    now=datetime.now(ZoneInfo('Asia/Ho_Chi_Minh'))
    if target-now<timedelta(minutes=35):raise StagingWindowElapsed('staging window elapsed before generation')
    with tempfile.TemporaryDirectory(prefix='.official-stage-',dir=base) as directory:
        folder=Path(directory); prefix=folder.name
        (folder/'draft.json').write_bytes(draft)
        (folder/'editorial.json').write_text(json.dumps({'category':category,'published_at':target.isoformat()}),encoding='utf-8')
        manifest={'source_id':source_id,'source_sha256':source_sha,'draft':prefix+'/draft.json',
                  'editorial':prefix+'/editorial.json','media':[{'descriptor':descriptor,'image':image_name,'rights':rights_name}]}
        (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
        return run(root,prefix+'/manifest.json',restage=restage,lock_held=lock_held)

if __name__=='__main__':
    import argparse,sys
    parser=argparse.ArgumentParser(description='Stage an official draft without publishing')
    parser.add_argument('--root',required=True)
    parser.add_argument('--discover',action='store_true')
    parser.add_argument('--source-id');parser.add_argument('--source-sha');parser.add_argument('--draft-sha')
    parser.add_argument('--category');parser.add_argument('--slot')
    args=parser.parse_args()
    try:
        if args.discover:
            if any((args.source_id,args.source_sha,args.draft_sha,args.category,args.slot)):raise ValueError('discover takes no staging options')
            result=discover(Path(args.root))
        else:result=stage_draft(Path(args.root),args.source_id,args.source_sha,args.draft_sha,args.category,args.slot)
        print(json.dumps(result))
    except Exception as error:
        print('official draft staging held: '+type(error).__name__,file=sys.stderr);raise SystemExit(1)
