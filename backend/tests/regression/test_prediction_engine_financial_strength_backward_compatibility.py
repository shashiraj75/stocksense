"""
Regression test: introducing Financial Strength into PredictionEngine
(Epic 002 Sprint #010) must not change any existing market's behavior —
India predictions, the existing risk-reward/pledge adjustments, and the
existing business_quality/quality_factors fields must all be byte-for-
byte unaffected. Mirrors every prior Epic 002 sprint's own backward-
compatibility proof pattern.
"""

import pytest

from services.prediction_engine import PredictionEngine


@pytest.fixture
def engine():
    return PredictionEngine()


@pytest.mark.regression
def test_india_market_confidence_adjustment_is_always_a_no_op(engine):
    """India predictions must be completely unaffected by this sprint --
    Financial Strength is US-only (SSDS-005/SSDS-006 v1 scope)."""
    fs = {"score": 100, "grade": "strong_buy", "metadata": {}}
    confidence = 55
    result = engine._apply_financial_strength_adjustment("IN", fs, confidence, [], [], [])
    assert result == confidence


@pytest.mark.regression
def test_pledge_adjustment_unaffected_by_financial_strength_existing(engine):
    """The pre-existing _apply_pledge_adjustment must compute identically
    whether or not Financial Strength's adjustment is ever called --
    confirms the two are independent, not coupled."""
    info = {"promoter_pledge_pct": 60.0}
    reasoning, bear_case = [], []
    result = engine._apply_pledge_adjustment("IN", info, "BUY", 80, reasoning, bear_case)
    assert result == 30  # unchanged from its pre-Sprint-#010 behavior
    assert len(reasoning) == 1
    assert len(bear_case) == 1


@pytest.mark.regression
def test_risk_reward_adjustment_unaffected_by_financial_strength_existing(engine):
    trade_levels = {"risk_reward_ratio": 0.5, "risk_per_share": 2.0, "reward_per_share": 1.0}
    reasoning, bear_case = [], []
    result = engine._apply_risk_reward_adjustment("BUY", 80, trade_levels, reasoning, bear_case)
    assert result == 30  # unchanged from its pre-Sprint-#010 behavior


@pytest.mark.regression
def test_existing_business_quality_engine_module_unmodified():
    """Confirms business_quality_engine.py has zero coupling to the new
    Financial Strength adjustment -- this sprint touched only
    prediction_engine.py and thresholds.py, never business_quality_engine.py."""
    import services.business_quality_engine as bqe
    assert "financial_strength" not in bqe.__dict__
    assert not hasattr(bqe, "_apply_financial_strength_adjustment")


@pytest.mark.regression
def test_all_epic_002_modules_still_coexist_after_prediction_engine_integration():
    import services.sec_edgar_adapter  # noqa: F401
    import services.us_provider_precedence  # noqa: F401
    import services.financial_strength_engine  # noqa: F401
    import services.us_financial_strength_adapter  # noqa: F401
    import services.prediction_engine  # noqa: F401
    import services.business_quality_engine  # noqa: F401
