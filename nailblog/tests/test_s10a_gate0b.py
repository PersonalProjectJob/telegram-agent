"""S10a safety assertions that still apply after the Agy-only cut (the paid-ledger, run-ceiling, card-cost and
pre-dispatch tests are gone with the paid routes): locked topics only, the legacy `approve` command and the one-post CLI
cannot bypass the engine, the engine guards the post state, resume arms what the process loaded, publish_not_done,
the staging marker."""
import asyncio
import contextlib
import io
import json
import tempfile
import unittest

from nailblog import __main__ as cli
from nailblog import autoflow
from nailblog.commands import execute, resume
from nailblog.config import ContractConfig
from nailblog.onepost import OnePost
from nailblog.service import Service
from nailblog.state import State
from tests.helpers import load, settings, write_config
from tests.test_autoflow import Base as FxBase
from tests.test_autoflow import at, slot_text
from tests.test_autoflow_e2e import Base as E2EBase, slot_ts


class B1LockRule(FxBase):
    async def test_only_decided_topics_get_a_run(self):
        fx = self.fixture(topics=())
        x = fx.state.add_topic('w', 'X', 'hot-trend', slot_at=slot_text(3, 6), state='approved')
        y = fx.state.add_topic('w', 'Y', 'hot-trend', slot_at=slot_text(3, 19), state='approved')   # window opens 2/11 19:00
        label = {'day': '2026-11-03', 'slot': '06:00', 'origin': 'pick', 'auto': False}
        await fx.ticks(2)
        self.assertEqual(autoflow.AutoflowRuns(fx.state).rows(), [])            # approved only: not locked
        fx.state.update_topic_meta(x, chosen=label)
        await fx.ticks(2)
        self.assertEqual(autoflow.AutoflowRuns(fx.state).rows(), [])            # label without the decide marker
        fx.state.daily_mark('2026-11-02', 'decide', fx.now)
        await fx.ticks(2)                                                       # marker: X is locked, Y has no label
        await fx.tick(at(2, 20))                                                # Y's own window is open now
        self.assertEqual([row['topic_id'] for row in autoflow.AutoflowRuns(fx.state).rows()], [x])
        self.assertIsNone(autoflow.AutoflowRuns(fx.state).by_topic(y))

    async def test_a_second_locked_topic_for_a_slot_with_a_run_never_gets_one(self):
        fx = self.fixture()
        first = fx.topic
        await fx.ticks(2)
        fx.state.set_topic_state(first, 'dropped')
        second = fx.state.add_topic('w', 'Second', 'hot-trend', slot_at=slot_text(3, 6), state='approved')
        fx.lock_topic(second)
        await fx.ticks(6)
        rows = autoflow.AutoflowRuns(fx.state).rows()
        self.assertEqual([(row['topic_id'], row['stage'], row['terminal_reason']) for row in rows],
                         [(first, 'held', 'topic_changed')])
        self.assertEqual(fx.onepost.n('prepare'), 1)


class B2LegacyApprove(E2EBase):
    async def test_p1_legacy_approve_is_refused_and_the_draft_still_needs_its_ok(self):
        h = self.harness(slots=('19:00',))
        await h.until_stage('images_wait')
        await h.ok_all('images')
        await h.until_stage('draft_wait')
        post = h.state.list_posts()[0]
        reply = (await h.owner('/nailblog approve %d' % post['id']))[0]
        self.assertIn('/nailblog ok', reply)
        self.assertEqual(h.state.get_post(post['id'])['state'], 'review')
        h.now = slot_ts('19:00') - 3 * 3600                   # the Draft ok never came: nothing may publish at the slot
        await h.tick(0)
        h.now = slot_ts('19:00') + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual(h.remote.patches, 0)
        self.assertNotEqual(h.state.get_post(post['id'])['state'], 'published')

    async def test_a_post_of_a_one_post_run_cannot_be_approved_even_with_autoflow_off(self):
        with tempfile.TemporaryDirectory() as directory:
            config = settings(directory)                       # autoflow disabled, preview mode
            state = State(config.db_path)
            service = Service(config, state=state)
            OnePost(service)                                   # creates onepost_runs
            linked = state.add_post(slot_text(3, 6), topic_id=None, state='review')
            free = state.add_post(slot_text(3, 19), topic_id=None, state='review')
            with state.connection() as db:
                db.execute('INSERT INTO onepost_runs(run_id,binding,phase,document,post_id) VALUES(?,?,?,?,?)',
                           ('one', 'b', 'draft_verified', '{}', linked))
            refused = execute(state, 'approve', (linked,), now=0, config=config)[0]
            self.assertIn('/nailblog ok', refused)
            self.assertEqual(state.get_post(linked)['state'], 'review')
            self.assertIn('đã lên lịch', execute(state, 'approve', (free,), now=0, config=config)[0])   # legacy post
            self.assertEqual(state.get_post(free)['state'], 'scheduled')


