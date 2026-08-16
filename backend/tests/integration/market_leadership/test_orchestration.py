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
    # compute_stock_context is TTL-cached at module level (Section 17 #10)
    # — clear it so one test's cached result can't leak into another via
    # a shared (symbol, market, as_of) key.
    orch._STOCK_CONTEXT_CACHE.clear()


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

        def history(self, period=None, timeout=None):
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

    def test_second_call_is_served_from_cache_no_second_fetch(self, monkeypatch, mocked_market):
        """Section 17 #10: prevent expensive recalculation on every page
        request. A second call for the same (symbol, market, as_of) must
        not re-invoke fetch_price_history."""
        call_count = {"n": 0}
        price_data, benchmark = mocked_market

        def counting_fetch(symbol, market, period="2y"):
            call_count["n"] += 1
            return price_data.get(symbol, pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))

        monkeypatch.setattr(orch, "fetch_price_history", counting_fetch)

        first = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        second = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)

        assert call_count["n"] == 1
        assert first == second

    def test_different_symbols_are_cached_independently(self, monkeypatch, mocked_market):
        orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        orch.compute_stock_context("BBB", "IN", as_of=AS_OF)
        assert orch._stock_context_cache_key("AAA", "IN", AS_OF) in orch._STOCK_CONTEXT_CACHE
        assert orch._stock_context_cache_key("BBB", "IN", AS_OF) in orch._STOCK_CONTEXT_CACHE

    def test_market_isolation_in_cache_key(self, mocked_market):
        """The same symbol in two markets must never share a cache entry."""
        key_in = orch._stock_context_cache_key("V", "IN", AS_OF)
        key_us = orch._stock_context_cache_key("V", "US", AS_OF)
        assert key_in != key_us

    def test_expired_cache_entry_triggers_a_fresh_fetch(self, monkeypatch, mocked_market):
        call_count = {"n": 0}
        price_data, benchmark = mocked_market

        def counting_fetch(symbol, market, period="2y"):
            call_count["n"] += 1
            return price_data.get(symbol, pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"]))

        monkeypatch.setattr(orch, "fetch_price_history", counting_fetch)
        orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        assert call_count["n"] == 1

        # Force the cached entry to look expired.
        key = orch._stock_context_cache_key("AAA", "IN", AS_OF)
        stale_ts, cached_value = orch._STOCK_CONTEXT_CACHE[key]
        orch._STOCK_CONTEXT_CACHE[key] = (stale_ts - orch._STOCK_CONTEXT_TTL - 1, cached_value)

        orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        assert call_count["n"] == 2

    def test_caller_mutating_a_cache_hit_response_does_not_corrupt_the_cache(self, mocked_market):
        """Pre-publication audit finding: compute_stock_context previously
        returned the exact object stored in _STOCK_CONTEXT_CACHE — a
        caller mutating its own response (e.g. building a modified copy
        for a different consumer) silently corrupted every subsequent
        cache hit for that symbol/market/date. The same hazard class this
        codebase's own Recommendation Consolidation Sprint #007 found and
        designed around for prediction_engine.py's _pred_cache."""
        first = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        first["rs"]["stock_rs_score"] = 999999
        first["status"] = "tampered"

        second = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        assert second["rs"]["stock_rs_score"] != 999999
        assert second["status"] == "ok"

    def test_caller_mutating_the_first_fresh_response_does_not_corrupt_the_cache(self, mocked_market):
        """Same hazard, but on the cache-MISS (first-computation) path —
        the object handed back on a fresh calculation must also be
        independent from what was just stored in the cache."""
        first = orch.compute_stock_context("CCC", "IN", as_of=AS_OF)
        first["why_now"]["summary"] = "TAMPERED"

        second = orch.compute_stock_context("CCC", "IN", as_of=AS_OF)
        assert second["why_now"]["summary"] != "TAMPERED"

    def test_repeated_calls_return_equal_but_independent_objects(self, mocked_market):
        first = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        second = orch.compute_stock_context("AAA", "IN", as_of=AS_OF)
        assert first == second
        assert first is not second
        assert first["rs"] is not second["rs"]


@pytest.mark.integration
class TestUniverseSymbols:
    def test_dedupes_across_sectors(self, monkeypatch):
        monkeypatch.setattr(orch, "_sector_map", lambda m: {"A": ["X", "Y"], "B": ["Y", "Z"]})
        symbols = orch.universe_symbols("IN")
        assert sorted(symbols) == ["X", "Y", "Z"]
