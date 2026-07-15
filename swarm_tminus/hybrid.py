"""HybridAnchor: swarm-tminus + swarm-anchor under one .swarm/ roof.

This module is the *glue* between swarm-tminus (time-shaped coordination
primitives) and swarm-anchor (file-based shared state). swarm-anchor is
declared as an **optional peer dependency** — swarm-tminus continues to
work without it (degraded mode: pure swarm-tminus file layout), and
lights up richer heartbeats + roster queries when installed.

Same `.swarm/` directory convention. One root, many file types:

```
.swarm/
├── *.heartbeat.json     ← swarm-anchor (if installed)  ← HybridAnchor.heartbeat()
├── *.event.json         ← swarm-tminus                ← HybridAnchor.add_event()
├── *.deadline.json      ← swarm-tminus                ← HybridAnchor.deadline_tree().save()
└── *.campaign.json      ← swarm-tminus                ← HybridAnchor.campaign()
```

The shepherd's console and any animal in the swarm sees a single unified
shared state — heartbeat next to event files next to deadline trees.

Source: the documented gap in swarm-tminus v0.1.0 README ("Honest gaps")
plus the swarm-anchor API at github.com/SuperInstance/swarm-anchor.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Union


# ---------------------------------------------------------------------------
# Optional swarm-anchor import (peer dep)
# ---------------------------------------------------------------------------

_HAS_SWARM_ANCHOR = False
_Anchor: Any = None
_Heartbeat: Any = None
_HeartbeatStatus: Any = None
_Roster: Any = None
_swarm_anchor_version: str = ""

try:
    from swarm_anchor import (
        Anchor as _Anchor,
        Heartbeat as _Heartbeat,
        HeartbeatStatus as _HeartbeatStatus,
        Roster as _Roster,
    )
    import swarm_anchor as _swarm_anchor_module
    _swarm_anchor_version = getattr(_swarm_anchor_module, "__version__", "unknown")
    _HAS_SWARM_ANCHOR = True
except ImportError:
    # swarm-anchor not installed — degrade gracefully.
    pass


# Re-export the imports under public names (only when present).
def _anchor_class():
    return _Anchor


def _heartbeat_class():
    return _Heartbeat


def _heartbeat_status_class():
    return _HeartbeatStatus


def _roster_class():
    return _Roster


# ---------------------------------------------------------------------------
# Lazy heartbeat stub (only used when swarm-anchor is missing)
# ---------------------------------------------------------------------------

@dataclass
class _LocalHeartbeat:
    """Minimal Heartbeat used when swarm-anchor is not installed.

    Mirrors the swarm-anchor `Heartbeat` shape (animal, model, task,
    status, started_at, last_seen, pid, proposals, warnings, extras)
    so that `.heartbeat.json` files written here are *read-compatible*
    with swarm-anchor's `Roster.active()`.
    """

    animal: str
    model: str = ""
    task: str = ""
    status: str = "starting"
    started_at: str = ""
    last_seen: str = ""
    pid: int = 0
    proposals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extras: dict[str, str] = field(default_factory=dict)

    def touch(self) -> None:
        now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        self.last_seen = now
        if not self.started_at:
            self.started_at = now


# ---------------------------------------------------------------------------
# HybridAnchor
# ---------------------------------------------------------------------------

class HybridAnchor:
    """swarm-tminus + swarm-anchor under one .swarm/ roof.

    When swarm-anchor is installed:
        - `heartbeat(hb)` uses swarm-anchor's Heartbeat for `.heartbeat.json`
          file shape (so swarm-anchor's Roster can read it back).
        - `roster()` returns the swarm-anchor Roster.
    When swarm-anchor is NOT installed:
        - `heartbeat(hb)` writes a local stub Heartbeat that's still
          swarm-anchor JSON-compatible.
        - `roster()` returns a list of stub heartbeats (since swarm-anchor
          isn't there to construct a Roster).

    Either way, EventStore / DeadlineTree / Campaign live alongside.

    Args:
        root: directory for `.swarm/` files. Created if missing.
    """

    def __init__(self, root: str = ".swarm") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._anchor: Any = None  # swarm_anchor.Anchor (when available)
        if _HAS_SWARM_ANCHOR:
            self._anchor = _Anchor(root=str(self.root))

        # Lazy imports of swarm-tminus modules to keep `import swarm_tminus`
        # light (and to avoid circular import surprises).
        from swarm_tminus.events import EventStore
        from swarm_tminus.deadlines import DeadlineTree
        from swarm_tminus.tempo import TickClock

        self._EventStore = EventStore
        self._DeadlineTree = DeadlineTree
        self._TickClock = TickClock

        self.events = EventStore(self.root)
        self.tempo: TickClock = TickClock()
        # Deadline trees are looked up on demand (see deadline_tree()).
        self._deadline_trees: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Capability flag
    # ------------------------------------------------------------------

    @property
    def has_swarm_anchor(self) -> bool:
        """True iff the swarm-anchor package is importable."""
        return _HAS_SWARM_ANCHOR

    @property
    def swarm_anchor_version(self) -> str:
        """Version of the loaded swarm-anchor, or "not-installed"."""
        return _swarm_anchor_version if _HAS_SWARM_ANCHOR else "not-installed"

    # ------------------------------------------------------------------
    # Heartbeats (swarm-anchor) — with graceful degradation
    # ------------------------------------------------------------------

    def heartbeat(
        self,
        animal: str,
        model: str = "",
        task: str = "",
        status: Any = None,
        proposals: Optional[list[str]] = None,
        warnings: Optional[list[str]] = None,
        extras: Optional[dict[str, str]] = None,
    ) -> Path:
        """Write a heartbeat file for `animal`.

        Returns the path written.
        """
        if _HAS_SWARM_ANCHOR and _Heartbeat is not None and _HeartbeatStatus is not None:
            status_enum = status if status is not None else _HeartbeatStatus.STARTING
            hb = _Heartbeat(
                animal=animal,
                model=model,
                task=task,
                status=status_enum,
            )
            if proposals:
                for p in proposals:
                    hb.add_proposal(p)
            if warnings:
                for w in warnings:
                    hb.add_warning(w)
            if extras:
                hb.extras.update(extras)
            if self._anchor is not None:
                return self._anchor.heartbeat(hb)
            # Fall through to degraded if the anchor ref is gone.

        # Degraded mode: write a local stub.
        local = _LocalHeartbeat(
            animal=animal,
            model=model,
            task=task,
            status=(status.value if hasattr(status, "value") else (status or "starting")),
        )
        if proposals:
            local.proposals.extend(proposals)
        if warnings:
            local.warnings.extend(warnings)
        if extras:
            local.extras.update(extras)
        local.touch()
        path = self.root / f"{animal}.heartbeat.json"
        path.write_text(
            json.dumps(local.__dict__, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Countdown events
    # ------------------------------------------------------------------

    def add_event(self, event: Any) -> Path:
        """Add (or replace) a CountdownEvent and persist it.

        Returns the file path written.
        """
        self.events.add_event(event)
        return self._write_event_file(event)

    def _write_event_file(self, event: Any) -> Path:
        # Reuse EventStore's sanitizer/serialization path.
        path = self.events._path_for(event.name)  # type: ignore[attr-defined]
        # Ensure it's in the right dir.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(event.to_dict(), fh, indent=2, sort_keys=True)
        tmp.replace(path)
        return path

    # ------------------------------------------------------------------
    # Deadline trees
    # ------------------------------------------------------------------

    def deadline_tree(self, root_node: Any) -> Any:
        """Wrap a DeadlineNode in a DeadlineTree tracked under `root_node.name`.

        The tree is kept in memory and persisted via `.save(self.root)`.
        """
        tree = self._DeadlineTree(root=root_node)
        self._deadline_trees[root_node.name] = tree
        tree.save(self.root)
        return tree

    def load_deadline_tree(self, name: str) -> Optional[Any]:
        """Load a previously saved deadline tree by root name (or None)."""
        try:
            tree = self._DeadlineTree.load(self.root, name)
            self._deadline_trees[name] = tree
            return tree
        except FileNotFoundError:
            return None

    # ------------------------------------------------------------------
    # TickClock
    # ------------------------------------------------------------------

    def tick_clock(self) -> Any:
        """Return the shared TickClock instance."""
        return self.tempo

    # ------------------------------------------------------------------
    # Roster (only meaningful when swarm-anchor is installed)
    # ------------------------------------------------------------------

    def roster(self, stale_seconds: int = 30) -> Any:
        """Return the current roster.

        With swarm-anchor: a `swarm_anchor.Roster`.
        Without: a plain dict `{"at": iso, "animals": [stub dicts]}`.
        """
        if _HAS_SWARM_ANCHOR:
            return self._anchor.roster(stale_seconds=stale_seconds)
        # Degraded mode: read *.heartbeat.json files ourselves.
        animals: list[dict[str, Any]] = []
        at = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        for fp in self.root.glob("*.heartbeat.json"):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                data["_path"] = str(fp)
                animals.append(data)
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        return {"at": at, "animals": animals, "stale_seconds": stale_seconds}

    # ------------------------------------------------------------------
    # Campaign (lazily imported)
    # ------------------------------------------------------------------

    def campaign(self, name: str) -> Any:
        """Construct a Campaign attached to this anchor's directory."""
        from swarm_tminus.campaign import Campaign
        return Campaign(name=name)

    # ------------------------------------------------------------------
    # Aggregated view
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Render all .swarm/ state in one place — for the shepherd's-console
        kennel view.

        Returns a dict with keys:
            root, swarm_anchor (bool), swarm_anchor_version,
            heartbeats (list), events (list), deadlines (list),
            tick_clock_bpm (float), tick_clock_swing (float),
            campaign_count (int)
        """
        # Heartbeats
        hb_files = sorted(self.root.glob("*.heartbeat.json"))
        heartbeats: list[dict[str, Any]] = []
        for fp in hb_files:
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                data["_path"] = str(fp)
                heartbeats.append(data)
            except (json.JSONDecodeError, KeyError, ValueError):
                heartbeats.append({"_path": str(fp), "_error": "unparseable"})

        # Events (use EventStore for consistency)
        events_list = [
            {
                "name": e.name,
                "fire_at_unix": e.fire_at_unix,
                "status": e.status.value,
                "quorum_required": e.quorum_required,
                "confirmed": e.confirmed_count(),
            }
            for e in self.events.all_events()
        ]

        # Deadlines — read any *.deadline.json files we find
        deadlines: list[dict[str, Any]] = []
        for fp in sorted(self.root.glob("*.deadline.json")):
            try:
                data = json.loads(fp.read_text(encoding="utf-8"))
                deadlines.append(
                    {"name": fp.stem, "root": data.get("root", {}).get("name"),
                     "status": data.get("root", {}).get("status")}
                )
            except (json.JSONDecodeError, KeyError, ValueError):
                deadlines.append({"name": fp.stem, "_error": "unparseable"})

        # Campaign count
        campaigns = list(self.root.glob("*.campaign.json"))

        return {
            "root": str(self.root),
            "swarm_anchor": self.has_swarm_anchor,
            "swarm_anchor_version": self.swarm_anchor_version,
            "heartbeats": heartbeats,
            "heartbeat_count": len(heartbeats),
            "events": events_list,
            "event_count": len(events_list),
            "deadlines": deadlines,
            "deadline_count": len(deadlines),
            "tick_clock_bpm": self.tempo.bpm,
            "tick_clock_swing": self.tempo.swing,
            "campaign_count": len(campaigns),
        }

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def reap(self, animal: str) -> bool:
        """Remove a heartbeat file. Returns True if removed."""
        path = self.root / f"{animal}.heartbeat.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def __repr__(self) -> str:  # pragma: no cover
        anchor = "swarm-anchor" if _HAS_SWARM_ANCHOR else "degraded"
        return (
            f"HybridAnchor(root={self.root!s} mode={anchor!r} "
            f"events={len(self.events)} heartbeats={len(list(self.root.glob('*.heartbeat.json')))})"
        )


__all__ = [
    "HybridAnchor",
    "_HAS_SWARM_ANCHOR",
]