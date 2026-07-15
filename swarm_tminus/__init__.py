"""swarm-tminus: tminus capabilities for swarm-anchor.

A unified Python package that adds the time-shaped coordination
primitives developed across the SuperInstance T-Minus ecosystem
(t-minus, t-minus-rs, tminus-music, lau-tminus, tick-engine,
terax-fleet-modules) to the file-based shared-state model of
swarm-anchor.

Predict-and-confirm, deadline cascades, rate limiters with
backpressure, BPM-adaptive heartbeats, CRON scheduling, and
DAG-ordered campaigns — all in stdlib Python, all living
alongside `.swarm/heartbeat.json` files.

This package imports nothing from swarm-anchor (the dependency
is by convention, not import), so it can also be used standalone.

Usage:
    from swarm_tminus import (
        Predictor, Prediction,
        CountdownEvent, EventStore, EventStatus, SubscriberStatus,
        DeadlineTree, DeadlineNode, cascade_cancel,
        TokenBucket, LeakyBucket, RatePair,
        TickClock, BPM, swing, TempoNegotiator,
        CronParser, next_fire,
        Campaign, topological_order,
        EventMatcher, EventMatch,
    )
"""

from swarm_tminus.predictor import (
    Predictor, Prediction, MessageSavings,
    ChordProgression, ii_v_i, twelve_bar_blues, chromatic,
    EVENT_TYPES, cr_impact,
)
from swarm_tminus.events import (
    CountdownEvent, EventStore, EventStatus, SubscriberStatus,
)
from swarm_tminus.deadlines import (
    DeadlineTree, DeadlineNode, DeadlineStatus, cascade_cancel,
)
from swarm_tminus.rate import TokenBucket, LeakyBucket, RatePair
from swarm_tminus.tempo import (
    TickClock, BPM, Tick, TempoNegotiator,
    bpm_to_seconds, seconds_to_bpm, swing_offset as swing,
)
from swarm_tminus.cron import CronParser, next_fire, CronError
from swarm_tminus.campaign import Campaign, topological_order, CycleError
from swarm_tminus.matcher import EventMatcher, EventMatch, find_matches

__version__ = "0.1.0"
__all__ = [
    "Predictor", "Prediction", "MessageSavings",
    "ChordProgression", "ii_v_i", "twelve_bar_blues", "chromatic",
    "EVENT_TYPES", "cr_impact",
    "CountdownEvent", "EventStore", "EventStatus", "SubscriberStatus",
    "DeadlineTree", "DeadlineNode", "DeadlineStatus", "cascade_cancel",
    "TokenBucket", "LeakyBucket", "RatePair",
    "TickClock", "BPM", "Tick", "TempoNegotiator",
    "bpm_to_seconds", "seconds_to_bpm", "swing",
    "CronParser", "next_fire", "CronError",
    "Campaign", "topological_order", "CycleError",
    "EventMatcher", "EventMatch", "find_matches",
]