"""Scheduler dispatch tests; mocked publisher is not a production proof."""
import unittest
from contextlib import nullcontext
from unittest.mock import patch
import test_official_store as fixture
from source_mirror.official_scheduler import try_once
from source_mirror.official_store import CandidateUnavailable
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock

class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.f=fixture.OfficialStoreTests();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.aid=self.f.stage();self.policy={'enabled':True,'source_ids':['cdc-niosh-nail-technicians']}
        p=patch('source_mirror.official_scheduler.scheduled',side_effect=lambda *args:nullcontext());p.start();self.addCleanup(p.stop)
        p=patch('source_mirror.official_reservation_preflight.check_candidate');p.start();self.addCleanup(p.stop)
    def test_disabled_and_missing_publisher_never_reserve(self):
        self.assertFalse(try_once(self.f.store,'2026-11-01','08:00'))
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True),patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy):
            self.assertFalse(try_once(self.f.store,'2026-11-01','08:00'))
        self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)

    def test_only_candidate_specific_failure_skips_and_system_failure_stops(self):
        from source_mirror.official_scheduler import _try_once
        root=Path('fixture-root')
        candidates=[{'id':1,'evidence_path':str(root/'official-auto-evidence/cdc-niosh-nail-technicians/a/b')},
                    {'id':2,'evidence_path':str(root/'official-auto-evidence/cdc-niosh-nail-technicians/c/d')}]
        db=Mock();db.execute.side_effect=[Mock(fetchone=lambda: (1,)),Mock(fetchall=lambda:candidates)]
        store=SimpleNamespace(root=root,db=db,published=Mock(),safe_export_reports=Mock(),unknown=Mock())
        with patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy),patch('source_mirror.official_publisher.available',return_value=True), \
             patch('source_mirror.official_store.reserve',side_effect=[CandidateUnavailable('changed'),(3,{'id':2})]) as reserve, \
             patch('source_mirror.official_publisher.publish',return_value={'verified':True,'url':'x','published_day':'2026-11-01'}):
            self.assertTrue(_try_once(store,'2026-11-01','08:00'));self.assertEqual(reserve.call_count,2)
        db.execute.side_effect=[Mock(fetchone=lambda:(1,)),Mock(fetchall=lambda:candidates)]
        with patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy),patch('source_mirror.official_publisher.available',return_value=True), \
             patch('source_mirror.official_store.reserve',side_effect=ValueError('broker offline')) as reserve:
            self.assertFalse(_try_once(store,'2026-11-01','08:00'));self.assertEqual(reserve.call_count,1)

    def test_duplicate_and_fetched_source_change_skip_without_intent(self):
        from source_mirror.official_duplicate_scan import DuplicateSource
        from source_mirror.official_auto_eligibility import SourceChanged
        from source_mirror.official_prepost import CandidateTiming
        from source_mirror.official_scheduler import _try_once
        root=Path('fixture-root')
        candidates=[{'id':i,'evidence_path':str(root/'official-auto-evidence/cdc-niosh-nail-technicians'/str(i)/'x')} for i in (1,2)]
        for failure in (DuplicateSource('duplicate'),SourceChanged('changed'),CandidateTiming('stale'),OSError('offline')):
            db=Mock();db.execute.side_effect=[Mock(fetchone=lambda:(1,)),Mock(fetchall=lambda:candidates)]
            store=SimpleNamespace(root=root,db=db,published=Mock(),safe_export_reports=Mock(),unknown=Mock())
            with self.subTest(failure=type(failure).__name__), \
                 patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy), \
                 patch('source_mirror.official_publisher.available',return_value=True), \
                 patch('source_mirror.official_reservation_preflight.check_candidate',side_effect=[failure,None]) as preflight, \
                 patch('source_mirror.official_store.reserve',return_value=(3,{'id':2})) as reserve, \
                 patch('source_mirror.official_publisher.publish',return_value={'verified':True,'url':'x','published_day':'2026-11-01'}):
                self.assertEqual(bool(_try_once(store,'2026-11-01','08:00')),not isinstance(failure,OSError))
                self.assertEqual(preflight.call_count,1 if isinstance(failure,OSError) else 2)
                self.assertEqual(reserve.call_count,0 if isinstance(failure,OSError) else 1)
    def test_mock_verified_publisher_uses_existing_quota_and_dedupe(self):
        result={'verified':True,'url':'https://nailhub.ai/blog/fixture','published_day':'2026-11-01'}
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True),patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy), \
             patch('source_mirror.official_publisher.available',return_value=True),patch('source_mirror.official_publisher.publish',return_value=result):
            self.assertTrue(try_once(self.f.store,'2026-11-01','08:00'))
            self.assertFalse(try_once(self.f.store,'2026-11-01','12:00'))
        self.assertEqual(self.f.store.db.execute('SELECT state FROM articles WHERE id=?',(self.aid,)).fetchone()[0],'published')
        self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],1)
    def test_priority_reads_real_official_evidence_not_article_snapshot(self):
        result={'verified':True,'url':'https://nailhub.ai/blog/fixture','published_day':'2026-11-01'}
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True),patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy), \
             patch('source_mirror.official_publisher.available',return_value=True),patch('source_mirror.official_publisher.publish',return_value=result), \
             patch('source_mirror.slot_policy.subtopic_rank',return_value=1) as rank:
            self.assertFalse(try_once(self.f.store,'2026-11-01','08:00',priority=0))
            self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
            self.assertTrue(try_once(self.f.store,'2026-11-01','08:00',priority=1))
            self.assertEqual(rank.call_count,2)
    def test_mock_post_failure_remains_unknown_not_retried(self):
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True),patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy), \
             patch('source_mirror.official_publisher.available',return_value=True),patch('source_mirror.official_publisher.publish',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):try_once(self.f.store,'2026-11-01','08:00')
        self.assertEqual(self.f.store.db.execute('SELECT state FROM attempts').fetchone()[0],'unknown')
    def test_unavailable_policy_or_stale_official_candidate_does_not_consume_slot(self):
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True),patch('source_mirror.official_scheduler.load_publish_policy',side_effect=ValueError):
            self.assertFalse(try_once(self.f.store,'2026-11-01','08:00'))
        with patch('source_mirror.official_store.OFFICIAL_RESERVATION_ENABLED',True),patch('source_mirror.official_scheduler.load_publish_policy',return_value=self.policy), \
             patch('source_mirror.official_publisher.available',return_value=True),patch('source_mirror.official_store.reserve',side_effect=ValueError('stale')):
            self.assertFalse(try_once(self.f.store,'2026-11-01','08:00'))
        self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM attempts').fetchone()[0],0)
        self.assertEqual(self.f.store.db.execute('SELECT count(*) FROM slots').fetchone()[0],0)
