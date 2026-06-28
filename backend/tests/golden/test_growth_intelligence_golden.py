"""
Golden tests for Growth Intelligence Engine v1 (Epic 003 Sprint #003).

Honesty note, per this sprint's "evidence over assumptions" rule: these
are DETERMINISTIC SYNTHETIC PROFILES, not live-company snapshots. No
outcome-validated/backtested calibration exists yet for this brand-new
engine (named explicitly in thresholds.py's GrowthIntelligenceThresholds
docstring and the v1 implementation report) — these golden values lock
in the engine's current, deliberate behavior for a fixed input shape so
a future change can't silently alter it, but they are not a claim that
"strong_buy" for this synthetic secular-grower profile has been
validated against real market outcomes. The Live Validation Report
(separate, run against real companies) is the evidence for real-world
behavior; this file is regression insurance for the formula itself.
"""

import pytest

from services.engine_contract import Grade
from services.growth_intelligence_engine import compute_growth_intelligence
from services.india_growth_adapter import build_india_growth_fields


def _secular_grower() -> dict:
    return {
        "available": True,
        "sales_growth_3y_pct": 18.0, "sales_growth_5y_pct": 14.0,
        "profit_growth_3y_pct": 22.0, "profit_growth_5y_pct": 16.0,
        "sales_annual_cr": [100, 110, 121, 133, 146, 161, 177, 195, 214, 236, 259, 285],
        "operating_profit_annual_cr": [20, 23, 26, 30, 34, 39, 45, 51, 58, 66, 75, 85],
        "opm_annual_pct": [20, 21, 21.5, 22.5, 23, 24, 25, 26, 27, 28, 29, 30],
        "reserves_annual_cr": [50, 60, 72, 86, 103, 124, 149, 179, 215, 258, 310, 372],
        "equity_capital_cr": [10] * 12,
        "borrowings_annual_cr": [30, 30, 28, 26, 24, 22, 20, 18, 16, 14, 12, 10],
        "quarterly_pat_cr": [40, 42, 45, 49],
    }


def _mature_compounder() -> dict:
    """Steady, moderate, highly consistent growth — a "boring compounder"
    profile distinct from the secular grower's faster, accelerating shape."""
    return {
        "available": True,
        "sales_growth_3y_pct": 9.0, "sales_growth_5y_pct": 9.0,
        "profit_growth_3y_pct": 10.0, "profit_growth_5y_pct": 10.0,
        "sales_annual_cr": [100, 109, 118.8, 129.5, 141.2, 153.9, 167.7, 182.8, 199.3, 217.2, 236.8, 258.1],
        "operating_profit_annual_cr": [25, 27.25, 29.7, 32.4, 35.3, 38.5, 41.9, 45.7, 49.8, 54.3, 59.2, 64.5],
        "opm_annual_pct": [25] * 12,
        "reserves_annual_cr": [200, 215, 231, 248, 267, 287, 308, 331, 356, 382, 411, 442],
        "equity_capital_cr": [10] * 12,
        "borrowings_annual_cr": [40] * 12,
        "quarterly_pat_cr": [60, 61, 62, 63],
    }


def _declining_business() -> dict:
    return {
        "available": True,
        "sales_growth_3y_pct": -5.0, "sales_growth_5y_pct": 1.0,
        "profit_growth_3y_pct": -20.0, "profit_growth_5y_pct": -10.0,
        "sales_annual_cr": [200, 190, 185, 170, 160, 150, 145, 135, 125, 118, 110, 100],
        "operating_profit_annual_cr": [40, 35, 30, 25, 20, 18, 16, 13, 10, 8, 6, 4],
        "opm_annual_pct": [20, 18, 16, 15, 12.5, 12, 11, 9.6, 8, 6.8, 5.5, 4],
        "reserves_annual_cr": [100, 98, 95, 90, 88, 85, 80, 75, 70, 65, 60, 55],
        "equity_capital_cr": [10] * 12,
        "borrowings_annual_cr": [50, 55, 60, 68, 72, 80, 85, 90, 95, 98, 100, 102],
        "quarterly_pat_cr": [20, 15, 10, 5],
    }


def _bank_nbfc() -> dict:
    return {
        "available": True,
        "sales_growth_3y_pct": 15.0, "sales_growth_5y_pct": 14.0,
        "profit_growth_3y_pct": 16.0, "profit_growth_5y_pct": 10.0,
        "sales_annual_cr": None, "operating_profit_annual_cr": None, "opm_annual_pct": None,
        "reserves_annual_cr": None, "equity_capital_cr": None, "borrowings_annual_cr": None,
        "quarterly_pat_cr": [50, 55, 60, 66],
    }


@pytest.mark.golden
class TestGrowthIntelligenceGoldenProfiles:
    def test_secular_grower_golden(self):
        fields = build_india_growth_fields(_secular_grower())
        result = compute_growth_intelligence("SECULAR", fields, sector_bucket="Consumer", market="IN")
        assert result["score"] == 100
        assert result["grade"] == Grade.STRONG_BUY.value
        assert result["confidence"] == 100.0

    def test_mature_compounder_golden(self):
        fields = build_india_growth_fields(_mature_compounder())
        result = compute_growth_intelligence("COMPOUNDER", fields, sector_bucket="Consumer", market="IN")
        # Moderate, highly consistent growth should land HOLD/BUY territory
        # -- a deliberately different (lower-magnitude, lower-volatility)
        # profile than the secular grower above, expected to score lower.
        assert 50 <= result["score"] < 100
        assert result["grade"] in (Grade.HOLD.value, Grade.BUY.value)
        assert result["confidence"] == 100.0

    def test_declining_business_golden(self):
        fields = build_india_growth_fields(_declining_business())
        result = compute_growth_intelligence("DECLINING", fields, sector_bucket="Industrials", market="IN")
        assert result["score"] == 0
        assert result["grade"] == Grade.AVOID.value

    def test_bank_nbfc_golden(self):
        fields = build_india_growth_fields(_bank_nbfc())
        result = compute_growth_intelligence("BANKGOLDEN", fields, sector_bucket="Financials", market="IN")
        assert result["grade"] != Grade.REJECTED.value
        assert result["confidence"] < 60.0  # only 3/7 fields present, well below full completeness
        assert result["metadata"]["skipped_extended_fields"] == [
            "operating_profit_growth_3y_pct", "reinvestment_capital_growth_3y_pct", "margin_trend_pct_change",
        ]

    def test_mature_compounder_scores_lower_than_secular_grower(self):
        """Relative ordering sanity check — the two profiles are
        deliberately differentiated (faster/accelerating vs. steady/
        moderate growth), and the engine's score should preserve that
        ordering, not treat them as equivalent."""
        grower_result = compute_growth_intelligence(
            "A", build_india_growth_fields(_secular_grower()), market="IN")
        compounder_result = compute_growth_intelligence(
            "B", build_india_growth_fields(_mature_compounder()), market="IN")
        assert grower_result["score"] > compounder_result["score"]
