"""S10a: the GATE 0b review blockers (B1-B3, M4, M5) and minors (m1-m3, m5, m7), each reproduced as a test.

B1 locked topics only / swap or drop cannot spawn a second run, B2 legacy approve cannot bypass the Draft ok,
B3 resume arms what the process loaded, M4 one cap across state roots, M5 publish limit vs slots, m1 run ceiling,
m2 retry interruption, m3 real settled spend on the card, m5 free pre-dispatch failure is resumable, m7 staging.
"""
import asyncio
import contextlib
import io
import json
import re
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from nailblog import __main__ as cli
from nailblog import autoflow
from nailblog.approvals import build_preview
from nailblog.autoflow import AutoflowRuns
from nailblog.commands import execute, resume
from nailblog.config import BudgetConfig, ContractConfig
from nailblog.images import ImageError
from nailblog.images.compare import parse_variants, run_compare
from nailblog.images.pipeline import DispatchPaused, ImagePipeline
from nailblog.onepost import OnePost
from nailblog.paid import PaidLedger, RunCeilingExceeded
from nailblog.schedule import VN_TZ
from nailblog.service import Service
from nailblog.state import State
from nailblog.usage import BudgetExceeded, UsageRecorder
from tests.helpers import load, settings, write_config
from tests.test_autoflow import Base as FxBase
from tests.test_autoflow import FakeOnePost, at, slot_text
from tests.test_autoflow_e2e import SLOT, Base as E2EBase
from tests.test_config_autoflow import LIVE, ConfigCase, live_sections
from tests.test_image_compare import MODELS, SECRET, FakeGemini
from tests.test_real_images import FixtureProvider, article, config as image_config, fixture_cover, fixture_image

SLOT6_TEXT = '2026-10-25T06:00+07:00'
META = {'sources': [], 'product_promo': False}
DAY = datetime(2026, 10, 24, tzinfo=VN_TZ)


def vn(hour, minute=0, day=24):
    return datetime(2026, 10, day, hour, minute, tzinfo=VN_TZ).timestamp()


class Slot6(E2EBase):
    """The e2e harness with two candidates A/B for the 06:00 slot of 25/10 (the reviewer's P2/P4 setup)."""

    def setup_candidates(self):
        h = self.harness()
        h.state.set_topic_state(h.topic, 'dropped')          # the harness' own 19:00 topic is not part of these probes
        h.now = vn(10, 30)
        h.state.daily_mark('2026-10-24', 'propose', h.now)   # no model call for the proposal step
        a = h.state.add_topic('daily:2026-10-25', 'Chu de A', 'nail-salon', SLOT6_TEXT, 0.5, META)
        b = h.state.add_topic('daily:2026-10-25', 'Chu de B', 'nail-salon', SLOT6_TEXT, 0.4, META)
        return h, a, b

    def generate_calls(self, h):
        return sum(1 for call in h.gemini.calls if call == 'generateContent')

    def runs(self, h):
        return AutoflowRuns(h.state).rows()


