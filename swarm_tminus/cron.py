"""5-field cron expression parser and next-fire computer.

Supports the Vixie-cron field set:
  *      — any value
  N      — exact
  A,B,C  — list
  A-B    — range
  */N or A-B/N or A/N  — step (every N starting at A)

Fields are: minute hour day-of-month month day-of-week (Sunday=0).

Matching semantics:
    All fields are AND-combined: a time matches iff every field matches.
    NOTE: Standard Vixie cron uses OR semantics when BOTH day-of-month AND
    day-of-week are restricted (e.g. ``30 4 1,15 * 5`` fires on the 1st/15th
    OR every Friday). This implementation deliberately uses AND semantics,
    matching the upstream t-minus-rs/src/schedule.rs:165-178. If you need
    Vixie-OR semantics, split the expression into two.

Source: t-minus-rs/src/schedule.rs.
"""

from __future__ import annotations

import calendar
import datetime as _dt
import time
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Field bounds
# ---------------------------------------------------------------------------

_FIELD_BOUNDS = {
    "minute": (0, 59),
    "hour": (0, 23),
    "dom": (1, 31),
    "month": (1, 12),
    "dow": (0, 6),  # 0 = Sunday
}

_FIELD_ORDER = ("minute", "hour", "dom", "month", "dow")


class CronError(ValueError):
    """Raised on any cron parse error."""


# ---------------------------------------------------------------------------
# CronField — internal representation
# ---------------------------------------------------------------------------

@dataclass
class _CronField:
    """Internal: a parsed cron field. The set of allowed values is computed lazily."""
    raw: str
    kind: str           # "all", "exact", "list", "range", "step"
    min_val: int
    max_val: int
    values: Optional[set[int]] = field(default=None)

    def matches(self, value: int) -> bool:
        if value < self.min_val or value > self.max_val:
            return False
        if self.values is not None:
            return value in self.values
        # Compute on demand
        if self.kind == "all":
            return True
        if self.kind == "exact":
            return value == int(self.raw)
        if self.kind == "list":
            return value in self._parse_list()
        if self.kind == "range":
            a, b = self._parse_range()
            return a <= value <= b
        if self.kind == "step":
            start, step = self._parse_step()
            # Standard cron step: anchor at min_val when start is "*"
            anchor = start if start != self.min_val else self.min_val
            return value >= anchor and (value - anchor) % step == 0
        return False

    def _parse_list(self) -> set[int]:
        return {int(p) for p in self.raw.split(",")}

    def _parse_range(self) -> tuple[int, int]:
        a, b = self.raw.split("-")
        return int(a), int(b)

    def _parse_step(self) -> tuple[int, int]:
        # raw looks like "*/N" or "A-B/N" or "A/N"
        if "/" in self.raw:
            base, step = self.raw.split("/", 1)
            step_n = int(step)
            if base == "*":
                start = self.min_val
            elif "-" in base:
                a, _b = base.split("-", 1)
                start = int(a)
            else:
                start = int(base)
            return start, step_n
        # shouldn't happen
        return self.min_val, 1

    def allowed_values(self) -> set[int]:
        if self.values is not None:
            return self.values
        # Compute full set lazily
        if self.kind == "all":
            return set(range(self.min_val, self.max_val + 1))
        if self.kind == "exact":
            return {int(self.raw)}
        if self.kind == "list":
            return self._parse_list()
        if self.kind == "range":
            a, b = self._parse_range()
            return set(range(a, b + 1))
        if self.kind == "step":
            start, step = self._parse_step()
            # anchor at min_val if start is min_val; otherwise range starts at start.
            return set(range(start, self.max_val + 1, step))
        return set()


