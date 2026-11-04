"""Isolated non-publishing official editor canary.

The context is created here, service workers are blocked, and request filtering
is installed before navigation. This is not an eligibility or publish gate.
"""
from pathlib import Path
import re
from urllib.parse import urlsplit
from .lock import worker_lock
from .official_editor_adapter import plan
from .official_editor_check import AnchorAudit
from .publisher import content_fingerprint, paste_html


def allowed_read(method,url):
    if method not in ('GET','HEAD'): return False
    try:
        parsed=urlsplit(url); port=parsed.port
    except (ValueError,TypeError): return False
    if parsed.scheme!='https' or parsed.username or parsed.password or port not in (None,443) or parsed.fragment:
        return False
    if '%' in parsed.path or '\\' in parsed.path or any(p in ('.','..') for p in parsed.path.split('/')):
        return False
    if parsed.hostname=='nailhub.ai':
        return parsed.path=='/admin/blogs' or bool(re.fullmatch(r'/assets/[A-Za-z0-9._/-]+\.(?:js|css)',parsed.path))
    return parsed.hostname=='api.nailhub.ai' and parsed.path in {
        '/api/v1/UserProfile/me','/api/v1/admin/blog-categories','/api/v1/admin/blogs'}


def run(root,draft_bytes,editorial,media,payload_bytes):
    """Fill and inspect only; all non-allowlisted requests and writes abort."""
    values=plan(draft_bytes,editorial,media,payload_bytes)
    root=Path(root).resolve(); profile=root/'browser'/'nailhub-profile'
    if any(p.is_symlink() for p in (root/'browser',profile)) or not profile.is_dir():
        raise ValueError('dedicated browser profile unavailable')
    from playwright.sync_api import sync_playwright
    with worker_lock(root), sync_playwright() as playwright:
        context=playwright.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',
                  timezone_id='Asia/Ho_Chi_Minh',permissions=['clipboard-read','clipboard-write'],args=['--no-startup-window'])
        try:
            # No page or caller-supplied route is trusted. Block sockets too.
            if not hasattr(context,'route_web_socket'): raise ValueError('websocket blocking unavailable')
            context.route_web_socket('**/*',lambda ws:ws.close())
            def guard(route):
                if allowed_read(route.request.method,route.request.url): route.continue_()
                else: route.abort('blockedbyclient')
            context.route('**/*',guard)
            if any(page.url not in ('','about:blank') for page in context.pages): raise ValueError('unexpected startup page')
            page=context.new_page(); response=page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
            if not response or response.status!=200 or page.url!='https://nailhub.ai/admin/blogs': raise ValueError('admin canary unavailable')
            page.get_by_role('button',name='Create Blog',exact=True).click(timeout=30000)
            dialog=page.get_by_role('dialog'); dialog.locator('#externalSourceUrl').fill(values['source_url'])
            for language,code in (('Vietnamese','vi'),('English','en')):
                fields=values['locales'][code]; dialog.get_by_role('tab',name=language,exact=True).click()
                for placeholder,key in ((f'Enter title in {language} (5-200 characters)','title'),
                                        (f'Enter summary in {language} (max 500 characters)','summary'),
                                        ('SEO title (50-70 characters recommended)','meta_title'),
                                        ('SEO description (120-160 characters recommended)','meta_description')):
                    field=dialog.get_by_placeholder(placeholder,exact=True); field.fill(fields[key])
                    if field.input_value()!=fields[key]: raise ValueError('canary field mismatch')
                editor=dialog.locator('.ql-editor[contenteditable=true]')
                paste_html(page,editor,fields['body_html']); observed=editor.inner_html(); expected=fields['body_html']
                if content_fingerprint(observed)!=content_fingerprint(expected) or AnchorAudit(observed).links!=AnchorAudit(expected).links:
                    raise ValueError('canary content mismatch')
            return {'status':'CANARY_ONLY_NOT_PUBLISHABLE','payload_sha256':values['payload_sha256']}
        finally:
            context.close()
