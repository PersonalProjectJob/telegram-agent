"""Read-only view of the retired API spend ledger.

NailBlog is Agy-only: no API image/text call is reserved, settled or charged any more. The old SQLite ledger is kept
only so the Owner can reconcile what was left open (an `uncertain` intent keeps its full bound until the Owner decides).

Nothing here writes to that database. A WAL database cannot be opened `mode=ro` without touching its `-shm`, so every
read works on a private copy: `<db>` and `<db>-wal` are copied (never `-shm`) into a temporary directory and the copy is
opened read-only with `query_only=ON`. The original files are only ever read.
"""
import contextlib
import math
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from .usage import BudgetExceeded

_COPY_ATTEMPTS = 5


class LedgerReadOnly(RuntimeError):
    """The API ledger is retired: every write operation is refused."""


class LedgerSnapshotUnstable(RuntimeError):
    """The ledger kept changing while it was copied; no number is returned (code `ledger_snapshot_unstable`)."""
    code = 'ledger_snapshot_unstable'


class IntentExists(RuntimeError):
    pass


class RunCeilingExceeded(BudgetExceeded):
    """Kept for import compatibility; nothing raises it any more."""


def micros(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid_cost_bound')
    return math.ceil(round(value * 1_000_000, 9))


def _stamp(path):
    try:
        info = Path(path).stat()
    except OSError:
        return None
    return info.st_size, info.st_mtime_ns


@contextlib.contextmanager
def _snapshot(path):
    """Yield a read-only connection on a private copy of the ledger, or None when the file does not exist."""
    source = Path(path)
    if not source.is_file():
        yield None
        return
    wal = Path(str(source) + '-wal')
    with tempfile.TemporaryDirectory(prefix='nailblog-ledger-ro-') as directory:
        target = Path(directory) / source.name
        for _ in range(_COPY_ATTEMPTS):
            before = (_stamp(source), _stamp(wal))
            shutil.copyfile(source, target)
            copied_wal = Path(str(target) + '-wal')
            if wal.is_file():
                shutil.copyfile(wal, copied_wal)
            elif copied_wal.exists():
                copied_wal.unlink()
            if before == (_stamp(source), _stamp(wal)):   # nobody checkpointed/appended while copying
                break
        else:
            raise LedgerSnapshotUnstable('ledger_snapshot_unstable')
        db = sqlite3.connect(target.as_uri() + '?mode=ro', uri=True)
        try:
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA query_only=ON')
            yield db
        finally:
            db.close()


def _tables(db):
    return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def report(path):
    """Intents of the retired ledger plus what is still open: {'intents', 'uncertain_count', 'uncertain_usd'}.
    Missing file or missing table gives an empty report."""
    empty = {'intents': [], 'uncertain_count': 0, 'uncertain_usd': 0.0}
    if not path:
        return empty
    with _snapshot(path) as db:
        if db is None or 'paid_intents' not in _tables(db):
            return empty
        rows = db.execute('SELECT * FROM paid_intents ORDER BY at,id').fetchall()
    intents = [{'id': row['id'], 'status': row['status'], 'reserved': row['reserved'] / 1_000_000,
                'charged': row['charged'] / 1_000_000, 'at': row['at'], 'run_id': row['run_id'],
                'provider': row['provider'], 'model': row['model']} for row in rows]
    open_micros = sum(row['reserved'] for row in rows if row['status'] == 'uncertain')
    return {'intents': intents, 'uncertain_count': sum(row['status'] == 'uncertain' for row in rows),
            'uncertain_usd': open_micros / 1_000_000}


def usage_rows(path, start, end):
    """(at, provider, model, units, usd) rows of the retired ledger's `usage` table in [start, end)."""
    if not path:
        return []
    with _snapshot(path) as db:
        if db is None or 'usage' not in _tables(db):
            return []
        return [tuple(row) for row in db.execute(
            'SELECT at,provider,model,units,usd FROM usage WHERE at>=? AND at<? ORDER BY at', (start, end))]


def cancel_pending_before_dispatch(state, table, intent, clock):
    raise LedgerReadOnly('api_ledger_retired')


def local_ledger_problem(state, config, clock=time.time):
    return None


class PaidLedger:
    """Compatibility stub: construction does no I/O, reads use a snapshot, every write raises LedgerReadOnly."""

    def __init__(self, state, config, clock=time.time):
        shared = getattr(getattr(config, 'budget', None), 'ledger_db_path', '')
        self.state, self.config, self.clock = state, config, clock
        self.path = str(shared) if shared else str(getattr(state, 'path', ''))

    def _refuse(self, *args, **kwargs):
        raise LedgerReadOnly('api_ledger_retired')

    run = reconcile_stale = age_out_uncertain = _refuse

    # Agy calls are not billed (bound 0.0) and have no row here: nothing to book, so those calls stay no-ops until
    # service.model_call stops asking the ledger (S4). Anything that would put money on, or change, a ledger row raises.
    def reserve(self, intent, provider, model, bound, *, run_id=None):
        if micros(bound) > 0:
            self._refuse()

    def settle(self, intent, units, usd, metadata=None):
        if micros(usd) > 0 or self.get(intent) is not None:
            self._refuse()

    def uncertain(self, intent):
        if self.get(intent) is not None:
            self._refuse()

    def check(self):
        return None

    def daily_left_usd(self):
        return 0.0

    def settled_usd(self, run_id):
        return sum(row['charged'] for row in self.report(run_id) if row['status'] == 'settled') / 1_000_000

    def get(self, intent):
        with _snapshot(self.path) as db:
            if db is None or 'paid_intents' not in _tables(db):
                return None
            row = db.execute('SELECT * FROM paid_intents WHERE id=?', (intent,)).fetchone()
            return dict(row) if row else None

    def report(self, run_id):
        with _snapshot(self.path) as db:
            if db is None or 'paid_intents' not in _tables(db):
                return []
            return [dict(row) for row in db.execute('SELECT * FROM paid_intents WHERE run_id=? ORDER BY at,id', (run_id,))]
