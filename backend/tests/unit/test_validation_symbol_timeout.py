"""
Bounded yfinance timeout + whole-run stall watchdog for the walk-forward
validation engine.

Confirmed production incident (2026-08-14): a medium/us validation run
started 2026-08-13T00:51:35Z, reached 25/42 symbols normally, then stalled
with zero progress for 15+ minutes and counting — a prior run reportedly
took ~8h5m for the same 42-symbol US universe versus 5-6 minutes for the
112-134-symbol Indian universes. Root cause: none of
services/validation_engine.py's `yf.Ticker(...).history(...)` calls passed
an explicit timeout, and `run_validation()`'s per-symbol ThreadPoolExecutor
stage waited on `as_completed(futures)` with no bound at all — a single
stalled Yahoo Finance connection could block a worker thread (and therefore
the whole run) indefinitely, since Python threads cannot be forcibly
stopped.

This file proves two independent layers of the fix:
1. every yfinance `.history()` call in this module passes an explicit
   `timeout=YFINANCE_REQUEST_TIMEOUT_SECONDS` kwarg;
2. even if that per-request timeout somehow failed to bound a call (or a
   code path — `.info` — has no timeout kwarg to pass at all),
   `run_validation()`'s own stall watchdog (RUN_STALL_TIMEOUT_SECONDS)
   guarantees the whole run finishes with a partial, honestly-labeled
   result rather than hanging, and that symbols which complete normally
   are entirely unaffected.

No real network access anywhere in this file — every yfinance call is
monkeypatched to a synthetic, deterministic OHLCV DataFrame or a
controlled fake. No database (Postgres or SQLite) is touched.
"""
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from services import validation_engine as ve


# ── Synthetic, deterministic OHLCV fixture (no network) ───────────────────────

def _synthetic_ohlcv(n=220, seed=42):
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n)
    rets = rng.normal(0.0004, 0.01, n)
    close = 100.0 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = close * (1 + rng.normal(0, 0.001, n))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume},
        index=dates,
    )


def _valid_benchmark_df(n=300, seed=99):
    """A benchmark DataFrame that passes _validate_benchmark_acquisition
    for every horizon — sorted, unique, finite, positive Close, enough
    rows."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n)
    rets = rng.normal(0.0003, 0.008, n)
    close = 100.0 * np.cumprod(1 + rets)
    return pd.DataFrame({"Close": close}, index=dates)


class _NoWriteCursor:
    lastrowid = 0


class _NoWriteConn:
    """SQLite connection stand-in that performs no I/O at all."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, *args, **kwargs):
        return _NoWriteCursor()

    def executemany(self, *args, **kwargs):
        pass


@pytest.fixture(autouse=True)
def _reset_run_status():
    with ve._status_lock:
        ve._run_status["running"] = False
    yield
    with ve._status_lock:
        ve._run_status["running"] = False


@pytest.fixture(autouse=True)
def _stub_sec_edgar_as_of(monkeypatch):
    monkeypatch.setattr(
        ve.sec_edgar_adapter, "get_fundamentals_as_of",
        lambda symbol, as_of: {"available": False, "reason": "stubbed in test"},
    )


def _mock_run_validation_io(monkeypatch, backtest_stock_fake):
    """Mocks every I/O boundary run_validation() touches — yfinance, DB
    init, and the SQLite connection — mirroring
    test_validation_engine_market_routing.py's
    _mock_validation_io_boundaries helper, but always reports one genuine
    benchmark-valid window per call so a stub returning [] never trips the
    unrelated post-alignment coverage gate."""
    def _wrapped_backtest(*args, **kwargs):
        window_stats = kwargs.get("_window_stats")
        if window_stats is not None:
            window_stats["considered"] = window_stats.get("considered", 0) + 1
            window_stats["benchmark_valid"] = window_stats.get("benchmark_valid", 0) + 1
        return backtest_stock_fake(*args, **kwargs)

    mock_yf = MagicMock()
    mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()

    monkeypatch.setattr(ve, "_backtest_stock", _wrapped_backtest)
    monkeypatch.setattr(ve, "yf", mock_yf)
    monkeypatch.setattr(ve, "_init_db", lambda: None)
    monkeypatch.setattr(ve, "_get_sqlite_conn", lambda: _NoWriteConn())
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)  # bounded retry — never slow a test


# ── Layer 1: every yfinance .history() call passes an explicit timeout ────────

