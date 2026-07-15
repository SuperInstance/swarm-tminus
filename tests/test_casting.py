"""Tests for swarm_tminus.casting — casting-call model router."""

import unittest

from swarm_tminus.casting import (
    CASTING_MAP,
    STRENGTH_SYNONYMS,
    MAX_POSSIBLE_STRENGTHS,
    CastingRequest,
    CastingResult,
    effective_strengths,
    select_model,
    strength_score,
)


class TestCastingMap(unittest.TestCase):
    """The routing table itself — shape, completeness, and inferred values."""

    def test_map_has_exactly_ten_entries(self):
        """CASTING_MAP must contain exactly the 10 task entries from DOCS §3.4."""
        self.assertEqual(len(CASTING_MAP), 10)

    def test_entries_have_required_fields(self):
        """Every entry must have cost, latency, strengths, languages (and more)."""
        required = {
            "task", "provider", "model", "confidence",
            "rationale", "cost", "latency", "strengths", "languages",
        }
        for task_name, entry in CASTING_MAP.items():
            with self.subTest(task=task_name):
                missing = required - set(entry.keys())
                self.assertFalse(
                    missing,
                    f"entry {task_name!r} missing fields: {sorted(missing)}",
                )

    def test_cost_and_latency_in_range(self):
        """cost in [0, 1] and latency > 0 — sanity-check inferred values."""
        for task_name, entry in CASTING_MAP.items():
            with self.subTest(task=task_name):
                self.assertGreaterEqual(entry["cost"], 0.0)
                self.assertLessEqual(entry["cost"], 1.0)
                self.assertGreater(entry["latency"], 0.0)

    def test_strengths_is_nonempty_list(self):
        for task_name, entry in CASTING_MAP.items():
            with self.subTest(task=task_name):
                self.assertIsInstance(entry["strengths"], list)
                self.assertGreater(len(entry["strengths"]), 0)

    def test_languages_is_nonempty_list(self):
        for task_name, entry in CASTING_MAP.items():
            with self.subTest(task=task_name):
                self.assertIsInstance(entry["languages"], list)
                self.assertGreater(len(entry["languages"]), 0)


class TestEffectiveStrengths(unittest.TestCase):
    """Synonym expansion for matching task/prefer words to entries."""

    def test_synonyms_expand_for_known_categories(self):
        """An entry with 'code' in strengths should expand to all code synonyms."""
        code_entry = CASTING_MAP["code_review"]
        eff = effective_strengths(code_entry)
        # All STRENGTH_SYNONYMS['code'] entries should be present.
        for syn in STRENGTH_SYNONYMS["code"]:
            self.assertIn(syn, eff)

    def test_unknown_strength_does_not_explode(self):
        """A strength word that matches no category is still in the set."""
        # Pick an entry and ensure no exception when expanding.
        for entry in CASTING_MAP.values():
            effective_strengths(entry)  # should not raise

    def test_strength_score_in_unit_range(self):
        """strength_score returns a value in [0, 1] for every entry."""
        for task_name, entry in CASTING_MAP.items():
            with self.subTest(task=task_name):
                score = strength_score(entry)
                self.assertGreaterEqual(score, 0.0)
                self.assertLessEqual(score, 1.0)


class TestSelectModelDefaults(unittest.TestCase):
    """Behavior of select_model with default and edge-case inputs."""

    def test_empty_prefer_returns_top_scoring_overall(self):
        """No preferences → returns SOME entry (not None). Score is a float."""
        result = select_model(CastingRequest(task="anything goes"))
        self.assertIsNotNone(result)
        self.assertIsInstance(result, CastingResult)
        self.assertIsInstance(result.score, float)

    def test_defaults_work_for_casting_request(self):
        """CastingRequest with only `task` works; other fields have sensible defaults."""
        req = CastingRequest(task="hello")
        self.assertEqual(req.task, "hello")
        self.assertEqual(req.prefer, [])
        self.assertEqual(req.avoid, [])
        self.assertEqual(req.min_strength, 0.5)

    def test_result_is_hashable(self):
        """CastingResult is frozen=True → hashable, equal instances dedupe."""
        r1 = CastingResult(model="m", score=0.5, reasoning="x", cost=0.3, latency=1.0)
        r2 = CastingResult(model="m", score=0.5, reasoning="x", cost=0.3, latency=1.0)
        bucket = {r1, r2}
        self.assertEqual(len(bucket), 1)
        # Confirm it can be used as a dict key too.
        _ = {r1: "first"}


