"""Tests for swarm_tminus.campaign — DAG of countdown events."""

import tempfile
import time
import unittest

from swarm_tminus.campaign import Campaign, CycleError, topological_order
from swarm_tminus.events import CountdownEvent, EventStatus


class TestTopologicalOrder(unittest.TestCase):
    def test_linear(self):
        order = topological_order([("a", "b"), ("b", "c")], nodes=["a", "b", "c"])
        self.assertEqual(order, ["a", "b", "c"])

    def test_diamond(self):
        edges = [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]
        order = topological_order(edges, nodes=["a", "b", "c", "d"])
        self.assertEqual(order[0], "a")
        self.assertEqual(order[-1], "d")
        # b and c are between
        self.assertIn("b", order[1:3])
        self.assertIn("c", order[1:3])

    def test_no_deps(self):
        order = topological_order([], nodes=["x", "y", "z"])
        self.assertEqual(sorted(order), ["x", "y", "z"])

    def test_cycle_detected(self):
        edges = [("a", "b"), ("b", "c"), ("c", "a")]
        with self.assertRaises(CycleError):
            topological_order(edges, nodes=["a", "b", "c"])

    def test_self_cycle(self):
        edges = [("a", "a")]
        with self.assertRaises(CycleError):
            topological_order(edges, nodes=["a"])

    def test_inferred_nodes(self):
        order = topological_order([("x", "y")])
        self.assertEqual(order, ["x", "y"])


class TestCampaign(unittest.TestCase):
    def _build_campaign(self) -> Campaign:
        c = Campaign(name="deploy-pipeline")
        c.add_event(CountdownEvent(name="build", fire_at_unix=1000.0))
        c.add_event(CountdownEvent(name="test", fire_at_unix=1100.0))
        c.add_event(CountdownEvent(name="deploy", fire_at_unix=1200.0))
        c.add_edge("build", "test")
        c.add_edge("test", "deploy")
        return c

    def test_creation(self):
        c = Campaign(name="empty")
        self.assertEqual(c.name, "empty")
        self.assertEqual(c.events, [])
        self.assertEqual(c.edges, [])

    def test_add_event(self):
        c = Campaign(name="c")
        c.add_event(CountdownEvent(name="e1", fire_at_unix=100.0))
        self.assertEqual(len(c.events), 1)
        self.assertIsNotNone(c.get("e1"))

    def test_add_duplicate_event_raises(self):
        c = Campaign(name="c")
        c.add_event(CountdownEvent(name="e1", fire_at_unix=100.0))
        with self.assertRaises(ValueError):
            c.add_event(CountdownEvent(name="e1", fire_at_unix=200.0))

    def test_add_edge_unknown_node(self):
        c = Campaign(name="c")
        c.add_event(CountdownEvent(name="a", fire_at_unix=100.0))
        with self.assertRaises(KeyError):
            c.add_edge("a", "nope")
        with self.assertRaises(KeyError):
            c.add_edge("nope", "a")

    def test_add_edge_self_loop(self):
        c = Campaign(name="c")
        c.add_event(CountdownEvent(name="a", fire_at_unix=100.0))
        with self.assertRaises(ValueError):
            c.add_edge("a", "a")

    def test_topological_order(self):
        c = self._build_campaign()
        order = c.topological_order()
        self.assertEqual(order, ["build", "test", "deploy"])

    def test_cycle_check_valid(self):
        c = self._build_campaign()
        self.assertTrue(c.cycle_check())

    def test_cycle_check_invalid(self):
        c = Campaign(name="c")
        c.add_event(CountdownEvent(name="a", fire_at_unix=100.0))
        c.add_event(CountdownEvent(name="b", fire_at_unix=200.0))
        c.add_edge("a", "b")
        # Manually add a cycle edge to bypass the cycle check on add
        c.edges.append(("b", "a"))
        self.assertFalse(c.cycle_check())

    def test_get(self):
        c = self._build_campaign()
        self.assertIsNotNone(c.get("build"))
        self.assertIsNone(c.get("nope"))

    def test_repr(self):
        c = self._build_campaign()
        self.assertIn("deploy-pipeline", repr(c))


class TestCampaignFileIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, ignore_errors=True))

    def test_save_and_load(self):
        c = Campaign(name="rollout")
        c.add_event(CountdownEvent(name="step1", fire_at_unix=1000.0, quorum_required=2))
        c.add_event(CountdownEvent(name="step2", fire_at_unix=2000.0))
        c.add_event(CountdownEvent(name="step3", fire_at_unix=3000.0))
        c.add_edge("step1", "step2")
        c.add_edge("step2", "step3")
        c.save(self.tmpdir)

        loaded = Campaign.load(self.tmpdir, "rollout")
        self.assertEqual(loaded.name, "rollout")
        self.assertEqual(len(loaded.events), 3)
        self.assertEqual(loaded.edges, [("step1", "step2"), ("step2", "step3")])
        self.assertEqual(loaded.get("step1").quorum_required, 2)

    def test_roundtrip_preserves_subscribers(self):
        c = Campaign(name="c")
        e = CountdownEvent(name="e1", fire_at_unix=1000.0)
        e.confirm("alice")
        c.add_event(e)
        c.save(self.tmpdir)
        loaded = Campaign.load(self.tmpdir, "c")
        loaded_e = loaded.get("e1")
        from swarm_tminus.events import SubscriberStatus
        self.assertEqual(loaded_e.subscriber_statuses["alice"], SubscriberStatus.CONFIRMED)


if __name__ == "__main__":
    unittest.main()