"""
Reliability Release 15E-B — direct unit tests for `_deep_fundamental_score()`.

Confirms the repaired method (now receiving `info` as a real parameter,
see `prediction_engine.py:1402`) actually executes the free-cash-flow,
liquidity, and net-debt sections that were previously silently skipped
by an unbound `info` reference caught by the method's own broad
`except Exception: pass` boundary. No network calls — pure in-memory
`pandas.DataFrame` fixtures and a fake ticker only.
"""

import pandas as pd
import pytest

from services.prediction_engine import PredictionEngine
from tests.conftest import MockTicker


def _make_ticker(financials: dict, balance_sheet: dict, cashflow: dict) -> MockTicker:
    cols = pd.to_datetime(["2023-12-31", "2024-12-31"])
    fin_df = pd.DataFrame({cols[0]: financials["old"], cols[1]: financials["new"]})
    bs_df = pd.DataFrame({cols[1]: balance_sheet})
    cf_df = pd.DataFrame({cols[0]: cashflow["old"], cols[1]: cashflow["new"]})
    return MockTicker(financials=fin_df, balance_sheet=bs_df, cashflow=cf_df)


@pytest.fixture
def engine() -> PredictionEngine:
    return PredictionEngine()


def test_deep_fundamental_score_positive_fcf_liquidity_net_cash_fixture(engine):
    ticker = _make_ticker(
        financials={
            "old": {"Total Revenue": 1000, "Operating Income": 150},
            "new": {"Total Revenue": 1250, "Operating Income": 350},
        },
        balance_sheet={
            "Current Assets": 500,
            "Current Liabilities": 200,
            "Total Debt": 100,
            "Cash And Cash Equivalents": 150,
        },
        cashflow={
            "old": {"Operating Cash Flow": 200, "Capital Expenditure": -50},
            "new": {"Operating Cash Flow": 300, "Capital Expenditure": -60},
        },
    )
    info = {"sector": "Technology", "_screener_data": {}}

    result = engine._deep_fundamental_score(ticker, "medium", info)

    assert result["score"] == 100
    assert result["available"] is True
    assert any("Positive free cash flow" in r for r in result["reasons"])
    assert any("Free cash flow growing" in r for r in result["reasons"])
    assert any("strong liquidity" in r for r in result["reasons"])
    assert any("Net cash position" in r for r in result["reasons"])


def test_deep_fundamental_score_negative_fcf_liquidity_net_debt_fixture(engine):
    ticker = _make_ticker(
        financials={
            "old": {"Total Revenue": 1000, "Operating Income": 50},
            "new": {"Total Revenue": 940, "Operating Income": -20},
        },
        balance_sheet={
            "Current Assets": 150,
            "Current Liabilities": 300,
            "Total Debt": 4000,
            "Cash And Cash Equivalents": 100,
        },
        cashflow={
            "old": {"Operating Cash Flow": 80, "Capital Expenditure": -40},
            "new": {"Operating Cash Flow": -30, "Capital Expenditure": -45},
        },
    )
    info = {"sector": "Industrials", "_screener_data": {}}

    result = engine._deep_fundamental_score(ticker, "medium", info)

    assert result["score"] == 4
    assert result["available"] is True
    assert any("Negative free cash flow" in r for r in result["reasons"])
    assert any("liquidity risk" in r for r in result["reasons"])
    assert any("Net debt exceeds 3x revenue" in r for r in result["reasons"])
