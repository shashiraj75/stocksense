"""
Unit tests for the StockSense360 India Business Quality Adapter
(services/india_business_quality_adapter.py, Sprint #007).

These exercise the pure mapping/derivation logic (build_india_info) and
the entry point's guard behavior — no network, no real yfinance, no DB.
The numbers in FMCG_SCREENER are taken from a real BRITANNIA screener.in
scrape so the fixture stays representative rather than invented.
"""

import pandas as pd
import pytest

from services.india_business_quality_adapter import (
    build_india_info,
    compute_india_business_quality,
    _CR_TO_UNITS,
)


# A realistic FMCG screener.in dict (BRITANNIA, captured live Sprint #007).
FMCG_SCREENER = {
    "available": True,
    "sector_name": "Fast Moving Consumer Goods",
    "industry_name": "Packaged Foods",
    "roe_pct": 53.6,
    "roce_pct": 56.0,
    "debt_to_equity_pct": 27.0,
    "pe_ratio": 49.8,
    "market_cap_cr": 126143.0,
    "sales_growth_ttm_pct": 7.0,
    "sales_growth_3y_pct": 6.0,
    "operating_cf_latest_cr": 2612.0,
    "quarterly_pat_cr": [558.0, 455.0, 586.0, 556.0, 537.0, 505.0, 532.0,
                          582.0, 559.0, 520.0, 655.0, 682.0, 680.0],
    "sales_latest_cr": 19152.0,
    "borrowings_latest_cr": 1380.0,
    "total_liabilities_annual_cr": [2793.0, 3494.0, 4109.0, 5188.0, 6238.0,
                                     7830.0, 8000.0, 7527.0, 9351.0, 9072.0,
                                     8837.0, 9732.0],
    "operating_profit_latest_cr": 3514.0,
    "reserves_latest_cr": 5082.0,
    "interest_coverage_ratio": 31.1,
    "opm_pct": 18.0,
}


# ── Mapping: direct provider values ───────────────────────────────────────

@pytest.mark.unit
def test_direct_ratios_mapped_and_rescaled():
    info = build_india_info(FMCG_SCREENER)
    # ROE/ROCE are percentages on screener.in, fractions in the info-dict
    # convention — same rescale augment_info_with_screener applies.
    assert info["returnOnEquity"] == pytest.approx(0.536)
    assert info["returnOnCapitalEmployed"] == pytest.approx(0.56)
    # D/E and PE pass through unchanged.
    assert info["debtToEquity"] == 27.0
    assert info["trailingPE"] == 49.8


@pytest.mark.unit
def test_classification_mapped():
    info = build_india_info(FMCG_SCREENER)
    assert info["sector"] == "Fast Moving Consumer Goods"
    assert info["industry"] == "Packaged Foods"


@pytest.mark.unit
def test_crore_fields_converted_to_rupees():
    info = build_india_info(FMCG_SCREENER)
    assert info["marketCap"] == 126143.0 * _CR_TO_UNITS
    assert info["operatingCashflow"] == 2612.0 * _CR_TO_UNITS
    assert info["totalRevenue"] == 19152.0 * _CR_TO_UNITS
    assert info["totalDebt"] == 1380.0 * _CR_TO_UNITS


# ── Derivation: proven and supported ──────────────────────────────────────

@pytest.mark.unit
def test_total_assets_derived_from_balance_sheet_identity():
    """[DERIVED/PROVEN] Total Assets = the latest total_liabilities_annual_cr
    (Assets = Liabilities + Equity), the Sprint #006-proven derivation."""
    info = build_india_info(FMCG_SCREENER)
    assert info["totalAssets"] == 9732.0 * _CR_TO_UNITS  # latest year


