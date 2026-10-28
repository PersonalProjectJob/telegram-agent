"""R7b: `python -m nailblog autoflow-requeue --run <id> --slot <ISO>` reopens a run held `text_quality_held`.

Real: sqlite State, OnePost, ApprovalStore, the quality gate, AdminStore, Service.publish_pass and the engine.
Fakes: Agy `ask`, image pipeline, Admin browser, Telegram client, frozen clock (test_autoflow_e2e). No network.
The "new, looser threshold" of R7 is simulated by patching a quality constant while the text is written (the run is
held on `kw_density`) and un-patching it for the requeue; `quality.py` itself is never edited here.
"""
import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from nailblog import __main__ as entry
from nailblog import autoflow
from nailblog.admin_state import AdminStore
from nailblog.autoflow import AutoflowRuns, RequeueRefused, requeue_held_text
from nailblog.config import ScheduleConfig
from nailblog.schedule import VN_TZ
from tests import fakes
from tests.test_autoflow_e2e import DAY, SLOTS
from tests.test_r6b_auto_approval import AutoBase, staging



@contextlib.contextmanager
def strict():
    """The OLD, stricter threshold (every article `fixable` on kw_density); the Agy repair changes nothing."""
    with patch('nailblog.quality.DENSITY_LOW', 99.0),             patch('nailblog.writer.repair_agy', side_effect=lambda ask, article, issues, **kw: (article, [])):
        yield


def slot_text(now, *, minutes):
    """ISO slot (minute precision, +07:00) at least `minutes` from `now`."""
    moment = datetime.fromtimestamp(now, VN_TZ) + timedelta(minutes=minutes)
    moment = moment.replace(second=0, microsecond=0) + timedelta(minutes=1)
    return moment.isoformat(timespec='minutes')


