"""
Unit tests for the additive Business Quality Engine block added to
services/us_fundamentals.py's _build() (Sprint #005).
"""

import pandas as pd
import pytest


class _FakeTicker:
    """Minimal ticker stand-in — enough for _build() to not crash before
    reaching the Business Quality Engine block under test."""
    info = {"regularMarketPrice": 100.0, "currentPrice": 100.0}
    financials = pd.DataFrame()
    balance_sheet = pd.DataFrame()
    cashflow = pd.DataFrame()
    dividends = pd.Series(dtype=float)
    actions = pd.DataFrame()


@pytest.mark.unit
def test_business_quality_failure_does_not_break_existing_fetch(monkeypatch):
    """If compute_business_quality raises for any reason, _build() must
    still return its existing fields (available=True, etc.) — the new
    block degrades to None/None/None, never an exception that breaks the
    function callers have always relied on."""
    import services.us_fundamentals as usf

    def _raises(*args, **kwargs):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr("services.business_quality_engine.compute_business_quality", _raises)
    monkeypatch.setattr(usf.yf, "Ticker", lambda sym: _FakeTicker())

    result = usf._build("FAKESYM")

    assert result["available"] is True
    assert result.get("business_quality_score") is None
    assert result.get("business_quality_grade") is None
    assert result.get("business_quality_style") is None


@pytest.mark.unit
def test_business_quality_fields_populated_on_success(monkeypatch):
    import services.us_fundamentals as usf

    def _fake_compute(symbol, ticker, df, info, market="US"):
        return {
            "score": 80, "grade": "strong_buy",
            "metadata": {"suitable_investment_style": "Quality Compounder"},
        }

    monkeypatch.setattr("services.business_quality_engine.compute_business_quality", _fake_compute)
    monkeypatch.setattr(usf.yf, "Ticker", lambda sym: _FakeTicker())

    result = usf._build("FAKESYM")

    assert result["business_quality_score"] == 80
    assert result["business_quality_grade"] == "strong_buy"
    assert result["business_quality_style"] == "Quality Compounder"


@pytest.mark.unit
def test_empty_dataframe_passed_for_df_not_a_new_network_call(monkeypatch):
    """Confirms the documented design decision: df is an empty DataFrame,
    not a fetched price history — no ticker.history() call should occur."""
    import services.us_fundamentals as usf

    captured = {}

    def _capture(symbol, ticker, df, info, market="US"):
        captured["df"] = df
        return {"score": 50, "grade": "hold", "metadata": {}}

    monkeypatch.setattr("services.business_quality_engine.compute_business_quality", _capture)
    monkeypatch.setattr(usf.yf, "Ticker", lambda sym: _FakeTicker())

    usf._build("FAKESYM")

    assert isinstance(captured["df"], pd.DataFrame)
    assert captured["df"].empty