class B1LockedTopicsOnly(Slot6):
    async def test_p2_pick_swap_before_the_decision_never_starts_a_paid_run(self):
        h, a, b = self.setup_candidates()
        self.assertIn('Đã chọn', (await h.owner('/nailblog pick 06 1'))[0])
        for _ in range(8):                                   # 10:31..10:38: picks are still open, nothing is locked
            await h.tick()
        self.assertEqual((self.runs(h), h.text_calls, h.gemini.calls, h.client.cards), ([], 0, [], []))
        self.assertIn('Đã chọn', (await h.owner('/nailblog pick 06 2'))[0])   # a swap is fine before the lock
        self.assertEqual((h.state.get_topic(a)['state'], h.state.get_topic(b)['state']), ('dropped', 'approved'))
        h.now = vn(13, 0)
        for _ in range(8):                                   # the 13:00 decision locks B, then exactly one run
            await h.tick()
        rows = self.runs(h)
        self.assertEqual([(row['topic_id'], row['stage']) for row in rows], [(b, 'images_wait')])
        self.assertEqual([p['topic_id'] for p in h.state.list_posts() if p['slot_at'] == SLOT6_TEXT], [b])
        self.assertEqual((h.text_calls, self.generate_calls(h), [k for k, _ in h.client.cards]), (1, 4, ['images']))

    async def test_pick_and_drop_are_refused_once_the_slot_has_a_run(self):
        h, a, b = self.setup_candidates()
        await h.owner('/nailblog pick 06 1')
        h.now = vn(13, 0)
        for _ in range(8):
            await h.tick()
        self.assertEqual([row['topic_id'] for row in self.runs(h)], [a])
        drop = (await h.owner(f'/nailblog topics drop {a}'))[0]
        self.assertIn('run autoflow', drop)
        self.assertEqual(h.state.get_topic(a)['state'], 'approved')
        calls = self.generate_calls(h)
        for _ in range(4):
            await h.tick()
        self.assertEqual(([row['topic_id'] for row in self.runs(h)], self.generate_calls(h)), ([a], calls))

    async def test_pick_refuses_a_slot_that_already_has_a_run_even_before_the_deadline(self):
        h, a, b = self.setup_candidates()
        await h.owner('/nailblog pick 06 1')
        AutoflowRuns(h.state).create('auto-20261025-0600-t%d' % a, a, SLOT6_TEXT, str(h.root / 'x'), 1.0)
        reply = (await h.owner('/nailblog pick 06 2'))[0]
        self.assertIn('đã có run autoflow', reply)
        self.assertEqual((h.state.get_topic(a)['state'], h.state.get_topic(b)['state']), ('approved', 'dropped'))

    async def test_p4_topic_swapped_during_the_paid_step_never_starts_a_second_run_or_post(self):
        h, a, b = self.setup_candidates()
        await h.owner('/nailblog pick 06 1')
        original, swapped = h.service.draft, []

        async def draft(topic, model, *, posts, today):
            if not swapped:       # the Owner/another process swaps the topic while the run is writing
                swapped.append(1)
                h.state.set_topic_state(a, 'dropped')
                h.state.set_topic_state(b, 'approved')
            return await original(topic, model, posts=posts, today=today)
        h.service.draft = draft
        h.now = vn(13, 0)
        for _ in range(12):
            await h.tick()
        self.assertEqual(swapped, [1])
        self.assertEqual([(row['topic_id'], row['stage'], row['terminal_reason']) for row in self.runs(h)],
                         [(a, 'held', 'topic_changed')])
        self.assertEqual([p['topic_id'] for p in h.state.list_posts()], [a])
        self.assertEqual(h.state.list_posts()[0]['state'], 'held')
        self.assertEqual((h.text_calls, self.generate_calls(h), h.client.cards), (1, 4, []))   # no card, no more paid calls
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (0, 0, 0))
        self.assertEqual(len(h.dms('chủ đề không còn được duyệt')), 1)

    async def test_topic_swapped_before_save_holds_without_a_draft(self):
        h, a, b = self.setup_candidates()
        await h.owner('/nailblog pick 06 1')
        h.now = vn(13, 0)
        await h.until('images_wait')
        await h.answer('images', 'ok')
        h.state.set_topic_state(a, 'dropped')
        h.state.set_topic_state(b, 'approved')
        for _ in range(6):
            await h.tick()
        self.assertEqual((self.runs(h)[0]['stage'], self.runs(h)[0]['terminal_reason']), ('held', 'topic_changed'))
        self.assertEqual((h.remote.uploads, h.remote.creates, h.remote.patches), (0, 0, 0))
        self.assertEqual(len(self.runs(h)), 1)
        self.assertEqual(len(h.dms('chủ đề không còn được duyệt')), 1)

    async def test_topic_swapped_before_the_draft_approval_never_schedules_or_publishes(self):
        h, a, b = self.setup_candidates()
        await h.owner('/nailblog pick 06 1')
        h.now = vn(13, 0)
        await h.until('images_wait')
        await h.answer('images', 'ok')
        await h.until('draft_wait')
        await h.answer('draft', 'ok')
        h.state.set_topic_state(a, 'dropped')
        h.state.set_topic_state(b, 'approved')
        for _ in range(6):
            await h.tick()
        self.assertEqual((self.runs(h)[0]['stage'], self.runs(h)[0]['terminal_reason']), ('held', 'topic_changed'))
        self.assertEqual(h.state.list_posts()[0]['state'], 'held')
        h.now = SLOT.replace(day=25, hour=6).timestamp() + 60
        await h.tick(0)
        await h.tick()
        self.assertEqual((h.remote.creates, h.remote.patches, len(self.runs(h))), (1, 0, 1))


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
        h = self.harness()
        await h.until('images_wait')
        await h.answer('images', 'ok')
        await h.until('draft_wait')
        post = h.state.list_posts()[0]
        reply = (await h.owner('/nailblog approve %d' % post['id']))[0]
        self.assertIn('/nailblog ok', reply)
        self.assertEqual(h.state.get_post(post['id'])['state'], 'review')
        h.now = SLOT.timestamp() - 3 * 3600                   # the Draft ok never came: nothing may publish at the slot
        await h.tick(0)
        h.now = SLOT.timestamp() + 60
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


