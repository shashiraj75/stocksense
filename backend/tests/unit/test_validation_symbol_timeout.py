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

This file proves two independent, honestly-scoped layers:
1. every yfinance `.history()` call in this module passes an explicit
   `timeout=YFINANCE_REQUEST_TIMEOUT_SECONDS` kwarg — best-effort (yfinance
   internally has its own cookie/crumb/retry requests that do not all
   inherit this value), never claimed as a guaranteed whole-call deadline;
2. `run_validation()`'s stall watchdog (RUN_STALL_TIMEOUT_SECONDS) is an
   INACTIVITY detector, not a hard total-runtime deadline, and not a
   thread-killer. When it fires: every already-started worker is waited
   for to genuinely finish (ThreadPoolExecutor.shutdown(wait=True,
   cancel_futures=True) — never wait=False, which would abandon live
   threads that Python cannot forcibly stop) before this function raises
   a typed `_ProviderStallDuringComputation`; no metrics are computed, no
   val_runs/val_signals row is written, and the run never becomes the
   public "latest" result. A stalled attempt falls through to the
   existing, unmodified failed_retryable/lease-release contract in
   execute_and_complete_admitted_attempt.

No real network access anywhere in this file — every yfinance call is
monkeypatched to a synthetic, deterministic OHLCV DataFrame or a
controlled fake. No database (Postgres or SQLite) is touched except via
the isolated_db fixture used for the ledger-level fail-closed proof.
"""
import inspect
import subprocess
import sys
import sqlite3
import threading
import time
from datetime import datetime, timezone
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


def _real_run_validation_worker(result_queue, horizon, universe, max_workers, trigger_type):
    """V-USACT1-B-C3 — top-level, spawn-safe test double for
    _validation_child_worker used by the handful of tests in this file
    that must exercise the REAL run_validation() (and therefore its own
    internal RUN_STALL_TIMEOUT_SECONDS drain-stall path) inside a
    genuinely spawned child. Configures its OWN freshly imported copy of
    services.validation_engine using environment variables set by the
    PARENT (env vars, unlike monkeypatches, are genuinely inherited by a
    spawned child) — never relies on inheriting any parent monkeypatch."""
    import os as _eos
    import threading as _ethreading
    from datetime import datetime as _edatetime, timezone as _etimezone
    from unittest.mock import MagicMock as _EMagicMock
    import numpy as _enp
    import pandas as _epd
    import services.validation_engine as _ve

    _ve.RUN_STALL_TIMEOUT_SECONDS = float(_eos.environ.get("V_SYMTIMEOUT_STALL_TIMEOUT", "3600"))

    basket_raw = _eos.environ.get("V_SYMTIMEOUT_BASKET", "S1")
    symbols = basket_raw.split(",")
    _ve.US_BASKET = symbols
    _ve.NIFTY_100 = symbols
    _ve.NSE_MIDCAP = symbols

    stall_symbol = _eos.environ.get("V_SYMTIMEOUT_STALL_SYMBOL", "")
    stall_seconds = float(_eos.environ.get("V_SYMTIMEOUT_STALL_SECONDS", "0"))
    uniform_seconds = float(_eos.environ.get("V_SYMTIMEOUT_UNIFORM_SECONDS", "0"))

    def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
        ws = kwargs.get("_window_stats")
        if ws is not None:
            ws["considered"] = 1
            ws["benchmark_valid"] = 1
        if uniform_seconds > 0:
            _ethreading.Event().wait(uniform_seconds)
        elif sym == stall_symbol and stall_seconds > 0:
            _ethreading.Event().wait(stall_seconds)
        return []
    _ve._backtest_stock = _fake_backtest

    def _bench_df():
        dates = _epd.bdate_range("2019-01-01", periods=300)
        close = 100.0 * _enp.cumprod(1 + _enp.random.default_rng(1).normal(0.0003, 0.008, 300))
        return _epd.DataFrame({"Close": close}, index=dates)
    mock_yf = _EMagicMock()
    mock_yf.Ticker.return_value.history.return_value = _bench_df()
    _ve.yf = mock_yf
    _ve.time.sleep = lambda *a, **k: None

    try:
        metrics = _ve.run_validation(horizon=horizon, universe=universe, max_workers=max_workers,
                                      trigger_type=trigger_type, _persist=False)
        payload = metrics.get("_persist_payload")
        now_iso = _edatetime.now(_etimezone.utc).isoformat()
        if payload is None:
            result_queue.put({"type": "error", "category": "NO_RESULT_RUN_ID", "message": None,
                               "completed_at_utc": now_iso})
            return
        result_queue.put({"type": "result", "payload": payload, "completed_at_utc": now_iso})
    except Exception as e:
        now_iso = _edatetime.now(_etimezone.utc).isoformat()
        result_queue.put({"type": "error", "category": type(e).__name__, "message": str(e)[:200],
                           "completed_at_utc": now_iso})


def _use_real_subprocess_worker(monkeypatch, *, basket, stall_symbol="", stall_seconds=0,
                                 uniform_seconds=0, stall_timeout=3600):
    monkeypatch.setattr(ve, "_validation_child_worker", _real_run_validation_worker)
    monkeypatch.setenv("V_SYMTIMEOUT_BASKET", ",".join(basket))
    monkeypatch.setenv("V_SYMTIMEOUT_STALL_SYMBOL", stall_symbol)
    monkeypatch.setenv("V_SYMTIMEOUT_STALL_SECONDS", str(stall_seconds))
    monkeypatch.setenv("V_SYMTIMEOUT_UNIFORM_SECONDS", str(uniform_seconds))
    monkeypatch.setenv("V_SYMTIMEOUT_STALL_TIMEOUT", str(stall_timeout))


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """A real, isolated SQLite database (not the _NoWriteConn stub) — for
    the ledger-level proof that a stalled attempt writes zero val_runs/
    val_signals rows, this must be genuine persistence, not a no-op."""
    db_path = str(tmp_path / "provider_stall_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    ve._init_db()
    return db_path


def _count_val_runs(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM val_runs").fetchone()[0]


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


# ── Layer 2: the stall watchdog is fail-closed — an inactivity detector that
# waits for every already-started worker to genuinely finish, then raises a
# typed exception instead of ever publishing a truncated run as successful ──

@pytest.mark.unit
class TestProviderStallFailsClosed:
    def test_stall_raises_typed_exception_not_a_normal_return(self, monkeypatch):
        """The core corrected-behavior proof. A stalled symbol must turn
        the whole run into a raised, typed failure — never a normal
        return value a caller could mistake for a completed result."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.2)
        stocks = ["AAA", "BBB", "STALLED", "CCC"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(0.6)  # outlasts the watchdog, but finite — must be waited for, not abandoned
                return [{"symbol": sym}]
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)

        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(horizon="short", universe="us", max_workers=4)

    def test_metrics_are_never_computed_on_stall(self, monkeypatch):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.2)
        stocks = ["AAA", "STALLED"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        def _boom_compute_metrics(*args, **kwargs):
            raise AssertionError("_compute_metrics was called on a stalled run — must never be reached")

        monkeypatch.setattr(ve, "_compute_metrics", _boom_compute_metrics)

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(0.6)
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)

        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(horizon="short", universe="us", max_workers=4)

    def test_no_provider_worker_survives_past_the_raised_exception(self, monkeypatch):
        """Proves ThreadPoolExecutor.shutdown(wait=True, cancel_futures=True)
        actually waits — not shutdown(wait=False), which would abandon the
        live worker. Captures the exact moment the STALLED worker finishes
        (via a threading.Event it sets itself) and asserts that timestamp
        is BEFORE run_validation raises, i.e. the function cannot return
        control to its caller while that worker is still running."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.2)
        stocks = ["AAA", "STALLED"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        worker_finished = threading.Event()
        baseline_threads = threading.active_count()

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                # Event.wait (not time.sleep, which _mock_run_validation_io
                # patches to a no-op on the shared ve.time module) — a real,
                # unpatched wait that outlasts the 0.2s watchdog.
                threading.Event().wait(0.5)
                worker_finished.set()
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)

        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(horizon="short", universe="us", max_workers=4)

        # By the time the exception has propagated here, the worker MUST
        # already be done — shutdown(wait=True) blocked until it finished.
        assert worker_finished.is_set(), (
            "run_validation raised before the stalled worker finished — "
            "a live provider thread survived past the function's return, "
            "exactly the zombie-worker defect this correction closes"
        )
        # No lingering thread left behind either.
        time.sleep(0.05)
        assert threading.active_count() == baseline_threads

    def test_pending_never_started_futures_are_cancelled(self, monkeypatch):
        """With more symbols than worker slots, some futures never even
        start running — those must be cancelled (never invoked at all),
        distinct from the one already-running worker that must be waited
        for instead of cancelled."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.2)
        stocks = ["STALLED"] + [f"QUEUED{i}" for i in range(20)]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        invoked = []

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            invoked.append(sym)
            if sym == "STALLED":
                threading.Event().wait(0.6)
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)

        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(horizon="short", universe="us", max_workers=1)

        # max_workers=1 means only STALLED itself ever actually started;
        # every QUEUED symbol was still pending (never dequeued) when the
        # watchdog fired and must have been cancelled, not invoked.
        assert "STALLED" in invoked
        assert not any(s.startswith("QUEUED") for s in invoked), (
            f"a queued-but-not-yet-started symbol was invoked instead of cancelled: {invoked}"
        )

    def test_completed_signals_before_the_stall_are_discarded_not_persisted(self, monkeypatch):
        """Symbols that DID complete successfully before the stall must
        not survive into any published result — a stall fails the WHOLE
        run closed, never a partial success built from the subset that
        happened to finish first."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.2)
        stocks = ["AAA", "BBB", "STALLED"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(0.6)
                return []
            return [{"symbol": sym, "signal_date": "2020-01-01"}]

        _mock_run_validation_io(monkeypatch, _fake_backtest)

        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(horizon="short", universe="us", max_workers=4)
        # No return value exists at all — the exception IS the only outcome;
        # nothing downstream can accidentally read a partial signals list.

    def test_run_status_marks_failed_with_provider_stall_code_not_left_running(self, monkeypatch):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.2)
        stocks = ["AAA", "STALLED"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(0.6)
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)
        with ve._status_lock:
            ve._run_status["job"] = {"failure_code": None}

        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(horizon="short", universe="us", max_workers=4)

        with ve._status_lock:
            assert ve._run_status["running"] is False
            assert ve._run_status["job"]["failure_code"] == "PROVIDER_STALL"

    def test_no_stall_normal_run_still_processes_every_symbol(self, monkeypatch):
        """Successful-control proof: the ordinary no-stall path is
        completely unchanged by this correction — full processing,
        normal return value, no exception."""
        stocks = ["AAA", "BBB", "CCC", "DDD", "EEE"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        seen = []

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            seen.append(sym)
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)
        result = ve.run_validation(horizon="short", universe="us", max_workers=3)

        assert sorted(seen) == sorted(stocks)
        assert result is not None
        with ve._status_lock:
            assert ve._run_status["progress"] == len(stocks)

    def test_run_stall_timeout_constant_exists_and_is_bounded(self):
        assert isinstance(ve.RUN_STALL_TIMEOUT_SECONDS, (int, float))
        assert 0 < ve.RUN_STALL_TIMEOUT_SECONDS < 3600


# ── Ledger-level proof: a stall through the real admitted-validation path ─────

@pytest.mark.unit
class TestProviderStallLedgerIntegrity:
    def test_stall_writes_no_val_runs_row_and_fails_the_attempt_retryable(self, monkeypatch, isolated_db):
        """Exercises the REAL admit_validation_attempt -> run_validation ->
        execute_and_complete_admitted_attempt chain (only yfinance/
        _backtest_stock are faked) to prove the existing, unmodified
        failed_retryable/lease-release contract correctly absorbs the new
        typed stall exception, and that zero rows are ever written."""
        # V-USACT1-B-C3 — stall_timeout is passed to the CHILD only (via
        # env var), deliberately leaving the PARENT's own RUN_STALL_
        # TIMEOUT_SECONDS at its generous default so the PARENT's own
        # authoritative inactivity clock (see _run_validation_in_
        # subprocess) doesn't fire first and mask what this test is
        # actually proving: run_validation's OWN internal drain-stall
        # path, executing for real inside a genuinely spawned child.
        _use_real_subprocess_worker(monkeypatch, basket=["AAA", "STALLED"],
                                     stall_symbol="STALLED", stall_seconds=0.6, stall_timeout=0.2)

        now = datetime.now(timezone.utc)
        admitted = ve.admit_validation_attempt(
            horizon="short", universe="us", trigger_type="manual", owner="stall-test", now=now,
        )
        assert admitted["ok"] is True

        result = ve.execute_and_complete_admitted_attempt(
            admitted["attempt_id"], admitted["owner"], admitted["fencing_token"],
            "short", "us", "manual",
        )

        assert result["ok"] is False
        assert _count_val_runs(isolated_db) == 0, "a stalled run must never persist a val_runs row"

    def test_exact_ledger_states_after_ordinary_provider_stall(self, monkeypatch, isolated_db):
        """Literal, not ambiguous, terminal-state assertions — the exact
        attempt status/failure_category/slot/lease/binding values, not the
        vague phrase 'failed/failed_retryable'."""
        _use_real_subprocess_worker(monkeypatch, basket=["AAA", "STALLED"],
                                     stall_symbol="STALLED", stall_seconds=0.6, stall_timeout=0.2)

        now = datetime.now(timezone.utc)
        admitted = ve.admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                                owner="stall-test", now=now)
        assert admitted["ok"] is True
        result = ve.execute_and_complete_admitted_attempt(
            admitted["attempt_id"], admitted["owner"], admitted["fencing_token"], "short", "us", "manual",
        )
        assert result["ok"] is False

        attempt = ve.get_schedule_attempt(admitted["attempt_id"])
        assert attempt["status"] == "failed"
        assert attempt["failure_category"] == "RUN_EXCEPTION"
        assert attempt["result_run_id"] is None
        assert attempt["slot_id"] is None  # manual trigger — no slot to retry

        with sqlite3.connect(isolated_db) as conn:
            lease_row = conn.execute(
                "SELECT lease_owner, active_attempt_id FROM validation_execution_leases"
            ).fetchone()
        assert lease_row == (None, None), f"lease must be fully released after a stall, got {lease_row}"


# ── V-USCAP4 — the lease must survive draining, so a second worker can never ──
# admit real overlapping provider work while the first worker's provider call
# is still alive, even after the original lease TTL has elapsed. ─────────────

def _seed_successful_result(monkeypatch, horizon="short", universe="us", owner="seed"):
    """Real end-to-end seed of a genuine prior successful val_runs row via
    the actual admitted-validation path — returns its run_id."""
    _use_real_subprocess_worker(monkeypatch, basket=["SEED1", "SEED2"])

    now = datetime.now(timezone.utc)
    admitted = ve.admit_validation_attempt(horizon=horizon, universe=universe, trigger_type="manual",
                                            owner=owner, now=now)
    assert admitted["ok"] is True
    result = ve.execute_and_complete_admitted_attempt(
        admitted["attempt_id"], admitted["owner"], admitted["fencing_token"], horizon, universe, "manual",
    )
    assert result["ok"] is True
    return result["run_id"]


@pytest.mark.unit
class TestLeaseSurvivesDrain:
    def test_worker_b_cannot_admit_while_worker_a_drains_past_original_lease_expiry(self, monkeypatch, isolated_db):
        """The core no-overlap proof. Worker A's ORIGINAL lease is
        deliberately SHORTER (1s) than its one genuinely-alive provider
        worker's runtime (2.5s) — the exact condition that matters: does
        the lease's own `expires_at` (a CAS comparison in acquire_
        validation_execution_lease, completely independent of whether A
        ever explicitly releases anything) pass while A's provider work
        is still alive? Without drain-heartbeating, A never renews during
        the wait, so by t≈1s a second worker's CAS-based reclaim succeeds
        even though A's worker keeps running until t≈2.5s — proven unsafe
        in the V-USCAP3 review. With the correction, A's heartbeat keeps
        pushing `expires_at` forward throughout the drain, so Worker B's
        CAS must keep failing for as long as A's provider worker is
        genuinely alive — checked here specifically in the window AFTER
        the original 1s would have expired but BEFORE the 2.5s worker
        finishes."""
        # V-USACT1-B-C3 — the PARENT's own RUN_STALL_TIMEOUT_SECONDS is
        # left at its generous default (this call sets the CHILD's
        # internal drain-stall value, via env var, to a value that never
        # fires either — 2.5s of genuine work is well under it) so the
        # PARENT's own authoritative inactivity clock never kills the
        # child before this test's own lease/heartbeat timing plays out.
        # The provider worker runs inside a genuine CHILD PROCESS —
        # `threading.Event` cannot observe cross-process state under real
        # spawn (a fresh interpreter, no inherited memory at all), so this
        # test uses no in-process liveness signal at all; the actual
        # safety property under test — B cannot acquire while A's worker
        # is genuinely still alive, but can once A has fully released —
        # is proven directly and unambiguously by the lease-acquisition
        # assertions below.
        _use_real_subprocess_worker(monkeypatch, basket=["STUCK"], uniform_seconds=2.5, stall_timeout=3600)

        now = datetime.now(timezone.utc)
        lease_a = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=1)
        assert lease_a["ok"] is True
        admitted_a = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                               fencing_token=lease_a["fencing_token"], now=now)
        assert admitted_a["ok"] is True
        ve.mark_attempt_running(admitted_a["id"], owner="A", fencing_token=lease_a["fencing_token"], now=now)

        # Run the real execution in a background thread — it drives
        # run_validation's stall/drain loop with a drain interval bounded
        # by the 1s lease (max(1, min(30, 1//4)) = 1s... actually below 1,
        # see production code's max(1, ...) floor — a 1s lease still gets
        # heartbeated well before expiry each cycle in the real formula).
        exec_thread = threading.Thread(
            target=lambda: ve.execute_and_complete_admitted_attempt(
                admitted_a["id"], "A", lease_a["fencing_token"], "short", "us", "manual",
                lease_duration_seconds=1, heartbeat_every_n_stocks=1,
            ),
            daemon=True,
        )
        exec_thread.start()

        # t ≈ 1.6s: well past A's ORIGINAL 1s lease expiry, well before
        # the 2.5s worker finishes. This is the decisive check. Event.wait
        # (not time.sleep, which this test's own `ve.time.sleep` patch
        # above silently neuters to a no-op, since `time` is a singleton
        # module shared with this test file's own `import time`) — a real,
        # unpatched wait.
        threading.Event().wait(1.6)

        now_b = datetime.now(timezone.utc)
        lease_b = ve.acquire_validation_execution_lease(owner="B", now=now_b, lease_duration_seconds=600)
        assert lease_b["ok"] is False, (
            f"UNSAFE: worker B acquired the lease while A's provider worker was still alive: {lease_b}"
        )

        exec_thread.join(timeout=5)
        assert not exec_thread.is_alive(), "execute_and_complete_admitted_attempt never returned"

        # NOW that A has fully terminated and completed its failure
        # transition (releasing the lease), B must be able to acquire normally.
        now_b2 = datetime.now(timezone.utc)
        lease_b2 = ve.acquire_validation_execution_lease(owner="B", now=now_b2, lease_duration_seconds=600)
        assert lease_b2["ok"] is True, f"B should be able to acquire after A fully released: {lease_b2}"

    def test_drain_heartbeats_fire_periodically_below_the_lease_duration(self, monkeypatch, isolated_db):
        # V-USACT1-B-C1 note: unlike the sibling test above, this test
        # calls ve.run_validation() directly, in-process — it never goes
        # through _run_validation_in_subprocess / execute_and_complete_
        # admitted_attempt, so the NEW parent-side authoritative
        # inactivity detector (which lives in _run_validation_in_
        # subprocess) never applies here. RUN_STALL_TIMEOUT_SECONDS below
        # still refers to run_validation's own original in-process
        # drain-stall detection, unchanged by V-USACT1-B-C1 — kept small
        # so the ~0.7s drain genuinely trips it, proving _ProviderStall
        # DuringComputation still fires from inside run_validation itself.
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr(ve, "US_BASKET", ["STUCK"])

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            threading.Event().wait(0.7)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _fake_backtest)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

        heartbeat_calls = []

        def _counting_heartbeat():
            heartbeat_calls.append(time.monotonic())
            return False  # never fenced

        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(
                horizon="short", universe="us", max_workers=2, _persist=False,
                _heartbeat=_counting_heartbeat, _lease_duration_seconds=1,
            )

        assert len(heartbeat_calls) >= 2, (
            f"expected multiple drain heartbeats over a ~0.7s drain with a bounded interval, got {len(heartbeat_calls)}"
        )
        if len(heartbeat_calls) >= 2:
            gaps = [b - a for a, b in zip(heartbeat_calls, heartbeat_calls[1:])]
            assert all(g < 1 for g in gaps), f"a drain heartbeat gap reached/exceeded the 1s lease duration: {gaps}"

    def test_heartbeat_rejection_during_drain_raises_fenced_out_not_provider_stall(self, monkeypatch):
        # V-USACT1-B-C1 note: this test also calls ve.run_validation()
        # directly, in-process — see the comment on the sibling test
        # above. RUN_STALL_TIMEOUT_SECONDS here is run_validation's own
        # original in-process drain-stall constant, unrelated to the new
        # parent-side subprocess inactivity detector.
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.1)
        monkeypatch.setattr(ve, "US_BASKET", ["STUCK"])

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            threading.Event().wait(0.5)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _fake_backtest)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

        def _rejecting_heartbeat():
            return True  # fenced out immediately

        with pytest.raises(ve._FencedOutDuringComputation):
            ve.run_validation(
                horizon="short", universe="us", max_workers=2, _persist=False,
                _heartbeat=_rejecting_heartbeat, _lease_duration_seconds=1,
            )

    def test_public_latest_preserved_with_seeded_prior_result_after_stall(self, monkeypatch, isolated_db):
        """Strengthens the prior insufficient test — an empty database
        remaining unavailable proves nothing about preserving an EXISTING
        result. This seeds a genuine prior successful run first."""
        seeded_run_id = _seed_successful_result(monkeypatch, horizon="short", universe="us")
        prior_latest = ve.get_latest_results(horizon="short", universe="us")
        assert prior_latest["available"] is True
        assert prior_latest["run_id"] == seeded_run_id

        _use_real_subprocess_worker(monkeypatch, basket=["AAA", "STALLED"],
                                     stall_symbol="STALLED", stall_seconds=0.6, stall_timeout=0.2)

        now2 = datetime.now(timezone.utc)
        admitted = ve.admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                                owner="stall", now=now2)
        assert admitted["ok"] is True
        result = ve.execute_and_complete_admitted_attempt(
            admitted["attempt_id"], admitted["owner"], admitted["fencing_token"], "short", "us", "manual",
        )
        assert result["ok"] is False

        post_latest = ve.get_latest_results(horizon="short", universe="us")
        assert post_latest["run_id"] == seeded_run_id, "public latest changed after a stall — must remain the seeded prior result"
        assert _count_val_runs(isolated_db) == 1, "no new val_runs row may appear after a stall"


# ── V-USCAP6 — wall-clock lease heartbeat interval helper ─────────────────────

@pytest.mark.unit
class TestLeaseHeartbeatInterval:
    """_lease_heartbeat_interval must be the SOLE, unconditional-runtime-
    check gate for the interval safety invariant — no reliance on a bare
    `assert` (stripped under `python -O`), no floor that makes short,
    controlled test/production leases invalid, and always strictly below
    the lease duration itself."""

    @pytest.mark.parametrize("bad", [0, 0.0, -1, -100.0, float("nan"), float("inf"), float("-inf")])
    def test_invalid_duration_raises_value_error_explicitly(self, bad):
        with pytest.raises(ValueError):
            ve._lease_heartbeat_interval(bad)

    @pytest.mark.parametrize("duration", [0.5, 1, 2, 4, 30, 60, 120, 600, 3600])
    def test_valid_duration_always_produces_interval_strictly_below_ttl(self, duration):
        interval = ve._lease_heartbeat_interval(duration)
        assert 0 < interval < duration, (
            f"interval {interval} not strictly below duration {duration}"
        )

    def test_production_ttl_yields_thirty_second_interval(self):
        assert ve._lease_heartbeat_interval(600) == 30.0

    def test_no_unnecessary_floor_for_very_short_controlled_leases(self):
        # A 1s lease used throughout this test suite's controlled
        # reproductions must not be pushed to/above its own duration by an
        # artificial minimum floor (the old 0.25s floor was harmless for
        # 1s but this proves the new helper imposes none at all).
        interval = ve._lease_heartbeat_interval(1)
        assert interval == 0.25 or (0 < interval < 1)

    def test_safety_boundary_is_not_a_bare_assert(self):
        src = inspect.getsource(ve._lease_heartbeat_interval)
        assert "assert " not in src, (
            "the lease-heartbeat-interval safety boundary must use an "
            "unconditional runtime check (raise ValueError), not a bare "
            "`assert` — assertions are stripped entirely under `python -O`, "
            "which would silently remove this production safety boundary"
        )

    def test_run_validation_drain_path_no_longer_relies_on_bare_assert(self):
        src = inspect.getsource(ve.run_validation)
        assert "assert drain_interval" not in src
        assert "assert " not in src, (
            "run_validation must not gate lease-interval safety behind a "
            "bare assert anywhere in its body"
        )


# ── V-USCAP6 — unified wall-clock heartbeat throughout normal execution ───────

@pytest.mark.unit
class TestWallClockLeaseHeartbeat:
    """Medium 1 from the V-USCAP5 independent review: lease renewal was
    still primarily progress-count driven (every heartbeat_every_n_stocks
    completions). A sequence of individually-completing-but-slow symbols
    could consume more than the lease TTL between checkpoints without ever
    tripping the (unrelated) all-zero-completions inactivity watchdog —
    meaning the lease could expire during perfectly ordinary execution,
    long before the drain correction (V-USCAP4) is ever entered. These
    tests prove a genuine, unconditional wall-clock heartbeat now runs
    throughout the ENTIRE executor lifecycle, not only during a
    stall/drain — independent of how many (or how few) symbols have
    completed."""

    def test_slow_but_completing_symbols_cannot_expire_the_lease_without_wallclock_heartbeat(
        self, monkeypatch, isolated_db
    ):
        """The core RED/GREEN proof. Three symbols each take 0.6s and
        genuinely COMPLETE one at a time (never triggering the inactivity
        watchdog, which is set generously here), and
        heartbeat_every_n_stocks=100 guarantees the pre-existing
        progress-driven checkpoint never fires either (only 3 symbols
        total). The ONLY thing that can keep worker A's 1s lease alive
        past the original expiry is a genuine wall-clock heartbeat running
        independently of both progress and inactivity detection."""
        # V-USACT1-B-C3 — RUN_STALL_TIMEOUT_SECONDS here applies to the
        # PARENT's own authoritative inactivity clock (see
        # _run_validation_in_subprocess) — 5.0s is generous relative to
        # the ~0.6s per-symbol progress cadence, so it never fires falsely.
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 5.0)
        _use_real_subprocess_worker(monkeypatch, basket=["S1", "S2", "S3"],
                                     uniform_seconds=0.6, stall_timeout=5.0)

        now = datetime.now(timezone.utc)
        lease_a = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=1)
        assert lease_a["ok"] is True
        admitted_a = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                               fencing_token=lease_a["fencing_token"], now=now)
        assert admitted_a["ok"] is True
        ve.mark_attempt_running(admitted_a["id"], owner="A", fencing_token=lease_a["fencing_token"], now=now)

        exec_thread = threading.Thread(
            target=lambda: ve.execute_and_complete_admitted_attempt(
                admitted_a["id"], "A", lease_a["fencing_token"], "short", "us", "manual",
                lease_duration_seconds=1, heartbeat_every_n_stocks=100, max_workers=1,
            ),
            daemon=True,
        )
        exec_thread.start()

        # t ≈ 1.6s: past A's original 1s lease; S1 (~0.6s) and S2 (~1.2s)
        # have completed, S3 is still genuinely mid-flight (max_workers=1
        # forces strict sequential execution) — no stall was ever
        # detected, no progress checkpoint ever fired.
        threading.Event().wait(1.6)
        assert exec_thread.is_alive(), "test bug — run finished too early; widen the margin"

        now_b = datetime.now(timezone.utc)
        lease_b = ve.acquire_validation_execution_lease(owner="B", now=now_b, lease_duration_seconds=600)
        assert lease_b["ok"] is False, (
            f"UNSAFE: worker B acquired the lease during ordinary slow-but-completing "
            f"execution while A was still genuinely alive: {lease_b}"
        )

        exec_thread.join(timeout=3)
        assert not exec_thread.is_alive(), "execute_and_complete_admitted_attempt never returned"

        # After A fully finishes and releases, B must be able to acquire normally.
        now_b2 = datetime.now(timezone.utc)
        lease_b2 = ve.acquire_validation_execution_lease(owner="B", now=now_b2, lease_duration_seconds=600)
        assert lease_b2["ok"] is True, f"B should be able to acquire after A fully released: {lease_b2}"

    def test_wallclock_heartbeats_fire_periodically_during_ordinary_slow_execution(self, monkeypatch):
        """Trickle-completion cadence proof (Stage 2C): several futures
        each resolve near the heartbeat interval, one after another — the
        maximum observed gap between heartbeats must stay strictly below
        the lease TTL even though a future resolves on almost every wait
        iteration."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 5.0)
        monkeypatch.setattr(ve, "US_BASKET", ["S1", "S2", "S3", "S4"])

        def _slow(sym, horizon, bench_df, market, **kwargs):
            ws = kwargs.get("_window_stats")
            if ws is not None:
                ws["considered"] = 1
                ws["benchmark_valid"] = 1
            threading.Event().wait(0.3)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _slow)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

        heartbeat_calls = []

        def _counting_heartbeat():
            heartbeat_calls.append(time.monotonic())
            return False

        ve.run_validation(
            horizon="short", universe="us", max_workers=1, _persist=False,
            _heartbeat=_counting_heartbeat, _lease_duration_seconds=1,
        )

        assert len(heartbeat_calls) >= 2, (
            f"expected multiple wall-clock heartbeats over a ~1.2s run with a bounded "
            f"interval, got {len(heartbeat_calls)}"
        )
        gaps = [b - a for a, b in zip(heartbeat_calls, heartbeat_calls[1:])]
        assert all(g < 1 for g in gaps), (
            f"a heartbeat gap reached/exceeded the 1s lease duration even though "
            f"futures kept completing near the interval: {gaps}"
        )

    def test_heartbeat_rejection_during_ordinary_execution_raises_fenced_out(self, monkeypatch):
        """Stage 2E — a wall-clock heartbeat that reports fencing loss
        during ORDINARY (non-stalled) execution must cancel queued work,
        drain already-running work, persist nothing, and raise the
        existing fenced-out contract — never the provider-stall path."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 5.0)
        monkeypatch.setattr(ve, "US_BASKET", ["S1", "S2", "S3"])

        def _slow(sym, horizon, bench_df, market, **kwargs):
            ws = kwargs.get("_window_stats")
            if ws is not None:
                ws["considered"] = 1
                ws["benchmark_valid"] = 1
            threading.Event().wait(0.5)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _slow)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

        def _rejecting_heartbeat():
            return True

        with pytest.raises(ve._FencedOutDuringComputation):
            ve.run_validation(
                horizon="short", universe="us", max_workers=1, _persist=False,
                _heartbeat=_rejecting_heartbeat, _lease_duration_seconds=1,
            )

    def test_queued_futures_never_start_when_wallclock_heartbeat_is_rejected(self, monkeypatch):
        """Companion to the previous test: not-yet-started symbols must be
        cancelled, not invoked, the moment a wall-clock heartbeat reports
        fencing loss — mirrors the existing stall-path guarantee."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 5.0)
        stocks = ["RUNNING"] + [f"QUEUED{i}" for i in range(10)]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        invoked = []

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            invoked.append(sym)
            ws = kwargs.get("_window_stats")
            if ws is not None:
                ws["considered"] = 1
                ws["benchmark_valid"] = 1
            if sym == "RUNNING":
                # Long enough that the single worker thread is still busy
                # with RUNNING (never freed to dequeue a QUEUED* task) by
                # the time the immediate pre-loop heartbeat below fires
                # and the main thread cancels every not-yet-started future.
                threading.Event().wait(1.0)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _fake_backtest)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

        def _rejecting_heartbeat():
            return True  # fenced out on the very first (immediate, pre-loop) heartbeat

        with pytest.raises(ve._FencedOutDuringComputation):
            ve.run_validation(
                horizon="short", universe="us", max_workers=1, _persist=False,
                _heartbeat=_rejecting_heartbeat, _lease_duration_seconds=1,
            )

        assert "RUNNING" in invoked
        assert not any(s.startswith("QUEUED") for s in invoked), (
            f"a queued-but-not-yet-started symbol was invoked instead of cancelled: {invoked}"
        )

    def test_wallclock_heartbeat_does_not_delay_or_suppress_genuine_stall_detection(self, monkeypatch):
        """Stage 2F — a wall-clock heartbeat firing throughout a genuine
        stall must never reset or extend the SEPARATE inactivity clock:
        total elapsed time must track the truly-stuck worker's own
        duration, not be pushed out by ongoing heartbeat activity."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 1.0)
        monkeypatch.setattr(ve, "US_BASKET", ["STUCK"])

        def _stuck(sym, horizon, bench_df, market, **kwargs):
            threading.Event().wait(2.5)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _stuck)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

        heartbeat_calls = []

        def _counting_heartbeat():
            heartbeat_calls.append(time.monotonic())
            return False

        t0 = time.monotonic()
        with pytest.raises(ve._ProviderStallDuringComputation):
            ve.run_validation(
                horizon="short", universe="us", max_workers=1, _persist=False,
                _heartbeat=_counting_heartbeat, _lease_duration_seconds=4,
            )
        elapsed = time.monotonic() - t0

        # The stuck worker itself takes ~2.5s; heartbeats (interval ~1s)
        # fire throughout but must not push total elapsed time
        # meaningfully past the worker's own real duration.
        assert 2.0 <= elapsed <= 4.0, (
            f"elapsed={elapsed}s suggests heartbeat activity distorted stall "
            f"detection/drain timing (expected close to the 2.5s stuck-worker duration)"
        )
        assert len(heartbeat_calls) >= 1

    def test_wallclock_heartbeat_never_increments_progress_or_run_status(self, monkeypatch):
        """Stage 2F — a heartbeat call, by itself, must never be mistaken
        for a completed symbol: progress_callback must only ever be
        invoked on genuine per-symbol completion, never once per
        heartbeat tick."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 5.0)
        monkeypatch.setattr(ve, "US_BASKET", ["S1", "S2"])

        def _slow(sym, horizon, bench_df, market, **kwargs):
            ws = kwargs.get("_window_stats")
            if ws is not None:
                ws["considered"] = 1
                ws["benchmark_valid"] = 1
            threading.Event().wait(0.6)
            return []

        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _slow)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

        progress_calls = []
        heartbeat_calls = {"n": 0}

        def _counting_heartbeat():
            heartbeat_calls["n"] += 1
            return False

        ve.run_validation(
            horizon="short", universe="us", max_workers=1, _persist=False,
            progress_callback=lambda done, total: progress_calls.append((done, total)),
            _heartbeat=_counting_heartbeat, _lease_duration_seconds=1,
        )

        assert progress_calls == [(1, 2), (2, 2)], (
            f"progress_callback must fire exactly once per genuine symbol completion, "
            f"got {progress_calls}"
        )
        assert heartbeat_calls["n"] >= 2, "expected multiple wall-clock heartbeat ticks over ~1.2s"

    def test_direct_caller_without_heartbeat_params_is_unaffected(self, monkeypatch):
        """Stage 2D — a caller that predates this correction and never
        passes _heartbeat/_lease_duration_seconds must behave exactly as
        before: no heartbeat scheduling, no new exception types, normal
        completion."""
        stocks = ["AAA", "BBB", "CCC"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        seen = []

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            seen.append(sym)
            return []

        _mock_run_validation_io(monkeypatch, _fake_backtest)
        result = ve.run_validation(horizon="short", universe="us", max_workers=3)

        assert sorted(seen) == sorted(stocks)
        assert result is not None
        with ve._status_lock:
            assert ve._run_status["progress"] == len(stocks)


# ── Truly non-returning worker: subprocess-isolated, externally bounded ───────

_NEVER_RETURNS_SCRIPT = r"""
import sys, threading, time
sys.path.insert(0, {backend_path!r})
from unittest.mock import MagicMock
import services.validation_engine as ve

ve.RUN_STALL_TIMEOUT_SECONDS = 0.3
ve.US_BASKET = ["STUCK"]

def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
    threading.Event().wait()  # LITERALLY never returns
    return []

def _wrapped(*args, **kwargs):
    ws = kwargs.get("_window_stats")
    if ws is not None:
        ws["considered"] = 1
        ws["benchmark_valid"] = 1
    return _fake_backtest(*args, **kwargs)

import numpy as np, pandas as pd
def _bench_df():
    dates = pd.bdate_range("2019-01-01", periods=300)
    close = 100.0 * np.cumprod(1 + np.random.default_rng(1).normal(0.0003, 0.008, 300))
    return pd.DataFrame({{"Close": close}}, index=dates)

mock_yf = MagicMock()
mock_yf.Ticker.return_value.history.return_value = _bench_df()
ve.yf = mock_yf
ve._init_db = lambda: None
ve._USE_POSTGRES = False

class _NoWriteConn:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, *a, **k):
        class C: lastrowid = 0
        return C()
    def executemany(self, *a, **k): pass
ve._get_sqlite_conn = lambda: _NoWriteConn()
ve._backtest_stock = _wrapped

print("STARTING", flush=True)
ve.run_validation(horizon="short", universe="us", max_workers=2)
print("RETURNED — THIS MUST NEVER PRINT", flush=True)
"""


@pytest.mark.unit
class TestTrulyNonReturningWorkerIsBoundedExternally:
    def test_run_validation_never_returns_while_a_worker_is_permanently_stuck(self, tmp_path):
        """The one demonstration involving a worker that ACTUALLY never
        terminates. Per this correction's honest limitation, run_validation
        has no hard kill switch — it correctly stays blocked forever rather
        than returning early and abandoning the thread. Proven by running
        it in a subprocess with a strict EXTERNAL kill timeout: the
        subprocess must print "STARTING" and then be killed by the test's
        own timeout — never reach "RETURNED", and never exit on its own.
        This is the fail-closed contract, not a bug: no thread leak is
        possible because the function simply never lets control escape
        while the worker survives.
        """
        script = _NEVER_RETURNS_SCRIPT.format(backend_path=str(_BACKEND_DIR))
        script_path = tmp_path / "never_returns_demo.py"
        script_path.write_text(script)

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True, text=True, timeout=3,
            )
            pytest.fail(
                f"subprocess exited on its own (returncode={result.returncode}) — "
                f"run_validation returned/raised while its worker was still stuck. "
                f"stdout={result.stdout!r} stderr={result.stderr[-1000:]!r}"
            )
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            assert "STARTING" in stdout, f"subprocess never even started: {stdout!r}"
            assert "RETURNED" not in stdout, (
                "run_validation returned control to its caller while a worker was "
                "permanently stuck — this is the exact zombie-worker defect"
            )


_BACKEND_DIR = None  # set below, after ve import resolves the real path
import os as _os
_BACKEND_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(ve.__file__)))


# ── Documentation honesty: comments must not overclaim what the code does ─────

@pytest.mark.unit
class TestDocumentationHonesty:
    def test_source_does_not_describe_watchdog_as_a_hard_deadline(self):
        src = inspect.getsource(ve)
        forbidden = ["hard deadline", "guaranteed total-run", "kills the thread", "terminates the worker"]
        lowered = src.lower()
        for phrase in forbidden:
            assert phrase not in lowered, f"source overclaims: {phrase!r} found in validation_engine.py"

    def test_source_does_not_claim_shutdown_wait_false_as_safe_here(self):
        src = inspect.getsource(ve.run_validation)
        assert "shutdown(wait=false" not in src.lower(), (
            "run_validation must not use shutdown(wait=False) — it does not terminate "
            "live worker threads and must never be relied on as a safety mechanism here"
        )
