"""Official native publisher; disabled until independent integration review."""
from datetime import datetime,timezone
import re
from urllib.parse import urlsplit
from .localization import strict_json,packed
from .official_editor_check import AnchorAudit
from .publisher import content_fingerprint,admin_content,guest_content,search_response

PUBLISHER_VERIFIED=False

def available():
    return PUBLISHER_VERIFIED is True

def publish(store,article,attempt):
    if not available():raise ValueError('official native publisher is not yet verified')
    from .official_scratch_publish import publish as direct_publish
    return direct_publish(store,article,attempt)


def verify_readback(page,playwright,payload_bytes,native_request,mapping,reserved_day):
    """Require unique admin record and guest body after Create; otherwise HOLD."""
    payload=strict_json(payload_bytes.decode('utf-8'));source=payload['source'];locales=payload['locales']
    search=page.get_by_placeholder('Search by title, slug...')
    with page.expect_response(lambda r:search_response(r,locales['vi']['title']),timeout=30000) as observed:
        search.fill(locales['vi']['title'])
    response=observed.value
    if not response.ok:raise ValueError('official admin readback unavailable')
    listing=strict_json(response.body().decode('utf-8'))
    if not isinstance(listing,dict) or not isinstance(listing.get('items'),list):raise ValueError('official admin readback schema changed')
    candidates=[]
    for record in listing['items']:
        if not isinstance(record,dict) or record.get('externalSourceUrl')!=source['url']:continue
        translations=record.get('translations')
        if not isinstance(translations,list) or len(translations)!=2:continue
        by_language={item.get('languageCode'):item for item in translations if isinstance(item,dict)}
        if set(by_language)!={'Vi','En'}:continue
        good=True
        for code,language in (('Vi','vi'),('En','en')):
            fields=locales[language];entry=by_language[code]
            for field,key in (('title','title'),('summary','summary'),('metaTitle','meta_title'),('metaDescription','meta_description'),('content','body_html')):
                if entry.get(field)!=fields[key]:good=False
        if not good:continue
        if (record.get('status')!='Published' or payload.get('status')!='Published' or record.get('isPublic') is not True
                or payload.get('is_public') is not True or type(record.get('isFeatured')) is not bool
                or record['isFeatured'] is not payload.get('is_featured')
                or not isinstance(payload.get('video_urls'),list) or not isinstance(record.get('videoUrls'),list)
                or packed(record['videoUrls'])!=packed(payload['video_urls']) or record.get('categoryId')!=mapping.get(payload['category'])
                or packed(record.get('hashtags'))!=packed(payload['hashtags'])
                or packed(record.get('coverImage'))!=packed(native_request['coverImage']) or packed(record.get('images'))!=packed(native_request['images'])):continue
        try:
            timestamp=datetime.fromisoformat(record['publishedAt'].replace('Z','+00:00'))
            expected=datetime.fromisoformat(payload['published_at'].replace('Z','+00:00'))
            if timestamp.tzinfo is None or expected.tzinfo is None or timestamp.astimezone(timezone.utc)!=expected.astimezone(timezone.utc):continue
        except (KeyError,AttributeError,ValueError):continue
        candidates.append(record)
    if len(candidates)!=1:raise ValueError('official admin record not unique or differs')
    record=candidates[0];slug=record.get('slug')
    if not isinstance(slug,str) or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',slug):raise ValueError('official public slug invalid')
    rows=page.locator('tbody tr')
    if rows.count()!=1 or 'Published' not in rows.first.inner_text():raise ValueError('official admin row not unique')
    rows.first.locator('button[title="View"]').click();dialog=page.get_by_role('dialog')
    for language,code in (('Vietnamese','vi'),('English','en')):
        dialog.get_by_role('tab',name=('VI' if code=='vi' else 'EN'),exact=True).click()
        body=admin_content(dialog);actual=body.inner_html();expected=locales[code]['body_html']
        if content_fingerprint(actual)!=content_fingerprint(expected) or AnchorAudit(actual).links!=AnchorAudit(expected).links:
            raise ValueError('official admin body differs')
    url='https://nailhub.ai/blog/'+slug;guest=playwright.chromium.launch(headless=True)
    try:
        context=guest.new_context(service_workers='block');public=context.new_page()
        def guard(route):
            parsed=urlsplit(route.request.url);method=route.request.method
            allowed=(method in ('GET','HEAD') and parsed.scheme=='https' and not parsed.username and not parsed.password and parsed.port in (None,443)
                     and ((parsed.hostname=='nailhub.ai' and (parsed.path=='/blog/'+slug or re.fullmatch(r'/assets/[A-Za-z0-9._/-]+\.(?:js|css)',parsed.path)))
                          or (parsed.hostname=='api.nailhub.ai' and parsed.path=='/api/v1/blogs/'+slug)
                          or (parsed.hostname=='vlink-storages-prod.sgp1.cdn.digitaloceanspaces.com' and route.request.url in media_urls)))
            if allowed:route.continue_()
            else:route.abort('blockedbyclient')
        media_urls={entry['imageUrl'] for entry in [native_request['coverImage'],*native_request['images']]}
        context.route('**/*',guard)
        if hasattr(context,'route_web_socket'):context.route_web_socket('**/*',lambda ws:ws.close())
        else:raise ValueError('guest websocket blocking unavailable')
        response=public.goto(url,wait_until='domcontentloaded',timeout=30000)
        if not response or response.status!=200 or public.url!=url:raise ValueError('official guest unavailable')
        title=public.locator('h1')
        if title.count()!=1 or title.inner_text().strip()!=locales['vi']['title']:raise ValueError('official guest title differs')
        body=guest_content(public);actual=body.inner_html();expected=locales['vi']['body_html']
        if content_fingerprint(actual)!=content_fingerprint(expected) or AnchorAudit(actual).links!=AnchorAudit(expected).links:
            raise ValueError('official guest content differs')
        visible=public.locator('img').evaluate_all('(imgs) => imgs.filter(i => i.complete && i.naturalWidth>0 && i.getClientRects().length>0).map(i => i.src)')
        if any(url not in visible for url in media_urls):raise ValueError('official guest media missing')
        context.close()
    finally: guest.close()
    published=datetime.fromisoformat(payload['published_at'].replace('Z','+00:00'))
    from zoneinfo import ZoneInfo
    if published.astimezone(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()!=reserved_day:raise ValueError('official publication day differs')
    return {'verified':True,'url':url,'published_day':reserved_day}
