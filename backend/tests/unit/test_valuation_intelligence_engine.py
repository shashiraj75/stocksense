"""
Unit tests for services/valuation_intelligence_engine.py (Epic 004 Sprint #003).
Tests the pure engine directly against hand-built `fields` dicts — no
adapters, no live data, no providers.
"""

import pytest

from services.valuation_intelligence_engine import compute_valuation_intelligence
from services.engine_contract import Grade


def _f(value):
    return {"value": value}


@pytest.mark.unit
class TestComputeValuationIntelligence:
    def test_rejects_when_insufficient_core_data(self):
        result = compute_valuation_intelligence("X", {}, market="US")
        assert result["grade"] == Grade.REJECTED.value
        assert result["score"] == 0
        assert result["confidence"] == 0.0
        assert result["metadata"]["rejection_reason"] == "insufficient_data"

    def test_rejects_with_only_one_core_field(self):
        fields = {"pe_ratio": _f(20.0)}
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["grade"] == Grade.REJECTED.value

    def test_does_not_reject_with_two_core_fields(self):
        fields = {"pe_ratio": _f(20.0), "dividend_yield_pct": _f(1.0)}
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["grade"] != Grade.REJECTED.value

    def test_cheap_earnings_multiple_scores_high(self):
        fields = {
            "pe_ratio": _f(10.0), "forward_pe": _f(9.0), "ev_sales": _f(1.0),
            "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0),
        }
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["score"] > 50
        assert any("Earnings Multiple" in s for s in result["strengths"])

    def test_expensive_earnings_multiple_scores_low(self):
        fields = {
            "pe_ratio": _f(50.0), "forward_pe": _f(45.0), "ev_sales": _f(8.0),
            "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0),
        }
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["score"] < 50
        assert any("Earnings Multiple" in w for w in result["weaknesses"])

    def test_dividend_zero_yield_not_penalized(self):
        """A company paying no dividend should never be scored down for it
        — many legitimate growth companies pay none (engine docstring)."""
        fields = {"pe_ratio": _f(20.0), "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0)}
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["metadata"]["category_contributions"]["Dividend Income"] == 0.0

    def test_high_yield_with_sustainable_payout_scores_well(self):
        fields = {
            "pe_ratio": _f(20.0), "dividend_yield_pct": _f(4.0), "payout_ratio": _f(0.40),
            "market_cap": _f(1000.0),
        }
        result = compute_valuation_intelligence("X", fields, market="US")
        assert result["metadata"]["category_contributions"]["Dividend Income"] > 10

    def test_high_yield_with_risky_payout_is_penalized_relative_to_sustainable(self):
        sustainable = {
            "pe_ratio": _f(20.0), "dividend_yield_pct": _f(4.0), "payout_ratio": _f(0.40),
            "market_cap": _f(1000.0),
        }
        risky = {
            "pe_ratio": _f(20.0), "dividend_yield_pct": _f(4.0), "payout_ratio": _f(0.95),
            "market_cap": _f(1000.0),
        }
        sustainable_result = compute_valuation_intelligence("X", sustainable, market="US")
        risky_result = compute_valuation_intelligence("Y", risky, market="US")
        assert risky_result["metadata"]["category_contributions"]["Dividend Income"] < \
            sustainable_result["metadata"]["category_contributions"]["Dividend Income"]
        assert any("sustainability risk" in r for r in risky_result["risks"])

    def test_price_book_inapplicable_outside_financial_real_estate(self):
        fields = {
            "pe_ratio": _f(20.0), "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0),
            "price_book": _f(0.5),  # would score STRONG if applicable
        }
        result = compute_valuation_intelligence("X", fields, sector_bucket="IT", market="US")
        assert result["metadata"]["category_contributions"]["Price/Book"] == 0.0
        assert "price_book" in result["metadata"]["inapplicable_fields"]

    def test_price_book_applicable_for_financial_sector(self):
        fields = {
            "pe_ratio": _f(20.0), "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0),
            "price_book": _f(0.5),
        }
        result = compute_valuation_intelligence("X", fields, sector_bucket="FINANCIAL", market="IN")
        assert result["metadata"]["category_contributions"]["Price/Book"] > 0

    def test_ev_ebitda_fcf_peg_inapplicable_for_financial_sector(self):
        fields = {
            "pe_ratio": _f(8.0), "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0),
            "ev_ebitda": _f(5.0), "fcf_yield_pct": _f(10.0), "peg_ratio": _f(0.5),
        }
        result = compute_valuation_intelligence("X", fields, sector_bucket="FINANCIAL", market="IN")
        assert result["metadata"]["category_contributions"]["EV/EBITDA"] == 0.0
        assert result["metadata"]["category_contributions"]["Free Cash Flow Yield"] == 0.0
        assert result["metadata"]["category_contributions"]["PEG Ratio"] == 0.0
        for f in ("ev_ebitda", "fcf_yield_pct", "peg_ratio"):
            assert f in result["metadata"]["inapplicable_fields"]

    def test_ev_ebitda_fcf_peg_applicable_for_non_financial_sector(self):
        fields = {
            "pe_ratio": _f(8.0), "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0),
            "ev_ebitda": _f(5.0), "fcf_yield_pct": _f(10.0), "peg_ratio": _f(0.5),
        }
        result = compute_valuation_intelligence("X", fields, sector_bucket="IT", market="US")
        assert result["metadata"]["category_contributions"]["EV/EBITDA"] > 0
        assert result["metadata"]["category_contributions"]["Free Cash Flow Yield"] > 0
        assert result["metadata"]["category_contributions"]["PEG Ratio"] > 0

    def test_score_bounded_0_100(self):
        cheap = {
            "pe_ratio": _f(5.0), "forward_pe": _f(4.0), "ev_sales": _f(0.5),
            "dividend_yield_pct": _f(8.0), "payout_ratio": _f(0.3), "market_cap": _f(1000.0),
            "ev_ebitda": _f(3.0), "fcf_yield_pct": _f(15.0), "peg_ratio": _f(0.3),
            "price_book": _f(0.3),
        }
        result = compute_valuation_intelligence("X", cheap, sector_bucket="FINANCIAL", market="IN")
        assert 0 <= result["score"] <= 100

        expensive = {
            "pe_ratio": _f(80.0), "forward_pe": _f(75.0), "ev_sales": _f(10.0),
            "dividend_yield_pct": _f(0.0), "market_cap": _f(1000.0),
            "ev_ebitda": _f(25.0), "fcf_yield_pct": _f(0.5), "peg_ratio": _f(3.0),
            "price_book": _f(6.0),
        }
        result2 = compute_valuation_intelligence("Y", expensive, sector_bucket="FINANCIAL", market="IN")
        assert 0 <= result2["score"] <= 100
        assert result2["score"] < result["score"]

    def test_deterministic_same_input_same_output(self):
        fields = {
            "pe_ratio": _f(18.0), "forward_pe": _f(16.0), "ev_sales": _f(2.5),
            "dividend_yield_pct": _f(2.0), "payout_ratio": _f(0.5), "market_cap": _f(5000.0),
        }
        r1 = compute_valuation_intelligence("X", fields, sector_bucket="IT", market="US")
        r2 = compute_valuation_intelligence("X", fields, sector_bucket="IT", market="US")
        assert r1 == r2

    def test_missing_extended_fields_does_not_crash_and_lowers_confidence(self):
        fields = {"pe_ratio": _f(20.0), "dividend_yield_pct": _f(1.0), "market_cap": _f(1000.0)}
        result = compute_valuation_intelligence("X", fields, sector_bucket="IT", market="US")
        assert result["grade"] != Grade.REJECTED.value
        assert result["confidence"] < 100.0

    def test_empty_fields_dict_does_not_crash(self):
        result = compute_valuation_intelligence("X", None, market="US")
        assert result["grade"] == Grade.REJECTED.value

    def test_engine_response_contract_keys(self):
        fields = {"pe_ratio": _f(18.0), "dividend_yield_pct": _f(2.0), "market_cap": _f(5000.0)}
        result = compute_valuation_intelligence("X", fields, market="US")
        for key in ("score", "grade", "confidence", "strengths", "weaknesses", "risks", "explanation", "metadata"):
            assert key in result
