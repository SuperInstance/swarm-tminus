"""Tests for swarm_tminus.deadlines — hierarchical deadline trees."""

import tempfile
import time
import unittest

from swarm_tminus.deadlines import (
    DeadlineNode, DeadlineTree, DeadlineStatus, cascade_cancel,
)


class TestDeadlineNode(unittest.TestCase):
    def test_creation(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        self.assertEqual(n.name, "root")
        self.assertEqual(n.duration_seconds, 60.0)
        self.assertEqual(n.status, DeadlineStatus.ACTIVE)
        self.assertEqual(n.children, [])

    def test_repr(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        self.assertIn("root", repr(n))

    def test_add_child(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        c = n.add_child("child", duration_seconds=30.0)
        self.assertEqual(c.name, "child")
        self.assertEqual(c.parent, n)
        self.assertEqual(len(n.children), 1)

    def test_start(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        self.assertIsNone(n.started_at_unix)
        n.start(1000.0)
        self.assertEqual(n.started_at_unix, 1000.0)

    def test_complete(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        n.start(1000.0)
        n.complete(1030.0)
        self.assertEqual(n.status, DeadlineStatus.COMPLETED)
        self.assertEqual(n.completed_at_unix, 1030.0)

    def test_remaining_when_active(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        n.start(1000.0)
        self.assertAlmostEqual(n.remaining(1030.0), 30.0)

    def test_remaining_when_not_started(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        self.assertIsNone(n.remaining(1000.0))

    def test_remaining_zero_at_expiry(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        n.start(1000.0)
        self.assertEqual(n.remaining(1100.0), 0.0)

    def test_is_expired(self):
        n = DeadlineNode(name="root", duration_seconds=60.0)
        n.start(1000.0)
        self.assertFalse(n.is_expired(1030.0))
        self.assertTrue(n.is_expired(1100.0))
        # Already terminal
        n.complete(1030.0)
        self.assertFalse(n.is_expired(2000.0))


class TestCascadeCancel(unittest.TestCase):
    def test_cascade_simple(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        root.add_child("a", duration_seconds=30.0)
        root.add_child("b", duration_seconds=30.0)
        cancelled = cascade_cancel(root)
        self.assertEqual(cancelled, 3)  # root + 2 children
        self.assertEqual(root.status, DeadlineStatus.CANCELLED)
        for c in root.children:
            self.assertEqual(c.status, DeadlineStatus.CANCELLED)

    def test_cascade_deep(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        a = root.add_child("a", duration_seconds=30.0)
        b = a.add_child("b", duration_seconds=10.0)
        c = b.add_child("c", duration_seconds=5.0)
        cancelled = cascade_cancel(root)
        self.assertEqual(cancelled, 4)
        for n in [root, a, b, c]:
            self.assertEqual(n.status, DeadlineStatus.CANCELLED)

    def test_cascade_idempotent(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        root.add_child("a", duration_seconds=30.0)
        first = cascade_cancel(root)
        self.assertEqual(first, 2)
        # Recancelling the same root returns 0
        second = cascade_cancel(root)
        self.assertEqual(second, 0)

    def test_cascade_skips_completed(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        a = root.add_child("a", duration_seconds=30.0)
        a.complete(1010.0)
        cancelled = cascade_cancel(root)
        # root cancelled, a skipped (already COMPLETED)
        self.assertEqual(cancelled, 1)
        self.assertEqual(a.status, DeadlineStatus.COMPLETED)


class TestCancelReturnsList(unittest.TestCase):
    def test_cancel_returns_nodes(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        a = root.add_child("a", duration_seconds=30.0)
        b = a.add_child("b", duration_seconds=10.0)
        cancelled = root.cancel(cascade=True)
        self.assertIn(root, cancelled)
        self.assertIn(a, cancelled)
        self.assertIn(b, cancelled)


class TestDeadlineTree(unittest.TestCase):
    def _build_tree(self) -> DeadlineTree:
        root = DeadlineNode(name="root", duration_seconds=120.0)
        root.add_child("phase1", duration_seconds=60.0)
        root.add_child("phase2", duration_seconds=90.0)
        phase1 = root.children[0]
        phase1.add_child("step1", duration_seconds=20.0)
        phase1.add_child("step2", duration_seconds=30.0)
        return DeadlineTree(root=root)

    def test_find(self):
        tree = self._build_tree()
        self.assertEqual(tree.find("root").name, "root")
        self.assertEqual(tree.find("phase1").name, "phase1")
        self.assertEqual(tree.find("step1").name, "step1")
        self.assertIsNone(tree.find("nonexistent"))

    def test_active(self):
        tree = self._build_tree()
        self.assertEqual(len(tree.active()), 5)

    def test_active_after_complete(self):
        tree = self._build_tree()
        tree.find("phase1").complete()
        self.assertEqual(len(tree.active()), 4)

    def test_expired(self):
        tree = self._build_tree()
        # start everything at now=0
        for n in tree.all_nodes():
            n.start(0.0)
        # at now=25, only "step1" (20s) has expired
        expired = tree.expired(25.0)
        names = sorted(n.name for n in expired)
        self.assertEqual(names, ["step1"])
        # at now=35, step1 (20s) and step2 (30s) have expired
        expired = tree.expired(35.0)
        names = sorted(n.name for n in expired)
        self.assertEqual(names, ["step1", "step2"])

    def test_cancel_by_name(self):
        tree = self._build_tree()
        count = tree.cancel("phase1")
        self.assertEqual(count, 3)  # phase1 + step1 + step2
        self.assertEqual(tree.find("phase1").status, DeadlineStatus.CANCELLED)

    def test_cancel_missing_returns_zero(self):
        tree = self._build_tree()
        self.assertEqual(tree.cancel("nope"), 0)

    def test_find_after_add_child(self):
        """Regression: nodes added after tree construction must be findable.

        Lazy re-index on miss: the tree walks its root to find nodes that
        were attached via ``root.add_child(...)`` after __init__ ran.
        """
        root = DeadlineNode(name="root", duration_seconds=10)
        tree = DeadlineTree(root=root)
        root.add_child("late", duration_seconds=5)
        self.assertIsNotNone(tree.find("late"),
                             "node added after tree construction was not findable")

    def test_cancel_after_add_child(self):
        """Regression: cancellation by name works for post-construction children."""
        root = DeadlineNode(name="root", duration_seconds=10)
        tree = DeadlineTree(root=root)
        late = root.add_child("late", duration_seconds=5)
        count = tree.cancel("late")
        self.assertEqual(count, 1)
        self.assertEqual(late.status, DeadlineStatus.CANCELLED)

    def test_all_nodes(self):
        tree = self._build_tree()
        self.assertEqual(len(tree.all_nodes()), 5)

    def test_repr(self):
        tree = self._build_tree()
        self.assertIn("root", repr(tree))


class TestDeadlineTreeFileIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, ignore_errors=True))

    def test_save_load(self):
        root = DeadlineNode(name="root", duration_seconds=120.0)
        a = root.add_child("a", duration_seconds=60.0)
        a.add_child("b", duration_seconds=30.0)
        a.start(1000.0)
        tree = DeadlineTree(root=root)
        tree.save(self.tmpdir)
        loaded = DeadlineTree.load(self.tmpdir, "root")
        self.assertEqual(loaded.root.name, "root")
        self.assertEqual(loaded.root.duration_seconds, 120.0)
        self.assertEqual(loaded.find("a").duration_seconds, 60.0)
        self.assertEqual(loaded.find("b").duration_seconds, 30.0)
        self.assertEqual(loaded.find("a").started_at_unix, 1000.0)

    def test_save_with_status(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        root.start(1000.0)
        root.complete(1030.0)
        tree = DeadlineTree(root=root)
        tree.save(self.tmpdir)
        loaded = DeadlineTree.load(self.tmpdir, "root")
        self.assertEqual(loaded.root.status, DeadlineStatus.COMPLETED)


class TestDepthAndDescendants(unittest.TestCase):
    def test_depth(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        a = root.add_child("a", duration_seconds=30.0)
        b = a.add_child("b", duration_seconds=10.0)
        c = b.add_child("c", duration_seconds=5.0)
        self.assertEqual(root.depth(), 0)
        self.assertEqual(a.depth(), 1)
        self.assertEqual(b.depth(), 2)
        self.assertEqual(c.depth(), 3)

    def test_descendants(self):
        root = DeadlineNode(name="root", duration_seconds=60.0)
        a = root.add_child("a", duration_seconds=30.0)
        b = a.add_child("b", duration_seconds=10.0)
        d = root.descendants()
        self.assertEqual(len(d), 2)
        names = sorted(n.name for n in d)
        self.assertEqual(names, ["a", "b"])


if __name__ == "__main__":
    unittest.main()