"""
Product Integrity Workstream #023 — per-horizon candidate pool isolation.

History:
- 2026-07-16 production memory investigation traced a Daily Picks run's
  memory climbing to 98% of an 8GB container limit to `generate_picks()`'s
  `raw` dict: Phase 1 predicted every candidate x horizon (~400 symbols x 3
  horizons for a full US run) and held ALL of them simultaneously.
- 2026-07-21 postmortem: `raw[horizon] = None` released each horizon's slot
  immediately after Phase 3-6 captured it as `items`, and
  `_write_score_snapshots` moved from a single whole-`raw` call to a
  per-horizon call right after that release — cutting the SNAPSHOT-WRITE
  step's peak to one horizon's pool. But `raw` was still built by a single
  Phase 1 pass across ALL THREE horizons before this release loop even
  started, so the true peak (all 3 horizons resident, about to release one
  at a time) was unchanged by that fix.
- 2026-07-22 US Daily Picks generation-reliability incident: Phase 1
  became horizon-major so nothing held more than one horizon's pool.
- 2026-07-23/24 incident: horizon-major ordering was found to have
  TRIPLED SEC companyfacts download/parse churn (each symbol re-visited
  ~400 tasks after its facts left the 25-entry cache) — the slim result
  dicts it economized on are only ~12 KB each (~15 MB for all three
  horizons), never the dominant memory term. Phase 1 is now chunked
  candidate-major: the three horizons' slim pools accumulate in
  `_horizon_items` (the explicit, measured, accepted trade), each popped
  out and released after its own horizon's Phases 3-6 complete. The old
  `raw` dict (rich pools built with no release path) and flat `tasks`
  list must still never return.

`generate_picks()` itself is a large, deeply-integrated function (screener
calls, regime detection, DB writes, Telegram, multiple external services)
that isn't practically mountable end-to-end in a unit test — consistent
with this codebase's existing convention for functions of this shape (see
e.g. Product Integrity #011/#012's structural wiring tests). This file
verifies the fix as a structural/source property instead.
"""

import re
from pathlib import Path

_SOURCE = (Path(__file__).parent.parent.parent / "services" / "daily_picks.py").read_text()


def test_raw_dict_and_flat_tasks_list_no_longer_exist():
    """The multi-horizon `raw` dict and the flat all-horizons `tasks` list
    it used to be built from must not exist anywhere in the file — the
    2026-07-22 fix removes the accumulation entirely rather than releasing
    it promptly."""
    assert 'raw: dict[str, list] = {"short": [], "medium": [], "long": []}' not in _SOURCE
    assert 'tasks = [(sym, h) for sym in candidates for h in' not in _SOURCE
    # No live code reference to a bare `raw[` or `raw.` indexing/attribute
    # access anywhere (comments describing the OLD, now-removed design are
    # fine and expected — e.g. this file's own docstring above, and
    # daily_picks.py's own historical postmortem comments — so only
    # non-comment lines are checked).
    code_only = "\n".join(
        line for line in _SOURCE.splitlines() if not line.strip().startswith("#")
    )
    assert not re.search(r"\braw\[", code_only), (
        "a live `raw[...]` access was found — the multi-horizon raw dict "
        "should no longer exist as a code-level variable at all"
    )


def test_each_horizon_takes_ownership_of_its_own_pool_inside_the_loop():
    """Each horizon's Phases 3-6 iteration must take exclusive ownership of
    its OWN pool via `_horizon_items.pop(horizon)` — removing the
    accumulator's reference so the pool is collectable as soon as this
    iteration's `items` goes out of scope, never leaving a second live
    reference behind in a structure that outlives the iteration."""
    phases_idx = _SOURCE.index("Phases 3-6 per horizon")
    tail = _SOURCE[phases_idx:phases_idx + 2000]
    assert "items: list = _horizon_items.pop(horizon)" in tail


def test_release_happens_before_the_empty_items_early_return():
    # Every horizon, including one with zero candidates, must reach the
    # same `if not items: picks[horizon] = []; continue` early-return path
    # — the per-horizon pool must be taken (however empty) and
    # score-snapshotted before that check, for every horizon, every time.
    loop_idx = _SOURCE.index("Phases 3-6 per horizon")
    idx_items_built = _SOURCE.index("items: list = _horizon_items.pop(horizon)", loop_idx)
    idx_snapshot_call = _SOURCE.index("_write_score_snapshots({horizon: items}, market)", loop_idx)
    idx_early_return = _SOURCE.index("if not items:\n            picks[horizon] = []", loop_idx)
    assert idx_items_built < idx_snapshot_call < idx_early_return


def test_write_score_snapshots_runs_per_horizon_not_for_a_shared_dict():
    # _write_score_snapshots must never run once for a structure spanning
    # multiple horizons — only per-horizon, using that horizon's own items.
    assert "_write_score_snapshots(raw, market)" not in _SOURCE
    assert "_write_score_snapshots({horizon: items}, market)" in _SOURCE


def test_horizon_pool_is_never_reassigned_into_a_surviving_structure():
    """Once a horizon's pool is popped as `items`, it must never be
    assigned back into any structure that survives the iteration
    (`picks[horizon]` stores only the small top-6 published slice via
    `top_buy`, not `items` itself) — so each horizon's full pool is
    collectable as soon as its own Phases 3-6 complete, and the peak
    multi-horizon retention is exactly the accumulator the Phase-1 loop
    deliberately builds, never that plus stragglers."""
    loop_idx = _SOURCE.index("Phases 3-6 per horizon")
    loop_body = _SOURCE[loop_idx:]
    assert not re.search(r"\bitems_by_horizon\b", loop_body)
    assert not re.search(r"\[\s*horizon\s*\]\s*=\s*items\b", loop_body)
    # And nothing may re-add to the accumulator after Phase 1 hands pools over.
    assert "_horizon_items[" not in loop_body
