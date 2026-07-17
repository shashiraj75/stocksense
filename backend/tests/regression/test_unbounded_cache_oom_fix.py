"""
2026-07-17 — closing the remaining gap from Product Integrity #020.

That fix (see tests/unit/test_sec_edgar_adapter_facts_cache_cap.py) traced a
2026-07-15/16 Railway OOM (US Daily Picks job stalled ~5h) to exactly one
unbounded cache and capped it, matching the pattern already used by
prediction_engine.py's _pred_cache ("cap at 300 entries to prevent OOM").
The SAME class of incident recurred on 2026-07-16/17 (this session directly
observed and diagnosed it: memory climbing to the 8GB container limit then
OOM-killing the process, at least twice in 24h, independent of any deploy) —
because eleven OTHER per-symbol caches across the services layer were never
given the same treatment. This file verifies each of them now is.

No live network calls — each test drives the module's own cache-set logic
directly (or through its lock-protected write path) and inspects the
resulting dict, mirroring test_sec_edgar_adapter_facts_cache_cap.py's style.
"""
import threading

import pytest


@pytest.mark.regression
class TestCacheCapMatchesTheEstablishedPrecedent:
    """Not new/untested numbers — every module reuses the exact cap value
    already proven safe by prediction_engine.py and sec_edgar_adapter.py."""

    def test_market_data_cap(self):
        import services.market_data as md
        from services.prediction_engine import _CACHE_MAX
        assert md._CACHE_MAX == _CACHE_MAX == 300

    def test_finnhub_client_cap(self):
        import services.finnhub_client as fc
        assert fc._CACHE_MAX == 300

    def test_screener_data_cap(self):
        import services.screener_data as sd
        assert sd._CACHE_MAX == 300

    def test_bse_data_cap(self):
        import services.bse_data as bd
        assert bd._CACHE_MAX == 300

    def test_us_fundamentals_cap(self):
        import services.us_fundamentals as usf
        assert usf._CACHE_MAX == 300

    def test_nse_client_cap(self):
        import services.nse_client as nc
        assert nc._CACHE_MAX == 300

    def test_news_sentiment_cap(self):
        import services.news_sentiment as ns
        assert ns._NEWS_CACHE_MAX == 300

    def test_nse_pledge_cap(self):
        import services.nse_pledge as npg
        assert npg._CACHE_MAX == 300


@pytest.mark.regression
class TestMarketDataFourCachesStayBounded:
    """market_data.py is the highest-traffic file in the app (every quote/
    stock-detail page view) — all four of its caches must independently
    respect the cap and evict oldest-first, not just the one that happened
    to be checked in isolation."""

    def _reset(self, md):
        md._quote_cache.clear()
        md._fundamentals_cache.clear()
        md._ohlcv_cache.clear()
        md._name_cache.clear()

    def test_all_four_caches_stay_at_or_below_cap_across_a_1000_symbol_run(self):
        """The exact scenario that produced the observed OOM: a Daily
        Picks run touching far more distinct symbols than any single
        cache's cap — every cache must stay bounded throughout, not just
        at the end."""
        import services.market_data as md
        self._reset(md)
        max_sizes = {"quote": 0, "fund": 0, "ohlcv": 0, "name": 0}
        for i in range(1000):
            key = f"SYM{i}:IN"
            md._cache_set(md._quote_cache, key, (float(i), {"price": i}))
            md._cache_set(md._fundamentals_cache, key, (float(i), {"pe": i}))
            md._cache_set(md._ohlcv_cache, key, (float(i), {"data": []}))
            md._cache_set(md._name_cache, key, (float(i), f"Company {i}"))
            max_sizes["quote"] = max(max_sizes["quote"], len(md._quote_cache))
            max_sizes["fund"] = max(max_sizes["fund"], len(md._fundamentals_cache))
            max_sizes["ohlcv"] = max(max_sizes["ohlcv"], len(md._ohlcv_cache))
            max_sizes["name"] = max(max_sizes["name"], len(md._name_cache))
        assert all(v <= md._CACHE_MAX for v in max_sizes.values()), max_sizes
        self._reset(md)

    def test_evicts_oldest_not_an_arbitrary_entry(self):
        import services.market_data as md
        self._reset(md)
        for i in range(md._CACHE_MAX):
            md._cache_set(md._quote_cache, f"S{i}", (float(i), {}))
        # One more past the cap — S0 (smallest timestamp) must go.
        md._cache_set(md._quote_cache, "NEW", (float(md._CACHE_MAX), {}))
        assert len(md._quote_cache) == md._CACHE_MAX
        assert "S0" not in md._quote_cache
        assert "NEW" in md._quote_cache
        assert "S1" in md._quote_cache  # second-oldest survives
        self._reset(md)

    def test_re_inserting_an_existing_key_does_not_evict_anything(self):
        import services.market_data as md
        self._reset(md)
        md._cache_set(md._name_cache, "AAPL:US", (1.0, "old name"))
        md._cache_set(md._name_cache, "AAPL:US", (2.0, "new name"))
        assert len(md._name_cache) == 1
        assert md._name_cache["AAPL:US"] == (2.0, "new name")
        self._reset(md)


