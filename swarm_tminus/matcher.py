"""Pattern-based event matching with confidence + energy cost.

Mirrors lau-tminus's typed event matching. A pattern describes what
kind of event you expect; an actual event says what really happened.
`matches()` returns True if the actual fits the pattern.

Source: lau-tminus/src/lib.rs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


# Supported operators in pattern dicts:
#   key_eq, key_neq      — equality / inequality
#   key_gt, key_gte      — numeric comparison
#   key_lt, key_lte      — numeric comparison
#   key_in               — membership in a list
#   key_contains         — substring / list membership
#   key_exists           — key must be present
#   key_absent           — key must be absent

_OPS = (
    "eq", "neq", "gt", "gte", "lt", "lte",
    "in", "contains", "exists", "absent",
)


def _eval_op(actual: Any, op: str, expected: Any) -> bool:
    if op == "eq":
        return actual == expected
    if op == "neq":
        return actual != expected
    if op in ("gt", "gte", "lt", "lte"):
        try:
            if op == "gt":
                return actual > expected
            if op == "gte":
                return actual >= expected
            if op == "lt":
                return actual < expected
            if op == "lte":
                return actual <= expected
        except TypeError:
            return False
    if op == "in":
        return actual in expected
    if op == "contains":
        if isinstance(actual, str) and isinstance(expected, str):
            return expected in actual
        if isinstance(actual, (list, tuple, set, frozenset)):
            return expected in actual
        return False
    if op == "exists":
        return actual is not None
    if op == "absent":
        return actual is None
    return False


@dataclass
class EventMatcher:
    """Pattern-based event matcher.

    `pattern` is a dict mapping key names to either:
    - A bare value (interpreted as an equality check against actual[key]), or
    - A dict of {op: value} pairs (e.g. {"gt": 100}).

    The matcher supports a special key `__type__` for matching the
    "type" field of an actual event.
    """
    name: str
    pattern: dict[str, Any]
    confidence: float = 1.0
    energy_cost: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0,1], got {self.confidence!r}"
            )
        if self.energy_cost < 0:
            raise ValueError(f"energy_cost must be >= 0, got {self.energy_cost!r}")

    def matches(self, actual: dict[str, Any]) -> bool:
        """Return True iff `actual` satisfies every entry in `pattern`."""
        for key, expected in self.pattern.items():
            if key == "__type__":
                # Special: actual.type must equal expected
                if actual.get("type") != expected:
                    return False
                continue
            value = actual.get(key)
            if isinstance(expected, dict):
                # dict of {op: value}
                for op, op_val in expected.items():
                    if op not in _OPS:
                        return False
                    if not _eval_op(value, op, op_val):
                        return False
            else:
                # bare value: equality against actual[key]
                if value != expected:
                    return False
        return True

    def executable(self, budget: float) -> bool:
        """True iff this matcher's energy_cost fits within `budget`."""
        return self.energy_cost <= budget

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"EventMatcher(name={self.name!r} conf={self.confidence:.2f} "
            f"cost={self.energy_cost:.2f})"
        )


@dataclass
class EventMatch:
    """The result of matching an actual event against a matcher."""
    matcher: EventMatcher
    actual: dict[str, Any]
    confidence: float

    def can_execute(self, budget: float) -> bool:
        return self.matcher.executable(budget)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"EventMatch(matcher={self.matcher.name!r} "
            f"confidence={self.confidence:.2f})"
        )


def find_matches(matchers: list[EventMatcher], actual: dict[str, Any]) -> list[EventMatch]:
    """Return all matchers that match the given actual event, sorted by confidence desc."""
    hits = [
        EventMatch(matcher=m, actual=actual, confidence=m.confidence)
        for m in matchers
        if m.matches(actual)
    ]
    hits.sort(key=lambda x: x.confidence, reverse=True)
    return hits


__all__ = [
    "EventMatcher",
    "EventMatch",
    "find_matches",
]