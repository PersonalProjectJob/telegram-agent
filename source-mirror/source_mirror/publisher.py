"""Nailhub UI preparation; final Create remains disabled pending readback verification."""
import html, json, re
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlsplit, parse_qs
from .store import ZONE
from pathlib import Path
from .store import verify_snapshot

SEMANTIC_INLINE=r"""root => {
 const result=[];
 function walk(n,bold,italic,href) {
   if(n.nodeType===3) { for(const char of n.textContent) if(!/\s/.test(char)) result.push([char,bold,italic,href]); return; }
   if(n.nodeType!==1 || n.tagName==='IMG') return;
   const b=bold || n.tagName==='STRONG', i=italic || n.tagName==='EM', h=n.tagName==='A' ? n.href : href;
   for(const child of n.childNodes) walk(child,b,i,h);
 }
 walk(root,false,false,null); return result;
}"""

class VerificationIncomplete(RuntimeError): pass
class BodyMediaChanged(VerificationIncomplete): pass

def body_retry_allowed(body_blocked,phase):
    return body_blocked is True and phase=='pre_create'

def public_source_footer(source,label,language,is_original):
    """Build only reader-facing source text; provenance remains in the snapshot."""
    prefix='Nguồn tham khảo' if is_original and language=='Vietnamese' else ('Reference' if is_original else 'Source')
    return f'<p>{prefix}: <a href="{html.escape(source,quote=True)}">{html.escape(label,quote=False)}</a></p>'

def search_response(response,title):
    url=urlsplit(response.url); query=parse_qs(url.query,keep_blank_values=True)
    return (response.request.method=='GET' and response.ok and url.scheme=='https' and url.hostname=='api.nailhub.ai'
            and url.path=='/api/v1/admin/blogs' and query.get('searchTerm')==[title] and query.get('pageNumber')==['1'])

class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True); self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('p','h1','h2','h3','h4','li','br'): self.parts.append(' ')
    def handle_endtag(self,tag):
        if tag in ('p','h1','h2','h3','h4','li'): self.parts.append(' ')
    def handle_data(self,data): self.parts.append(data)

def source_summary(blocks,limit=180):
    """A short plain-text excerpt from the first substantive source text block."""
    for block in blocks:
        if block.get('type')!='html': continue
        parser=_TextOnly(); parser.feed(block['html']); text=' '.join(''.join(parser.parts).split())
        if not text or re.fullmatch(r'Step\s+\d+',text,flags=re.IGNORECASE): continue
        if len(text)<=limit: return text
        cut=text[:limit-1].rsplit(' ',1)[0].rstrip()
        if not cut: cut=text[:limit-1]
        return cut+'…'
    raise VerificationIncomplete('no source text available for summary')

def preflight(config):
    if config.get('enable_verified_canary') is not True:
        raise VerificationIncomplete('production canary disabled; live editor and public readback must be verified')
    from .localization import config_check
    config_check(config)

def wait_loaded_cover(guest_page,cover,timeout=30000):
    guest_page.wait_for_function('(i) => i.complete && i.naturalWidth > 0',arg=cover.element_handle(),timeout=timeout)

def wait_loaded_images(page, root, expected_count, timeout=30000):
    page.wait_for_function('''({root,count}) => { const imgs=[...root.querySelectorAll('img')];
      return imgs.length===count && imgs.every(i => i.complete && i.naturalWidth>0); }''',
      arg={'root':root.element_handle(),'count':expected_count},timeout=timeout)

def paste_html(page, editor, value):
    # Clipboard is a browser user-input mechanism; no application state is injected.
    page.evaluate('''async html => {
      const plain = new DOMParser().parseFromString(html, 'text/html').body.textContent;
      await navigator.clipboard.write([new ClipboardItem({'text/html':new Blob([html],{type:'text/html'}), 'text/plain':new Blob([plain],{type:'text/plain'})})]);
    }''',value)
    editor.click(); page.keyboard.press('Control+End'); page.keyboard.press('Control+V')

def media_layout(folder, meta, media, cover):
    """Cover is independent of ordered body media for new snapshots."""
    all_paths=[str(folder/item['path']) for item in media]
    offset=1 if meta.get('source_cover_separate') is True and cover['kind']=='source' else 0
    if cover['kind']=='source' and not all_paths: raise VerificationIncomplete('source cover missing')
    cover_path=cover['path'] if cover['kind']=='generated' else all_paths[0]
    return cover_path,all_paths[offset:],offset

def make_native_create_guard(localized, contents, source_url, chosen, cover_receipt, gallery_receipts, revalidate, audit, authorize=None, deadline=None, on_body_block=None):
    """Guard the actual native POST; revalidate supplies current approved hashes."""
    from .scratch_native_guard import verify_media
    def guard(route):
        req=route.request; parsed=urlsplit(req.url)
        if req.method!='POST' or parsed.path!='/api/v1/admin/blogs':
            route.continue_(); return
        receipt={'allowed':False}
        try:
            if parsed.scheme!='https' or parsed.hostname!='api.nailhub.ai' or parsed.port not in (None,443) or parsed.username or parsed.password or parsed.query or parsed.fragment: raise VerificationIncomplete('native endpoint differs')
            sent=req.post_data_json
            verify_outgoing_locales(sent,localized,contents)
            if sent.get('externalSourceUrl')!=source_url or sent.get('isPublic') is not True or sent.get('status')!='Published': raise VerificationIncomplete('native source/status differs')
            sent_time=datetime.fromisoformat(sent['publishedAt'].replace('Z','+00:00'))
            if sent_time.tzinfo is None or sent_time.astimezone(timezone.utc)!=chosen.astimezone(timezone.utc): raise VerificationIncomplete('native timestamp differs')
            cover_sha,gallery_sha=revalidate()
            verify_media(sent,cover_receipt,gallery_receipts,cover_sha,gallery_sha)
            if authorize is None: raise VerificationIncomplete('create authorization unavailable')
            authorize()
            receipt['allowed']=True
            audit(receipt)
            if deadline is not None: deadline()
            route.continue_()
        except Exception as exc:
            receipt['allowed']=False
            receipt['reason']=str(exc) if isinstance(exc,VerificationIncomplete) else type(exc).__name__
            try:audit(receipt)
            except OSError:pass
            route.abort('blockedbyclient')
            if isinstance(exc,BodyMediaChanged) and on_body_block is not None:on_body_block()
    return guard

