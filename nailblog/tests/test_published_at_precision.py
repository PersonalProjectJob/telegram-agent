"""publishedAt canonicalisation (UTC, minute floor) for the admin write guard. No network."""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from nailblog.admin_security import Guard
from nailblog.config import STAGING_API_ORIGIN
from nailblog.publication import instant, matches, snapshot
from tests.test_admin_phase2 import config, payload

DURABLE = '2026-10-21T10:24:02.186417+00:00'


def with_time(base, value, **extra):
    return {**copy.deepcopy(base), 'publishedAt': value, **extra}


class PublishedAtPrecisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.c = config(self.tmp.name)
        self.slot = with_time(payload(), DURABLE)

    def request(self, data):
        return SimpleNamespace(url=STAGING_API_ORIGIN + '/api/v1/admin/blogs', method='POST',
                               post_data_json=data, redirected_from=None, headers={})

    def test_seconds_and_microseconds_slot_matches_minute_browser_body(self):
        body = with_time(self.slot, '2026-10-21T10:24:00.000Z')
        self.assertTrue(matches(body, self.slot, self.c))

    def test_same_instant_in_offset_and_z_forms_matches(self):
        forms = ('2026-10-21T17:24:00+07:00', '2026-10-21T10:24:00+00:00', '2026-10-21T10:24:00.000Z')
        for a in forms:
            for b in forms:
                with self.subTest(body=a, slot=b):
                    self.assertTrue(matches(with_time(self.slot, a), with_time(self.slot, b), self.c))
        self.assertTrue(matches(with_time(self.slot, '2026-10-21T17:24:30+07:00'), self.slot, self.c))

    def test_different_minute_does_not_match_draft(self):
        for value in ('2026-10-21T10:25:00.000Z', '2026-10-21T10:23:00.000Z', '2026-10-21T17:25:00+07:00',
                      '2026-10-22T10:24:00.000Z'):
            with self.subTest(value=value):
                self.assertFalse(matches(with_time(self.slot, value), self.slot, self.c))

    def test_guard_permits_truncated_body_only(self):
        def valid(q):
            body = q.post_data_json
            return matches(body, self.slot, self.c) and not body.get('id')

        truncated = with_time(self.slot, '2026-10-21T10:24:00.000Z')
        wrong_minute = with_time(self.slot, '2026-10-21T10:25:00.000Z')
        wrong_title = copy.deepcopy(truncated)
        wrong_title['translations'][0]['title'] += ' khác'
        guard = Guard(self.c)
        with guard.allow('POST', '/api/v1/admin/blogs', valid):
            self.assertFalse(guard.permits(self.request(wrong_minute)))
            self.assertFalse(guard.permits(self.request(wrong_title)))
            self.assertTrue(guard.permits(self.request(truncated)))
            self.assertFalse(guard.permits(self.request(truncated)))

    def test_published_tolerance_stays_900_seconds(self):
        base = payload()
        slot = instant(base['publishedAt'])
        for minutes, expected in ((14, True), (-14, True), (16, False), (-16, False)):
            with self.subTest(minutes=minutes):
                actual = {**base, 'status': 'Published', 'publishedAt': (slot + timedelta(minutes=minutes)).isoformat()}
                self.assertEqual(matches(actual, base, self.c, status='Published', published=True), expected)

    def test_snapshot_published_at_is_utc_minute_iso(self):
        for value in (DURABLE, '2026-10-21T17:24:59.999999+07:00', '2026-10-21T10:24:00.000Z'):
            with self.subTest(value=value):
                self.assertEqual(snapshot(with_time(self.slot, value), self.c)['publishedAt'],
                                 '2026-10-21T10:24:00+00:00')
        result = instant(snapshot(self.slot, self.c)['publishedAt'])
        self.assertEqual((result.utcoffset(), result.second, result.microsecond),
                         (timedelta(0), 0, 0))
        self.assertEqual(result, datetime(2026, 10, 21, 10, 24, tzinfo=timezone.utc))


if __name__ == '__main__':
    unittest.main()
