import unittest
from datetime import date, datetime, timedelta, timezone

from nailblog import schedule as s

UTC = timezone.utc
H = lambda n: timedelta(hours=n)
M = lambda n: timedelta(minutes=n)
MORNING = s.slot_datetime(date(2026, 10, 17), "06:00")
EVENING = s.slot_datetime(date(2026, 10, 17), "19:00")


class SlotTests(unittest.TestCase):
    def test_slot_is_vietnam_time(self):
        self.assertEqual(MORNING.utcoffset(), H(7))
        self.assertEqual(MORNING.astimezone(UTC), datetime(2026, 10, 16, 23, 0, tzinfo=UTC))
        self.assertEqual(EVENING.astimezone(UTC), datetime(2026, 10, 17, 12, 0, tzinfo=UTC))
        self.assertEqual(s.iso_slot(MORNING), "2026-10-17T06:00+07:00")
        self.assertEqual(s.iso_slot(MORNING.astimezone(UTC)), "2026-10-17T06:00+07:00")

    def test_us_daylight_saving_change_does_not_move_vietnam_slots(self):
        # US clocks fall back on 2026-11-01; Vietnam stays at UTC+7 so slots stay exactly 24h apart.
        days = [s.slot_datetime(date(2026, 10, 31) + timedelta(days=i), hhmm) for i in range(3) for hhmm in ("06:00", "19:00")]
        for day in days:
            self.assertEqual(day.utcoffset(), H(7))
        self.assertEqual(s.slot_datetime(date(2026, 11, 1), "06:00").astimezone(UTC),
                         datetime(2026, 10, 31, 23, 0, tzinfo=UTC))
        self.assertEqual(s.slot_datetime(date(2026, 11, 2), "06:00") - s.slot_datetime(date(2026, 11, 1), "06:00"), H(24))
        self.assertEqual(s.slot_datetime(date(2026, 11, 1), "06:00") - s.slot_datetime(date(2026, 10, 31), "06:00"), H(24))
        slot = s.slot_datetime(date(2026, 11, 1), "19:00")
        self.assertEqual(s.image_deadline(slot), slot - H(3))
        self.assertTrue(s.is_due_for_publish(datetime(2026, 11, 1, 12, 0, tzinfo=UTC), slot))  # 19:00 VN

    def test_bad_inputs_raise(self):
        for bad in ("6:00", "24:00", "06:60", "0600", "", None, 600):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                s.parse_hhmm(bad)
        with self.assertRaises(ValueError):
            s.slot_datetime("2026-10-17", "06:00")
        with self.assertRaises(ValueError):
            s.image_deadline(datetime(2026, 10, 17, 6, 0))  # naive


class DeadlineTests(unittest.TestCase):
    def test_deadlines_and_window(self):
        self.assertEqual(s.image_deadline(MORNING), MORNING - H(3))
        self.assertEqual(s.approve_deadline(MORNING), MORNING - H(2))
        self.assertEqual(s.publish_window(MORNING), (MORNING, MORNING + M(15)))

    def test_publish_window_boundaries(self):
        one = timedelta(seconds=1)
        cases = [(MORNING - one, False), (MORNING, True), (MORNING + M(7), True), (MORNING + M(15), True),
                 (MORNING + M(15) + one, False), (MORNING + H(1), False)]
        for now, due in cases:
            with self.subTest(offset=now - MORNING):
                self.assertIs(s.is_due_for_publish(now, MORNING), due)
        self.assertIs(s.is_due_for_publish(MORNING + M(20), MORNING, late_minutes=30), True)

    def test_publish_window_is_the_same_in_any_timezone_representation(self):
        self.assertTrue(s.is_due_for_publish((MORNING + M(5)).astimezone(UTC), MORNING.astimezone(UTC)))


