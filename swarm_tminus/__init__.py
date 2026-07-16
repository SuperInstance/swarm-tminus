"""swarm-tminus: tminus capabilities for swarm-anchor.

A unified Python package that adds the time-shaped coordination
primitives developed across the SuperInstance T-Minus ecosystem
(t-minus, t-minus-rs, tminus-music, lau-tminus, tick-engine,
terax-fleet-modules) to the file-based shared-state model of
swarm-anchor.

Predict-and-confirm, deadline cascades, rate limiters with
backpressure, BPM-adaptive heartbeats, CRON scheduling, and
DAG-ordered campaigns - all in stdlib Python, all living
alongside `.swarm/heartbeat.json` files.

Optional peer dep: `swarm-anchor` (installed separately; adds the
HybridAnchor glue class for unified .swarm/ shared state).

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
        CastingRequest, CastingResult, select_model,
        Tile, format_tiles_as_context, fetch_fleet_context,
    )
    # Optional, only when swarm-anchor is installed:
    from swarm_tminus import HybridAnchor, hybrid_summary
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
from swarm_tminus.casting import (
    CastingRequest, CastingResult, select_model, CASTING_MAP,
    STRENGTH_SYNONYMS, MAX_POSSIBLE_STRENGTHS,
    effective_strengths, strength_score,
)
from swarm_tminus.context import (
    Tile, format_tiles_as_context, fetch_fleet_context,
    save_tiles, load_tiles, PLATO_URL,
)

# Optional peer dep — HybridAnchor only imports if swarm-anchor exists
try:
    from swarm_tminus.hybrid import HybridAnchor, _HAS_SWARM_ANCHOR as _HAS_HYBRID
except ImportError:
    _HAS_HYBRID = False

__version__ = "0.2.1"
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
    "CastingRequest", "CastingResult", "select_model", "CASTING_MAP",
    "STRENGTH_SYNONYMS", "MAX_POSSIBLE_STRENGTHS",
    "effective_strengths", "strength_score",
    "Tile", "format_tiles_as_context", "fetch_fleet_context",
    "save_tiles", "load_tiles", "PLATO_URL",
] + (["HybridAnchor"] if _HAS_HYBRID else [])