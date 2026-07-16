"""Hierarchical deadline trees with cascade cancellation.

Each DeadlineNode has a duration and may have children. When a parent
is cancelled or expires, all descendants inherit the transition.

Source: t-minus-rs/src/deadline.rs.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Optional


class DeadlineStatus(str, enum.Enum):
    """Lifecycle status of a deadline node."""
    ACTIVE = "active"
    COMPLETED = "completed"   # alias for "expired but useful"; kept for clarity
    CANCELLED = "cancelled"
    EXPIRED = "expired"


@dataclass
class DeadlineNode:
    """A node in a deadline tree.

    Each node knows its parent (None for roots), its children, and a
    duration in seconds. `started_at_unix` is set on `start()`.
    """
    name: str
    parent: Optional["DeadlineNode"] = field(default=None, repr=False)
    children: list["DeadlineNode"] = field(default_factory=list, repr=False)
    duration_seconds: float = 0.0
    started_at_unix: Optional[float] = None
    status: DeadlineStatus = DeadlineStatus.ACTIVE
    completed_at_unix: Optional[float] = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = DeadlineStatus(self.status)

    # ------------------------------------------------------------------
    # Tree building
    # ------------------------------------------------------------------

    def add_child(self, name: str, duration_seconds: float) -> "DeadlineNode":
        """Create a child node and append it."""
        child = DeadlineNode(
            name=name,
            parent=self,
            duration_seconds=duration_seconds,
        )
        self.children.append(child)
        return child

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self, now_unix: float) -> None:
        """Mark this node as started. Lazy: doesn't start children."""
        if self.status == DeadlineStatus.ACTIVE:
            self.started_at_unix = now_unix

    def complete(self, now_unix: Optional[float] = None) -> None:
        """Mark this node as completed (success path)."""
        if self.status == DeadlineStatus.ACTIVE:
            self.status = DeadlineStatus.COMPLETED
            if now_unix is not None:
                self.completed_at_unix = now_unix

    def cancel(self, cascade: bool = True) -> list["DeadlineNode"]:
        """Cancel this node. If cascade=True, also cancel all descendants.

        Returns the list of all nodes cancelled (including self).
        """
        cancelled: list[DeadlineNode] = []
        if self.status not in (DeadlineStatus.CANCELLED, DeadlineStatus.COMPLETED):
            self.status = DeadlineStatus.CANCELLED
            cancelled.append(self)
        if cascade:
            cancelled.extend(cascade_cancel_recursive(self))
        return cancelled

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def is_expired(self, now_unix: float) -> bool:
        """Has the deadline passed without completion?"""
        if self.started_at_unix is None:
            return False
        if self.status != DeadlineStatus.ACTIVE:
            return False
        return (now_unix - self.started_at_unix) >= self.duration_seconds

    def remaining(self, now_unix: float) -> Optional[float]:
        """Time remaining until expiry, or None if not started/already terminal."""
        if self.started_at_unix is None:
            return None
        if self.status != DeadlineStatus.ACTIVE:
            return None
        elapsed = now_unix - self.started_at_unix
        return max(0.0, self.duration_seconds - elapsed)

    def depth(self) -> int:
        """Depth in the tree (root=0)."""
        d = 0
        cur: Optional[DeadlineNode] = self.parent
        while cur is not None:
            d += 1
            cur = cur.parent
        return d

    def descendants(self) -> list["DeadlineNode"]:
        """All descendants in DFS order."""
        out: list[DeadlineNode] = []
        for c in self.children:
            out.append(c)
            out.extend(c.descendants())
        return out

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"DeadlineNode(name={self.name!r} dur={self.duration_seconds}s "
            f"status={self.status.value!r} children={len(self.children)})"
        )


def cascade_cancel_recursive(node: DeadlineNode) -> list[DeadlineNode]:
    """Walk children depth-first, cancelling ACTIVE descendants."""
    cancelled: list[DeadlineNode] = []
    for child in node.children:
        if child.status == DeadlineStatus.ACTIVE:
            child.status = DeadlineStatus.CANCELLED
            cancelled.append(child)
        cancelled.extend(cascade_cancel_recursive(child))
    return cancelled


