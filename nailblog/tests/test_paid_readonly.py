"""The retired API ledger is read-only: real SQLite WAL files, a live writer, no mock of sqlite."""
import contextlib
import hashlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

from nailblog import paid
from nailblog.paid import LedgerReadOnly, LedgerSnapshotUnstable, PaidLedger

UNCERTAIN_MICROS = 124928
SCHEMA = '''
    CREATE TABLE paid_runs(id TEXT PRIMARY KEY, binding TEXT NOT NULL, ceiling INTEGER NOT NULL);
    CREATE TABLE paid_intents(id TEXT PRIMARY KEY, run_id TEXT, provider TEXT NOT NULL, model TEXT NOT NULL,
        reserved INTEGER NOT NULL, charged INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, at REAL NOT NULL,
        metadata TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE usage(at REAL NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL, units INTEGER NOT NULL, usd REAL NOT NULL);
'''


def sha(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def fingerprint(db):
    return tuple(sha(str(db) + suffix) for suffix in ('', '-wal', '-shm'))


def seed(connection):
    connection.executescript(SCHEMA)
    connection.execute("INSERT INTO paid_runs VALUES('run-1','binding',1000000)")
    connection.executemany('INSERT INTO paid_intents(id,run_id,provider,model,reserved,charged,status,at) VALUES(?,?,?,?,?,?,?,?)', [
        ('settled-1', 'run-1', 'gemini', 'img', 50000, 40000, 'settled', 1000.0),
        ('uncertain-1', 'run-1', 'gemini', 'img', UNCERTAIN_MICROS, 0, 'uncertain', 2000.0),
        ('pending-1', 'run-2', 'gemini', 'txt', 300000, 0, 'pending', 3000.0)])
    connection.execute("INSERT INTO usage VALUES(1000.0,'gemini','img',1,0.04)")
    connection.commit()


class LiveWalLedger(unittest.TestCase):
    """A writer is still connected and nothing is checkpointed: the open intent exists only in the -wal file."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'ledger.sqlite3'
        self.writer = sqlite3.connect(self.db)
        self.addCleanup(self.writer.close)
        self.assertEqual(self.writer.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
        self.writer.execute('PRAGMA wal_autocheckpoint=0')
        seed(self.writer)

    def test_the_fixture_really_keeps_the_ledger_in_the_wal(self):
        self.assertTrue(Path(str(self.db) + '-wal').stat().st_size > 0)
        self.assertTrue(Path(str(self.db) + '-shm').exists())
        main_only = Path(self.temp.name) / 'main-only.sqlite3'
        main_only.write_bytes(self.db.read_bytes())
        with contextlib.closing(sqlite3.connect(main_only)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name='paid_intents'").fetchall(), [])

    def test_report_sees_the_uncertain_intent_and_leaves_every_original_file_byte_identical(self):
        before = fingerprint(self.db)
        self.assertTrue(all(before))   # db, -wal and -shm all exist and are hashed
        report = paid.report(str(self.db))
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(report['uncertain_count'], 1)
        self.assertEqual(report['uncertain_usd'], 0.124928)
        by_id = {row['id']: row for row in report['intents']}
        self.assertEqual(set(by_id), {'settled-1', 'uncertain-1', 'pending-1'})
        self.assertEqual(by_id['uncertain-1'], {'id': 'uncertain-1', 'status': 'uncertain', 'reserved': 0.124928, 'charged': 0.0,
                                                'at': 2000.0, 'run_id': 'run-1', 'provider': 'gemini', 'model': 'img'})
        self.assertEqual(by_id['settled-1']['charged'], 0.04)

    def test_reads_through_the_ledger_object_never_change_the_files_either(self):
        before = fingerprint(self.db)
        ledger = PaidLedger(SimpleNamespace(path=str(self.db)), SimpleNamespace(budget=SimpleNamespace(ledger_db_path='')))
        self.assertEqual(ledger.get('uncertain-1')['reserved'], UNCERTAIN_MICROS)
        self.assertIsNone(ledger.get('nope'))
        self.assertEqual([row['id'] for row in ledger.report('run-1')], ['settled-1', 'uncertain-1'])
        self.assertEqual(ledger.settled_usd('run-1'), 0.04)
        self.assertEqual(paid.usage_rows(str(self.db), 0, 10 ** 6), [(1000.0, 'gemini', 'img', 1, 0.04)])
        self.assertEqual(ledger.daily_left_usd(), 0.0)
        self.assertIsNone(ledger.check())
        self.assertEqual(fingerprint(self.db), before)

    def test_every_write_api_is_refused_and_the_ledger_stays_byte_identical(self):
        before = fingerprint(self.db)
        ledger = PaidLedger(SimpleNamespace(path=str(self.db)), SimpleNamespace(budget=SimpleNamespace(ledger_db_path='')))
        for name, call in (('run', lambda: ledger.run('run-9', 'b', 1.0)),
                           ('reserve', lambda: ledger.reserve('new', 'gemini', 'img', 0.1, run_id='run-1')),
                           ('settle', lambda: ledger.settle('pending-1', 1, 0.1)),
                           ('uncertain', lambda: ledger.uncertain('pending-1')),
                           ('reconcile_stale', lambda: ledger.reconcile_stale(10 ** 9, 1)),
                           ('age_out_uncertain', lambda: ledger.age_out_uncertain(10 ** 9, 1)),
                           ('cancel', lambda: paid.cancel_pending_before_dispatch(None, 'paid_intents', 'pending-1', lambda: 0.0))):
            with self.subTest(name), self.assertRaises(LedgerReadOnly):
                call()
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(self.writer.execute("SELECT status FROM paid_intents WHERE id='pending-1'").fetchone()[0], 'pending')


class UnstableSnapshot(LiveWalLedger):
    def test_a_ledger_that_changes_on_every_attempt_raises_instead_of_returning_a_number(self):
        ticks = iter(range(10 ** 6))
        with patch.object(paid, '_stamp', side_effect=lambda path: (next(ticks), 0)):
            with self.assertRaises(LedgerSnapshotUnstable):
                paid.report(str(self.db))
            with self.assertRaises(LedgerSnapshotUnstable):
                paid.usage_rows(str(self.db), 0, 10)

    def test_status_says_the_old_ledger_is_unreadable_while_it_is_written(self):
        from nailblog.commands import execute
        from nailblog.state import State
        config = SimpleNamespace(mode='preview', budget=SimpleNamespace(ledger_db_path=str(self.db)))
        state = State(str(Path(self.temp.name) / 'state.sqlite3'))
        ticks = iter(range(10 ** 6))
        with patch.object(paid, '_stamp', side_effect=lambda path: (next(ticks), 0)):
            reply = execute(state, 'status', (), now=1000.0, config=config)[0]
        self.assertIn('Sổ API cũ: không đọc được (đang ghi)', reply)
        self.assertEqual(execute(state, 'status', (), now=1000.0, config=config)[0].count('uncertain 0.124928 USD'), 1)


class IdleLedgerAndMissingData(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'ledger.sqlite3'

    def test_a_ledger_without_wal_or_shm_gets_none_created_beside_it(self):
        connection = sqlite3.connect(self.db)
        connection.execute('PRAGMA journal_mode=WAL')
        seed(connection)
        connection.close()   # the last connection checkpoints and removes -wal/-shm
        self.assertEqual(sorted(path.name for path in Path(self.temp.name).iterdir()), ['ledger.sqlite3'])
        before = fingerprint(self.db)
        self.assertEqual(paid.report(str(self.db))['uncertain_usd'], 0.124928)
        self.assertEqual(fingerprint(self.db), before)
        self.assertEqual(sorted(path.name for path in Path(self.temp.name).iterdir()), ['ledger.sqlite3'])

    def test_missing_file_or_table_gives_an_empty_report(self):
        empty = {'intents': [], 'uncertain_count': 0, 'uncertain_usd': 0.0}
        self.assertEqual(paid.report(str(self.db)), empty)
        self.assertEqual(paid.report(''), empty)
        self.assertFalse(self.db.exists())
        sqlite3.connect(self.db).close()
        self.assertEqual(paid.report(str(self.db)), empty)
        self.assertEqual(paid.usage_rows(str(self.db), 0, 10), [])

    def test_constructing_the_ledger_does_no_io(self):
        ledger = PaidLedger(SimpleNamespace(path=str(self.db)),
                            SimpleNamespace(budget=SimpleNamespace(ledger_db_path=str(Path(self.temp.name) / 'shared.sqlite3'))))
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])
        self.assertEqual(ledger.path, str(Path(self.temp.name) / 'shared.sqlite3'))
        self.assertIsNone(ledger.get('x'))
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_a_zero_cost_agy_call_stays_a_no_op_but_money_is_refused(self):
        ledger = PaidLedger(SimpleNamespace(path=str(self.db)), SimpleNamespace(budget=SimpleNamespace(ledger_db_path='')))
        ledger.reserve('text-1', 'agy', 'model', 0.0)
        ledger.settle('text-1', 10, 0.0)
        ledger.uncertain('text-1')
        with self.assertRaises(LedgerReadOnly):
            ledger.reserve('text-2', 'gemini', 'model', 0.01)
        with self.assertRaises(LedgerReadOnly):
            ledger.settle('text-2', 1, 0.01)
        self.assertFalse(self.db.exists())


if __name__ == '__main__':
    unittest.main()
