"""Production preflight checks that never authorize a Create by themselves."""
from datetime import datetime
from zoneinfo import ZoneInfo
from .official_deadline import SLOTS

ZONE=ZoneInfo('Asia/Ho_Chi_Minh')

class CandidateTiming(ValueError):
    """The immutable candidate time does not fit this reservation slot."""

def check_publish_time(value,day,slot,now):
    if slot not in SLOTS or not isinstance(now,datetime) or now.tzinfo is None: raise ValueError('official slot unavailable')
    try: published=datetime.fromisoformat(value.replace('Z','+00:00'))
    except (AttributeError,ValueError): raise CandidateTiming('official publication time invalid') from None
    if published.tzinfo is None: raise CandidateTiming('official publication time requires timezone')
    local_now=now.astimezone(ZONE);local_published=published.astimezone(ZONE)
    if day!=local_now.date().isoformat():raise ValueError('official reservation day changed')
    if local_published.date().isoformat()!=day or published>now:
        raise CandidateTiming('official publication day or future time invalid')
    hour,minute=map(int,slot.split(':'))
    slot_start=local_now.replace(hour=hour,minute=minute,second=0,microsecond=0)
    if local_now<slot_start:raise ValueError('official slot has not started')
    if local_published<slot_start:raise CandidateTiming('official publication before slot')
