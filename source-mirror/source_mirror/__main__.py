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
                    with worker_lock(store.root):
                        store.recover_closed_uploads(scheduled_now=datetime.now(ZONE))
                        recover(store)
                except OSError:
                    time.sleep(60); continue
                now=datetime.now(ZONE); day=now.date().isoformat(); minute=now.strftime('%H:%M')
                if not store.paused():
                    for slot in cfg['slots']:
                        if slot > minute: break
                        if not store.slot_available(day,slot,now): continue
                        try:
                            with worker_lock(store.root):
                                from .slot_notifications import claim_begin, finish
                                if not claim_begin(store,day,slot): continue
                                previous_attempt=store.db.execute('SELECT COALESCE(MAX(id),0) FROM attempts').fetchone()[0]
                                from .crawler import crawl
                                try:
                                    def prepare():
                                        try: crawl(cfg,store)
                                        finally: store.safe_export_reports()
                                    from .scheduled_assignments import run_slot
                                    url=run_slot(cfg,store,day,slot,once,before=prepare)
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

def once(cfg,store,day=None,slot=None,requested_article_id=None,scheduled_guard=False):
    from .publisher import publish, preflight, UploadFailure
    from .localization import generate_revision, load_approved, QualityHold
    from .topics import candidates, matches_slot
    preflight(cfg)
    # Quality failures occur before a publishing intent: no quota or unknown outcome.
    if store.paused(): raise ValueError('paused')
    store.recover_closed_uploads(scheduled_now=datetime.now(ZONE))
    if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone(): raise ValueError('unresolved attempt requires reconciliation')
    effective_day=day or local_day()
    if store.db.execute("SELECT count(*) FROM attempts WHERE state='published' AND published_day=?",(effective_day,)).fetchone()[0]>=4: raise ValueError('daily quota reached')
    from .official_scheduler import try_once as try_official_once
    if requested_article_id is None:
        official_url=try_official_once(store,effective_day,slot)
        if official_url: return official_url
    from .scratch_cover import prepare_cover, validate_cover, CoverHold
    choices=candidates(store,effective_day,slot,limit=None,config=cfg)
    if requested_article_id is None and slot in ('08:00','16:00','20:00'):
        choices.append(None)  # Fallback after source candidates have failed or been skipped.
    for candidate in choices:
        if candidate is None:
            from .official_deadline import seconds_available
            try:remaining=seconds_available(effective_day,slot,datetime.now(ZONE))
            except ValueError:continue
            if remaining<2400:continue
            from .original_business import prepare as prepare_original
            candidate=prepare_original(cfg,store,effective_day,slot)
            if candidate is None:continue
        if requested_article_id is not None and candidate['id']!=requested_article_id:continue
        if not matches_slot(store,candidate,slot):continue
        from .store import verify_snapshot
        folder=verify_snapshot(store.root,candidate['id'])
        metadata=json.loads((folder/'metadata.json').read_text(encoding='utf-8'))
        if metadata.get('content_origin')=='ai_original':
            from .original_business import verify_original
            blocks=json.loads((folder/'content.json').read_text(encoding='utf-8'))
            try:verify_original(metadata,blocks)
            except (QualityHold,OSError,ValueError) as exc:
                print('original preflight held: '+type(exc).__name__,flush=True);continue
        try:
            candidate['localization_revision']=generate_revision(cfg,store,candidate)
            localized=load_approved(store,candidate)
            if metadata.get('content_origin')=='ai_original':
                from .original_duplicate_scan import precheck
                try:precheck(store.root,localized,cfg)
                except (QualityHold,OSError,ValueError,RuntimeError) as exc:
                    print('original duplicate check held: '+type(exc).__name__,flush=True);continue
            try:
                cover=prepare_cover(cfg,store,candidate,localized)
                validate_cover(store,candidate,localized,cover,config=cfg,require_production=(cover['kind']=='generated'))
            except (CoverHold,OSError,ValueError,KeyError,TypeError) as exc:
                print('cover preflight held: '+candidate['id']+' '+str(exc),flush=True);continue
        except QualityHold as exc:
            store.quality_hold(candidate['id'],str(exc));store.safe_export_reports();continue
        body_bundle=None
        if metadata.get('content_origin')=='ai_original':
            try:
                from .official_deadline import seconds_available
                remaining=seconds_available(effective_day,slot,datetime.now(ZONE)) if slot is not None else 720
                # Reserve 80 seconds for the isolated uploader and 450 for the
                # existing publication path; skip optional generation if tight.
                image_seconds=min(360,max(0,remaining-530))
                if image_seconds>=1:
                    from .body_client import generate as generate_body
                    prepared=generate_body(store,candidate,localized,total_seconds=image_seconds)
                    if prepared and prepared['assets']:
                        remaining=seconds_available(effective_day,slot,datetime.now(ZONE)) if slot is not None else 720
                        if remaining>=530:
                            from .body_upload_client import prepare as preupload_body
                            body_bundle=preupload_body(candidate,prepared['assets'],effective_day,slot,budget_seconds=min(60,max(1,int(remaining-470))))
            except Exception as exc:
                print('optional body preparation omitted: '+type(exc).__name__,flush=True)
        if metadata.get('content_origin')=='ai_original' and slot is not None:
            from .official_deadline import seconds_available
            try:remaining=seconds_available(effective_day,slot,datetime.now(ZONE))
            except ValueError:continue
            if remaining<450:continue
        if scheduled_guard:
            from .scheduled_assignments import require_deadline
            require_deadline(day,slot)
        attempt,article=store.reserve(day,slot,candidate['id'])
        if scheduled_guard: article['_scheduled_guard']=(day,slot)
        if body_bundle is not None:article['body_preupload']=body_bundle
        article['localization_revision']=candidate['localization_revision'];article['cover_candidate']=cover
        try:
            result=publish(cfg,store,article,attempt)
            store.published(attempt,result['url'],result['published_day'])
            store.safe_export_reports(result['published_day']);return result['url']
        except UploadFailure as exc:
            try:store.fail_precreate_upload(attempt,str(exc))
            except BaseException:
                store.unknown(attempt);store.safe_export_reports();raise
            store.safe_export_reports()
            if requested_article_id is not None:raise
            continue
        except BaseException:
            store.unknown(attempt);store.safe_export_reports();raise
    raise QualityHold('no article passed localization or upload quality gate')

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'{type(exc).__name__}: {exc}',file=sys.stderr); sys.exit(1)
