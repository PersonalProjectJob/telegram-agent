"""Scheduler branch for trusted official candidates; opt-in stays disabled."""
from pathlib import Path
from . import official_store
from .official_publish_policy import load as load_publish_policy
from .official_deadline import scheduled, ScheduleUnavailable


def try_once(store,day,slot):
    if official_store.OFFICIAL_RESERVATION_ENABLED is not True: return False
    budget=scheduled(day,slot)
    try: budget.__enter__()
    except ScheduleUnavailable: return False
    try: return _try_once(store,day,slot)
    finally: budget.__exit__(None,None,None)


def _try_once(store,day,slot):
    try: policy=load_publish_policy()
    except (ValueError,OSError): return False
    if policy.get('enabled') is not True: return False
    table=store.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='official_candidates'").fetchone()
    if not table:return False
    from .official_sources import SOURCES
    urls=[source['url'] for source in SOURCES if source['id'] in policy.get('source_ids',[])]
    if not urls:return False
    placeholders=','.join('?' for _ in urls)
    candidates=store.db.execute("SELECT a.id,o.evidence_path FROM official_candidates o JOIN articles a ON a.id=o.article_id WHERE a.state='official_ready' AND a.source_url IN ("+placeholders+") ORDER BY a.created_at,a.id LIMIT 8",urls).fetchall()
    selected=[]
    for candidate in candidates:
        try: relative=Path(candidate['evidence_path']).relative_to(store.root/'official-auto-evidence')
        except ValueError: continue
        if len(relative.parts)==3 and relative.parts[0] in policy.get('source_ids',[]): selected.append(candidate['id'])
    if not selected:return False
    # Resolve the built-in publisher before any durable publishing intent.
    from .official_publisher import publish,available
    if available() is not True: return False
    from .official_reservation_preflight import check_candidate
    from .official_duplicate_scan import DuplicateSource
    from .official_auto_eligibility import SourceChanged
    from .official_prepost import CandidateTiming
    from urllib.error import HTTPError
    for candidate in selected:
        try:
            check_candidate(store,candidate,day,slot)
            attempt,article=official_store.reserve(store,candidate,day,slot)
            break
        except HTTPError as error:
            if error.code==403:continue  # This path precedes the reservation commit.
            return False
        except (official_store.CandidateUnavailable,DuplicateSource,SourceChanged,CandidateTiming):
            continue
        except (ValueError,OSError): return False
    else:return False
    try:
        result=publish(store,article,attempt)
        if not isinstance(result,dict) or result.get('verified') is not True: raise ValueError('official publication not verified')
        store.published(attempt,result['url'],result['published_day']);store.safe_export_reports(result['published_day'])
        return result['url']
    except BaseException:
        store.unknown(attempt);store.safe_export_reports();raise