@pytest.mark.unit
class TestExplicitYfinanceTimeout:
    def test_per_symbol_history_call_passes_explicit_timeout(self, monkeypatch):
        calls = []

        class _RecordingTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, **kwargs):
                calls.append(kwargs)
                return _synthetic_ohlcv()

            @property
            def info(self):
                return {}

        monkeypatch.setattr(ve.yf, "Ticker", _RecordingTicker)
        ve._backtest_stock("AAPL", "short", _valid_benchmark_df(), "US", universe="us")

        assert calls, "yf.Ticker(...).history() was never called"
        assert calls[0].get("timeout") == ve.YFINANCE_REQUEST_TIMEOUT_SECONDS
        assert ve.YFINANCE_REQUEST_TIMEOUT_SECONDS is not None
        assert ve.YFINANCE_REQUEST_TIMEOUT_SECONDS > 0

    def test_benchmark_history_call_passes_explicit_timeout(self, monkeypatch):
        calls = []

        class _RecordingTicker:
            def __init__(self, symbol):
                self.symbol = symbol

            def history(self, **kwargs):
                calls.append(kwargs)
                return _valid_benchmark_df()

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            return []

        monkeypatch.setattr(ve.yf, "Ticker", _RecordingTicker)
        _mock_run_validation_io(monkeypatch, _fake_backtest)
        # _mock_run_validation_io replaces ve.yf with a MagicMock — restore
        # the recording Ticker afterwards so the benchmark fetch (the one
        # thing this test cares about) still goes through it.
        monkeypatch.setattr(ve.yf, "Ticker", _RecordingTicker)

        ve.run_validation(horizon="short", universe="us", max_workers=2)

        assert calls, "benchmark yf.Ticker(...).history() was never called"
        assert calls[0].get("timeout") == ve.YFINANCE_REQUEST_TIMEOUT_SECONDS


# ── Layer 2: whole-run stall watchdog bounds the run regardless of cause ──────

@pytest.mark.unit
class TestRunStallWatchdog:
    def test_one_stalled_symbol_does_not_hang_the_whole_run(self, monkeypatch):
        """The core production-incident regression test. Before this fix,
        run_validation()'s `as_completed(futures)` loop had no bound at
        all — a single symbol whose fetch never returned would hang the
        entire run indefinitely (Python threads cannot be forcibly
        stopped). RUN_STALL_TIMEOUT_SECONDS is patched tiny so the
        watchdog fires almost immediately; the "stalled" symbol sleeps a
        few seconds (finite, so its worker thread eventually exits
        cleanly and interpreter shutdown is never at risk of hanging) —
        long enough to prove the watchdog returns well BEFORE that sleep
        naturally completes.
        """
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.3)

        stocks = ["AAA", "BBB", "STALLED", "CCC"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(2.0)  # never reached within the test's assertion window — Event.wait is immune to the ve.time.sleep patch below
                return [{"symbol": sym}]
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)

        start = time.monotonic()
        result = ve.run_validation(horizon="short", universe="us", max_workers=4)
        elapsed = time.monotonic() - start

        # Must return well before STALLED's own 2.0s sleep would — proves
        # the watchdog, not the slow symbol itself, ended the wait.
        assert elapsed < 1.5, f"run_validation took {elapsed:.2f}s — stall watchdog did not bound the run"
        assert result is not None

    def test_stalled_symbol_is_logged_as_bounded_timeout_not_silently_dropped(self, monkeypatch):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.3)
        stocks = ["AAA", "STALLED"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(2.0)
                return []
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)
        ve.run_validation(horizon="short", universe="us", max_workers=4)

        with ve._status_lock:
            log_lines = list(ve._run_status["log"])
        assert any("STALLED" in line and "SYMBOL_VALIDATION_TIMEOUT" in line for line in log_lines), (
            f"no bounded-timeout log entry for the stalled symbol — log was: {log_lines}"
        )

    def test_non_stalled_symbols_are_fully_unaffected_by_one_stall(self, monkeypatch):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.3)
        stocks = ["AAA", "BBB", "STALLED", "CCC"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        seen = []

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(2.0)
                return []
            seen.append(sym)
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)
        ve.run_validation(horizon="short", universe="us", max_workers=4)

        assert set(seen) == {"AAA", "BBB", "CCC"}

    def test_no_stall_normal_run_still_processes_every_symbol(self, monkeypatch):
        """Regression proof that swapping as_completed(futures) for the
        new wait()-based loop changed nothing about the ordinary,
        no-stall path — every symbol is still processed exactly once."""
        stocks = ["AAA", "BBB", "CCC", "DDD", "EEE"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        seen = []

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            seen.append(sym)
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)
        ve.run_validation(horizon="short", universe="us", max_workers=3)

        assert sorted(seen) == sorted(stocks)
        with ve._status_lock:
            assert ve._run_status["progress"] == len(stocks)

    def test_run_stall_timeout_constant_exists_and_is_bounded(self):
        assert isinstance(ve.RUN_STALL_TIMEOUT_SECONDS, (int, float))
        assert 0 < ve.RUN_STALL_TIMEOUT_SECONDS < 3600
