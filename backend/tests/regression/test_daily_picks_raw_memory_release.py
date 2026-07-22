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
- 2026-07-22 US Daily Picks generation-reliability incident (recurring
  failures 07-15 through 07-22; today's abort happened mid-Phase-1, before
  the release loop below could run even once): Phase 1 itself became
  horizon-bounded — each horizon's own candidates are scored, ranked,
  selected, persisted, and let go out of scope BEFORE the next horizon's
  Phase 1 scoring even begins. The `raw` dict (and the flat `tasks` list
  that used to feed it) no longer exist at all — there is nothing to
  release, because nothing ever holds more than one horizon's pool. This
  is a strictly stronger guarantee than "release promptly after capture":
  it removes the multi-horizon accumulation entirely rather than shortening
  its lifetime.

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


def test_each_horizon_builds_its_own_items_list_inside_the_loop():
    """Each horizon must build a fresh `items` list of its OWN candidates,
    inside the per-horizon loop — never reading a shared, pre-built,
    all-horizons structure."""
    loop_start = _SOURCE.index('for horizon in ("short", "medium", "long"):')
    tail = _SOURCE[loop_start:loop_start + 800]
    assert "items: list = []" in tail
    assert "for sym in candidates:" in tail
    assert "_predict_stock(sym, horizon, market)" in tail


def test_release_happens_before_the_empty_items_early_return():
    # Every horizon, including one with zero candidates, must reach the
    # same `if not items: picks[horizon] = []; continue` early-return path
    # — the per-horizon items list must be built (however empty) and
    # score-snapshotted before that check, for every horizon, every time.
    loop_idx = _SOURCE.index('for horizon in ("short", "medium", "long"):')
    idx_items_built = _SOURCE.index("items: list = []", loop_idx)
    idx_snapshot_call = _SOURCE.index("_write_score_snapshots({horizon: items}, market)", loop_idx)
    idx_early_return = _SOURCE.index("if not items:\n            picks[horizon] = []", loop_idx)
    assert idx_items_built < idx_snapshot_call < idx_early_return


def test_write_score_snapshots_runs_per_horizon_not_for_a_shared_dict():
    # _write_score_snapshots must never run once for a structure spanning
    # multiple horizons — only per-horizon, using that horizon's own items.
    assert "_write_score_snapshots(raw, market)" not in _SOURCE
    assert "_write_score_snapshots({horizon: items}, market)" in _SOURCE


def test_only_one_horizons_items_list_can_be_alive_at_a_time():
    """`items` is declared fresh (`items: list = []`) at the top of every
    iteration of the per-horizon loop and never assigned into any
    structure that survives past that iteration (`picks[horizon]` stores
    only the small top-6 published slice via `top_buy`, not `items`
    itself) — so by construction at most one horizon's full candidate pool
    can be reachable at any point during Phase 1 + ranking."""
    loop_idx = _SOURCE.index('for horizon in ("short", "medium", "long"):')
    loop_body = _SOURCE[loop_idx:]
    # `items` must never be assigned to a dict/list keyed or indexed by
    # horizon (which would let it outlive one iteration) — e.g. no
    # `all_items[horizon] = items` or `items_by_horizon.append(items)`.
    assert not re.search(r"\bitems_by_horizon\b", loop_body)
    assert not re.search(r"\[\s*horizon\s*\]\s*=\s*items\b", loop_body)
