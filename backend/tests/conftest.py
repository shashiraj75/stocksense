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

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class MockTicker:
    """
    A yfinance.Ticker stand-in for tests that exercise functions reading
    ticker.financials / .balance_sheet / .cashflow / .dividends / .actions
    (quality_factors.py's buffett_munger_score, quality_metrics_score,
    corporate_actions_score, and business_quality_engine.py's new metric
    helpers all take a `ticker` object rather than raw DataFrames, mirroring
    the real yfinance.Ticker interface).

    Every attribute defaults to an empty DataFrame/Series so a test that
    doesn't care about financial-statement history (e.g. testing the
    REJECTED/insufficient-data path) doesn't need to construct one — the
    functions under test already have defensive `if df.empty` guards for
    exactly this case (confirmed by reading their source before writing
    these fixtures, not assumed).
    """

    def __init__(self, financials=None, balance_sheet=None, cashflow=None,
                 dividends=None, actions=None):
        self.financials = financials if financials is not None else pd.DataFrame()
        self.balance_sheet = balance_sheet if balance_sheet is not None else pd.DataFrame()
        self.cashflow = cashflow if cashflow is not None else pd.DataFrame()
        self.dividends = dividends if dividends is not None else pd.Series(dtype=float)
        self.actions = actions if actions is not None else pd.DataFrame()


@pytest.fixture
def mock_ticker() -> MockTicker:
    """An empty MockTicker — the "recent IPO / incomplete statements" case."""
    return MockTicker()


@pytest.fixture
def mock_ticker_two_year_financials() -> MockTicker:
    """A MockTicker with 2 full years of financials/balance_sheet/cashflow,
    populated with internally-consistent, healthy-business figures —
    enough data for quality_metrics_score's Piotroski checks, the Beneish
    M-Score's 8 variables, and the standalone asset-turnover/working-
    capital checks to all compute a real (non-None) value, so tests can
    assert on actual numbers rather than just "didn't crash"."""
    cols = pd.to_datetime(["2023-12-31", "2024-12-31"])

    financials = pd.DataFrame({
        cols[0]: {
            "Total Revenue": 1_000_000_000,
            "Cost Of Revenue": 600_000_000,
            "Net Income": 150_000_000,
            "Selling General And Administration": 100_000_000,
            "Depreciation And Amortization": 40_000_000,
            "Diluted Average Shares": 100_000_000,
        },
        cols[1]: {
            "Total Revenue": 1_100_000_000,
            "Cost Of Revenue": 640_000_000,
            "Net Income": 170_000_000,
            "Selling General And Administration": 105_000_000,
            "Depreciation And Amortization": 42_000_000,
            "Diluted Average Shares": 100_000_000,
        },
    })
    balance_sheet = pd.DataFrame({
        cols[0]: {
            "Total Assets": 2_000_000_000,
            "Current Assets": 700_000_000,
            "Total Current Assets": 700_000_000,
            "Current Liabilities": 400_000_000,
            "Total Current Liabilities": 400_000_000,
            "Net PPE": 600_000_000,
            "Property Plant And Equipment Net": 600_000_000,
            "Long Term Debt": 300_000_000,
            "Receivables": 120_000_000,
            "Accounts Receivable": 120_000_000,
            "Net Receivables": 120_000_000,
            "Ordinary Shares Number": 100_000_000,
        },
        cols[1]: {
            "Total Assets": 2_150_000_000,
            "Current Assets": 740_000_000,
            "Total Current Assets": 740_000_000,
            "Current Liabilities": 380_000_000,
            "Total Current Liabilities": 380_000_000,
            "Net PPE": 630_000_000,
            "Property Plant And Equipment Net": 630_000_000,
            "Long Term Debt": 280_000_000,
            "Receivables": 125_000_000,
            "Accounts Receivable": 125_000_000,
            "Net Receivables": 125_000_000,
            "Ordinary Shares Number": 100_000_000,
        },
    })
    cashflow = pd.DataFrame({
        cols[0]: {
            "Operating Cash Flow": 180_000_000,
            "Cash From Operations": 180_000_000,
            "Repurchase Of Capital Stock": -20_000_000,
        },
        cols[1]: {
            "Operating Cash Flow": 200_000_000,
            "Cash From Operations": 200_000_000,
            "Repurchase Of Capital Stock": -25_000_000,
        },
    })
    dividends = pd.Series(
        [1.0, 1.05, 1.10, 1.15, 1.20],
        index=pd.to_datetime(["2020-06-01", "2021-06-01", "2022-06-01", "2023-06-01", "2024-06-01"]),
    )
    return MockTicker(financials=financials, balance_sheet=balance_sheet,
                       cashflow=cashflow, dividends=dividends)


@pytest.fixture
def business_quality_info(base_info) -> dict:
    """Extends base_info with the additional fields business_quality_engine.py
    reads that aren't part of the original base_info fixture (margins, net
    income, total revenue, payout ratio, and — since the Production
    Readiness Validation's calibration fix made Altman/Sloan actually
    compute real values from ticker.balance_sheet — marketCap/ebit/
    retainedEarnings, so this fixture's "healthy company" no longer
    artificially lands in the Altman distress zone purely because those
    fields were never set. operatingCashflow is also raised from
    base_info's 8,000,000 to be consistent with netIncome=170,000,000 —
    the original combination was never internally consistent, it just
    happened not to matter before Altman/Sloan could compute anything
    from it).
    """
    info = dict(base_info)
    info.update({
        "netIncome": 170_000_000,
        "totalRevenue": 1_100_000_000,
        "operatingCashflow": 200_000_000,
        "operatingCashflows": 200_000_000,
        "grossMargins": 0.42,
        "operatingMargins": 0.25,
        "payoutRatio": 0.30,
        "marketCap": 20_000_000_000,
        "ebit": 300_000_000,
        "retainedEarnings": 800_000_000,
    })
    return info


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


def make_companyfacts(tags: dict[str, dict], entity_name: str = "TEST CORP") -> dict:
    """
    Builds a SEC EDGAR `companyfacts`-shaped fixture (services/sec_edgar_adapter.py,
    SSDS-006 Sprint #004) for a us-gaap concept set, without any live network call.

    `tags` maps an XBRL concept name (e.g. "AssetsCurrent") to a single fact
    entry dict (val/end/fy/fp/form/filed) — the shape confirmed live against
    SEC EDGAR's real API for AAPL during this sprint, not invented. Pass only
    the concepts a given test case needs; everything else is correctly absent,
    matching how a real company's filing can omit a tag entirely (e.g. JPM's
    real, confirmed-live absence of AssetsCurrent/LiabilitiesCurrent).
    """
    return {
        "cik": 9999999999,
        "entityName": entity_name,
        "facts": {
            "us-gaap": {
                concept: {"units": {"USD": [entry]}} for concept, entry in tags.items()
            }
        },
    }
