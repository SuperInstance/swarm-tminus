"""Casting-call model router — Python port of terax-fleet-modules/casting-call.ts.

Score-based router over 10 curated model/task entries sourced from the
terax-fleet-modules MAP. Each request gets the best-fit model based on the
task text, preference words (``cheap``, ``fast``, ``creative``, ``code``,
``accurate``, ``planning``), and avoidance words (``expensive``, ``slow``).

Source: /tmp/superinstance-tminus/terax-fleet-modules-DOCS.md (subagent doc).

Inferred fields
---------------
The original TypeScript ``MAP`` (casting-call.ts:6-15) only stores
``{provider, model, confidence, rationale}`` keyed by task name. The Python
router enriches each entry with ``cost``, ``latency``, ``strengths``, and
``languages`` so the score-based algorithm has signal to work with:

- ``cost`` (0-1, lower=cheaper): inferred per model. ``glm-5.1`` mid-high
  (0.55) — flagship reasoning model. ``glm-4.7`` low (0.25) — fast iteration.
  ``Bytedance/Seed-2.0-mini`` low (0.15) — small creative model.
- ``latency`` (seconds, lower=faster): inferred per model. ``glm-4.7`` ~1.2s,
  ``Seed-2.0-mini`` ~2.0s, ``glm-5.1`` ~4.0s.
- ``strengths`` (list of words the model excels at): inferred per task entry
  so the router has semantic signal. The ``deep_reasoning`` entry is the
  only "generalist" with full category coverage (6/6).
- ``languages`` (model origin/languages): inferred — all three models are
  Chinese-origin and ship ``en``/``zh``.

The original DOCS ``MAP`` task keys are preserved verbatim so callers can
match on task name directly when they know it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum number of distinct strength categories a model could possibly cover.
# Used to normalize ``strength_score`` into [0, 1].
MAX_POSSIBLE_STRENGTHS: int = 6


# ---------------------------------------------------------------------------
# 10 curated entries — populated from terax-fleet-modules-DOCS.md §3.4.
#
# Fields per entry:
#   task:        routing key (matches DOCS MAP keys verbatim)
#   provider:    backend provider (zai | deepinfra) — from DOCS
#   model:       model identifier — from DOCS
#   confidence:  curated confidence score [0, 1] — from DOCS
#   rationale:   human-readable reason for the pairing — from DOCS
#   cost:        [0, 1], lower=cheaper — inferred per model
#   latency:     seconds, lower=faster — inferred per model
#   strengths:   words the model excels at — inferred per task
#   languages:   model languages/origin — inferred
# ---------------------------------------------------------------------------
CASTING_MAP: dict[str, dict] = {
    "code_review": {
        "task": "code_review",
        "provider": "zai",
        "model": "glm-5.1",
        "confidence": 0.89,
        "rationale": "Reasoning model for code review",
        "cost": 0.55,
        "latency": 4.0,
        "strengths": ["code", "review", "function", "refactor"],
        "languages": ["en", "zh"],
    },
    "deep_reasoning": {
        "task": "deep_reasoning",
        "provider": "zai",
        "model": "glm-5.1",
        "confidence": 0.91,
        "rationale": "Best reasoning model",
        "cost": 0.55,
        "latency": 4.0,
        "strengths": [
            "reasoning", "code", "accurate", "planning", "review", "creative",
        ],
        "languages": ["en", "zh"],
    },
    "architecture": {
        "task": "architecture",
        "provider": "zai",
        "model": "glm-5.1",
        "confidence": 0.85,
        "rationale": "Structured design",
        "cost": 0.55,
        "latency": 4.0,
        "strengths": ["planning", "code", "accurate", "structure"],
        "languages": ["en", "zh"],
    },
    "creative": {
        "task": "creative",
        "provider": "deepinfra",
        "model": "Bytedance/Seed-2.0-mini",
        "confidence": 0.82,
        "rationale": "Creative breadth",
        "cost": 0.15,
        "latency": 2.0,
        "strengths": ["creative", "story", "fiction", "essay"],
        "languages": ["en", "zh"],
    },
    "research": {
        "task": "research",
        "provider": "deepinfra",
        "model": "Bytedance/Seed-2.0-mini",
        "confidence": 0.78,
        "rationale": "Divergent thinking",
        "cost": 0.15,
        "latency": 2.0,
        "strengths": ["accurate", "reasoning", "essay"],
        "languages": ["en", "zh"],
    },
    "quick_fix": {
        "task": "quick_fix",
        "provider": "zai",
        "model": "glm-4.7",
        "confidence": 0.75,
        "rationale": "Fast iteration",
        "cost": 0.25,
        "latency": 1.2,
        "strengths": ["fast", "code", "cheap", "iteration"],
        "languages": ["en", "zh"],
    },
    "fast_implementation": {
        "task": "fast_implementation",
        "provider": "zai",
        "model": "glm-4.7",
        "confidence": 0.73,
        "rationale": "Fast implementation",
        "cost": 0.25,
        "latency": 1.2,
        "strengths": ["fast", "code", "cheap", "iteration"],
        "languages": ["en", "zh"],
    },
    "arithmetic": {
        "task": "arithmetic",
        "provider": "deepinfra",
        "model": "Bytedance/Seed-2.0-mini",
        "confidence": 0.89,
        "rationale": "Arithmetic precision",
        "cost": 0.15,
        "latency": 2.0,
        "strengths": ["accurate", "reasoning", "arithmetic"],
        "languages": ["en", "zh"],
    },
    "verification": {
        "task": "verification",
        "provider": "deepinfra",
        "model": "Bytedance/Seed-2.0-mini",
        "confidence": 0.86,
        "rationale": "Pattern verification",
        "cost": 0.15,
        "latency": 2.0,
        "strengths": ["accurate", "reasoning", "verification"],
        "languages": ["en", "zh"],
    },
    "visual_memory": {
        "task": "visual_memory",
        "provider": "zai",
        "model": "glm-5.1",
        "confidence": 0.80,
        "rationale": "Visual reasoning",
        "cost": 0.55,
        "latency": 4.0,
        "strengths": ["accurate", "creative", "planning"],
        "languages": ["en", "zh"],
    },
}


# Strength synonyms — maps a strength category to the words callers might use
# to express it. Used by ``_strength_match_count`` to expand each entry's
# declared strengths into an "effective strengths" set.
STRENGTH_SYNONYMS: dict[str, list[str]] = {
    "creative": [
        "creative", "story", "fiction", "essay", "lyrical", "narrative", "writing",
    ],
    "code": [
        "code", "function", "algorithm", "bug", "refactor", "implement", "review",
    ],
    "cheap": ["cheap", "budget", "small", "affordable"],
    "fast": ["fast", "latency", "low-latency", "realtime", "iteration", "quick"],
    "accurate": [
        "accurate", "factual", "precise", "exact",
        "reasoning", "verification", "arithmetic",
    ],
    "planning": [
        "plan", "structure", "outline", "decompose", "roadmap", "architecture",
    ],
}


# Avoid-word thresholds — when ``avoid`` contains one of these tokens and the
# model crosses the threshold, the model is excluded.
_AVOID_EXPENSIVE_COST_THRESHOLD = 0.7
_AVOID_SLOW_LATENCY_THRESHOLD = 5.0


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CastingRequest:
    """Input to the model router.

    Attributes:
        task: free-text task description. Used for keyword matching against
            entry strengths and for direct task-name lookup when the user's
            text contains an entry's task name.
        prefer: list of strength words to favor (e.g., ``["cheap", "fast"]``).
        avoid: list of attribute words to exclude (``"expensive"``, ``"slow"``).
        min_strength: minimum strength-coverage threshold (0..1) for a model
            to be considered. Models with ``len(strengths)/MAX_POSSIBLE_STRENGTHS``
            below this threshold are filtered out before scoring.
    """
    task: str
    prefer: list[str] = field(default_factory=list)
    avoid: list[str] = field(default_factory=list)
    min_strength: float = 0.5


@dataclass(frozen=True)
class CastingResult:
    """Output of the model router.

    Attributes:
        model: selected model identifier (e.g., ``"glm-5.1"``,
            ``"Bytedance/Seed-2.0-mini"``).
        score: composite routing score for the selected model (higher = better
            fit). Used for ranking and introspection; not a model quality metric.
        reasoning: human-readable string explaining why this model was selected.
        cost: cost metric [0, 1] copied from the selected entry.
        latency: latency in seconds copied from the selected entry.
    """
    model: str
    score: float
    reasoning: str
    cost: float
    latency: float


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def effective_strengths(entry: dict) -> set[str]:
    """Return the entry's strengths plus all synonyms for those categories.

    For example, an entry with ``strengths=["code", "review"]`` returns a set
    containing both words plus all ``STRENGTH_SYNONYMS["code"]`` entries
    (``function``, ``algorithm``, ``bug``, ``refactor``, ``implement``).
    """
    eff: set[str] = set()
    for s in entry["strengths"]:
        s_lower = s.lower()
        eff.add(s_lower)
        for cat, syns in STRENGTH_SYNONYMS.items():
            if s_lower == cat or s_lower in syns:
                eff.add(cat)
                eff.update(syns)
                break
    return eff


def strength_score(entry: dict) -> float:
    """Return the entry's strength coverage as a ratio in [0, 1].

    Defined as ``len(strengths) / MAX_POSSIBLE_STRENGTHS``. Used by
    ``select_model`` to filter against ``CastingRequest.min_strength``.
    """
    return len(entry["strengths"]) / MAX_POSSIBLE_STRENGTHS


# ---------------------------------------------------------------------------
# Internal scoring helpers
# ---------------------------------------------------------------------------

def _strength_match_count(entry: dict, words: list[str]) -> int:
    """Count how many of ``words`` appear in the entry's effective strengths."""
    eff = effective_strengths(entry)
    return sum(1 for w in words if w.lower() in eff)


