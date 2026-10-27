"""Atomic shared spend reservations. Unknown requests retain their full bound indefinitely."""
import math
import json
import re
import time

from .usage import BudgetExceeded, day_bounds


class IntentExists(RuntimeError):
    pass


class RunCeilingExceeded(BudgetExceeded):
    """The run's own USD ceiling would be exceeded (not the daily cap)."""


def cancel_pending_before_dispatch(state, table, intent, clock):
    """Archive/release an intent proven not dispatched by its owning call boundary.

    Never use for timeout/cancellation after transport started. Unknown/settled
    intents are deliberately ineligible. History retains the original reservation.
    """
    if table not in ('paid_intents', 'cf_intents'):
        raise ValueError('invalid_intent_table')
    with state.connection() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM '+table+' WHERE id=?', (intent,)).fetchone()
        if not row:
            return False
        if row['status'] != 'pending':
            raise ValueError('only_undispatched_pending_intent_can_be_cancelled')
        db.execute('''CREATE TABLE IF NOT EXISTS image_dispatch_cancellations(
            sequence INTEGER PRIMARY KEY, ledger_table TEXT NOT NULL, intent TEXT NOT NULL,
            at REAL NOT NULL, reason TEXT NOT NULL, reservation TEXT NOT NULL)''')
        db.execute('INSERT INTO image_dispatch_cancellations(ledger_table,intent,at,reason,reservation) VALUES(?,?,?,?,?)',
                   (table, intent, clock(), 'workflow_paused_before_dispatch', json.dumps(dict(row),sort_keys=True)))
        db.execute('DELETE FROM '+table+' WHERE id=? AND status=?', (intent,'pending'))
        return True


_SHARED_STATES = {}


def _shared_state(path):
    """One State (schema script run once) per shared ledger path for the life of the process."""
    from .state import State
    found = _SHARED_STATES.get(path)
    if found is None:
        found = _SHARED_STATES[path] = State(path)
    return found


def local_ledger_problem(state, config, clock=time.time):
    """`local_ledger_not_empty` when `budget.ledger_db_path` is set but the root's OWN database still holds unsettled
    paid intents or today's (Vietnam day) spend: switching to the shared ledger would silently forget them. Else None.
    Do the cutover on a fresh Vietnam day or after everything is settled."""
    shared = getattr(getattr(config, 'budget', None), 'ledger_db_path', '')
    if not shared or str(shared) == str(state.path):
        return None
    start, end = day_bounds(clock())
    with state.connection() as db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'paid_intents' in names and db.execute(
                "SELECT 1 FROM paid_intents WHERE status!='settled' LIMIT 1").fetchone():
            return 'local_ledger_not_empty'
        if 'usage' in names and (db.execute('SELECT COALESCE(SUM(usd),0) FROM usage WHERE at>=? AND at<?',
                                            (start, end)).fetchone()[0] or 0) > 0:
            return 'local_ledger_not_empty'
    return None


