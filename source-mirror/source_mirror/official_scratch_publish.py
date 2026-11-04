"""Candidate direct official publisher using the Scratch native UI path.

Not enabled by this module. A post-submit mismatch is UNKNOWN, not success.
"""
import hashlib
import json
import os
from html.parser import HTMLParser
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from contextlib import nullcontext
from .localization import strict_json,packed,digest
from .official_native_guard import _live_inputs,readback
from .official_direct_upload import upload
from .official_editor_adapter import plan
from .official_native_mock import mock_request
from .official_visual_evidence import load_policy,select_rule,receipt_url
from .isolated_compare_client import compare
from .official_publish_policy import load as load_publish_policy
from .official_prepost import check_publish_time
from .official_deadline import require
from .publisher import paste_html,content_fingerprint,search_response
from .official_editor_check import AnchorAudit
from .store import local_day

def canonical_html(markup):
    """Compare serialized HTML structure, attributes and text without string-order noise."""
    class Parser(HTMLParser):
        def __init__(self):super().__init__(convert_charrefs=True);self.events=[]
        def handle_starttag(self,tag,attrs):
            names=[key for key,_ in attrs]
            if len(names)!=len(set(names)):raise ValueError('duplicate HTML attribute')
            self.events.append(('start',tag,tuple(sorted(attrs))))
        def handle_startendtag(self,tag,attrs):self.handle_starttag(tag,attrs);self.handle_endtag(tag)
        def handle_endtag(self,tag):self.events.append(('end',tag))
        def handle_data(self,data):
            if self.events and self.events[-1][0]=='text':self.events[-1]=('text',self.events[-1][1]+data)
            else:self.events.append(('text',data))
        def handle_comment(self,data):self.events.append(('comment',data))
        def handle_decl(self,decl):self.events.append(('decl',decl))
        def handle_pi(self,data):self.events.append(('pi',data))
    if not isinstance(markup,str):raise ValueError('invalid HTML')
    parser=Parser();parser.feed(markup);parser.close();return parser.events


def require_owned_worker_lock(root):
    """Fail closed unless this Linux process owns the scheduler's flock."""
    if os.name!='posix' or not Path('/proc/locks').is_file():raise ValueError('lock ownership evidence unavailable')
    path=Path(root)/'locks'/'worker.lock'
    if path.is_symlink() or not path.is_file():raise ValueError('worker lock unavailable')
    stat=path.stat(); identity=f'{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}:{stat.st_ino}'
    for line in Path('/proc/locks').read_text().splitlines():
        fields=line.split()
        if len(fields)>=6 and fields[1]=='FLOCK' and fields[3]=='WRITE' and fields[4]==str(os.getpid()) and fields[5].lower()==identity.lower():return
    raise ValueError('scheduler worker lock not owned by this process')


def verify_media(root,folder,record_sha,payload,originals,receipts):
    """Check CDN HTTP availability while retaining original and upload bindings."""
    from .scratch_cdn_readback import readback as check_cdn_status
    expected=payload['media']
    if len(receipts)!=len(expected): raise ValueError('upload count differs')
    for descriptor,receipt in zip(expected,receipts):
        if (receipt.get('status')!='UPLOADED_NOT_PUBLISHABLE' or receipt.get('media_id')!=descriptor['id']
                or receipt.get('source_sha256')!=descriptor['sha256']): raise ValueError('upload receipt differs')
        if not isinstance(originals.get(descriptor['id']),bytes) or hashlib.sha256(originals[descriptor['id']]).hexdigest()!=descriptor['sha256']:raise ValueError('original image hash differs')
        binding=receipt.get('binding')
        if not isinstance(binding,dict) or binding.get('source_sha256')!=descriptor['sha256']:raise ValueError('upload provenance differs')
        response=receipt['binding']['response']
        thumb=response.get('thumbnailUrl'); thumb_path=response.get('thumbnailRelativePath')
        if not isinstance(thumb,str) or not isinstance(thumb_path,str) or bool(thumb)!=bool(thumb_path):
            raise ValueError('incomplete thumbnail receipt')
        for key in (('imageUrl','thumbnailUrl') if thumb else ('imageUrl',)):
            url=receipt_url(response,key)
            if check_cdn_status(url)!=200:raise ValueError('CDN image unavailable')


