"""Predict-and-confirm pattern from tminus-music.

The Predictor is the core of the t-minus philosophy: agents predict future
events, then confirm or miss them when time arrives. This is dramatically more
efficient than polling.

Source: tminus-music/src/lib.rs (lines 60-260).
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Event types (from MusicalEventType)
# ---------------------------------------------------------------------------

EVENT_TYPES: tuple[str, ...] = (
    "chord_change",
    "key_change",
    "tempo_shift",
    "dynamics_change",
    "cadence",
    "modulation",
    "rest",
    "note_resolution",
)

# cr_impact per event type, mirrored from tminus-music
_CR_IMPACT: dict[str, float] = {
    "chord_change": 0.05,
    "key_change": 0.15,
    "tempo_shift": 0.10,
    "dynamics_change": 0.07,
    "cadence": 0.12,
    "modulation": 0.14,
    "rest": 0.02,
    "note_resolution": 0.06,
}


def cr_impact(event_type: str) -> float:
    """Return the cr (causal/relevance) impact of an event type."""
    return _CR_IMPACT.get(event_type, 0.0)


# ---------------------------------------------------------------------------
# Named progressions (from ProgressionDB)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChordProgression:
    """A named chord progression pattern."""
    name: str
    chords: tuple[str, ...]
    beats_per_chord: int
    cr: float
    sigma_above_random: float

    def __repr__(self) -> str:  # pragma: no cover
        return f"ChordProgression({self.name!r}, cr={self.cr})"


def ii_v_i() -> ChordProgression:
    return ChordProgression(
        name="ii-V-I",
        chords=("ii", "V", "I"),
        beats_per_chord=4,
        cr=0.94,
        sigma_above_random=6.5,
    )


def twelve_bar_blues() -> ChordProgression:
    return ChordProgression(
        name="12-Bar Blues",
        chords=(
            "I", "I", "I", "I",
            "IV", "IV", "I", "I",
            "V", "IV", "I", "V",
        ),
        beats_per_chord=4,
        cr=0.87,
        sigma_above_random=5.2,
    )


def random() -> ChordProgression:
    """A low-information baseline progression."""
    return ChordProgression(
        name="Random",
        chords=tuple("?" for _ in range(8)),
        beats_per_chord=4,
        cr=0.31,
        sigma_above_random=0.0,
    )


def chromatic() -> ChordProgression:
    return ChordProgression(
        name="Chromatic",
        chords=("C", "C#", "D", "D#", "E", "F", "F#", "G"),
        beats_per_chord=2,
        cr=0.62,
        sigma_above_random=2.1,
    )


# ---------------------------------------------------------------------------
# Prediction + Predictor
# ---------------------------------------------------------------------------

@dataclass
class Prediction:
    """A single predicted future event."""
    id: str
    event_type: str
    predicted_at_beat: float
    confirmed: bool = False
    confidence: float = 0.0
    cr_impact: float = 0.0

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Prediction({self.event_type!r} id={self.id[:8]} "
            f"beat={self.predicted_at_beat:.2f} conf={self.confidence:.2f})"
        )


@dataclass
class MessageSavings:
    """Quantifies poll-vs-predict savings."""
    predictions_made: int
    confirmations_sent: int
    polling_equivalent: int
    savings_ratio: float

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"MessageSavings(pred={self.predictions_made} "
            f"conf={self.confirmations_sent} ratio={self.savings_ratio:.2%})"
        )


class Predictor:
    """Predict-and-confirm engine driven by beats.

    Time advances by calling `advance(beats)`. Predictions whose
    `predicted_at_beat` falls in the advanced window are returned.

    Attributes:
        bpm: beats per minute
        key: optional musical key (free text)
        current_beat: progress on the beat clock
        events: list of registered predictions
        predictions_made: counter
        confirmations_sent: counter
    """

    def __init__(self, bpm: float = 120.0, key: str = "") -> None:
        if bpm <= 0:
            raise ValueError(f"bpm must be > 0, got {bpm!r}")
        self.bpm: float = float(bpm)
        self.key: str = key
        self.current_beat: float = 0.0
        self.events: list[Prediction] = []
        self.predictions_made: int = 0
        self.confirmations_sent: int = 0
        self.time_signature: tuple[int, int] = (4, 4)

    # ------------------------------------------------------------------
    # Time + scheduling
    # ------------------------------------------------------------------

    def beat_to_seconds(self, beats: float) -> float:
        """Convert a beat count into seconds at the current tempo."""
        return beats * 60.0 / self.bpm

    def seconds_to_beats(self, seconds: float) -> float:
        """Convert seconds into beats at the current tempo."""
        return seconds * self.bpm / 60.0

    def advance(self, beats: float) -> list[Prediction]:
        """Advance the clock by `beats`, return events whose time has come.

        The returned list contains events with
        `old_beat <= predicted_at_beat < new_beat`. Past-due events whose
        `predicted_at_beat` was already passed before advance() are NOT
        re-triggered; only the new window matters.
        """
        if beats < 0:
            raise ValueError(f"beats must be >= 0, got {beats!r}")
        old_beat = self.current_beat
        self.current_beat += beats
        triggered: list[Prediction] = []
        for ev in self.events:
            if old_beat <= ev.predicted_at_beat < self.current_beat:
                triggered.append(ev)
        return triggered

    # ------------------------------------------------------------------
    # Prediction inspection
    # ------------------------------------------------------------------

    def predict_next(self) -> Optional[Prediction]:
        """Return the soonest unconfirmed, future prediction (or None)."""
        future = [e for e in self.events if e.predicted_at_beat > self.current_beat]
        if not future:
            return None
        return min(future, key=lambda e: e.predicted_at_beat)

    def countdown_beats(self, p: Prediction) -> float:
        return p.predicted_at_beat - self.current_beat

    def countdown_seconds(self, p: Prediction) -> float:
        return self.countdown_beats(p) * 60.0 / self.bpm

    # ------------------------------------------------------------------
    # Confirmations
    # ------------------------------------------------------------------

    def confirm(self, prediction_id: str) -> bool:
        """Confirm a prediction by id. Returns False if already confirmed or not found."""
        for e in self.events:
            if e.id == prediction_id:
                if not e.confirmed:
                    e.confirmed = True
                    self.confirmations_sent += 1
                    return True
                return False
        return False

    def add_prediction(
        self,
        event_type: str,
        beats_ahead: float,
        confidence: float,
    ) -> str:
        """Add a new prediction `beats_ahead` in the future.

        Returns the prediction id. Raises ValueError on invalid input.
        """
        if event_type not in _CR_IMPACT:
            raise ValueError(
                f"unknown event_type {event_type!r}; valid: {sorted(_CR_IMPACT)}"
            )
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be in [0,1], got {confidence!r}")
        if beats_ahead < 0:
            raise ValueError(f"beats_ahead must be >= 0, got {beats_ahead!r}")
        new_id = uuid.uuid4().hex
        self.events.append(
            Prediction(
                id=new_id,
                event_type=event_type,
                predicted_at_beat=self.current_beat + beats_ahead,
                confirmed=False,
                confidence=confidence,
                cr_impact=_CR_IMPACT[event_type],
            )
        )
        self.predictions_made += 1
        return new_id

    # ------------------------------------------------------------------
    # Savings
    # ------------------------------------------------------------------

    def message_savings(self) -> MessageSavings:
        """Compute poll-vs-predict efficiency.

        Polling equivalent = predictions_made * 10 (estimated poll count per
        prediction window). savings_ratio = 1 - (predictions + confirmations)
        / polling_equivalent.
        """
        polling_equiv = self.predictions_made * 10
        total = self.predictions_made + self.confirmations_sent
        if polling_equiv > 0:
            ratio = 1.0 - (total / polling_equiv)
        else:
            ratio = 0.0
        return MessageSavings(
            predictions_made=self.predictions_made,
            confirmations_sent=self.confirmations_sent,
            polling_equivalent=polling_equiv,
            savings_ratio=ratio,
        )

    # ------------------------------------------------------------------
    # Now-relative helpers
    # ------------------------------------------------------------------

    def add_prediction_in_seconds(
        self, event_type: str, seconds_ahead: float, confidence: float,
    ) -> str:
        """Convenience: add prediction measured in wall-clock seconds."""
        beats_ahead = self.seconds_to_beats(seconds_ahead)
        return self.add_prediction(event_type, beats_ahead, confidence)

    def add_progression(
        self,
        progression: ChordProgression,
        confidence: float = 0.9,
        beat_offset: float = 0.0,
    ) -> list[str]:
        """Add one prediction per chord in the progression. Returns ids."""
        ids: list[str] = []
        for i, _chord in enumerate(progression.chords):
            beats_ahead = beat_offset + i * progression.beats_per_chord
            new_id = self.add_prediction("chord_change", beats_ahead, confidence)
            ids.append(new_id)
        return ids

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"Predictor(bpm={self.bpm} key={self.key!r} "
            f"beat={self.current_beat} events={len(self.events)})"
        )


__all__ = [
    "EVENT_TYPES",
    "cr_impact",
    "ChordProgression",
    "ii_v_i",
    "twelve_bar_blues",
    "chromatic",
    "random",
    "Prediction",
    "MessageSavings",
    "Predictor",
]