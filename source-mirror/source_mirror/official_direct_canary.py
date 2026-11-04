"""Direct UI upload canary. It never clicks Create or publishes an article."""
from pathlib import Path
from urllib.parse import urlsplit
from .lock import worker_lock
from .localization import strict_json
from .official_native_guard import _live_inputs
from .official_editor_adapter import plan
from .official_upload_canary import isolated_profile
from .official_direct_upload import upload
from .official_scratch_publish import verify_media
from .publisher import paste_html,content_fingerprint
from .official_editor_check import AnchorAudit

API_HOST='api.nailhub.ai'


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
                context.route('**/*',block_create)
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
                # Never click the Create button; all receipts remain non-publishable.
                return {'status':'DIRECT_UI_CANARY_NOT_PUBLISHABLE','uploads':len(receipts),'create_clicked':False}
            finally:context.close()
