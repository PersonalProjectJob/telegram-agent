"""Candidate pre-reservation native body upload; not installed or wired."""
import json,os,re,stat,time
from pathlib import Path
from urllib.parse import urlsplit
from . import body_policy,preupload_journal
from .body_render import _asset
from .illustration_plan import bind_plan,verify_producer
from .localization import digest,load_approved
from .store import verify_snapshot,sha,ZONE
from .official_upload_canary import isolated_profile
from .official_editor_canary import allowed_read

class UploadHold(ValueError):pass


def _write_evidence(path,data):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as out:out.write(data);out.flush();os.fsync(out.fileno())

def run(store,article,localized,assets,day,slot,deadline,*,playwright_factory=None,peer_alive=lambda:True):
    """Upload verified body images in a disposable context; never creates a blog.

    Caller holds worker_lock and supervises this in a dedicated cgroup; the
    worker-to-service channel must validate SO_PEERCRED and article binding.
    """
    from datetime import datetime
    from .official_deadline import seconds_available
    from .scratch_native_guard import bind_upload
    if time.monotonic()>=deadline or not peer_alive():raise UploadHold('request expired or disconnected')
    root=store.root
    if store.paused() or store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone():raise UploadHold('publication paused or unresolved')
    if store.db.execute("SELECT count(*) FROM attempts WHERE state='published' AND published_day=?",(day,)).fetchone()[0]>=4:raise UploadHold('daily quota reached')
    if datetime.now(ZONE).date().isoformat()!=day:raise UploadHold('day changed')
    if slot is not None and seconds_available(day,slot,datetime.now(ZONE))<450:raise UploadHold('slot budget unavailable')
    folder=verify_snapshot(root,article['id']);meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    row=store.db.execute('SELECT state FROM articles WHERE id=?',(article['id'],)).fetchone()
    if not row or row[0]!='ready' or meta.get('content_origin')!='ai_original':raise UploadHold('original article unavailable')
    if load_approved(store,article)!=localized:raise UploadHold('localization changed')
    manifest=sha(folder/'snapshot-manifest.json');payload=digest(localized);vi=localized['locales']['Vietnamese'];plan=bind_plan(vi['title'],vi['blocks'],manifest,payload);_,policy_sha=body_policy.load_current()
    if not isinstance(assets,list) or len(assets)>5:raise UploadHold('invalid assets')
    prepared=[];seen=set();entries={x['ordinal']:x for x in plan['entries']}
    for asset in assets:
        try:
            if not isinstance(asset,dict) or set(asset)!={'ordinal','image_path','producer_request','producer_evidence'}:continue
            n=asset['ordinal'];entry=entries.get(n)
            if entry is None or n in seen:continue
            data=_asset(article['localization_revision'],asset['image_path']);verify_producer(entry,article['id'],manifest,payload,plan['plan_sha256'],policy_sha,data,asset['producer_request'],asset['producer_evidence'])
            prepared.append((asset,data));seen.add(n)
        except Exception:continue
    if not prepared:return {'session_id':None,'plan':plan,'uploads':[],'policy_sha256':policy_sha}
    preupload_journal.setup(store.db);session=preupload_journal.begin(store.db,article['id'],manifest,payload,plan['plan_sha256']);live={};saved=[];closed=False
    from playwright.sync_api import sync_playwright
    context=None
    with isolated_profile(root) as profile, (playwright_factory or sync_playwright)() as pw:
        try:
            context=pw.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',timezone_id='Asia/Ho_Chi_Minh',args=['--no-startup-window'])
            if not hasattr(context,'route_web_socket'):raise UploadHold('websocket blocking unavailable')
            context.route_web_socket('**/*',lambda ws:ws.close());pending={'allowed':False,'bytes':None}
            def guard(route):
                req=route.request;u=urlsplit(req.url)
                if req.method=='POST' and u.scheme=='https' and u.hostname=='api.nailhub.ai' and u.path=='/api/v1/Images/quick-upload' and not u.query and not u.fragment and pending['allowed'] and time.monotonic()<deadline and peer_alive():
                    pending['allowed']=False
                    try:
                        stub={'imageUrl':'https://vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com/probe.png','imageRelativePath':'probe.png','thumbnailUrl':'','thumbnailRelativePath':''}
                        bind_upload(req.post_data_buffer,req.headers.get('content-type',''),pending['bytes'],stub)
                    except Exception:route.abort('blockedbyclient');return
                    route.continue_()
                elif allowed_read(req.method,req.url):route.continue_()
                else:route.abort('blockedbyclient')
            context.route('**/*',guard);page=context.new_page();response=page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
            if not response or response.status!=200:raise UploadHold('admin unavailable')
            page.get_by_role('button',name='Create Blog',exact=True).click(timeout=30000);dialog=page.get_by_role('dialog')
            for asset,data in prepared:
                left=deadline-time.monotonic()
                if left<3 or not peer_alive():break
                n=asset['ordinal'];preupload_journal.started(store.db,session,n,__import__('hashlib').sha256(data).hexdigest())
                try:
                    suffix=Path(asset['image_path']).suffix;mime={'.png':'image/png','.jpg':'image/jpeg','.webp':'image/webp'}[suffix]
                    with page.expect_file_chooser(timeout=min(30000,int(left*1000))) as chooser:dialog.get_by_role('button',name='Upload Images',exact=True).click()
                    pending['bytes']=data;pending['allowed']=True
                    with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).hostname=='api.nailhub.ai' and urlsplit(r.url).path=='/api/v1/Images/quick-upload',timeout=min(30000,int(left*1000))) as response:
                        chooser.value.set_files({'name':'image'+suffix,'mimeType':mime,'buffer':data})
                    r=response.value
                    if not r.ok:raise UploadHold('upload unsuccessful')
                    req=r.request;body=req.post_data_buffer;ct=req.headers.get('content-type','');result=r.json()
                    bind_upload(body,ct,data,result)
                    proof=Path(asset['image_path']).parent/('request-'+session+'-'+str(n)+'.multipart')
                    _write_evidence(proof,body)
                    preupload_journal.bound(store.db,session,n,body,ct,data,result)
                    live[n]={'request_bytes':body,'content_type':ct,'source_bytes':data,'response':result}
                    saved.append(dict(asset,request_path=str(proof),content_type=ct,response=result))
                except Exception:break  # Close the context, mark unresolved image omitted.
            preupload_journal.close_context(store.db,session,context);closed=True
        finally:
            if context is not None and not closed:
                # A failed close is not proof of cancellation; the supervising
                # service must terminate this cgroup and drop all body images.
                try:context.close()
                except Exception:pass
    if not closed:raise UploadHold('context closure unproven')
    decisions=preupload_journal.completed(store.db,session,article['id'],manifest,payload,plan['plan_sha256'],live_evidence=live)
    valid={x['ordinal'] for x in decisions if x['status']=='bound'}
    return {'session_id':session,'plan':plan,'uploads':[x for x in saved if x['ordinal'] in valid],'policy_sha256':policy_sha}