class M4SharedLedger(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.shared = str(root / 'ledger' / 'ledger.sqlite3')
        self.now = vn(12)

    def cfg(self, name, shared=True):
        (Path(self.temp.name) / name).mkdir(exist_ok=True)
        budget = BudgetConfig(daily_usd=2.0, ledger_db_path=self.shared if shared else '')
        return replace(settings(Path(self.temp.name) / name), budget=budget)

    def ledger(self, name, shared=True):
        config = self.cfg(name, shared)
        return PaidLedger(State(config.db_path), config, lambda: self.now), config

    def test_two_state_roots_share_one_two_dollar_cap(self):
        a, _ = self.ledger('a')
        b, _ = self.ledger('b')
        a.reserve('a1', 'gemini', 'm', 1.9)
        a.settle('a1', 1, 1.9)
        with self.assertRaises(BudgetExceeded):
            b.reserve('b1', 'gemini', 'm', 0.2)                  # 1.9 + 0.2 > 2.0, seen from the OTHER root
        b.reserve('b2', 'gemini', 'm', 0.1)                      # exactly 2.0 is allowed
        with self.assertRaises(BudgetExceeded):
            a.reserve('a2', 'gemini', 'm', 0.01)                 # and A sees B's open reservation
        self.assertEqual(round(a.daily_left_usd(), 6), 0.0)

    def test_every_paid_ledger_lives_in_the_shared_database_only(self):
        a, config = self.ledger('a')
        a.reserve('a1', 'gemini', 'm', 0.5)
        self.assertEqual(Path(a.state.path), Path(self.shared))
        with State(config.db_path).connection() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='paid_intents'").fetchone())
        pipeline = ImagePipeline(config, state=State(config.db_path), provider=object())
        self.assertEqual(Path(pipeline.ledger.state.path), Path(self.shared))
        self.assertEqual(Path(a.state.path), Path(PaidLedger(State(config.db_path), config).state.path))
        recorder = UsageRecorder(State(config.db_path), config, clock=lambda: self.now)
        other, _ = self.ledger('b')
        other.reserve('b1', 'gemini', 'm', 1.5)                  # 0.5 + 1.5 = 2.0: the recorder's check refuses
        with self.assertRaises(BudgetExceeded):
            recorder.check()

    def test_unset_keeps_one_ledger_per_state_root(self):
        a, _ = self.ledger('a', shared=False)
        b, _ = self.ledger('b', shared=False)
        a.reserve('a1', 'gemini', 'm', 1.9)
        b.reserve('b1', 'gemini', 'm', 1.9)                      # independent caps, exactly as before
        self.assertNotEqual(a.state.path, b.state.path)

    def test_age_out_and_stale_reconciliation_work_on_the_shared_database(self):
        a, _ = self.ledger('a')
        b, _ = self.ledger('b')
        a.reserve('u', 'gemini', 'm', 0.7)
        self.assertEqual(b.reconcile_stale(self.now + 600, 300), 1)          # B turns A's crashed pending uncertain
        with self.assertRaises(BudgetExceeded):
            b.reserve('x', 'gemini', 'm', 1.5)                               # the full bound still counts
        self.assertEqual(b.age_out_uncertain(self.now + 90000), 1)           # then settles it at the full bound
        self.assertEqual(round(a.daily_left_usd(), 6), 1.3)                  # 2.0 - 0.7, booked to the intent's day

    def test_compare_uses_the_shared_ledger(self):
        config = self.cfg('a')
        config = replace(config, budget=replace(config.budget, text_prices={m: (.5, 3) for m in MODELS},
                                                image_output_prices={m: 3 for m in MODELS},
                                                image_unit_prices={m: {'1K': .04, '2K': .04} for m in MODELS}),
                         image=replace(config.image, model_body='img-a', model_cover='img-a', review_model='img-a'))
        board = run_compare(config, article(), Path(self.temp.name) / 'board', parse_variants('img-a@1K'), 1.0,
                            paid_enabled=True, clock=lambda: self.now, transport=FakeGemini(),
                            key_reader=lambda *a, **k: SECRET)
        self.assertEqual(board['ledger_settled_usd'], 0.08)
        self.assertEqual(round(PaidLedger(State(self.cfg('b').db_path), self.cfg('b'), lambda: self.now).daily_left_usd(), 6),
                         1.92)                                                # B sees A's compare spend
        with State(config.db_path).connection() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='paid_intents'").fetchone())

    def test_status_reports_the_cap_ledger_spend(self):
        config = self.cfg('a')
        State(self.shared).add_usage(self.now, 'gemini', 'm', 1, 0.37)
        text = execute(State(config.db_path), 'status', (), now=self.now, config=config)[0]
        self.assertIn('$0.37/$2.00', text)


class ConfigRules(ConfigCase):
    def test_ledger_db_path_must_be_absolute_and_is_loaded(self):
        self.invalid('budget.ledger_db_path must be an absolute path', budget='ledger_db_path = "ledger.sqlite3"')
        self.assertEqual(self.load(budget='ledger_db_path = "/var/lib/x/l.sqlite3"').budget.ledger_db_path,
                         '/var/lib/x/l.sqlite3')
        self.assertEqual(self.load().budget.ledger_db_path, '')

    def test_autoflow_requires_the_shared_ledger(self):
        self.invalid('budget.ledger_db_path', LIVE, **live_sections(budget='daily_usd = 3.0'))
        self.load(LIVE, **live_sections())

    def test_autoflow_requires_staging(self):
        self.invalid('contract.environment = staging', LIVE,
                     **live_sections(contract='verified = true\nverification_ref = "report-1"'))

    def test_autoflow_requires_a_publish_limit_covering_every_slot(self):
        self.invalid('daily_publish_limit >= the number of service.slots', LIVE,
                     **live_sections(admin='daily_publish_limit = 1'))
        self.invalid('daily_publish_limit >= the number of service.slots', LIVE, **live_sections(admin=None))
        self.load(LIVE, **live_sections(admin='daily_publish_limit = 2'))
        self.load({'mode': 'live', 'slots': ['06:00', '13:00', '19:00']}, **live_sections(admin='daily_publish_limit = 3'))
        self.invalid('daily_publish_limit >= the number of service.slots', {'mode': 'live', 'slots': ['06:00', '13:00', '19:00']},
                     **live_sections(admin='daily_publish_limit = 2'))


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


class RunCeilingRefusal(E2EBase):
    async def test_m1_run_ceiling_holds_with_its_own_reason_not_the_daily_budget_path(self):
        h = self.harness(run_usd=0.05)                         # one image reserves about 0.12
        for _ in range(4):
            await h.tick()
        row = h.row()
        self.assertEqual((row['stage'], row['terminal_reason']), ('held', 'run_ceiling_reached'))
        self.assertEqual(sum(1 for c in h.gemini.calls if c == 'generateContent'), 0)
        self.assertEqual(len(h.dms('chạm trần chi phí của run')), 1)
        self.assertEqual(h.dms('hết ngân sách'), [])



class RunCeilingEngine(FxBase):
    async def test_run_ceiling_holds_but_the_daily_cap_still_waits_for_tomorrow(self):
        fx = self.fixture()
        fx.onepost.prepare_error = RunCeilingExceeded('run USD reservation would exceed ceiling')
        await fx.ticks(2)
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'run_ceiling_reached'))
        self.assertEqual(len(fx.dms), 1)
        self.assertNotIn('hết ngân sách', fx.dms[0])
        daily = self.fixture()
        daily.onepost.prepare_error = BudgetExceeded('daily USD reservation would exceed ceiling')
        await daily.ticks(2)
        self.assertEqual(daily.row()['stage'], 'new')
        self.assertEqual(len(daily.dms), 1)
        self.assertIn('hết ngân sách', daily.dms[0])


