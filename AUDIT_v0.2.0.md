# swarm-tminus v0.2.0 — Code Audit Report

**Auditor:** MiniMax-M3 (self-audit, post–subagent failure recovery)  
**Date:** 2026-07-16 (initial audit); updated 2026-07-16 after upstream verification  
**Scope:** All 11 modules + 11 test files (6,215 LoC, 294 tests at audit time, 301 tests after follow-up fixes)  
**Repo state at audit start:** commit `201fc62` on `main`  
**Resolutions:**
- BUG #1, #2, #3, #4, #5 fixed in **v0.2.1** (commit `756853a`)
- BUG #6 reclassified as DOC, documented in **v0.2.2** (commit `e4c4e13`)
- BUG #7 fixed in **v0.2.2** (commit `e4c4e13`)

---

## TL;DR

- **294/294 tests pass** ✓ (in 0.038s)
- **stdlib-only, zero runtime deps** ✓
- **Public API surface is small, focused, well-typed** ✓
- **10 of 11 modules are solid** ✓
- **1 module has real correctness bugs** that pass tests but break in practice: **`context.py`** (room-tagnesting over-close, source-field loss on truncation)
- **1 module has a real correctness bug** not covered by tests: **`deadlines.py`** (index stale after `add_child`)
- **Documentation has 3 inconsistencies** with implementation
- **No critical security issues** (no eval, no exec, no shell, no path traversal, all file writes use `.tmp` + rename)

**Verdict:** Worth shipping to PyPI as `swarm-tminus` v0.2.0 **after fixing the 3 bugs and 3 doc inconsistencies**. The bugs are localized and the fixes are small (under ~10 LoC each).

---

## Bugs found

### BUG #1 — `context.py`: room tags over-closed (HIGH)

**Severity:** High (produces malformed XML; semantic breakage)  
**Module:** `swarm_tminus/context.py`, function `format_tiles_as_context` (lines ~110-145)  
**Same bug duplicated in:** `lines_rebuild()` (lines ~158-187)

**Issue:** When iterating over tiles and encountering a new domain, the code closes ALL previously-opened room tags instead of just the most recent one:

```python
for prev in current_domains:        # ← closes ALL prior rooms
    lines.append(f"  </room>")
lines.append(f"  <room>{tile.domain}</room>")
current_domains.append(tile.domain)
```

This produces malformed XML for any input with 2+ distinct domains:

```xml
<fleet-context>
  <room>alpha</room>
  </room>           ← closes alpha which was just opened
  <room>beta</room>
  </room>           ← invalid (alpha already closed)
  </room>           ← invalid
  <room>gamma</room>
  </room>           ← invalid
  </room>           ← invalid
  </room>           ← invalid
</fleet-context>
```

Empirically verified: 3 tile domains → 3 open `<room>` tags but 9 close `</room>` tags.

**Test gap:** `test_07_groups_by_domain` only checks that `<room>A</room>` and `<room>B</room>` strings appear. It doesn't verify the tags are balanced or in the right positions.

**Fix:** Track only the last opened room and close it before opening the next:

```python
if tile.domain and tile.domain not in current_domains:
    if current_domains:
        lines.append(f"  </room>")  # close only the most recent
    lines.append(f"  <room>{_xml_escape(tile.domain)}</room>")
    current_domains.append(tile.domain)
```

Same fix applies to `lines_rebuild`.

---

### BUG #2 — `context.py`: `source` field lost on truncation (MEDIUM)

**Severity:** Medium (silent data loss)  
**Module:** `swarm_tminus/context.py`, function `lines_rebuild` (lines ~158-187)

**Issue:** When tiles are truncated to fit `max_chars`, the `source` field is dropped from metadata. The initial loop includes source:

```python
if include_metadata:
    meta_parts = [f"confidence: {tile.confidence:.2f}"]
    if tile.tags:
        meta_parts.append(f"tags: {', '.join(tile.tags)}")
    if tile.source:                              # ← source included
        meta_parts.append(f"source: {tile.source}")
    tile_lines.append(f"  {' | '.join(meta_parts)}")
```

