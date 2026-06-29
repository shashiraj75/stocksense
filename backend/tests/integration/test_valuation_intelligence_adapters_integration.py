"""
Integration tests for Valuation Intelligence's adapters wired to the
engine (Epic 004 Sprint #003) — confirms india_valuation_adapter.py and
us_valuation_adapter.py each correctly shape their respective provider
data into fields compute_valuation_intelligence() can score, using
realistic synthetic fixtures (no live network calls).
"""

import pytest

from services.engine_contract import Grade
from services.valuation_intelligence_engine import compute_valuation_intelligence
from services.india_valuation_adapter import build_india_valuation_fields
from services.us_valuation_adapter import build_us_valuation_fields


@pytest.mark.integration
class TestIndiaValuationAdapterIntegration:
    def _undervalued_screener_data(self):
        return {
            "available": True, "pe_ratio": 11.0, "price_to_sales": 1.2, "ev_ebitda": 6.5,
            "dividend_yield_pct": 3.5, "market_cap_cr": 50000.0,
            "operating_cf_annual_cr": [800, 950, 1100], "investing_cf_latest_cr": -200,
            "profit_growth_3y_pct": 14.0,
        }

    def _undervalued_info(self):
        return {"forwardPE": 10.0, "payoutRatio": 0.45, "priceToBook": 1.4, "trailingPE": 11.0,
                "sector": "Energy"}

    def test_undervalued_dividend_payer_scores_strong(self):
        fields = build_india_valuation_fields(self._undervalued_screener_data(), self._undervalued_info())
        result = compute_valuation_intelligence("UNDERVALUED", fields, sector_bucket="UTILITIES_ENERGY", market="IN")
        assert result["score"] >= 65
        assert result["grade"] in (Grade.STRONG_BUY.value, Grade.BUY.value)

    def test_bank_gracefully_skips_ev_ebitda_fcf_peg(self):
        """Per the India Data Feasibility Study's confirmed finding: banks
        structurally lack EV/EBITDA-shaped statements in both providers.
        Must not reject, must not fabricate, must not penalize."""
        bank_screener = {
            "available": True, "pe_ratio": 16.0, "dividend_yield_pct": 1.5, "market_cap_cr": 300000.0,
        }
        bank_info = {"trailingPE": 16.0, "priceToBook": 3.5, "payoutRatio": 0.20}
        fields = build_india_valuation_fields(bank_screener, bank_info)
        assert fields["ev_ebitda"] is None
        assert fields["fcf_yield_pct"] is None
        result = compute_valuation_intelligence("BANK", fields, sector_bucket="FINANCIAL", market="IN")
        assert result["grade"] != Grade.REJECTED.value
        assert result["metadata"]["category_contributions"]["EV/EBITDA"] == 0.0
        assert result["metadata"]["category_contributions"]["Price/Book"] != 0.0  # applicable for FINANCIAL, P/B=3.5 is "expensive"

    def test_overvalued_no_dividend_scores_weak(self):
        screener_data = {
            "available": True, "pe_ratio": 60.0, "price_to_sales": 9.0, "dividend_yield_pct": 0.0,
            "market_cap_cr": 200000.0,
        }
        info = {"forwardPE": 55.0}
        fields = build_india_valuation_fields(screener_data, info)
        result = compute_valuation_intelligence("RICH", fields, sector_bucket="IT", market="IN")
        assert result["score"] < 50
        assert result["grade"] in (Grade.SELL.value, Grade.AVOID.value, Grade.WATCH.value)


@pytest.mark.integration
class TestUSValuationAdapterIntegration:
    def _undervalued_info(self):
        return {
            "trailingPE": 9.0, "forwardPE": 8.5, "enterpriseToRevenue": 1.0, "priceToBook": 0.9,
            "enterpriseToEbitda": 6.0, "dividendYield": 3.8, "payoutRatio": 0.50,
            "marketCap": 1e11, "freeCashflow": 9e9, "trailingPegRatio": 0.8,
        }

    def test_undervalued_us_company_scores_strong(self):
        fields = build_us_valuation_fields(self._undervalued_info())
        result = compute_valuation_intelligence("CHEAPCO", fields, sector_bucket="FINANCIAL", market="US")
        assert result["score"] >= 65
        assert result["grade"] in (Grade.STRONG_BUY.value, Grade.BUY.value)
        assert result["confidence"] == 100.0

    def test_missing_info_does_not_crash(self):
        fields = build_us_valuation_fields({})
        result = compute_valuation_intelligence("EMPTY", fields, market="US")
        assert result["grade"] == Grade.REJECTED.value