def upload_media(root,folder,record_sha,page,dialog,media):
    """Upload a separate cover, then body descriptors in their frozen order."""
    if not isinstance(media,list) or not media:raise ValueError('cover required')
    receipts=[upload(root,folder,record_sha,page,dialog,media[0]['id'],'cover')]
    for item in media[1:]:
        receipts.append(upload(root,folder,record_sha,page,dialog,item['id'],'gallery'))
    return receipts

def validate_post(payload_bytes,mapping,receipts,request_bytes):
    """After POST only: reject changed body or upload mapping as UNKNOWN."""
    actual=strict_json(request_bytes.decode('utf-8'))
    if not isinstance(actual,dict) or not isinstance(actual.get('images'),list):raise ValueError('native create body invalid')
    objects=[actual.get('coverImage'),*actual['images']]
    if len(objects)!=len(receipts):raise ValueError('native media count differs')
    for obj,receipt in zip(objects,receipts):
        if not isinstance(obj,dict) or not isinstance(receipt.get('binding'),dict):raise ValueError('native media invalid')
        response=receipt['binding']['response']
        if any(obj.get(k)!=response.get(k) for k in ('imageUrl','imageRelativePath','thumbnailUrl','thumbnailRelativePath')):
            raise ValueError('native media receipt differs')
    expected=mock_request(payload_bytes,objects[0],objects[1:],mapping)
    for expected_locale,actual_locale in zip(expected['translations'],actual.get('translations',[])):
        if canonical_html(expected_locale['content'])!=canonical_html(actual_locale.get('content')):raise ValueError('native HTML content differs')
        expected_locale['content']=actual_locale['content']
    if packed(actual)!=packed(expected):raise ValueError('native body differs from canonical payload')
    return actual

def create_route_guard(payload_bytes,mapping,receipts,state,at_create=None):
    """Validate only the native Create POST; never intercept image upload."""
    def guard(route):
        req=route.request
        if req.method!='POST':return route.continue_()
        parsed=urlsplit(req.url)
        if (parsed.scheme!='https' or parsed.hostname!='api.nailhub.ai' or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.path!='/api/v1/admin/blogs' or parsed.query or parsed.fragment):
            return route.abort('blockedbyclient')
        if state.get('seen'):return route.abort('blockedbyclient')
        state['seen']=True
        try:
            data=req.post_data_buffer
            if not isinstance(data,bytes) or len(data)>1_000_000:raise ValueError('create request too large')
            validate_post(payload_bytes,mapping,receipts,data)
            if at_create is not None:at_create()
            state['validated']=True
        except Exception:
            state['invalid']=True
            return route.abort('blockedbyclient')
        return route.continue_()
    return guard


