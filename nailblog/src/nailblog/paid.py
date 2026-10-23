"""Atomic shared spend reservations. Unknown requests retain their full bound indefinitely."""
import math
import json
import re
import time

from .usage import BudgetExceeded, day_bounds


class IntentExists(RuntimeError):
    pass


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


def micros(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        raise ValueError('invalid_cost_bound')
    return math.ceil(round(value * 1_000_000, 9))


class PaidLedger:
    def __init__(self, state, config, clock=time.time):
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
                    raise BudgetExceeded('run USD reservation would exceed ceiling')
            db.execute('INSERT INTO paid_intents(id,run_id,provider,model,reserved,status,at) VALUES(?,?,?,?,?,?,?)',
                       (intent,run_id,provider,model,amount,'pending',self.clock()))

    def uncertain(self, intent):
        with self.state.connection() as db:
            db.execute("UPDATE paid_intents SET status='uncertain' WHERE id=? AND status='pending'", (intent,))

    def reconcile_stale(self, now, older_than_s):
        """A pending intent older than `older_than_s` is presumed crashed mid-call: it becomes uncertain and keeps its
        full reservation in the daily cap. Returns the number of intents changed."""
        with self.state.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return db.execute("UPDATE paid_intents SET status='uncertain' WHERE status='pending' AND at<?",
                              (now - older_than_s,)).rowcount

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
