"""Scratch Step-by-step adapter for the observed Beaver Builder article layout."""
from urllib.parse import urlsplit
import ipaddress, socket, re, http.client, ssl
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .snapshot import save, MAX_MEDIA
from .store import identity

SOURCE_HOSTS={'www.scratchmagazine.co.uk','scratchmagazine.co.uk'}
MEDIA_HOSTS=SOURCE_HOSTS | {'cdn.scratchmagazine.co.uk'}
class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_media_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def check_media_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in MEDIA_HOSTS or p.username or p.password or p.port not in (None,443): raise ValueError('media URL outside allowed hosts')

def check_command_request(url, navigation):
    """Check each browser request before it is sent; DNS remains a trust boundary."""
    parts=urlsplit(url)
    allowed=SOURCE_HOSTS if navigation else MEDIA_HOSTS
    if (parts.scheme!='https' or parts.hostname not in allowed or parts.username or parts.password
            or parts.port not in (None,443) or (navigation and not parts.path.startswith('/nail_art/'))):
        raise ValueError('request outside article allowlist')
    addresses=socket.getaddrinfo(parts.hostname,443,type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(info[4][0]).is_global for info in addresses):
        raise ValueError('source host resolves to non-public address')

def command_route(route, limit=MAX_MEDIA):
    try:
        request=route.request
        check_command_request(request.url,request.is_navigation_request())
        if request.method!='GET': raise ValueError('only GET permitted')
        parts=urlsplit(request.url); host=parts.hostname
        addresses=socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)
        if not addresses or any(not ipaddress.ip_address(x[4][0]).is_global for x in addresses):
            raise ValueError('source DNS changed')
        target=(addresses[0][4][0],443)
        class PinnedHTTPS(http.client.HTTPSConnection):
            def connect(self):
                raw=socket.create_connection(target,timeout=self.timeout)
                try: self.sock=ssl.create_default_context().wrap_socket(raw,server_hostname=host)
                except BaseException: raw.close(); raise
        connection=PinnedHTTPS(host,timeout=12)
        try:
            path=parts.path or '/'
            if parts.query: path+='?'+parts.query
            connection.request('GET',path,headers={'Accept':'*/*','Accept-Encoding':'identity'})
            response=connection.getresponse()
            if response.status!=200: raise ValueError('redirect or unsuccessful response')
            ceiling=min(MAX_MEDIA,limit)
            body=response.read(ceiling+1)
            if len(body)>ceiling: raise ValueError('response exceeds limit')
            content_type=response.getheader('Content-Type','application/octet-stream')
        finally: connection.close()
    except Exception:
        route.abort(); return
    route.fulfill(status=200,body=body,content_type=content_type)
    return len(body)

def download_url(source_url):
    check_media_url(source_url)
    source=urlsplit(source_url)
    # Verified public WordPress origin path for the observed CDN asset only.
    if source.hostname=='cdn.scratchmagazine.co.uk':
        if not source.path.startswith('/wp-content/uploads/') or source.query or '%' in source.path or '..' in source.path.split('/'):
            raise ValueError('unrecognized CDN asset path')
        return 'https://www.scratchmagazine.co.uk'+source.path
    return source_url

def download(page,url):
    check_media_url(url)
    for info in socket.getaddrinfo(urlsplit(url).hostname,443,type=socket.SOCK_STREAM):
        if not ipaddress.ip_address(info[4][0]).is_global: raise ValueError('media host resolves to non-public address')
    # Fetch in the source browser session, reading the stream with an enforced cap.
    result=page.evaluate('''async ({url,limit}) => {
      const response=await fetch(url,{credentials:'include',redirect:'error'});
      if (!response.ok) throw Error('media request failed');
      const parsed=new URL(response.url);
      if (parsed.protocol!=='https:' || !['www.scratchmagazine.co.uk','scratchmagazine.co.uk','cdn.scratchmagazine.co.uk'].includes(parsed.hostname)) throw Error('media redirect outside allowed hosts');
      const declared=Number(response.headers.get('content-length') || 0);
      if (declared>limit || !response.body) throw Error('media too large or stream unavailable');
      const reader=response.body.getReader(), chunks=[]; let size=0;
      for (;;) { const part=await reader.read(); if(part.done) break; size+=part.value.length;
        if(size>limit) { await reader.cancel(); throw Error('media too large'); } chunks.push(part.value); }
      const all=new Uint8Array(size); let offset=0; for(const part of chunks){all.set(part,offset);offset+=part.length;}
      let binary=''; for(let i=0;i<all.length;i+=32768) binary+=String.fromCharCode(...all.subarray(i,i+32768));
      return btoa(binary);
    }''',{'url':url,'limit':MAX_MEDIA})
    import base64
    return base64.b64decode(result,validate=True)

