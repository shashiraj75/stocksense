"""
Golden tests for Valuation Intelligence Engine v1 (Epic 004 Sprint #003).

Honesty note, per this sprint's "evidence over assumptions" rule: these
are DETERMINISTIC SYNTHETIC PROFILES, not live-company snapshots. No
outcome-validated/backtested calibration exists yet for this brand-new
engine (named explicitly in thresholds.py's ValuationIntelligenceThresholds
docstring and the v1 implementation report) — these golden values lock
in the engine's current, deliberate behavior for a fixed input shape so
a future change can't silently alter it. They are not a claim that
"strong_buy" for the synthetic "deep value" profile below has been
validated against real market outcomes — that is exactly what a future
Calibration/Outcome Validation sprint exists to measure, mirroring
Growth Intelligence's own proven lifecycle.
"""

import pytest

from services.engine_contract import Grade
from services.valuation_intelligence_engine import compute_valuation_intelligence


def _deep_value() -> dict:
    """Cheap on every multiple, sustainable dividend, no structural gaps."""
    return {
        "pe_ratio": {"value": 9.0}, "forward_pe": {"value": 8.0}, "ev_sales": {"value": 0.8},
        "price_book": {"value": 0.8}, "ev_ebitda": {"value": 5.5},
        "dividend_yield_pct": {"value": 4.5}, "payout_ratio": {"value": 0.40},
        "market_cap": {"value": 50000.0}, "fcf_yield_pct": {"value": 11.0}, "peg_ratio": {"value": 0.6},
    }


def _richly_valued() -> dict:
    """Expensive on every multiple, no dividend, unsustainable payout N/A."""
    return {
        "pe_ratio": {"value": 65.0}, "forward_pe": {"value": 58.0}, "ev_sales": {"value": 9.0},
        "price_book": {"value": 7.0}, "ev_ebitda": {"value": 28.0},
        "dividend_yield_pct": {"value": 0.0}, "payout_ratio": None,
        "market_cap": {"value": 200000.0}, "fcf_yield_pct": {"value": 0.5}, "peg_ratio": {"value": 3.2},
    }


def _mixed_signal() -> dict:
    """Cheap earnings multiple, but rich on EV/Sales and a risky payout —
    a genuinely mixed profile, not engineered to land in any one grade."""
    return {
        "pe_ratio": {"value": 12.0}, "forward_pe": {"value": 11.0}, "ev_sales": {"value": 6.0},
        "dividend_yield_pct": {"value": 4.0}, "payout_ratio": {"value": 0.95},
        "market_cap": {"value": 80000.0},
    }


@pytest.mark.golden
class TestValuationIntelligenceGolden:
    def test_deep_value_financial_sector_locked(self):
        result = compute_valuation_intelligence("DEEPVALUE", _deep_value(), sector_bucket="FINANCIAL", market="IN")
        assert result["score"] == 100
        assert result["grade"] == Grade.STRONG_BUY.value
        assert result["confidence"] == 100.0

    def test_richly_valued_financial_sector_locked(self):
        """EV/EBITDA, FCF Yield, PEG are inapplicable for FINANCIAL — only
        Earnings Multiple/EV/Sales/Price-Book drive the score here, hence
        13, not 0 (locking the actual computed value, not a hand guess)."""
        result = compute_valuation_intelligence("RICH", _richly_valued(), sector_bucket="FINANCIAL", market="US")
        assert result["score"] == 13
        assert result["grade"] == Grade.AVOID.value

    def test_mixed_signal_locked(self):
        result = compute_valuation_intelligence("MIXED", _mixed_signal(), sector_bucket="IT", market="IN")
        assert result["score"] == 58
        assert result["grade"] == Grade.HOLD.value
        assert "Earnings Multiple" in " ".join(result["strengths"])
        assert any("sustainability risk" in r for r in result["risks"])

    def test_deep_value_strengths_surface_correct_categories(self):
        result = compute_valuation_intelligence("DEEPVALUE", _deep_value(), sector_bucket="FINANCIAL", market="IN")
        strength_names = {s.split(":")[0] for s in result["strengths"]}
        assert strength_names.issubset({
            "Earnings Multiple", "EV/Sales", "Price/Book", "EV/EBITDA",
            "Dividend Income", "Free Cash Flow Yield", "PEG Ratio",
        })
        assert len(result["strengths"]) <= 3
