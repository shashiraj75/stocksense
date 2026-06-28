"""
Regression tests for Growth Intelligence Engine v1 (Epic 003 Sprint #003)
— locks in the graceful-degradation/no-fabrication contract, the
acceleration-bonus-cap defect fix, and the CAGR negative-latest-value
crash fix, all found and fixed during this sprint's own testing.
"""

import pytest

from services.engine_contract import Grade
from services.growth_intelligence_engine import compute_growth_intelligence
from services.growth_utils import compute_cagr_from_series
from services.india_growth_adapter import build_india_growth_fields
from services.thresholds import GROWTH, GROWTH_INTELLIGENCE


def _f(value):
    return {"value": value}


@pytest.mark.regression
class TestNoFabricationContract:
    """Sanity-checked: every assertion here fails loudly if the engine
    ever starts inventing a value for a field the adapter passed as None."""

    def test_missing_operating_profit_never_defaults_to_zero_score_contribution_being_mistaken_for_real_data(self):
        fields = {
            "revenue_growth_3y_pct": _f(10.0), "profit_growth_3y_pct": _f(10.0),
            "eps_trend": _f("mixed_positive"), "revenue_annual_series": _f([100, 110, 121, 133]),
        }
        result = compute_growth_intelligence("X", fields, market="IN")
        assert result["metadata"]["operating_profit_growth_3y_pct"] is None
        assert "operating_profit_growth_3y_pct" in result["metadata"]["skipped_extended_fields"]

    def test_bank_with_zero_extended_fields_is_not_rejected(self):
        """The exact case the sprint brief names explicitly: gracefully
        skip, don't reject, for a bank/NBFC lacking operating-profit data."""
        bank_data = {
            "available": True,
            "sales_growth_3y_pct": 15.0, "sales_growth_5y_pct": 14.0,
            "profit_growth_3y_pct": 16.0, "profit_growth_5y_pct": 10.0,
            "sales_annual_cr": None, "operating_profit_annual_cr": None, "opm_annual_pct": None,
            "reserves_annual_cr": None, "equity_capital_cr": None, "borrowings_annual_cr": None,
            "quarterly_pat_cr": [50, 55, 60, 66],
        }
        fields = build_india_growth_fields(bank_data)
        result = compute_growth_intelligence("BANK2", fields, sector_bucket="Financials", market="IN")
        assert result["grade"] != Grade.REJECTED.value
        assert result["metadata"]["skipped_extended_fields"] == [
            "operating_profit_growth_3y_pct", "reinvestment_capital_growth_3y_pct", "margin_trend_pct_change",
        ]


@pytest.mark.regression
class TestGrowthIntelligenceThresholdsAreIndependentFromExistingGrowthRegistry:
    """Locks in this sprint's explicit design decision (SSDS-007 Open
    Question #1, resolved): GROWTH_INTELLIGENCE is a separate registry
    entry from the pre-existing GROWTH, never a reuse or rename."""

    def test_growth_intelligence_is_not_the_same_object_as_growth(self):
        assert GROWTH_INTELLIGENCE is not GROWTH

    def test_growth_intelligence_has_its_own_independently_named_constants(self):
        assert not hasattr(GROWTH, "REVENUE_GROWTH_STRONG_MIN_PCT")
        assert hasattr(GROWTH_INTELLIGENCE, "REVENUE_GROWTH_STRONG_MIN_PCT")


@pytest.mark.regression
class TestAccelerationBonusNotDeadCode:
    """Regression for the defect found in this sprint's own unit tests:
    the acceleration bonus used to be clamped to the same cap as the base
    strong-band score, making it invisible for any company already at or
    above the strong threshold — the most common case it should apply to."""

    def test_acceleration_bonus_visibly_increases_score_above_base_cap(self):
        fields_with_bonus = {
            "revenue_growth_3y_pct": _f(16.0), "revenue_growth_5y_pct": _f(10.0),
            "profit_growth_3y_pct": _f(10.0), "eps_trend": _f("mixed_positive"),
            "revenue_annual_series": _f([100, 110, 121, 133]),
        }
        result = compute_growth_intelligence("X", fields_with_bonus, market="US")
        assert result["metadata"]["category_contributions"]["Revenue Growth"] > 15.0


@pytest.mark.regression
class TestCagrNegativeLatestValueDoesNotCrash:
    """Regression for the TypeError ('complex doesn't define __round__')
    found while writing this sprint's own integration tests — a positive
    base with a negative final value raises a negative ratio to a
    fractional power, producing a complex number."""

    def test_negative_latest_value_returns_none_not_an_exception(self):
        result = compute_cagr_from_series([10, 5, -2, -8], 3)
        assert result is None

    def test_full_pipeline_with_negative_terminal_value_does_not_crash(self):
        """End-to-end reproduction through the actual India adapter +
        engine, not just the isolated utility function."""
        data = {
            "available": True,
            "sales_growth_3y_pct": 5.0, "sales_growth_5y_pct": 4.0,
            "profit_growth_3y_pct": -10.0, "profit_growth_5y_pct": -5.0,
            "sales_annual_cr": [100, 90, 80, 70, 60, 50, 40, 30, 20, 10, 5, -5],
            "operating_profit_annual_cr": [20, 15, 10, 5, 0, -5, -10, -15, -20, -25, -30, -35],
            "opm_annual_pct": [20, 16, 12, 7, 0, -10, -25, -50, -100, -250, -600, -700],
            "reserves_annual_cr": [50] * 12, "equity_capital_cr": [10] * 12, "borrowings_annual_cr": [40] * 12,
            "quarterly_pat_cr": [10, 5, -2, -8],
        }
        fields = build_india_growth_fields(data)  # must not raise
        result = compute_growth_intelligence("CRASHCHECK", fields, market="IN")  # must not raise
        assert result is not None
