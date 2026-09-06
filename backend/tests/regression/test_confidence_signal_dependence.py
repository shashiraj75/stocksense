"""
2026-09-06 corrective review, Task 4 — confidence-field tracing.

The prior corrective-review report concluded momentum "cannot corrupt
confidence-score saturation" because `_confidence_engine` does not take
`composite_r` or momentum's contribution as direct arguments. That is
true but incomplete: `_confidence_engine` DOES take `signal`
(BUY/HOLD/SELL), and its `factor_agreement` component
(services/prediction_engine.py PredictionEngine._confidence_engine,
`_agrees()` helper) uses a COMPLETELY DIFFERENT agreement threshold per
signal — BUY: score>=55, SELL: score<=45, HOLD: 40<=score<=60. Since
momentum (or any other change to composite_r) can flip `signal` across
the BUY/HOLD boundary, it has a confirmed, sometimes dramatic INDIRECT
effect on `confidence_score` — the exact field daily_picks.py's
publication gate (`confidence` field, compared against
MIN_CONVICTION_TO_PUBLISH=85.0) actually reads.

These tests hold every OTHER input identical and toggle only `signal`,
proving the indirect effect empirically rather than by inspection alone.
"""
from services.prediction_engine import PredictionEngine


def _kwargs(score=53):
    return dict(
        tech_score=score, fund_score=score, sentiment_score=score,
        quality=None, regime={}, info={}, horizon="medium",
        sentiment_obj={"data_available": False}, market="US",
    )


class TestSignalDependentConfidence:
    def test_identical_factor_scores_different_signal_yields_different_confidence(self):
        """score=53 agrees with HOLD's 40-60 band but not BUY's >=55 band
        — a single-boundary case demonstrating the effect exists at all,
        deterministically, with no randomness or external data."""
        engine = PredictionEngine()
        hold_score, _band, hold_components = engine._confidence_engine(signal="HOLD", **_kwargs(53))
        buy_score, _band2, buy_components = engine._confidence_engine(signal="BUY", **_kwargs(53))
        assert hold_components["factor_agreement"] == 100
        assert buy_components["factor_agreement"] == 0
        assert hold_score != buy_score

    def test_score_that_agrees_with_both_signals_yields_same_agreement(self):
        """Sanity check: a score that satisfies BOTH thresholds (e.g. 57,
        which is >=55 AND within 40-60) must show identical agreement
        under either label — the effect is specifically about scores in
        the disagreement zone (40-54), not a blanket difference."""
        engine = PredictionEngine()
        _hold_score, _band, hold_components = engine._confidence_engine(signal="HOLD", **_kwargs(57))
        _buy_score, _band2, buy_components = engine._confidence_engine(signal="BUY", **_kwargs(57))
        assert hold_components["factor_agreement"] == buy_components["factor_agreement"] == 100

    def test_sell_uses_a_third_independent_threshold(self):
        """SELL's <=45 agreement band is independent of both BUY's and
        HOLD's — confirms all three signal labels route through
        genuinely different agreement criteria, not just a BUY-vs-rest
        split."""
        engine = PredictionEngine()
        _s, _b, sell_components = engine._confidence_engine(signal="SELL", **_kwargs(53))
        _s2, _b2, hold_components = engine._confidence_engine(signal="HOLD", **_kwargs(53))
        assert sell_components["factor_agreement"] == 0     # 53 > 45, disagrees with SELL
        assert hold_components["factor_agreement"] == 100   # 53 within 40-60, agrees with HOLD