But `lines_rebuild` omits it:

```python
if include_metadata:
    meta_parts = [f"confidence: {tile.confidence:.2f}"]
    if tile.tags:
        meta_parts.append(f"tags: {', '.join(tile.tags)}")
    # ← source NOT included
    lines.append(f"    {' | '.join(meta_parts)}")
```

This means when truncation occurs, users lose traceability on the kept tiles — exactly the tiles they cared most about (highest confidence).

**Fix:** Add the source line to `lines_rebuild` matching the initial loop. Extract a shared helper.

---

### BUG #3 — `context.py`: metadata line has 6-space indent vs 4-space elsewhere (LOW)

**Severity:** Low (cosmetic)  
**Module:** `swarm_tminus/context.py`

**Issue:** The metadata line in `format_tiles_as_context` is built with 2-space prefix, then has 4 spaces prepended on output → 6 total spaces. Q and A lines have no prefix → 4 total spaces. Output is:

```
  <tile>
    Q: Q1
    A: A1
      confidence: 0.90 | source: src1   ← 6 spaces
  </tile>
```

`lines_rebuild` uses 4-space indent directly → no inconsistency in the rebuild path.

**Fix:** Drop the leading 2 spaces from the meta line in the initial loop.

---

### BUG #4 — `deadlines.py`: `_index` stale after `add_child` (HIGH)

**Severity:** High (silent failure; cancellation of post-construction children is broken)  
**Module:** `swarm_tminus/deadlines.py`, method `DeadlineNode.add_child` (lines ~57-66) + `DeadlineTree.cancel` (lines ~167-171)

**Issue:** `DeadlineTree._index` is built in `__post_init__` and `_reindex`, but `DeadlineNode.add_child` does NOT call `_reindex` on its parent tree. After:

```python
root = DeadlineNode(name="root", duration_seconds=10)
tree = DeadlineTree(root=root)
root.add_child("late_child", duration_seconds=5)
tree.find("late_child")  # → None  (should be the child node)
tree.cancel("late_child")  # → 0     (returns 0, doesn't cancel)
```

Empirically verified — `find` returns `None` and `cancel` returns 0 for any node added after `DeadlineTree.__init__`.

**Root cause:** The index is owned by `DeadlineTree` but mutating children happens on `DeadlineNode`, which has no reference to the tree.

**Test gap:** Tests always build the complete tree before instantiating `DeadlineTree`.

**Fix options (pick one):**
1. (Lightweight) Have `DeadlineTree` re-index on every `find`/`cancel` call (lazy reindex). Cost: O(n) per lookup; OK for typical tree sizes.
2. (Proactive) Walk parent chain on `add_child` and notify any associated tree. Cost: extra storage on each node.
3. (API change) Make `DeadlineTree.add_child(name, dur)` the only way to add children; deprecate `DeadlineNode.add_child` from outside the tree. Cost: API breakage.

**Recommended:** Option 1 — lazy `_reindex()` in `find` and `cancel` (single-line guard). Keeps backward compat.

---

### BUG #5 — `campaign.py`: `add_edge` accepts cyclic edges (MEDIUM, doc/code mismatch)

**Severity:** Medium (docs lie; user surprised)  
**Module:** `swarm_tminus/campaign.py`, method `Campaign.add_edge` (lines ~70-78)

**Issue:** README states:
> 6. **Cycle detection is atomic on `add_edge`**: the edge is added tentatively, validated via topological sort, and reverted if a cycle would form.

But the actual `add_edge` does NOT check for cycles — only `add_edge_force` does. Empirically verified: `camp.add_edge("c", "a")` on a → b → c succeeds without error, leaving a cyclic graph.

**Fix:** Either (a) add cycle detection to `add_edge` to match the docs (preferred), or (b) fix the README to say only `add_edge_force` checks cycles. The test `test_cycle_check_invalid` even has a comment: "Manually add a cycle edge to bypass the cycle check on add" — the test author knew.

