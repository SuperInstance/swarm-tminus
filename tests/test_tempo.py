"""Tests for swarm_tminus.tempo — BPM-adaptive tick clocks."""

import unittest

from swarm_tminus.tempo import (
    Tick, TickClock, BPM, TempoNegotiator,
    bpm_to_seconds, seconds_to_bpm, swing_offset,
)


class TestBpmHelpers(unittest.TestCase):
    def test_bpm_to_seconds(self):
        self.assertAlmostEqual(bpm_to_seconds(120.0), 0.5)
        self.assertAlmostEqual(bpm_to_seconds(60.0), 1.0)
        self.assertAlmostEqual(bpm_to_seconds(240.0), 0.25)

    def test_bpm_to_seconds_invalid(self):
        with self.assertRaises(ValueError):
            bpm_to_seconds(0)
        with self.assertRaises(ValueError):
            bpm_to_seconds(-1)

    def test_seconds_to_bpm(self):
        self.assertAlmostEqual(seconds_to_bpm(0.5), 120.0)
        self.assertAlmostEqual(seconds_to_bpm(1.0), 60.0)

    def test_seconds_to_bpm_invalid(self):
        with self.assertRaises(ValueError):
            seconds_to_bpm(0)
        with self.assertRaises(ValueError):
            seconds_to_bpm(-1)

    def test_swing_offset_even_tick(self):
        # Even tick ids: no swing
        self.assertEqual(swing_offset(0.5, 1.0, 0), 0.0)
        self.assertEqual(swing_offset(0.5, 1.0, 2), 0.0)
        self.assertEqual(swing_offset(0.5, 1.0, 4), 0.0)

    def test_swing_offset_odd_tick(self):
        # Odd tick ids: interval * swing * 0.33
        self.assertAlmostEqual(swing_offset(0.5, 1.0, 1), 0.165)
        self.assertAlmostEqual(swing_offset(0.5, 0.5, 1), 0.0825)

    def test_swing_clamped(self):
        # swing > 1.0 is clamped to 1.0
        self.assertAlmostEqual(swing_offset(0.5, 2.0, 1), 0.165)
        # swing < 0 is clamped to 0.0
        self.assertAlmostEqual(swing_offset(0.5, -0.5, 1), 0.0)


class TestBPM(unittest.TestCase):
    def test_creation(self):
        b = BPM(bpm=120.0)
        self.assertEqual(b.bpm, 120.0)
        self.assertAlmostEqual(b.seconds(), 0.5)

    def test_invalid(self):
        with self.assertRaises(ValueError):
            BPM(bpm=0)
        with self.assertRaises(ValueError):
            BPM(bpm=-1)

    def test_repr(self):
        b = BPM(bpm=120.0)
        self.assertIn("120", repr(b))


