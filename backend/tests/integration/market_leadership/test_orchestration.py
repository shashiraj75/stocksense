"""
Integration tests for orchestration.py — wires relative_strength +
group_leadership + trend_lifecycle + breadth + explanation together with
a fully mocked acquisition layer (no live network, SES-003). Verifies
market isolation, flag gating, and that a symbol/group with insufficient
data never gets a fabricated score.
"""
import datetime

import pandas as pd
import pytest

from services.market_leadership import orchestration as orch
from tests.unit.market_leadership.conftest import make_price_df, make_uptrend_df, make_flat_df

AS_OF = datetime.date(2026, 7, 20)


def _fake_universe(market):
    return {"TestSector": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]}


@pytest.fixture(autouse=True)
def _engine_on(monkeypatch):
    monkeypatch.setenv("MARKET_LEADERSHIP_ENGINE_ENABLED", "1")
    monkeypatch.delenv("MARKET_LEADERSHIP_SHADOW_ENABLED", raising=False)


@pytest.fixture
def mocked_market(monkeypatch):
    monkeypatch.setattr(orch, "_sector_map", _fake_universe)
    price_data = {
        "AAA": make_uptrend_df(300, seed=1), "BBB": make_uptrend_df(300, seed=2),
        "CCC": make_flat_df(300, seed=3), "DDD": make_flat_df(300, seed=4),
        "EEE": make_price_df(300, daily_drift_pct=-0.1, volatility_pct=0.3, seed=5),
        "FFF": make_price_df(40, seed=6),  # IPO-like, insufficient history
    }
    benchmark = make_flat_df(300, seed=99)

    def fake_fetch(symbol, market, period="2y"):
        return price_data.get(symbol, pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))

    monkeypatch.setattr(orch, "fetch_price_history", fake_fetch)

    class _FakeTicker:
        def __init__(self, ticker):
            self.ticker = ticker

        def history(self, period=None):
            return benchmark

    monkeypatch.setattr(orch.yf, "Ticker", _FakeTicker)
    return price_data, benchmark


@pytest.mark.integration
class TestComputeMarketContext:
    def test_disabled_status_with_flag_off(self, monkeypatch, mocked_market):
        monkeypatch.delenv("MARKET_LEADERSHIP_ENGINE_ENABLED", raising=False)
        result = orch.compute_market_context("IN", as_of=AS_OF)
        assert result == {"status": "disabled"}

    def test_ok_status_computes_rs_for_every_universe_symbol(self, mocked_market):
        result = orch.compute_market_context("IN", as_of=AS_OF)
        assert result["status"] == "ok"
        assert set(result["rs"]) == {"AAA", "BBB", "CCC", "DDD", "EEE", "FFF"}

    def test_ipo_like_symbol_gets_insufficient_data_never_a_score(self, mocked_market):
        result = orch.compute_market_context("IN", as_of=AS_OF)
        fff = result["rs"]["FFF"]
        assert fff["stock_rs_score"] is None
        assert "INSUFFICIENT_HISTORY" in fff["reason_codes"]

    def test_uptrend_symbols_rank_above_downtrend_symbols(self, mocked_market):
        result = orch.compute_market_context("IN", as_of=AS_OF)
        aaa_pct = result["rs"]["AAA"]["stock_rs_percentile"]
        eee_pct = result["rs"]["EEE"]["stock_rs_percentile"]
        assert aaa_pct is not None and eee_pct is not None
        assert aaa_pct > eee_pct

    def test_market_field_is_never_inferred(self, mocked_market):
        result_in = orch.compute_market_context("IN", as_of=AS_OF)
        result_us = orch.compute_market_context("US", as_of=AS_OF)
        assert all(v["market"] == "IN" for v in result_in["rs"].values())
        assert all(v["market"] == "US" for v in result_us["rs"].values())

    def test_group_snapshot_present_for_the_synthetic_sector(self, mocked_market):
        result = orch.compute_market_context("IN", as_of=AS_OF)
        assert "TestSector" in result["groups"]

    def test_breadth_snapshot_present(self, mocked_market):
        result = orch.compute_market_context("IN", as_of=AS_OF)
        assert result["breadth"]["market"] == "IN"

    def test_one_symbol_fetch_failure_does_not_invalidate_the_whole_snapshot(self, monkeypatch, mocked_market):
        price_data, benchmark = mocked_market

        def flaky_fetch(symbol, market, period="2y"):
            if symbol == "BBB":
                raise RuntimeError("synthetic provider failure")
            return price_data.get(symbol, pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))

        monkeypatch.setattr(orch, "fetch_price_history", flaky_fetch)
        result = orch.compute_market_context("IN", as_of=AS_OF)
        assert result["status"] == "ok"
        assert result["rs"]["BBB"]["stock_rs_score"] is None
        assert any("BBB" in e for e in result["errors"])
        # Other symbols unaffected by BBB's failure.
        assert result["rs"]["AAA"]["stock_rs_score"] is not None


@pytest.mark.integration
class TestComputeStockContext:
    def test_disabled_with_flag_off(self, monkeypatch, mocked_market):
        monkeypatch.delenv("MARKET_LEADERSHIP_ENGINE_ENABLED", raising=False)
        result = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        assert result == {"status": "disabled"}

    def test_ok_status_returns_rs_trend_and_why_now(self, mocked_market):
        result = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        assert result["status"] == "ok"
        assert "rs" in result and "trend" in result and "why_now" in result
        assert result["rs"]["market"] == "IN"

    def test_acquisition_failure_returns_error_status_not_a_crash(self, monkeypatch, mocked_market):
        def boom(*a, **k):
            raise RuntimeError("provider down")

        monkeypatch.setattr(orch, "fetch_price_history", boom)
        result = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        assert result["status"] == "error"


@pytest.mark.integration
class TestUniverseSymbols:
    def test_dedupes_across_sectors(self, monkeypatch):
        monkeypatch.setattr(orch, "_sector_map", lambda m: {"A": ["X", "Y"], "B": ["Y", "Z"]})
        symbols = orch.universe_symbols("IN")
        assert sorted(symbols) == ["X", "Y", "Z"]