EXTRACT=r'''() => {
 const h = document.querySelector('h1'); const main = h && h.closest('.fl-col');
 if (!main) throw Error('article column missing');
 const panels=[...main.querySelectorAll('.fl-module-html .fl-html .panel.panel-default')];
 if (!panels.length) throw Error('step panels missing');
 const hero=main.querySelector('.fl-module-photo img');
 const rich=[...main.querySelectorAll('.fl-module-rich-text .fl-rich-text')].filter(x=>!x.closest('[aria-label="Advertisements"],.scrat-mid-point-signup,.fl-module-box,form'));
 if (!hero || rich.length!==2) throw Error('unexpected article text modules');
 if (!['Autumn/Winter, Seasonal & Occasions, Step by Step, Themed','Step by Step'].includes(rich[0].textContent.trim()) || rich[0].querySelector('a,img,video,iframe,table,h1,h2,h3,h4,ul,ol')) throw Error('unrecognized taxonomy module');
 const articlePhotos=[...main.querySelectorAll('.fl-module-photo img')].filter(x=>!x.closest('[aria-label="Advertisements"],.scrat-mid-point-signup,.fl-module-box,form'));
 if(articlePhotos.length!==1 || articlePhotos[0]!==hero) throw Error('unhandled article photo module');
 if (hero.closest('.fl-module-photo').querySelector('figcaption,.fl-photo-caption')) throw Error('unhandled hero caption');
 if (!(hero.compareDocumentPosition(rich[1]) & Node.DOCUMENT_POSITION_FOLLOWING)) throw Error('unexpected hero order');
 const stepModule=panels[0].closest('.fl-module-html');
 if (!stepModule || panels.some(x=>x.closest('.fl-module-html')!==stepModule)) throw Error('multiple step modules');
 const remainder=stepModule.cloneNode(true); remainder.querySelectorAll('.panel.panel-default,script,style').forEach(n=>n.remove());
 if (remainder.textContent.trim() || remainder.querySelector('img,video,iframe,table')) throw Error('unhandled step module content');
 if (!(rich[1].compareDocumentPosition(stepModule) & Node.DOCUMENT_POSITION_FOLLOWING)) throw Error('unexpected content order');
 for (const module of main.querySelectorAll('.fl-module-html')) {
   if (module===stepModule || module.closest('.fl-module-box,[aria-label="Advertisements"],.scrat-mid-point-signup,form')) continue;
   if (module.querySelector('.ns-buttons')) continue;
   if (module.innerText.trim() || module.querySelector('img,video,iframe,table')) throw Error('unhandled html module');
 }
 for (const image of main.querySelectorAll('img')) {
   if (image===hero || image.closest('.panel.panel-default,.fl-module-box,[aria-label="Advertisements"],.scrat-mid-point-signup,form,.ns-buttons')) continue;
   throw Error('unhandled article image');
 }
 const clean = (node) => {
   if (node.nodeType===Node.TEXT_NODE) return node.textContent.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
   if (node.nodeType!==Node.ELEMENT_NODE) return '';
   if (node.matches('[aria-label="Advertisements"],.scrat-mid-point-signup,form,script,style,noscript')) return '';
   if (node.matches('img,video,iframe,table,svg,object,embed,b,i')) throw Error('unsupported content or emphasis in text');
   const tag=node.tagName.toLowerCase();
   const children=[...node.childNodes].map(clean).join('');
   if (tag==='a') {
      const href=node.getAttribute('href') || ''; const u=new URL(href,location.href);
      if (!['https:','http:'].includes(u.protocol)) throw Error('unsafe link');
      return '<a href="'+u.href.replaceAll('&','&amp;').replaceAll('"','&quot;')+'">'+children+'</a>';
   }
   if (['p','h2','h3','h4','ul','ol','li','strong','em','b','i','br'].includes(tag)) return '<'+tag+'>'+children+(tag==='br'?'':'</'+tag+'>');
   if (['div','span'].includes(tag)) return children;
   throw Error('unsupported element '+tag);
 };
 const img = x => ({url:x.dataset.src || x.currentSrc || x.src,alt:x.alt || ''});
 const blocks=[{type:'image',media:0}, {type:'html',html:clean(rich[rich.length-1])}];
 const media=[img(hero)];
 for (const panel of panels) {
   const image=panel.querySelector('.step-by-step-image img');
   const number=panel.querySelector('.step-by-step-number');
   const row=panel.querySelector('.row');
   const description=row && [...row.children].find(x=>!x.classList.contains('step-by-step-image'));
   if (!image || !number || !description || !description.textContent.trim()) throw Error('incomplete step');
   if (!(number.compareDocumentPosition(image) & Node.DOCUMENT_POSITION_FOLLOWING) || !(image.compareDocumentPosition(description) & Node.DOCUMENT_POSITION_FOLLOWING)) throw Error('unexpected step order');
   if (panel.querySelectorAll('img').length!==1 || panel.querySelector('video,iframe,table,figcaption')) throw Error('unhandled step media');
   const panelRemainder=panel.cloneNode(true); panelRemainder.querySelectorAll('.step-by-step-number,.step-by-step-image img').forEach(n=>n.remove());
   const remainingText=panelRemainder.textContent.replace(/\s+/g,' ').trim();
   if (remainingText!==description.textContent.replace(/\s+/g,' ').trim()) throw Error('unhandled step text');
   blocks.push({type:'html',html:'<h3>'+number.textContent.trim().replaceAll('&','&amp;').replaceAll('<','&lt;')+'</h3>'});
   blocks.push({type:'image',media:media.length}); media.push(img(image));
   blocks.push({type:'html',html:clean(description)});
 }
 const date=document.querySelector('meta[property="article:published_time"]')?.content || null;
 const evidence=main.cloneNode(true); evidence.querySelectorAll('[aria-label="Advertisements"],.scrat-mid-point-signup,.fl-module-box,form,script,style').forEach(n=>n.remove());
 return {canonical:document.querySelector('link[rel=canonical]')?.href || location.href, title:h.innerText.trim(), date, author:document.querySelector('meta[name=author]')?.content || null, raw_html:evidence.outerHTML, blocks, media};
}'''

