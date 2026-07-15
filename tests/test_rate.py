"""Tests for swarm_tminus.rate — token & leaky bucket rate limiters."""

import unittest

from swarm_tminus.rate import TokenBucket, LeakyBucket, RatePair


class TestTokenBucket(unittest.TestCase):
    def test_starts_full(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        self.assertEqual(b.tokens, 10.0)

    def test_invalid_capacity(self):
        with self.assertRaises(ValueError):
            TokenBucket(capacity=0, refill_per_sec=1.0)
        with self.assertRaises(ValueError):
            TokenBucket(capacity=-1, refill_per_sec=1.0)

    def test_invalid_refill(self):
        with self.assertRaises(ValueError):
            TokenBucket(capacity=10, refill_per_sec=-1.0)

    def test_consume(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        self.assertTrue(b.try_consume(5.0, now_unix=100.0))
        self.assertAlmostEqual(b.tokens, 5.0)

    def test_consume_more_than_available_fails(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        self.assertFalse(b.try_consume(15.0, now_unix=100.0))
        # tokens still 10 (untouched)
        self.assertAlmostEqual(b.tokens, 10.0)

    def test_consume_zero_raises(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        with self.assertRaises(ValueError):
            b.try_consume(0, now_unix=100.0)

    def test_consume_negative_raises(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        with self.assertRaises(ValueError):
            b.try_consume(-1, now_unix=100.0)

    def test_refill_over_time(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        b.try_consume(10.0, now_unix=100.0)  # empty
        # 5s later, 5 tokens back
        self.assertTrue(b.try_consume(5.0, now_unix=105.0))
        self.assertAlmostEqual(b.tokens, 0.0)

    def test_refill_caps_at_capacity(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        # 100s later, but capped at capacity
        self.assertAlmostEqual(b.available_tokens(200.0), 10.0)

    def test_time_to_n_tokens(self):
        b = TokenBucket(capacity=10, refill_per_sec=2.0)
        b.try_consume(10.0, now_unix=100.0)  # empty
        # Need 4 tokens at 2/s = 2s
        self.assertAlmostEqual(b.time_to_n_tokens(4.0, now_unix=100.0), 2.0)

    def test_time_to_n_zero_rate(self):
        b = TokenBucket(capacity=10, refill_per_sec=0.0)
        b.try_consume(10.0, now_unix=100.0)
        # No refill possible
        self.assertEqual(b.time_to_n_tokens(1.0, now_unix=100.0), float("inf"))

    def test_time_to_n_already_available(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        self.assertEqual(b.time_to_n_tokens(5.0, now_unix=100.0), 0.0)

    def test_time_to_n_invalid(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        self.assertEqual(b.time_to_n_tokens(0.0, now_unix=100.0), 0.0)
        with self.assertRaises(ValueError):
            b.time_to_n_tokens(-1.0, now_unix=100.0)

    def test_deadline_expired(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0, deadline_unix=200.0)
        self.assertFalse(b.is_expired(now_unix=100.0))
        self.assertTrue(b.is_expired(now_unix=200.0))
        self.assertFalse(b.try_consume(1.0, now_unix=300.0))

    def test_no_deadline_never_expires(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        self.assertFalse(b.is_expired(now_unix=10**12))

    def test_reset(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        b.try_consume(10.0, now_unix=100.0)
        b.reset(now_unix=100.0)
        self.assertAlmostEqual(b.tokens, 10.0)

    def test_repr(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        self.assertIn("TokenBucket", repr(b))

    def test_available_tokens_includes_refill(self):
        b = TokenBucket(capacity=10, refill_per_sec=1.0)
        b.try_consume(10.0, now_unix=100.0)
        # After 3s without consuming, should report 3
        self.assertAlmostEqual(b.available_tokens(103.0), 3.0)


class TestLeakyBucket(unittest.TestCase):
    def test_starts_empty(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0)
        self.assertEqual(b.level, 0.0)

    def test_invalid_capacity(self):
        with self.assertRaises(ValueError):
            LeakyBucket(capacity=0, drip_per_sec=1.0)

    def test_invalid_drip(self):
        with self.assertRaises(ValueError):
            LeakyBucket(capacity=10, drip_per_sec=-1.0)

    def test_add_within_capacity(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0)
        self.assertTrue(b.add(5.0, now_unix=100.0))
        self.assertAlmostEqual(b.level, 5.0)

    def test_add_overflow(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0)
        self.assertFalse(b.add(15.0, now_unix=100.0))

    def test_drip_over_time(self):
        b = LeakyBucket(capacity=10, drip_per_sec=2.0)
        b.add(10.0, now_unix=100.0)
        # After 5s, level is 10 - 5*2 = 0
        self.assertAlmostEqual(b.queue_level(105.0), 0.0)

    def test_drip_does_not_go_negative(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0)
        b.add(2.0, now_unix=100.0)
        # After 100s, level floored at 0
        self.assertAlmostEqual(b.queue_level(200.0), 0.0)

    def test_add_zero_raises(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0)
        with self.assertRaises(ValueError):
            b.add(0, now_unix=100.0)

    def test_add_negative_raises(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0)
        with self.assertRaises(ValueError):
            b.add(-1, now_unix=100.0)

    def test_deadline_expired(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0, deadline_unix=200.0)
        self.assertTrue(b.is_expired(now_unix=200.0))
        self.assertFalse(b.add(1.0, now_unix=300.0))

    def test_repr(self):
        b = LeakyBucket(capacity=10, drip_per_sec=1.0)
        self.assertIn("LeakyBucket", repr(b))


class TestRatePair(unittest.TestCase):
    def test_both_allow(self):
        tp = TokenBucket(capacity=10, refill_per_sec=1.0)
        lp = LeakyBucket(capacity=10, drip_per_sec=1.0)
        pair = RatePair(token=tp, leaky=lp)
        ok, reason = pair.try_send(1.0, now_unix=100.0)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_token_blocks(self):
        tp = TokenBucket(capacity=1, refill_per_sec=0.0)
        # consume the only token (but rate is 0, so it won't refill)
        tp.try_consume(1.0, now_unix=100.0)
        lp = LeakyBucket(capacity=10, drip_per_sec=1.0)
        pair = RatePair(token=tp, leaky=lp)
        ok, reason = pair.try_send(1.0, now_unix=100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "token_bucket_full")

    def test_leaky_blocks(self):
        tp = TokenBucket(capacity=100, refill_per_sec=1.0)
        lp = LeakyBucket(capacity=2, drip_per_sec=0.0)
        lp.add(2.0, now_unix=100.0)  # fill leaky
        pair = RatePair(token=tp, leaky=lp)
        ok, reason = pair.try_send(1.0, now_unix=100.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "leaky_bucket_full")

    def test_token_rollback_when_leaky_blocks(self):
        tp = TokenBucket(capacity=10, refill_per_sec=1.0)
        lp = LeakyBucket(capacity=1, drip_per_sec=0.0)
        lp.add(1.0, now_unix=100.0)
        pair = RatePair(token=tp, leaky=lp)
        # First send fills leaky to capacity
        ok1, _ = pair.try_send(1.0, now_unix=100.0)
        # The leaky is now full again from the rollback... actually let's see
        # The token bucket: 10 -> 9 (after one ok send)
        # Then second send: token ok, leaky blocks, token rolls back to 10
        # But the first send also: token 9, leaky 1
        # So leaky is at 1, capacity is 1
        # Second send: token consumes 1 (9), leaky should add 1 but full, blocks
        # After rollback: token 10
        # Try a fresh third send:
        before = tp.tokens
        ok2, reason2 = pair.try_send(1.0, now_unix=100.0)
        self.assertFalse(ok2)
        # Token bucket should be rolled back
        self.assertAlmostEqual(tp.tokens, before)

    def test_expired(self):
        tp = TokenBucket(capacity=10, refill_per_sec=1.0, deadline_unix=200.0)
        lp = LeakyBucket(capacity=10, drip_per_sec=1.0)
        pair = RatePair(token=tp, leaky=lp)
        ok, reason = pair.try_send(1.0, now_unix=300.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "expired")

    def test_repr(self):
        tp = TokenBucket(capacity=10, refill_per_sec=1.0)
        lp = LeakyBucket(capacity=10, drip_per_sec=1.0)
        pair = RatePair(token=tp, leaky=lp)
        self.assertIn("RatePair", repr(pair))


if __name__ == "__main__":
    unittest.main()