def cascade_cancel(node: DeadlineNode) -> int:
    """Cancel `node` and all its descendants. Returns count cancelled.

    Idempotent: cancelling an already-cancelled node returns 0.
    """
    if node.status == DeadlineStatus.CANCELLED:
        return 0
    cancelled = node.cancel(cascade=True)
    return len(cancelled)


@dataclass
class DeadlineTree:
    """Wrapper around a root DeadlineNode with name-based lookup."""
    root: DeadlineNode
    _index: dict[str, DeadlineNode] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._reindex()

    def _reindex(self) -> None:
        """Rebuild the name->node index."""
        self._index = {}
        stack = [self.root]
        while stack:
            cur = stack.pop()
            self._index[cur.name] = cur
            stack.extend(cur.children)

    def find(self, name: str) -> Optional[DeadlineNode]:
        """Look up a node by name.

        Uses a lazy re-index on miss: if a node was added to the tree via
        ``root.add_child(...)`` after the tree was constructed, the index
        will be stale. We walk the tree to recover the node on miss.
        """
        if name in self._index:
            return self._index[name]
        # Lazy re-index: walk the tree to find nodes missing from the index.
        found = self._walk_find(self.root, name)
        if found is not None:
            self._index[name] = found
        return found

    @staticmethod
    def _walk_find(node: "DeadlineNode", name: str) -> Optional["DeadlineNode"]:
        """DFS search through the tree for a node with the given name."""
        if node.name == name:
            return node
        for child in node.children:
            hit = DeadlineTree._walk_find(child, name)
            if hit is not None:
                return hit
        return None

    def active(self) -> list[DeadlineNode]:
        """All nodes still ACTIVE."""
        return [n for n in self._index.values() if n.status == DeadlineStatus.ACTIVE]

    def expired(self, now_unix: float) -> list[DeadlineNode]:
        """All ACTIVE nodes whose deadline has passed at `now_unix`."""
        return [n for n in self._index.values() if n.is_expired(now_unix)]

    def cancel(self, name: str) -> int:
        """Cancel a node by name. Returns count cancelled (or 0 if not found).

        Will lazy-reindex if the node was added after tree construction.
        """
        node = self.find(name)
        if node is None:
            return 0
        return cascade_cancel(node)

    def all_nodes(self) -> list[DeadlineNode]:
        return list(self._index.values())

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialize the tree to a JSON-compatible dict."""
        def ser(n: DeadlineNode) -> dict:
            return {
                "name": n.name,
                "duration_seconds": n.duration_seconds,
                "started_at_unix": n.started_at_unix,
                "completed_at_unix": n.completed_at_unix,
                "status": n.status.value,
                "children": [ser(c) for c in n.children],
            }
        return {"root": ser(self.root)}

    @classmethod
    def from_dict(cls, data: dict) -> "DeadlineTree":
        root = cls._deser(data["root"], parent=None)
        return cls(root=root)

    @classmethod
    def _deser(cls, data: dict, parent: Optional[DeadlineNode]) -> DeadlineNode:
        node = DeadlineNode(
            name=data["name"],
            parent=parent,
            duration_seconds=float(data.get("duration_seconds", 0.0)),
            started_at_unix=data.get("started_at_unix"),
            completed_at_unix=data.get("completed_at_unix"),
            status=DeadlineStatus(data.get("status", "active")),
        )
        for c in data.get("children", []):
            node.children.append(cls._deser(c, parent=node))
        return node

    def save(self, dir: "object") -> None:  # type: ignore[override]
        """Persist the tree to `<dir>/<root.name>.deadline.json`.

        `dir` may be a string or Path.
        """
        from pathlib import Path
        path = Path(dir)  # type: ignore[arg-type]
        path.mkdir(parents=True, exist_ok=True)
        import json
        target = path / f"{self.root.name}.deadline.json"
        tmp = target.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        tmp.replace(target)

    @classmethod
    def load(cls, dir: "object", name: str) -> "DeadlineTree":  # type: ignore[override]
        """Load a previously-saved tree by name."""
        from pathlib import Path
        path = Path(dir) / f"{name}.deadline.json"  # type: ignore[arg-type]
        import json
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    def __repr__(self) -> str:  # pragma: no cover
        return f"DeadlineTree(root={self.root.name!r} nodes={len(self._index)})"


__all__ = [
    "DeadlineStatus",
    "DeadlineNode",
    "DeadlineTree",
    "cascade_cancel",
]