def micros(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid_cost_bound')
    return math.ceil(round(value * 1_000_000, 9))


class PaidLedger:
    def __init__(self, state, config, clock=time.time):
        shared = getattr(getattr(config, 'budget', None), 'ledger_db_path', '')
        if shared:
            # One daily cap across state roots: every paid reservation/settlement lives in the shared DB, whatever
            # State the caller passed. Every config that points here must use the same budget.daily_usd.
            state = _shared_state(shared)
        self.state, self.config, self.clock = state, config, clock
        with state.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS paid_runs(id TEXT PRIMARY KEY, binding TEXT NOT NULL, ceiling INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS paid_intents(id TEXT PRIMARY KEY, run_id TEXT, provider TEXT NOT NULL,
                    model TEXT NOT NULL, reserved INTEGER NOT NULL, charged INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL, at REAL NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            if 'metadata' not in {row[1] for row in db.execute('PRAGMA table_info(paid_intents)')}:
                db.execute("ALTER TABLE paid_intents ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'")

    def run(self, run_id, binding, ceiling):
        if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', run_id):
            raise ValueError('invalid_run_id')
        amount = micros(ceiling)
        if amount <= 0:
            raise ValueError('invalid_run_ceiling')
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT OR IGNORE INTO paid_runs VALUES(?,?,?)', (run_id, binding, amount))
            row = db.execute('SELECT * FROM paid_runs WHERE id=?', (run_id,)).fetchone()
            if row['binding'] != binding or row['ceiling'] != amount:
                raise ValueError('run_binding_or_ceiling_changed')

    def _daily(self, db):
        start, end = day_bounds(self.clock())
        spent = db.execute('SELECT COALESCE(SUM(usd),0) FROM usage WHERE at>=? AND at<?', (start,end)).fetchone()[0]
        active = db.execute("SELECT COALESCE(SUM(reserved),0) FROM paid_intents WHERE status!='settled'").fetchone()[0]
        return micros(spent) + active

    def daily_left_usd(self):
        """USD still available today: cap minus settled spend minus every open reservation (never below 0)."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return max(0, micros(self.config.budget.daily_usd) - self._daily(db)) / 1_000_000

    def settled_usd(self, run_id):
        """USD actually charged by the settled intents of `run_id`."""
        return sum(row['charged'] for row in self.report(run_id) if row['status'] == 'settled') / 1_000_000

    def check(self):
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._daily(db) >= micros(self.config.budget.daily_usd):
                raise BudgetExceeded('daily USD budget including reservations reached')

    def reserve(self, intent, provider, model, bound, *, run_id=None):
        amount = micros(bound)
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM paid_intents WHERE id=?', (intent,)).fetchone():
                raise IntentExists('paid_intent_already_exists')
            if self._daily(db) + amount > micros(self.config.budget.daily_usd):
                raise BudgetExceeded('daily USD reservation would exceed ceiling')
            if run_id:
                row = db.execute('SELECT ceiling FROM paid_runs WHERE id=?', (run_id,)).fetchone()
                used = db.execute("SELECT COALESCE(SUM(CASE WHEN status='settled' THEN charged ELSE reserved END),0) "
                                  'FROM paid_intents WHERE run_id=?', (run_id,)).fetchone()[0]
                if not row or used + amount > row['ceiling']:
                    raise RunCeilingExceeded('run USD reservation would exceed ceiling')
            db.execute('INSERT INTO paid_intents(id,run_id,provider,model,reserved,status,at) VALUES(?,?,?,?,?,?,?)',
                       (intent,run_id,provider,model,amount,'pending',self.clock()))

    def uncertain(self, intent):
        with self.state.connection() as db:
            db.execute("UPDATE paid_intents SET status='uncertain' WHERE id=? AND status='pending'", (intent,))

    def reconcile_stale(self, now, older_than_s, *, run_id=None, run_prefix=None):
        """A pending intent older than `older_than_s` is presumed crashed mid-call: it becomes uncertain and keeps its
        full reservation in the daily cap. `run_id` / `run_prefix` limit it to the caller's own run(s): on a shared
        ledger another process's in-flight call must never be touched. No filter = every pending intent (legacy).
        Returns the number of intents changed."""
        sql, args = "UPDATE paid_intents SET status='uncertain' WHERE status='pending' AND at<?", [now - older_than_s]
        if run_id is not None:
            sql += ' AND run_id=?'
            args.append(run_id)
        elif run_prefix is not None:
            sql += ' AND substr(run_id,1,?)=?'
            args += [len(run_prefix), run_prefix]
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return db.execute(sql, args).rowcount

    def age_out_uncertain(self, now, after_s=86400):
        """An uncertain intent older than `after_s` is settled at its FULL reserved amount, booked to the Vietnam day
        of the intent's own original `at`. Never lowers a charge. Returns the number of intents settled."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT * FROM paid_intents WHERE status='uncertain' AND at<? ORDER BY at,id",
                              (now - after_s,)).fetchall()
            for row in rows:
                try:
                    metadata = json.loads(row['metadata'] or '{}')
                except ValueError:
                    metadata = {}
                if not isinstance(metadata, dict):
                    metadata = {}
                metadata['assumed_full_bound'] = True
                db.execute('INSERT INTO usage VALUES(?,?,?,?,?)',
                           (row['at'], row['provider'], row['model'], 0, row['reserved'] / 1_000_000))
                db.execute("UPDATE paid_intents SET status='settled',charged=?,metadata=? WHERE id=?",
                           (row['reserved'], json.dumps(metadata, sort_keys=True), row['id']))
            return len(rows)

    def settle(self, intent, units, usd, metadata=None):
        charged = micros(usd)
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM paid_intents WHERE id=?', (intent,)).fetchone()
            if not row or row['status'] != 'pending' or charged > row['reserved']:
                raise ValueError('paid_settlement_invalid_or_bound_exceeded')
            db.execute('INSERT INTO usage VALUES(?,?,?,?,?)',
                       (self.clock(),row['provider'],row['model'],units,charged/1_000_000))
            db.execute("UPDATE paid_intents SET status='settled',charged=?,metadata=? WHERE id=?",
                       (charged,json.dumps(metadata or {},sort_keys=True),intent))

    def get(self, intent):
        with self.state.connection() as db:
            row = db.execute('SELECT * FROM paid_intents WHERE id=?', (intent,)).fetchone()
            return dict(row) if row else None

    def report(self, run_id):
        with self.state.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM paid_intents WHERE run_id=? ORDER BY at,id', (run_id,))]
