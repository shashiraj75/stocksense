"""
Shared pytest fixtures for the Selection Engine test suite.

The central fixture here is `base_info` — a minimal, valid yfinance-`info`-
shaped dict. SEAR-001 flagged the `info` dict as the Selection Engine's
primary, untyped data-flow vehicle; until it gets a real typed contract
(roadmap item 1.6), every test that exercises gate/scoring logic needs a
realistic fixture to mutate rather than hand-rolling its own dict shape.
Use `base_info` (or `financial_sector_info` / `in_market_info`) and override
only the keys relevant to the behavior under test — don't construct `info`
dicts from scratch in individual test files.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def base_info() -> dict:
    """A financially healthy, non-financial-sector US stock. Passes every
    hard quality-gate check in prediction_engine.py with margin to spare."""
    return {
        "symbol": "TEST",
        "longName": "Test Corp",
        "sector": "Technology",
        "industry": "Software",
        "currentPrice": 100.0,
        "regularMarketPrice": 100.0,
        "trailingPE": 22.0,
        "forwardPE": 20.0,
        "bookValue": 25.0,
        "beta": 1.1,
        "debtToEquity": 40.0,
        "returnOnEquity": 0.22,
        "returnOnCapitalEmployed": 0.18,
        "profitMargins": 0.18,
        "revenueGrowth": 0.14,
        "freeCashflow": 5_000_000,
        "operatingCashflow": 8_000_000,
        "operatingCashflows": 8_000_000,
    }


@pytest.fixture
def financial_sector_info(base_info) -> dict:
    """A bank — exempt from the OCF/leverage hard-reject checks per the
    Ind-AS accounting rationale documented in prediction_engine.py's
    _quality_gate (loans disbursed count as operating outflows)."""
    info = dict(base_info)
    info.update({
        "sector": "Financial Services",
        "industry": "Banks",
        "operatingCashflow": -2_000_000,
        "operatingCashflows": -2_000_000,
        "debtToEquity": 600.0,  # banks run structurally high D/E; must not hard-reject
    })
    return info


@pytest.fixture
def in_market_info(base_info) -> dict:
    """An Indian-market stock with the screener.in-augmented `_screener_data`
    sub-dict attached, mirroring augment_info_with_screener's output shape."""
    info = dict(base_info)
    info.update({
        "symbol": "TEST.NS",
        "debtToEquity": 30.0,  # IN convention: percent, same as US in this codebase
        "_screener_data": {
            "sales_growth_3y_pct": 18.0,
            "sales_growth_5y_pct": 16.0,
            "profit_growth_3y_pct": 20.0,
            "eps_trend": "accelerating",
            "operating_cf_latest_cr": 120.0,
            "operating_cf_annual_cr": [80, 95, 110, 120],
            "book_value": 25.0,
        },
    })
    return info


@pytest.fixture
def multibagger_stock_in() -> dict:
    """A stock dict in the shape multibagger_scorecard.compute_scorecard()
    expects (stock_fundamentals_cache row shape), tuned to pass every IN
    Quality Compounder checklist item."""
    return {
        "symbol": "TEST",
        "roe_pct": 22.0,
        "roe_5y_pct": 20.0,
        "roce_pct": 18.0,
        "sales_growth_3y_pct": 15.0,
        "sales_growth_5y_pct": 13.0,
        "profit_growth_3y_pct": 16.0,
        "profit_growth_5y_pct": 14.0,
        "debt_to_equity_pct": 30.0,
        "interest_coverage_ratio": 8.0,
        "operating_cf_latest_cr": 150.0,
        "pe_ratio": 28.0,
        "ev_ebitda": 15.0,
        "promoter_pledge_pct": 0.0,
    }
