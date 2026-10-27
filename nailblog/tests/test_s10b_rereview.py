"""S10b: GATE 0b re-review fixes. B2-residual (one-post approve bypass), binding digest golden, shared-ledger cutover,
usage summary, retry failure wording, binding sentinel, armed==loaded gate, reconcile scope, unlocked-slot DM."""
import asyncio
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from nailblog import __main__ as cli
from nailblog import autoflow
from nailblog.commands import preflight, resume
from nailblog.config import BudgetConfig
from nailblog.images import ImageError
from nailblog.images import pipeline as pipeline_module
from nailblog.onepost import OnePost
from nailblog.paid import PaidLedger, local_ledger_problem
from nailblog.publication import digest as real_digest
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State
from nailblog.usage import UsageRecorder
from tests.helpers import settings, write_config, load
from tests.test_autoflow import Base as FxBase
from tests.test_autoflow import FakeOnePost, at, slot_text
from tests.test_autoflow_e2e import SLOT, Base as E2EBase
from tests.test_paid_ledger_hardening import make_config
from tests.test_real_images import FixtureProvider, article, fixture_cover

# Computed from the base build 7a27128 (git archive) for make_config(dir) with the directory fixed to /golden/out.
GOLDEN_BINDING = '56b22f0549f593da7f384623c5a3f04602c1e2ba937472c3ddcd901bf926b185'


class B2ResidualEngine(E2EBase):
    async def to_draft_wait(self):
        h = self.harness()
        await h.until('images_wait')
        await h.answer('images', 'ok')
        await h.until('draft_wait')
        return h, h.row()

    async def test_p9_one_post_approve_behind_the_engine_holds_the_run_and_the_post_never_publishes(self):
        h, row = await self.to_draft_wait()
        workflow = OnePost(h.service)
        art = workflow.artifact(row['run_id'])
        workflow.approve(row['run_id'], art['snapshot'])                  # what the CLI did before the guard
        self.assertEqual(workflow.artifact(row['run_id'])['phase'], 'scheduled')
        for _ in range(3):
            await h.tick()
        self.assertEqual((h.stage(), h.row()['terminal_reason']), ('held', 'post_changed_outside_autoflow'))
        self.assertEqual(h.state.get_post(art['post_id'])['state'], 'held')
        h.now = SLOT.timestamp() + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual((h.remote.patches, h.state.get_post(art['post_id'])['state']), (0, 'held'))
        self.assertEqual(len(h.dms('bị đổi trạng thái ngoài autoflow')), 1)

    async def test_the_one_post_cli_refuses_every_step_of_an_autoflow_run(self):
        h, row = await self.to_draft_wait()
        for action in ('accept-images', 'draft', 'approve', 'publish'):
            args = SimpleNamespace(action=action, run_id=row['run_id'], snapshot='x', category_id='c')
            with self.subTest(action=action), self.assertRaisesRegex(ImageError, 'autoflow_owned_run'):
                await cli.execute('one-post', h.config, args)
        art = OnePost(h.service).artifact(row['run_id'])
        self.assertEqual(art['phase'], 'draft_verified')                  # nothing moved
        other = SimpleNamespace(action='approve', run_id='not-an-autoflow-run', snapshot='x', category_id='c')
        with self.assertRaises(Exception) as caught:
            await cli.execute('one-post', h.config, other)
        self.assertNotIn('autoflow_owned_run', str(caught.exception))


