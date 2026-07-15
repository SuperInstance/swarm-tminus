"""DAG-ordered campaigns of countdown events.

A Campaign is a set of CountdownEvents plus edges (before, after).
`topological_order()` returns the events in a valid execution order
using Kahn's algorithm.

Source: t-minus/src/types.rs (Campaign) and t-minus/src/engine.rs.
"""

from __future__ import annotations

import json
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

from swarm_tminus.events import CountdownEvent, EventStatus


class CycleError(ValueError):
    """Raised when the DAG contains a cycle."""

    def __init__(self, nodes: list[str]):
        self.cycle_nodes = list(nodes)
        super().__init__(f"cycle detected involving nodes: {nodes}")


def topological_order(
    edges: list[tuple[str, str]],
    nodes: Optional[list[str]] = None,
) -> list[str]:
    """Kahn's topological sort.

    `edges` is a list of (before, after) tuples — `before` must come
    before `after` in the output. `nodes` is the explicit node set; if
    omitted, it is inferred from the edges.

    Raises CycleError if the graph has a cycle.
    """
    adj: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = defaultdict(int)
    if nodes is None:
        nodes_set: set[str] = set()
        for a, b in edges:
            nodes_set.add(a)
            nodes_set.add(b)
        nodes = list(nodes_set)
    for n in nodes:
        in_degree.setdefault(n, 0)
        adj.setdefault(n, [])
    for a, b in edges:
        adj[a].append(b)
        in_degree[b] += 1
    # Use deque for O(1) popleft; sort start set for determinism
    queue: deque[str] = deque(sorted(n for n, d in in_degree.items() if d == 0))
    out: list[str] = []
    while queue:
        n = queue.popleft()
        out.append(n)
        # Iterate over a copy so we can modify in_degree
        for m in sorted(adj[n]):
            in_degree[m] -= 1
            if in_degree[m] == 0:
                queue.append(m)
    if len(out) != len(nodes):
        # Cycle: report nodes not in output
        leftover = [n for n in nodes if n not in out]
        raise CycleError(leftover)
    return out


@dataclass
class Campaign:
    """A DAG of CountdownEvents with edge dependencies.

    Edges are (before, after) pairs: `before` must fire before `after`.
    """
    name: str
    events: list[CountdownEvent] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)
    _index: dict[str, CountdownEvent] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self._reindex()

    def _reindex(self) -> None:
        self._index = {e.name: e for e in self.events}

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def add_event(self, event: CountdownEvent) -> None:
        if event.name in self._index:
            raise ValueError(f"event {event.name!r} already in campaign")
        self.events.append(event)
        self._index[event.name] = event

    def add_edge(self, before: str, after: str) -> None:
        """Add a (before, after) dependency. Raises if either side is unknown or a cycle forms."""
        if before not in self._index:
            raise KeyError(f"unknown event {before!r}")
        if after not in self._index:
            raise KeyError(f"unknown event {after!r}")
        if before == after:
            raise ValueError(f"self-edge not allowed: {before!r}")
        # Cycle check: would `after → before` already exist? Or any path?
        # Cheap: just try to add and topo-sort
        self.edges.append((before, after))

    def add_edge_force(self, before: str, after: str) -> None:
        """Add edge and validate via topo sort; raises CycleError on cycle.

        Removes the offending edge if cycle is detected.
        """
        self.add_edge(before, after)
        try:
            self.topological_order()
        except CycleError:
            # Roll back
            self.edges.pop()
            raise

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def topological_order(self) -> list[str]:
        """Return the topological order of event names."""
        return topological_order(self.edges, nodes=list(self._index.keys()))

    def cycle_check(self) -> bool:
        """Return False if a cycle exists, True if DAG is valid.

        Raises CycleError only if you call topological_order() on a cyclic graph.
        This method never raises — it just returns the bool.
        """
        try:
            self.topological_order()
            return True
        except CycleError:
            return False

    def get(self, name: str) -> Optional[CountdownEvent]:
        return self._index.get(name)

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "events": [e.to_dict() for e in self.events],
            "edges": [list(pair) for pair in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Campaign":
        events = [CountdownEvent.from_dict(d) for d in data.get("events", [])]
        edges = [tuple(pair) for pair in data.get("edges", [])]  # type: ignore[misc]
        return cls(
            name=data["name"],
            events=events,
            edges=edges,
        )

    def save(self, dir: Union[str, Path]) -> None:
        path = Path(dir)
        path.mkdir(parents=True, exist_ok=True)
        target = path / f"{self.name}.campaign.json"
        tmp = target.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        tmp.replace(target)

    @classmethod
    def load(cls, dir: Union[str, Path], name: str) -> "Campaign":
        path = Path(dir) / f"{name}.campaign.json"
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return cls.from_dict(data)

    def __repr__(self) -> str:  # pragma: no cover
        return f"Campaign(name={self.name!r} events={len(self.events)} edges={len(self.edges)})"


__all__ = [
    "Campaign",
    "CycleError",
    "topological_order",
]