class UploadFailure(Exception):
    pass

def prepare(config, store, article, attempt):
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    folder=verify_snapshot(store.root,article['id'])
    meta=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
    blocks=json.loads((folder/'content.json').read_text(encoding='utf-8'))
    media=json.loads((folder/'media-manifest.json').read_text(encoding='utf-8'))
    source_blocks=blocks
    is_original=meta.get('content_origin')=='ai_original'
    source_label='Scratch Magazine'
    if is_original:
        from .original_business import verify_original
        source_label=verify_original(meta,source_blocks)['label']
    elif meta.get('content_origin') is not None:raise VerificationIncomplete('unsupported content origin')
    from .localization import load_approved
    localized=load_approved(store,article)
    locales=localized['locales']
    from .scratch_cover import validate_cover
    cover=article.get('cover_candidate',{'kind':'source'})
    validate_cover(store,article,localized,cover,config=config,require_production=(cover['kind']=='generated'))
    if len(media)>10 or not 5<=len(meta['title'])<=200: raise ValueError('unsupported title or gallery size')
    summary=locales['Vietnamese']['summary']
    cover_path,paths,offset=media_layout(folder,meta,media,cover)
    with sync_playwright() as playwright:
        profile=store.root/'browser'/'nailhub-profile'; profile.mkdir(parents=True,exist_ok=True)
        context=playwright.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',timezone_id='Asia/Ho_Chi_Minh',permissions=['clipboard-read','clipboard-write'])
        try:
            def deny_early_create(route):
                req=route.request; path=urlsplit(req.url).path.rstrip('/')
                if req.method=='POST' and path=='/api/v1/admin/blogs': route.abort('blockedbyclient')
                else: route.continue_()
            context.route('**/*',deny_early_create)
            page=context.new_page(); page.goto('https://nailhub.ai/admin/blogs',wait_until='domcontentloaded',timeout=30000)
            try:
                page.get_by_role('button',name='Create Blog').wait_for(state='visible',timeout=30000)
            except PlaywrightTimeoutError:
                raise VerificationIncomplete('admin page unavailable or dedicated profile requires authentication')
            # Compare exact titles against the complete Published list.
            from .original_duplicate_scan import check_titles
            check_titles(page,[locales['Vietnamese']['title'],locales['English']['title']])
            page.get_by_role('button',name='Create Blog').click()
            dialog=page.get_by_role('dialog'); dialog.get_by_role('combobox').nth(0).click()
            page.get_by_role('option',name='Hot Trend',exact=True).click()
            dialog.locator('#externalSourceUrl').fill(meta['source_url'])
            source=html.escape(meta['source_url'],quote=True)
            # Upload through native controls one file at a time and bind each response to its chooser.
            def upload_one(button,path,ordinal):
                from .store import sha
                upload_id=store.begin_upload(attempt,ordinal,sha(Path(path)))
                try:
                    with page.expect_file_chooser() as chooser: dialog.get_by_role('button',name=button,exact=True).click()
                    with page.expect_response(lambda r:r.request.method=='POST' and urlsplit(r.url).scheme=='https' and urlsplit(r.url).hostname=='api.nailhub.ai' and urlsplit(r.url).path=='/api/v1/Images/quick-upload' and r.ok,timeout=30000) as response:
                        chooser.value.set_files(path)
                    from .scratch_native_guard import bind_upload
                    upload_response=response.value
                    payload=upload_response.json()
                    binding=bind_upload(upload_response.request.post_data_buffer,upload_response.request.headers.get('content-type',''),Path(path).read_bytes(),payload)
                    store.bind_upload(attempt,upload_id,binding)
                    return payload['imageUrl'],binding
                except Exception as exc:
                    raise UploadFailure('native upload failed: '+type(exc).__name__) from exc
            def upload_form_media(start_ordinal):
                cover_url,cover_receipt=upload_one('Upload Cover Image',cover_path,start_ordinal)
                try:page.wait_for_function("url => [...document.querySelectorAll('[role=dialog] img')].some(i => i.src === url && i.complete && i.naturalWidth > 0)",arg=cover_url,timeout=30000)
                except Exception as exc:raise UploadFailure('cover upload did not settle: '+type(exc).__name__) from exc
                urls=[];gallery_receipts=[]
                for index,path in enumerate(paths,1):
                    url,binding=upload_one('Upload Images',path,start_ordinal+index);urls.append(url);gallery_receipts.append(binding)
                    try:dialog.get_by_text(f'Images ({index}/10)',exact=True).wait_for(state='visible',timeout=30000)
                    except Exception as exc:raise UploadFailure('gallery upload did not settle: '+type(exc).__name__) from exc
                if len(set(urls))!=len(paths):raise VerificationIncomplete('duplicate upload URLs')
                return cover_url,cover_receipt,urls,gallery_receipts
            def reopen_form():
                dialog.get_by_role('button',name='Close',exact=True).click()
                dialog.wait_for(state='hidden',timeout=30000)
                page.get_by_role('button',name='Create Blog',exact=True).click()
                fresh=page.get_by_role('dialog');fresh.get_by_role('combobox').nth(0).click()
                page.get_by_role('option',name='Hot Trend',exact=True).click()
                fresh.locator('#externalSourceUrl').fill(meta['source_url'])
                return fresh
            def next_upload_ordinal():
                return store.db.execute('SELECT COALESCE(MAX(ordinal),-1)+1 FROM upload_journal WHERE attempt_id=?',(attempt,)).fetchone()[0]
            cover_url,cover_receipt,urls,gallery_receipts=upload_form_media(0)
            def body_only_unloaded(editor,body_urls):
                if not body_urls:return False
                states=editor.locator('img').evaluate_all('(imgs) => imgs.map(i => ({src:i.src,ok:i.complete && i.naturalWidth>0}))')
                def loaded(url):return any(item['src']==url and item['ok'] for item in states)
                return all(loaded(url) for url in urls) and any(not loaded(url) for url in body_urls)
            def fill_locales(rendered_blocks,body_urls):
                locale_checks={}
                for language in ('Vietnamese','English'):
                    fields=locales[language]
                    blocks=rendered_blocks[language]
                    parts=[]
                    for block in blocks:
                        if block['type']=='html':
                            fragment=block['html']
                            if page.evaluate("value => !!new DOMParser().parseFromString(value,'text/html').body.querySelector('b,i')",fragment): raise VerificationIncomplete('unsupported legacy emphasis tags')
                            has_block=page.evaluate("value => !!new DOMParser().parseFromString(value,'text/html').body.querySelector('p,h1,h2,h3,h4,ul,ol')",fragment)
                            parts.append(fragment if has_block else '<p>'+fragment+'</p>')
                        elif block['type']=='image':
                            idx=block['media']
                            if type(idx) is not int or not 0<=idx-offset<len(urls): raise ValueError('invalid media reference')
                            parts.append('<p><img src="'+html.escape(urls[idx-offset],quote=True)+'"></p>')
                        else: raise ValueError('unsupported snapshot block')
                    parts.append(public_source_footer(meta['source_url'],source_label,language,is_original))
                    content=''.join(parts)
                    expected_urls=page.evaluate("markup => [...new DOMParser().parseFromString(markup,'text/html').body.querySelectorAll('img')].map(i => i.src)",content)
                    if Counter(expected_urls)!=Counter(urls+body_urls):raise VerificationIncomplete('rendered image set differs')
                    dialog.get_by_role('tab',name=language,exact=True).click()
                    dialog.get_by_placeholder(f'Enter title in {language} (5-200 characters)').fill(fields['title'])
                    summary_field=dialog.get_by_placeholder(f'Enter summary in {language} (max 500 characters)')
                    summary_field.fill(fields['summary'])
                    if summary_field.input_value()!=fields['summary']: raise VerificationIncomplete('summary differs from approved locale')
                    for placeholder,key in [('SEO title (50-70 characters recommended)','meta_title'),('SEO description (120-160 characters recommended)','meta_description')]:
                        field=dialog.get_by_placeholder(placeholder,exact=True); field.fill(fields[key])
                        if field.input_value()!=fields[key]: raise VerificationIncomplete('meta field differs from approved locale')
                    editor=dialog.locator('.ql-editor[contenteditable=true]')
                    paste_html(page,editor,content)
                    try:
                        page.wait_for_function('''arg => { const root=document.querySelector('[role=dialog] .ql-editor[contenteditable=true]');
                          if(!root) return false; const imgs=[...root.querySelectorAll('img')];
                          return imgs.length===arg.urls.length && imgs.every((img,i)=>img.src===arg.urls[i] && img.complete && img.naturalWidth>0)
                            && root.textContent.includes(arg.label); }''',arg={'urls':expected_urls,'label':source_label},timeout=30000)
                    except PlaywrightTimeoutError:
                        if body_only_unloaded(editor,body_urls):raise BodyMediaChanged('body image failed to load')
                        evidence=store.root/'reports'/f'attempt-{attempt}'; evidence.mkdir(parents=True,exist_ok=True)
                        (evidence/f'editor-{language.lower()}.html').write_text(editor.inner_html(),encoding='utf-8')
                        page.screenshot(path=str(evidence/f'editor-{language.lower()}-timeout.png'),full_page=True)
                        raise VerificationIncomplete('editor paste or images did not settle')
                    editor_urls=editor.locator('img').evaluate_all('(imgs) => imgs.map(i => i.src)')
                    if editor_urls!=expected_urls: raise VerificationIncomplete('inline images differ or are out of order')
                    sequence=r"""root => { const out=[]; function walk(n){if(n.nodeType===3){out.push(n.textContent);return;} if(n.nodeType!==1)return; if(n.tagName==='IMG'){out.push(' [IMAGE:'+n.src+'] ');return;} if(['P','H1','H2','H3','H4','LI','BR'].includes(n.tagName))out.push(' '); for(const child of n.childNodes)walk(child); if(['P','H1','H2','H3','H4','LI'].includes(n.tagName))out.push(' ');} walk(root);return out.join('').replace(/\s+/g,' ').trim(); }"""
                    wanted=page.evaluate("markup => { const root=new DOMParser().parseFromString(markup,'text/html').body; return ("+sequence+")(root); }",content)
                    observed_sequence=editor.evaluate(sequence)
                    if observed_sequence!=wanted:
                        evidence=store.root/'reports'/f'attempt-{attempt}'; evidence.mkdir(parents=True,exist_ok=True)
                        (evidence/f'editor-{language.lower()}-sequence.json').write_text(json.dumps({'expected':wanted,'actual':observed_sequence},ensure_ascii=False,indent=2),encoding='utf-8')
                        (evidence/f'editor-{language.lower()}-sequence.html').write_text(editor.inner_html(),encoding='utf-8')
                        page.screenshot(path=str(evidence/f'editor-{language.lower()}-sequence.png'),full_page=True)
                        raise VerificationIncomplete('editor text and media sequence differs')
                    structure=r"""root => [...root.querySelectorAll('p,h1,h2,h3,h4,ul,ol,li')].map(n => ({tag:n.tagName,text:['UL','OL'].includes(n.tagName)?null:(n.textContent||'').replace(/\s+/g,' ').trim(),href:n.tagName==='A'?n.href:null}))"""
                    wanted_structure=page.evaluate("markup => { const root=new DOMParser().parseFromString(markup,'text/html').body; return ("+structure+")(root); }",content)
                    wanted_inline=page.evaluate("markup => { const root=new DOMParser().parseFromString(markup,'text/html').body; return ("+SEMANTIC_INLINE+")(root); }",content)
                    observed_inline=editor.evaluate(SEMANTIC_INLINE)
                    if observed_inline!=wanted_inline:
                        evidence=store.root/'reports'/f'attempt-{attempt}'; evidence.mkdir(parents=True,exist_ok=True)
                        (evidence/f'editor-{language.lower()}-inline.json').write_text(json.dumps({'expected':wanted_inline,'actual':observed_inline},ensure_ascii=False),encoding='utf-8')
                        (evidence/f'editor-{language.lower()}-inline.html').write_text(editor.inner_html(),encoding='utf-8')
                        page.screenshot(path=str(evidence/f'editor-{language.lower()}-inline.png'),full_page=True)
                        raise VerificationIncomplete('editor inline emphasis or link ranges differ')
                    observed_structure=editor.evaluate(structure)
                    if observed_structure!=wanted_structure:
                        evidence=store.root/'reports'/f'attempt-{attempt}'; evidence.mkdir(parents=True,exist_ok=True)
                        (evidence/f'editor-{language.lower()}-structure.json').write_text(json.dumps({'expected':wanted_structure,'actual':observed_structure},ensure_ascii=False,indent=2),encoding='utf-8')
                        (evidence/f'editor-{language.lower()}-structure.html').write_text(editor.inner_html(),encoding='utf-8')
                        page.screenshot(path=str(evidence/f'editor-{language.lower()}-structure.png'),full_page=True)
                        raise VerificationIncomplete('editor structure differs')
                    if not editor.locator('img').evaluate_all('(imgs) => imgs.every(i => i.complete && i.naturalWidth > 0)'):
                        if body_only_unloaded(editor,body_urls):raise BodyMediaChanged('body image failed to load')
                        raise VerificationIncomplete('editor image failed to load')
                    expected=[]
                    for block in blocks:
                        if block['type']=='html':
                            parser=_TextOnly();parser.feed(block['html']);expected.append(' '.join(''.join(parser.parts).split()))
                    actual=' '.join(editor.inner_text().split()); cursor=0
                    for text in expected:
                        pos=actual.find(text,cursor) if text else cursor
                        if pos<0: raise VerificationIncomplete('editor lost or reordered text')
                        cursor=pos+len(text)
                    locale_checks[language]={'wanted':wanted,'structure':wanted_structure,'inline':wanted_inline,'expected':expected,'urls':expected_urls}
                return locale_checks,sequence,structure
            from .body_render import render as render_body, omit_all as omit_body_images
            rendered_blocks,body_urls=render_body(store,article,localized)
            try:locale_checks,sequence,structure=fill_locales(rendered_blocks,body_urls)
            except BodyMediaChanged:
                omit_body_images(store,article,localized,'body_image_failed_to_load')
                dialog=reopen_form()
                cover_url,cover_receipt,urls,gallery_receipts=upload_form_media(next_upload_ordinal())
                rendered_blocks={'Vietnamese':locales['Vietnamese']['blocks'],'English':locales['English']['blocks']};body_urls=[]
                locale_checks,sequence,structure=fill_locales(rendered_blocks,body_urls)
            def fill_hashtags():
                tag_input=dialog.get_by_placeholder('Add hashtag (2-50 characters each)',exact=True)
                for tag in localized['hashtags']:
                    tag_input.fill(tag)
                    tag_input.locator('..').get_by_role('button',name='Add',exact=True).click()
                    if tag_input.input_value(): raise VerificationIncomplete('hashtag was not added')
            fill_hashtags()
            # Recheck the frozen revision immediately before the mutating submit.
            if load_approved(store,article)!=localized: raise VerificationIncomplete('approved localization changed during editing')
            def capture_locales(locale_checks,sequence,structure):
                outgoing_content={}
                for language in ('Vietnamese','English'):
                    fields=locales[language]; checks=locale_checks[language]
                    dialog.get_by_role('tab',name=language,exact=True).click()
                    if dialog.get_by_placeholder(f'Enter title in {language} (5-200 characters)').input_value()!=fields['title']: raise VerificationIncomplete('locale title changed before submit')
                    if dialog.get_by_placeholder(f'Enter summary in {language} (max 500 characters)').input_value()!=fields['summary']: raise VerificationIncomplete('locale summary changed before submit')
                    for placeholder,key in [('SEO title (50-70 characters recommended)','meta_title'),('SEO description (120-160 characters recommended)','meta_description')]:
                        if dialog.get_by_placeholder(placeholder,exact=True).input_value()!=fields[key]: raise VerificationIncomplete('locale meta changed before submit')
                    editor=dialog.locator('.ql-editor[contenteditable=true]')
                    if editor.evaluate(sequence)!=checks['wanted'] or editor.evaluate(SEMANTIC_INLINE)!=checks['inline'] or editor.evaluate(structure)!=checks['structure']: raise VerificationIncomplete('locale body changed before submit')
                    outgoing_content[language]=editor.inner_html()
                return outgoing_content
            outgoing_content=capture_locales(locale_checks,sequence,structure)
            validate_cover(store,article,localized,cover,config=config,require_production=(cover['kind']=='generated'))
            def revalidate_media():
                from .store import sha
                verify_snapshot(store.root,article['id'])
                if is_original:
                    from .original_business import verify_original
                    verify_original(meta,source_blocks,fresh=False)
                if load_approved(store,article)!=localized: raise VerificationIncomplete('approved revision changed at submit')
                validate_cover(store,article,localized,cover,config=config,require_production=(cover['kind']=='generated'))
                if body_urls:
                    current_blocks,current_urls=render_body(store,article,localized)
                    if current_blocks!=rendered_blocks or current_urls!=body_urls:raise BodyMediaChanged('body illustration no longer valid')
                return sha(Path(cover_path)),[sha(Path(path)) for path in paths]
            def audit_guard(result):
                (evidence/'native-submit-guard.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
            # Installation happens after the frozen timestamp is selected.

            # Explicitly observe user-facing form state before submitting.
            if dialog.get_by_role('combobox').nth(1).inner_text().strip()!='Published': raise VerificationIncomplete('status is not Published')
            if not dialog.get_by_role('checkbox').nth(0).is_checked(): raise VerificationIncomplete('public setting is not enabled')
            evidence=store.root/'reports'/f'attempt-{attempt}'; evidence.mkdir(parents=True,exist_ok=True)
            observed=[]
            def watch_read(response):
                if response.request.method=='GET' and '/api/v1/admin/blogs' in response.url and response.ok:
                    try: observed.append(response.json())
                    except Exception: pass
            page.on('response',watch_read)
            def require_original_time(minimum):
                if not is_original:return
                from .official_deadline import seconds_available
                data=store.db.execute('SELECT a.reserved_day,p.slot FROM attempts a JOIN attempt_phases p ON p.attempt_id=a.id WHERE a.id=?',(attempt,)).fetchone()
                if not data:raise VerificationIncomplete('original slot evidence unavailable')
                try:remaining=seconds_available(data['reserved_day'],data['slot'],datetime.now(ZONE))
                except ValueError as exc:raise VerificationIncomplete('original slot deadline unavailable') from exc
                if remaining<minimum:raise VerificationIncomplete('original slot deadline elapsed')
            if is_original:require_original_time(350)
            # Compare rendered outgoing bodies against every published article.
            from .original_duplicate_scan import check_bodies, audited as audit_original_duplicate
            duplicate_page=context.new_page()
            try:audit_original_duplicate(store.root,localized,'precreate',lambda:check_bodies(duplicate_page,{'VI':outgoing_content['Vietnamese'],'EN':outgoing_content['English']}))
            finally:duplicate_page.close()
            if is_original:
                verify_original(meta,source_blocks)
                require_original_time(30)
            # Reject a midnight-crossing attempt rather than guessing the publication day.
            from .store import local_day
            reserved=store.db.execute('SELECT reserved_day FROM attempts WHERE id=?',(attempt,)).fetchone()[0]
            if local_day()!=reserved: raise VerificationIncomplete('reservation crossed Vietnam day boundary')
            chosen=datetime.now(ZONE).replace(second=0,microsecond=0)
            dialog.locator('#publishedAt').fill(chosen.strftime('%Y-%m-%dT%H:%M'))
            def check_scheduled_deadline():
                if article.get('_scheduled_guard') is not None:
                    from .scheduled_assignments import require_deadline
                    require_deadline(*article['_scheduled_guard'])
            def authorize_create():
                require_original_time(0)
                check_scheduled_deadline()
                store.begin_create(attempt)
            body_blocked=[False]
            def on_body_block():body_blocked[0]=True
            guard_create=make_native_create_guard(localized,outgoing_content,meta['source_url'],chosen,cover_receipt,gallery_receipts,revalidate_media,audit_guard,authorize_create,check_scheduled_deadline,on_body_block)
            page.route('**/api/v1/admin/blogs',guard_create)
            page.screenshot(path=str(evidence/'before-create.png'),full_page=True)
            # Observe the actual native request; the first body-only failure was
            # aborted by the route before begin_create and is safe to rebuild.
            try:
                with page.expect_response(lambda r:r.request.method=='POST' and '/api/v1/admin/blogs' in r.url,timeout=30000) as create_response:
                    dialog.get_by_role('button',name='Create',exact=True).click()
            except Exception:
                phase=store.db.execute('SELECT phase FROM attempt_phases WHERE attempt_id=?',(attempt,)).fetchone()
                if not body_retry_allowed(body_blocked[0],phase['phase'] if phase else None):raise
                page.unroute('**/api/v1/admin/blogs',guard_create)
                omit_body_images(store,article,localized,'body_changed_before_create')
                dialog=reopen_form()
                cover_url,cover_receipt,urls,gallery_receipts=upload_form_media(next_upload_ordinal())
                rendered_blocks={'Vietnamese':locales['Vietnamese']['blocks'],'English':locales['English']['blocks']};body_urls=[]
                locale_checks,sequence,structure=fill_locales(rendered_blocks,body_urls)
                fill_hashtags()
                if dialog.get_by_role('combobox').nth(1).inner_text().strip()!='Published' or not dialog.get_by_role('checkbox').nth(0).is_checked():raise VerificationIncomplete('fresh form status or visibility differs')
                dialog.locator('#publishedAt').fill(chosen.strftime('%Y-%m-%dT%H:%M'))
                outgoing_content=capture_locales(locale_checks,sequence,structure)
                guard_create=make_native_create_guard(localized,outgoing_content,meta['source_url'],chosen,cover_receipt,gallery_receipts,revalidate_media,audit_guard,authorize_create,check_scheduled_deadline)
                page.route('**/api/v1/admin/blogs',guard_create)
                with page.expect_response(lambda r:r.request.method=='POST' and '/api/v1/admin/blogs' in r.url,timeout=30000) as create_response:
                    dialog.get_by_role('button',name='Create',exact=True).click()
            request_payload=create_response.value.request.post_data_json
            sent=request_payload.get('publishedAt') if isinstance(request_payload,dict) else None
            try:
                sent_time=datetime.fromisoformat(sent.replace('Z','+00:00'))
                if sent_time.tzinfo is None or sent_time.astimezone(timezone.utc)!=chosen.astimezone(timezone.utc): raise ValueError()
            except (AttributeError,TypeError,ValueError): raise VerificationIncomplete('UI publish timestamp lacks verified offset')
            page.wait_for_timeout(1500)
            if dialog.is_visible(): raise VerificationIncomplete('create outcome requires reconciliation')
            search=page.get_by_placeholder('Search by title, slug...')
            with page.expect_response(lambda r:search_response(r,locales['Vietnamese']['title']),timeout=15000):
                search.fill(locales['Vietnamese']['title'])
            page.wait_for_timeout(500)
            rows=page.locator('tbody tr')
            if rows.count()!=1 or 'Published' not in rows.first.inner_text(): raise VerificationIncomplete('cannot uniquely confirm published admin row')
            rows.first.locator('button[title="View"]').click()
            details=page.get_by_role('dialog')
            for language,tab in [('Vietnamese','VI'),('English','EN')]:
                details.get_by_role('tab',name=tab,exact=True).click()
                prose=admin_content(details); checks=locale_checks[language]
                if prose.evaluate(sequence)!=checks['wanted']: raise VerificationIncomplete('admin locale text/media sequence differs')
                if prose.evaluate(structure)!=checks['structure']: raise VerificationIncomplete('admin locale structure differs')
                if prose.evaluate(SEMANTIC_INLINE)!=checks['inline']: raise VerificationIncomplete('admin locale emphasis/links differ')
                try:
                    wait_loaded_images(page,prose,len(checks['urls']))
                except PlaywrightTimeoutError:
                    raise VerificationIncomplete('admin locale image failed to load')
                if prose.locator('img').evaluate_all('(imgs) => imgs.map(i => i.src)')!=checks['urls']: raise VerificationIncomplete('admin locale media mismatch')
                page.screenshot(path=str(evidence/f'admin-{language.lower()}.png'),full_page=True)
            page.screenshot(path=str(evidence/'admin-details.png'),full_page=True)
            records=[]
            for payload in observed:
                if not isinstance(payload,dict) or not isinstance(payload.get('items'),list): continue
                for record in payload['items']:
                    if not isinstance(record,dict) or record.get('externalSourceUrl')!=meta['source_url']: continue
                    titles={t.get('languageCode'):t.get('title') for t in record.get('translations',[]) if isinstance(t,dict)}
                    summaries={t.get('languageCode'):t.get('summary') for t in record.get('translations',[]) if isinstance(t,dict)}
                    if titles.get('En')!=locales['English']['title'] or titles.get('Vi')!=locales['Vietnamese']['title']: continue
                    if summaries.get('En')!=locales['English']['summary'] or summaries.get('Vi')!=locales['Vietnamese']['summary']: continue
                    translations={t.get('languageCode'):t for t in record.get('translations',[]) if isinstance(t,dict)}
                    if any(translations.get(code,{}).get('metaTitle')!=locales[lang]['meta_title'] or translations.get(code,{}).get('metaDescription')!=locales[lang]['meta_description'] for code,lang in [('Vi','Vietnamese'),('En','English')]): continue
                    try:
                        if Counter(canonical_hashtags(record.get('hashtags'),allow_prefix=False))!=Counter(canonical_hashtags(localized['hashtags'])): continue
                    except VerificationIncomplete: continue
                    if record.get('status')!='Published' or record.get('isPublic') is not True: continue
                    if not record.get('id') or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',record.get('slug') or ''): continue
                    records.append(record)
            unique={r['id']:r for r in records}
            if len(unique)!=1: raise VerificationIncomplete('published record could not be uniquely bound')
            record=next(iter(unique.values()))
            try:
                published=datetime.fromisoformat(record['publishedAt'].replace('Z','+00:00'))
                expected_utc=chosen.astimezone(timezone.utc)
                if published.tzinfo is None:
                    if published!=expected_utc.replace(tzinfo=None): raise ValueError()
                elif published.astimezone(timezone.utc)!=expected_utc: raise ValueError()
                if chosen.date().isoformat()!=reserved: raise ValueError()
            except (KeyError,TypeError,ValueError): raise VerificationIncomplete('publication date is unavailable or differs')
            public_url='https://nailhub.ai/blog/'+record['slug']
            checks=locale_checks['Vietnamese']
            wanted=checks['wanted']; wanted_structure=checks['structure']; wanted_inline=checks['inline']; expected=checks['expected']
            # A separate context proves guest access. Do not infer success from the toast.
            guest=playwright.chromium.launch(headless=True); guest_context=guest.new_context()
            try:
                public=guest_context.new_page(); response=public.goto(public_url,wait_until='domcontentloaded',timeout=30000)
                if not response or response.status!=200: raise VerificationIncomplete('guest article unavailable')
                public.locator('h1').filter(has_text=locales['Vietnamese']['title']).wait_for(state='visible',timeout=30000)
                body=guest_content(public)
                public.wait_for_function('''() => [...document.querySelectorAll(".blog-article-content.ql-editor img")].every(i => i.complete)''',timeout=30000)
                text=' '.join(public.locator('body').inner_text().split()); cursor=0
                for item in expected:
                    pos=text.find(item,cursor) if item else cursor
                    if pos<0: raise VerificationIncomplete('guest text differs')
                    cursor=pos+len(item)
                if body.count()!=1: raise VerificationIncomplete('public article body not uniquely identified')
                if body.evaluate(sequence)!=wanted: raise VerificationIncomplete('public text and media sequence differs')
                if body.evaluate(structure)!=wanted_structure: raise VerificationIncomplete('public structure differs')
                if body.evaluate(SEMANTIC_INLINE)!=wanted_inline: raise VerificationIncomplete('public inline emphasis or link ranges differ')
                if not body.locator('img').evaluate_all('(imgs) => imgs.every(i => i.complete && i.naturalWidth > 0 && i.getClientRects().length > 0)'): raise VerificationIncomplete('public media is hidden or failed to load')
                public_images=body.locator('img').evaluate_all('(imgs) => imgs.map(i => i.src)')
                positions=[public_images.index(url) if url in public_images else -1 for url in checks['urls']]
                if -1 in positions or positions!=sorted(positions): raise VerificationIncomplete('guest media missing or out of order')
                public.screenshot(path=str(evidence/'guest.png'),full_page=True)
                # The home page has two observed card variants; neither may render HTML in its excerpt.
                public.goto('https://nailhub.ai/',wait_until='domcontentloaded',timeout=30000)
                small=public.locator('h3.font-semibold.text-sm.mb-2.leading-snug').filter(has_text=locales['Vietnamese']['title'])
                large=public.locator('h4.font-bold.text-lg.leading-snug.line-clamp-2').filter(has_text=locales['Vietnamese']['title'])
                small.wait_for(state='attached',timeout=30000); large.wait_for(state='attached',timeout=30000)
                if small.count()!=1 or large.count()!=1: raise VerificationIncomplete('home cards not uniquely identified')
                if not (small.is_visible() or large.is_visible()): raise VerificationIncomplete('no home card variant visible')
                for heading in (small,large):
                    excerpt=heading.locator('xpath=..').locator('p.line-clamp-3')
                    if excerpt.count()!=1 or ' '.join(excerpt.text_content().split())!=' '.join(summary.split()): raise VerificationIncomplete('card summary differs')
                    if excerpt.locator('*').count()!=0: raise VerificationIncomplete('card summary contains rendered markup')
                    if heading.is_visible() and not excerpt.evaluate('el => { const h=parseFloat(getComputedStyle(el).lineHeight); return Number.isFinite(h) && el.getBoundingClientRect().height <= h*3+2; }'):
                        raise VerificationIncomplete('card summary exceeds three lines')
                small_card=small.locator('xpath=..')
                if small_card.locator('img').count()!=0: raise VerificationIncomplete('text-only card contains an image')
                if small.is_visible() and not small_card.evaluate('el => el.scrollWidth <= el.clientWidth+1'): raise VerificationIncomplete('text-only card overflows horizontally')
                card=large.locator('xpath=ancestor::div[contains(@class,"rounded-2xl")][1]')
                if large.is_visible():
                    cover=card.locator('img')
                    if cover.count()!=1: raise VerificationIncomplete('card cover missing or extra image present')
                    try:
                        wait_loaded_cover(public,cover)
                    except PlaywrightTimeoutError:
                        raise VerificationIncomplete('card cover did not load')
                if large.is_visible() and not card.evaluate('el => el.scrollWidth <= el.clientWidth+1'): raise VerificationIncomplete('card overflows horizontally')
                public.screenshot(path=str(evidence/'guest-cards.png'),full_page=True)

            finally: guest.close()
            if local_day()!=reserved: raise VerificationIncomplete('verification crossed Vietnam day boundary')
            return {'url':public_url,'published_day':reserved}

        finally: context.close()

def publish(config,store,article,attempt):
    try:return prepare(config,store,article,attempt)
    except UploadFailure:
        # prepare's finally has completed context.close before this point.
        store.record_upload_closed(attempt)
        raise


def admin_content(details):
    """Locate exactly one visible content region in the active View dialog."""
    content=details.locator('.blog-article-content.ql-editor')
    content.wait_for(state='visible',timeout=15000)
    if content.count()!=1: raise VerificationIncomplete('admin content is not unique')
    return content


def guest_content(page):
    """Locate exactly one visible article content region on the guest page."""
    content=page.locator('.blog-article-content.ql-editor')
    content.wait_for(state='visible',timeout=30000)
    if content.count()!=1: raise VerificationIncomplete('public article body not uniquely identified')
    return content


def verify_outgoing_locales(sent,localized,contents):
    """Abort the native UI write unless it carries the exact checked locale values."""
    if not isinstance(sent,dict) or not isinstance(sent.get('translations'),list) or len(sent['translations'])!=2:
        raise VerificationIncomplete('unexpected create translation schema')
    translations={item.get('languageCode'):item for item in sent['translations'] if isinstance(item,dict)}
    if set(translations)!={'VI','EN'}:
        raise VerificationIncomplete('missing or duplicated create locales')
    for code,language in [('VI','Vietnamese'),('EN','English')]:
        fields=localized['locales'][language]; item=translations[code]
        for key,expected in [('title',fields['title']),('summary',fields['summary']),('metaTitle',fields['meta_title']),('metaDescription',fields['meta_description'])]:
            if item.get(key)!=expected:
                raise VerificationIncomplete('native create payload differs from approved locale')
        if not isinstance(item.get('content'),str) or content_fingerprint(item['content'])!=content_fingerprint(contents[language]):
            raise VerificationIncomplete('native create body differs from checked editor')
    if Counter(canonical_hashtags(sent.get('hashtags'),allow_prefix=False))!=Counter(canonical_hashtags(localized['hashtags'])):
        raise VerificationIncomplete('native create hashtags differ')


def canonical_hashtags(tags,allow_prefix=True):
    """UI strips one leading hash; no other mutation is accepted."""
    if not isinstance(tags,list) or not 3<=len(tags)<=5:
        raise VerificationIncomplete('invalid hashtag list')
    normalized=[]
    for tag in tags:
        if not isinstance(tag,str): raise VerificationIncomplete('invalid hashtag value')
        canonical=tag[1:] if allow_prefix and tag.startswith('#') else tag
        if not 2<=len(canonical)<=50 or canonical!=canonical.strip() or canonical.startswith('#') or '<' in canonical or '>' in canonical:
            raise VerificationIncomplete('invalid canonical hashtag')
        normalized.append(canonical)
    if len(set(normalized))!=len(normalized):
        raise VerificationIncomplete('duplicate canonical hashtags')
    return normalized


class _ContentFingerprint(HTMLParser):
    """Match Quill-equivalent text, images, block structure and effective inline ranges."""
    blocks={'p','h1','h2','h3','h4','li','ul','ol'}
    def __init__(self,markup):
        super().__init__(convert_charrefs=True)
        self.stack=[]; self.open_blocks=[]; self.structure=[]; self.stream=[]; self.inline=[]; self.images=[]
        self.feed(markup); self.close()
        if self.stack: raise VerificationIncomplete('unbalanced outgoing content')
    def handle_starttag(self,tag,attrs):
        if tag not in self.blocks|{'strong','em','a','br','img'} or any(k.lower().startswith('on') for k,_ in attrs):
            raise VerificationIncomplete('unsafe outgoing content')
        attr=dict(attrs)
        allowed_attrs={'img':{'src','class'},'a':{'href','target','rel'}}.get(tag,set())
        if len(attr)!=len(attrs) or not set(attr)<=allowed_attrs:
            raise VerificationIncomplete('unexpected outgoing markup attributes')
        if tag=='img':
            if 'class' in attr and attr['class']!='blog-standalone-image':
                raise VerificationIncomplete('unexpected outgoing image class')
            # Observed Quill editor uses this presentation-only class and the
            # native serializer removes it. All other classes remain blocked.
            self.images.append(attr.get('src'))
            self.stream.append(' [IMAGE:'+str(attr.get('src'))+'] '); return
        if tag=='br': self.stream.append(' '); return
        self.stack.append((tag,attr))
        if tag in self.blocks:
            self.stream.append(' '); self.open_blocks.append([tag,[],len(self.structure)])
            self.structure.append(None)
    def handle_endtag(self,tag):
        if tag in ('br','img'): return
        if not self.stack or self.stack.pop()[0]!=tag: raise VerificationIncomplete('unbalanced outgoing content')
        if tag in self.blocks:
            name,parts,index=self.open_blocks.pop()
            self.structure[index]=(name,None if name in ('ul','ol') else ' '.join(''.join(parts).split()))
            self.stream.append(' ')
    def handle_data(self,data):
        self.stream.append(data)
        for _,parts,_ in self.open_blocks: parts.append(data)
        bold=any(tag=='strong' for tag,_ in self.stack); italic=any(tag=='em' for tag,_ in self.stack)
        href=next((attrs.get('href') for tag,attrs in reversed(self.stack) if tag=='a'),None)
        self.inline.extend((char,bold,italic,href) for char in data if not char.isspace())
    def handle_comment(self,data): raise VerificationIncomplete('outgoing comments forbidden')


def content_fingerprint(markup):
    parsed=_ContentFingerprint(markup)
    return (' '.join(''.join(parsed.stream).split()),parsed.structure,parsed.inline,parsed.images)
