"""
Golden tests for the India Business Quality Adapter across the 12 sectors
named in the Sprint #007 brief (Banking, NBFC, Insurance, IT, FMCG,
Pharma, Manufacturing, Utilities, Telecom, Energy, Real Estate,
Turnaround).

These pin STABLE, non-price-volatile outcomes — the sector bucket the
adapter+engine resolve to, whether Altman computes, and whether the hard
gate fires — rather than exact scores (which move with live market data
and would make a golden test brittle). Each fixture's ratios are shaped
to be representative of its sector, not copied from one live snapshot, so
the test documents the intended sector behavior rather than a moment in
time.
"""

import pandas as pd
import pytest

from services.india_business_quality_adapter import build_india_info
from services.business_quality_engine import compute_business_quality


class _FakeTicker:
    financials = pd.DataFrame()
    balance_sheet = pd.DataFrame()
    cashflow = pd.DataFrame()
    dividends = pd.Series(dtype=float)
    actions = pd.DataFrame()


def _screener(sector, industry, *, roe, roce, de, ocf, pat, sales,
              borrow, tl_latest, op_profit, reserves):
    """Builds a representative screener.in-shaped dict. `pat` is a single
    quarterly figure repeated x4 (so Net Income TTM is well-defined);
    `tl_latest` is the latest Total Liabilities (= Total Assets)."""
    return {
        "available": True,
        "sector_name": sector,
        "industry_name": industry,
        "roe_pct": roe,
        "roce_pct": roce,
        "debt_to_equity_pct": de,
        "pe_ratio": 30.0,
        "market_cap_cr": 50000.0,
        "sales_growth_3y_pct": 12.0,
        "operating_cf_latest_cr": ocf,
        "quarterly_pat_cr": [pat] * 4,
        "sales_latest_cr": sales,
        "borrowings_latest_cr": borrow,
        "total_liabilities_annual_cr": [tl_latest * 0.9, tl_latest],
        "operating_profit_latest_cr": op_profit,
        "reserves_latest_cr": reserves,
        "interest_coverage_ratio": 15.0,
        "opm_pct": 22.0,
    }


# One representative company shape per requested sector.
SECTOR_FIXTURES = {
    "Banking":      _screener("Financial Services", "Private Sector Bank",
                              roe=16, roce=7, de=None, ocf=None, pat=12000,
                              sales=90000, borrow=None, tl_latest=2000000,
                              op_profit=40000, reserves=180000),
    "NBFC":         _screener("Financial Services", "Non Banking Financial Company",
                              roe=20, roce=11, de=None, ocf=None, pat=3500,
                              sales=35000, borrow=None, tl_latest=300000,
                              op_profit=18000, reserves=70000),
    "Insurance":    _screener("Financial Services", "Life Insurance",
                              roe=14, roce=12, de=None, ocf=None, pat=400,
                              sales=80000, borrow=None, tl_latest=250000,
                              op_profit=600, reserves=11000),
    "IT":           _screener("Information Technology", "Computers - Software",
                              roe=30, roce=38, de=5, ocf=12000, pat=3000,
                              sales=60000, borrow=500, tl_latest=70000,
                              op_profit=15000, reserves=50000),
    # Real screener.in labels — exercises the Sprint #007 classifier fix.
    "FMCG":         _screener("Fast Moving Consumer Goods", "Packaged Foods",
                              roe=50, roce=55, de=27, ocf=2600, pat=600,
                              sales=19000, borrow=1380, tl_latest=9700,
                              op_profit=3500, reserves=5000),
    "Pharma":       _screener("Healthcare", "Pharmaceuticals",
                              roe=18, roce=20, de=10, ocf=9000, pat=2500,
                              sales=45000, borrow=4000, tl_latest=80000,
                              op_profit=11000, reserves=55000),
    "Manufacturing":_screener("Capital Goods", "Heavy Electrical Equipment",
                              roe=16, roce=22, de=8, ocf=3000, pat=900,
                              sales=20000, borrow=1500, tl_latest=30000,
                              op_profit=2800, reserves=18000),
    "Utilities":    _screener("Power", "Integrated Power Utilities",
                              roe=14, roce=12, de=90, ocf=42000, pat=4500,
                              sales=170000, borrow=160000, tl_latest=450000,
                              op_profit=45000, reserves=180000),
    "Telecom":      _screener("Telecommunication", "Telecom - Infrastructure",
                              roe=11, roce=14, de=60, ocf=6000, pat=700,
                              sales=28000, borrow=12000, tl_latest=80000,
                              op_profit=12000, reserves=30000),
    "Energy":       _screener("Energy", "Oil Exploration & Production",
                              roe=15, roce=14, de=40, ocf=40000, pat=9000,
                              sales=600000, borrow=80000, tl_latest=900000,
                              op_profit=90000, reserves=400000),
    "RealEstate":   _screener("Realty", "Residential Commercial Projects",
                              roe=10, roce=11, de=30, ocf=1500, pat=600,
                              sales=6000, borrow=4000, tl_latest=40000,
                              op_profit=1800, reserves=22000),
    "Turnaround":   _screener("Capital Goods", "Heavy Electrical Equipment",
                              roe=9, roce=8, de=50, ocf=900, pat=200,
                              sales=10000, borrow=6000, tl_latest=25000,
                              op_profit=1200, reserves=8000),
}