def publish(store,article,attempt):
    """Only the scheduler holding worker_lock with a verified intent may invoke this."""
    from .official_publisher import available,verify_readback
    if available() is not True:raise ValueError('official publisher disabled')
    require(750,active=True)
    row=store.db.execute("SELECT o.evidence_path,o.record_sha,a.state,t.state AS attempt_state,t.reserved_day,s.day AS slot_day,s.slot FROM official_candidates o JOIN articles a ON a.id=o.article_id JOIN attempts t ON t.article_id=a.id JOIN official_attempt_slots s ON s.attempt_id=t.id WHERE t.id=? AND a.id=?",(attempt,article['id'])).fetchone()
    if not row or row['state']!='publishing' or row['attempt_state']!='intent' or row['reserved_day']!=local_day() or row['slot_day']!=row['reserved_day']:
        raise ValueError('official publishing intent unavailable')
    policy=load_publish_policy()
    if policy.get('enabled') is not True:raise ValueError('official publication policy disabled')
    root=store.root;folder=Path(row['evidence_path']);record_sha=row['record_sha']
    # Scheduler owns the only lock acquisition; live lock is non-reentrant.
    with nullcontext():
        require_owned_worker_lock(root)
        payload_bytes,boundary,mapping,originals,media_policy=_live_inputs(root,folder,record_sha)
        payload=strict_json(payload_bytes.decode('utf-8'))
        if payload['source']['id'] not in policy.get('source_ids',[]):raise ValueError('source not enabled')
        check_publish_time(payload['published_at'],row['reserved_day'],row['slot'],datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
        record=strict_json((folder/'record.json').read_text(encoding='utf-8'));draft=(folder/'draft.json').read_bytes()
        form=plan(draft,record['editorial'],record['media_manifest'],payload_bytes)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            profile=root/'browser'/'nailhub-profile';profile.mkdir(parents=True,exist_ok=True)
            context=p.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',timezone_id='Asia/Ho_Chi_Minh',permissions=['clipboard-read','clipboard-write'])
            try:
                page=context.new_page(); page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
                from .official_duplicate_scan import check
                check(page,payload['source']['url'])
                from .original_duplicate_scan import check_bodies
                check_bodies(page,{'VI':payload['locales']['vi']['body_html'],'EN':payload['locales']['en']['body_html']})
                from .original_duplicate_scan import check_titles
                check_titles(page,[payload['locales']['vi']['title'],payload['locales']['en']['title']])
                page.get_by_role('button',name='Create Blog',exact=True).click();dialog=page.get_by_role('dialog')
                dialog.get_by_role('combobox').nth(0).click();page.get_by_role('option',name=form['category'],exact=True).click()
                dialog.locator('#externalSourceUrl').fill(form['source_url'])
                media=payload['media'];receipts=upload_media(root,folder,record_sha,page,dialog,media)
                verify_media(root,folder,record_sha,payload,originals,receipts)
                for language,code in (('Vietnamese','vi'),('English','en')):
                    fields=form['locales'][code];dialog.get_by_role('tab',name=language,exact=True).click()
                    for placeholder,key in ((f'Enter title in {language} (5-200 characters)','title'),(f'Enter summary in {language} (max 500 characters)','summary'),('SEO title (50-70 characters recommended)','meta_title'),('SEO description (120-160 characters recommended)','meta_description')):
                        field=dialog.get_by_placeholder(placeholder,exact=True);field.fill(fields[key])
                        if field.input_value()!=fields[key]:raise ValueError('editor field mismatch')
                    editor=dialog.locator('.ql-editor[contenteditable=true]');paste_html(page,editor,fields['body_html'])
                    if content_fingerprint(editor.inner_html())!=content_fingerprint(fields['body_html']) or AnchorAudit(editor.inner_html()).links!=AnchorAudit(fields['body_html']).links:raise ValueError('editor content mismatch')
                for tag in form['hashtags']:
                    field=dialog.get_by_placeholder('Add hashtag (2-50 characters each)',exact=True);field.fill(tag);field.locator('..').get_by_role('button',name='Add',exact=True).click()
                    if field.input_value():raise ValueError('hashtag not added')
                dialog.get_by_role('combobox').nth(1).click();page.get_by_role('option',name=payload['status'],exact=True).click()
                dialog.get_by_role('checkbox').nth(0).set_checked(payload['is_public']);dialog.get_by_role('checkbox').nth(1).set_checked(payload['is_featured'])
                published=datetime.fromisoformat(payload['published_at'].replace('Z','+00:00'))
                dialog.locator('#publishedAt').fill(published.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%Y-%m-%dT%H:%M'))
                latest=_live_inputs(root,folder,record_sha)
                if (latest[0],latest[2],latest[3],latest[4])!=(payload_bytes,mapping,originals,media_policy) or load_publish_policy()!=policy or store.paused():raise ValueError('evidence or policy changed')
                verify_media(root,folder,record_sha,payload,originals,receipts)
                for language,code in (('Vietnamese','vi'),('English','en')):
                    fields=form['locales'][code];dialog.get_by_role('tab',name=language,exact=True).click()
                    for placeholder,key in ((f'Enter title in {language} (5-200 characters)','title'),(f'Enter summary in {language} (max 500 characters)','summary'),('SEO title (50-70 characters recommended)','meta_title'),('SEO description (120-160 characters recommended)','meta_description')):
                        if dialog.get_by_placeholder(placeholder,exact=True).input_value()!=fields[key]:raise ValueError('editor field changed')
                    editor=dialog.locator('.ql-editor[contenteditable=true]');actual=editor.inner_html()
                    if content_fingerprint(actual)!=content_fingerprint(fields['body_html']) or AnchorAudit(actual).links!=AnchorAudit(fields['body_html']).links:raise ValueError('editor changed')
                if dialog.locator('#externalSourceUrl').input_value()!=form['source_url']:raise ValueError('source changed')
                if form['category'] not in dialog.get_by_role('combobox').nth(0).inner_text() or payload['status'] not in dialog.get_by_role('combobox').nth(1).inner_text():raise ValueError('category or status changed')
                if dialog.get_by_role('checkbox').nth(0).is_checked() is not payload['is_public'] or dialog.get_by_role('checkbox').nth(1).is_checked() is not payload['is_featured']:raise ValueError('visibility changed')
                expected_time=published.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%Y-%m-%dT%H:%M')
                if dialog.locator('#publishedAt').input_value()!=expected_time:raise ValueError('publication time changed')
                if any(dialog.get_by_text(tag,exact=True).count()!=1 for tag in form['hashtags']):raise ValueError('hashtag changed')
                for receipt in receipts:
                    present=dialog.evaluate("(d,url) => [...d.querySelectorAll('img')].some(i => i.src===url && i.complete && i.naturalWidth>0)",receipt['url'])
                    if not present:raise ValueError('uploaded image missing from editor')
                current=store.db.execute('SELECT state,reserved_day FROM attempts WHERE id=?',(attempt,)).fetchone()
                if (not current or current['state']!='intent' or current['reserved_day']!=row['reserved_day']
                        or local_day()!=row['reserved_day'] or available() is not True or load_publish_policy()!=policy or store.paused()):raise ValueError('official intent or policy changed')
                check_publish_time(payload['published_at'],row['reserved_day'],row['slot'],datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')));require(1,active=True)
                route_state={}
                def at_create():
                    current=_live_inputs(root,folder,record_sha)
                    if (current[0],current[2],current[3],current[4])!=(payload_bytes,mapping,originals,media_policy) or load_publish_policy()!=policy or store.paused():raise ValueError('create inputs changed')
                    verify_media(root,folder,record_sha,payload,originals,receipts)
                duplicate_page=context.new_page()
                try:check_bodies(duplicate_page,{'VI':payload['locales']['vi']['body_html'],'EN':payload['locales']['en']['body_html']})
                finally:duplicate_page.close()
                context.route('https://api.nailhub.ai/api/v1/admin/blogs*',create_route_guard(payload_bytes,mapping,receipts,route_state,at_create))
                with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).hostname=='api.nailhub.ai' and urlsplit(r.url).path=='/api/v1/admin/blogs',timeout=30000) as created:
                    dialog.get_by_role('button',name='Create',exact=True).click()
                if not route_state.get('validated') or not created.value.ok:raise ValueError('create outcome unknown')
                native=validate_post(payload_bytes,mapping,receipts,created.value.request.post_data_buffer)
                return verify_readback(page,p,payload_bytes,native,mapping,row['reserved_day'])
            finally:context.close()
