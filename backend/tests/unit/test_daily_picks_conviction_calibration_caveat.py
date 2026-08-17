"""
DP-035 — Conviction-gate calibration-caveat truthfulness (2026-08-17).

A fresh re-query of the walk-forward backtest table `val_signals`
(re-run against the same doxdexwjeonzigfewfva Supabase project a prior
calibration study used) found `val_signals.composite_score` is a
different, simplified proxy score than the production `confidence`
field this gate reads (see `services/thresholds.py`'s
`DailyPicksPublicationThresholds.CONVICTION_WIN_RATE_CALIBRATION_VALIDATED`
docstring for the full evidence) — it tops out at ~82 for every horizon
and never reaches the gate's 85.0 threshold, so no win-rate-lift claim
at >=85 is currently reproducible from that table for ANY horizon,
including medium (which a prior study had claimed was validated).

This test suite does NOT re-run that SQL (it requires live Supabase
access unavailable in CI) — it verifies the smallest correct code
consequence: the gate's own source truthfully surfaces this
evidence-integrity caveat, and does NOT silently claim a validated
win-rate correlation, while leaving the actual gate values (85.0
threshold, 3-per-horizon cap) completely unchanged.
"""
import inspect

from services import daily_picks
from services.thresholds import DAILY_PICKS_PUBLICATION, DailyPicksPublicationThresholds


def test_threshold_and_cap_unchanged_by_the_caveat_finding():
    """No evidence supports a different numeric threshold or cap for any
    horizon — the calibration finding is a measurement-validity gap, not
    proof any specific number is wrong. Values must stay exactly as the
    original evidence-backed feature (DP-034) set them."""
    assert DAILY_PICKS_PUBLICATION.MIN_CONVICTION_TO_PUBLISH == 85.0
    assert DAILY_PICKS_PUBLICATION.MAX_PUBLISHED_PER_HORIZON == 3


def test_calibration_validated_flag_is_honestly_false():
    """DP-035 finding: no threshold-vs-win-rate claim is currently
    reproducible from val_signals for any horizon — this flag must not be
    silently flipped True without a corrected calibration study backing it."""
    assert DailyPicksPublicationThresholds.CONVICTION_WIN_RATE_CALIBRATION_VALIDATED is False


def test_conviction_semantic_source_does_not_overclaim_calibration():
    """The `conviction_semantic` string built in daily_picks.py (surfaced to
    both the API payload and the /picks page) must not read as a validated
    win-probability/quality claim, and must include the honest caveat."""
    src = inspect.getsource(daily_picks)
    assert '"conviction_semantic": (' in src
    assert "not yet confirmed" in src
    assert "not a calibrated win probability" in src
    # Must still identify the field correctly as Model Conviction, 0-100 scale
    assert "Model Conviction (0-100 scale" in src


def test_conviction_semantic_never_claims_percent_chance():
    """Regression guard carried over from the original DP-034 test intent
    (frontend wording.test.ts asserts the same for the header/card copy) —
    the semantic string itself must never read as a probability claim."""
    src = inspect.getsource(daily_picks)
    # Extract the conviction_semantic literal roughly, then check it doesn't
    # contain a probability/percent-chance claim.
    start = src.index('"conviction_semantic": (')
    end = src.index(")", start) + 1
    semantic_literal = src[start:end]
    assert "% chance" not in semantic_literal
    assert "probability of" not in semantic_literal