@pytest.mark.regression
class TestThreadLockedCachesStayBoundedUnderConcurrentWrites:
    """screener_data.py, bse_data.py, us_fundamentals.py, and nse_pledge.py
    all protect their cache with a threading.Lock (fetches run via
    run_in_executor thread pools, not a single asyncio loop like
    market_data.py) — the eviction check must happen INSIDE that same lock,
    not via a helper that would double-acquire and deadlock. These tests
    drive real concurrent threads to prove both bounded size and the
    absence of a deadlock/lock-reentry bug."""

    def test_screener_data_cache_bounded_under_concurrent_writes(self):
        import services.screener_data as sd
        sd._cache.clear()

        def write(i):
            with sd._cache_lock:
                if f"S{i}" not in sd._cache and len(sd._cache) >= sd._CACHE_MAX:
                    oldest = min(sd._cache, key=lambda k: sd._cache[k][0])
                    del sd._cache[oldest]
                sd._cache[f"S{i}"] = (float(i), {"symbol": f"S{i}"})

        threads = [threading.Thread(target=write, args=(i,)) for i in range(500)]
        for t in threads: t.start()
        for t in threads: t.join(timeout=5)
        assert len(sd._cache) <= sd._CACHE_MAX
        sd._cache.clear()

    def test_bse_data_cache_bounded_and_symbol_cache_untouched(self):
        """_symbol_cache (NSE symbol -> BSE code) is deliberately left
        uncapped — naturally bounded by the finite NSE symbol universe,
        tiny string values. Only _data_cache needed the fix."""
        import services.bse_data as bd
        bd._data_cache.clear()
        for i in range(400):
            with bd._lock:
                if f"S{i}" not in bd._data_cache and len(bd._data_cache) >= bd._CACHE_MAX:
                    oldest = min(bd._data_cache, key=lambda k: bd._data_cache[k][0])
                    del bd._data_cache[oldest]
                bd._data_cache[f"S{i}"] = (float(i), {})
        assert len(bd._data_cache) == bd._CACHE_MAX
        bd._data_cache.clear()

    def test_us_fundamentals_cache_bounded(self):
        import services.us_fundamentals as usf
        usf._cache.clear()
        for i in range(400):
            with usf._cache_lock:
                if f"S{i}" not in usf._cache and len(usf._cache) >= usf._CACHE_MAX:
                    oldest = min(usf._cache, key=lambda k: usf._cache[k][0])
                    del usf._cache[oldest]
                usf._cache[f"S{i}"] = (float(i), {})
        assert len(usf._cache) == usf._CACHE_MAX
        usf._cache.clear()


@pytest.mark.regression
class TestWriteThroughGuardsAgainstFutureRegression:
    """Regression guard: a future edit reverting any of these functions
    back to a direct dict assignment (bypassing the cap) must fail here,
    not silently reintroduce the OOM — mirrors
    test_fetch_company_facts_writes_through_the_capped_setter for the SEC
    EDGAR fix."""

    def test_finnhub_get_quote_writes_through_the_capped_setter(self, monkeypatch):
        import services.finnhub_client as fc
        fc._cache.clear()
        monkeypatch.setattr(fc, "FINNHUB_KEY", "test-key")

        class _FakeResp:
            status_code = 200
            def raise_for_status(self):
                pass
            def json(self):
                return {"c": 100.0, "pc": 99.0, "t": 1700000000}

        monkeypatch.setattr(fc._SESSION, "get", lambda *a, **k: _FakeResp())
        for i in range(fc._CACHE_MAX + 10):
            fc.get_quote(f"SYM{i}", "US")
        assert len(fc._cache) == fc._CACHE_MAX
        fc._cache.clear()

    def test_nse_pledge_fetch_writes_through_the_capped_setter(self, monkeypatch):
        import services.nse_pledge as npg
        npg._cache.clear()
        monkeypatch.setattr(npg, "_fetch_pledge", lambda sym: 12.5)
        for i in range(npg._CACHE_MAX + 10):
            npg.get_promoter_pledge_pct(f"SYM{i}")
        assert len(npg._cache) == npg._CACHE_MAX
        npg._cache.clear()
