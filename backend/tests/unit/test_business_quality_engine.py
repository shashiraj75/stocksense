"""
Unit tests for services/business_quality_engine.py (SSDS-003, Sprint #004).

Covers the new metric helpers in isolation (cash conversion, asset
turnover, working capital trend, Beneish M-Score) and compute_business_quality's
hard-gate / grade-banding / EngineResponse-shape behavior.
"""

import pandas as pd
import pytest

from services.business_quality_engine import (
    compute_business_quality,
    _compute_cash_conversion,
    _compute_asset_turnover,
    _compute_working_capital_trend,
    _compute_beneish_m_score,
    _map_subscore,
)
from services.engine_contract import Grade


class TestMapSubscore:
    @pytest.mark.unit
    def test_neutral_score_maps_to_zero_contribution(self):
        assert _map_subscore(50, cap=10) == 0.0

    @pytest.mark.unit
    def test_max_score_maps_to_full_cap(self):
        assert _map_subscore(100, cap=10) == 10.0

    @pytest.mark.unit
    def test_min_score_maps_to_negative_cap(self):
        assert _map_subscore(0, cap=10) == -10.0

    @pytest.mark.unit
    def test_none_score_maps_to_zero(self):
        assert _map_subscore(None, cap=10) == 0.0


class TestCashConversion:
    @pytest.mark.unit
    def test_strong_conversion_scores_high(self, base_info):
        info = dict(base_info, operatingCashflow=900_000, netIncome=1_000_000)
        result = _compute_cash_conversion(info)
        assert result["ratio"] == pytest.approx(0.9)
        assert result["score"] == 65

    @pytest.mark.unit
    def test_weak_conversion_scores_low(self, base_info):
        info = dict(base_info, operatingCashflow=300_000, netIncome=1_000_000)
        result = _compute_cash_conversion(info)
        assert result["score"] == 35

    @pytest.mark.unit
    def test_missing_net_income_returns_none(self, base_info):
        info = dict(base_info)
        info.pop("operatingCashflow", None)
        info["netIncome"] = None
        result = _compute_cash_conversion(info)
        assert result["ratio"] is None
        assert result["score"] is None

    @pytest.mark.unit
    def test_zero_or_negative_net_income_returns_none_not_a_crash(self, base_info):
        """Edge case: a loss-making business — division by a non-positive
        net income must degrade gracefully, not raise or return nonsense."""
        info = dict(base_info, operatingCashflow=900_000, netIncome=-500_000)
        result = _compute_cash_conversion(info)
        assert result["ratio"] is None
        assert result["score"] is None


class TestAssetTurnover:
    @pytest.mark.unit
    def test_high_turnover_scores_above_neutral(self, base_info, mock_ticker_two_year_financials):
        info = dict(base_info, totalRevenue=3_000_000_000)
        result = _compute_asset_turnover(info, mock_ticker_two_year_financials)
        assert result["turnover"] > 1.0
        assert result["score"] == 60

    @pytest.mark.unit
    def test_missing_revenue_returns_none(self, mock_ticker_two_year_financials):
        result = _compute_asset_turnover({}, mock_ticker_two_year_financials)
        assert result["turnover"] is None

    @pytest.mark.unit
    def test_empty_balance_sheet_returns_none_not_a_crash(self, base_info, mock_ticker):
        """Edge case: recent IPO / incomplete statements — empty balance_sheet."""
        result = _compute_asset_turnover(base_info, mock_ticker)
        assert result["turnover"] is None
        assert result["score"] is None


class TestWorkingCapitalTrend:
    @pytest.mark.unit
    def test_improving_trend_detected(self, mock_ticker_two_year_financials):
        result = _compute_working_capital_trend(mock_ticker_two_year_financials)
        assert result["score"] == 58

    @pytest.mark.unit
    def test_empty_balance_sheet_returns_none_not_a_crash(self, mock_ticker):
        result = _compute_working_capital_trend(mock_ticker)
        assert result["score"] is None


class TestBeneishMScore:
    @pytest.mark.unit
    def test_clean_financials_below_manipulation_threshold(self, mock_ticker_two_year_financials):
        result = _compute_beneish_m_score(mock_ticker_two_year_financials)
        assert result["m_score"] is not None
        assert result["m_score"] < -1.78
        assert result["reason"] is None

    @pytest.mark.unit
    def test_missing_financials_returns_unavailable_not_a_crash(self, mock_ticker):
        """Edge case: incomplete statements — must not guess a number."""
        result = _compute_beneish_m_score(mock_ticker)
        assert result["m_score"] is None
        assert result["reason"] is None


class TestComputeBusinessQualityInsufficientData:
    @pytest.mark.unit
    def test_mostly_empty_info_returns_rejected(self, mock_ticker):
        """Edge case: missing data — well under the 60% mandatory-metric
        completeness bar must produce REJECTED/insufficient_data, never a
        guessed low score."""
        df = pd.DataFrame()
        result = compute_business_quality("TEST", mock_ticker, df, {}, market="US")
        assert result["grade"] == Grade.REJECTED.value
        assert result["metadata"]["rejection_reason"] == "insufficient_data"
        assert result["score"] == 0