class PlanImageRunTests(unittest.TestCase):
    NOW = MORNING - H(30)

    def plan(self, slot, mode, **values):
        args = dict(fixed_at="01:00", now=self.NOW, seed=1)
        args.update(values)
        return s.plan_image_run(slot, mode, **args)

    def test_fixed_uses_latest_fixed_time_before_deadline(self):
        self.assertEqual(self.plan(MORNING, "fixed"), s.slot_datetime(date(2026, 10, 17), "01:00"))
        self.assertEqual(self.plan(EVENING, "fixed"), s.slot_datetime(date(2026, 10, 17), "01:00"))
        # Deadline for the 06:00 slot is 03:00: 04:00 is too late, the previous day's 04:00 falls before
        # slot - 24h, so the result is clamped to the window start.
        self.assertEqual(self.plan(MORNING, "fixed", fixed_at="04:00"), MORNING - H(24))
        # Exactly at slot - 3h is allowed.
        self.assertEqual(self.plan(MORNING, "fixed", fixed_at="03:00"), MORNING - H(3))
        self.assertEqual(self.plan(MORNING, "fixed", fixed_at="03:01"), MORNING - H(24))
        self.assertEqual(self.plan(MORNING, "fixed", fixed_at="06:00"), MORNING - H(24))
        self.assertEqual(self.plan(MORNING, "fixed", fixed_at="07:00"), s.slot_datetime(date(2026, 10, 16), "07:00"))
        for slot in (MORNING, EVENING):
            for at in ("00:00", "04:00", "12:00", "23:59"):
                picked = self.plan(slot, "fixed", fixed_at=at)
                self.assertLessEqual(picked, slot - H(3))
                self.assertGreaterEqual(picked, slot - H(24))

    def test_fixed_ignores_now(self):
        self.assertEqual(self.plan(MORNING, "fixed", now=MORNING - H(1)), self.plan(MORNING, "fixed", now=MORNING - H(40)))

    def test_random_is_inside_window_and_reproducible(self):
        picks = [self.plan(MORNING, "random", seed=seed) for seed in range(300)]
        for pick in picks:
            self.assertGreaterEqual(pick, MORNING - H(24))
            self.assertLessEqual(pick, MORNING - H(3))
        self.assertEqual(picks, [self.plan(MORNING, "random", seed=seed) for seed in range(300)])
        self.assertGreater(len(set(picks)), 250)
        self.assertEqual(self.plan(MORNING, "random", seed="run-42"), self.plan(MORNING, "random", seed="run-42", now=MORNING))
        self.assertNotEqual(self.plan(MORNING, "random", seed=1), self.plan(MORNING, "random", seed=2))
        self.assertLess(min(picks), MORNING - H(20))  # spread over the window, not clustered at an end
        self.assertGreater(max(picks), MORNING - H(7))

    def test_adaptive_boundaries(self):
        cases = [(MORNING - H(48), MORNING - H(24)),   # earlier than the window: wait for it
                 (MORNING - H(24), MORNING - H(24)),   # exactly at the start
                 (MORNING - H(10), MORNING - H(10)),   # inside: run as soon as free
                 (MORNING - H(3), MORNING - H(3)),     # exactly the deadline
                 (MORNING - H(2), MORNING - H(3)),     # past the deadline: capped
                 (MORNING + H(5), MORNING - H(3))]
        for now, expected in cases:
            with self.subTest(offset=now - MORNING):
                self.assertEqual(self.plan(MORNING, "adaptive", now=now), expected)

    def test_custom_window_hours(self):
        planned = self.plan(MORNING, "adaptive", now=MORNING - H(40), window_start_hours=12, window_end_hours=4)
        self.assertEqual(planned, MORNING - H(12))

    def test_invalid_arguments(self):
        with self.assertRaises(ValueError):
            self.plan(MORNING, "weekly")
        with self.assertRaises(ValueError):
            self.plan(MORNING, "fixed", window_start_hours=3, window_end_hours=3)
        with self.assertRaises(ValueError):
            self.plan(MORNING, "fixed", fixed_at="25:00")
        with self.assertRaises(ValueError):
            self.plan(MORNING, "adaptive", now=datetime(2026, 10, 16, 6, 0))

    def test_results_are_vietnam_time(self):
        for mode in s.MODES:
            self.assertEqual(self.plan(MORNING.astimezone(UTC), mode).utcoffset(), H(7))


class BackoffTests(unittest.TestCase):
    def test_backoff_doubles_from_five_minutes(self):
        self.assertEqual([s.adaptive_backoff(n) for n in range(6)], [M(5), M(10), M(20), M(40), M(80), M(160)])
        for bad in (-1, 1.5, "1", True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                s.adaptive_backoff(bad)

    def test_next_attempt_never_passes_the_image_deadline(self):
        now = MORNING - H(5)
        self.assertEqual(s.adaptive_next_attempt(now, 0, MORNING), now + M(5))
        self.assertEqual(s.adaptive_next_attempt(now, 3, MORNING), now + M(40))
        self.assertEqual(s.adaptive_next_attempt(now, 6, MORNING), MORNING - H(3))
        self.assertEqual(s.adaptive_next_attempt(MORNING - H(3), 0, MORNING), MORNING - H(3))


if __name__ == "__main__":
    unittest.main()