class TestSelectModelPrefer(unittest.TestCase):
    """prefer= routes to models that excel at the named strength."""

    def test_prefer_cheap_returns_cheap_model(self):
        """prefer=['cheap'] should return a model with cost <= 0.3."""
        result = select_model(CastingRequest(task="x", prefer=["cheap"]))
        self.assertIsNotNone(result)
        self.assertLessEqual(result.cost, 0.3)

    def test_prefer_creative_returns_creative_model(self):
        """prefer=['creative'] should return a model that has 'creative' in strengths."""
        result = select_model(CastingRequest(task="x", prefer=["creative"]))
        self.assertIsNotNone(result)
        # Find entries that have 'creative' in their strengths
        creative_models = {
            e["model"] for e in CASTING_MAP.values() if "creative" in e["strengths"]
        }
        self.assertIn(result.model, creative_models)

    def test_prefer_code_returns_code_model(self):
        """prefer=['code'] should return a model that has 'code' in strengths."""
        result = select_model(CastingRequest(task="x", prefer=["code"]))
        self.assertIsNotNone(result)
        code_models = {
            e["model"] for e in CASTING_MAP.values() if "code" in e["strengths"]
        }
        self.assertIn(result.model, code_models)

    def test_prefer_fast_returns_low_latency_model(self):
        """prefer=['fast'] should return a low-latency model (latency < 3.0s)."""
        result = select_model(CastingRequest(task="x", prefer=["fast"]))
        self.assertIsNotNone(result)
        self.assertLess(result.latency, 3.0)


class TestSelectModelAvoid(unittest.TestCase):
    """avoid= filters out models that match the avoid rule."""

    def test_avoid_expensive_excludes_high_cost(self):
        """avoid=['expensive'] should exclude cost >= 0.7 → no result has that cost."""
        result = select_model(CastingRequest(task="x", avoid=["expensive"]))
        self.assertIsNotNone(result)
        self.assertLess(result.cost, 0.7)

    def test_avoid_slow_excludes_high_latency(self):
        """avoid=['slow'] should exclude latency >= 5.0 → no result has that latency."""
        result = select_model(CastingRequest(task="x", avoid=["slow"]))
        self.assertIsNotNone(result)
        self.assertLess(result.latency, 5.0)


class TestSelectModelMinStrength(unittest.TestCase):
    """min_strength filters out models with insufficient strength coverage."""

    def test_min_strength_high_filters_out_low_strength_models(self):
        """With min_strength=0.95, the selected model must have >= 0.95 coverage."""
        result = select_model(CastingRequest(task="anything", min_strength=0.95))
        self.assertIsNotNone(result)
        # Find entries matching the selected model and confirm coverage.
        matching_entries = [
            e for e in CASTING_MAP.values() if e["model"] == result.model
        ]
        self.assertTrue(matching_entries, "no entry matches the selected model")
        self.assertTrue(
            any(
                len(e["strengths"]) / MAX_POSSIBLE_STRENGTHS >= 0.95
                for e in matching_entries
            ),
            f"no matching entry has strength_score >= 0.95; "
            f"selected model={result.model}",
        )

    def test_returns_none_when_no_model_passes_min_strength(self):
        """Impossibly-high min_strength → no entry passes → returns None."""
        result = select_model(CastingRequest(task="anything", min_strength=1.5))
        self.assertIsNone(result)


class TestSelectModelScoring(unittest.TestCase):
    """Score composition: task words vs keywords (prefer), reasoning output."""

    def test_task_word_match_scores_higher_than_keyword_match(self):
        """A strength word in the task text should outscore the same word in prefer."""
        r_task = select_model(CastingRequest(task="creative writing"))
        r_keyword = select_model(
            CastingRequest(task="something else entirely", prefer=["creative"]),
        )
        # Sanity: both should pick a model (could be the same or different)
        self.assertIsNotNone(r_task)
        self.assertIsNotNone(r_keyword)
        # The core claim: a task-word match contributes more than a keyword match.
        self.assertGreater(r_task.score, r_keyword.score)

    def test_reasoning_explains_choice(self):
        """The reasoning string should be non-empty and mention the model name."""
        result = select_model(CastingRequest(task="creative writing"))
        self.assertIsNotNone(result)
        self.assertIsInstance(result.reasoning, str)
        self.assertGreater(len(result.reasoning), 0)
        self.assertIn(result.model, result.reasoning)
        # The reasoning should reference some scoring signal — at minimum a number.
        self.assertTrue(
            any(ch.isdigit() for ch in result.reasoning),
            "reasoning should contain numeric scoring details",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()