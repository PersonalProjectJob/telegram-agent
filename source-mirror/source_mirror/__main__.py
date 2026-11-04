import argparse, json, sys, time
from datetime import datetime
from pathlib import Path
from .store import Store, ZONE, local_day
from .lock import worker_lock

def main():
    parser=argparse.ArgumentParser(prog='source-mirror')
    parser.add_argument('--config',required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('status','pause','resume','crawl','once','run','command-once'): sub.add_parser(name)
    rec=sub.add_parser('reconcile'); rec.add_argument('attempt',type=int)
    group=rec.add_mutually_exclusive_group(required=True)
    group.add_argument('--published-url'); group.add_argument('--confirmed-not-created',action='store_true')
    rec.add_argument('--published-day'); rec.add_argument('--evidence',required=True)
    qrec=sub.add_parser('reconcile-command'); qrec.add_argument('owner_id',type=int); qrec.add_argument('message_id',type=int)
    args=parser.parse_args(); cfg=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
    if cfg.get('timezone')!='Asia/Ho_Chi_Minh' or cfg.get('slots')!=['08:00','12:00','16:00','20:00']: raise ValueError('invalid schedule')
    store=Store(cfg['data_root'])
    try:
        if args.command=='status':
            try:
                with worker_lock(store.root): store.safe_export_reports()
            except OSError:
                print('worker busy; showing database status without rebuilding reports',file=sys.stderr)
            print(json.dumps(store.status(),ensure_ascii=False,indent=2)); return
        if args.command=='pause': store.set_paused(True); print('paused'); return
        if args.command=='resume': store.set_paused(False); print('resumed'); return
        if args.command=='reconcile-command':
            from .telegram_queue_worker import reconcile_unknown
            with worker_lock(store.root):
                print(json.dumps(reconcile_unknown(store,args.owner_id,args.message_id),ensure_ascii=False))
            return
        if args.command=='reconcile':
            with worker_lock(store.root):
                if args.published_url:
                    if not args.published_day: raise ValueError('published day required')
                    datetime.strptime(args.published_day,'%Y-%m-%d')
                    store.published(args.attempt,args.published_url,args.published_day,args.evidence)
                else: store.release(args.attempt,args.evidence)
                store.safe_export_reports(args.published_day if args.published_url else None)
                print('reconciled')
            return
        if args.command=='crawl':
            from .crawler import crawl
            with worker_lock(store.root):
                try: crawl(cfg,store)
                finally: store.safe_export_reports()
            return
        if args.command=='once':
            with worker_lock(store.root): once(cfg,store)
            return
        if args.command=='command-once':
            from .telegram_queue_worker import process_one
            with worker_lock(store.root): process_one(cfg,store)
            return
        if args.command=='run':
            while True:
                from .slot_notifications import recover
                try:
                    with worker_lock(store.root): recover(store)
                except OSError:
                    time.sleep(60); continue
                now=datetime.now(ZONE); day=now.date().isoformat(); minute=now.strftime('%H:%M')
                if not store.paused():
                    for slot in cfg['slots']:
                        if slot > minute: break
                        if store.db.execute('SELECT 1 FROM slots WHERE day=? AND slot=?',(day,slot)).fetchone(): continue
                        try:
                            with worker_lock(store.root):
                                from .slot_notifications import claim_begin, finish
                                if not claim_begin(store,day,slot): continue
                                previous_attempt=store.db.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]
                                from .crawler import crawl
                                try:
                                    try: crawl(cfg,store)
                                    finally: store.safe_export_reports()
                                    url=once(cfg,store,day,slot)
                                except Exception:
                                    uncertain=store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone()
                                    published=store.db.execute("SELECT target_url FROM attempts WHERE id>? AND state='published' ORDER BY id DESC LIMIT 1",(previous_attempt,)).fetchone()
                                    if uncertain: finish(store,day,slot,'unknown')
                                    elif published and published['target_url']: finish(store,day,slot,'published',published['target_url'])
                                    else: finish(store,day,slot,'held')
                                    raise
                                else:
                                    finish(store,day,slot,'published' if url else 'unknown',url)
                        except Exception as exc:
                            print(f'slot held: {type(exc).__name__}: {exc}',flush=True); break
                if cfg.get('command_url_jobs_enabled') is True:
                    from .telegram_command_dispatch import dispatch
                    try:
                        dispatch(args.config,cfg['slots'])
                    except Exception as exc:
                        print(f'command queue held: {type(exc).__name__}',flush=True)
                time.sleep(60)
    finally: store.close()

def once(cfg,store,day=None,slot=None,requested_article_id=None):
    from .publisher import publish, preflight
    from .localization import generate_revision, load_approved, QualityHold
    from .topics import candidates
    preflight(cfg)
    # Quality failures occur before a publishing intent: no quota or unknown outcome.
    selected=None
    if store.paused(): raise ValueError('paused')
    if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone(): raise ValueError('unresolved attempt requires reconciliation')
    effective_day=day or local_day()
    if store.db.execute("SELECT count(*) FROM attempts WHERE state='published' AND published_day=?",(effective_day,)).fetchone()[0]>=4: raise ValueError('daily quota reached')
    from .official_scheduler import try_once as try_official_once
    if requested_article_id is None:
        official_url=try_official_once(store,effective_day,slot)
        if official_url: return official_url
    choices = (candidates(store,effective_day,slot,config=cfg) if requested_article_id is None
               else candidates(store,effective_day,slot,limit=None,config=cfg))
    for candidate in choices:
        if requested_article_id is not None and candidate['id'] != requested_article_id: continue
        try:
            candidate['localization_revision']=generate_revision(cfg,store,candidate)
            load_approved(store,candidate)
            selected=candidate; break
        except QualityHold as exc:
            store.quality_hold(candidate['id'],str(exc)); store.safe_export_reports()
            print('article quality held: '+candidate['id']+' '+str(exc),flush=True)
    if selected is None: raise QualityHold('no article passed localization quality gate')
    attempt,article=store.reserve(day,slot,selected['id'])
    article['localization_revision']=selected['localization_revision']
    try:
        result=publish(cfg,store,article,attempt)
        store.published(attempt,result['url'],result['published_day'])
        store.safe_export_reports(result['published_day'])
        print(f'verified publication: {result["url"]}')
        return result['url']
    except BaseException:
        store.unknown(attempt); store.safe_export_reports(); raise

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr); sys.exit(1)