class B2EngineGuard(FxBase):
    async def test_post_scheduled_outside_the_engine_holds_the_run_with_one_dm(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        post = fx.post()
        fx.state.transition(post['id'], 'scheduled', 'cli approve')
        await fx.ticks(4)
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'post_changed_outside_autoflow'))
        self.assertEqual(fx.onepost.n('approve'), 0)
        self.assertEqual(len([d for d in fx.dms if 'bị đổi trạng thái ngoài autoflow' in d]), 1)
        self.assertEqual(fx.post()['state'], 'held')            # S10b: a still-scheduled post is held so it cannot publish

    async def test_post_held_outside_the_engine_holds_the_run(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.state.transition(fx.post()['id'], 'held', 'operator hold')
        await fx.ticks(3)
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'post_changed_outside_autoflow'))
        self.assertEqual(len(fx.dms), 1)

    async def test_an_owner_approved_draft_whose_post_the_engine_already_scheduled_is_not_a_false_alarm(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok')
        post = fx.post()
        fx.state.transition(post['id'], 'scheduled', 'engine approve; crash before the stage move')
        await fx.ticks(2)
        self.assertEqual(fx.row()['stage'], 'scheduled')
        self.assertEqual(fx.onepost.n('approve'), 0)
        self.assertEqual(fx.dms, [])


class B3ResumeArmsWhatWasLoaded(unittest.IsolatedAsyncioTestCase):
    def gate(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(['startup-gate', '--config', str(path)])
        return json.loads(out.getvalue())

    async def test_p3_resume_after_a_config_edit_is_refused_and_a_restart_pauses_and_disarms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_config(directory)
            first = await asyncio.to_thread(self.gate, path)
            self.assertEqual(first['decision'], 'paused_disarmed')
            config = load(directory)
            state = State(config.db_path)
            service = Service(config, state=state, config_path=str(path))          # the process starts here
            loaded = service.startup_binding
            path.write_text(path.read_text(encoding='utf-8') + '# edited after start, not loaded\n', encoding='utf-8')
            reply = (await resume(service, config_path=str(path)))[0]
            self.assertIn('restart_required', reply)
            self.assertEqual((state.control()['paused'], state.armed_binding()), (1, ''))
            second = await asyncio.to_thread(self.gate, path)                      # restart with the edited config
            self.assertEqual((second['decision'], second['paused'], second['armed']), ('paused_disarmed', True, False))
            self.assertIsNotNone(loaded)

    async def test_resume_arms_exactly_the_binding_the_process_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_config(directory)
            await asyncio.to_thread(self.gate, path)
            config = load(directory)
            state = State(config.db_path)
            service = Service(config, state=state, config_path=str(path))
            self.assertIn('đã arm', (await resume(service, config_path=str(path)))[0])
            self.assertEqual(state.armed_binding(), service.startup_binding)
            self.assertEqual(state.control()['paused'], 0)
            self.assertEqual((await asyncio.to_thread(self.gate, path))['decision'], 'unchanged')


class M5PublishNotDone(FxBase):
    async def test_a_post_still_scheduled_an_hour_after_its_slot_holds_the_run_with_one_dm(self):
        fx = self.fixture()
        await self.to_draft_wait(fx)
        fx.decide('draft', 'ok')
        await fx.ticks(2)
        self.assertEqual(fx.row()['stage'], 'scheduled')
        await fx.tick(at(3, 6, 59, 59))                       # inside the hour: still waiting for the scheduler
        self.assertEqual((fx.row()['stage'], fx.dms), ('scheduled', []))
        await fx.tick(at(3, 7, 0, 1))
        await fx.tick(at(3, 7, 5))
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'publish_not_done'))
        self.assertEqual(len([d for d in fx.dms if 'chưa xác nhận đã đăng' in d]), 1)
        self.assertEqual(fx.post()['state'], 'scheduled')       # the post state machine is not touched


class StagingMarker(FxBase):
    async def test_m7_cards_and_dms_carry_the_staging_prefix(self):
        fx = self.fixture()
        fx.configure(contract=ContractConfig(environment='staging'))
        await self.to_images_wait(fx)
        self.assertTrue(fx.media[0][2].startswith('[staging] NailBlog duyệt bài + ảnh'))
        fx.state.set_topic_state(fx.topic, 'dropped')           # force a held run: one DM
        await fx.ticks(2)
        self.assertEqual(len(fx.dms), 1)
        self.assertTrue(fx.dms[0].startswith('[staging] NailBlog autoflow:'))

    async def test_production_environment_has_no_prefix(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        self.assertTrue(fx.media[0][2].startswith('NailBlog duyệt bài + ảnh'))


if __name__ == '__main__':
    unittest.main()
