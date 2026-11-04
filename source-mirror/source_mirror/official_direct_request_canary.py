"""Direct UI request canary: Create request is always aborted before network."""
from datetime import datetime
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from .lock import worker_lock
from .localization import strict_json
from .official_native_guard import _live_inputs
from .official_editor_adapter import plan
from .official_upload_canary import isolated_profile
from .official_direct_upload import upload
from .official_scratch_publish import verify_media,validate_post
from .official_upload_controller import _sync_directory_chain
from .publisher import paste_html,content_fingerprint
from .official_editor_check import AnchorAudit

API_HOST='api.nailhub.ai'

def persist_aborted_request(root,folder,data):
    if not isinstance(data,bytes) or len(data)>1000000:raise ValueError('invalid request capture')
    request_sha256=hashlib.sha256(data).hexdigest();capture_dir=folder/'aborted-create-requests'
    if capture_dir.is_symlink():raise ValueError('unsafe request capture directory')
    capture_dir.mkdir(mode=0o700,exist_ok=True)
    stat=capture_dir.stat()
    if os.name=='posix' and (stat.st_uid!=os.getuid() or stat.st_mode & 0o077):raise ValueError('unsafe request capture directory')
    fd=os.open(capture_dir/(request_sha256+'.json'),os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    _sync_directory_chain(capture_dir,root)
    return request_sha256

def capture_or_block(route,captured):
    req=route.request; parsed=urlsplit(req.url)
    if req.method=='POST' and parsed.scheme=='https' and parsed.hostname==API_HOST and parsed.port in (None,443) and not parsed.username and not parsed.password and parsed.path=='/api/v1/admin/blogs' and not parsed.query and not parsed.fragment:
        try: captured.append(req.post_data_buffer)
        finally: route.abort('blockedbyclient')
    else: block_create(route)


def block_create(route):
    """Block writes except the native upload; pass OPTIONS unchanged."""
    req=route.request; parsed=urlsplit(req.url)
    if req.method in ('GET','HEAD','OPTIONS'):
        route.continue_(); return
    if (req.method=='POST' and parsed.scheme=='https' and parsed.hostname==API_HOST
            and parsed.port in (None,443) and not parsed.username and not parsed.password
            and parsed.path=='/api/v1/Images/quick-upload' and not parsed.query and not parsed.fragment):
        route.continue_(); return
    route.abort('blockedbyclient')


def run(root,evidence_path,record_sha):
    root=Path(root).resolve();folder=Path(evidence_path)
    with worker_lock(root):
        payload,boundary,mapping,originals,policy=_live_inputs(root,folder,record_sha)
        value=strict_json(payload.decode('utf-8'));record=strict_json((folder/'record.json').read_text(encoding='utf-8'))
        form=plan((folder/'draft.json').read_bytes(),record['editorial'],record['media_manifest'],payload)
        if not value['media'] or len(value['media'])>10:raise ValueError('invalid media')
        from playwright.sync_api import sync_playwright
        with isolated_profile(root) as profile,sync_playwright() as p:
            context=p.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',timezone_id='Asia/Ho_Chi_Minh',permissions=['clipboard-read','clipboard-write'],args=['--no-startup-window','--disable-background-networking'])
            try:
                if not hasattr(context,'route_web_socket'):raise ValueError('websocket guard unavailable')
                context.route_web_socket('**/*',lambda websocket:websocket.close())
                captured=[]
                def guard(route):
                    capture_or_block(route,captured)
                context.route('**/*',guard)
                if any(page.url not in ('','about:blank') for page in context.pages):raise ValueError('unexpected startup page')
                page=context.new_page();page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
                page.get_by_role('button',name='Create Blog',exact=True).click(timeout=30000);dialog=page.get_by_role('dialog')
                dialog.get_by_role('combobox').nth(0).click();page.get_by_role('option',name=form['category'],exact=True).click()
                dialog.locator('#externalSourceUrl').fill(form['source_url'])
                receipts=[upload(root,folder,record_sha,page,dialog,value['media'][0]['id'],'cover')]
                for item in value['media']:receipts.append(upload(root,folder,record_sha,page,dialog,item['id'],'gallery'))
                verify_media(root,folder,record_sha,value,originals,receipts)
                for receipt in receipts:
                    page.wait_for_function("url => [...document.querySelectorAll('[role=dialog] img')].some(i => i.src === url && i.complete && i.naturalWidth > 0)",arg=receipt['url'],timeout=30000)
                for language,code in (('Vietnamese','vi'),('English','en')):
                    fields=form['locales'][code];dialog.get_by_role('tab',name=language,exact=True).click()
                    for placeholder,key in ((f'Enter title in {language} (5-200 characters)','title'),(f'Enter summary in {language} (max 500 characters)','summary'),('SEO title (50-70 characters recommended)','meta_title'),('SEO description (120-160 characters recommended)','meta_description')):
                        field=dialog.get_by_placeholder(placeholder,exact=True);field.fill(fields[key])
                        if field.input_value()!=fields[key]:raise ValueError('editor field mismatch')
                    editor=dialog.locator('.ql-editor[contenteditable=true]');paste_html(page,editor,fields['body_html'])
                    actual=editor.inner_html()
                    if content_fingerprint(actual)!=content_fingerprint(fields['body_html']) or AnchorAudit(actual).links!=AnchorAudit(fields['body_html']).links:raise ValueError('editor content mismatch')
                for tag in form['hashtags']:
                    field=dialog.get_by_placeholder('Add hashtag (2-50 characters each)',exact=True);field.fill(tag);field.locator('..').get_by_role('button',name='Add',exact=True).click()
                dialog.get_by_role('combobox').nth(1).click();page.get_by_role('option',name=value['status'],exact=True).click()
                dialog.get_by_role('checkbox').nth(0).set_checked(value['is_public']);dialog.get_by_role('checkbox').nth(1).set_checked(value['is_featured'])
                published=datetime.fromisoformat(value['published_at'].replace('Z','+00:00'))
                dialog.locator('#publishedAt').fill(published.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%Y-%m-%dT%H:%M'))
                with page.expect_request(lambda req:req.method=='POST' and urlsplit(req.url).hostname==API_HOST and urlsplit(req.url).path=='/api/v1/admin/blogs',timeout=30000):
                    dialog.get_by_role('button',name='Create',exact=True).click()
                for _ in range(50):
                    if captured:break
                    page.wait_for_timeout(100)
                if len(captured)!=1 or not isinstance(captured[0],bytes) or len(captured[0])>1000000:raise ValueError('create abort capture unavailable')
                request_sha256=persist_aborted_request(root,folder,captured[0])
                print('ABORTED_CREATE_REQUEST_SHA256',request_sha256,flush=True)
                validate_post(payload,mapping,receipts,captured[0])
                return {'status':'DIRECT_REQUEST_ABORTED_NOT_PUBLISHABLE','uploads':len(receipts),'create_blocked':True,'request_sha256':request_sha256}
            finally:context.close()