class RetryInterrupted(FxBase):
    def interrupted(self, error):
        class Interrupted(FakeOnePost):
            async def retry_images(self, run_id, reason):
                self.calls.append(('retry_images', (run_id, reason)))
                run = self.runs[run_id]
                run['retries'] += 1
                run['phase'] = 'images_pending'                # the single retry is spent durably BEFORE the pipeline
                raise error
        return Interrupted

    async def reject_and_interrupt(self, error):
        fx = self.fixture()
        fx.onepost = self.interrupted(error)(fx)
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'màu xấu')
        await fx.ticks(4)
        return fx

    async def test_m2_pause_during_the_retry_is_not_reported_as_rejected_images(self):
        fx = await self.reject_and_interrupt(DispatchPaused('workflow_paused_before_dispatch'))
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'image_retry_interrupted'))
        self.assertEqual(len(fx.dms), 1)
        self.assertNotIn('từ chối', fx.dms[0])
        self.assertIn('gián đoạn', fx.dms[0])

    async def test_m2_daily_cap_during_the_retry_is_not_reported_as_rejected_images(self):
        fx = await self.reject_and_interrupt(BudgetExceeded('daily USD budget including reservations reached'))
        self.assertEqual((fx.row()['stage'], fx.row()['terminal_reason']), ('held', 'image_retry_interrupted'))
        self.assertEqual(len(fx.dms), 1)
        self.assertNotIn('từ chối', fx.dms[0])
        self.assertEqual(fx.state.get_post(fx.onepost.runs[fx.run_id]['post_id'])['state'], 'held')

    async def test_a_genuine_second_reject_still_says_rejected(self):
        fx = self.fixture()
        await self.to_images_wait(fx)
        fx.decide('images', 'reject', 'xấu')
        await fx.ticks(3)
        fx.decide('images', 'reject', 'vẫn xấu')
        await fx.ticks(3)
        self.assertEqual(fx.row()['terminal_reason'], 'owner_images_rejected')