---

### BUG #6 — `cron.py`: AND semantics for DOM+DOW instead of OR (RESOLVED 2026-07-16)

**Severity:** Medium (deviation from Vixie cron standard; users surprised)  
**Module:** `swarm_tminus/cron.py`, method `CronParser.matches` (lines ~244-254)

**Issue:** Standard Vixie cron uses OR semantics when both DOM and DOW are restricted:

> Note: The day of a command's execution can be specified in the following two fields — 'day of month', and 'day of week'. If both fields are restricted (ie, are not *), the command will be run when **either** field matches the current time.

This implementation uses AND semantics (all fields must match). Empirically verified: `0 12 1 * 1` (noon on day-1 OR Monday) matches nothing.

**Resolution 2026-07-16:** Verified against upstream `t-minus-rs/src/schedule.rs:165-178` — the Rust source ALSO uses AND semantics. The Python port faithfully reproduces the upstream behavior. The standard Vixie cron OR semantics is a convention, not a requirement.

**Final status:** Reclassified as **DOC #4** (documentation gap, not a bug). Module docstring updated in **v0.2.2** to explicitly document AND semantics and link to the upstream source. No code change.

---

### BUG #7 — `events.py`: deferred blocks FIRE even when quorum met (RESOLVED 2026-07-16)

**Severity:** Medium (real porting bug — order of checks contradicts upstream)  
**Module:** `swarm_tminus/events.py`, method `CountdownEvent.tick` (lines ~95-117)

**Issue:** Current code:

```python
if self.deferred_count() > 0:
    self.status = EventStatus.COUNTING   # ← even if quorum met
    return self.status
if self.has_quorum():
    self.status = EventStatus.FIRED
    return self.status
```

Means: if any subscriber is DEFERRED, the event never FIRES regardless of quorum. This contradicts the upstream semantics.

**Verification 2026-07-16:** Read `t-minus/src/engine.rs:188-208` directly. The Rust engine checks `has_quorum()` FIRST:

```rust
if now >= event.fire_time() {
    if event.has_quorum() {
        tick.fired.push(event.id);       // ← quorum fires regardless of deferred
    } else {
        // Only when quorum is NOT met does deferred grant grace
        let any_deferred = ...;
        if !any_deferred {
            tick.missed.push(event.id);
        }
    }
}
```

The Python port had the order wrong — it checked deferred first, blocking fire even when quorum was reached. **This was a real port bug.**

**Fix applied in v0.2.2:** Reordered check to match upstream. New regression test `test_quorum_fires_even_with_deferred` covers the case.

---

## Documentation inconsistencies

### DOC #1 — README claims 230 tests; actually 294

The badge at top of README says "230 passing". The actual test count is 294. The "Test breakdown" table is correct (sums to 294). Badge is stale.

**Fix:** Update badge to `[![Tests](https://img.shields.io/badge/tests-294%20passing-brightgreen)](#tests)`.

### DOC #2 — `pyproject.toml` version is 0.1.0; package is 0.2.0

The `__version__` in `swarm_tminus/__init__.py` is `"0.2.0"`, but `pyproject.toml` line 8 has `version = "0.1.0"`. This means `pip install swarm-tminus` would show the package as v0.1.0 even though it's v0.2.0. Must fix before PyPI publish.

**Fix:** `version = "0.2.0"` in `pyproject.toml`.

### DOC #3 — README swarm-anchor integration example uses non-existent API

```python
from swarm_anchor import Heartbeat
clock = TickClock(bpm=120.0)
hb = Heartbeat(agent_id="alice")        # ← wrong: no agent_id kwarg
while True:
    tick = clock.next_tick()
    hb.update()                          # ← wrong: method is .touch()
    hb.save(".swarm")                    # ← wrong: no .save() method
    time.sleep(tick.delta)
```

