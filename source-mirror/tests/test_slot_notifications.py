import tempfile
import unittest
from unittest.mock import patch
from source_mirror.store import Store
from source_mirror.slot_notifications import begin, claim_begin, finish, recover, schema
from source_mirror.telegram_command_backend import notification


class SlotNotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.addCleanup(self.store.close)

    def test_official_scheduler_url_reaches_slot_notice(self):
        from source_mirror.__main__ import once
        url='https://nailhub.ai/blog/official-fixture'
        with patch('source_mirror.publisher.preflight'),patch.object(self.store,'paused',return_value=False),patch('source_mirror.official_scheduler.try_once',return_value=url):
            self.assertTrue(claim_begin(self.store,'2026-10-30','20:00'))
            result=once({},self.store,'2026-10-30','20:00')
            finish(self.store,'2026-10-30','20:00','published' if result else 'unknown',result)
        notice=notification(self.store,42,'claim')
        self.assertEqual(notice['payload']['state'],'published');self.assertEqual(notice['payload']['url'],url)

    def test_same_hold_is_deduplicated_but_later_publish_notified(self):
        for _ in range(3):
            begin(self.store,'2026-10-30','16:00');finish(self.store,'2026-10-30','16:00','held')
        begin(self.store,'2026-10-30','16:00');finish(self.store,'2026-10-30','16:00','published','https://nailhub.ai/blog/ok')
        second=notification(self.store,42,'claim');self.assertEqual(second['payload']['state'],'published')
        self.assertEqual(second['payload']['url'],'https://nailhub.ai/blog/ok')
        notification(self.store,42,'sent',second['notice_id'])
        self.assertIsNone(notification(self.store,42,'claim'))

    def test_inflight_unknown_precedes_resolution_without_resending(self):
        begin(self.store,'2026-10-30','16:00');finish(self.store,'2026-10-30','16:00','unknown')
        first=notification(self.store,42,'claim');self.assertEqual(first['payload']['state'],'unknown')
        begin(self.store,'2026-10-30','16:00');finish(self.store,'2026-10-30','16:00','published','https://nailhub.ai/blog/ok')
        notification(self.store,42,'unknown',first['notice_id'])
        second=notification(self.store,42,'claim');self.assertEqual(second['payload']['state'],'published')
        notification(self.store,42,'sent',second['notice_id'])
        self.assertIsNone(notification(self.store,42,'claim'))

    def test_return_to_previous_state_produces_new_current_notice(self):
        for state in ('unknown','held','unknown'):
            begin(self.store,'2026-10-30','16:00');finish(self.store,'2026-10-30','16:00',state)
        item=notification(self.store,42,'claim')
        self.assertEqual(item['payload']['state'],'unknown')
        notification(self.store,42,'sent',item['notice_id'])
        self.assertIsNone(notification(self.store,42,'claim'))

    def test_interrupted_slot_is_unknown_not_falsely_published(self):
        begin(self.store,'2026-10-30','16:00'); recover(self.store);recover(self.store)
        item=notification(self.store,42,'claim')
        self.assertEqual(item['payload']['state'],'unknown')
        self.assertIsNone(item['payload']['url'])
        notification(self.store,42,'sent',item['notice_id'])
        self.assertIsNone(notification(self.store,42,'claim'))

    def test_slot_claim_and_marker_rollback_together(self):
        schema(self.store.db)
        self.store.db.execute("CREATE TRIGGER fail_slot BEFORE INSERT ON slot_runs BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(Exception): claim_begin(self.store,'2026-10-30','16:00')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slot_attempts').fetchone()[0],0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM slot_runs').fetchone()[0],0)
        self.store.db.execute('DROP TRIGGER fail_slot')
        self.assertTrue(claim_begin(self.store,'2026-10-30','16:00'))
        self.assertEqual(self.store.db.execute('SELECT count FROM slot_attempts').fetchone()[0],1)
        recover(self.store)
        self.assertEqual(notification(self.store,42,'claim')['payload']['state'],'unknown')

    def test_failure_recording_success_does_not_claim_publication(self):
        self.assertTrue(claim_begin(self.store,'2026-10-30','16:00'))
        self.store.db.execute("CREATE TRIGGER fail_notice BEFORE INSERT ON slot_notice_events BEGIN SELECT RAISE(ABORT,'injected'); END")
        with self.assertRaises(Exception): finish(self.store,'2026-10-30','16:00','published','https://nailhub.ai/blog/ok')
        self.assertEqual(self.store.db.execute('SELECT state FROM slot_runs').fetchone()[0],'running')
        self.store.db.execute('DROP TRIGGER fail_notice');recover(self.store)
        self.assertEqual(notification(self.store,42,'claim')['payload']['state'],'unknown')