class CardCost(FxBase):
    async def test_m3_the_images_card_shows_the_settled_ledger_spend_and_the_daily_remainder(self):
        fx = self.fixture()
        row = await fx.tick()                                   # prepare
        ledger = PaidLedger(fx.state, fx.config, lambda: fx.now)
        images = fx.run_id + '-images'
        ledger.run(images, 'binding', 0.5)
        ledger.reserve('g1', 'gemini', 'image-test', 0.40, run_id=images)
        ledger.settle('g1', 1, 0.37)
        ledger.reserve('g2', 'gemini', 'image-test', 0.10, run_id=images)       # open reservation: not "spent"
        await fx.tick()
        card = fx.media[0][2]
        self.assertIn('Chi phí ảnh đã chi (quyết toán): 0.37 USD; còn lại hôm nay: 2.53 USD', card)
        self.assertNotIn('0.50 USD', card)                      # run_usd is not the cost
        self.assertEqual(row['stage'], 'images_send')

    def test_build_preview_keeps_the_forecast_line_without_spend(self):
        _, card, _ = build_preview('images', {'image_quota_forecast': {'usd': 0.5}}, [], approval_id=3)
        self.assertIn('Dự báo chi phí ảnh: 0.50 USD', card)


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


class PredispatchFailure(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.c = image_config(self.temp.name)
        self.out = Path(self.temp.name) / 'output'
        self.now = vn(12, day=20)
        self.counting_fails = True
        outer = self

        class Provider(FixtureProvider):
            def count(self, *args):
                if outer.counting_fails:
                    raise ImageError('provider_rate_limited')       # countTokens 429/timeout: free, nothing generated
                return 100
        self.provider = Provider(self.c)

    def pipeline(self):
        return ImagePipeline(self.c, provider=self.provider, cover_renderer=fixture_cover, clock=lambda: self.now)

    async def run_once(self):
        return await self.pipeline().run(article(), self.out, 'run', 1, paid_enabled=True)

    def manifest(self):
        return json.loads((self.out / 'manifest.json').read_text(encoding='utf-8'))

    async def test_m5_a_failed_countTokens_pauses_the_candidate_and_the_next_run_generates(self):
        with self.assertRaisesRegex(ImageError, 'provider_rate_limited'):
            await self.run_once()
        candidate = self.manifest()['candidates']['cover-r0-c0']
        self.assertEqual(candidate['status'], 'paused')
        self.assertEqual(candidate['dispatch_pause']['reason'], 'predispatch_failure')
        self.assertEqual((self.provider.calls, self.pipeline().ledger.report('run')), ([], []))
        self.counting_fails = False
        document = await self.run_once()                          # resumes: NOT candidate_requires_reconciliation
        self.assertEqual(len(document['image_hashes']), 4)
        self.assertEqual(sum(1 for model, _ in self.provider.calls if model == 'image-test'), 4)

    async def test_a_failure_after_the_reservation_keeps_the_reconciliation_path(self):
        self.counting_fails = False
        self.provider.fail = ImageError('provider_unavailable')   # generation fails AFTER the reservation exists
        with self.assertRaises(ImageError):
            await self.run_once()
        self.assertEqual(self.manifest()['candidates']['cover-r0-c0']['status'], 'pending')
        self.provider.fail = None
        with self.assertRaisesRegex(ImageError, 'candidate_requires_reconciliation'):
            await self.run_once()


if __name__ == '__main__':
    unittest.main()
