import argparse, json, sys, time
from datetime import datetime
from pathlib import Path
from .store import Store, ZONE, local_day
from .lock import worker_lock

def main():
    parser=argparse.ArgumentParser(prog='source-mirror')
    parser.add_argument('--config',required=True)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('status','pause','resume','crawl','once','run'): sub.add_parser(name)
    rec=sub.add_parser('reconcile'); rec.add_argument('attempt',type=int)
    group=rec.add_mutually_exclusive_group(required=True)
    group.add_argument('--published-url'); group.add_argument('--confirmed-not-created',action='store_true')
    rec.add_argument('--published-day'); rec.add_argument('--evidence',required=True)
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
        if args.command=='run':
            while True:
                now=datetime.now(ZONE); day=now.date().isoformat(); minute=now.strftime('%H:%M')
                if not store.paused():
                    for slot in cfg['slots']:
                        if slot > minute: break
                        if store.db.execute('SELECT 1 FROM slots WHERE day=? AND slot=?',(day,slot)).fetchone(): continue
                        try:
                            with worker_lock(store.root):
                                if not store.claim_slot_try(day,slot): continue
                                from .crawler import crawl
                                try: crawl(cfg,store)
                                finally: store.safe_export_reports()
                                once(cfg,store,day,slot)
                        except Exception as exc:
                            print(f'slot held: {type(exc).__name__}: {exc}',flush=True); break
                time.sleep(60)
    finally: store.close()

def once(cfg,store,day=None,slot=None):
    from .publisher import publish, preflight
    from .localization import generate_revision, load_approved, QualityHold
    preflight(cfg)
    # Quality failures occur before a publishing intent: no quota or unknown outcome.
    selected=None
    if store.paused(): raise ValueError('paused')
    if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone(): raise ValueError('unresolved attempt requires reconciliation')
    effective_day=day or local_day()
    if store.db.execute("SELECT count(*) FROM attempts WHERE state='published' AND published_day=?",(effective_day,)).fetchone()[0]>=4: raise ValueError('daily quota reached')
    for candidate in store.ready_candidates():
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
    except BaseException:
        store.unknown(attempt); store.safe_export_reports(); raise

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr); sys.exit(1)
