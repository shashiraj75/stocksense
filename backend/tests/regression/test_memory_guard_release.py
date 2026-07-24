"""
2026-07-23/24 US Daily Picks memory incident — genuine safe-cache release.

Before this fix the 72% "cleanup" tier logged "releasing caches and
collecting" while only executing gc.collect() — no cache was ever released
(directly observed in the 2026-07-24 production run: thirteen consecutive
cleanup log lines while memory ratcheted 72.2% -> 79.9%). The tier now:

  1. clears an explicit allowlist of safe, deterministic-to-rebuild TTL
     caches (each under its module's own lock where one exists);
  2. runs gc.collect();
  3. best-effort glibc malloc_trim(0) — the only lever that returns
     fragmented allocator arenas to the OS (never guaranteed to reduce
     RSS, never allowed to fail generation);
  4. records memory before, after clear+gc, and after trim.

These tests use mocked readers/clearers throughout — they are deterministic
regression evidence for the control flow and allowlist discipline, NOT a
claim about production RSS behavior (that is the controlled production
verification step, separately).
"""
from unittest.mock import patch

import pytest


def _fake_reader(used_pct, limit=100):
    def reader():
        return (used_pct, limit)
    return reader


def _stepping_reader(values):
    """Reader returning successive (used, limit) tuples, repeating the last."""
    state = {"i": 0}

    def reader():
        v = values[min(state["i"], len(values) - 1)]
        state["i"] += 1
        return v
    return reader


def test_crossing_65_warns_but_does_not_abort_or_clear():
    from services.memory_guard import MemoryCircuitBreaker

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(66))
    with patch("services.memory_guard.clear_safe_caches") as mock_clear, \
         patch("services.memory_guard.log") as mock_log:
        breaker.check("phase_1", 30, 1200)
    assert mock_log.warning.call_count == 1
    mock_clear.assert_not_called()
    assert breaker.cleanup_invoked is False


def test_crossing_72_invokes_allowlist_clear_then_gc_then_trim_in_order():
    from services.memory_guard import MemoryCircuitBreaker

    order: list[str] = []
    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(73))
    with patch("services.memory_guard.clear_safe_caches",
               side_effect=lambda: order.append("clear") or ["sec_edgar_facts"]) as mock_clear, \
         patch("services.memory_guard.gc") as mock_gc, \
         patch("services.memory_guard.malloc_trim",
               side_effect=lambda: order.append("trim") or (True, True)) as mock_trim:
        mock_gc.collect.side_effect = lambda: order.append("gc")
        breaker.check("phase_1", 570, 1197)

    mock_clear.assert_called_once()
    mock_gc.collect.assert_called_once()
    mock_trim.assert_called_once()
    assert order == ["clear", "gc", "trim"], "must clear caches BEFORE gc, trim last"
    assert breaker.cleanup_invoked is True
    assert breaker.malloc_trim_available is True
    assert breaker.malloc_trim_invoked is True


def test_release_memory_records_measurements_before_and_after():
    from services.memory_guard import MemoryCircuitBreaker

    # before -> after clear+gc -> after trim
    breaker = MemoryCircuitBreaker(
        "US", reader=_stepping_reader([(90, 100), (80, 100), (75, 100)])
    )
    with patch("services.memory_guard.clear_safe_caches", return_value=["a"]), \
         patch("services.memory_guard.gc"), \
         patch("services.memory_guard.malloc_trim", return_value=(True, True)):
        summary = breaker.release_memory("phase_1", processed=570, total=1197)

    assert summary["used_before"] == 90
    assert summary["used_after_gc"] == 80
    assert summary["used_after_trim"] == 75
    assert summary["cleared"] == ["a"]
    assert summary["trim_available"] is True and summary["trim_invoked"] is True
    # Peak tracking must have seen the highest reading.
    assert breaker.peak_used == 90
    assert breaker.last_used == 75


def test_missing_or_failing_malloc_trim_never_fails_the_job():
    from services.memory_guard import MemoryCircuitBreaker

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(73))
    with patch("services.memory_guard.clear_safe_caches", return_value=[]), \
         patch("services.memory_guard.gc"), \
         patch("services.memory_guard.malloc_trim", return_value=(False, False)):
        breaker.check("phase_1", 30, 1200)  # must not raise
    assert breaker.malloc_trim_invoked is False

    # Even an unexpected exception inside the release path is swallowed.
    breaker2 = MemoryCircuitBreaker("US", reader=_fake_reader(73))
    with patch("services.memory_guard.clear_safe_caches", side_effect=RuntimeError("boom")), \
         patch("services.memory_guard.gc"):
        breaker2.check("phase_1", 30, 1200)  # must not raise


