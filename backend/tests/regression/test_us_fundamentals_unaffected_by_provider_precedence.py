"""
Regression test: introducing services/us_provider_precedence.py (SSDS-006
Sprint #006) must not change services/us_fundamentals.py's or
services/sec_edgar_adapter.py's existing behavior in any way -- this
sprint's module is a standalone, additive precedence decision, not yet
wired into any engine, provider, or consumer.

Mirrors the exact proof pattern Sprint #004's
test_us_fundamentals_unaffected_by_sec_edgar_adapter.py already used for
the prior additive change.
"""

import pandas as pd
import pytest


class _FakeTicker:
    info = {"regularMarketPrice": 100.0, "currentPrice": 100.0, "trailingPE": 24.5}
    financials = pd.DataFrame()
    balance_sheet = pd.DataFrame()
    cashflow = pd.DataFrame()
    dividends = pd.Series(dtype=float)
    actions = pd.DataFrame()


@pytest.mark.regression
def test_us_fundamentals_module_does_not_import_provider_precedence():
    import services.us_fundamentals as usf
    assert "us_provider_precedence" not in usf.__dict__
    assert not hasattr(usf, "us_provider_precedence")


@pytest.mark.regression
def test_sec_edgar_adapter_module_does_not_import_provider_precedence():
    """Confirms the precedence decision is not wired into the adapter
    itself either -- per this sprint's explicit 'do not implement the
    Financial Strength Engine' / no-engine-integration rule, this is a
    standalone building block, not a change to the adapter's own
    behavior."""
    import services.sec_edgar_adapter as sea
    assert "us_provider_precedence" not in sea.__dict__
    assert not hasattr(sea, "us_provider_precedence")


@pytest.mark.regression
def test_existing_us_fundamentals_build_behavior_unchanged(monkeypatch):
    import services.us_fundamentals as usf

    monkeypatch.setattr(usf.yf, "Ticker", lambda sym: _FakeTicker())

    result = usf._build("AAPL")

    assert result["available"] is True
    assert result["symbol"] == "AAPL"
    assert result["source"] == "yfinance"
    assert result["pe_ratio"] == 24.5


@pytest.mark.regression
def test_all_three_modules_import_cleanly_together():
    """services/us_fundamentals.py, services/sec_edgar_adapter.py, and
    services/us_provider_precedence.py must coexist without import-order
    side effects or shared mutable state."""
    import services.us_fundamentals  # noqa: F401
    import services.sec_edgar_adapter  # noqa: F401
    import services.us_provider_precedence  # noqa: F401
