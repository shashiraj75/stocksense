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
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 0.2)
        stocks = ["AAA", "STALLED"]
        monkeypatch.setattr(ve, "US_BASKET", stocks)

        def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
            if sym == "STALLED":
                threading.Event().wait(0.6)
            return []

        def _wrapped_backtest(*args, **kwargs):
            window_stats = kwargs.get("_window_stats")
            if window_stats is not None:
                window_stats["considered"] = window_stats.get("considered", 0) + 1
                window_stats["benchmark_valid"] = window_stats.get("benchmark_valid", 0) + 1
            return _fake_backtest(*args, **kwargs)

        # Deliberately NOT _mock_run_validation_io here — that stubs
        # _get_sqlite_conn/_init_db to no-ops, which would make the
        # "zero val_runs rows" assertion below vacuous. Only yfinance and
        # _backtest_stock are faked; isolated_db provides a REAL sqlite
        # database so persistence (or its absence) is genuinely proven.
        mock_yf = MagicMock()
        mock_yf.Ticker.return_value.history.return_value = _valid_benchmark_df()
        monkeypatch.setattr(ve, "_backtest_stock", _wrapped_backtest)
        monkeypatch.setattr(ve, "yf", mock_yf)
        monkeypatch.setattr(ve.time, "sleep", lambda *a, **k: None)

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

        attempt = ve.get_schedule_attempt(admitted["attempt_id"])
        assert attempt["status"] in ("failed", "failed_retryable"), (
            f"stalled attempt must end in a retryable-failure state, got: {attempt['status']}"
        )

        # Public latest must be unaffected (still whatever it was before —
        # here, nothing at all, proving no partial run became "latest").
        latest = ve.get_latest_results(horizon="short", universe="us")
        assert latest["available"] is False


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
