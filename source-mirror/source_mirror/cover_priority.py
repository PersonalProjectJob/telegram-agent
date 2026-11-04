"""Best-effort body yield to mandatory cover producers; no shared lock claimed."""
from pathlib import Path

COVER_CGROUPS=(Path('/sys/fs/cgroup/system.slice/nailhub-source-mirror-scratch-cover.service/cgroup.procs'),
               Path('/sys/fs/cgroup/system.slice/nailhub-source-mirror-official-cover.service/cgroup.procs'))

def cover_busy(paths=COVER_CGROUPS):
    for path in paths:
        try:
            if path.is_symlink() or not path.is_file():return True
            pids=[int(x) for x in path.read_text().split()]
            if len(pids)!=1 or pids[0]<=0:return True
        except (OSError,ValueError):return True
    return False
