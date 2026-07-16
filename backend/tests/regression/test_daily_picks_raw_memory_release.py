"""
Product Integrity Workstream #023 — release `raw`'s per-horizon reference
after each horizon finishes ranking/selection.

A 2026-07-16 production memory investigation traced a Daily Picks run's
memory climbing to 98% of an 8GB container limit to `generate_picks()`'s
`raw` dict: Phase 1 predicts every candidate × horizon (~400 symbols × 3
horizons for a full US run) and holds ALL of them — full reasoning text,
quality-factor breakdowns, bull/bear cases — in `raw` simultaneously for
the rest of the function, even though the per-horizon ranking loop only
processes one horizon at a time and score snapshots (the only consumer
that needs the full set) already ran before that loop starts.

`generate_picks()` itself is a large, deeply-integrated function (screener
calls, regime detection, DB writes, Telegram, multiple external services)
that isn't practically mountable end-to-end in a unit test — consistent
with this codebase's existing convention for functions of this shape
(see e.g. Product Integrity #011/#012's structural wiring tests). This
file verifies the fix as a structural/source property instead: the exact
release line exists, in the right place, and `raw` (the dict itself, not
`items`/`raw[horizon]`) is never read again anywhere after the horizon
loop begins — the same manual verification (`grep`) that justified the
fix in the first place, now locked in as a regression guard.
"""

import re
from pathlib import Path

_SOURCE = (Path(__file__).parent.parent.parent / "services" / "daily_picks.py").read_text()


def test_raw_horizon_reference_is_released_immediately_after_capture():
    assert (
        'items = raw[horizon]\n'
        '        # Drop `raw`\'s own reference now that `items` holds it for this'
    ) in _SOURCE, (
        "The release must happen immediately after `items = raw[horizon]` "
        "captures the reference this horizon's processing actually needs — "
        "not deferred to the end of the loop body, where several local "
        "variables (`universe`, `ranked`, `all_buy`, `all_buy_deduped`) would "
        "already be holding shallow-copied references to the same underlying "
        "reasoning/summary/quality_factors objects, making a later release "
        "of `raw[horizon]` alone ineffective."
    )
    assert "raw[horizon] = None" in _SOURCE


def test_release_happens_before_the_empty_items_early_return():
    # The release must apply to every horizon, including one with zero
    # candidates (`if not items: picks[horizon] = []; continue`) — placing
    # it after that early-return branch would silently skip the release for
    # any horizon that legitimately has no items.
    idx_items = _SOURCE.index("items = raw[horizon]")
    idx_release = _SOURCE.index("raw[horizon] = None")
    idx_early_return = _SOURCE.index("if not items:\n            picks[horizon] = []")
    assert idx_items < idx_release < idx_early_return


def test_raw_dict_is_never_read_again_after_the_horizon_loop_starts():
    # Only acceptable accesses of the `raw` dict variable anywhere in the
    # file are the single `raw[horizon]` read (capturing `items`) and the
    # `raw[horizon] = None` release, both inside the per-horizon loop.
    # Matches only actual indexing/assignment (`raw[` or `raw.`), not the
    # plain English word "raw" that appears elsewhere in unrelated comments
    # (e.g. "raw screener count", "raw static-universe size"). If a future
    # change adds another `raw[...]` read after the loop starts, this test
    # must fail — that would mean a horizon's data was needed again after
    # being released, silently reintroducing a use-after-clear bug (reading
    # `None` where a list was expected).
    loop_start = _SOURCE.index('for horizon in ("short", "medium", "long"):')
    # Strip comment lines before matching — this file's own explanatory
    # comments (including the one right above the release line) mention
    # `raw[horizon]` in prose, which must not count as a code access.
    tail_code_only = "\n".join(
        line for line in _SOURCE[loop_start:].splitlines()
        if not line.strip().startswith("#")
    )
    raw_dict_accesses = re.findall(r"\braw\[", tail_code_only)
    assert raw_dict_accesses == ["raw[", "raw["], (
        f"Expected exactly two `raw[...]` accesses after the horizon loop "
        f"starts (the `items = raw[horizon]` read and the `raw[horizon] = "
        f"None` release), found {len(raw_dict_accesses)}: a new access to "
        f"`raw` was added — verify it doesn't read a horizon whose entry "
        f"was already released to None."
    )


def test_write_score_snapshots_runs_before_the_release_loop_so_full_data_was_available():
    # The release is only safe because _write_score_snapshots(raw, market)
    # — the one consumer that legitimately needs every candidate's
    # quality_factors, not just the eventual Top-6 winners — already
    # completed before the per-horizon loop (and its release) begins.
    idx_snapshot_call = _SOURCE.index("_write_score_snapshots(raw, market)")
    idx_loop_start = _SOURCE.index('for horizon in ("short", "medium", "long"):')
    assert idx_snapshot_call < idx_loop_start
