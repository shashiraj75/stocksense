"""
Integration tests for the US Financial Strength Adapter
(services/us_financial_strength_adapter.py, Epic 002 Sprint #008) —
exercises the full pipeline (EDGAR fetch -> yfinance fetch -> sector
classification -> precedence resolution -> engine scoring) together,
with no live network calls per pytest.ini's own integration definition.
"""

import pandas as pd
import pytest

import services.us_financial_strength_adapter as fsa


class _FakeTicker:
    def __init__(self, info=None, balance_sheet=None, cashflow=None, financials=None):
        self.info = info or {}
        self.balance_sheet = balance_sheet if balance_sheet is not None else pd.DataFrame()
        self.cashflow = cashflow if cashflow is not None else pd.DataFrame()
        self.financials = financials if financials is not None else pd.DataFrame()


def _healthy_it_company_ticker():
    info = {
        "sector": "Technology", "industry": "Software",
        "totalRevenue": 50_000_000_000.0, "totalCash": 4_000_000_000.0,
        "totalDebt": 10_000_000_000.0, "freeCashflow": 5_500_000_000.0,
        "operatingCashflow": 8_500_000_000.0, "longName": "Healthy IT Co",
    }
    bs = pd.DataFrame(
        {"FY1": [12_000_000_000.0, 7_000_000_000.0, 60_000_000_000.0, 35_000_000_000.0,
                 2_000_000_000.0, 8_000_000_000.0, 25_000_000_000.0]},
        index=["Current Assets", "Current Liabilities", "Total Assets", "Total Liabilities Net Minority Interest",
               "Current Debt", "Long Term Debt", "Stockholders Equity"],
    )
    cf = pd.DataFrame(
        {"FY1": [8_500_000_000.0, 3_000_000_000.0, 5_500_000_000.0]},
        index=["Operating Cash Flow", "Capital Expenditure", "Free Cash Flow"],
    )
    fin = pd.DataFrame(
        {"FY1": [9_000_000_000.0, 500_000_000.0, 6_000_000_000.0]},
        index=["EBIT", "Interest Expense", "Net Income"],
    )
    return _FakeTicker(info=info, balance_sheet=bs, cashflow=cf, financials=fin)


def _bank_ticker():
    info = {"sector": "Financial Services", "industry": "Banks", "totalDebt": 1_000_000_000.0,
            "totalCash": 50_000_000_000.0, "operatingCashflow": -1_000_000_000.0, "longName": "Test Bank"}
    return _FakeTicker(info=info)


@pytest.mark.integration
def test_full_pipeline_scores_a_healthy_non_excluded_company(monkeypatch):
    monkeypatch.setattr(fsa.sea, "fetch_us_fundamentals_sec_edgar", lambda sym: {"available": False})
    monkeypatch.setattr(fsa.yf, "Ticker", lambda sym: _healthy_it_company_ticker())

    result = fsa.compute_us_financial_strength("TESTCO")

    assert result["grade"] != "rejected"
    assert result["metadata"]["sector_bucket"] == "IT"
    assert result["metadata"]["data_completeness_pct"] == 100.0
    assert result["score"] > 50


@pytest.mark.integration
def test_full_pipeline_excludes_financial_sector_via_real_classification(monkeypatch):
    """Confirms sector exclusion is reached through the REAL
    classify_sector() call (not a mocked sector), end-to-end."""
    monkeypatch.setattr(fsa.sea, "fetch_us_fundamentals_sec_edgar", lambda sym: {"available": False})
    monkeypatch.setattr(fsa.yf, "Ticker", lambda sym: _bank_ticker())

    result = fsa.compute_us_financial_strength("TESTBANK")

    assert result["grade"] == "rejected"
    assert result["metadata"]["sector_bucket"] == "FINANCIAL"
    assert result["metadata"]["rejection_reason"] == "sector_not_yet_supported"


@pytest.mark.integration
def test_edgar_failure_falls_back_to_yfinance_through_full_pipeline(monkeypatch):
    """EDGAR totally unavailable (e.g. CIK resolution failed) must not
    break the pipeline — yfinance-only resolution should still produce
    a real score, per Fail-Soft Engineering."""
    monkeypatch.setattr(fsa.sea, "fetch_us_fundamentals_sec_edgar",
                         lambda sym: {"available": False, "reason": "CIK not found"})
    monkeypatch.setattr(fsa.yf, "Ticker", lambda sym: _healthy_it_company_ticker())

    result = fsa.compute_us_financial_strength("TESTCO")

    assert result["grade"] != "rejected"
    for field_name, rec in result["metadata"].items():
        pass  # no exception raised is itself the primary assertion here
    assert result["metadata"]["data_completeness_pct"] == 100.0


@pytest.mark.integration
def test_yfinance_failure_falls_back_to_edgar_through_full_pipeline(monkeypatch):
    """yfinance totally unavailable must not break the pipeline either —
    confirms symmetry of the fallback behavior."""
    edgar_result = {
        "available": True,
        "cik": 123,
        "company_name": "Test EDGAR Co",
        "fields": {
            "revenue": {"value": 50_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "net_income": {"value": 6_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "ebit": {"value": 9_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "interest_expense": {"value": 500_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "cash_and_equivalents": {"value": 4_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "current_assets": {"value": 12_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "current_liabilities": {"value": 7_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "total_assets": {"value": 60_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "total_liabilities": {"value": 35_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "short_term_debt": {"value": 2_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "long_term_debt": {"value": 8_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "total_debt": {"value": 10_000_000_000.0, "confidence": 0.8, "derivation_status": "DERIVED"},
            "operating_cash_flow": {"value": 8_500_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "capital_expenditure": {"value": 3_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
            "free_cash_flow": {"value": 5_500_000_000.0, "confidence": 0.8, "derivation_status": "DERIVED"},
            "shareholders_equity": {"value": 25_000_000_000.0, "confidence": 0.95, "derivation_status": "DIRECT"},
        },
    }

    def _raise_ticker(sym):
        raise RuntimeError("simulated yfinance outage")

    monkeypatch.setattr(fsa.sea, "fetch_us_fundamentals_sec_edgar", lambda sym: edgar_result)
    monkeypatch.setattr(fsa.yf, "Ticker", _raise_ticker)

    result = fsa.compute_us_financial_strength("TESTCO")

    # With yfinance unavailable, sector classification falls back to
    # "OTHER" (no .info to read sector/industry from) -- not excluded,
    # so the engine should still score using EDGAR-only data.
    assert result["metadata"]["sector_bucket"] == "OTHER"
    assert result["grade"] != "rejected"
    assert result["metadata"]["data_completeness_pct"] == 100.0


@pytest.mark.integration
def test_build_fields_resolves_every_unified_field(monkeypatch):
    monkeypatch.setattr(fsa.sea, "fetch_us_fundamentals_sec_edgar", lambda sym: {"available": False})
    monkeypatch.setattr(fsa.yf, "Ticker", lambda sym: _healthy_it_company_ticker())

    built = fsa.build_us_financial_strength_fields("TESTCO")

    assert built["symbol"] == "TESTCO"
    assert built["company_name"] == "Healthy IT Co"
    assert set(built["fields"].keys()) == set(fsa.upp.FIELD_PRECEDENCE.keys())
    for field_name, resolved in built["fields"].items():
        assert "value" in resolved
        assert "chosen_source" in resolved
