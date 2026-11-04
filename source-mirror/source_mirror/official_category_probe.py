"""Read-only native category observation; never creates or publishes."""
import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit
import uuid
from .lock import worker_lock
from .official_upload_canary import isolated_profile
from .official_editor_canary import allowed_read
from .localization import strict_json

def extract_categories(value):
    result=[];visited=0
    def visit(obj,depth):
        nonlocal visited
        visited+=1
        if visited>1000 or depth>12:raise ValueError('category response too large')
        if isinstance(obj,dict):
            ident=obj.get('id');label=obj.get('name')
            if isinstance(ident,str) and isinstance(label,str) and label and len(label)<=80:
                try:canonical=str(uuid.UUID(ident))
                except ValueError:canonical=None
                if canonical==ident.lower():result.append({'id':canonical,'name':label})
            for child in obj.values():visit(child,depth+1)
        elif isinstance(obj,list):
            for child in obj:visit(child,depth+1)
    visit(value,0)
    if not result or len(result)>100 or len({x['id'] for x in result})!=len(result) or len({x['name'] for x in result})!=len(result):
        raise ValueError('invalid or duplicate categories')
    return result

def run(root):
    root=Path(root).resolve()
    from playwright.sync_api import sync_playwright
    with worker_lock(root),isolated_profile(root) as profile,sync_playwright() as playwright:
        context=playwright.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',
                 timezone_id='Asia/Ho_Chi_Minh',args=['--no-startup-window','--disable-background-networking'])
        try:
            if not hasattr(context,'route_web_socket'):raise ValueError('websocket blocking unavailable')
            context.route_web_socket('**/*',lambda ws:ws.close())
            context.route('**/*',lambda route:route.continue_() if allowed_read(route.request.method,route.request.url) else route.abort('blockedbyclient'))
            if any(page.url not in ('','about:blank') for page in context.pages):raise ValueError('unexpected startup page')
            page=context.new_page()
            with page.expect_response(lambda r:r.request.method=='GET' and urlsplit(r.url).hostname=='api.nailhub.ai'
                                      and urlsplit(r.url).path=='/api/v1/admin/blog-categories',timeout=30000) as observed:
                nav=page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
            if not nav or nav.status!=200 or page.url!='https://nailhub.ai/admin/blogs' or not observed.value.ok:
                raise ValueError('admin category source unavailable')
            declared=observed.value.headers.get('content-length')
            if declared is not None and (not declared.isdecimal() or int(declared)>256000):raise ValueError('category response too large')
            raw=observed.value.body()
            if len(raw)>256000:raise ValueError('category response too large')
            categories=extract_categories(strict_json(raw.decode('utf-8')))
            page.get_by_role('button',name='Create Blog',exact=True).click(timeout=15000)
            dialog=page.get_by_role('dialog');dialog.get_by_role('combobox').nth(0).click(timeout=10000)
            options=page.get_by_role('option').all_text_contents()
            if len(options)!=len(categories) or set(options)!={item['name'] for item in categories}:
                raise ValueError('UI and API category list differ')
            return {'status':'READ_ONLY_CATEGORY_OBSERVATION','categories':categories,'writes_allowed':False}
        finally:context.close()

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--root',required=True);args=parser.parse_args()
    print(json.dumps(run(args.root),ensure_ascii=False))