def _parse_field(token: str, min_val: int, max_val: int) -> _CronField:
    token = token.strip()
    if not token:
        raise CronError(f"empty field, expected [{min_val},{max_val}]")

    # Determine kind
    if token == "*":
        return _CronField(token, "all", min_val, max_val)
    if "/" in token:
        # step
        base, step = token.split("/", 1)
        try:
            step_n = int(step)
            if step_n <= 0:
                raise CronError(f"step must be > 0, got {step!r}")
        except ValueError:
            raise CronError(f"invalid step in {token!r}")
        if base == "*":
            start = min_val
            end = max_val
        elif "-" in base:
            try:
                a, b = base.split("-", 1)
                lo, hi = int(a), int(b)
                if lo < min_val or hi > max_val or lo > hi:
                    raise CronError(
                        f"range out of bounds in {token!r} (must be [{min_val},{max_val}])"
                    )
            except ValueError:
                raise CronError(f"invalid range in {token!r}")
            start = lo
            end = hi
        else:
            try:
                start = int(base)
            except ValueError:
                raise CronError(f"invalid step anchor in {token!r}")
            if start < min_val or start > max_val:
                raise CronError(
                    f"step anchor out of bounds in {token!r} (must be [{min_val},{max_val}])"
                )
            end = max_val
        # Validate the whole range
        values = set(range(start, end + 1, step_n))
        if not values:
            raise CronError(f"step {token!r} yields no values")
        return _CronField(token, "step", min_val, max_val, values=values)
    if "," in token:
        # list
        parts = [p.strip() for p in token.split(",") if p.strip()]
        values: set[int] = set()
        for p in parts:
            try:
                v = int(p)
            except ValueError:
                raise CronError(f"invalid list item {p!r} in {token!r}")
            if v < min_val or v > max_val:
                raise CronError(
                    f"value {v} out of bounds [{min_val},{max_val}] in {token!r}"
                )
            values.add(v)
        return _CronField(token, "list", min_val, max_val, values=values)
    if "-" in token:
        a, b = token.split("-", 1)
        try:
            lo, hi = int(a), int(b)
        except ValueError:
            raise CronError(f"invalid range {token!r}")
        if lo < min_val or hi > max_val or lo > hi:
            raise CronError(
                f"range out of bounds in {token!r} (must be [{min_val},{max_val}])"
            )
        values = set(range(lo, hi + 1))
        return _CronField(token, "range", min_val, max_val, values=values)
    # exact
    try:
        v = int(token)
    except ValueError:
        raise CronError(f"invalid exact value {token!r}")
    if v < min_val or v > max_val:
        raise CronError(
            f"value {v} out of bounds [{min_val},{max_val}] in {token!r}"
        )
    return _CronField(token, "exact", min_val, max_val, values={v})


# ---------------------------------------------------------------------------
# CronParser
# ---------------------------------------------------------------------------

@dataclass
class CronParser:
    """Parse and query a 5-field cron expression."""
    expression: str
    fields: dict[str, _CronField] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.expression or not self.expression.strip():
            raise CronError("empty cron expression")
        parts = self.expression.split()
        if len(parts) != 5:
            raise CronError(
                f"expected 5 fields, got {len(parts)}: {self.expression!r}"
            )
        parsed: dict[str, _CronField] = {}
        for name, token in zip(_FIELD_ORDER, parts):
            lo, hi = _FIELD_BOUNDS[name]
            parsed[name] = _parse_field(token, lo, hi)
        self.fields = parsed

    def parse(self) -> dict[str, set[int]]:
        """Return a dict of field-name → set of allowed values."""
        return {name: f.allowed_values() for name, f in self.fields.items()}

    def matches(self, when: _dt.datetime) -> bool:
        """Return True if `when` matches the parsed cron expression."""
        if when.second != 0:
            # We only match on minute granularity
            return False
        # Python weekday(): Monday=0..Sunday=6. Cron DOW: Sunday=0..Saturday=6.
        cron_dow = (when.weekday() + 1) % 7
        return all(
            f.matches(val)
            for f, val in zip(
                self.fields.values(),
                (when.minute, when.hour, when.day, when.month, cron_dow),
            )
        )

    def matches_unix(self, ts: float) -> bool:
        """Return True if the unix timestamp matches."""
        when = _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc)
        # Cron DOW: in Python, Monday=0..Sunday=6; cron uses Sunday=0..Saturday=6
        # Translate:
        py_dow = when.weekday()  # Mon=0..Sun=6
        cron_dow = (py_dow + 1) % 7  # Sun=0..Sat=6
        # Match on components
        if not self.fields["minute"].matches(when.minute):
            return False
        if not self.fields["hour"].matches(when.hour):
            return False
        if not self.fields["dom"].matches(when.day):
            return False
        if not self.fields["month"].matches(when.month):
            return False
        if not self.fields["dow"].matches(cron_dow):
            return False
        return True

    def next_fire(self, after_unix: Optional[float] = None) -> float:
        """Compute the next unix timestamp >= `after_unix` that matches.

        Brute-force scan in 1-minute steps (per t-minus-rs design).
        Capped at ~5 years to avoid infinite loops on impossible expressions.
        """
        if after_unix is None:
            after_unix = time.time()
        # Snap to next minute boundary
        start = int(after_unix // 60) * 60
        # We want strictly > after_unix
        if start <= after_unix:
            start += 60
        max_steps = 5 * 365 * 24 * 60  # 5 years of minutes
        for i in range(max_steps):
            candidate = start + i * 60
            if self.matches_unix(candidate):
                return float(candidate)
        raise CronError(
            f"no match found within 5 years for expression {self.expression!r}"
        )


def next_fire(expression: str, after_unix: Optional[float] = None) -> float:
    """Convenience: parse + compute next fire time."""
    parser = CronParser(expression)
    return parser.next_fire(after_unix=after_unix)


__all__ = [
    "CronError",
    "CronParser",
    "next_fire",
]