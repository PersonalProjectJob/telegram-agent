"""Read-only preflight before a durable official reservation."""
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import nullcontext
from .official_deadline import require
from .official_duplicate_scan import check
from .official_editor_canary import allowed_read
from .official_prepost import check_publish_time
from .official_store import _validated,_check_candidate_hashes,CandidateUnavailable
from .localization import strict_json
from .lock import worker_lock
from .official_upload_canary import isolated_profile

def check_candidate(store,article_id,day,slot):
    require(750,active=True)
    row=store.db.execute('SELECT evidence_path,record_sha FROM official_candidates WHERE article_id=?',(article_id,)).fetchone()
    if not row:raise CandidateUnavailable('official candidate missing')
    _check_candidate_hashes(store,row['evidence_path'],row['record_sha'])
    payload,_,_=_validated(store,row['evidence_path'],row['record_sha'])
    data=strict_json(payload.decode('utf-8'))
    check_publish_time(data['published_at'],day,slot,datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')))
    return scan_admin(store.root,data['source']['url'],lock_held=True)

def scan_admin(root,source_url,*,lock_held=False):
    """Read-only, isolated profile; no upload, Create, or caller-supplied routes."""
    from .official_sources import SOURCES
    if source_url not in {entry['url'] for entry in SOURCES}:raise ValueError('source not allowlisted')
    if type(lock_held) is not bool:raise ValueError('invalid lock contract')
    if lock_held:
        from .official_scratch_publish import require_owned_worker_lock
        require_owned_worker_lock(root)
    from playwright.sync_api import sync_playwright
    with (nullcontext() if lock_held else worker_lock(root)),isolated_profile(root) as profile,sync_playwright() as playwright:
        context=playwright.chromium.launch_persistent_context(str(profile),headless=True,service_workers='block',
            timezone_id='Asia/Ho_Chi_Minh',args=['--no-startup-window','--disable-background-networking'])
        try:
            if not hasattr(context,'route_web_socket'):raise ValueError('websocket blocking unavailable')
            context.route_web_socket('**/*',lambda ws:ws.close())
            context.route('**/*',lambda route:route.continue_() if allowed_read(route.request.method,route.request.url) else route.abort('blockedbyclient'))
            if any(p.url not in ('','about:blank') for p in context.pages):raise ValueError('unexpected startup page')
            return check(context.new_page(),source_url)
        finally:context.close()

if __name__=='__main__':
    import argparse,json,sys
    from pathlib import Path
    parser=argparse.ArgumentParser(description='Read-only official source duplicate scan')
    parser.add_argument('--root',required=True);parser.add_argument('--source-url',required=True)
    args=parser.parse_args()
    try:print(json.dumps(scan_admin(Path(args.root).resolve(),args.source_url)))
    except Exception as error:
        print('source scan held: '+type(error).__name__,file=sys.stderr);raise SystemExit(1)
