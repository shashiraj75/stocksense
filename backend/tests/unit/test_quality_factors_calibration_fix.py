"""
Unit tests for the calibration fix made to quality_factors.py's
altman_zscore_signal / sloan_accruals_signal following the Business
Quality Engine Production Readiness Validation (Architecture/Business-
Quality-Engine-Production-Readiness-Validation.md, Phase 6 Finding A).

Confirmed via live yfinance calls: info.get("totalAssets") is never
populated in yfinance's .info dict for any ticker, in either market —
so both functions always returned z_score/accruals_ratio = None against
real data before this fix, regardless of what else was in `info`. The
fix adds an optional `ticker` parameter and a fallback to
ticker.balance_sheet's "Total Assets" row when info lacks the field.
"""

import pytest

from services.quality_factors import altman_zscore_signal, sloan_accruals_signal, _total_assets_fallback


class TestTotalAssetsFallback:
    @pytest.mark.unit
    def test_returns_none_when_ticker_is_none(self):
        assert _total_assets_fallback(None) is None

    @pytest.mark.unit
    def test_returns_none_for_empty_balance_sheet(self, mock_ticker):
        assert _total_assets_fallback(mock_ticker) is None

    @pytest.mark.unit
    def test_returns_latest_total_assets_from_balance_sheet(self, mock_ticker_two_year_financials):
        result = _total_assets_fallback(mock_ticker_two_year_financials)
        assert result == 2_150_000_000.0


class TestAltmanZScoreFallback:
    @pytest.mark.unit
    def test_no_ticker_preserves_old_unavailable_behavior(self, base_info):
        """Backward compatibility: a caller that doesn't pass ticker (or
        passes None) gets exactly the pre-fix behavior — unavailable when
        info lacks totalAssets, never a behavior change for existing
        callers that don't opt in."""
        info = dict(base_info)
        info.pop("totalAssets", None)
        result = altman_zscore_signal(info)
        assert result["z_score"] is None
        assert result["z_zone"] == "unavailable"

    @pytest.mark.unit
    def test_ticker_fallback_computes_a_real_score(self, base_info, mock_ticker_two_year_financials):
        """The fix: with a ticker supplied and info lacking totalAssets,
        a real Altman Z-Score now computes instead of unavailable."""
        info = dict(base_info)
        info.pop("totalAssets", None)
        result = altman_zscore_signal(info, mock_ticker_two_year_financials)
        assert result["z_score"] is not None
        assert result["z_zone"] != "unavailable"

    @pytest.mark.unit
    def test_info_totalassets_takes_priority_over_ticker_fallback(self, business_quality_info, mock_ticker_two_year_financials):
        """If info already has totalAssets (e.g. a future data source
        populates it), the fallback must not override it. Uses
        business_quality_info (not bare base_info) because it has
        marketCap/ebit/retainedEarnings/totalRevenue populated — without
        those, every X-term except the one driven by total_assets
        defaults to 0, and the comparison below can't distinguish the two
        total_assets values from each other."""
        info = dict(business_quality_info, totalAssets=999_000_000)
        result = altman_zscore_signal(info, mock_ticker_two_year_financials)
        # A materially different total assets value (999M vs the fixture's
        # 2.15B) must produce a different Z-Score than the fallback would.
        result_with_fallback_value = altman_zscore_signal(
            dict(business_quality_info, totalAssets=2_150_000_000.0), mock_ticker_two_year_financials
        )
        assert result["z_score"] != result_with_fallback_value["z_score"]


class TestSloanAccrualsFallback:
    @pytest.mark.unit
    def test_no_ticker_preserves_old_unavailable_behavior(self, base_info):
        info = dict(base_info)
        info.pop("totalAssets", None)
        result = sloan_accruals_signal(info)
        assert result["accruals_ratio"] is None

    @pytest.mark.unit
    def test_ticker_fallback_computes_a_real_ratio(self, base_info, mock_ticker_two_year_financials):
        info = dict(base_info, netIncome=170_000_000, operatingCashflow=200_000_000)
        info.pop("totalAssets", None)
        result = sloan_accruals_signal(info, mock_ticker_two_year_financials)
        assert result["accruals_ratio"] is not None
