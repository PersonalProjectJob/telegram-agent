"""Full editor canary: observed official uploads; Create is always aborted.

This is intentionally not connected to Store.reserve or the live publisher.
It requires previously captured, trusted source/media policy evidence.
"""
from datetime import datetime
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from .lock import worker_lock
from .localization import strict_json,packed
from .official_create_observer import capture_aborted,verify_observed
from .official_editor_adapter import plan
from .official_editor_canary import allowed_read
from .official_native_guard import _live_inputs,readback
from .official_upload_canary import isolated_profile
from .official_upload_controller import UploadObserver,_sync_directory_chain
from .publisher import paste_html,content_fingerprint
from .official_editor_check import AnchorAudit
from .official_visual_evidence import inspect as inspect_visual
from .official_upload_evidence import CDN
from .official_upload_response_stream import UploadResponseStream


def observed_media_read(method,url,observers):
    """Preview only exact CDN URLs from completed same-process upload receipts."""
    if method not in ('GET','HEAD'):return False
    try:
        parsed=urlsplit(url)
        if (parsed.scheme!='https' or parsed.hostname!=CDN or parsed.port not in (None,443)
                or parsed.username or parsed.password or parsed.query or parsed.fragment):return False
    except (TypeError,ValueError):return False
    for observer in observers:
        if not isinstance(observer,UploadObserver) or observer.closed is not True:continue
        proof=observer.observation
        if (not isinstance(proof,dict) or proof.get('status')!='CANARY_ONLY_NOT_PUBLISHABLE'
                or proof.get('record_sha256')!=observer.record_sha):continue
        binding=proof.get('binding');receipt=binding.get('response') if isinstance(binding,dict) else None
        if isinstance(receipt,dict) and url in (receipt.get('imageUrl'),receipt.get('thumbnailUrl')):return True
    return False


def run(root,evidence_path,record_sha):
    return _run(root,evidence_path,record_sha,None)


def run_production(store,article,attempt):
    from .official_deadline import require
    require(750,active=True)
    from .official_publisher import available
    from .official_publish_policy import load as load_publish_policy
    from .store import local_day
    if available() is not True: raise ValueError('official publisher disabled')
    row=store.db.execute("SELECT o.evidence_path,o.record_sha,a.state,t.state AS attempt_state,t.reserved_day,s.day AS slot_day,s.slot FROM official_candidates o JOIN articles a ON a.id=o.article_id JOIN attempts t ON t.article_id=a.id JOIN official_attempt_slots s ON s.attempt_id=t.id WHERE t.id=? AND a.id=?",(attempt,article['id'])).fetchone()
    if not row or row['state']!='publishing' or row['attempt_state']!='intent' or row['reserved_day']!=local_day() or row['slot_day']!=row['reserved_day']:
        raise ValueError('official publishing intent unavailable')
    policy=load_publish_policy()
    if policy.get('enabled') is not True: raise ValueError('official publication policy disabled')
    return _run(store.root,row['evidence_path'],row['record_sha'],{'store':store,'attempt':attempt,'day':row['reserved_day'],'slot':row['slot'],'policy':policy})