class RequeueBase(AutoBase):
    async def held_ready(self, *, slots=('06:00',), window=1, approve=1):
        """A harness whose runs are all held `text_quality_held` (kw_density) and a bot that is paused."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        overrides = {**staging(str(Path(temp.name) / 'runs')),
                     'schedule': ScheduleConfig(window_end_hours=window, approve_deadline_hours=approve)}
        h = self.harness(slots=slots, overrides=overrides)
        h.pipeline.cover = (1376, 768)
        with strict():
            await h.until(lambda: h.stages() == ['held'] * len(slots))
        self.assertEqual({r['terminal_reason'] for r in h.rows()}, {'text_quality_held'})
        h.state.set_paused(True)
        return h

    @staticmethod
    def requeue(h, slot, run=None, **kwargs):
        run = run or h.rows()[0]['run_id']
        return requeue_held_text(h.service, run, slot, now=h.now, **kwargs)

    def refused(self, h, code, slot, run=None):
        with self.assertRaises(RequeueRefused) as caught:
            self.requeue(h, slot, run)
        self.assertEqual(caught.exception.code, code)

    @staticmethod
    def dump(h):
        db = sqlite3.connect(h.state.path)
        try:
            return '\n'.join(db.iterdump())
        finally:
            db.close()


class RequeueFlowTests(RequeueBase):
    async def test_held_run_is_requeued_then_images_approval_draft_and_one_create_one_patch(self):
        h = await self.held_ready()
        self.assertEqual((h.counts(), h.pipeline.runs, h.posts(), h.ask.calls), ((0, 0), [], [], ['nailblog-write']))
        asks_before = list(h.ask.calls)
        run_id = h.rows()[0]['run_id']
        old_slot = h.rows()[0]['slot_at']
        new_slot = slot_text(h.now, minutes=70)
        report = self.requeue(h, new_slot)
        self.assertEqual((report['old_slot'], report['rescored_quality']['verdict']), (old_slot, 'pass'))
        row = AutoflowRuns(h.state).get(run_id)
        self.assertEqual((row['stage'], row['slot_at'], row['terminal_reason']), ('new', new_slot, ''))
        self.assertEqual(h.state.get_topic(row['topic_id'])['slot_at'], new_slot)
        with h.state.connection() as db:
            phase, document = db.execute('SELECT phase,document FROM onepost_runs WHERE run_id=?', (run_id,)).fetchone()
        saved = json.loads(document)
        self.assertEqual((phase, saved['quality'], saved['slot_at']), ('text_ready', {'verdict': 'pass', 'issues': []}, new_slot))
        audit = json.loads(row['ctx'])['requeues'][0]
        self.assertEqual((audit['requeued_by'], audit['old_slot'], audit['new_slot'], audit['rescored_quality']['verdict']),
                         ('operator', old_slot, new_slot, 'pass'))
        self.assertEqual(audit['previous_quality']['verdict'], 'fixable')
        self.assertIn('kw_density', audit['previous_quality']['issues'])
        h.state.set_paused(False)
        await h.until(lambda: h.stages() == ['scheduled'])
        self.assertEqual(h.counts(), (1, 0))
        self.assertEqual([p['state'] for p in h.posts()], ['scheduled'])
        self.assertEqual(h.posts()[0]['slot_at'], new_slot)
        rows = self.approval_rows(h)
        self.assertEqual(sorted((r['kind'], r['status'], r['decided_by']) for r in rows),
                         [('draft', 'approved', 'auto'), ('images', 'approved', 'auto')])
        h.now = datetime.fromisoformat(new_slot).timestamp() + 60
        await h.tick(0)
        await h.tick(60)
        for _ in range(3):
            await h.tick(300)
        self.assertEqual((h.stages(), [p['state'] for p in h.posts()]), (['published'], ['published']))
        self.assertEqual(h.counts(), (1, 1))                      # exactly one Create and one PATCH
        self.assertEqual(h.ask.calls, asks_before)                # no new text job
        self.assertEqual(len(h.pipeline.runs), 1)
        self.assertEqual(AutoflowRuns(h.state).get(run_id)['run_id'], run_id)
        self.assertEqual(len(AutoflowRuns(h.state).rows()), 1)    # same run id, no second run for the topic

    async def test_cli_entry_prints_the_audit_and_a_refusal_exits_nonzero(self):
        h = await self.held_ready()
        run_id = h.rows()[0]['run_id']
        slot = slot_text(h.now, minutes=70)
        args = type('Args', (), {'run': run_id, 'slot': slot, 'dry_run': False})()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            await entry.execute('autoflow-requeue', h.config, args, entry.Runtime(now=h.now))
        text = out.getvalue()
        self.assertIn('requeued_by=operator', text)
        self.assertIn('verdict=pass', text)
        self.assertEqual(h.rows()[0]['stage'], 'new')
        with patch('sys.stderr'), self.assertRaises(SystemExit) as caught:
            entry.main(['autoflow-requeue'])
        self.assertEqual(caught.exception.code, 2)
        with patch('sys.stderr'), self.assertRaises(SystemExit) as caught:
            entry.main(['status', '--run', 'x'])
        self.assertEqual(caught.exception.code, 2)


class RequeueRefusalTests(RequeueBase):
    async def test_refused_when_the_bot_is_not_paused(self):
        h = await self.held_ready()
        h.state.set_paused(False)
        before = self.dump(h)
        self.refused(h, 'requeue_requires_pause', slot_text(h.now, minutes=70))
        self.assertEqual(self.dump(h), before)

    async def test_refused_when_the_run_is_not_held(self):
        h = self.auto(slots=('06:00',))
        await h.tick(60)
        self.assertNotEqual(h.stages(), ['held'])
        h.state.set_paused(True)
        self.refused(h, 'run_not_text_held', slot_text(h.now, minutes=300))

    async def test_refused_when_the_rescore_is_still_fixable_with_the_old_threshold(self):
        h = await self.held_ready()
        before = self.dump(h)
        with strict():
            self.refused(h, 'requeue_quality_not_pass', slot_text(h.now, minutes=70))
        self.assertEqual(self.dump(h), before)
        self.assertEqual(h.stages(), ['held'])

    async def test_refused_when_a_post_and_a_create_already_exist(self):
        h = await self.held_ready()
        topic_id = h.rows()[0]['topic_id']
        post_id = h.state.add_post(h.rows()[0]['slot_at'], topic_id=topic_id, state='writing')
        AdminStore(h.state, h.config)
        with h.state.connection() as db:
            db.execute("INSERT INTO admin_operations(post_id,intent,payload) VALUES(?,'x','{}')", (post_id,))
        before = self.dump(h)
        self.refused(h, 'post_or_create_exists', slot_text(h.now, minutes=70))
        self.assertEqual(self.dump(h), before)

    async def test_refused_when_the_slot_is_in_the_past_or_too_close(self):
        h = await self.held_ready()
        self.refused(h, 'slot_in_past', slot_text(h.now, minutes=-30))
        self.refused(h, 'slot_too_close', slot_text(h.now, minutes=62))       # window 1 h + 10 min needed
        self.refused(h, 'slot_timezone_required', '2026-10-28T22:30')
        self.assertEqual(h.stages(), ['held'])

    async def test_refused_when_the_slot_belongs_to_another_run_or_topic(self):
        h = await self.held_ready(slots=('06:00', '10:00'))
        first, second = [r['run_id'] for r in h.rows()]
        slot = slot_text(h.now, minutes=70)
        self.requeue(h, slot, first)
        self.refused(h, 'slot_taken', slot, second)                          # a run already moved there
        other = slot_text(h.now, minutes=130)
        h.state.add_topic('daily:' + DAY, 'Chủ đề khác', 'nail-salon', other, 0.5, [fakes.SOURCE], state='approved')
        self.refused(h, 'slot_taken', other, second)                         # an approved topic holds it
        self.assertEqual([r['stage'] for r in h.rows()], ['new', 'held'])


class RequeueDryRunTests(RequeueBase):
    async def test_dry_run_reports_and_writes_nothing(self):
        h = await self.held_ready()
        before = self.dump(h)
        report = self.requeue(h, slot_text(h.now, minutes=70), dry_run=True)
        self.assertEqual((report['dry_run'], report['rescored_quality']['verdict']), (True, 'pass'))
        self.assertTrue(report['changes'])
        self.assertEqual(self.dump(h), before)
        args = type('Args', (), {'run': h.rows()[0]['run_id'], 'slot': slot_text(h.now, minutes=70), 'dry_run': True})()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            await entry.execute('autoflow-requeue', h.config, args, entry.Runtime(now=h.now))
        self.assertIn('--dry-run: không ghi gì', out.getvalue())
        self.assertIn('verdict=pass', out.getvalue())
        self.assertEqual(self.dump(h), before)


if __name__ == '__main__':
    unittest.main()
