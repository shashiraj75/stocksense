"""
Unit tests for PredictionEngine._quality_gate — the hard reject/accept gate
run before any stock is scored (services/prediction_engine.py).

Pattern for future unit tests: instantiate the engine, call the private
method directly with a fixture `info` dict, assert on the (passed, reasons)
tuple. No network calls, no Postgres — pure logic only.
"""

import pandas as pd
import pytest

from services.prediction_engine import PredictionEngine


@pytest.fixture
def engine():
    return PredictionEngine()


@pytest.fixture
def empty_df():
    return pd.DataFrame()


class TestQualityGateAcceptsHealthyStock:
    @pytest.mark.unit
    def test_healthy_stock_passes(self, engine, base_info, empty_df):
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
        assert passed is True
        assert reasons == []


class TestQualityGateRejectsBrokenFinancials:
    @pytest.mark.unit
    def test_severely_negative_roe_rejected(self, engine, base_info, empty_df):
        base_info["returnOnEquity"] = -0.25
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
        assert passed is False
        assert any("ROE" in r for r in reasons)

    @pytest.mark.unit
    def test_extreme_leverage_rejected(self, engine, base_info, empty_df):
        base_info["debtToEquity"] = 600.0
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
        assert passed is False
        assert any("leverage" in r.lower() for r in reasons)

    @pytest.mark.unit
    def test_non_positive_ocf_rejected_for_medium_horizon(self, engine, base_info, empty_df):
        base_info["operatingCashflow"] = -1.0
        base_info["operatingCashflows"] = -1.0
        # Must also fail the turnaround exception to actually reject — drop growth too.
        base_info["revenueGrowth"] = 0.02
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
        assert passed is False
        assert any("cash flow" in r.lower() for r in reasons)


class TestQualityGateHorizonSpecificOcfCheck:
    """Documents a genuine horizon-specific *logic* difference (not just a
    weight) flagged in SEAR-001 Section 5: the OCF hard-reject is skipped
    entirely for horizon == 'short'."""

    @pytest.mark.unit
    def test_negative_ocf_exempt_for_short_horizon(self, engine, base_info, empty_df):
        base_info["operatingCashflow"] = -1.0
        base_info["operatingCashflows"] = -1.0
        base_info["revenueGrowth"] = 0.02
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="short")
        assert passed is True

    @pytest.mark.unit
    def test_negative_ocf_rejected_for_medium_horizon_with_same_inputs(self, engine, base_info, empty_df):
        base_info["operatingCashflow"] = -1.0
        base_info["operatingCashflows"] = -1.0
        base_info["revenueGrowth"] = 0.02
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
        assert passed is False


class TestQualityGateFinancialSectorExemption:
    """Banks/NBFCs structurally violate the OCF and leverage checks for
    Ind-AS accounting reasons (loans disbursed count as operating outflows)
    — they're exempt by design, not a loophole."""

    @pytest.mark.unit
    def test_bank_with_negative_ocf_and_high_leverage_not_rejected(self, engine, financial_sector_info, empty_df):
        passed, reasons = engine._quality_gate(financial_sector_info, empty_df, horizon="medium")
        assert passed is True
        assert reasons == []


class TestQualityGateOrderBookTurnaroundException:
    """Regression coverage for the order-book exception added this engagement
    (HFCL / Apollo Micro Systems / ideaForge case) — see
    Documentation/Engineering-Handbook/Architecture/Sprint-001-Selection-Engine-Audit.md
    Section 3, Cash Flow. Negative OCF should NOT auto-reject when revenue
    growth is strong AND leverage is contained AND (ROCE is decent OR
    execution is improving)."""

    @pytest.mark.unit
    def test_turnaround_exception_applies_with_strong_growth_and_good_roce(self, engine, base_info, empty_df):
        base_info["operatingCashflow"] = -1.0
        base_info["operatingCashflows"] = -1.0
        base_info["revenueGrowth"] = 0.20       # > 0.15 threshold
        base_info["debtToEquity"] = 100.0       # < 150 threshold
        base_info["returnOnCapitalEmployed"] = 0.10  # > 0.08 threshold
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
        assert passed is True

    @pytest.mark.unit
    def test_turnaround_exception_does_not_apply_with_weak_growth(self, engine, base_info, empty_df):
        base_info["operatingCashflow"] = -1.0
        base_info["operatingCashflows"] = -1.0
        base_info["revenueGrowth"] = 0.05       # below 0.15 threshold — no exception
        base_info["returnOnCapitalEmployed"] = 0.10
        passed, reasons = engine._quality_gate(base_info, empty_df, horizon="medium")
        assert passed is False
