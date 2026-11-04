"""Run URL jobs in a killable process group with a conservative slot cutoff."""
from datetime import datetime, timedelta
import os
import signal
import subprocess
import sys
from .store import ZONE


def budget_seconds(slots, now=None):
    now=now or datetime.now(ZONE)
    if now.tzinfo is None: raise ValueError('timezone-aware time required')
    targets=[]
    for day_offset in (0,1):
        day=now.date()+timedelta(days=day_offset)
        for slot in slots:
            parsed=datetime.strptime(slot,'%H:%M').time()
            target=datetime.combine(day,parsed,tzinfo=ZONE)
            if target>now: targets.append(target)
    if not targets: raise ValueError('no slot deadline')
    return max(0,min(900,(min(targets)-now).total_seconds()-120))


def dispatch(config_path, slots, *, now=None):
    if os.name!='posix' or not hasattr(os,'killpg'): raise ValueError('process-group isolation required')
    budget=budget_seconds(slots,now)
    # Two model calls can each take several minutes; do not start a job that
    # would predictably be cut off just to protect the next scheduled slot.
    if budget<600: return 'too_close_to_slot'
    proc=subprocess.Popen([sys.executable,'-m','source_mirror','--config',config_path,'command-once'],
                          start_new_session=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        proc.wait(timeout=budget)
    except subprocess.TimeoutExpired:
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait(timeout=5)
        return 'timeout_unknown'
    return 'completed' if proc.returncode==0 else 'held'