@pytest.mark.unit
def test_ebit_and_retained_earnings_supported_derivations():
    """[DERIVED/SUPPORTED] EBIT via Operating Profit, Retained Earnings via
    Reserves & Surplus."""
    info = build_india_info(FMCG_SCREENER)
    assert info["ebit"] == 3514.0 * _CR_TO_UNITS
    assert info["operatingIncome"] == 3514.0 * _CR_TO_UNITS
    assert info["retainedEarnings"] == 5082.0 * _CR_TO_UNITS


@pytest.mark.unit
def test_net_income_is_trailing_four_quarters():
    """Net Income derives from the last 4 quarters of PAT, not a single
    quarter — a TTM figure, matching how the engine's other income reads
    are framed."""
    info = build_india_info(FMCG_SCREENER)
    expected = sum([655.0, 682.0, 680.0, 520.0])  # last 4 of the list
    # order: ...520, 655, 682, 680 -> last four are 520,655,682,680
    assert info["netIncome"] == pytest.approx(
        sum(FMCG_SCREENER["quarterly_pat_cr"][-4:]) * _CR_TO_UNITS
    )


# ── Unavailable values: deliberately absent, never guessed ────────────────

@pytest.mark.unit
def test_working_capital_not_fabricated():
    """[UNAVAILABLE] Working Capital needs Current Assets/Liabilities, which
    the scrape does not contain — the adapter must NOT invent it."""
    info = build_india_info(FMCG_SCREENER)
    assert "workingCapital" not in info
    assert "currentAssets" not in info
    assert "currentLiabilities" not in info


@pytest.mark.unit
def test_beneish_inputs_not_fabricated():
    """[UNAVAILABLE] Receivables/SG&A — confirmed total gap, out of scope
    this sprint; must stay absent so Beneish degrades to None, never a
    guessed value."""
    info = build_india_info(FMCG_SCREENER)
    assert "netReceivables" not in info
    assert "sellingGeneralAdministrative" not in info


@pytest.mark.unit
def test_missing_fields_omitted_not_zeroed():
    """A sparse screener dict must yield a sparse info dict — absent keys,
    not zero values that would corrupt downstream ratios."""
    sparse = {"available": True, "sector_name": "X", "roe_pct": 10.0}
    info = build_india_info(sparse)
    assert info["returnOnEquity"] == 0.10
    assert "totalAssets" not in info
    assert "ebit" not in info
    assert "operatingCashflow" not in info


# ── Entry-point guards ────────────────────────────────────────────────────

@pytest.mark.unit
def test_returns_none_when_screener_unavailable():
    assert compute_india_business_quality("X", {"available": False}) is None
    assert compute_india_business_quality("X", {}) is None
    assert compute_india_business_quality("X", None) is None


@pytest.mark.unit
def test_failure_in_engine_returns_none_not_raises(monkeypatch):
    """If the engine raises, the adapter must swallow and return None — the
    refresh loop's row should upsert without BQ fields, never crash."""
    import services.india_business_quality_adapter as adapter

    monkeypatch.setattr(adapter, "compute_business_quality",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(adapter.yf, "Ticker", lambda s: object())

    assert compute_india_business_quality("X", FMCG_SCREENER) is None


@pytest.mark.unit
def test_empty_dataframe_passed_no_price_history_fetch(monkeypatch):
    """Confirms the documented design decision: df is an empty DataFrame,
    no ticker.history() call — same as the US adapter."""
    import services.india_business_quality_adapter as adapter

    captured = {}

    def _capture(symbol, ticker, df, info, market="IN"):
        captured["df"] = df
        captured["market"] = market
        return {"score": 70, "grade": "buy", "confidence": 90.0,
                "metadata": {"suitable_investment_style": "Quality Compounder"}}

    monkeypatch.setattr(adapter, "compute_business_quality", _capture)
    monkeypatch.setattr(adapter.yf, "Ticker", lambda s: object())

    out = compute_india_business_quality("X", FMCG_SCREENER)
    assert isinstance(captured["df"], pd.DataFrame) and captured["df"].empty
    assert captured["market"] == "IN"
    assert out["score"] == 70