class TestComputeBusinessQualityHardGate:
    @pytest.mark.unit
    def test_distress_and_aggressive_accruals_rejects(self, business_quality_info, mock_ticker_two_year_financials, monkeypatch):
        """Hard gate (SSDS-003 §2): Altman distress AND aggressive Sloan
        accruals simultaneously must reject outright, not just penalize."""
        import services.business_quality_engine as bqe

        monkeypatch.setattr(bqe, "altman_zscore_signal", lambda info: {
            "score": 20, "reasons": ["Altman Z-Score 1.00 — Distress Zone"], "z_score": 1.0, "z_zone": "distress",
        })
        monkeypatch.setattr(bqe, "sloan_accruals_signal", lambda info: {
            "score": 30, "reasons": ["High accruals"], "accruals_ratio": 0.15,
        })

        df = pd.DataFrame({"Close": [100, 101, 102]})
        result = compute_business_quality("TEST", mock_ticker_two_year_financials, df, business_quality_info, market="US")
        assert result["grade"] == Grade.REJECTED.value
        assert result["metadata"]["rejection_reason"] == "distress_and_aggressive_accruals"

    @pytest.mark.unit
    def test_beneish_flag_rejects_independent_of_other_categories(self, business_quality_info, mock_ticker_two_year_financials, monkeypatch):
        import services.business_quality_engine as bqe

        monkeypatch.setattr(bqe, "_compute_beneish_m_score", lambda ticker: {
            "m_score": -1.0, "reason": "Beneish M-Score -1.00 — above the manipulation-likelihood threshold",
        })

        df = pd.DataFrame({"Close": [100, 101, 102]})
        result = compute_business_quality("TEST", mock_ticker_two_year_financials, df, business_quality_info, market="US")
        assert result["grade"] == Grade.REJECTED.value
        assert result["metadata"]["rejection_reason"] == "fraud_risk"

    @pytest.mark.unit
    def test_financial_sector_exempt_from_distress_gate(self, financial_sector_info, mock_ticker_two_year_financials, monkeypatch):
        """Banks are exempt from the distress+accruals hard gate, matching
        the same is_financial rationale already established elsewhere."""
        import services.business_quality_engine as bqe

        monkeypatch.setattr(bqe, "altman_zscore_signal", lambda info: {
            "score": 20, "reasons": [], "z_score": 1.0, "z_zone": "distress",
        })
        monkeypatch.setattr(bqe, "sloan_accruals_signal", lambda info: {
            "score": 30, "reasons": [], "accruals_ratio": 0.15,
        })

        df = pd.DataFrame({"Close": [100, 101, 102]})
        result = compute_business_quality("TEST.NS", mock_ticker_two_year_financials, df, financial_sector_info, market="IN")
        assert result["grade"] != Grade.REJECTED.value


class TestComputeBusinessQualityGradeBanding:
    @pytest.mark.unit
    def test_healthy_business_quality_info_produces_a_valid_response_shape(self, business_quality_info, mock_ticker_two_year_financials):
        df = pd.DataFrame({"Close": [100.0] * 30})
        result = compute_business_quality("TEST", mock_ticker_two_year_financials, df, business_quality_info, market="US")

        assert 0 <= result["score"] <= 100
        assert result["grade"] in [g.value for g in Grade]
        assert 0 <= result["confidence"] <= 100
        assert "category_contributions" in result["metadata"]
        assert set(result["metadata"]["category_contributions"].keys()) == {
            "profitability_capital_efficiency", "balance_sheet_strength", "earnings_quality",
            "capital_allocation_shareholder_treatment", "durable_competitive_position",
        }
        assert "suitable_investment_style" in result["metadata"]
        assert "suggested_holding_horizon" in result["metadata"]

    @pytest.mark.unit
    def test_zero_revenue_does_not_crash(self, business_quality_info, mock_ticker_two_year_financials):
        """Edge case: zero revenue — division guards must hold throughout
        the engine, not just in one helper."""
        info = dict(business_quality_info, totalRevenue=0, netIncome=0)
        df = pd.DataFrame({"Close": [100.0] * 30})
        result = compute_business_quality("TEST", mock_ticker_two_year_financials, df, info, market="US")
        assert isinstance(result["score"], int)

    @pytest.mark.unit
    def test_extreme_leverage_penalizes_balance_sheet_category(self, business_quality_info, mock_ticker_two_year_financials):
        """Edge case: extreme leverage — non-financial sector should be
        penalized in Balance Sheet Strength, not exempted."""
        info = dict(business_quality_info, debtToEquity=450.0)
        df = pd.DataFrame({"Close": [100.0] * 30})
        result = compute_business_quality("TEST", mock_ticker_two_year_financials, df, info, market="US")
        assert result["metadata"]["category_contributions"]["balance_sheet_strength"] <= 0

    @pytest.mark.unit
    def test_negative_roe_and_roce_penalize_profitability(self, business_quality_info, mock_ticker_two_year_financials):
        """Edge case: negative values."""
        info = dict(business_quality_info, returnOnEquity=-0.20, returnOnCapitalEmployed=-0.05)
        df = pd.DataFrame({"Close": [100.0] * 30})
        result = compute_business_quality("TEST", mock_ticker_two_year_financials, df, info, market="US")
        assert result["metadata"]["category_contributions"]["profitability_capital_efficiency"] < 0

    @pytest.mark.unit
    def test_recent_ipo_incomplete_statements_does_not_crash(self, business_quality_info, mock_ticker):
        """Edge case: recent IPO — empty financials/balance_sheet/cashflow,
        no dividend history. Must degrade gracefully (lower confidence),
        never raise."""
        df = pd.DataFrame({"Close": [100.0] * 5})
        result = compute_business_quality("NEWCO", mock_ticker, df, business_quality_info, market="US")
        assert isinstance(result["score"], int)
        assert result["confidence"] <= 100
