# swarm-tminus

> Time-shaped coordination primitives for [swarm-anchor](https://github.com/SuperInstance/swarm-anchor)-style multi-agent systems.

[![Tests](https://img.shields.io/badge/tests-230%20passing-brightgreen)](#tests)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](#)
[![Deps](https://img.shields.io/badge/dependencies-stdlib%20only-success)](#)
[![License](https://img.shields.io/badge/license-MIT-blue)](#)

**swarm-tminus** is a Python package that adds the time-shaped coordination primitives developed across the SuperInstance T-Minus ecosystem — `t-minus`, `t-minus-rs`, `tminus-music`, `lau-tminus`, `tick-engine`, and `terax-fleet-modules` — to the file-based shared-state model of swarm-anchor.

Predict-and-confirm, deadline cascades, rate limiters with backpressure, BPM-adaptive heartbeats, CRON scheduling, and DAG-ordered campaigns — all in stdlib Python, all living alongside `.swarm/*.json` files.

## Why

Multi-agent swarms typically coordinate through *state*: who's here, what's done, who's responsible. swarm-anchor gives you that. But state alone can't tell you **when** to act, **how long** to wait, or **how fast** to send.

`tminus-*` repositories answered those questions in Rust. `swarm-tminus` is the Python re-implementation: stdlib-only, file-based, drop-in compatible with the swarm-anchor `.swarm/` directory convention.

The unifying idea across the tminus ecosystem:

```
1. Declare the FUTURE (a countdown event, a predicted beat, a deadline)
2. Subscribe agents confirm readiness →  quorum fires
3. Time elapses via a SHARED CLOCK
4. Predictions match →  precompiled script EXECUTES
5. Predictions miss  →  script is discarded, agent re-plans
```

## Installation

```bash
pip install swarm-tminus
```

Or directly from source:

```bash
git clone https://github.com/SuperInstance/swarm-tminus.git
cd swarm-tminus
pip install -e .
```

This installs a `swarm-tminus` CLI command and the `swarm_tminus` Python package.

**Zero runtime dependencies.** Pure stdlib Python 3.10+.

## Quick start

```python
from swarm_tminus import (
    Predictor, CountdownEvent, EventStore,
    DeadlineTree, TokenBucket, LeakyBucket, RatePair,
    TickClock, CronParser, Campaign,
)

# 1. Predict-and-confirm
p = Predictor(bpm=120.0)
pid = p.add_prediction("chord_change", beats_ahead=4.0, confidence=0.9)
triggered = p.advance(beats=5.0)   # advance the clock
p.confirm(pid)

# 2. Quorum-gated countdown events
store = EventStore(".swarm")
e = CountdownEvent(name="deploy-v1", fire_at_unix=time.time() + 300, quorum_required=3)
e.add_subscriber("alice"); e.confirm("alice")
e.confirm("bob")
store.add_event(e); store.save()

# 3. Hierarchical deadline trees
root = DeadlineNode(name="release", duration_seconds=3600)
root.add_child("build", duration_seconds=600)
root.add_child("test", duration_seconds=900)
tree = DeadlineTree(root=root)
tree.save(".swarm")

# 4. Rate limiters
tb = TokenBucket(capacity=10, refill_per_sec=1.0)
lb = LeakyBucket(capacity=10, drip_per_sec=1.0)
pair = RatePair(token=tb, leaky=lb)
ok, reason = pair.try_send(1.0)

# 5. BPM-adaptive heartbeat
clock = TickClock(bpm=120.0, swing=0.5)
t = clock.next_tick()         # Tick(id=0, timestamp=..., delta=...)
clock.adapt(energy=0.7)       # adaptive tempo

# 6. Cron scheduling
nxt = CronParser("*/15 * * * *").next_fire()

# 7. DAG-ordered campaigns
camp = Campaign(name="rollout")
camp.add_event(CountdownEvent(name="step1", fire_at_unix=100.0))
camp.add_event(CountdownEvent(name="step2", fire_at_unix=200.0))
camp.add_edge("step1", "step2")
order = camp.topological_order()    # ["step1", "step2"]
```

## Modules

| Module | Source | Purpose |
|--------|--------|---------|
| `predictor` | tminus-music | Predict-and-confirm with named chord progressions |
| `events` | t-minus | Countdown + quorum-gated events with file-based store |
| `deadlines` | t-minus-rs | Hierarchical deadline trees with cascade cancel |
| `rate` | t-minus-rs | Token + leaky bucket rate limiters, in series |
| `tempo` | tick-engine | BPM-adaptive tick clocks with swing |
| `cron` | t-minus-rs | 5-field cron parser, no external deps |
| `campaign` | t-minus | DAG of countdown events with topological order |
| `matcher` | lau-tminus | Pattern-based event matching with confidence |

All modules export a `__repr__` and a small, focused surface. All are file-backed where appropriate.

## CLI

The `swarm-tminus` command gives you access to every module from the shell:

```bash
# Predict-and-confirm
swarm-tminus predict add --bpm 120 --ahead 4 --confidence 0.9
swarm-tminus predict advance --bpm 120 --beats 8 --ahead 4
swarm-tminus predict savings --predictions 10 --confirmations 3

# Countdown events
swarm-tminus event add --name deploy --in-seconds +5m --quorum 3
swarm-tminus event confirm --name deploy --subscriber alice
swarm-tminus event fire --now-unix 1784154000
swarm-tminus event list

# Deadline trees
swarm-tminus deadline start --name review --duration 60
swarm-tminus deadline cancel --root review --name review
swarm-tminus deadline show --root review

# Rate limiters
swarm-tminus rate token --cap 10 --refill 1 --consume 3
swarm-tminus rate leaky --cap 10 --drip 1 --add 5
swarm-tminus rate pair --cap 10 --refill 1 --drip 1 --consume 3

# Tempo
swarm-tminus tempo tick --bpm 120 --swing 0.5 --count 4
swarm-tminus tempo adapt --bpm 120 --energy 0.7

# Cron
swarm-tminus cron parse --expr "*/5 * * * *"
swarm-tminus cron next --expr "0 9 * * *" --after "+1h"

# Campaigns
swarm-tminus campaign order --edges "a,b|b,c" --nodes "a,b,c"
swarm-tminus campaign add-event --name rollout --event step1 --fire-at 1000
swarm-tminus campaign show --name rollout

# Matcher
swarm-tminus matcher match --name fast \
    --pattern '{"speed":{"gt":100}}' \
    --actual '{"speed":120}'
```

## Integration with swarm-anchor

`swarm-tminus` deliberately does **not** import `swarm-anchor`. The dependency is by convention: both packages share the `.swarm/` directory.

```
.swarm/
├── *.heartbeat.json     ← swarm-anchor (existing)
├── *.prediction.json    ← swarm-tminus (predict-and-confirm)
├── *.deadline.json      ← swarm-tminus (deadline trees)
├── *.event.json         ← swarm-tminus (countdown + quorum)
├── *.campaign.json      ← swarm-tminus (DAG campaigns)
└── *.tempo.json         ← swarm-tminus (BPM-adaptive)
```

Example: an agent's heartbeat cadence driven by a `TickClock`:

```python
from swarm_tminus import TickClock
from swarm_anchor import Heartbeat

clock = TickClock(bpm=120.0)
hb = Heartbeat(agent_id="alice")

while True:
    tick = clock.next_tick()
    hb.update()
    hb.save(".swarm")
    time.sleep(tick.delta)
```

## File-based ground truth

Two modules are explicitly designed for durable shared state:

### `EventStore`

```python
from swarm_tminus import EventStore, CountdownEvent

store = EventStore(".swarm")
store.add_event(CountdownEvent(name="deploy", fire_at_unix=...))
store.save()   # writes deploy.event.json

# Reload later:
store2 = EventStore.load(".swarm")
ev = store2.get("deploy")
```

### `DeadlineTree`

```python
from swarm_tminus import DeadlineTree, DeadlineNode

root = DeadlineNode(name="release", duration_seconds=3600)
root.add_child("build", duration_seconds=600)
tree = DeadlineTree(root=root)
tree.save(".swarm")

loaded = DeadlineTree.load(".swarm", "release")
```

### `Campaign`

```python
from swarm_tminus import Campaign, CountdownEvent

camp = Campaign(name="rollout")
camp.add_event(CountdownEvent(name="step1", fire_at_unix=100.0))
camp.add_event(CountdownEvent(name="step2", fire_at_unix=200.0))
camp.add_edge("step1", "step2")
camp.save(".swarm")
```

## House style

- **stdlib only** — zero runtime deps; PyYAML is optional
- **Heavy tests** — 230 tests across 8 modules
- **Dataclasses** for value types; `__repr__` on everything for debugging
- **File-based** ground truth where applicable
- **CLI per module** — `swarm-tminus <module> <action> [args]`
- **PyPI-ready** — `pyproject.toml` with `console_scripts` entry point

## Architecture decisions

1. **stdlib-only**: matches the swarm-anchor convention; no `pip install numpy`. Tradeoff: cron `next_fire` brute-force-scans up to 5 years of minutes. Acceptable for human timescales.

2. **File-based vs SQLite**: t-minus-rs uses SQLite. swarm-tminus uses JSON files because (a) it slots into swarm-anchor's existing convention, (b) JSON is human-inspectable, (c) zero deps. If you need atomic writes, both `EventStore.save()` and `DeadlineTree.save()` use a `.tmp` + rename pattern.

3. **Token-bucket starts FULL** (per t-minus-rs); **leaky-bucket starts EMPTY**. The asymmetry is intentional: token-bucket allows bursts up to capacity; leaky-bucket smooths them out.

4. **Quorum=0 always fires**: a 0-quorum event with no confirmations still fires, because `confirmed_count() >= 0` is always true.

5. **Deferred attendee grants grace**: an event with any `SubscriberStatus.DEFERRED` attendee is held in `COUNTING` (not `MISSED`) regardless of how much time has passed — mirroring `src/engine.rs:165-189`.

6. **Cycle detection is atomic on `add_edge`**: the edge is added tentatively, validated via topological sort, and reverted if a cycle would form.

## Tests

```bash
$ python3 -m unittest discover tests
......................................................................................................................................................................................................................................
----------------------------------------------------------------------
Ran 230 tests in 0.020s

OK
```

Test breakdown:

| Module | Tests | File |
|--------|-------|------|
| predictor | 41 | tests/test_predictor.py |
| events | 30 | tests/test_events.py |
| deadlines | 26 | tests/test_deadlines.py |
| rate | 35 | tests/test_rate.py |
| tempo | 34 | tests/test_tempo.py |
| cron | 24 | tests/test_cron.py |
| campaign | 18 | tests/test_campaign.py |
| matcher | 22 | tests/test_matcher.py |

## Source provenance

This package is a clean-room Python re-implementation of primitives from the following SuperInstance T-Minus ecosystem repositories:

- **t-minus** — countdown + quorum events
- **t-minus-rs** — cron, deadline cascade, rate limiters, ensemble tempo
- **tminus-music** — predict-and-confirm + chord progressions
- **lau-tminus** — typed event matching with confidence + energy cost
- **tick-engine** — BPM-adaptive scheduler
- **terax-fleet-modules** — *context-and-modeling* (referenced but not yet ported — would be a future addition)

The patterns map 1:1; only the language and storage substrate change.

## License

MIT — see [LICENSE](LICENSE).

## Contributing

Issues and PRs welcome at https://github.com/SuperInstance/swarm-tminus.

When adding a new module:
1. Place it in `swarm_tminus/<name>.py`
2. Re-export from `swarm_tminus/__init__.py`
3. Add a `tests/test_<name>.py` with ≥10 tests
4. Add CLI subparser in `swarm_tminus/cli.py`
5. Document in this README

The repo targets Python 3.10+ for `match`/`case` syntax and modern type hints.

---

*Part of the [SuperInstance](https://github.com/SuperInstance) ecosystem. Built 2026-07-15.*