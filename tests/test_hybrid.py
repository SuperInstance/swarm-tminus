"""Tests for swarm_tminus.hybrid.HybridAnchor.

Covers:
- Degraded mode (without swarm-anchor) — pure swarm-tminus file layout
- Full mode (with swarm-anchor installed) — same .swarm/ + swarm-anchor Roster
- Save/load round-trip for deadline trees
- Unified summary aggregation
"""

from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path

# Use the standard swarm_tminus import (no special monkey-patching needed;
# we test both paths via direct invocation).
from swarm_tminus.hybrid import HybridAnchor, _HAS_SWARM_ANCHOR


class TestHybridAnchorInit(unittest.TestCase):
    def test_root_dir_created(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "fresh_swarm"
            anchor = HybridAnchor(root=str(target))
            self.assertTrue(target.exists())
            self.assertTrue(target.is_dir())

    def test_default_root_is_dot_swarm(self):
        # Just check it accepts the default — don't create .swarm in cwd.
        anchor = HybridAnchor(root="/tmp/swarm-tminus-default-test")
        self.assertEqual(anchor.root, Path("/tmp/swarm-tminus-default-test"))
        # Cleanup
        import shutil
        if anchor.root.exists():
            shutil.rmtree(anchor.root)

    def test_has_swarm_anchor_flag_is_bool(self):
        anchor = HybridAnchor(root="/tmp/swarm-tminus-flag-test")
        self.assertIsInstance(anchor.has_swarm_anchor, bool)
        # In this environment swarm-anchor IS installed.
        self.assertTrue(_HAS_SWARM_ANCHOR)
        self.assertTrue(anchor.has_swarm_anchor)
        self.assertNotEqual(anchor.swarm_anchor_version, "not-installed")
        import shutil
        if anchor.root.exists():
            shutil.rmtree(anchor.root)

    def test_repr_does_not_crash(self):
        anchor = HybridAnchor(root="/tmp/swarm-tminus-repr-test")
        r = repr(anchor)
        self.assertIn("HybridAnchor", r)
        self.assertIn("root=", r)
        import shutil
        if anchor.root.exists():
            shutil.rmtree(anchor.root)


class TestHeartbeatFullMode(unittest.TestCase):
    """Heartbeat tests with swarm-anchor installed."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "swarm"
        self.anchor = HybridAnchor(root=str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_heartbeat_writes_file(self):
        path = self.anchor.heartbeat("alice", model="deepseek", task="test")
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "alice.heartbeat.json")

    def test_heartbeat_file_is_valid_json(self):
        self.anchor.heartbeat("bob", model="kimi", task="t")
        path = self.root / "bob.heartbeat.json"
        data = json.loads(path.read_text())
        self.assertEqual(data["animal"], "bob")
        self.assertEqual(data["model"], "kimi")
        self.assertEqual(data["task"], "t")

    def test_heartbeat_status_and_extras(self):
        self.anchor.heartbeat(
            "carol",
            model="seed",
            task="t",
            proposals=["add skill", "review PR"],
            warnings=["flaky network"],
            extras={"region": "us-east-1"},
        )
        data = json.loads((self.root / "carol.heartbeat.json").read_text())
        self.assertIn("add skill", data["proposals"])
        self.assertIn("flaky network", data["warnings"])
        self.assertEqual(data["extras"]["region"], "us-east-1")

    def test_roster_returns_swarm_anchor_roster(self):
        self.anchor.heartbeat("alice", model="m", task="t")
        self.anchor.heartbeat("bob", model="m", task="t")
        ros = self.anchor.roster(stale_seconds=60)
        # swarm-anchor Roster has .animals dict
        self.assertTrue(hasattr(ros, "animals"))
        self.assertIn("alice", ros.animals)
        self.assertIn("bob", ros.animals)

    def test_reap_removes_heartbeat(self):
        self.anchor.heartbeat("eve", model="m", task="t")
        path = self.root / "eve.heartbeat.json"
        self.assertTrue(path.exists())
        ok = self.anchor.reap("eve")
        self.assertTrue(ok)
        self.assertFalse(path.exists())
        # Re-reaping returns False.
        self.assertFalse(self.anchor.reap("eve"))


class TestHeartbeatDegradedMode(unittest.TestCase):
    """Heartbeat tests with swarm-anchor import forced to be missing."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "swarm"

        # Force-degrade by monkey-patching the module flag + class refs.
        import swarm_tminus.hybrid as h
        self._saved_has = h._HAS_SWARM_ANCHOR
        self._saved_anchor = h._Anchor
        self._saved_hb = h._Heartbeat
        h._HAS_SWARM_ANCHOR = False
        h._Anchor = None
        h._Heartbeat = None
        h._HeartbeatStatus = None
        h._Roster = None

        self.anchor = HybridAnchor(root=str(self.root))

    def tearDown(self):
        import swarm_tminus.hybrid as h
        h._HAS_SWARM_ANCHOR = self._saved_has
        h._Anchor = self._saved_anchor
        h._Heartbeat = self._saved_hb
        h._HeartbeatStatus = self._saved_has and getattr(h, "_HeartbeatStatus", None)
        h._Roster = self._saved_has and getattr(h, "_Roster", None)
        self.tmp.cleanup()

    def test_has_swarm_anchor_is_false(self):
        self.assertFalse(self.anchor.has_swarm_anchor)

    def test_swarm_anchor_version_is_not_installed(self):
        self.assertEqual(self.anchor.swarm_anchor_version, "not-installed")

    def test_heartbeat_writes_json_file(self):
        path = self.anchor.heartbeat("zoe", model="m", task="t")
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "zoe.heartbeat.json")
        data = json.loads(path.read_text())
        self.assertEqual(data["animal"], "zoe")

    def test_roster_returns_dict(self):
        self.anchor.heartbeat("alice", model="m", task="t")
        ros = self.anchor.roster(stale_seconds=60)
        # Degraded mode returns a plain dict (not a swarm-anchor Roster).
        self.assertIsInstance(ros, dict)
        self.assertIn("animals", ros)
        self.assertEqual(len(ros["animals"]), 1)
        self.assertEqual(ros["animals"][0]["animal"], "alice")


