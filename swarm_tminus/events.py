"""Countdown + quorum events from t-minus.

Each CountdownEvent is a scheduled time. Subscribers confirm readiness.
When quorum is reached and time arrives, the event fires. Missed events
are reaped when fire_at passes without quorum.

Source: t-minus/src/types.rs and t-minus/src/engine.rs.
"""

from __future__ import annotations

import enum
import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Union


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EventStatus(str, enum.Enum):
    """Lifecycle status of an event."""
    SCHEDULED = "scheduled"   # fire_at is in the future
    COUNTING = "counting"     # fire_at has passed but quorum not reached
    FIRED = "fired"           # quorum reached + time reached
    MISSED = "missed"         # fire_at passed without quorum
    CANCELED = "canceled"     # explicitly cancelled


class SubscriberStatus(str, enum.Enum):
    """Per-subscriber response state."""
    PENDING = "pending"
    CONFIRMED = "confirmed"
    DEFERRED = "deferred"
    MISSED = "missed"
    READY = "ready"


# ---------------------------------------------------------------------------
# CountdownEvent
# ---------------------------------------------------------------------------

@dataclass
class CountdownEvent:
    """A scheduled countdown with quorum-gated firing.

    File-based shared state: each event saves as <name>.event.json.
    """
    name: str
    fire_at_unix: float
    quorum_required: int = 1
    subscriber_statuses: dict[str, SubscriberStatus] = field(default_factory=dict)
    status: EventStatus = EventStatus.SCHEDULED
    created_at_unix: float = field(default_factory=lambda: time.time())
    payload: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Coerce types from JSON-loaded data
        if isinstance(self.status, str):
            self.status = EventStatus(self.status)
        coerced: dict[str, SubscriberStatus] = {}
        for k, v in self.subscriber_statuses.items():
            if isinstance(v, str):
                coerced[k] = SubscriberStatus(v)
            else:
                coerced[k] = v
        self.subscriber_statuses = coerced

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def confirmed_count(self) -> int:
        return sum(1 for s in self.subscriber_statuses.values()
                   if s == SubscriberStatus.CONFIRMED)

    def deferred_count(self) -> int:
        return sum(1 for s in self.subscriber_statuses.values()
                   if s == SubscriberStatus.DEFERRED)

    def has_quorum(self) -> bool:
        """Quorum is satisfied if confirmed_count >= quorum_required.

        Note: quorum_required == 0 is a free-fire (any state).
        """
        return self.confirmed_count() >= self.quorum_required

    def ready_to_fire(self) -> bool:
        """All conditions met: time + quorum."""
        return (
            self.status in (EventStatus.SCHEDULED, EventStatus.COUNTING)
            and self.has_quorum()
        )

    def is_missed(self, now_unix: float) -> bool:
        """Fire time has passed AND no quorum reached."""
        return now_unix >= self.fire_at_unix and not self.has_quorum()

    def time_remaining(self, now_unix: float) -> float:
        """Seconds remaining until fire (negative if past)."""
        return self.fire_at_unix - now_unix

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def tick(self, now_unix: float) -> EventStatus:
        """Update status from current time. Returns the (possibly updated) status.

        - SCHEDULED -> COUNTING once fire_at has passed without quorum
        - SCHEDULED/COUNTING -> FIRED once quorum reached (any time)
        - SCHEDULED/COUNTING -> MISSED if time passed AND no quorum
        """
        if self.status in (EventStatus.FIRED, EventStatus.MISSED, EventStatus.CANCELED):
            return self.status

        # A Deferred attendee grants extra time per t-minus engine.rs:165-189 semantics.
        # We mirror that: if anyone is DEFERRED, do NOT mark missed; leave in COUNTING.
        if self.deferred_count() > 0:
            self.status = EventStatus.COUNTING
            return self.status

        if self.has_quorum():
            self.status = EventStatus.FIRED
            return self.status

        if now_unix >= self.fire_at_unix:
            self.status = EventStatus.MISSED
        else:
            self.status = EventStatus.COUNTING
        return self.status

    def confirm(self, subscriber_id: str, status: SubscriberStatus = SubscriberStatus.CONFIRMED) -> None:
        """Record a subscriber's response. Default status is CONFIRMED."""
        self.subscriber_statuses[subscriber_id] = status

    def cancel(self) -> None:
        self.status = EventStatus.CANCELED

    def add_subscriber(self, subscriber_id: str) -> None:
        """Add a subscriber in PENDING state (idempotent)."""
        if subscriber_id not in self.subscriber_statuses:
            self.subscriber_statuses[subscriber_id] = SubscriberStatus.PENDING

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "fire_at_unix": self.fire_at_unix,
            "quorum_required": self.quorum_required,
            "subscriber_statuses": {
                k: v.value for k, v in self.subscriber_statuses.items()
            },
            "status": self.status.value,
            "created_at_unix": self.created_at_unix,
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CountdownEvent":
        return cls(
            name=data["name"],
            fire_at_unix=float(data["fire_at_unix"]),
            quorum_required=int(data.get("quorum_required", 1)),
            subscriber_statuses=dict(data.get("subscriber_statuses", {})),
            status=EventStatus(data.get("status", "scheduled")),
            created_at_unix=float(data.get("created_at_unix", time.time())),
            payload=dict(data.get("payload", {})),
        )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"CountdownEvent(name={self.name!r} fire_at={self.fire_at_unix:.2f} "
            f"q={self.quorum_required} status={self.status.value!r})"
        )


