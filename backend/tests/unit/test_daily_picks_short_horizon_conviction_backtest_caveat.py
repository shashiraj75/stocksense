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
import re

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


def test_short_horizon_caveat_may_not_claim_completed_full_population_evidence():
    """Short horizon's caveat must report the prior finding's DIRECTION while
    stating that its denominator and return methodology are under re-audit.

    DP-037 (2026-08-22), pass 1: this test previously required the literal
    "13,988". That figure was a MISLABELLED FETCHED row count pooling the
    short and medium horizons — never a resolved short-horizon sample size —
    so the assertion was inverted to forbid it.

    DP-037 pass 2 (2026-08-22): an independent review found the copy STILL
    overclaimed in two further ways, both now forbidden outright:

      * "across the full candidate population" asserted a denominator the
        data never supported;
      * "shows no meaningful win-rate improvement" asserted a settled
        negative result resting on inference that assumed independent
        observations — an assumption repeated symbols and session-date
        clustering violate.

    Until a corrected LIVE full-population audit run actually succeeds, the
    copy must say the prior analysis found no PROVEN lift and that the
    methodology behind it is being re-audited. It may only be strengthened to
    whatever level the governed classifier actually returns — never promoted
    by hand."""
    src = inspect.getsource(daily_picks)
    start = src.index('"conviction_semantic": (')
    short_end = src.index(') if horizon == "short"', start)
    short_literal = src[start:short_end]

    assert "13,988" not in short_literal, (
        "user-facing conviction copy must not cite the mislabelled 13,988 "
        "figure (a pooled short+medium FETCHED count) — see DP-037"
    )
    lowered = short_literal.lower()
    assert "full candidate population" not in lowered, (
        "the full-population denominator claim is not yet supported by a "
        "completed live audit run — see DP-037"
    )
    assert "tested across" not in lowered
    assert "shows no meaningful" not in lowered, (
        "a settled negative result may not be asserted while the corrected "
        "re-audit is pending — see DP-037"
    )
    # The honest replacement states both halves.
    assert "no proven conviction lift" in lowered
    assert "re-audit" in lowered
    assert "denominator" in lowered


def _rendered_short_caveat() -> str:
    """
    The short-horizon caveat as the USER sees it.

    The source literal is written as adjacent quoted fragments across several
    lines, so a phrase can straddle two fragments and be absent from the raw
    source text even though it is present in the rendered string. Joining the
    fragments first is what makes a phrase assertion meaningful.
    """
    src = inspect.getsource(daily_picks)
    start = src.index('"conviction_semantic": (')
    short_end = src.index(') if horizon == "short"', start)
    literal = src[start:short_end]
    return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', literal.split("(", 1)[1]))


def test_short_horizon_caveat_never_asserts_a_proven_conviction_advantage():
    rendered = _rendered_short_caveat()
    assert "not a proven conviction advantage" in rendered
    assert "not a calibrated win probability" in rendered
    # It may say there is NO proven advantage; it may never assert one exists.
    assert not re.search(r"proven (edge|lift|advantage) (of|over)", rendered, re.I)
    assert not re.search(r"%\s*chance", rendered, re.I)


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
    assert "re-audit" not in else_literal.lower()


def test_neither_branch_overclaims_a_percent_chance_or_probability():
    src = inspect.getsource(daily_picks)
    start = src.index('"conviction_semantic": (')
    end = src.index(",\n            **_publication_meta", start)
    full_expr = src[start:end]
    assert "% chance" not in full_expr
    assert "probability of" not in full_expr
