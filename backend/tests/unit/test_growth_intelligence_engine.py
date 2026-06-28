"""
Unit tests for services/growth_intelligence_engine.py (Epic 003 Sprint #003).
Tests the pure engine directly against hand-built `fields` dicts — no
adapters, no live data, no providers.
"""

import pytest

from services.growth_intelligence_engine import compute_growth_intelligence
from services.engine_contract import Grade


def _f(value):
    return {"value": value}


@pytest.mark.unit
class TestComputeGrowthIntelligence:
    def test_rejects_when_insufficient_core_data(self):
        result = compute_growth_intelligence("X", {}, market="US")
        assert result["grade"] == Grade.REJECTED.value
        assert result["score"] == 0
        assert result["confidence"] == 0.0
        assert result["metadata"]["rejection_reason"] == "insufficient_data"

    def test_rejects_with_only_one_core_field(self):
        fields = {"revenue_growth_3y_pct": _f(15.0)}
        result = compute_growth_intelligence("X", fields, market="US")
        assert result["grade"] == Grade.REJECTED.value

    def test_does_not_reject_with_two_core_fields(self):
        fields = {"revenue_growth_3y_pct": _f(15.0), "profit_growth_3y_pct": _f(15.0)}
        result = compute_growth_intelligence("X", fields, market="US")
        assert result["grade"] != Grade.REJECTED.value

    def test_strong_growth_scores_high(self):
        fields = {
            "revenue_growth_3y_pct": _f(20.0),
            "revenue_growth_5y_pct": _f(12.0),
            "profit_growth_3y_pct": _f(25.0),
            "profit_growth_5y_pct": _f(15.0),
            "eps_trend": _f("accelerating"),
            "revenue_annual_series": _f([100, 110, 121, 133, 146, 161, 177, 195]),
            "revenue_growth_cv": _f(0.05),
        }
        result = compute_growth_intelligence("X", fields, market="US")
        assert result["score"] >= 80
        assert result["grade"] == Grade.STRONG_BUY.value

    def test_weak_growth_scores_low(self):
        fields = {
            "revenue_growth_3y_pct": _f(-10.0),
            "profit_growth_3y_pct": _f(-25.0),
            "eps_trend": _f("decelerating"),
            "revenue_annual_series": _f([100, 80, 95, 60, 100, 50, 90, 40]),
            "revenue_growth_cv": _f(1.2),
        }
        result = compute_growth_intelligence("X", fields, market="US")
        assert result["score"] <= 35
        assert result["grade"] in (Grade.WATCH.value, Grade.AVOID.value)
        assert any("contracting" in r for r in result["risks"])

    def test_never_fabricates_missing_extended_metrics(self):
        """Core data only, no operating-profit/reinvestment/margin data —
        confirms those categories contribute exactly 0, not a guessed value."""
        fields = {
            "revenue_growth_3y_pct": _f(20.0),
            "profit_growth_3y_pct": _f(20.0),
            "eps_trend": _f("accelerating"),
            "revenue_annual_series": _f([100, 120, 144, 173]),
        }
        result = compute_growth_intelligence("X", fields, market="IN", sector_bucket="Financials")
        assert result["metadata"]["category_contributions"]["Operating Profit Growth"] == 0.0
        assert result["metadata"]["category_contributions"]["Reinvestment Efficiency"] == 0.0
        assert result["metadata"]["category_contributions"]["Margin Trend"] == 0.0
        assert set(result["metadata"]["skipped_extended_fields"]) == {
            "operating_profit_growth_3y_pct", "reinvestment_capital_growth_3y_pct", "margin_trend_pct_change",
        }
        # Confirms graceful degradation, not rejection — sector confidence
        # penalty shows up in `confidence`, not in a hard reject.
        assert result["grade"] != Grade.REJECTED.value
        assert result["confidence"] < 100.0

    def test_confidence_reflects_data_completeness(self):
        full_fields = {
            "revenue_growth_3y_pct": _f(10.0), "profit_growth_3y_pct": _f(10.0),
            "eps_trend": _f("mixed_positive"), "revenue_annual_series": _f([100, 110, 121, 133]),
            "revenue_growth_cv": _f(0.1), "operating_profit_growth_3y_pct": _f(10.0),
            "reinvestment_capital_growth_3y_pct": _f(5.0), "margin_trend_pct_change": _f(1.0),
        }
        partial_fields = {
            "revenue_growth_3y_pct": _f(10.0), "profit_growth_3y_pct": _f(10.0),
            "eps_trend": _f("mixed_positive"), "revenue_annual_series": _f([100, 110, 121, 133]),
        }
        full = compute_growth_intelligence("X", full_fields, market="US")
        partial = compute_growth_intelligence("X", partial_fields, market="US")
        assert full["confidence"] > partial["confidence"]

    def test_reinvestment_efficiency_capital_light_growth(self):
        """Operating profit growing while invested capital shrinks should
        score as maximally efficient, not divide-by-near-zero garbage."""
        fields = {
            "revenue_growth_3y_pct": _f(10.0), "profit_growth_3y_pct": _f(10.0),
            "eps_trend": _f("mixed_positive"), "revenue_annual_series": _f([100, 110, 121, 133]),
            "operating_profit_growth_3y_pct": _f(20.0),
            "reinvestment_capital_growth_3y_pct": _f(-5.0),
        }
        result = compute_growth_intelligence("X", fields, market="US")
        assert result["metadata"]["category_contributions"]["Reinvestment Efficiency"] > 0

    def test_growth_acceleration_bonus_applied(self):
        # g3=16% is just above the +15 strong-band cutoff but below the
        # absolute score cap (15), leaving headroom for the +3 bonus to
        # actually move the (clamped) result.
        fields_accel = {
            "revenue_growth_3y_pct": _f(16.0), "revenue_growth_5y_pct": _f(10.0),
            "profit_growth_3y_pct": _f(10.0), "eps_trend": _f("mixed_positive"),
            "revenue_annual_series": _f([100, 110, 121, 133]),
        }
        fields_no_accel = {
            "revenue_growth_3y_pct": _f(16.0), "revenue_growth_5y_pct": _f(25.0),
            "profit_growth_3y_pct": _f(10.0), "eps_trend": _f("mixed_positive"),
            "revenue_annual_series": _f([100, 110, 121, 133]),
        }
        accel = compute_growth_intelligence("X", fields_accel, market="US")
        no_accel = compute_growth_intelligence("X", fields_no_accel, market="US")
        assert accel["metadata"]["category_contributions"]["Revenue Growth"] > no_accel["metadata"]["category_contributions"]["Revenue Growth"]

    def test_score_always_clamped_0_to_100(self):
        # Construct an extreme-positive case to confirm clamping at 100
        fields = {
            "revenue_growth_3y_pct": _f(500.0), "revenue_growth_5y_pct": _f(10.0),
            "profit_growth_3y_pct": _f(500.0), "profit_growth_5y_pct": _f(10.0),
            "eps_trend": _f("accelerating"), "revenue_annual_series": _f([1, 2, 4, 8]),
            "revenue_growth_cv": _f(0.01), "operating_profit_growth_3y_pct": _f(500.0),
            "reinvestment_capital_growth_3y_pct": _f(-50.0), "margin_trend_pct_change": _f(50.0),
        }
        result = compute_growth_intelligence("X", fields, market="US")
        assert 0 <= result["score"] <= 100