def _run(root,evidence_path,record_sha,production):
    root=Path(root).resolve(); folder=Path(evidence_path)
    with worker_lock(root):
        payload,boundary,mapping,originals,policy=_live_inputs(root,folder,record_sha)
        record_bytes=(folder/'record.json').read_bytes()
        if hashlib.sha256(record_bytes).hexdigest()!=record_sha: raise ValueError('evidence changed')
        record=strict_json(record_bytes.decode('utf-8')); draft=(folder/'draft.json').read_bytes()
        if hashlib.sha256(draft).hexdigest()!=record['draft_sha256']: raise ValueError('draft changed')
        form=plan(draft,record['editorial'],record['media_manifest'],payload)
        data=strict_json(payload.decode('utf-8')); media=data['media']
        if production and data['source']['id'] not in production['policy'].get('source_ids',[]): raise ValueError('source not enabled for official publication')
        if production:
            from .official_prepost import check_publish_time
            check_publish_time(data['published_at'],production['day'],production['slot'],datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
        if not media or len(media)>10: raise ValueError('canary media unavailable')
        from playwright.sync_api import sync_playwright
        with isolated_profile(root) as profile, sync_playwright() as playwright:
            context=playwright.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',
                       timezone_id='Asia/Ho_Chi_Minh',permissions=['clipboard-read','clipboard-write'],
                       args=['--no-startup-window','--disable-background-networking'])
            try:
                if not hasattr(context,'route_web_socket'): raise ValueError('websocket blocking unavailable')
                context.route_web_socket('**/*',lambda ws:ws.close())
                state={'upload':None,'cover':None,'gallery':[],'create':None,'error':None,'allowed':False,'native_request':None}
                def guard(route):
                    req=route.request; url=urlsplit(req.url)
                    if req.method=='POST' and url.hostname=='api.nailhub.ai' and url.path=='/api/v1/Images/quick-upload':
                        if state['upload'] is None: route.abort('blockedbyclient'); return
                        try: state['upload'].route(route)
                        except Exception as error: state['error']=type(error).__name__
                    elif req.method=='POST' and url.hostname=='api.nailhub.ai' and url.path=='/api/v1/admin/blogs':
                        if production is None:
                            try: state['create']=capture_aborted(route,root,folder,record_sha,state['cover'],state['gallery'])
                            except Exception as error: state['error']=type(error).__name__
                        else:
                            try:
                                from .official_publisher import available
                                from .official_publish_policy import load as load_publish_policy
                                from .store import local_day
                                raw=req.post_data_buffer
                                if available() is not True or load_publish_policy()!=production['policy'] or production['store'].paused():
                                    raise ValueError('official publication disabled')
                                row=production['store'].db.execute("SELECT state,reserved_day FROM attempts WHERE id=?",(production['attempt'],)).fetchone()
                                if not row or row['state']!='intent' or row['reserved_day']!=production['day'] or local_day()!=production['day']:
                                    raise ValueError('official intent changed')
                                state['create']=verify_observed(raw,req.method,url,root,folder,record_sha,state['cover'],state['gallery'])
                                observed=[inspect_visual(root,folder,record_sha,item) for item in [state['cover'],*state['gallery']]]
                                if any(v.get('status')!='VISUAL_QA_NOT_PUBLISHABLE' or v.get('publication_enabled') is not False for v in observed):
                                    raise ValueError('official visual evidence unavailable')
                                if load_publish_policy()!=production['policy'] or production['store'].paused() or local_day()!=production['day']:
                                    raise ValueError('official policy or day changed')
                                for observer,visual in zip([state['cover'],*state['gallery']],observed):
                                    receipt=observer.observation['binding']['response']
                                    if hashlib.sha256(readback(receipt['imageUrl'])).hexdigest()!=visual['output_sha256']:
                                        raise ValueError('official CDN output changed')
                                    if visual.get('thumbnail') and hashlib.sha256(readback(receipt['thumbnailUrl'])).hexdigest()!=visual['thumbnail']['sha256']:
                                        raise ValueError('official CDN thumbnail changed')
                                row=production['store'].db.execute("SELECT state,reserved_day FROM attempts WHERE id=?",(production['attempt'],)).fetchone()
                                if (not row or row['state']!='intent' or row['reserved_day']!=production['day'] or local_day()!=production['day']
                                        or production['store'].paused() or load_publish_policy()!=production['policy']):
                                    raise ValueError('official intent or policy changed before POST')
                                from .official_prepost import check_publish_time
                                check_publish_time(data['published_at'],production['day'],production['slot'],datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
                                from .official_deadline import require
                                require(1,active=True)
                                state['native_request']=strict_json(raw.decode('utf-8'));state['allowed']=True;route.continue_()
                            except Exception as error:
                                state['error']=type(error).__name__;route.abort('blockedbyclient')
                    elif (allowed_read(req.method,req.url) or observed_media_read(req.method,req.url,
                          [state['cover'],*state['gallery'],state['upload']])): route.continue_()
                    else: route.abort('blockedbyclient')
                context.route('**/*',guard)
                if any(page.url not in ('','about:blank') for page in context.pages): raise ValueError('unexpected startup page')
                if production:
                    from .official_deadline import require
                    require(120,active=True)
                page=context.new_page()
                bounded=UploadResponseStream(context,page,folder/'bounded-upload-responses')
                response=page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
                if not response or response.status!=200 or page.url!='https://nailhub.ai/admin/blogs': raise ValueError('admin canary unavailable')
                if production:
                    from .official_duplicate_scan import check
                    check(page,data['source']['url'])
                    from .publisher import search_response
                    search=page.get_by_placeholder('Search by title, slug...')
                    for title in dict.fromkeys([data['locales']['vi']['title'],data['locales']['en']['title']]):
                        with page.expect_response(lambda r:search_response(r,title),timeout=30000) as existing:search.fill(title)
                        listing=strict_json(existing.value.body().decode('utf-8'))
                        if not isinstance(listing,dict) or not isinstance(listing.get('items'),list) or listing['items']:
                            raise ValueError('possible existing official article')
                    search.fill('')
                page.get_by_role('button',name='Create Blog',exact=True).click(timeout=30000); dialog=page.get_by_role('dialog')
                dialog.get_by_role('combobox').nth(0).click(); page.get_by_role('option',name=form['category'],exact=True).click()
                dialog.locator('#externalSourceUrl').fill(form['source_url'])
                def upload(descriptor,role,button):
                    if production:
                        from .official_deadline import require
                        require(300,active=True)
                    observer=UploadObserver(root,folder,record_sha); observer.arm(descriptor['id'],role)
                    raw=originals[descriptor['id']]
                    if raw.startswith(b'\xff\xd8\xff'): name,mime='official.jpg','image/jpeg'
                    elif raw.startswith(b'\x89PNG\r\n\x1a\n'): name,mime='official.png','image/png'
                    else: raise ValueError('unsupported canary media')
                    state['upload']=observer
                    from .ui_image_upload import upload_with_chooser
                    received=upload_with_chooser(page,dialog,button,{'name':name,'mimeType':mime,'buffer':raw},lambda r:observer.pending is not None and r.request is observer.pending[0],timeout=900000)
                    observer.response(received,bounded_reader=bounded);state['upload']=None
                    return observer
                state['cover']=upload(media[0],'cover','Upload Cover Image')
                for descriptor in media: state['gallery'].append(upload(descriptor,'gallery','Upload Images'))
                for language,code in (('Vietnamese','vi'),('English','en')):
                    fields=form['locales'][code];dialog.get_by_role('tab',name=language,exact=True).click()
                    for placeholder,key in ((f'Enter title in {language} (5-200 characters)','title'),
                                            (f'Enter summary in {language} (max 500 characters)','summary'),
                                            ('SEO title (50-70 characters recommended)','meta_title'),
                                            ('SEO description (120-160 characters recommended)','meta_description')):
                        field=dialog.get_by_placeholder(placeholder,exact=True);field.fill(fields[key])
                        if field.input_value()!=fields[key]: raise ValueError('canary field mismatch')
                    editor=dialog.locator('.ql-editor[contenteditable=true]');paste_html(page,editor,fields['body_html'])
                    observed=editor.inner_html();expected=fields['body_html']
                    if content_fingerprint(observed)!=content_fingerprint(expected) or AnchorAudit(observed).links!=AnchorAudit(expected).links:
                        raise ValueError('canary editor content mismatch')
                for tag in form['hashtags']:
                    field=dialog.get_by_placeholder('Add hashtag (2-50 characters each)',exact=True);field.fill(tag)
                    field.locator('..').get_by_role('button',name='Add',exact=True).click()
                    if field.input_value(): raise ValueError('canary hashtag mismatch')
                dialog.get_by_role('combobox').nth(1).click();page.get_by_role('option',name=data['status'],exact=True).click()
                public=dialog.get_by_role('checkbox').nth(0); featured=dialog.get_by_role('checkbox').nth(1)
                public.set_checked(data['is_public']);featured.set_checked(data['is_featured'])
                when=datetime.fromisoformat(data['published_at'].replace('Z','+00:00'))
                if when.tzinfo is None: raise ValueError('canary time missing timezone')
                dialog.locator('#publishedAt').fill(when.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).strftime('%Y-%m-%dT%H:%M'))
                observers=[state['cover'],*state['gallery']]
                visuals=[inspect_visual(root,folder,record_sha,observer) for observer in observers]
                if any(value.get('status')!='VISUAL_QA_NOT_PUBLISHABLE' or value.get('publication_enabled') is not False for value in visuals):
                    raise ValueError('visual evidence unavailable')
                if production:
                    with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).hostname=='api.nailhub.ai' and urlsplit(r.url).path=='/api/v1/admin/blogs',timeout=900000) as created:
                        dialog.get_by_role('button',name='Create',exact=True).click()
                    if not state['allowed'] or state['error'] or not created.value.ok: raise ValueError('official create outcome requires reconciliation')
                    from .official_publisher import verify_readback
                    return verify_readback(page,playwright,payload,state['native_request'],mapping,production['day'])
                with page.expect_request(lambda r:r.method=='POST' and urlsplit(r.url).hostname=='api.nailhub.ai' and urlsplit(r.url).path=='/api/v1/admin/blogs',timeout=900000):
                    dialog.get_by_role('button',name='Create',exact=True).click()
                # expect_request can resolve before the route callback has
                # aborted and finished evidence validation. Pump events first.
                for _ in range(50):
                    if state['error'] or state['create']: break
                    page.wait_for_timeout(100)
                if state['error'] or not state['create']: raise ValueError('aborted create verification incomplete: '+str(state['error'] or 'no capture'))
                after=[inspect_visual(root,folder,record_sha,observer) for observer in observers]
                for before,current in zip(visuals,after):
                    if any(before.get(key)!=current.get(key) for key in ('status','record_sha256','media_id','source_sha256','output_sha256','comparison_policy_sha256','thumbnail','publication_enabled')):
                        raise ValueError('visual evidence changed after aborted create')
                bundle={'version':'official-canary-visual-v1','status':'CANARY_ONLY_NOT_PUBLISHABLE',
                        'create_observation_sha256':state['create']['observation_sha256'],'visuals':after}
                encoded=(packed(bundle)+'\n').encode('utf-8');sha=hashlib.sha256(encoded).hexdigest()
                directory=folder/'create-observations'
                if directory.is_symlink() or not directory.is_dir(): raise ValueError('unsafe canary evidence directory')
                path=directory/(sha+'.json')
                try: fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                except FileExistsError:
                    if path.is_symlink() or path.read_bytes()!=encoded: raise ValueError('canary evidence changed')
                else:
                    with os.fdopen(fd,'wb') as stream: stream.write(encoded);stream.flush();os.fsync(stream.fileno())
                    _sync_directory_chain(directory,root)
                return {**state['create'],'visual_bundle_sha256':sha,'visual_bundle_path':str(path)}
            finally:
                context.close()


if __name__=='__main__':
    import argparse,json,sys
    parser=argparse.ArgumentParser(description='Official upload/Create-abort canary; never publishes')
    parser.add_argument('--root',required=True);parser.add_argument('--evidence-path',required=True);parser.add_argument('--record-sha',required=True)
    args=parser.parse_args()
    try: print(json.dumps(run(args.root,args.evidence_path,args.record_sha),ensure_ascii=False))
    except Exception as error:
        print('canary held: '+type(error).__name__,file=sys.stderr);raise SystemExit(1)
