"""Tests for swarm_tminus.matcher — typed event matching."""

import unittest

from swarm_tminus.matcher import EventMatcher, EventMatch, find_matches


class TestEventMatcherBasics(unittest.TestCase):
    def test_simple_eq(self):
        m = EventMatcher(name="speed_eq", pattern={"speed": 100})
        self.assertTrue(m.matches({"speed": 100}))
        self.assertFalse(m.matches({"speed": 99}))

    def test_dict_op(self):
        m = EventMatcher(name="speed_gt", pattern={"speed": {"gt": 100}})
        self.assertTrue(m.matches({"speed": 101}))
        self.assertFalse(m.matches({"speed": 100}))  # not strictly greater
        self.assertFalse(m.matches({"speed": 50}))

    def test_gte(self):
        m = EventMatcher(name="gte", pattern={"x": {"gte": 10}})
        self.assertTrue(m.matches({"x": 10}))
        self.assertTrue(m.matches({"x": 11}))
        self.assertFalse(m.matches({"x": 9}))

    def test_lt_lte(self):
        m1 = EventMatcher(name="lt", pattern={"x": {"lt": 5}})
        self.assertTrue(m1.matches({"x": 4}))
        self.assertFalse(m1.matches({"x": 5}))

        m2 = EventMatcher(name="lte", pattern={"x": {"lte": 5}})
        self.assertTrue(m2.matches({"x": 5}))

    def test_neq(self):
        m = EventMatcher(name="neq", pattern={"x": {"neq": "foo"}})
        self.assertTrue(m.matches({"x": "bar"}))
        self.assertFalse(m.matches({"x": "foo"}))

    def test_in(self):
        m = EventMatcher(name="in", pattern={"color": {"in": ["red", "blue"]}})
        self.assertTrue(m.matches({"color": "red"}))
        self.assertFalse(m.matches({"color": "green"}))

    def test_contains_string(self):
        m = EventMatcher(name="contains", pattern={"name": {"contains": "alice"}})
        self.assertTrue(m.matches({"name": "alice smith"}))
        self.assertFalse(m.matches({"name": "bob"}))

    def test_contains_list(self):
        m = EventMatcher(name="contains", pattern={"tags": {"contains": "urgent"}})
        self.assertTrue(m.matches({"tags": ["urgent", "low"]}))
        self.assertFalse(m.matches({"tags": ["low"]}))

    def test_exists(self):
        m = EventMatcher(name="exists", pattern={"x": {"exists": True}})
        self.assertTrue(m.matches({"x": 5}))
        self.assertTrue(m.matches({"x": 0}))
        self.assertFalse(m.matches({"x": None}))
        self.assertFalse(m.matches({}))

    def test_absent(self):
        m = EventMatcher(name="absent", pattern={"x": {"absent": True}})
        self.assertTrue(m.matches({}))
        self.assertTrue(m.matches({"x": None}))
        self.assertFalse(m.matches({"x": 5}))

    def test_unknown_op(self):
        m = EventMatcher(name="bad", pattern={"x": {"weird_op": 1}})
        self.assertFalse(m.matches({"x": 1}))

    def test_type_special(self):
        m = EventMatcher(name="typed", pattern={"__type__": "sensor_reading"})
        self.assertTrue(m.matches({"type": "sensor_reading", "value": 5}))
        self.assertFalse(m.matches({"type": "alert", "value": 5}))

    def test_missing_key(self):
        m = EventMatcher(name="missing", pattern={"x": 5})
        self.assertFalse(m.matches({}))

    def test_invalid_confidence(self):
        with self.assertRaises(ValueError):
            EventMatcher(name="x", pattern={}, confidence=2.0)
        with self.assertRaises(ValueError):
            EventMatcher(name="x", pattern={}, confidence=-0.5)

    def test_invalid_energy_cost(self):
        with self.assertRaises(ValueError):
            EventMatcher(name="x", pattern={}, energy_cost=-1.0)

    def test_executable(self):
        m = EventMatcher(name="x", pattern={}, energy_cost=10.0)
        self.assertTrue(m.executable(20.0))
        self.assertTrue(m.executable(10.0))
        self.assertFalse(m.executable(5.0))

    def test_repr(self):
        m = EventMatcher(name="x", pattern={"y": 5})
        self.assertIn("x", repr(m))


class TestEventMatch(unittest.TestCase):
    def test_can_execute(self):
        m = EventMatcher(name="x", pattern={}, energy_cost=5.0)
        em = EventMatch(matcher=m, actual={}, confidence=0.9)
        self.assertTrue(em.can_execute(10.0))
        self.assertFalse(em.can_execute(1.0))

    def test_repr(self):
        m = EventMatcher(name="x", pattern={})
        em = EventMatch(matcher=m, actual={"a": 1}, confidence=0.5)
        self.assertIn("x", repr(em))


class TestFindMatches(unittest.TestCase):
    def test_find_matches_sorted(self):
        matchers = [
            EventMatcher(name="low", pattern={"x": 1}, confidence=0.3),
            EventMatcher(name="high", pattern={"x": 1}, confidence=0.9),
            EventMatcher(name="mid", pattern={"x": 1}, confidence=0.6),
        ]
        results = find_matches(matchers, {"x": 1})
        names = [r.matcher.name for r in results]
        self.assertEqual(names, ["high", "mid", "low"])

    def test_find_matches_filters(self):
        matchers = [
            EventMatcher(name="x_eq", pattern={"x": 1}),
            EventMatcher(name="y_eq", pattern={"y": 1}),
        ]
        results = find_matches(matchers, {"x": 1})
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].matcher.name, "x_eq")

    def test_find_matches_empty(self):
        results = find_matches([], {"x": 1})
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()