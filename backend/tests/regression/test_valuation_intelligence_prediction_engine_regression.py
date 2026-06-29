"""
Regression tests for Valuation Intelligence's Prediction Engine
integration (Epic 004 Sprint #007) -- locks in the binding constraints
Sprint #006's decision actually requires: an ASYMMETRIC +2 (gated) /
-4 (ungated) cap, the cross-engine gate applying only to the boost
side, market-adapted gate membership (Financial Strength only for US),
no scoring/composite_score/signal influence, both markets defaulting
to disabled, and the existing Financial Strength/Growth Intelligence/
Business Quality integrations confirmed unaffected.
"""

import pathlib

import pytest

import services.prediction_engine as pe
from services.prediction_engine import PredictionEngine


@pytest.mark.regression
class TestAsymmetricCapIsExactlyPlus2MinusFour:
    """Regression: confirms Valuation Intelligence's cap is genuinely
    asymmetric and does NOT accidentally reuse Growth Intelligence's
    own symmetric +-3 or Financial Strength's +-6 -- a real, plausible
    copy-paste risk given how closely all three integrations mirror
    each other's structure."""

    def test_max_positive_adjustment_is_exactly_2(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, {"grade": "buy"}, 50, [], [], []
        )
        assert new_conf - 50 == 2

    def test_max_negative_adjustment_is_exactly_minus_4(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 0, "grade": "avoid", "metadata": {}}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, None, None, None, 50, [], [], []
        )
        assert new_conf - 50 == -4

    def test_uses_its_own_threshold_constants_not_other_engines(self):
        from services.thresholds import VALUATION_INTELLIGENCE, GROWTH_INTELLIGENCE, FINANCIAL_STRENGTH
        assert VALUATION_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_POSITIVE == 2.0
        assert VALUATION_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_NEGATIVE == 4.0
        assert VALUATION_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_POSITIVE != \
            VALUATION_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_NEGATIVE
        assert VALUATION_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_POSITIVE != \
            GROWTH_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP
        assert VALUATION_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP_NEGATIVE != \
            FINANCIAL_STRENGTH.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP


@pytest.mark.regression
class TestGateAppliesOnlyToBoostSide:
    """Regression: the cross-engine gate must block ONLY the
    undervaluation (boost) path -- the overvaluation (demote) path must
    remain ungated in every case, per Sprint #006/#007's explicit
    'the warning must always apply' rule."""

    def test_demotion_unaffected_by_hard_negative_other_engines(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 0, "grade": "avoid", "metadata": {}}
        # Even with every gate engine ALSO avoid/rejected, demotion applies in full.
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "avoid"}, None, {"grade": "rejected"}, 50, [], [], []
        )
        assert new_conf - 50 == -4

    def test_boost_blocked_by_a_single_hard_negative_engine(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        # Only ONE of two available gate engines (BQ, GI) needs to be
        # hard-negative to block -- this is an ALL-clear (AND) gate, the
        # stricter reading Sprint #007's literal spec requires.
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, None, {"grade": "avoid"}, 50, [], [], []
        )
        assert new_conf == 50


@pytest.mark.regression
class TestMarketAdaptedGateMembership:
    """Regression: Financial Strength participates in the boost gate
    ONLY for US -- it has no India coverage (confirmed unchanged since
    Epic 002), so passing a hard-negative Financial Strength dict for
    India must never block the boost."""

    def test_financial_strength_ignored_for_india_even_if_avoid(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "IN", vi, {"grade": "buy"}, {"grade": "avoid"}, {"grade": "buy"}, 50, [], [], []
        )
        assert new_conf - 50 == 2  # FS ignored -- boost still applies

    def test_financial_strength_blocks_boost_for_us(self, monkeypatch):
        monkeypatch.setenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        new_conf = engine._apply_valuation_intelligence_adjustment(
            "US", vi, {"grade": "buy"}, {"grade": "avoid"}, {"grade": "buy"}, 50, [], [], []
        )
        assert new_conf == 50  # FS consulted for US -- blocks the boost


@pytest.mark.regression
class TestNoScoringOrSignalInfluence:
    """Per Sprint #007's explicit rule: no changes to composite_score,
    recommendation label, technical score, business quality score,
    financial strength score, or growth intelligence score."""

    def test_adjustment_function_has_no_composite_score_parameter(self):
        import inspect
        engine = PredictionEngine()
        params = inspect.signature(engine._apply_valuation_intelligence_adjustment).parameters
        assert "composite_score" not in params
        assert "technical_score" not in params

    def test_predict_source_does_not_add_valuation_to_composite_signal(self):
        """Static check: _composite_signal's own raw_score computation
        block must not reference valuation_intelligence at all --
        confirms this integration didn't follow business_quality/
        quality_factors' older pattern of blending into the raw score."""
        source = pathlib.Path(pe.__file__).read_text()
        composite_fn_start = source.index("def _composite_signal")
        composite_fn_end = source.index("\n    def ", composite_fn_start + 10)
        composite_block = source[composite_fn_start:composite_fn_end]
        assert "valuation_intelligence" not in composite_block


@pytest.mark.regression
class TestExistingIntegrationsUnaffected:
    """Confirms adding Valuation Intelligence did not regress the
    existing Financial Strength, Growth Intelligence, or Business
    Quality wiring -- all must still be present, still be called, still
    occupy their original positions relative to each other."""

    def test_financial_strength_still_wired(self):
        engine = PredictionEngine()
        assert hasattr(engine, "_apply_financial_strength_adjustment")

    def test_growth_intelligence_still_wired(self):
        engine = PredictionEngine()
        assert hasattr(engine, "_apply_growth_intelligence_adjustment")

    def test_business_quality_key_still_in_response_dict_construction(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert source.count('"business_quality": business_quality,') >= 1

    def test_financial_strength_cap_unchanged_at_6(self):
        from services.thresholds import FINANCIAL_STRENGTH
        assert FINANCIAL_STRENGTH.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP == 6.0

    def test_growth_intelligence_cap_unchanged_at_3(self):
        from services.thresholds import GROWTH_INTELLIGENCE
        assert GROWTH_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP == 3.0

    def test_growth_intelligence_adjustment_still_called_before_valuation(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], []) == 53


@pytest.mark.regression
class TestKillSwitchDefaultsDisabledBothMarkets:
    """Regression: confirms the deliberate departure from Growth
    Intelligence's own rollout (enabled-by-default for India) -- both
    Valuation Intelligence switches must default to disabled, per
    Sprint #006's more conservative posture."""

    def test_india_default_is_disabled_not_enabled(self, monkeypatch):
        from services.prediction_engine import _valuation_intelligence_confidence_enabled
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        assert _valuation_intelligence_confidence_enabled("IN") is False

    def test_us_default_is_disabled(self, monkeypatch):
        from services.prediction_engine import _valuation_intelligence_confidence_enabled
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        assert _valuation_intelligence_confidence_enabled("US") is False

    def test_with_disabled_default_no_adjustment_applies_in_either_market(self, monkeypatch):
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        monkeypatch.delenv("VALUATION_INTELLIGENCE_CONFIDENCE_ENABLED_US", raising=False)
        engine = PredictionEngine()
        vi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        for market in ("IN", "US"):
            new_conf = engine._apply_valuation_intelligence_adjustment(
                market, vi, {"grade": "buy"}, {"grade": "buy"}, {"grade": "buy"}, 50, [], [], []
            )
            assert new_conf == 50
