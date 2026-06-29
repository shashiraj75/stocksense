"""
Unit tests for india_valuation_adapter.py / us_valuation_adapter.py
(Epic 004 Sprint #003). Hand-built provider-shaped dicts — no live data.
"""

import pytest

from services.india_valuation_adapter import build_india_valuation_fields
from services.us_valuation_adapter import build_us_valuation_fields


@pytest.mark.unit
class TestIndiaValuationAdapter:
    def test_empty_inputs_return_empty_dict(self):
        assert build_india_valuation_fields({}, {}) == {}

    def test_unavailable_screener_with_no_info_returns_empty(self):
        assert build_india_valuation_fields({"available": False}, {}) == {}

    def test_prefers_yfinance_forward_pe_and_payout_ratio(self):
        """The Sprint #002 finding this adapter exists to apply: forward_pe
        and payout_ratio come from yfinance, never from screener.in."""
        screener_data = {"available": True, "pe_ratio": 18.0, "market_cap_cr": 50000.0}
        info = {"forwardPE": 15.5, "payoutRatio": 0.42, "priceToBook": 2.1}
        fields = build_india_valuation_fields(screener_data, info)
        assert fields["forward_pe"]["value"] == 15.5
        assert fields["payout_ratio"]["value"] == 0.42
        assert fields["price_book"]["value"] == 2.1

    def test_pe_ratio_prefers_screener_falls_back_to_yfinance(self):
        screener_data = {"available": True, "pe_ratio": 18.0}
        info = {"trailingPE": 19.0}
        fields = build_india_valuation_fields(screener_data, info)
        assert fields["pe_ratio"]["value"] == 18.0

        fields2 = build_india_valuation_fields({"available": False}, {"trailingPE": 19.0})
        assert fields2["pe_ratio"]["value"] == 19.0

    def test_ev_ebitda_gap_for_bank_passes_none_not_fabricated(self):
        """Banks structurally lack EV/EBITDA in both providers (Sprint
        #002 evidence) — must be None, never a fabricated 0 or guess."""
        screener_data = {"available": True, "pe_ratio": 16.0}
        info = {"trailingPE": 16.0}  # no enterpriseToEbitda field, as confirmed for banks
        fields = build_india_valuation_fields(screener_data, info)
        assert fields["ev_ebitda"] is None

    def test_fcf_yield_prefers_yfinance_direct_field(self):
        screener_data = {"available": True}
        info = {"freeCashflow": 1000.0, "marketCap": 50000.0}
        fields = build_india_valuation_fields(screener_data, info)
        assert fields["fcf_yield_pct"]["value"] == 2.0

    def test_fcf_yield_falls_back_to_screener_approximation(self):
        screener_data = {
            "available": True,
            "operating_cf_annual_cr": [100.0, 120.0, 150.0],
            "investing_cf_latest_cr": -30.0,
            "market_cap_cr": 6000.0,
        }
        fields = build_india_valuation_fields(screener_data, {})
        # (150 - 30) / 6000 * 100 = 2.0
        assert fields["fcf_yield_pct"]["value"] == 2.0

    def test_peg_prefers_yfinance_then_falls_back_to_screener_growth(self):
        screener_data = {"available": True, "pe_ratio": 20.0, "profit_growth_3y_pct": 10.0}
        info_with_peg = {"trailingPegRatio": 1.5}
        fields = build_india_valuation_fields(screener_data, info_with_peg)
        assert fields["peg_ratio"]["value"] == 1.5

        fields2 = build_india_valuation_fields(screener_data, {})
        assert fields2["peg_ratio"]["value"] == 2.0  # 20 / 10

    def test_peg_none_when_growth_unavailable_or_negative(self):
        screener_data = {"available": True, "pe_ratio": 20.0, "profit_growth_3y_pct": -5.0}
        fields = build_india_valuation_fields(screener_data, {})
        assert fields["peg_ratio"] is None


@pytest.mark.unit
class TestUSValuationAdapter:
    def test_empty_info_returns_empty_dict(self):
        assert build_us_valuation_fields({}) == {}
        assert build_us_valuation_fields(None) == {}

    def test_maps_all_fields_directly_from_info(self):
        info = {
            "trailingPE": 21.9, "forwardPE": 18.2, "enterpriseToRevenue": 1.99,
            "priceToBook": 1.96, "enterpriseToEbitda": 12.3, "dividendYield": 0.46,
            "payoutRatio": 0.09, "marketCap": 1.7e13, "trailingPegRatio": None,
            "freeCashflow": 2.18e11,
        }
        fields = build_us_valuation_fields(info)
        assert fields["pe_ratio"]["value"] == 21.9
        assert fields["forward_pe"]["value"] == 18.2
        assert fields["ev_sales"]["value"] == 1.99
        assert fields["price_book"]["value"] == 1.96
        assert fields["ev_ebitda"]["value"] == 12.3
        assert fields["dividend_yield_pct"]["value"] == 0.46
        assert fields["payout_ratio"]["value"] == 0.09
        assert fields["peg_ratio"] is None
        assert fields["fcf_yield_pct"]["value"] is not None

    def test_missing_fields_pass_none_not_fabricated(self):
        info = {"trailingPE": 21.9}
        fields = build_us_valuation_fields(info)
        assert fields["forward_pe"] is None
        assert fields["ev_ebitda"] is None
        assert fields["fcf_yield_pct"] is None