def test_malloc_trim_helper_is_noop_off_linux_and_swallows_all_failures():
    import services.memory_guard as mg

    with patch.object(mg.sys, "platform", "darwin"):
        assert mg.malloc_trim() == (False, False)

    # On "linux" but with libc resolution failing: unavailable, not an error.
    with patch.object(mg.sys, "platform", "linux"), \
         patch("ctypes.CDLL", side_effect=OSError("no libc")):
        available, invoked = mg.malloc_trim()
    assert (available, invoked) == (False, False)


def test_crossing_80_still_raises_exactly_once_and_before_any_cleanup():
    from services.memory_guard import MemoryCircuitBreaker, DailyPicksMemoryLimitError

    breaker = MemoryCircuitBreaker("US", reader=_fake_reader(81))
    with patch("services.memory_guard.clear_safe_caches") as mock_clear:
        with pytest.raises(DailyPicksMemoryLimitError) as exc_info:
            breaker.check("phase_1", 990, 1197)
    # Abort takes strict priority: no cache clearing on the abort path.
    mock_clear.assert_not_called()
    assert "80" in str(exc_info.value)


def test_allowlist_clears_only_the_allowlisted_caches():
    """Populate every allowlisted cache plus representative NON-allowlisted
    state (regime cache, alpha-engine IC cache) — clear_safe_caches() must
    empty exactly the former and never touch the latter."""
    from services import memory_guard as mg
    from services import sec_edgar_adapter as sea
    from services import us_fundamentals as usf
    from services import prediction_engine as pe
    from services import market_data as md
    from services import news_sentiment as ns
    from services.alpha_engine import ic_engine as ic

    with sea._facts_lock:
        sea._facts_cache[999999] = (0.0, {"facts": {}})
    with usf._cache_lock:
        usf._cache["TESTSYM"] = (0.0, {"available": False})
    pe._pred_cache["TESTSYM:US:short"] = (0.0, {})
    md._fundamentals_cache["TESTSYM"] = (0.0, {})
    with ns._news_lock:
        ns._news_cache["TESTKEY"] = (0.0, {})

    # Non-allowlisted state that must survive untouched:
    pe._regime_cache["US"] = (0.0, {"regime_id": 0})
    ic._cache["sentinel"] = {"weights": {}}

    cleared = mg.clear_safe_caches()

    assert set(cleared) == {
        "sec_edgar_facts", "us_fundamentals", "prediction_results",
        "market_data_fundamentals", "news_sentiment",
    }
    assert 999999 not in sea._facts_cache
    assert "TESTSYM" not in usf._cache
    assert "TESTSYM:US:short" not in pe._pred_cache
    assert "TESTSYM" not in md._fundamentals_cache
    assert "TESTKEY" not in ns._news_cache
    # Non-allowlisted caches untouched:
    assert "US" in pe._regime_cache
    assert "sentinel" in ic._cache
    # cleanup
    del pe._regime_cache["US"]
    del ic._cache["sentinel"]


def test_one_failing_clearer_does_not_stop_the_others():
    import services.memory_guard as mg

    calls = []
    broken = [("bad", lambda: (_ for _ in ()).throw(RuntimeError("x"))),
              ("good", lambda: calls.append("good"))]
    with patch.object(mg, "_SAFE_CACHE_CLEARERS", broken):
        cleared = mg.clear_safe_caches()
    assert cleared == ["good"]
    assert calls == ["good"]


def test_hardened_evictors_survive_concurrent_clear_race():
    """The lock-less allowlisted caches' evictors must tolerate the cache
    being emptied between their len() check and min() eviction — the exact
    race a mid-request clear_safe_caches() introduces."""
    from services import prediction_engine as pe
    from services import market_data as md

    class _VanishingDict(dict):
        """len() reports full, but iteration sees an empty dict — simulates
        a clear() landing between the evictor's len check and its min()."""

        def __len__(self):
            return 10_000

        def __iter__(self):
            return iter(())

    for module in (pe, md):
        cache = _VanishingDict()
        # Must not raise ValueError (min of empty) — insert still succeeds.
        module._cache_set(cache, "k", (0.0, {}))
        assert cache["k"] == (0.0, {})
