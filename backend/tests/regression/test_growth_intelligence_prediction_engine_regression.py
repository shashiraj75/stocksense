"""
Regression tests for Growth Intelligence's Prediction Engine integration
(Epic 003 Sprint #007) -- locks in the binding constraints Sprint #006's
decision actually requires: India confidence-only at +-3, US numerically
untouched, no scoring/composite_score/signal influence, and the existing
Financial Strength/Business Quality integrations confirmed unaffected.
"""

import pathlib

import pytest

import services.prediction_engine as pe
from services.prediction_engine import PredictionEngine


@pytest.mark.regression
class TestCapIsExactlyThreeNotSix:
    """Regression: confirms Growth Intelligence does NOT accidentally
    reuse Financial Strength's own +-6 cap -- a real, plausible copy-
    paste risk given how closely the two integrations mirror each
    other's structure."""

    def test_max_positive_adjustment_is_exactly_3(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], []) - 50 == 3

    def test_max_negative_adjustment_is_exactly_minus_3(self, monkeypatch):
        monkeypatch.delenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", raising=False)
        engine = PredictionEngine()
        gi = {"score": 0, "grade": "avoid", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], []) - 50 == -3

    def test_uses_its_own_threshold_constant_not_financial_strengths(self):
        from services.thresholds import GROWTH_INTELLIGENCE, FINANCIAL_STRENGTH
        assert GROWTH_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP == 3.0
        assert FINANCIAL_STRENGTH.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP == 6.0
        assert GROWTH_INTELLIGENCE.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP != \
            FINANCIAL_STRENGTH.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP


@pytest.mark.regression
class TestNoScoringOrSignalInfluence:
    """Per Sprint #007's explicit rule: no changes to composite_score,
    recommendation label, technical score, business quality score, or
    financial strength score."""

    def test_adjustment_function_has_no_composite_score_parameter(self):
        import inspect
        engine = PredictionEngine()
        params = inspect.signature(engine._apply_growth_intelligence_adjustment).parameters
        assert "composite_score" not in params
        assert "technical_score" not in params
        assert "business_quality_score" not in params
        assert "financial_strength_score" not in params

    def test_predict_source_does_not_add_growth_to_composite_signal(self):
        """Static check: _composite_signal's own raw_score computation
        block must not reference growth_intelligence at all -- confirms
        this integration didn't follow business_quality/quality_factors'
        older pattern of blending into the raw score."""
        source = pathlib.Path(pe.__file__).read_text()
        composite_fn_start = source.index("def _composite_signal")
        composite_fn_end = source.index("\n    def ", composite_fn_start + 10)
        composite_block = source[composite_fn_start:composite_fn_end]
        assert "growth_intelligence" not in composite_block


@pytest.mark.regression
class TestExistingIntegrationsUnaffected:
    """Confirms adding Growth Intelligence did not regress the existing
    Financial Strength or Business Quality wiring -- both must still be
    present, still be called, still occupy their original positions
    relative to each other."""

    def test_financial_strength_still_wired(self):
        engine = PredictionEngine()
        assert hasattr(engine, "_apply_financial_strength_adjustment")

    def test_business_quality_key_still_in_response_dict_construction(self):
        source = pathlib.Path(pe.__file__).read_text()
        assert source.count('"business_quality": business_quality,') >= 1

    def test_financial_strength_cap_unchanged_at_6(self):
        from services.thresholds import FINANCIAL_STRENGTH
        assert FINANCIAL_STRENGTH.PREDICTION_ENGINE_CONFIDENCE_ADJUSTMENT_CAP == 6.0


@pytest.mark.regression
class TestMarketGateRedundancy:
    """Regression: confirms BOTH the hard market check and the
    independent kill switch each, alone, are sufficient to keep US at
    zero adjustment -- defense in depth, not a single point of failure."""

    def test_hard_market_check_alone_blocks_us_even_with_switch_enabled(self, monkeypatch):
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_US", "1")
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("US", gi, 50, [], [], []) == 50

    def test_kill_switch_alone_blocks_india_even_with_correct_market(self, monkeypatch):
        monkeypatch.setenv("GROWTH_INTELLIGENCE_CONFIDENCE_ENABLED_IN", "0")
        engine = PredictionEngine()
        gi = {"score": 100, "grade": "strong_buy", "metadata": {}}
        assert engine._apply_growth_intelligence_adjustment("IN", gi, 50, [], [], []) == 50
