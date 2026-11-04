import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from source_mirror.official_prepost import check_publish_time,CandidateTiming
from source_mirror.official_deadline import seconds_available,maintenance_seconds,ScheduleUnavailable

class PublishTimeTests(unittest.TestCase):
    def test_slot_day_timezone_and_future(self):
        now=datetime(2026,10,30,12,10,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
        check_publish_time('2026-10-30T05:00:00Z','2026-10-30','12:00',now)
        for value,day,slot in [
            ('2026-10-30T05:11:00Z','2026-10-30','12:00'),
            ('2026-10-29T23:30:00Z','2026-10-30','12:00'),
            ('2026-10-29T17:00:00Z','2026-10-30','12:00'),
            ('2026-10-30T12:00:00','2026-10-30','12:00'),
            ('2026-10-30T05:00:00Z','2026-10-31','12:00'),
            ('2026-10-30T05:00:00Z','2026-10-30','16:00')]:
            with self.subTest(value=value,day=day,slot=slot),self.assertRaises(ValueError):
                check_publish_time(value,day,slot,now)
    def test_budget_cutoff(self):
        zone=ZoneInfo('Asia/Ho_Chi_Minh')
        self.assertEqual(seconds_available('2026-10-30','08:00',datetime(2026,10,30,11,50,tzinfo=zone)),300)
        self.assertLess(seconds_available('2026-10-30','08:00',datetime(2026,10,30,11,56,tzinfo=zone)),0)
        with self.assertRaises(ValueError):seconds_available('2026-10-29','20:00',datetime(2026,10,30,0,1,tzinfo=zone))
        self.assertEqual(seconds_available('2026-10-30','20:00',datetime(2026,10,30,23,50,tzinfo=zone)),300)

    def test_candidate_time_is_distinct_from_system_day(self):
        now=datetime(2026,10,30,12,10,tzinfo=ZoneInfo('Asia/Ho_Chi_Minh'))
        with self.assertRaises(CandidateTiming):check_publish_time('2026-10-29T05:00:00Z','2026-10-30','12:00',now)
        with self.assertRaises(ValueError) as day:check_publish_time('2026-10-30T05:00:00Z','2026-10-31','12:00',now)
        self.assertNotIsInstance(day.exception,CandidateTiming)

    def test_preparation_windows_are_not_fixed_to_early_morning(self):
        zone=ZoneInfo('Asia/Ho_Chi_Minh')
        for hour,minute in ((5,30),(9,10),(13,10),(21,10)):
            self.assertGreaterEqual(maintenance_seconds(datetime(2026,10,30,hour,minute,tzinfo=zone)),900)
        for hour,minute in ((8,15),(11,30),(12,30),(23,40)):
            with self.subTest(hour=hour,minute=minute),self.assertRaises(ScheduleUnavailable):
                maintenance_seconds(datetime(2026,10,30,hour,minute,tzinfo=zone))
