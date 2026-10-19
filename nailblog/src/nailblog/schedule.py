"""Pure scheduling arithmetic (TD-5). No I/O, no clock reads: callers pass `now`.

The image-run time depends on the mode; the publish window never does.
"""

import logging
import random
from datetime import date, datetime, time, timedelta, timezone

TIMEZONE_NAME = "Asia/Ho_Chi_Minh"
MODES = ("fixed", "random", "adaptive")
MAX_BACKOFF_ATTEMPT = 16  # 5 min x 2^16 is already far past any deadline; larger n must not overflow


def _vn_zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(TIMEZONE_NAME)
    except Exception:
        logging.getLogger(__name__).warning("tz data for %s unavailable; using fixed UTC+7 fallback", TIMEZONE_NAME)
        # Hosts without tz data (e.g. Windows without the tzdata package). Vietnam has had no
        # DST since 1975, so a fixed UTC+7 offset is exactly equivalent.
        return timezone(timedelta(hours=7), TIMEZONE_NAME)


VN_TZ = _vn_zone()


def parse_hhmm(text):
    if not isinstance(text, str) or len(text) != 5 or text[2] != ":":
        raise ValueError("time must be HH:MM")
    hour, minute = text[:2], text[3:]
    if not (hour.isdigit() and minute.isdigit()) or not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise ValueError("time must be HH:MM")
    return time(int(hour), int(minute))


def _aware(value, name):
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(name + " must be a timezone-aware datetime")
    return value.astimezone(VN_TZ)


def slot_datetime(day, slot):
    """The publish instant for a calendar day in Vietnam time, e.g. (2026-11-01, '06:00')."""
    if type(day) is not date:
        raise ValueError("day must be a date")
    return datetime.combine(day, parse_hhmm(slot), tzinfo=VN_TZ)


def iso_slot(moment):
    """Canonical stored text for a slot instant: Vietnam time, minute precision (sorts lexically)."""
    return _aware(moment, "slot").isoformat(timespec="minutes")


def image_deadline(slot, window_end_hours=3):
    return _aware(slot, "slot") - timedelta(hours=window_end_hours)


def approve_deadline(slot, hours=2):
    return _aware(slot, "slot") - timedelta(hours=hours)


def publish_window(slot, late_minutes=15):
    start = _aware(slot, "slot")
    return start, start + timedelta(minutes=late_minutes)


def is_due_for_publish(now, slot, late_minutes=15):
    start, end = publish_window(slot, late_minutes)
    return start <= _aware(now, "now") <= end


def adaptive_backoff(attempt):
    """2^n x 5 minutes after the n-th consecutive failure (n >= 0)."""
    if type(attempt) is not int or attempt < 0:
        raise ValueError("attempt must be a non-negative integer")
    return timedelta(minutes=5 * 2 ** min(attempt, MAX_BACKOFF_ATTEMPT))


def adaptive_next_attempt(now, attempt, slot, window_end_hours=3):
    """Retry time after a failure; never later than the image deadline."""
    return min(_aware(now, "now") + adaptive_backoff(attempt), image_deadline(slot, window_end_hours))


def plan_image_run(slot, mode, fixed_at, now, seed, *, window_start_hours=24, window_end_hours=3):
    """When to start the image job for the post published at `slot`.

    fixed:    latest `fixed_at` (HH:MM, Vietnam time) not after slot - window_end_hours,
              but never before slot - window_start_hours (clamped to the window start).
    random:   uniform over [slot - window_start_hours, slot - window_end_hours], reproducible
              from `seed` (independent of `now`; a point already in the past means "run now").
    adaptive: max(now, slot - window_start_hours), capped at slot - window_end_hours.
    """
    slot = _aware(slot, "slot")
    now = _aware(now, "now")
    if mode not in MODES:
        raise ValueError("unknown schedule mode")
    if not 0 < window_end_hours < window_start_hours:
        raise ValueError("window hours must satisfy 0 < end < start")
    start = slot - timedelta(hours=window_start_hours)
    end = slot - timedelta(hours=window_end_hours)
    if mode == "fixed":
        at = parse_hhmm(fixed_at)
        candidate = datetime.combine(end.date(), at, tzinfo=VN_TZ)
        if candidate > end:
            candidate = datetime.combine(end.date() - timedelta(days=1), at, tzinfo=VN_TZ)
        return max(candidate, start)
    if mode == "random":
        span = int((end - start).total_seconds())
        return start + timedelta(seconds=random.Random(seed).randint(0, span))
    return min(max(now, start), end)