# ---------------------------------------------------------------------------
# EventStore — file-based ground truth
# ---------------------------------------------------------------------------

def _sanitize_filename(name: str) -> str:
    """Sanitize an event name for use as a filename."""
    out = []
    for ch in name:
        if ch.isalnum() or ch in ("-", "_", "."):
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


class EventStore:
    """File-backed store of CountdownEvent objects.

    Events are persisted as `<name>.event.json` in `dir`. The store can
    be reloaded by reading all matching JSON files. Concurrent writes
    are not guaranteed — this is for shared-state-with-light-conflict use.
    """

    def __init__(self, dir: Union[str, Path]) -> None:
        self.dir = Path(dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._events: dict[str, CountdownEvent] = {}
        # Auto-load any existing events
        for fp in self.dir.glob("*.event.json"):
            try:
                ev = self._load_one(fp)
                self._events[ev.name] = ev
            except (OSError, ValueError, KeyError):
                # Skip malformed files
                pass

    def _path_for(self, name: str) -> Path:
        return self.dir / f"{_sanitize_filename(name)}.event.json"

    def _load_one(self, fp: Path) -> CountdownEvent:
        with fp.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return CountdownEvent.from_dict(data)

    def add_event(self, e: CountdownEvent) -> None:
        self._events[e.name] = e

    def get(self, name: str) -> Optional[CountdownEvent]:
        return self._events.get(name)

    def all_events(self) -> list[CountdownEvent]:
        return list(self._events.values())

    def remove(self, name: str) -> bool:
        if name in self._events:
            del self._events[name]
            path = self._path_for(name)
            if path.exists():
                path.unlink()
            return True
        return False

    def save(self) -> None:
        """Persist all events to disk as JSON."""
        for ev in self._events.values():
            path = self._path_for(ev.name)
            tmp = path.with_suffix(".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(ev.to_dict(), fh, indent=2, sort_keys=True)
            tmp.replace(path)

    @classmethod
    def load(cls, dir: Union[str, Path]) -> "EventStore":
        """Reload an EventStore from disk."""
        return cls(dir=dir)

    # ------------------------------------------------------------------
    # Tick operations
    # ------------------------------------------------------------------

    def fire_due(self, now_unix: float) -> list[CountdownEvent]:
        """Tick + collect events that fire at `now_unix`. Updates status in-place."""
        fired: list[CountdownEvent] = []
        for ev in self._events.values():
            ev.tick(now_unix)
            if ev.status == EventStatus.FIRED:
                fired.append(ev)
        return fired

    def reap_missed(self, now_unix: float) -> list[CountdownEvent]:
        """Tick + collect missed events. Updates status in-place."""
        missed: list[CountdownEvent] = []
        for ev in self._events.values():
            if ev.status in (EventStatus.FIRED, EventStatus.CANCELED):
                continue
            ev.tick(now_unix)
            if ev.status == EventStatus.MISSED:
                missed.append(ev)
        return missed

    def __len__(self) -> int:
        return len(self._events)

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._events

    def __iter__(self):
        return iter(self._events.values())

    def __repr__(self) -> str:  # pragma: no cover
        return f"EventStore(dir={self.dir!s} events={len(self._events)})"


__all__ = [
    "EventStatus",
    "SubscriberStatus",
    "CountdownEvent",
    "EventStore",
]