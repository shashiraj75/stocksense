"""
Integration tests: the India Business Quality Adapter driving the REAL,
unmodified compute_business_quality() engine end-to-end (Sprint #007).

No network: a fake yfinance Ticker stands in for the balance_sheet/
financials access the engine makes for Piotroski/Asset-Turnover, so these
tests exercise the genuine engine scoring path against adapter-mapped
inputs without leaving the process.
"""

import pandas as pd
import pytest

from services.india_business_quality_adapter import (
    build_india_info,
    compute_india_business_quality,
)
from services.business_quality_engine import compute_business_quality
from tests.unit.test_india_business_quality_adapter import FMCG_SCREENER


class _FakeTicker:
    financials = pd.DataFrame()
    balance_sheet = pd.DataFrame()
    cashflow = pd.DataFrame()
    dividends = pd.Series(dtype=float)
    actions = pd.DataFrame()


@pytest.mark.integration
def test_adapter_output_drives_real_engine_to_a_valid_response():
    """The adapter's info dict, fed to the real engine, must yield a
    well-formed EngineResponse (score in range, a known grade, confidence
    present) — proving the mapping is engine-compatible, not just
    dict-shaped."""
    info = build_india_info(FMCG_SCREENER)
    resp = compute_business_quality("BRITANNIA", _FakeTicker(), pd.DataFrame(),
                                    info, market="IN")

    assert 0 <= resp["score"] <= 100
    assert resp["grade"] in {"strong_buy", "buy", "hold", "watch", "rejected"}
    assert resp["confidence"] is not None
    assert resp["metadata"]["engine"] == "business_quality_engine"


@pytest.mark.integration
def test_proven_derivations_make_altman_computable():
    """The whole point of the adapter: the proven Total-Assets derivation
    (plus EBIT/Retained-Earnings) must let Altman actually compute, not
    fall back to a degenerate all-zero Z."""
    info = build_india_info(FMCG_SCREENER)
    resp = compute_business_quality("BRITANNIA", _FakeTicker(), pd.DataFrame(),
                                    info, market="IN")
    meta = resp["metadata"]
    assert meta["altman_z"] is not None
    assert meta["altman_zone"] in {"safe", "grey", "distress"}


@pytest.mark.integration
def test_strong_fmcg_is_not_rejected_and_scores_well():
    """A high-ROE, low-debt, cash-generative FMCG (BRITANNIA-shaped) must
    not trip the hard gate and should land in the upper half of the
    scale — a sanity floor, not a brittle exact-score assertion."""
    info = build_india_info(FMCG_SCREENER)
    resp = compute_business_quality("BRITANNIA", _FakeTicker(), pd.DataFrame(),
                                    info, market="IN")
    assert resp["grade"] != "rejected"
    assert resp["score"] >= 60


@pytest.mark.integration
def test_entry_point_matches_direct_engine_call(monkeypatch):
    """compute_india_business_quality must be a thin wrapper: its result
    equals build_india_info + a direct engine call with the same fake
    ticker. Guards against the wrapper silently diverging from the engine."""
    import services.india_business_quality_adapter as adapter
    monkeypatch.setattr(adapter.yf, "Ticker", lambda s: _FakeTicker())

    via_wrapper = compute_india_business_quality("BRITANNIA", FMCG_SCREENER)
    direct = compute_business_quality(
        "BRITANNIA", _FakeTicker(), pd.DataFrame(),
        build_india_info(FMCG_SCREENER), market="IN",
    )
    assert via_wrapper["score"] == direct["score"]
    assert via_wrapper["grade"] == direct["grade"]