def _task_name_match_score(entry_task: str, user_task: str) -> float:
    """Fraction of ``entry_task`` words that appear in ``user_task`` (0..1).

    Splits the entry's task name on underscores, lower-cases both sides, and
    returns the overlap ratio. Empty entry_task yields 0.0.
    """
    entry_words = entry_task.replace("_", " ").lower().split()
    if not entry_words:
        return 0.0
    user_words = set(user_task.lower().split())
    matches = sum(1 for w in entry_words if w in user_words)
    return matches / len(entry_words)


def _is_excluded_by_avoid(entry: dict, avoid: list[str]) -> bool:
    """Return True if ``entry`` should be excluded due to any ``avoid`` word."""
    for a in avoid:
        a_lower = a.lower()
        if a_lower == "expensive" and entry["cost"] >= _AVOID_EXPENSIVE_COST_THRESHOLD:
            return True
        if a_lower == "costly" and entry["cost"] >= _AVOID_EXPENSIVE_COST_THRESHOLD:
            return True
        if a_lower == "slow" and entry["latency"] >= _AVOID_SLOW_LATENCY_THRESHOLD:
            return True
        if a_lower == "high-latency" and entry["latency"] >= _AVOID_SLOW_LATENCY_THRESHOLD:
            return True
    return False


def _score_entry(entry: dict, req: CastingRequest) -> float:
    """Compute the composite routing score for an entry.

    Components (additive):
        + confidence * 0.2                  base quality
        + task_name_match * 0.5             strong: entry name in user task
        + task_strength_match * 0.3         per-word match in task text
        + prefer_strength_match * 0.2       per-word match in prefer list
        - cost * 0.15                       cheaper is better
        - latency * 0.02                    faster is better
    """
    task_words = req.task.lower().split()

    score = entry["confidence"] * 0.2
    score += _task_name_match_score(entry["task"], req.task) * 0.5
    score += _strength_match_count(entry, task_words) * 0.3
    score += _strength_match_count(entry, req.prefer) * 0.2
    score -= entry["cost"] * 0.15
    score -= entry["latency"] * 0.02
    return score


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def select_model(req: CastingRequest) -> Optional[CastingResult]:
    """Score each entry in ``CASTING_MAP`` and return the best match.

    Filters entries by:
        * ``req.min_strength`` — drops entries whose ``strength_score`` is
          below the threshold.
        * ``req.avoid`` — drops entries that match an avoid rule
          (e.g., ``avoid=["expensive"]`` excludes ``cost >= 0.7``).

    Returns ``None`` if no entry passes both filters, or if ``CASTING_MAP``
    is empty.

    The returned ``CastingResult`` includes a ``reasoning`` string with the
    model name, the entry task name, the confidence, scoring components, the
    cost/latency of the chosen entry, and the original DOCS rationale.
    """
    if not CASTING_MAP:
        return None

    scored: list[tuple[float, str, dict]] = []
    for task_name, entry in CASTING_MAP.items():
        if strength_score(entry) < req.min_strength:
            continue
        if _is_excluded_by_avoid(entry, req.avoid):
            continue
        score = _score_entry(entry, req)
        scored.append((score, task_name, entry))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_task, best_entry = scored[0]

    task_words = req.task.lower().split()
    reasoning = (
        f"Selected {best_entry['model']} (via {best_task!r} entry): "
        f"confidence={best_entry['confidence']:.2f}, "
        f"task_name_match={_task_name_match_score(best_entry['task'], req.task):.2f}, "
        f"strength_matches(task)={_strength_match_count(best_entry, task_words)}, "
        f"strength_matches(prefer)={_strength_match_count(best_entry, req.prefer)}, "
        f"cost={best_entry['cost']:.2f}, "
        f"latency={best_entry['latency']:.1f}s. "
        f"Rationale: {best_entry['rationale']}"
    )

    return CastingResult(
        model=best_entry["model"],
        score=best_score,
        reasoning=reasoning,
        cost=best_entry["cost"],
        latency=best_entry["latency"],
    )


__all__ = [
    "CASTING_MAP",
    "STRENGTH_SYNONYMS",
    "MAX_POSSIBLE_STRENGTHS",
    "CastingRequest",
    "CastingResult",
    "select_model",
    "effective_strengths",
    "strength_score",
]