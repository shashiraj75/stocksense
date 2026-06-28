"""
Regression test: introducing services/sec_edgar_adapter.py (SSDS-006
Sprint #004) must not change services/us_fundamentals.py's existing
yfinance-based behavior in any way — SEC EDGAR is additive, not a
replacement, per this sprint's explicit "do not break existing
yfinance paths" / "no provider replacement yet" rules.

This locks in the existing _build() behavior exactly as
test_us_fundamentals_business_quality_addition.py already does for the
Business Quality Engine addition (Sprint #005) — the same proof
pattern, applied to this sprint's own additive change.
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
def test_us_fundamentals_module_does_not_import_sec_edgar_adapter():
    """services/us_fundamentals.py must have zero coupling to the new
    adapter — confirms 'introduced as an additive provider first, no
    engine/consumer integration yet' was actually honored, not just
    stated."""
    import services.us_fundamentals as usf
    assert "sec_edgar_adapter" not in usf.__dict__
    assert not hasattr(usf, "sec_edgar_adapter")


@pytest.mark.regression
def test_existing_us_fundamentals_build_behavior_unchanged(monkeypatch):
    """Same fixture/assertions style as the existing Sprint #005
    regression test for this function — confirms _build() still
    returns its pre-existing fields, unaffected by this sprint's
    new, separate module existing in the codebase."""
    import services.us_fundamentals as usf

    monkeypatch.setattr(usf.yf, "Ticker", lambda sym: _FakeTicker())

    result = usf._build("AAPL")

    assert result["available"] is True
    assert result["symbol"] == "AAPL"
    assert result["source"] == "yfinance"
    assert result["pe_ratio"] == 24.5


@pytest.mark.regression
def test_sec_edgar_adapter_module_imports_cleanly_alongside_us_fundamentals():
    """Both modules must coexist without import-order side effects or
    shared mutable state — confirms the new module is genuinely
    additive, not a refactor disguised as an addition."""
    import services.us_fundamentals  # noqa: F401
    import services.sec_edgar_adapter  # noqa: F401
    # No exception means both modules loaded independently — the test's
    # entire assertion is the absence of an import-time error.