The actual swarm-anchor API is `Anchor(root=".swarm").heartbeat(Heartbeat(animal=...))`. Example would fail if copy-pasted.

**Fix:** Replace with a working example using the real swarm-anchor API.

---

## Minor / stylistic

- **`events.py` `_sanitize_filename`**: sanitizes `\` and `/` to `_`, but doesn't prevent empty filenames. Edge case: an event named `///` would produce `___.event.json`. Cosmetic.
- **`rate.py` `RatePair.try_send`**: comment is misleading — says "nudge last_refill" but the code directly modifies `tokens`. The behavior is correct (rollback works), just the comment is wrong.
- **`hybrid.py` `_LocalHeartbeat.touch`**: uses `time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())` (no timezone, seconds resolution), while real `swarm_anchor.Heartbeat.touch` uses `datetime.now(timezone.utc).isoformat()` (with timezone, microsecond resolution). Both parse via `datetime.fromisoformat()` so functionally compatible, but JSON files written by the degraded path look subtly different. Minor consistency issue.
- **`hybrid.py` `add_event`**: writes file directly via `_write_event_file`, then also adds to `self.events`. If user calls `add_event` then `events.save()`, the file is rewritten identically. Not a bug, just wasted IO.
- **`predictor.py` `confirm`**: returns False for both "already confirmed" and "not found". User can't distinguish. Minor API ergonomics.
- **`tempo.py` `TempoNegotiator.negotiated`**: docstring says "nearest the median" but implementation picks "median of compatible candidates". Semantically reasonable, but the docstring under-describes.

---

## What's solid

These modules passed without finding bugs:

- ✅ **predictor.py** — clean, well-tested, validation is comprehensive
- ✅ **rate.py** — TokenBucket + LeakyBucket + RatePair all behave correctly under stress. Rollback in `try_send` works.
- ✅ **tempo.py** — TickClock + BPM + TempoNegotiator all correct
- ✅ **matcher.py** — pattern matching with operators is well-typed and covered
- ✅ **casting.py** — score-based router works, avoid-words filter correctly excludes expensive/slow, min_strength filter works
- ✅ **hybrid.py** — graceful degradation when swarm-anchor absent, clean import handling
- ✅ **cron.py** — step/range/list parsing all correct (modulo BUG #6 above)

---

## Recommended fix order before PyPI publish

1. Fix BUG #1 (context room tags) — ~3 lines
2. Fix BUG #2 (context source loss) — ~1 line
3. Fix BUG #4 (deadline index stale) — ~5 lines (lazy reindex)
4. Fix BUG #5 (campaign cycle detection) — ~5 lines, or update README
5. Fix DOC #2 (pyproject.toml version) — 1 line
6. Fix DOC #1 (README test badge) — 1 line
7. Add regression tests for BUG #1, BUG #2, BUG #4, BUG #6
8. Decide on BUG #7 (deferred semantics) — verify with original
9. Fix BUG #6 (cron OR semantics) or document AND semantics
10. Fix DOC #3 (swarm-anchor example) — full rewrite of one example block

Total fix estimate: ~30 LoC + ~50 LoC tests = ship-ready.

---

## Beta-test lessons (what to remember)

1. **Subagent provider cooldowns DO kill parallel work** — all 4 subagents failed simultaneously with rate limit/cooldown. Falling back to direct execution in main session is the right move when subagents die.

2. **Read-then-test in main session works** — 6,215 LoC read in 4 batches + 6 empirical reproducer scripts + 1 test run = ~15 minutes. That's the floor for solo audit.

3. **Tests passing ≠ correct** — 294 tests pass but at least 4 real bugs ship. Coverage drives confidence but not correctness. The room-tagnesting bug and index-stale bug would not be caught by any reasonable coverage metric (specific edge cases).

4. **README claims must be verified** — three doc inconsistencies found (test count badge, pyproject version, swarm-anchor example). Always check pyproject.toml matches `__version__`. Always check README code examples are syntactically valid.

---

*End of audit.*