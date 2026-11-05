"""Candidate only: explicit nail assignments; caller already holds worker_lock."""
import json
import os
import re
import stat
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path

PATH = Path('/etc/nailhub-source-mirror-schedule.json')
MARKER = Path('/etc/nailhub-source-mirror-schedule.enabled')
MAX_BYTES = 65536

class ScheduleInvalid(ValueError):
    pass

def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ScheduleInvalid('duplicate JSON key')
        result[key] = value
    return result

def parse(raw):
    try:
        data = json.loads(raw, object_pairs_hook=_object)
        if not isinstance(data, dict) or set(data) != {'version', 'assignments'} or type(data['version']) is not int or data['version'] != 1 or not isinstance(data['assignments'], list):
            raise ScheduleInvalid('invalid schedule schema')
        result = {}
        ids = set()
        for entry in data['assignments']:
            if not isinstance(entry, dict) or set(entry) != {'day', 'slot', 'article_id'}:
                raise ScheduleInvalid('invalid assignment')
            day, slot, aid = entry['day'], entry['slot'], entry['article_id']
            if not isinstance(day, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
                raise ScheduleInvalid('invalid day')
            if date.fromisoformat(day).isoformat() != day or slot != '12:00' or not isinstance(aid, str) or not re.fullmatch(r'[a-f0-9]{64}', aid):
                raise ScheduleInvalid('invalid assignment values')
            key = (day, slot)
            if key in result or aid in ids:
                raise ScheduleInvalid('duplicate slot or article')
            result[key] = aid
            ids.add(aid)
        return result
    except (ValueError, TypeError, UnicodeDecodeError) as exc:
        if isinstance(exc, ScheduleInvalid):
            raise
        raise ScheduleInvalid('invalid schedule') from exc

def _trusted_read(path, *, missing_ok=False):
    if os.name != 'posix' or not hasattr(os, 'O_NOFOLLOW'):
        raise ScheduleInvalid('schedule requires POSIX no-follow support')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError as exc:
        if missing_ok: return None
        raise ScheduleInvalid('enabled schedule file missing') from exc
    except OSError as exc:
        raise ScheduleInvalid('schedule unavailable') from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > MAX_BYTES:
            raise ScheduleInvalid('untrusted schedule file')
        with os.fdopen(fd, 'rb', closefd=False) as file:
            raw = file.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ScheduleInvalid('schedule too large')
        return raw
    finally:
        os.close(fd)

def load(path=PATH, marker=MARKER):
    enabled = _trusted_read(marker)
    if enabled != b'enabled-v1\n': raise ScheduleInvalid('invalid schedule marker')
    return parse(_trusted_read(path))

def require_deadline(day, slot, *, clock=None):
    now = (clock or (lambda: datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))))()
    if now.tzinfo is None: raise ScheduleInvalid('clock requires timezone')
    local = now.astimezone(ZoneInfo('Asia/Ho_Chi_Minh'))
    windows={'08:00':('08:00','11:55'),'12:00':('12:00','15:55'),'16:00':('16:00','19:55'),'20:00':('20:00','23:55')}
    if slot not in windows or local.date().isoformat() != day or not (windows[slot][0] <= local.strftime('%H:%M') < windows[slot][1]):
        raise ScheduleInvalid('assigned slot window expired')

MAX_FALLBACKS = 3

def require_manual_unpinned(article_id,day,*,loader=load):
    mapping=loader()
    if any(aid==article_id and assigned_day>=day for (assigned_day,_),aid in mapping.items()):
        raise ScheduleInvalid('manual article is assigned to a current or future slot')

def run_slot(cfg, store, day, slot, once, *, before=lambda: None, loader=load, clock=None):
    """Never creates intent: delegates to existing guarded once under caller's lock."""
    mapping = loader()
    aid = mapping.get((day, slot))
    if aid is not None:require_deadline(day,slot,clock=clock)
    before()
    if aid is not None:require_deadline(day,slot,clock=clock)
    from .localization import QualityHold
    from .topics import candidates

    def no_attempts_since(baseline):
        if store.db.execute("SELECT 1 FROM attempts WHERE state IN ('intent','unknown') LIMIT 1").fetchone():
            raise ScheduleInvalid('fallback blocked by unresolved attempt')
        if store.db.execute('SELECT 1 FROM slots WHERE day=? AND slot=?',(day,slot)).fetchone():
            raise ScheduleInvalid('fallback blocked by reserved slot')
        current=tuple(store.db.execute('SELECT count(*), COALESCE(MAX(id),0) FROM attempts').fetchone())
        if current!=baseline:
            raise ScheduleInvalid('fallback blocked by new attempt')

    baseline=tuple(store.db.execute('SELECT count(*), COALESCE(MAX(id),0) FROM attempts').fetchone())
    attempted=set()
    try:
        if aid is None:return once(cfg,store,day,slot,attempted_ids=attempted)
        return once(cfg, store, day, slot, requested_article_id=aid, scheduled_guard=True,attempted_ids=attempted)
    except QualityHold:
        no_attempts_since(baseline)
    require_deadline(day,slot,clock=clock)
    # Never consume any article assigned to a scheduled slot, including future
    # pins. The existing candidate selector enforces topic and seasonal policy.
    pinned=set(mapping.values())
    alternatives=[x['id'] for x in candidates(store,day,slot,limit=None,config=cfg) if x['id'] not in pinned and x['id'] not in attempted][:MAX_FALLBACKS]
    for candidate in alternatives:
        no_attempts_since(baseline)
        require_deadline(day,slot,clock=clock)
        try:
            return once(cfg, store, day, slot, requested_article_id=candidate, scheduled_guard=True,attempted_ids=attempted)
        except QualityHold:
            no_attempts_since(baseline)
    raise QualityHold('scheduled flow and bounded alternatives held')
