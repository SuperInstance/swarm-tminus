"""BPM-adaptive tick clocks.

A TickClock generates ticks at a given BPM with optional swing. The
clock can adapt its tempo to a measured "energy" signal. TempoNegotiator
finds an LCM-compatible BPM that multiple agents can agree on.

Source: tick-engine/src/lib.rs and t-minus-rs::band_timing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import NamedTuple, Optional


# ---------------------------------------------------------------------------
# Tick types
# ---------------------------------------------------------------------------

class Tick(NamedTuple):
    """A single tick from the clock."""
    id: int
    timestamp: float
    delta: float


# ---------------------------------------------------------------------------
# BPM helpers
# ---------------------------------------------------------------------------

def bpm_to_seconds(bpm: float) -> float:
    """Convert BPM to beat interval in seconds. Raises if bpm <= 0."""
    if bpm <= 0:
        raise ValueError(f"bpm must be > 0, got {bpm!r}")
    return 60.0 / bpm


def seconds_to_bpm(seconds: float) -> float:
    """Inverse: seconds per beat -> BPM."""
    if seconds <= 0:
        raise ValueError(f"seconds must be > 0, got {seconds!r}")
    return 60.0 / seconds


def swing_offset(interval: float, swing: float, tick_id: int) -> float:
    """Compute the swing offset for a tick.

    On-beats (even tick ids) get no offset. Off-beats (odd tick ids) get
    `interval * swing * 0.33` added. `swing` is clamped to [0,1].
    """
    s = max(0.0, min(1.0, swing))
    if tick_id % 2 == 1:
        return interval * s * 0.33
    return 0.0


# ---------------------------------------------------------------------------
# Alias for BPM-only mode
# ---------------------------------------------------------------------------

@dataclass
class BPM:
    """Simple BPM holder. Mirrors the `BPM` API in the spec."""
    bpm: float

    def __post_init__(self) -> None:
        if self.bpm <= 0:
            raise ValueError(f"bpm must be > 0, got {self.bpm!r}")

    def seconds(self) -> float:
        return 60.0 / self.bpm

    def __repr__(self) -> str:  # pragma: no cover
        return f"BPM({self.bpm})"


# ---------------------------------------------------------------------------
# TickClock
# ---------------------------------------------------------------------------

@dataclass
class TickClock:
    """A repeating tick schedule driven by BPM with optional swing.

    `next_tick()` returns the next tick in monotonically increasing order
    starting at id=0. The clock can `adapt()` its BPM based on an energy
    signal in [0,1] (high energy → faster, low → slower).
    """
    bpm: float = 120.0
    swing: float = 0.0
    next_tick_id: int = 0
    started_at_unix: float = field(default_factory=time.time)
    min_bpm: float = 30.0
    max_bpm: float = 300.0

    def __post_init__(self) -> None:
        if self.bpm <= 0:
            raise ValueError(f"bpm must be > 0, got {self.bpm!r}")
        self.swing = max(0.0, min(1.0, self.swing))

    def tick_interval(self) -> float:
        return 60.0 / self.bpm

    def swing_for_tick(self, tick_id: int) -> float:
        return swing_offset(self.tick_interval(), self.swing, tick_id)

    def next_tick(self, now_unix: Optional[float] = None) -> Tick:
        """Compute and return the next tick, advancing `next_tick_id`."""
        tick_id = self.next_tick_id
        interval = self.tick_interval()
        swing = self.swing_for_tick(tick_id)
        delta = interval + swing
        if now_unix is None:
            now_unix = time.time()
        timestamp = now_unix + delta
        self.next_tick_id += 1
        return Tick(id=tick_id, timestamp=timestamp, delta=delta)

    def peek_next_tick(self, now_unix: Optional[float] = None) -> Tick:
        """Compute next tick without advancing the counter."""
        tick_id = self.next_tick_id
        interval = self.tick_interval()
        swing = self.swing_for_tick(tick_id)
        delta = interval + swing
        if now_unix is None:
            now_unix = time.time()
        timestamp = now_unix + delta
        return Tick(id=tick_id, timestamp=timestamp, delta=delta)

    def adapt(self, energy: float) -> None:
        """Adjust BPM based on an energy signal in [0,1].

        energy=0.5 is neutral. Maps to bpm *= 1 + (energy - 0.5) * 0.4
        (so 0.0 → *0.8, 1.0 → *1.2). Result is clamped to [min_bpm, max_bpm].
        """
        e = max(0.0, min(1.0, energy))
        factor = 1.0 + (e - 0.5) * 0.4
        self.bpm = max(self.min_bpm, min(self.max_bpm, self.bpm * factor))

    def reset(self, now_unix: Optional[float] = None) -> None:
        self.next_tick_id = 0
        if now_unix is not None:
            self.started_at_unix = float(now_unix)

    def __repr__(self) -> str:  # pragma: no cover
        return f"TickClock(bpm={self.bpm} swing={self.swing} next_id={self.next_tick_id})"


# ---------------------------------------------------------------------------
# TempoNegotiator
# ---------------------------------------------------------------------------

class TempoNegotiator:
    """Multi-agent tempo negotiation.

    Each agent proposes a BPM. `negotiated()` returns the BPM nearest
    the median proposal (within tolerance), or None if no proposal is
    within tolerance of any other.
    """

    def __init__(self) -> None:
        self.proposed_bpms: list[float] = []

    def propose(self, bpm: float) -> None:
        if bpm <= 0:
            raise ValueError(f"bpm must be > 0, got {bpm!r}")
        self.proposed_bpms.append(float(bpm))

    def negotiated(self, tolerance: float = 0.1) -> Optional[float]:
        """Return the BPM nearest the median, or None if proposals disagree.

        Two BPMs agree if they differ by at most `tolerance` (relative).
        We pick the candidate whose minimum relative distance to any other
        is largest ("the most-compatible proposal"), then return it.
        """
        if not self.proposed_bpms:
            return None
        if len(self.proposed_bpms) == 1:
            return self.proposed_bpms[0]
        # Compute compatibility: candidate is "compatible" if there's any
        # other proposal within `tolerance` relative difference.
        candidates: list[float] = []
        for i, c in enumerate(self.proposed_bpms):
            for j, o in enumerate(self.proposed_bpms):
                if i == j:
                    continue
                rel = abs(c - o) / max(abs(c), abs(o), 1e-12)
                if rel <= tolerance:
                    candidates.append(c)
                    break
        if not candidates:
            return None
        # Among compatible candidates, return median.
        s = sorted(candidates)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2.0

    def __repr__(self) -> str:  # pragma: no cover
        return f"TempoNegotiator(proposed={self.proposed_bpms})"


__all__ = [
    "Tick",
    "BPM",
    "TickClock",
    "TempoNegotiator",
    "bpm_to_seconds",
    "seconds_to_bpm",
    "swing_offset",
]