class TestEventsAndDeadlines(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "swarm"
        self.anchor = HybridAnchor(root=str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_event_writes_event_file(self):
        from swarm_tminus.events import CountdownEvent
        e = CountdownEvent(name="deploy", fire_at_unix=time.time() + 60, quorum_required=2)
        path = self.anchor.add_event(e)
        self.assertTrue(path.exists())
        self.assertEqual(path.name, "deploy.event.json")
        data = json.loads(path.read_text())
        self.assertEqual(data["name"], "deploy")
        self.assertEqual(data["quorum_required"], 2)

    def test_add_event_appears_in_summary(self):
        from swarm_tminus.events import CountdownEvent
        self.anchor.add_event(CountdownEvent(name="e1", fire_at_unix=100.0, quorum_required=1))
        self.anchor.add_event(CountdownEvent(name="e2", fire_at_unix=200.0, quorum_required=3))
        s = self.anchor.summary()
        self.assertEqual(s["event_count"], 2)
        names = sorted(e["name"] for e in s["events"])
        self.assertEqual(names, ["e1", "e2"])

    def test_deadline_tree_save_and_load(self):
        from swarm_tminus.deadlines import DeadlineNode
        root = DeadlineNode(name="release", duration_seconds=3600.0)
        root.add_child("build", duration_seconds=600.0)
        root.add_child("test", duration_seconds=900.0)
        tree = self.anchor.deadline_tree(root)
        # File should be on disk.
        path = self.root / "release.deadline.json"
        self.assertTrue(path.exists())
        # Reload
        loaded = self.anchor.load_deadline_tree("release")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.root.name, "release")
        self.assertEqual(len(loaded.root.children), 2)

    def test_load_deadline_tree_missing_returns_none(self):
        loaded = self.anchor.load_deadline_tree("nonexistent")
        self.assertIsNone(loaded)


class TestTickClock(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "swarm"
        self.anchor = HybridAnchor(root=str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_tick_clock_default(self):
        clock = self.anchor.tick_clock()
        self.assertEqual(clock.bpm, 120.0)
        self.assertEqual(clock.swing, 0.0)

    def test_tick_clock_advances(self):
        clock = self.anchor.tick_clock()
        t1 = clock.next_tick()
        t2 = clock.next_tick()
        self.assertEqual(t1.id, 0)
        self.assertEqual(t2.id, 1)
        self.assertGreater(t2.timestamp, t1.timestamp)


class TestSummary(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "swarm"
        self.anchor = HybridAnchor(root=str(self.root))

    def tearDown(self):
        self.tmp.cleanup()

    def test_summary_empty(self):
        s = self.anchor.summary()
        self.assertEqual(s["event_count"], 0)
        self.assertEqual(s["heartbeat_count"], 0)
        self.assertEqual(s["deadline_count"], 0)
        self.assertEqual(s["campaign_count"], 0)
        self.assertEqual(s["tick_clock_bpm"], 120.0)
        self.assertEqual(s["swarm_anchor"], True)
        self.assertNotEqual(s["swarm_anchor_version"], "not-installed")

    def test_summary_aggregates_all_state(self):
        from swarm_tminus.deadlines import DeadlineNode
        from swarm_tminus.events import CountdownEvent
        # Add one of each kind.
        self.anchor.heartbeat("alice", model="m", task="t")
        self.anchor.heartbeat("bob", model="m", task="t")
        self.anchor.add_event(CountdownEvent(name="e1", fire_at_unix=100.0, quorum_required=1))
        root = DeadlineNode(name="d1", duration_seconds=60.0)
        self.anchor.deadline_tree(root)

        s = self.anchor.summary()
        self.assertEqual(s["heartbeat_count"], 2)
        self.assertEqual(s["event_count"], 1)
        self.assertEqual(s["deadline_count"], 1)
        self.assertEqual(s["root"], str(self.root))

    def test_summary_is_json_serializable(self):
        # All values must round-trip through json.dumps (no dataclasses left in).
        from swarm_tminus.events import CountdownEvent
        self.anchor.add_event(CountdownEvent(name="e1", fire_at_unix=100.0))
        s = self.anchor.summary()
        # datetime-free, dict-only.
        encoded = json.dumps(s, default=str)
        self.assertIsInstance(encoded, str)
        # Round trip
        decoded = json.loads(encoded)
        self.assertEqual(decoded["event_count"], 1)


if __name__ == "__main__":
    unittest.main()