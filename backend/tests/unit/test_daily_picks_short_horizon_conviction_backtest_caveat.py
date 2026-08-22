"""
DP-036 — Short-horizon conviction-gate backtest finding, distinguishable
caveat (2026-08-18).

A real, full-population walk-forward backtest
(`backend/scripts/conviction_gate_backtest.py`) was run against the ACTUAL
Daily Picks publication gate field — `alpha_observations.signal_confidence`
— not `val_signals.composite_score` (DP-035's proxy) and not
`confidence_model`/`_confidence_engine` (a separate, unrelated heuristic).
13,988 rows across both markets, SHORT horizon (the only horizon with
enough resolved forward-return data to test yet):

    Market | <85 win rate | >=85 win rate
    -------|--------------|---------------
    India  |    51.5%     |     52.4%
    US     |    60.7%     |     60.8%  (avg realized return in the >=85
                                        bucket is actually LOWER than <85)

This is a definitive, adequately-sampled negative finding for short
horizon specifically — stronger than DP-035's generic "unconfirmed, not
enough data yet" caveat, which still correctly describes medium/long
(insufficient resolved data for either). This test suite verifies:

1. The 85.0 threshold and 3-per-horizon cap are completely unchanged
   (this is a caveat-honesty fix, not a gate-removal — see DP-036 in the
   Daily-Picks Implementation Register for why removing the gate was
   considered and deliberately not implemented in this pass).
2. Short horizon's `conviction_semantic` text is a definitive "tested, no
   lift found" statement, distinguishable from medium/long's "not yet
   confirmed" wording — they must not be identical boilerplate.
3. Neither string overclaims a calibrated win probability or "% chance".
"""
import inspect

from services import daily_picks
from services.thresholds import (
    DAILY_PICKS_PUBLICATION,
    DailyPicksPublicationThresholds,
)


def test_threshold_and_cap_unchanged_by_the_dp036_finding():
    """DP-036's negative finding does not justify changing the numeric gate
    values for any horizon, including short — see the decision rationale
    in thresholds.py's SHORT_HORIZON_CONVICTION_WIN_RATE_LIFT_CONFIRMED
    docstring and the DP-036 register entry."""
    assert DAILY_PICKS_PUBLICATION.MIN_CONVICTION_TO_PUBLISH == 85.0
    assert DAILY_PICKS_PUBLICATION.MAX_PUBLISHED_PER_HORIZON == 3


def test_short_horizon_lift_confirmed_flag_is_honestly_false():
    assert (
        DailyPicksPublicationThresholds.SHORT_HORIZON_CONVICTION_WIN_RATE_LIFT_CONFIRMED
        is False
    )


def test_conviction_semantic_branches_on_horizon_for_short():
    """The conviction_semantic construction must actually branch on
    `horizon == "short"` — a single shared string for all three horizons
    would fail to distinguish DP-036's definitive finding from medium/
    long's thinner-sample "unconfirmed" state."""
    src = inspect.getsource(daily_picks)
    assert 'if horizon == "short"' in src
    start = src.index('"conviction_semantic": (')
    end = src.index('),\n', start) + len('),\n')
    block = src[start:end]
    assert 'if horizon == "short"' in block or 'if horizon == "short"' in src[start:start + 4000]


def test_short_horizon_caveat_is_definitive_tested_language():
    """Short horizon's caveat must state the finding was actually tested and
    found no meaningful lift — not the softer 'unconfirmed, not enough data
    yet' phrasing that still applies to medium/long.

    DP-037 (2026-08-22): this test previously required the literal "13,988"
    in the user-facing string. That figure was a MISLABELLED FETCHED row
    count pooling the short and medium horizons — it was never a resolved
    short-horizon sample size. The assertion is now INVERTED: the copy must
    NOT cite it. The finding's direction is unchanged; only the denominator
    claim was wrong."""
    src = inspect.getsource(daily_picks)
    start = src.index('"conviction_semantic": (')
    short_end = src.index(') if horizon == "short"', start)
    short_literal = src[start:short_end]
    assert "13,988" not in short_literal, (
        "user-facing conviction copy must not cite the mislabelled 13,988 "
        "figure (a pooled short+medium FETCHED count) — see DP-037"
    )
    assert "no meaningful" in short_literal.lower() or "no meaningful win-rate" in short_literal
    assert "tested against realized outcomes" in short_literal


def test_medium_long_caveat_language_unchanged_and_distinguishable():
    """Medium/long must keep the DP-035 'not yet confirmed' wording,
    textually distinct from short horizon's definitive finding — not
    identical boilerplate across all three horizons."""
    src = inspect.getsource(daily_picks)
    start = src.index('"conviction_semantic": (')
    short_end = src.index(') if horizon == "short"', start)
    else_start = src.index("else (", short_end)
    else_end = src.index(")", src.index(")", else_start) + 1) + 1
    else_literal = src[else_start:else_end]
    assert "not yet confirmed" in else_literal
    assert "13,988" not in else_literal
    assert "tested against realized outcomes" not in else_literal


def test_neither_branch_overclaims_a_percent_chance_or_probability():
    src = inspect.getsource(daily_picks)
    start = src.index('"conviction_semantic": (')
    end = src.index(",\n            **_publication_meta", start)
    full_expr = src[start:end]
    assert "% chance" not in full_expr
    assert "probability of" not in full_expr
