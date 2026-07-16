# Changelog

All notable changes to `swarm-tminus` are documented here. The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.2.2] - 2026-07-16

### Fixed

- **`events.py`**: `CountdownEvent.tick()` now checks `has_quorum()` BEFORE checking deferred attendees, matching the upstream `t-minus/src/engine.rs:188-208` semantics. Previously a deferred attendee would block firing even when quorum was met (BUG #7 from the v0.2.0 audit).
- **`cron.py`**: Module docstring now explicitly documents AND semantics for day-of-month + day-of-week (matches upstream `t-minus-rs/src/schedule.rs`). Standard Vixie cron uses OR semantics; we use AND to match upstream.

### Added

- 1 regression test (`test_quorum_fires_even_with_deferred`).

## [0.2.1] - 2026-07-16

### Fixed

- **`context.py`**: Room tags were over-closed (3 domains produced 9 close tags instead of 3). Refactored to use `<room>DOMAIN</room>` as inline header markers rather than wrapping tile content. Single-source-of-truth via `_build_tiles_xml()` for both initial render and truncation paths.
- **`context.py`**: `source` field was silently dropped from metadata during truncation (BUG #2 from the v0.2.0 audit). Fixed by sharing the builder between initial render and truncation.
- **`context.py`**: Inconsistent indentation in metadata line (6 vs 4 spaces) (BUG #3).
- **`deadlines.py`**: `DeadlineTree.find()` returned `None` and `cancel()` returned 0 for nodes added via `root.add_child()` AFTER `DeadlineTree` construction (BUG #4). Fixed with lazy DFS walk on miss.
- **`campaign.py`**: `add_edge()` now does atomic cycle detection (BUG #5). Previously, `add_edge("c", "a")` on an `a → b → c` graph silently created a cycle, contradicting the README. Now raises `CycleError` and rolls back the edge.

### Docs

- README test badge: 230 → 300 passing (was stale from v0.1.0).
- `pyproject.toml` version: 0.1.0 → 0.2.0 → 0.2.1 (was stale).
- README `swarm-anchor` integration example now uses the real API (`Anchor.heartbeat(Heartbeat(...))` instead of non-existent `hb.update()` / `hb.save()`).

### Added

- `AUDIT_v0.2.0.md` — full code audit report (14KB).
- 6 regression tests (3 context, 2 deadlines, 1 campaign).

## [0.2.0] - 2026-07-15

### Added

- **11th module: `context.py`** — PLATO tile context formatter + fleet fetcher (stdlib urllib.request). `format_tiles_as_context()`, `fetch_fleet_context()`, `Tile`, `save_tiles()`, `load_tiles()`. Mirrors the upstream terax-fleet-modules/src/context-fetcher.ts with JavaScript `Promise.allSettled` semantics for partial failure handling.
- **10th module: `casting.py`** — casting-call model router (port of terax-fleet-modules/casting-call.ts). 10 curated model entries with score-based routing over confidence + cost + latency + strength matching. `CastingRequest`, `CastingResult`, `select_model()`, `CASTING_MAP`, `STRENGTH_SYNONYMS`, `effective_strengths()`, `strength_score()`.
- **`HybridAnchor`** in `hybrid.py` — glue class combining `swarm-anchor` + `swarm-tminus` under one `.swarm/` directory. Optional peer dependency on `swarm-anchor`; degrades gracefully when not installed. `heartbeat()`, `add_event()`, `deadline_tree()`, `campaign()`, `summary()`, `reap()`, `roster()`.

### Changed

- 64 new tests added across the 3 new modules and module integration. Test count: 230 → 294.
- README rewritten to document v0.2.0 surface (11 modules, HybridAnchor, integration patterns).

## [0.1.0] - 2026-07-15

### Added

- Initial release.
- 8 modules: `predictor`, `events`, `deadlines`, `rate`, `tempo`, `cron`, `campaign`, `matcher`.
- 230 tests across the 8 modules.
- Unified CLI: `swarm-tminus <module> <action> [args]`.
- File-based shared state in `.swarm/` directory (drop-in compatible with `swarm-anchor`).
- Source provenance: clean-room Python re-implementation of primitives from `t-minus`, `t-minus-rs`, `tminus-music`, `lau-tminus`, `tick-engine`.