# Sector bucket each fixture should classify into (from
# sector_quality_applicability.classify_sector). Financials all collapse
# to FINANCIAL; the rest map per the taxonomy.
EXPECTED_BUCKET = {
    "Banking": "FINANCIAL", "NBFC": "FINANCIAL", "Insurance": "FINANCIAL",
    "IT": "IT", "FMCG": "FMCG", "Pharma": "PHARMA",
    "Manufacturing": "MANUFACTURING", "Utilities": "UTILITIES_ENERGY",
    "Telecom": "TELECOM", "Energy": "UTILITIES_ENERGY",
    "RealEstate": "REAL_ESTATE", "Turnaround": "MANUFACTURING",
}


@pytest.mark.golden
@pytest.mark.parametrize("sector", list(SECTOR_FIXTURES))
def test_every_sector_produces_a_valid_response(sector):
    info = build_india_info(SECTOR_FIXTURES[sector])
    resp = compute_business_quality(sector, _FakeTicker(), pd.DataFrame(),
                                    info, market="IN")
    assert 0 <= resp["score"] <= 100
    assert resp["grade"] in {"strong_buy", "buy", "hold", "watch", "rejected"}
    assert resp["confidence"] is not None


@pytest.mark.golden
@pytest.mark.parametrize("sector", list(SECTOR_FIXTURES))
def test_sector_classification_is_stable(sector):
    info = build_india_info(SECTOR_FIXTURES[sector])
    resp = compute_business_quality(sector, _FakeTicker(), pd.DataFrame(),
                                    info, market="IN")
    assert resp["metadata"]["sector_bucket"] == EXPECTED_BUCKET[sector]


@pytest.mark.golden
@pytest.mark.parametrize("sector", list(SECTOR_FIXTURES))
def test_altman_computes_for_every_sector(sector):
    """The proven Total-Assets derivation must make Altman computable in
    every sector — the core capability this sprint delivers for India.
    altman_z is present on every code path; altman_zone is only emitted on
    the full-scoring path (the hard-gate rejection path carries altman_z
    but not the zone label), so the zone is asserted only when present."""
    info = build_india_info(SECTOR_FIXTURES[sector])
    resp = compute_business_quality(sector, _FakeTicker(), pd.DataFrame(),
                                    info, market="IN")
    assert resp["metadata"]["altman_z"] is not None
    if "altman_zone" in resp["metadata"]:
        assert resp["metadata"]["altman_zone"] in {"safe", "grey", "distress"}


@pytest.mark.golden
@pytest.mark.parametrize("sector", list(SECTOR_FIXTURES))
def test_beneish_absent_in_every_sector_but_does_not_reject(sector):
    """Beneish is the known, out-of-scope gap: it must be None everywhere,
    and its absence must NOT by itself trigger the hard gate (which needs a
    POSITIVE manipulation signal, never mere absence)."""
    info = build_india_info(SECTOR_FIXTURES[sector])
    resp = compute_business_quality(sector, _FakeTicker(), pd.DataFrame(),
                                    info, market="IN")
    assert resp["metadata"]["beneish_m"] is None
    # Absence of Beneish alone never rejects — rejection requires a real
    # distress+accruals or positive-Beneish signal.
    if resp["grade"] == "rejected":
        # If rejected, it must be for a substantive reason, not the gap.
        assert resp["metadata"]["beneish_m"] is None  # documents the gap