class IneligibleArticle(ValueError): pass

def validate_eligibility(title, media, *, downloaded=False):
    if not isinstance(title,str) or not 5<=len(title.strip())<=200: raise IneligibleArticle('title outside destination limits')
    if not 1<=len(media)<=10: raise IneligibleArticle('media count outside destination limits')
    if downloaded:
        for item in media:
            data=item['bytes']
            if not (data.startswith(b'\xff\xd8\xff') or data.startswith(b'\x89PNG\r\n\x1a\n') or (data[:4]==b'RIFF' and data[8:12]==b'WEBP')):
                raise IneligibleArticle('destination image format unsupported')

MAX_LISTING_PAGES=5
FIRST_LISTING='https://www.scratchmagazine.co.uk/step-by-step/'
CURATED_LUXURY_URLS=('https://www.scratchmagazine.co.uk/nail_art/crystal-nail-tutorial/',)

def include_curated_luxury(urls):
    """Prepend a fixed reviewed source; never accept a caller-supplied article URL."""
    return list(dict.fromkeys([*CURATED_LUXURY_URLS,*urls]))[:20]

def skip_known(store, url):
    """Avoid recrawling published, ready, localization-held or topic-held entries."""
    return bool(store.db.execute("SELECT 1 FROM articles WHERE id=? AND (state!='held' OR COALESCE(reason,'') LIKE 'localization:%' OR COALESCE(reason,'') LIKE 'topic:%')",(identity(url),)).fetchone())

