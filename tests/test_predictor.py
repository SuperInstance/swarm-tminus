"""Tests for swarm_tminus.predictor — predict-and-confirm pattern."""

import unittest

from swarm_tminus.predictor import (
    EVENT_TYPES,
    Predictor,
    Prediction,
    MessageSavings,
    ChordProgression,
    cr_impact,
    ii_v_i,
    twelve_bar_blues,
    chromatic,
    random as random_progression,
)


class TestPrediction(unittest.TestCase):
    def test_create_prediction(self):
        p = Prediction(
            id="abc",
            event_type="chord_change",
            predicted_at_beat=4.0,
            confidence=0.9,
            cr_impact=0.05,
        )
        self.assertEqual(p.id, "abc")
        self.assertFalse(p.confirmed)
        self.assertAlmostEqual(p.predicted_at_beat, 4.0)

    def test_repr_includes_type(self):
        p = Prediction(id="x", event_type="key_change", predicted_at_beat=2.0)
        r = repr(p)
        self.assertIn("key_change", r)


class TestProgressionDB(unittest.TestCase):
    def test_ii_v_i(self):
        prog = ii_v_i()
        self.assertEqual(prog.chords, ("ii", "V", "I"))
        self.assertAlmostEqual(prog.cr, 0.94)

    def test_blues_twelve_chords(self):
        prog = twelve_bar_blues()
        self.assertEqual(len(prog.chords), 12)
        self.assertAlmostEqual(prog.cr, 0.87)

    def test_random_low_cr(self):
        prog = random_progression()
        self.assertAlmostEqual(prog.cr, 0.31)
        self.assertAlmostEqual(prog.sigma_above_random, 0.0)

    def test_chromatic(self):
        prog = chromatic()
        self.assertEqual(len(prog.chords), 8)
        self.assertAlmostEqual(prog.cr, 0.62)

    def test_cr_ordering(self):
        crs = [
            random_progression().cr,
            chromatic().cr,
            twelve_bar_blues().cr,
            ii_v_i().cr,
        ]
        self.assertEqual(crs, sorted(crs))


class TestCrImpact(unittest.TestCase):
    def test_known_types_have_impact(self):
        for et in EVENT_TYPES:
            self.assertGreater(cr_impact(et), 0.0)

    def test_unknown_type_zero(self):
        self.assertEqual(cr_impact("nonexistent"), 0.0)


class TestPredictorBasics(unittest.TestCase):
    def test_creation(self):
        p = Predictor(120.0, "C")
        self.assertEqual(p.bpm, 120.0)
        self.assertEqual(p.current_beat, 0.0)
        self.assertEqual(p.key, "C")
        self.assertEqual(p.events, [])
        self.assertEqual(p.predictions_made, 0)

    def test_default_key(self):
        p = Predictor(60.0)
        self.assertEqual(p.key, "")

    def test_negative_bpm_raises(self):
        with self.assertRaises(ValueError):
            Predictor(bpm=-10.0)

    def test_zero_bpm_raises(self):
        with self.assertRaises(ValueError):
            Predictor(bpm=0.0)

    def test_repr(self):
        p = Predictor(120.0, "Am")
        r = repr(p)
        self.assertIn("120", r)
        self.assertIn("Am", r)


class TestAddPrediction(unittest.TestCase):
    def test_add_increments_counter(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 4.0, 0.9)
        self.assertEqual(len(p.events), 1)
        self.assertEqual(p.predictions_made, 1)
        self.assertAlmostEqual(p.events[0].predicted_at_beat, 4.0)
        self.assertFalse(p.events[0].confirmed)
        self.assertAlmostEqual(p.events[0].confidence, 0.9)

    def test_unknown_event_type_raises(self):
        p = Predictor(120.0)
        with self.assertRaises(ValueError):
            p.add_prediction("not_a_real_event", 4.0, 0.9)

    def test_invalid_confidence_raises(self):
        p = Predictor(120.0)
        with self.assertRaises(ValueError):
            p.add_prediction("chord_change", 4.0, 1.5)
        with self.assertRaises(ValueError):
            p.add_prediction("chord_change", 4.0, -0.1)

    def test_negative_beats_raises(self):
        p = Predictor(120.0)
        with self.assertRaises(ValueError):
            p.add_prediction("chord_change", -1.0, 0.5)

    def test_cr_impact_per_type(self):
        p = Predictor(120.0)
        # Key change should have higher cr than rest
        p.add_prediction("key_change", 4.0, 0.9)
        p.add_prediction("rest", 8.0, 0.5)
        self.assertGreater(p.events[0].cr_impact, p.events[1].cr_impact)

    def test_unique_ids(self):
        p = Predictor(120.0)
        ids = [p.add_prediction("chord_change", 4.0 + i, 0.9) for i in range(20)]
        self.assertEqual(len(ids), len(set(ids)))


