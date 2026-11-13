"""Owner-selected topic transaction and lock; no network or production state."""
import tempfile
import unittest
import json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from nailblog.config import AGY_SLOTS, AGY_SLOT_CATEGORIES, AutoflowConfig, ContractConfig, DailyConfig, PRODUCTION_MEDIA_ORIGIN
from nailblog.daily import locked_topic
from nailblog.operator_topics import select
from nailblog.schedule import VN_TZ
from nailblog.state import State, StateError
from tests.helpers import settings


class OperatorTopicsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config = settings(self.temp.name, mode='live', slots=AGY_SLOTS,
            contract=ContractConfig(verified=True, media_origin=PRODUCTION_MEDIA_ORIGIN),
            daily=DailyConfig(slot_categories=AGY_SLOT_CATEGORIES), autoflow=AutoflowConfig(enabled=True))
        self.state = State(config.db_path)
        self.state.set_paused(True)
        self.service = SimpleNamespace(config=config, state=self.state)
        self.now = datetime(2026, 10, 28, 10, 0, tzinfo=VN_TZ).timestamp()
        self.args = {'slot': '2026-10-28T19:00:00+07:00', 'title': 'Chuẩn bị bộ móng mùa lễ',
                     'category': 'nail-salon', 'approval_ref': 'owner-chat-2026-10-28', 'now': self.now}

    def select(self, **kwargs):
        return select(self.service, **{**self.args, **kwargs})

    def test_transaction_replay_lock_and_snapshot_change(self):
        first = self.select()
        self.assertTrue(first['created'])
        self.assertEqual(self.select(), {**first, 'created': False})
        late = datetime(2026, 10, 28, 16, 0, tzinfo=VN_TZ).timestamp()
        self.assertEqual(self.select(now=late), {**first, 'created': False})
        topic = self.state.get_topic(first['topic_id'])
        self.assertTrue(locked_topic(self.state, topic, self.service.config))
        self.assertIsNone(self.state.daily_get('2026-10-27', 'decide'))
        with self.assertRaisesRegex(StateError, 'selection_conflict'):
            self.select(title='Một chủ đề khác cho cùng giờ')
        self.state.edit_topic(first['topic_id'], 'Tiêu đề đã bị sửa')
        self.assertFalse(locked_topic(self.state, self.state.get_topic(first['topic_id']), self.service.config))
        # An altered operator selection may not fall through to an unrelated daily marker.
        with self.state.connection() as db:
            changed = {'sources': [], 'product_promo': False, 'chosen': {'day': '2026-10-28', 'slot': '19:00'}}
            db.execute('UPDATE topics SET sources_json=? WHERE id=?', (json.dumps(changed), first['topic_id']))
        self.state.daily_mark('2026-10-27', 'decide', self.now)
        self.assertFalse(locked_topic(self.state, self.state.get_topic(first['topic_id']), self.service.config))

    def test_missing_operator_marker_never_falls_back_to_daily_lock(self):
        first = self.select()
        with self.state.connection() as db:
            chosen = {'sources': [], 'product_promo': False, 'chosen': {'day': '2026-10-28', 'slot': '19:00'}}
            db.execute('UPDATE topics SET sources_json=? WHERE id=?', (json.dumps(chosen), first['topic_id']))
            db.execute('DELETE FROM operator_topic_selections WHERE topic_id=?', (first['topic_id'],))
        self.state.daily_mark('2026-10-27', 'decide', self.now)
        topic = self.state.get_topic(first['topic_id'])
        self.assertFalse(locked_topic(self.state, topic, self.service.config))
        self.assertFalse(locked_topic(self.state, topic))

    def test_operator_marker_starts_one_run_and_changed_topic_loses_slot(self):
        from nailblog.autoflow import _Env, _new_runs
        first = self.select()
        env = _Env(self.service, None, None, None, self.now)
        with patch('nailblog.production_state.check_production_runs'):
            self.assertEqual(_new_runs(env), [])
            self.assertEqual(_new_runs(env), [])
        [run] = env.runs.rows()
        self.assertTrue(env.owns_slot(run))
        self.state.edit_topic(first['topic_id'], 'Tiêu đề khác sau khi đã chọn')
        self.assertFalse(env.owns_slot(run))

    def test_daily_decision_preserves_operator_selection_and_metadata(self):
        from datetime import date
        from nailblog.daily import decide
        selected = self.select(slot='2026-10-29T19:00:00+07:00')
        before = self.state.get_topic(selected['topic_id'])
        text, _ = asyncio.run(decide(self.service, None, date(2026, 10, 28)))
        after = self.state.get_topic(selected['topic_id'])
        self.assertEqual(after['sources_json'], before['sources_json'])
        self.assertEqual(after['slot_at'], before['slot_at'])
        self.assertTrue(locked_topic(self.state, after, self.service.config))
        self.assertIn('Owner đã chọn', text)

    def test_wrong_state_deadline_category_and_occupied_slot_fail_closed(self):
        self.state.set_paused(False)
        with self.assertRaisesRegex(StateError, 'requires_pause'):
            self.select()
        self.state.set_paused(True)
        for change, error in [({'now': datetime(2026, 10, 28, 16, 0, tzinfo=VN_TZ).timestamp()}, 'deadline'),
                              ({'slot': '2026-10-28T19:00:00'}, 'slot_invalid'),
                              ({'category': 'hot-trend'}, 'selection_invalid')]:
            with self.subTest(change=change), self.assertRaisesRegex(StateError, error):
                self.select(**change)
        self.state.add_topic('other', 'Bài chiếm slot', 'nail-salon', self.args['slot'], state='approved')
        with self.assertRaisesRegex(StateError, 'slot_taken'):
            self.select()

    def test_competing_selections_cannot_both_claim_the_slot(self):
        def run(title):
            try:
                return self.select(title=title)['created']
            except StateError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run, ['Chủ đề đầu tiên', 'Chủ đề thứ hai']))
        self.assertEqual(sorted(outcomes), [False, True])
        self.assertEqual(len(self.state.list_topics(('approved',))), 1)

    def test_production_manual_and_verified_contract_required(self):
        for config in (replace(self.service.config, autoflow=AutoflowConfig(enabled=True, owner_approval=False)),
                       replace(self.service.config, contract=replace(self.service.config.contract, verified=False))):
            self.service.config = config
            with self.assertRaisesRegex(StateError, 'manual_mode'):
                self.select()

