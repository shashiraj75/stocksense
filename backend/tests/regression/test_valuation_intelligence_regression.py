"""
Regression tests for Valuation Intelligence Engine v1 (Epic 004 Sprint
#003) — locks in graceful-degradation and no-fabrication guarantees that
must never silently regress, mirroring growth_intelligence_regression.py's
own shape for the prior engine.
"""

import pytest

from services.engine_contract import Grade
from services.valuation_intelligence_engine import compute_valuation_intelligence
from services.india_valuation_adapter import build_india_valuation_fields
from services.us_valuation_adapter import build_us_valuation_fields


@pytest.mark.regression
class TestValuationIntelligenceRegression:
    def test_never_crashes_on_completely_empty_input(self):
        result = compute_valuation_intelligence("X", {}, sector_bucket="", market="US")
        assert result["grade"] == Grade.REJECTED.value

    def test_never_crashes_on_none_fields(self):
        result = compute_valuation_intelligence("X", None, market="US")
        assert result["grade"] == Grade.REJECTED.value

    def test_never_crashes_on_unknown_sector_bucket(self):
        fields = {"pe_ratio": {"value": 20.0}, "dividend_yield_pct": {"value": 1.0}, "market_cap": {"value": 1000.0}}
        result = compute_valuation_intelligence("X", fields, sector_bucket="UNKNOWN_BUCKET", market="US")
        assert result["grade"] != Grade.REJECTED.value

    def test_never_crashes_on_zero_or_negative_pe(self):
        """A loss-making company can have a negative or zero P/E — must
        not raise a ZeroDivisionError or score it as artificially cheap."""
        fields = {"pe_ratio": {"value": -5.0}, "dividend_yield_pct": {"value": 0.0}, "market_cap": {"value": 1000.0}}
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["metadata"]["category_contributions"]["Earnings Multiple"] == 0.0

    def test_never_crashes_on_zero_ev_ebitda(self):
        fields = {"pe_ratio": {"value": 20.0}, "dividend_yield_pct": {"value": 0.0}, "market_cap": {"value": 1000.0},
                   "ev_ebitda": {"value": 0.0}}
        result = compute_valuation_intelligence("X", fields, sector_bucket="IT", market="US")
        assert result["metadata"]["category_contributions"]["EV/EBITDA"] == 0.0

    def test_india_adapter_never_crashes_on_malformed_screener_data(self):
        malformed = {"available": True, "pe_ratio": "not_a_number", "market_cap_cr": None}
        fields = build_india_valuation_fields(malformed, {})
        assert "pe_ratio" in fields

    def test_engine_never_crashes_on_malformed_non_numeric_field_value(self):
        """Real defect found during this sprint's own graceful-degradation
        testing: a malformed, non-numeric provider value (e.g. a scraper
        parse artifact) used to raise TypeError on the `> 0` comparison
        inside _earnings_multiple instead of degrading gracefully — fixed
        at _val()'s shared boundary (filters to int/float once for every
        field), not patched separately in each scoring function."""
        fields = {"pe_ratio": {"value": "garbage"}, "dividend_yield_pct": {"value": 1.0},
                  "market_cap": {"value": 1000.0}}
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["grade"] != Grade.REJECTED.value or result["score"] == 0  # must not crash
        assert result["metadata"]["category_contributions"]["Earnings Multiple"] == 0.0

    def test_peg_adapter_never_crashes_on_malformed_growth_field(self):
        malformed = {"available": True, "pe_ratio": 20.0, "profit_growth_3y_pct": "garbage"}
        fields = build_india_valuation_fields(malformed, {})
        assert fields["peg_ratio"] is None

    def test_india_adapter_never_crashes_on_missing_operating_cf_for_fcf_yield(self):
        screener_data = {"available": True, "operating_cf_annual_cr": [], "investing_cf_latest_cr": -10.0,
                          "market_cap_cr": 1000.0}
        fields = build_india_valuation_fields(screener_data, {})
        assert fields["fcf_yield_pct"] is None

    def test_us_adapter_never_crashes_on_missing_market_cap_for_fcf_yield(self):
        info = {"freeCashflow": 1000.0, "marketCap": None}
        fields = build_us_valuation_fields(info)
        assert fields["fcf_yield_pct"] is None

    def test_does_not_fabricate_dividend_sustainability_without_payout_ratio(self):
        """A high yield with NO payout-ratio data must not silently apply
        either the bonus or the penalty — confidence/explainability must
        reflect the missing input honestly, not guess at sustainability."""
        fields = {"pe_ratio": {"value": 20.0}, "dividend_yield_pct": {"value": 5.0},
                  "market_cap": {"value": 1000.0}}
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["metadata"]["category_contributions"]["Dividend Income"] == 10.0  # yield bonus only, no sustainability modifier

    def test_score_and_confidence_always_within_contract_bounds(self):
        extreme_fields = {
            "pe_ratio": {"value": 1000.0}, "forward_pe": {"value": 1000.0}, "ev_sales": {"value": 1000.0},
            "price_book": {"value": 1000.0}, "ev_ebitda": {"value": 1000.0},
            "dividend_yield_pct": {"value": 1000.0}, "payout_ratio": {"value": 5.0},
            "market_cap": {"value": 1.0}, "fcf_yield_pct": {"value": -1000.0}, "peg_ratio": {"value": 1000.0},
        }
        result = compute_valuation_intelligence("X", extreme_fields, sector_bucket="FINANCIAL", market="IN")
        assert 0 <= result["score"] <= 100
        assert 0 <= result["confidence"] <= 100
