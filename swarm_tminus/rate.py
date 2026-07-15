"""Token-bucket + leaky-bucket rate limiters.

Two complementary primitives for throttling:
- TokenBucket: burst-capable (allows n tokens up to capacity, refills over time)
- LeakyBucket: queue-style (drips at fixed rate, overflow rejects)

Source: t-minus-rs/src/backpressure.rs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Union


def _now(now_unix: Optional[float]) -> float:
    return time.time() if now_unix is None else float(now_unix)


@dataclass
class TokenBucket:
    """A token-bucket rate limiter.

    Tokens accrue at `refill_per_sec` up to `capacity`. `try_consume(n)`
    succeeds only if at least `n` tokens are available. Optional
    `deadline_unix` makes the bucket irreversibly dead after that time.
    """
    capacity: float
    refill_per_sec: float
    tokens: float = 0.0
    last_refill_unix: float = 0.0
    deadline_unix: Optional[float] = None

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {self.capacity!r}")
        if self.refill_per_sec < 0:
            raise ValueError(f"refill_per_sec must be >= 0, got {self.refill_per_sec!r}")
        if self.tokens <= 0:
            self.tokens = self.capacity  # bucket starts full (per t-minus-rs)

    def _refill(self, now_unix: float) -> None:
        if now_unix > self.last_refill_unix:
            elapsed = now_unix - self.last_refill_unix
            self.tokens = min(
                self.capacity,
                self.tokens + elapsed * self.refill_per_sec,
            )
            self.last_refill_unix = now_unix

    def is_expired(self, now_unix: Optional[float] = None) -> bool:
        if self.deadline_unix is None:
            return False
        return _now(now_unix) >= self.deadline_unix

    def available_tokens(self, now_unix: Optional[float] = None) -> float:
        """Read available tokens (with refill accrued, no mutation of last_refill)."""
        n = _now(now_unix)
        if n <= self.last_refill_unix:
            return min(self.capacity, self.tokens)
        elapsed = n - self.last_refill_unix
        return min(self.capacity, self.tokens + elapsed * self.refill_per_sec)

    def try_consume(self, n: float = 1.0, now_unix: Optional[float] = None) -> bool:
        """Try to consume `n` tokens. Returns True on success."""
        if n <= 0:
            raise ValueError(f"n must be > 0, got {n!r}")
        now = _now(now_unix)
        if self.is_expired(now):
            return False
        self._refill(now)
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False

    def time_to_n_tokens(self, n: float, now_unix: Optional[float] = None) -> float:
        """Seconds until `n` tokens would be available. 0 if already available.

        Returns float('inf') if refill rate is 0 or bucket is expired.
        """
        if n < 0:
            raise ValueError(f"n must be >= 0, got {n!r}")
        if n == 0:
            return 0.0
        now = _now(now_unix)
        if self.is_expired(now):
            return float("inf")
        avail = self.available_tokens(now)
        if avail >= n:
            return 0.0
        if self.refill_per_sec <= 0:
            return float("inf")
        needed = n - avail
        return needed / self.refill_per_sec

    def reset(self, now_unix: Optional[float] = None) -> None:
        """Refill to capacity immediately."""
        self.tokens = self.capacity
        self.last_refill_unix = _now(now_unix)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TokenBucket(cap={self.capacity} refill={self.refill_per_sec}/s "
            f"tokens={self.tokens:.2f})"
        )


@dataclass
class LeakyBucket:
    """A leaky-bucket rate limiter.

    Drains at `drip_per_sec` (continuous). `add(n)` succeeds only if
    the bucket has capacity to absorb `n` after current drain.
    """
    capacity: float
    drip_per_sec: float
    level: float = 0.0
    last_drip_unix: float = 0.0
    deadline_unix: Optional[float] = None

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError(f"capacity must be > 0, got {self.capacity!r}")
        if self.drip_per_sec < 0:
            raise ValueError(f"drip_per_sec must be >= 0, got {self.drip_per_sec!r}")
        # starts empty (per t-minus-rs)
        self.level = 0.0

    def _drip(self, now_unix: float) -> None:
        if now_unix > self.last_drip_unix:
            elapsed = now_unix - self.last_drip_unix
            self.level = max(0.0, self.level - elapsed * self.drip_per_sec)
            self.last_drip_unix = now_unix

    def is_expired(self, now_unix: Optional[float] = None) -> bool:
        if self.deadline_unix is None:
            return False
        return _now(now_unix) >= self.deadline_unix

    def queue_level(self, now_unix: Optional[float] = None) -> float:
        """Read current queue level (with drip accrued, no mutation)."""
        n = _now(now_unix)
        if n <= self.last_drip_unix:
            return max(0.0, self.level)
        elapsed = n - self.last_drip_unix
        return max(0.0, self.level - elapsed * self.drip_per_sec)

    def add(self, n: float = 1.0, now_unix: Optional[float] = None) -> bool:
        """Try to add `n` units. Returns False if overflow or expired."""
        if n <= 0:
            raise ValueError(f"n must be > 0, got {n!r}")
        now = _now(now_unix)
        if self.is_expired(now):
            return False
        self._drip(now)
        if self.level + n <= self.capacity:
            self.level += n
            return True
        return False

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"LeakyBucket(cap={self.capacity} drip={self.drip_per_sec}/s "
            f"level={self.level:.2f})"
        )


@dataclass
class RatePair:
    """Token bucket + leaky bucket, in series.

    A message is admitted iff both buckets allow it. Useful for
    smoothing bursty traffic: token bucket caps rate, leaky bucket
    smooths the queue.
    """
    token: TokenBucket
    leaky: LeakyBucket

    def try_send(self, n: float = 1.0, now_unix: Optional[float] = None) -> tuple[bool, str]:
        """Attempt to send n units. Returns (allowed, reason).

        Reason is "ok", "token_bucket_full", "leaky_bucket_full",
        or "expired" depending on what rejected.
        """
        if not self.token.try_consume(n, now_unix=now_unix):
            if self.token.is_expired(now_unix):
                return (False, "expired")
            return (False, "token_bucket_full")
        if not self.leaky.add(n, now_unix=now_unix):
            # Roll back the token consumption so the pair is consistent.
            # We don't have a "give_back" on TokenBucket, so refilling it
            # is the safest move: nudge last_refill so the bucket sees the
            # consumed tokens as not-yet-consumed.
            self.token.tokens = min(self.token.capacity, self.token.tokens + n)
            if self.leaky.is_expired(now_unix):
                return (False, "expired")
            return (False, "leaky_bucket_full")
        return (True, "ok")

    def __repr__(self) -> str:  # pragma: no cover
        return f"RatePair(token={self.token!r} leaky={self.leaky!r})"


__all__ = [
    "TokenBucket",
    "LeakyBucket",
    "RatePair",
]