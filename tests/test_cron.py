"""Tests for swarm_tminus.cron — 5-field cron expression parser."""

import datetime as _dt
import time
import unittest

from swarm_tminus.cron import CronParser, CronError, next_fire


class TestCronParserBasics(unittest.TestCase):
    def test_parse_all_stars(self):
        p = CronParser("* * * * *")
        result = p.parse()
        self.assertEqual(len(result["minute"]), 60)
        self.assertEqual(len(result["hour"]), 24)
        self.assertEqual(len(result["dom"]), 31)
        self.assertEqual(len(result["month"]), 12)
        self.assertEqual(len(result["dow"]), 7)

    def test_parse_exact(self):
        p = CronParser("0 9 * * *")
        result = p.parse()
        self.assertEqual(result["minute"], {0})
        self.assertEqual(result["hour"], {9})

    def test_parse_list(self):
        p = CronParser("0,15,30,45 * * * *")
        self.assertEqual(p.parse()["minute"], {0, 15, 30, 45})

    def test_parse_range(self):
        p = CronParser("0 9-17 * * *")
        self.assertEqual(p.parse()["hour"], set(range(9, 18)))

    def test_parse_step(self):
        p = CronParser("*/15 * * * *")
        self.assertEqual(p.parse()["minute"], {0, 15, 30, 45})

    def test_parse_step_with_start(self):
        p = CronParser("5/10 * * * *")
        self.assertEqual(p.parse()["minute"], {5, 15, 25, 35, 45, 55})

    def test_parse_range_step(self):
        p = CronParser("1-5/2 * * * *")
        self.assertEqual(p.parse()["minute"], {1, 3, 5})

    def test_invalid_field_count(self):
        with self.assertRaises(CronError):
            CronParser("* * *")
        with self.assertRaises(CronError):
            CronParser("* * * * * *")

    def test_invalid_value(self):
        with self.assertRaises(CronError):
            CronParser("60 * * * *")
        with self.assertRaises(CronError):
            CronParser("-1 * * * *")

    def test_invalid_range(self):
        with self.assertRaises(CronError):
            CronParser("10-5 * * * *")

    def test_invalid_step(self):
        with self.assertRaises(CronError):
            CronParser("*/0 * * * *")

    def test_empty(self):
        with self.assertRaises(CronError):
            CronParser("")
        with self.assertRaises(CronError):
            CronParser("   ")


class TestCronMatches(unittest.TestCase):
    def test_matches_minute_hour(self):
        p = CronParser("0 9 * * *")
        when = _dt.datetime(2026, 7, 15, 9, 0, 0, tzinfo=_dt.timezone.utc)
        self.assertTrue(p.matches(when))

    def test_no_match_minute(self):
        p = CronParser("0 9 * * *")
        when = _dt.datetime(2026, 7, 15, 9, 30, 0, tzinfo=_dt.timezone.utc)
        self.assertFalse(p.matches(when))

    def test_matches_dow_sunday_zero(self):
        # Cron: 0 = Sunday
        p = CronParser("0 0 * * 0")
        # 2026-07-19 is a Sunday
        when = _dt.datetime(2026, 7, 19, 0, 0, 0, tzinfo=_dt.timezone.utc)
        self.assertTrue(p.matches(when))

    def test_matches_dow_saturday_six(self):
        p = CronParser("0 0 * * 6")
        # 2026-07-18 is a Saturday
        when = _dt.datetime(2026, 7, 18, 0, 0, 0, tzinfo=_dt.timezone.utc)
        self.assertTrue(p.matches(when))

    def test_matches_unix(self):
        p = CronParser("0 9 * * *")
        # 2026-07-15 09:00:00 UTC
        ts = _dt.datetime(2026, 7, 15, 9, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        self.assertTrue(p.matches_unix(ts))


class TestCronNextFire(unittest.TestCase):
    def test_next_fire_every_minute(self):
        p = CronParser("* * * * *")
        # After a known time, next is at most 60s away
        after = _dt.datetime(2026, 7, 15, 9, 30, 0, tzinfo=_dt.timezone.utc).timestamp()
        nxt = p.next_fire(after_unix=after)
        self.assertLess(nxt, after + 120)

    def test_next_fire_specific(self):
        p = CronParser("0 9 * * *")
        # After 2026-07-15 08:00, next is 2026-07-15 09:00
        after = _dt.datetime(2026, 7, 15, 8, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        nxt = p.next_fire(after_unix=after)
        expected = _dt.datetime(2026, 7, 15, 9, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        self.assertEqual(nxt, expected)

    def test_next_fire_skips_same_minute(self):
        p = CronParser("0 9 * * *")
        # Exactly at 09:00 → next is tomorrow 09:00
        after = _dt.datetime(2026, 7, 15, 9, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        nxt = p.next_fire(after_unix=after)
        expected = _dt.datetime(2026, 7, 16, 9, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        self.assertEqual(nxt, expected)

    def test_next_fire_weekday(self):
        # 0 9 * * 1-5 = 9am Mon-Fri
        p = CronParser("0 9 * * 1-5")
        # 2026-07-18 is Saturday → next fire is Mon 2026-07-20 09:00
        after = _dt.datetime(2026, 7, 18, 12, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        nxt = p.next_fire(after_unix=after)
        expected = _dt.datetime(2026, 7, 20, 9, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        self.assertEqual(nxt, expected)

    def test_next_fire_convenience(self):
        after = _dt.datetime(2026, 7, 15, 8, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        nxt = next_fire("0 9 * * *", after_unix=after)
        expected = _dt.datetime(2026, 7, 15, 9, 0, 0, tzinfo=_dt.timezone.utc).timestamp()
        self.assertEqual(nxt, expected)

    def test_next_fire_no_arg_uses_now(self):
        # Without after_unix, uses time.time()
        nxt = next_fire("* * * * *")
        # Should be within the next minute
        self.assertLessEqual(nxt, time.time() + 65)

    def test_next_fire_every_15_minutes(self):
        p = CronParser("*/15 * * * *")
        after = _dt.datetime(2026, 7, 15, 9, 7, 0, tzinfo=_dt.timezone.utc).timestamp()
        nxt = p.next_fire(after_unix=after)
        expected = _dt.datetime(2026, 7, 15, 9, 15, 0, tzinfo=_dt.timezone.utc).timestamp()
        self.assertEqual(nxt, expected)


if __name__ == "__main__":
    unittest.main()