def validate_cursor(url):
    parts=urlsplit(url)
    if (parts.scheme!='https' or parts.netloc!='www.scratchmagazine.co.uk' or parts.query or parts.fragment
        or not re.fullmatch(r'/step-by-step/page/(?:[2-9]|[1-9][0-9]+)/',parts.path)):
        raise ValueError('invalid observed listing cursor')
    return url

def choose_next(hrefs):
    valid=set()
    for href in hrefs:
        if not isinstance(href,str): continue
        try: valid.add(validate_cursor(href))
        except ValueError:
            link=urlsplit(href)
            known_video=(link.scheme=='https' and link.netloc=='www.scratchmagazine.co.uk' and not link.query and not link.fragment
                         and re.fullmatch(r'/step-by-step/paged-3/[1-9][0-9]*/',link.path))
            if known_video: continue
            if link.path.startswith('/step-by-step/'): raise ValueError('unrecognized article pagination link')
            continue  # Other pagers on the same page are unrelated.
    if len(valid)>1: raise ValueError('ambiguous listing pagination')
    return next(iter(valid),None)

def listing_page_plan(load,cursor=None):
    if cursor: validate_cursor(cursor)
    current=FIRST_LISTING; visited=set()
    for page_index in range(MAX_LISTING_PAGES):
        if current in visited: raise ValueError('listing pagination loop')
        visited.add(current)
        urls,observed=load(current)
        if len(urls)>20: raise ValueError('listing page exceeds candidate limit')
        if observed: validate_cursor(observed)
        yield current,urls,observed
        following=cursor if page_index==0 and cursor else observed
        if not following: break
        current=following

def crawl_url(config, store, url):
    """Capture one supported article; it remains subject to normal quality gates."""
    from playwright.sync_api import sync_playwright
    parts=urlsplit(url)
    if (parts.scheme!='https' or parts.hostname not in SOURCE_HOSTS or parts.username or parts.password
            or parts.port not in (None,443) or not parts.path.startswith('/nail_art/') or parts.fragment):
        raise ValueError('unsupported article URL')
    def known(source_url):
        row=store.db.execute('SELECT id,state FROM articles WHERE id=?',(identity(source_url),)).fetchone()
        if not row: return None
        if row['state']=='ready': return {'state':'captured','article_id':row['id']}
        if row['state']=='published':
            attempt=store.db.execute("SELECT target_url FROM attempts WHERE article_id=? AND state='published' ORDER BY id DESC LIMIT 1",(row['id'],)).fetchone()
            return {'state':'existing','url':attempt['target_url'] if attempt else None}
        return {'state':'held'}
    found=known(url)
    if found: return found
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        context=browser.new_context(service_workers='block',java_script_enabled=False)
        try:
            budget={'requests':0,'bytes':0}
            def bounded_route(route):
                budget['requests']+=1
                if budget['requests']>100 or budget['bytes']>=100*1024*1024:
                    route.abort(); return
                used=command_route(route,100*1024*1024-budget['bytes'])
                if used: budget['bytes']+=used
            context.route('**/*', bounded_route)
            if not hasattr(context,'route_web_socket'): raise ValueError('browser websocket confinement unavailable')
            context.route_web_socket('**/*', lambda websocket: websocket.close())
            page=context.new_page(); page.goto(url,wait_until='domcontentloaded',timeout=30000)
            actual=urlsplit(page.url)
            if actual.scheme!='https' or actual.hostname not in SOURCE_HOSTS or not actual.path.startswith('/nail_art/'):
                raise ValueError('source redirect outside allowed article scope')
            article=page.evaluate(EXTRACT); article_url=article['canonical']; canonical_parts=urlsplit(article_url)
            if canonical_parts.scheme!='https' or canonical_parts.hostname not in SOURCE_HOSTS or not canonical_parts.path.startswith('/nail_art/'):
                raise ValueError('canonical URL outside source')
            found=known(article_url)
            if found: return found
            validate_eligibility(article['title'],article['media'])
            files=[{'original_url':m['url'],'download_url':download_url(m['url']),'alt':m['alt'],'bytes':download(page,download_url(m['url']))} for m in article['media']]
            validate_eligibility(article['title'],files,downloaded=True)
            meta={'source_url':article_url,'title':article['title'],'source_date':article['date'],'author':article['author']}
            html='\n'.join(b['html'] for b in article['blocks'] if b['type']=='html')
            aid=save(store.root,meta,article['blocks'],html,files,{'html':article['raw_html'],'screenshot':page.screenshot(full_page=True)})
            store.add_ready(article_url,article['title'],article['date'])
            return {'state':'captured', 'article_id':aid}
        finally: context.close(); browser.close()

