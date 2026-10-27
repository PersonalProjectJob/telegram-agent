"""S10b safety assertions that still apply after the Agy-only cut: the engine owns its runs (a one-post approve behind
its back holds the run and the post never publishes), the armed==loaded binding gate, a binding that cannot be
computed is never armed, one DM for an unlocked slot, the single image retry wording, and the old paid ledger is
read-only (any non-zero reserve/settle raises LedgerReadOnly)."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from nailblog import __main__ as cli
from nailblog import autoflow
from nailblog.commands import resume
from nailblog.images import ImageError
from nailblog.onepost import OnePost
from nailblog.paid import LedgerReadOnly, PaidLedger
from nailblog.service import Service
from nailblog.state import State
from tests.helpers import load, settings, write_config
from tests.test_autoflow import Base as FxBase
from tests.test_autoflow import FakeOnePost, at, slot_text
from tests.test_autoflow_e2e import Base as E2EBase, slot_ts


class B2ResidualEngine(E2EBase):
    async def to_draft_wait(self):
        h = self.harness(slots=('19:00',))
        await h.until_stage('images_wait')
        await h.ok_all('images')
        await h.until_stage('draft_wait')
        return h, h.rows()[0]

    async def test_p9_one_post_approve_behind_the_engine_holds_the_run_and_the_post_never_publishes(self):
        h, row = await self.to_draft_wait()
        workflow = OnePost(h.service)
        art = workflow.artifact(row['run_id'])
        workflow.approve(row['run_id'], art['snapshot'])                  # what the CLI did before the guard
        self.assertEqual(workflow.artifact(row['run_id'])['phase'], 'scheduled')
        for _ in range(3):
            await h.tick()
        self.assertEqual((h.stages(), h.rows()[0]['terminal_reason']), (['held'], 'post_changed_outside_autoflow'))
        self.assertEqual(h.state.get_post(art['post_id'])['state'], 'held')
        h.now = slot_ts('19:00') + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual((h.remote.patches, h.state.get_post(art['post_id'])['state']), (0, 'held'))
        self.assertEqual(len([t for t in h.client.texts if 'bị đổi trạng thái ngoài autoflow' in t]), 1)

    async def test_the_one_post_cli_is_gone_so_no_cli_step_can_move_an_autoflow_run(self):
        h, row = await self.to_draft_wait()
        for action in ('accept-images', 'draft', 'approve', 'publish'):
            args = SimpleNamespace(action=action, run_id=row['run_id'], snapshot='x', category_id='c')
            with self.subTest(action=action), self.assertRaises(Exception):
                await cli.execute('one-post', h.config, args)
        art = OnePost(h.service).artifact(row['run_id'])
        self.assertEqual(art['phase'], 'draft_verified')                  # nothing moved
        self.assertEqual(h.remote.patches, 0)



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


class PaidLedgerReadOnly(unittest.TestCase):
    def test_zero_is_a_no_op_and_any_money_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory)
            ledger = PaidLedger(State(config.db_path), config)
            ledger.reserve('intent-0', 'agy', 'm', 0)
            ledger.settle('intent-0', 0, 0)
            with self.assertRaises(LedgerReadOnly):
                ledger.reserve('intent-1', 'gemini', 'm', 0.5)
            with self.assertRaises(LedgerReadOnly):
                ledger.settle('intent-1', 10, 0.1)


if __name__ == '__main__':
    unittest.main()