class TestTickClock(unittest.TestCase):
    def test_creation(self):
        c = TickClock(bpm=120.0, swing=0.0)
        self.assertEqual(c.bpm, 120.0)
        self.assertEqual(c.swing, 0.0)
        self.assertEqual(c.next_tick_id, 0)

    def test_invalid_bpm(self):
        with self.assertRaises(ValueError):
            TickClock(bpm=0)
        with self.assertRaises(ValueError):
            TickClock(bpm=-1)

    def test_swing_clamped(self):
        c = TickClock(bpm=120.0, swing=2.0)
        self.assertEqual(c.swing, 1.0)
        c = TickClock(bpm=120.0, swing=-0.5)
        self.assertEqual(c.swing, 0.0)

    def test_tick_interval(self):
        c = TickClock(bpm=120.0)
        self.assertAlmostEqual(c.tick_interval(), 0.5)
        c = TickClock(bpm=60.0)
        self.assertAlmostEqual(c.tick_interval(), 1.0)

    def test_swing_for_tick(self):
        c = TickClock(bpm=120.0, swing=1.0)
        self.assertAlmostEqual(c.swing_for_tick(1), 0.165)
        self.assertAlmostEqual(c.swing_for_tick(0), 0.0)

    def test_next_tick_advances(self):
        c = TickClock(bpm=120.0)
        t0 = c.next_tick(now_unix=1000.0)
        self.assertEqual(t0.id, 0)
        self.assertAlmostEqual(t0.timestamp, 1000.5)
        self.assertAlmostEqual(t0.delta, 0.5)
        self.assertEqual(c.next_tick_id, 1)

    def test_next_tick_with_swing(self):
        c = TickClock(bpm=120.0, swing=1.0)
        t0 = c.next_tick(now_unix=1000.0)
        self.assertAlmostEqual(t0.delta, 0.5)  # even tick, no swing
        t1 = c.next_tick(now_unix=1000.0)
        self.assertAlmostEqual(t1.delta, 0.5 + 0.165)

    def test_peek_does_not_advance(self):
        c = TickClock(bpm=120.0)
        t = c.peek_next_tick(now_unix=1000.0)
        self.assertEqual(t.id, 0)
        self.assertEqual(c.next_tick_id, 0)

    def test_adapt_high_energy(self):
        c = TickClock(bpm=120.0)
        c.adapt(1.0)
        self.assertGreater(c.bpm, 120.0)

    def test_adapt_low_energy(self):
        c = TickClock(bpm=120.0)
        c.adapt(0.0)
        self.assertLess(c.bpm, 120.0)

    def test_adapt_neutral(self):
        c = TickClock(bpm=120.0)
        c.adapt(0.5)
        self.assertAlmostEqual(c.bpm, 120.0)

    def test_adapt_clamped_min(self):
        c = TickClock(bpm=120.0)
        for _ in range(10):
            c.adapt(0.0)
        # Should clamp at min_bpm (default 30)
        self.assertGreaterEqual(c.bpm, c.min_bpm)

    def test_adapt_clamped_max(self):
        c = TickClock(bpm=120.0)
        for _ in range(10):
            c.adapt(1.0)
        self.assertLessEqual(c.bpm, c.max_bpm)

    def test_adapt_clamps_energy(self):
        c = TickClock(bpm=120.0)
        c.adapt(2.0)  # over 1.0
        # Should still produce a valid bpm <= max
        self.assertLessEqual(c.bpm, c.max_bpm)

    def test_reset(self):
        c = TickClock(bpm=120.0)
        c.next_tick()
        c.next_tick()
        c.reset()
        self.assertEqual(c.next_tick_id, 0)

    def test_reset_with_time(self):
        c = TickClock(bpm=120.0)
        c.next_tick()
        c.reset(now_unix=5000.0)
        self.assertEqual(c.next_tick_id, 0)
        self.assertEqual(c.started_at_unix, 5000.0)

    def test_repr(self):
        c = TickClock(bpm=120.0)
        self.assertIn("120", repr(c))


class TestTempoNegotiator(unittest.TestCase):
    def test_empty(self):
        n = TempoNegotiator()
        self.assertIsNone(n.negotiated())

    def test_single(self):
        n = TempoNegotiator()
        n.propose(120.0)
        self.assertEqual(n.negotiated(), 120.0)

    def test_agreement(self):
        n = TempoNegotiator()
        n.propose(120.0)
        n.propose(120.5)
        n.propose(119.5)
        self.assertIsNotNone(n.negotiated())

    def test_no_agreement(self):
        n = TempoNegotiator()
        n.propose(60.0)
        n.propose(180.0)
        self.assertIsNone(n.negotiated())

    def test_invalid_propose(self):
        n = TempoNegotiator()
        with self.assertRaises(ValueError):
            n.propose(0)
        with self.assertRaises(ValueError):
            n.propose(-1)

    def test_repr(self):
        n = TempoNegotiator()
        n.propose(120.0)
        self.assertIn("120", repr(n))


class TestTick(unittest.TestCase):
    def test_namedtuple(self):
        t = Tick(id=0, timestamp=100.0, delta=0.5)
        self.assertEqual(t.id, 0)
        self.assertEqual(t.timestamp, 100.0)
        self.assertEqual(t.delta, 0.5)


if __name__ == "__main__":
    unittest.main()