class TestAdvance(unittest.TestCase):
    def test_advance_returns_in_window(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 4.0, 0.9)
        p.add_prediction("cadence", 8.0, 0.7)
        triggered = p.advance(5.0)
        self.assertEqual(len(triggered), 1)
        self.assertEqual(triggered[0].event_type, "chord_change")

    def test_advance_no_events(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 10.0, 0.9)
        triggered = p.advance(4.0)
        self.assertEqual(triggered, [])

    def test_advance_negative_raises(self):
        p = Predictor(120.0)
        with self.assertRaises(ValueError):
            p.advance(-1.0)

    def test_advance_increments_beat(self):
        p = Predictor(120.0)
        p.advance(5.0)
        self.assertAlmostEqual(p.current_beat, 5.0)

    def test_advance_boundary_excludes_exact(self):
        """`old <= beat < new`: events exactly at `old_beat` still fire; events at new_beat don't."""
        p = Predictor(120.0)
        p.add_prediction("chord_change", 0.0, 0.9)
        triggered = p.advance(2.0)
        self.assertEqual(len(triggered), 1)

        p2 = Predictor(120.0)
        p2.add_prediction("chord_change", 4.0, 0.9)
        triggered2 = p2.advance(4.0)
        self.assertEqual(len(triggered2), 0)

    def test_advance_in_seconds(self):
        p = Predictor(120.0)
        p.add_prediction_in_seconds("chord_change", 2.0, 0.9)  # 2s at 120bpm = 4 beats
        triggered = p.advance(5.0)
        self.assertEqual(len(triggered), 1)

    def test_advance_property_after_100_iterations(self):
        """Property: after 100 advances of 0.5 beat, total beat = 50; predictions within window fire."""
        p = Predictor(120.0)
        for i in range(100):
            p.add_prediction("chord_change", i + 0.5, 0.9)
        for _ in range(100):
            p.advance(0.5)
        # All 100 predictions should have fired exactly once total
        fired = sum(1 for ev in p.events if ev.predicted_at_beat < p.current_beat)
        # 100 events, all <= 99.5 < 50? NO. Only those < 50 fired.
        # Actually we added 100 events with beats 0.5..99.5, then advanced 50 beats total.
        # Events triggered: those with 0.5..50
        self.assertEqual(fired, 50)


class TestPredictNext(unittest.TestCase):
    def test_picks_soonest(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 8.0, 0.8)
        p.add_prediction("key_change", 4.0, 0.6)
        nxt = p.predict_next()
        self.assertIsNotNone(nxt)
        self.assertEqual(nxt.event_type, "key_change")

    def test_skips_past(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 4.0, 0.9)
        p.advance(10.0)
        self.assertIsNone(p.predict_next())

    def test_empty_returns_none(self):
        p = Predictor(120.0)
        self.assertIsNone(p.predict_next())


class TestCountdowns(unittest.TestCase):
    def test_countdown_beats(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 8.0, 0.9)
        p.current_beat = 2.0
        cd = p.countdown_beats(p.events[0])
        self.assertAlmostEqual(cd, 6.0)

    def test_countdown_seconds(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 8.0, 0.9)
        p.current_beat = 2.0
        secs = p.countdown_seconds(p.events[0])
        # 6 beats at 120 bpm = 3 seconds
        self.assertAlmostEqual(secs, 3.0)


class TestConfirm(unittest.TestCase):
    def test_confirm_first_time(self):
        p = Predictor(120.0)
        pid = p.add_prediction("chord_change", 4.0, 0.9)
        self.assertTrue(p.confirm(pid))
        self.assertTrue(p.events[0].confirmed)
        self.assertEqual(p.confirmations_sent, 1)

    def test_confirm_twice_returns_false(self):
        p = Predictor(120.0)
        pid = p.add_prediction("chord_change", 4.0, 0.9)
        self.assertTrue(p.confirm(pid))
        self.assertFalse(p.confirm(pid))

    def test_confirm_nonexistent(self):
        p = Predictor(120.0)
        self.assertFalse(p.confirm("nonexistent_id"))


class TestMessageSavings(unittest.TestCase):
    def test_with_predictions_and_one_confirm(self):
        p = Predictor(120.0)
        p.add_prediction("chord_change", 4.0, 0.9)
        p.add_prediction("key_change", 8.0, 0.8)
        pid = p.events[0].id
        p.confirm(pid)
        s = p.message_savings()
        self.assertEqual(s.predictions_made, 2)
        self.assertEqual(s.confirmations_sent, 1)
        self.assertEqual(s.polling_equivalent, 20)
        # savings = 1 - (2 + 1) / 20 = 0.85
        self.assertAlmostEqual(s.savings_ratio, 0.85)

    def test_empty_no_savings(self):
        p = Predictor(120.0)
        s = p.message_savings()
        self.assertEqual(s.predictions_made, 0)
        self.assertEqual(s.savings_ratio, 0.0)


class TestAddProgression(unittest.TestCase):
    def test_add_progression_blues(self):
        p = Predictor(120.0)
        prog = twelve_bar_blues()
        ids = p.add_progression(prog)
        self.assertEqual(len(ids), 12)
        # First chord at beat 0
        self.assertAlmostEqual(p.events[0].predicted_at_beat, 0.0)
        # Last chord at beat 11 * 4 = 44
        self.assertAlmostEqual(p.events[11].predicted_at_beat, 44.0)


class TestSecondsHelpers(unittest.TestCase):
    def test_beats_to_seconds(self):
        p = Predictor(120.0)
        self.assertAlmostEqual(p.beat_to_seconds(120.0), 60.0)

    def test_seconds_to_beats(self):
        p = Predictor(120.0)
        self.assertAlmostEqual(p.seconds_to_beats(60.0), 120.0)


class TestEventTypeCoverage(unittest.TestCase):
    def test_all_event_types_addable(self):
        p = Predictor(120.0)
        for et in EVENT_TYPES:
            pid = p.add_prediction(et, 4.0, 0.5)
            self.assertTrue(pid)


if __name__ == "__main__":
    unittest.main()