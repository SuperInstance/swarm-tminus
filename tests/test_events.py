"""Tests for swarm_tminus.events — countdown + quorum primitives."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from swarm_tminus.events import (
    CountdownEvent, EventStore, EventStatus, SubscriberStatus,
)


class TestEventStatus(unittest.TestCase):
    def test_values(self):
        self.assertEqual(EventStatus.SCHEDULED.value, "scheduled")
        self.assertEqual(EventStatus.FIRED.value, "fired")
        self.assertEqual(EventStatus.MISSED.value, "missed")
        self.assertEqual(EventStatus.CANCELED.value, "canceled")
        self.assertEqual(EventStatus.COUNTING.value, "counting")


class TestSubscriberStatus(unittest.TestCase):
    def test_values(self):
        self.assertEqual(SubscriberStatus.PENDING.value, "pending")
        self.assertEqual(SubscriberStatus.CONFIRMED.value, "confirmed")
        self.assertEqual(SubscriberStatus.DEFERRED.value, "deferred")
        self.assertEqual(SubscriberStatus.MISSED.value, "missed")
        self.assertEqual(SubscriberStatus.READY.value, "ready")


class TestCountdownEventBasics(unittest.TestCase):
    def test_creation(self):
        e = CountdownEvent(name="deploy", fire_at_unix=time.time() + 60)
        self.assertEqual(e.name, "deploy")
        self.assertEqual(e.quorum_required, 1)
        self.assertEqual(e.status, EventStatus.SCHEDULED)
        self.assertEqual(e.subscriber_statuses, {})

    def test_quorum_zero_always_met(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() + 60, quorum_required=0)
        self.assertTrue(e.has_quorum())

    def test_quorum_required_not_met(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() + 60, quorum_required=2)
        e.confirm("alice")
        self.assertFalse(e.has_quorum())
        e.confirm("bob")
        self.assertTrue(e.has_quorum())

    def test_repr(self):
        e = CountdownEvent(name="e", fire_at_unix=100.0)
        self.assertIn("e", repr(e))

    def test_add_subscriber(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() + 60)
        e.add_subscriber("alice")
        e.add_subscriber("bob")
        self.assertEqual(e.subscriber_statuses["alice"], SubscriberStatus.PENDING)
        self.assertEqual(e.subscriber_statuses["bob"], SubscriberStatus.PENDING)
        # idempotent
        e.add_subscriber("alice")
        self.assertEqual(len(e.subscriber_statuses), 2)


class TestCountdownEventTick(unittest.TestCase):
    def test_past_no_quorum_is_missed(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60)
        status = e.tick(time.time())
        self.assertEqual(status, EventStatus.MISSED)

    def test_future_no_quorum_is_counting(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() + 60)
        status = e.tick(time.time())
        self.assertEqual(status, EventStatus.COUNTING)

    def test_quorum_reached_is_fired(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60, quorum_required=1)
        e.confirm("alice")
        status = e.tick(time.time())
        self.assertEqual(status, EventStatus.FIRED)

    def test_deferred_blocks_missed(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60)
        e.confirm("alice", SubscriberStatus.DEFERRED)
        status = e.tick(time.time())
        self.assertEqual(status, EventStatus.COUNTING)

    def test_quorum_fires_even_with_deferred(self):
        """Regression: quorum must be checked BEFORE deferral grace.

        Mirrors t-minus/src/engine.rs:188-208: an event with quorum AND any
        deferred attendee should still FIRE (not be held in COUNTING).
        Previous behavior held it in COUNTING because deferral was checked
        first.
        """
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60,
                           quorum_required=2)
        e.confirm("alice", SubscriberStatus.CONFIRMED)
        e.confirm("bob", SubscriberStatus.CONFIRMED)
        e.confirm("carol", SubscriberStatus.DEFERRED)
        status = e.tick(time.time())
        self.assertEqual(status, EventStatus.FIRED,
                         "quorum met should fire even with deferred attendee")

    def test_tick_idempotent(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60)
        e.tick(time.time())
        self.assertEqual(e.status, EventStatus.MISSED)
        status = e.tick(time.time())
        self.assertEqual(status, EventStatus.MISSED)

    def test_canceled_stays_canceled(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60)
        e.cancel()
        status = e.tick(time.time())
        self.assertEqual(status, EventStatus.CANCELED)

    def test_ready_to_fire(self):
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60, quorum_required=1)
        self.assertFalse(e.ready_to_fire())
        e.confirm("alice")
        self.assertTrue(e.ready_to_fire())

    def test_time_remaining(self):
        now = time.time()
        e = CountdownEvent(name="e", fire_at_unix=now + 60)
        self.assertAlmostEqual(e.time_remaining(now), 60.0, places=5)
        self.assertLess(e.time_remaining(now + 100), 0.0)


class TestCountdownEventSerialization(unittest.TestCase):
    def test_roundtrip(self):
        e = CountdownEvent(
            name="deploy-v1",
            fire_at_unix=1234567890.5,
            quorum_required=3,
            subscriber_statuses={"alice": SubscriberStatus.CONFIRMED, "bob": SubscriberStatus.PENDING},
            status=EventStatus.COUNTING,
            payload={"version": "1.0"},
        )
        data = e.to_dict()
        e2 = CountdownEvent.from_dict(data)
        self.assertEqual(e.name, e2.name)
        self.assertEqual(e.fire_at_unix, e2.fire_at_unix)
        self.assertEqual(e.quorum_required, e2.quorum_required)
        self.assertEqual(e.subscriber_statuses, e2.subscriber_statuses)
        self.assertEqual(e.status, e2.status)
        self.assertEqual(e.payload, e2.payload)

    def test_roundtrip_json(self):
        e = CountdownEvent(name="e", fire_at_unix=100.0, subscriber_statuses={"a": SubscriberStatus.CONFIRMED})
        s = json.dumps(e.to_dict())
        e2 = CountdownEvent.from_dict(json.loads(s))
        self.assertEqual(e.subscriber_statuses, e2.subscriber_statuses)


class TestEventStoreFileIO(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, ignore_errors=True))

    def test_save_and_load(self):
        store = EventStore(self.tmpdir)
        e = CountdownEvent(name="deploy", fire_at_unix=1000.0, quorum_required=2)
        e.confirm("alice")
        store.add_event(e)
        store.save()
        # Re-load
        store2 = EventStore.load(self.tmpdir)
        self.assertEqual(len(store2), 1)
        e2 = store2.get("deploy")
        self.assertIsNotNone(e2)
        self.assertEqual(e2.quorum_required, 2)
        self.assertEqual(e2.subscriber_statuses["alice"], SubscriberStatus.CONFIRMED)

    def test_multiple_events(self):
        store = EventStore(self.tmpdir)
        store.add_event(CountdownEvent(name="a", fire_at_unix=100.0))
        store.add_event(CountdownEvent(name="b", fire_at_unix=200.0))
        store.add_event(CountdownEvent(name="c", fire_at_unix=300.0))
        store.save()
        store2 = EventStore.load(self.tmpdir)
        self.assertEqual(len(store2), 3)

    def test_filenames_are_sanitized(self):
        store = EventStore(self.tmpdir)
        store.add_event(CountdownEvent(name="weird/name with spaces", fire_at_unix=100.0))
        store.save()
        files = list(Path(self.tmpdir).glob("*.event.json"))
        self.assertEqual(len(files), 1)

    def test_get_missing(self):
        store = EventStore(self.tmpdir)
        self.assertIsNone(store.get("nope"))

    def test_remove(self):
        store = EventStore(self.tmpdir)
        store.add_event(CountdownEvent(name="a", fire_at_unix=100.0))
        store.save()
        self.assertTrue(store.remove("a"))
        self.assertFalse(store.remove("a"))
        self.assertEqual(len(store), 0)

    def test_load_skips_malformed(self):
        store = EventStore(self.tmpdir)
        # Write a malformed file
        bad = Path(self.tmpdir) / "bad.event.json"
        bad.write_text("{not valid json")
        store2 = EventStore.load(self.tmpdir)
        self.assertEqual(len(store2), 0)

    def test_contains(self):
        store = EventStore(self.tmpdir)
        store.add_event(CountdownEvent(name="a", fire_at_unix=100.0))
        self.assertIn("a", store)
        self.assertNotIn("b", store)

    def test_iter(self):
        store = EventStore(self.tmpdir)
        store.add_event(CountdownEvent(name="a", fire_at_unix=100.0))
        store.add_event(CountdownEvent(name="b", fire_at_unix=200.0))
        names = sorted(e.name for e in store)
        self.assertEqual(names, ["a", "b"])

    def test_repr(self):
        store = EventStore(self.tmpdir)
        r = repr(store)
        self.assertIn("EventStore", r)


class TestEventStoreTick(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmpdir, ignore_errors=True))

    def test_fire_due(self):
        store = EventStore(self.tmpdir)
        past = CountdownEvent(name="past", fire_at_unix=time.time() - 60, quorum_required=1)
        past.confirm("alice")
        store.add_event(past)
        fired = store.fire_due(time.time())
        self.assertEqual(len(fired), 1)
        self.assertEqual(fired[0].status, EventStatus.FIRED)

    def test_reap_missed(self):
        store = EventStore(self.tmpdir)
        e = CountdownEvent(name="e", fire_at_unix=time.time() - 60, quorum_required=2)
        e.confirm("alice")  # only 1 of 2
        store.add_event(e)
        missed = store.reap_missed(time.time())
        self.assertEqual(len(missed), 1)
        self.assertEqual(missed[0].status, EventStatus.MISSED)


class TestCountdownEventCounts(unittest.TestCase):
    def test_confirmed_count(self):
        e = CountdownEvent(name="e", fire_at_unix=100.0, quorum_required=3)
        e.confirm("alice")
        e.confirm("bob", SubscriberStatus.PENDING)
        e.confirm("carol", SubscriberStatus.CONFIRMED)
        self.assertEqual(e.confirmed_count(), 2)

    def test_deferred_count(self):
        e = CountdownEvent(name="e", fire_at_unix=100.0)
        e.confirm("alice", SubscriberStatus.DEFERRED)
        e.confirm("bob", SubscriberStatus.DEFERRED)
        e.confirm("carol", SubscriberStatus.CONFIRMED)
        self.assertEqual(e.deferred_count(), 2)


if __name__ == "__main__":
    unittest.main()