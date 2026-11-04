"""Cooperative time budget for official scheduled work; not a hard watchdog."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime,timedelta
import time
from zoneinfo import ZoneInfo

ZONE=ZoneInfo('Asia/Ho_Chi_Minh')
SLOTS=('08:00','12:00','16:00','20:00')
_deadline=ContextVar('official_deadline',default=None)

class ScheduleUnavailable(ValueError):
    pass


def seconds_available(day,slot,now):
    if slot not in SLOTS or now.tzinfo is None: raise ValueError('official schedule unavailable')
    now=now.astimezone(ZONE)
    if day!=now.date().isoformat(): raise ValueError('official schedule day changed')
    hour,minute=map(int,slot.split(':'));start=now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if now<start:raise ValueError('official slot has not started')
    following=SLOTS[(SLOTS.index(slot)+1)%len(SLOTS)]
    next_hour,next_minute=map(int,following.split(':'))
    next_slot=start.replace(hour=next_hour,minute=next_minute)
    if following<=slot:next_slot+=timedelta(days=1)
    midnight=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    return (min(next_slot,midnight)-timedelta(minutes=5)-now).total_seconds()


@contextmanager
def scheduled(day,slot):
    try: seconds=seconds_available(day,slot,datetime.now(ZONE))
    except ValueError as exc: raise ScheduleUnavailable(str(exc)) from exc
    if seconds<750: raise ScheduleUnavailable('insufficient official time budget')
    token=_deadline.set(time.monotonic()+seconds)
    try:yield
    finally:_deadline.reset(token)

def maintenance_seconds(now):
    if now.tzinfo is None:raise ScheduleUnavailable('timezone required')
    now=now.astimezone(ZONE)
    starts=[now.replace(hour=int(slot[:2]),minute=0,second=0,microsecond=0) for slot in SLOTS]
    previous=[value for value in starts if value<=now]
    if previous and now<previous[-1]+timedelta(hours=1):raise ScheduleUnavailable('official preparation near active slot')
    future=[value for value in starts if value>now]
    midnight=(now+timedelta(days=1)).replace(hour=0,minute=0,second=0,microsecond=0)
    cutoff=min(future[0] if future else midnight,midnight)-timedelta(minutes=20)
    seconds=(cutoff-now).total_seconds()
    if seconds<900:raise ScheduleUnavailable('official preparation budget insufficient')
    return seconds

@contextmanager
def maintenance():
    """Bound preparation outside slot windows; still not a watchdog."""
    token=_deadline.set(time.monotonic()+maintenance_seconds(datetime.now(ZONE)))
    try:yield
    finally:_deadline.reset(token)


def require(seconds,*,active=False):
    deadline=_deadline.get()
    if deadline is None:
        if active:raise ValueError('official schedule budget required')
        return
    if time.monotonic()+seconds>=deadline:raise ValueError('official time budget exhausted')