class B2ResidualFx(FxBase):
    async def test_phase_scheduled_before_the_draft_card_holds_instead_of_routing_to_scheduled(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'ok')
        await fx.ticks(2)                                                 # saving -> draft_send
        self.assertEqual(fx.row()['stage'], 'draft_send')
        run = fx.onepost.runs[fx.run_id]
        fx.state.transition(run['post_id'], 'scheduled', 'one-post approve')
        run['phase'] = 'scheduled'
        await fx.ticks(2)
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'post_changed_outside_autoflow'))
        self.assertEqual(fx.post()['state'], 'held')
        self.assertEqual(len(fx.dms), 1)

    async def test_phase_scheduled_while_waiting_without_the_owner_ok_holds(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        run = fx.onepost.runs[fx.run_id]
        fx.state.transition(run['post_id'], 'scheduled', 'one-post approve')
        run['phase'] = 'scheduled'
        await fx.ticks(2)
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'post_changed_outside_autoflow'))
        self.assertEqual(fx.post()['state'], 'held')

    async def test_phase_scheduled_after_the_owner_ok_is_the_engines_own_crash_window(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok')
        run = fx.onepost.runs[fx.run_id]
        fx.state.transition(run['post_id'], 'scheduled', 'engine approve')
        run['phase'] = 'scheduled'
        await fx.ticks(2)
        self.assertEqual((fx.row()['stage'], fx.dms), ('scheduled', []))


class BindingGolden(unittest.IsolatedAsyncioTestCase):
    async def payload(self, shared=False):
        seen = []

        def spy(value):
            if isinstance(value, dict) and 'prices' in value and 'directory' in value:
                seen.append(value)
            return real_digest(value)
        with tempfile.TemporaryDirectory() as directory:
            config = make_config(directory)
            if shared:
                config = replace(config, budget=replace(config.budget,
                                                        ledger_db_path=str(Path(directory) / 'ledger' / 'l.sqlite3')))
            with patch.object(pipeline_module, 'digest', spy):
                await pipeline_module.ImagePipeline(config, provider=FixtureProvider(config),
                                                    cover_renderer=fixture_cover).run(
                    article(), Path(directory) / 'out', 'run', 1, paid_enabled=True)
        return {**seen[0], 'directory': '/golden/out'}

    async def test_a_default_config_keeps_the_binding_digest_of_7a27128(self):
        self.assertEqual(real_digest(await self.payload()), GOLDEN_BINDING)

    async def test_a_set_ledger_path_stays_in_the_binding(self):
        payload = await self.payload(shared=True)
        self.assertTrue(payload['prices']['ledger_db_path'].endswith('l.sqlite3'))
        self.assertNotEqual(real_digest(payload), GOLDEN_BINDING)


class Cutover(unittest.IsolatedAsyncioTestCase):
    T = 1_761_300_000.0

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = make_config(self.temp.name, daily=2.0)
        self.new = replace(self.old, budget=replace(self.old.budget, ledger_db_path=str(self.root / 'shared' / 'l.sqlite3')))

    def local(self):
        return PaidLedger(State(self.old.db_path), self.old, lambda: self.T)

    def test_same_day_spend_or_an_unsettled_intent_blocks_the_cutover(self):
        self.assertIsNone(local_ledger_problem(State(self.new.db_path), self.new, lambda: self.T))
        ledger = self.local()
        ledger.reserve('c1', 'gemini', 'image-test', 0.8)
        ledger.settle('c1', 1, 0.8)
        state = State(self.new.db_path)
        self.assertEqual(local_ledger_problem(state, self.new, lambda: self.T + 3600), 'local_ledger_not_empty')
        self.assertIsNone(local_ledger_problem(state, self.new, lambda: self.T + 86400 + 3600))   # fresh Vietnam day
        self.assertIsNone(local_ledger_problem(state, self.old, lambda: self.T))                  # no shared ledger set
        ledger.reserve('crash', 'gemini', 'image-test', 0.3)
        ledger.uncertain('crash')
        self.assertEqual(local_ledger_problem(state, self.new, lambda: self.T + 86400 + 3600), 'local_ledger_not_empty')

    async def test_resume_preflight_refuses_with_local_ledger_not_empty(self):
        ledger = self.local()
        ledger.reserve('c1', 'gemini', 'image-test', 0.8)
        ledger.settle('c1', 1, 0.8)
        path = write_config(self.temp.name)
        service = Service(self.new, clock=lambda: self.T + 60, config_path=str(path))
        service.state.set_paused(True)
        self.assertIn('local_ledger_not_empty', await preflight(service, config_path=str(path)))
        reply = (await resume(service, config_path=str(path)))[0]
        self.assertIn('local_ledger_not_empty', reply)
        self.assertEqual((service.state.control()['paused'], service.state.armed_binding()), (1, ''))

    def test_paid_cli_starts_refuse_a_dirty_cutover(self):
        ledger = self.local()
        ledger.reserve('c1', 'gemini', 'image-test', 0.8)
        ledger.settle('c1', 1, 0.8)
        with patch('nailblog.__main__.time.time', return_value=self.T + 60):
            with self.assertRaisesRegex(ImageError, 'local_ledger_not_empty'):
                cli._refuse_dirty_cutover(self.new)
            cli._refuse_dirty_cutover(self.old)                   # unset: unchanged behaviour


class UsageSummary(unittest.TestCase):
    def test_summary_includes_the_shared_ledger_spend(self):
        with tempfile.TemporaryDirectory() as directory:
            now = datetime(2026, 10, 24, 12, 0, tzinfo=VN_TZ).timestamp()
            config = replace(settings(directory), budget=BudgetConfig(daily_usd=2.0,
                                                                      ledger_db_path=str(Path(directory) / 'ledger' / 'l.sqlite3')))
            state = State(config.db_path)
            ledger = PaidLedger(state, config, lambda: now)
            ledger.reserve('i1', 'gemini', 'image-test', 0.5)
            ledger.settle('i1', 1, 0.5)
            recorder = UsageRecorder(state, config, clock=lambda: now)
            self.assertEqual(round(recorder.total_today(), 6), 0.5)
            self.assertTrue(recorder.publish(force=True))
            summary = json.loads(recorder.path.read_text(encoding='utf-8'))
            self.assertEqual(summary['daily'][-1]['cost'], 0.5)
            self.assertEqual(summary['windows']['today']['calls'] if 'calls' in summary['windows']['today'] else 1, 1)


class SharedLedgerHandling(unittest.TestCase):
    def test_the_shared_state_is_built_once_per_path(self):
        with tempfile.TemporaryDirectory() as directory:
            config = replace(settings(directory), budget=BudgetConfig(daily_usd=2.0, ledger_db_path=str(Path(directory) / 'l' / 'x.sqlite3')))
            first = PaidLedger(State(config.db_path), config)
            second = PaidLedger(State(config.db_path), config)
            self.assertIs(first.state, second.state)

    def test_reconcile_stale_touches_only_the_callers_own_runs(self):
        with tempfile.TemporaryDirectory() as directory:
            t = 1_761_300_000.0
            config = make_config(directory, daily=5.0)
            ledger = PaidLedger(State(config.db_path), config, lambda: t)
            ledger.run('mine', 'b', 2.0)
            ledger.run('theirs', 'b', 2.0)
            ledger.reserve('m1', 'gemini', 'image-test', 0.1, run_id='mine')
            ledger.reserve('t1', 'gemini', 'image-test', 0.1, run_id='theirs')
            ledger.reserve('text-1', 'gemini', 'image-test', 0.1)           # a text call of another process: no run
            self.assertEqual(ledger.reconcile_stale(t + 1000, 600, run_id='mine'), 1)
            self.assertEqual([ledger.get(i)['status'] for i in ('m1', 't1', 'text-1')], ['uncertain', 'pending', 'pending'])
            ledger.reserve('compare-9-v0-cover', 'gemini', 'image-test', 0.1, run_id='theirs')
            self.assertEqual(ledger.reconcile_stale(t + 1000, 600, run_prefix='the'), 2)
            self.assertEqual(ledger.get('text-1')['status'], 'pending')


class ResumeAndGate(FxBase):
    async def test_a_binding_that_could_not_be_computed_is_never_armed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_config(directory)
            config = load(directory)
            state = State(config.db_path)
            state.set_paused(True)
            with patch('nailblog.service.startup_binding', side_effect=OSError('unreadable')):
                service = Service(config, state=state, config_path=str(path))
            self.assertIsNone(service.startup_binding)
            reply = (await resume(service, config_path=str(path)))[0]
            self.assertIn('binding_unavailable', reply)
            self.assertEqual((state.control()['paused'], state.armed_binding()), (1, ''))

    async def test_the_engine_runs_only_when_armed_equals_the_loaded_binding(self):
        fx = self.fixture()
        service = fx.service()
        service.startup_binding = 'loaded-by-this-process'                # armed is 'binding-1'
        await autoflow.run_due(service, None, onepost=fx.onepost, approvals=fx.approvals, now=fx.now)
        self.assertEqual(fx.onepost.n('prepare'), 0)
        fx.state.arm('loaded-by-this-process')
        await autoflow.run_due(service, None, onepost=fx.onepost, approvals=fx.approvals, now=fx.now + 60)
        self.assertEqual(fx.onepost.n('prepare'), 1)
        service.startup_binding = None                                    # could not be computed: fail closed
        fx.onepost.calls.clear()
        fx.state.arm('anything')
        await autoflow.run_due(service, None, onepost=fx.onepost, approvals=fx.approvals, now=fx.now + 120)
        self.assertEqual(fx.onepost.calls, [])


class RetryFailureWording(FxBase):
    async def test_a_predispatch_failure_during_the_single_retry_is_not_an_owner_rejection(self):
        class Failing(FakeOnePost):
            async def retry_images(self, run_id, reason):
                self.calls.append(('retry_images', (run_id, reason)))
                run = self.runs[run_id]
                run['retries'] += 1
                run['phase'] = 'images_pending'
                raise ImageError('provider_rate_limited')                 # countTokens 429 before any dispatch
        fx = self.fixture()
        fx.onepost = Failing(fx)
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'màu xấu')
        await fx.ticks(4)
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'image_retry_interrupted'))
        self.assertEqual(len(fx.dms), 1)
        self.assertNotIn('từ chối', fx.dms[0])


class UnlockedSlotDm(FxBase):
    async def test_a_decided_day_with_an_unchosen_approved_topic_sends_one_dm_and_no_run(self):
        fx = self.fixture(topics=())
        topic = fx.state.add_topic('w', 'Ngoài nhóm', 'hot-trend', slot_at=slot_text(3, 6), state='approved')
        await fx.ticks(2)
        self.assertEqual(fx.dms, [])                                       # no decision yet: nothing to say
        fx.state.daily_mark('2026-11-02', 'decide', fx.now)
        await fx.ticks(3)
        self.assertEqual(autoflow.AutoflowRuns(fx.state).rows(), [])
        self.assertEqual(len(fx.dms), 1)
        self.assertIn('chưa có chủ đề được chốt', fx.dms[0])
        self.assertIn(f'#{topic}', fx.dms[0])
        await fx.ticks(3)
        self.assertEqual(len(fx.dms), 1)                                   # once


if __name__ == '__main__':
    unittest.main()