def crawl(config,store):
    from playwright.sync_api import sync_playwright
    if config['source_url'].rstrip('/') != FIRST_LISTING.rstrip('/'): raise ValueError('unsupported source listing')
    with sync_playwright() as p:
        profile=store.root/'browser'/'source-profile'; profile.mkdir(parents=True,exist_ok=True)
        browser=p.chromium.launch_persistent_context(str(profile),headless=True)
        try:
            listing=browser.new_page(); page=browser.new_page()
            row=store.db.execute("SELECT value FROM settings WHERE key='listing_cursor'").fetchone()
            cursor=validate_cursor(row[0]) if row and row[0] else None
            def load_listing(current):
                listing.goto(current,wait_until='domcontentloaded',timeout=30000)
                if listing.url.rstrip('/')!=current.rstrip('/'): raise ValueError('listing redirect not permitted')
                urls=listing.locator('a[href*="/nail_art/"]').evaluate_all('(nodes) => [...new Set(nodes.map(n=>n.href))]')
                next_links=listing.locator('a.next.page-numbers')
                hrefs=next_links.evaluate_all('(nodes) => nodes.map(n => n.href)')
                observed=choose_next(hrefs)
                return urls,observed
            for page_index,(current,urls,observed) in enumerate(listing_page_plan(load_listing,cursor)):
                if page_index==0: urls=include_curated_luxury(urls)
                for url in urls[:20]:
                    parts=urlsplit(url)
                    if parts.scheme!='https' or parts.hostname not in SOURCE_HOSTS or not parts.path.startswith('/nail_art/'): continue
                    if skip_known(store,url): continue
                    try:
                        page.goto(url,wait_until='domcontentloaded',timeout=30000)
                        if urlsplit(page.url).hostname not in SOURCE_HOSTS: raise ValueError('source redirect outside allowed hosts')
                        article=page.evaluate(EXTRACT)
                        article_url=article['canonical']; canonical_parts=urlsplit(article_url)
                        if canonical_parts.scheme!='https' or canonical_parts.hostname not in SOURCE_HOSTS or not canonical_parts.path.startswith('/nail_art/'): raise ValueError('canonical URL outside source')
                        if skip_known(store,article_url): continue
                        validate_eligibility(article['title'],article['media'])
                        files=[{'original_url':m['url'],'download_url':download_url(m['url']),'alt':m['alt'],'bytes':download(page,download_url(m['url']))} for m in article['media']]
                        validate_eligibility(article['title'],files,downloaded=True)
                        meta={'source_url':article_url,'title':article['title'],'source_date':article['date'],'author':article['author']}
                        html='\n'.join(b['html'] for b in article['blocks'] if b['type']=='html')
                        evidence={'html':article['raw_html'],'screenshot':page.screenshot(full_page=True)}
                        aid=save(store.root,meta,article['blocks'],html,files,evidence)
                        store.add_ready(article_url,article['title'],article['date']); print('captured',aid)
                        # Resume this already-observed archive page until its eligible articles are exhausted.
                        if page_index>0: store.db.execute("INSERT INTO settings VALUES ('listing_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(current,))
                        return
                    except Exception as exc:
                        reason=str(exc) if isinstance(exc,IneligibleArticle) else type(exc).__name__
                        store.hold(url,reason); print('candidate held:',reason,flush=True)
                # Save only a link observed on this page, or the previously observed resume cursor.
                following=cursor if page_index==0 and cursor else observed
                if following:
                    store.db.execute("INSERT INTO settings VALUES ('listing_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(following,))
                else:
                    store.db.execute("DELETE FROM settings WHERE key='listing_cursor'")
            print('no eligible captured article')
        finally: browser.close()
