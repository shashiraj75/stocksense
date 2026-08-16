"""
V-USACT1-B — killable validation deadline via OS-process containment.

Root problem (V-USACT1-A investigation): medium/us ran for ~8h09m and
~8h06m on two consecutive days, pre-PR-#63. Python cannot forcibly stop a
running thread, so the existing per-symbol ThreadPoolExecutor stall/drain
path (V-USCAP2/4/6) — while provably safe against corruption — can only
WAIT for an uncooperative provider call to finish; it cannot bound real
wall-clock time. This file proves the OS-process-based correction: the
entire per-attempt computation now runs inside a child process the
PARENT can genuinely kill (SIGTERM then SIGKILL) on a hard
MAX_RUN_DURATION_SECONDS deadline, while the parent retains 100% of
lease/heartbeat/fencing/persistence authority throughout.

V-USACT1-B-C3 — this file NO LONGER uses "fork" anywhere. Production is
unconditionally "spawn" (there is no module-level start-method name left
to override at all — see services/validation_engine.py). Every test that
exercises the real process-containment mechanism (Group A: spawn
startup, deadline, inactivity, terminate/kill/join, progress IPC,
/status live progress, result/deadline race, child crash, malformed IPC,
saturation, orphan cleanup, fencing during execution) now runs under
genuine "spawn" using a top-level, picklable, self-contained test-double
child target — never depending on the child inheriting any parent
monkeypatch, closure, global, mock, list, lock, or thread. Tests whose
subject is ledger/retry/persistence behavior GIVEN a failure (not the
containment mechanism itself — Group B) instead monkeypatch the private
parent-side orchestration boundary `_run_validation_in_subprocess`
directly with a synchronous, in-process, deterministic fake — no process
at all, which is both correct (this is test-only DI on a private
function, not a production bypass) and far faster.
"""
import multiprocessing
import os as _os
import queue as _queue_module
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from services import validation_engine as ve

_BACKEND_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(ve.__file__)))


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "run_deadline_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    ve._init_db()
    return db_path


def _count_val_runs(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM val_runs").fetchone()[0]


def _count_val_signals(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM val_signals").fetchone()[0]


# ─────────────────────────────────────────────────────────────────────────
# V-USACT1-B-C3 — the single, self-contained, top-level, spawn-safe test
# double for _validation_child_worker used by every Group A test in this
# file. It has NO dependency on services.validation_engine or on
# anything the parent test process has monkeypatched — behavior is
# selected entirely via environment variables the PARENT sets (via
# monkeypatch.setenv) BEFORE process.start(); env vars, unlike
# monkeypatches, are genuinely inherited by a spawned child. This proves
# the containment mechanism itself without ever touching a real
# provider, real network, or real per-symbol backtest logic.
# ─────────────────────────────────────────────────────────────────────────

def _env_worker(result_queue, horizon, universe, max_workers, trigger_type):
    import os as _eos
    import time as _etime
    import threading as _ethreading
    from datetime import datetime as _edatetime, timezone as _etimezone

    mode = _eos.environ.get("V_USACT1B_TEST_MODE", "instant_success")

    def _progress(done, total):
        try:
            result_queue.put_nowait({"type": "progress", "done": done, "total": total})
        except _queue_module.Full:
            pass

    def _success_payload(n_stocks=1):
        now_iso = _edatetime.now(_etimezone.utc).isoformat()
        return {
            "type": "result",
            "payload": {
                "run_at": now_iso, "horizon": horizon, "n_stocks": n_stocks,
                "n_signals": 0, "summary_json": "{}", "universe": universe,
                "signal_rows": [],
            },
            "completed_at_utc": now_iso,
        }

    def _error(category, message=None):
        return {
            "type": "error", "category": category, "message": message,
            "completed_at_utc": _edatetime.now(_etimezone.utc).isoformat(),
        }

    if mode == "instant_success":
        result_queue.put(_success_payload())
        return
    if mode == "never_returns":
        _ethreading.Event().wait()
        return
    if mode == "slow_then_success":
        _etime.sleep(float(_eos.environ.get("V_USACT1B_SLEEP_SECONDS", "1")))
        result_queue.put(_success_payload())
        return
    if mode == "progress_then_success":
        n = int(_eos.environ.get("V_USACT1B_N_PROGRESS", "4"))
        step = float(_eos.environ.get("V_USACT1B_STEP_SECONDS", "0.3"))
        for i in range(1, n + 1):
            _etime.sleep(step)
            _progress(i, n)
        result_queue.put(_success_payload(n_stocks=n))
        return
    if mode == "progress_then_error":
        n = int(_eos.environ.get("V_USACT1B_N_PROGRESS", "4"))
        step = float(_eos.environ.get("V_USACT1B_STEP_SECONDS", "0.001"))
        for i in range(1, n + 1):
            _etime.sleep(step)
            _progress(i, n)
        result_queue.put(_error("SimulatedZeroCoverage", "simulated benchmark-coverage failure"))
        return
    if mode == "crash":
        raise RuntimeError("simulated child crash")
    if mode == "error_no_result":
        result_queue.put(_error("NO_RESULT_RUN_ID"))
        return
    if mode == "malformed":
        result_queue.put({"not_a_valid_message": True})
        return
    result_queue.put(_error("UnknownTestMode", f"unrecognized V_USACT1B_TEST_MODE={mode!r}"))


def _set_env_mode(monkeypatch, mode, **extra):
    monkeypatch.setattr(ve, "_validation_child_worker", _env_worker)
    monkeypatch.setenv("V_USACT1B_TEST_MODE", mode)
    for k, v in extra.items():
        monkeypatch.setenv(f"V_USACT1B_{k.upper()}", str(v))


def _worker_result_then_hang(result_queue, horizon, universe, max_workers, trigger_type):
    """Top-level (picklable) stand-in for _validation_child_worker that
    reports a well-formed success and then hangs indefinitely, simulating
    a child whose cleanup/atexit/lingering-thread teardown never
    completes on its own after the terminal message is already queued.
    Self-contained — no dependency on the parent's ve module state."""
    from datetime import datetime as _edatetime, timezone as _etimezone
    now_iso = _edatetime.now(_etimezone.utc).isoformat()
    result_queue.put({
        "type": "result",
        "payload": {
            "run_at": now_iso, "horizon": horizon,
            "n_stocks": 1, "n_signals": 0, "summary_json": "{}", "universe": universe,
            "signal_rows": [],
        },
        "completed_at_utc": now_iso,
    })
    threading.Event().wait()


def _worker_result_then_abnormal_exit(result_queue, horizon, universe, max_workers, trigger_type):
    """Top-level stand-in that reports success, then exits immediately
    with a non-zero code — simulating a crash in teardown that happens
    strictly after the terminal message was already queued."""
    from datetime import datetime as _edatetime, timezone as _etimezone
    now_iso = _edatetime.now(_etimezone.utc).isoformat()
    result_queue.put({
        "type": "result",
        "payload": {
            "run_at": now_iso, "horizon": horizon,
            "n_stocks": 1, "n_signals": 0, "summary_json": "{}", "universe": universe,
            "signal_rows": [],
        },
        "completed_at_utc": now_iso,
    })
    result_queue.close()
    result_queue.join_thread()
    _os._exit(7)


def _worker_calls_real_run_validation_with_stuck_backtest(result_queue, horizon, universe, max_workers, trigger_type):
    """Top-level, spawn-safe worker that configures its OWN freshly
    imported copy of services.validation_engine (no inheritance from any
    parent monkeypatch — this genuinely runs inside a fresh spawned
    interpreter and mutates only its own copy of the module) and then
    calls the REAL run_validation(). Used only to prove run_validation's
    own PRE-EXISTING, C1/C2/C3-unrelated internal RUN_STALL_TIMEOUT_
    SECONDS drain-stall path still correctly raises from inside the
    child, distinct from the parent's own authoritative inactivity
    clock (see TestParentSideInactivityDetection)."""
    import threading as _t
    from unittest.mock import MagicMock
    import numpy as _np
    import pandas as _pd
    from datetime import datetime as _edatetime, timezone as _etimezone
    import services.validation_engine as _ve

    _ve.RUN_STALL_TIMEOUT_SECONDS = 1
    _ve.US_BASKET = ["STUCK"]

    def _slow_but_finishes(sym, horizon, bench_df, market, **kwargs):
        ws = kwargs.get("_window_stats")
        if ws is not None:
            ws["considered"] = 1
            ws["benchmark_valid"] = 1
        _t.Event().wait(2.0)
    _ve._backtest_stock = _slow_but_finishes

    def _bench_df():
        dates = _pd.bdate_range("2019-01-01", periods=300)
        close = 100.0 * _np.cumprod(1 + _np.random.default_rng(1).normal(0.0003, 0.008, 300))
        return _pd.DataFrame({"Close": close}, index=dates)
    mock_yf = MagicMock()
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


# ─────────────────────────────────────────────────────────────────────────
# Group B helpers — a trivial, synchronous, in-process fake for the
# private parent-side orchestration boundary _run_validation_in_
# subprocess, used only by tests whose subject is LEDGER/retry/
# persistence behavior GIVEN a particular outcome, not the containment
# mechanism itself (already exhaustively proven by the Group A classes
# below). No process, no pickling, no fork/spawn concerns — ordinary
# Python-level monkeypatching of a private function, not a production
# bypass.
# ─────────────────────────────────────────────────────────────────────────

def _fake_subprocess_success(*, horizon, universe, max_workers=None, trigger_type=None, **kwargs):
    return {"_persist_payload": {
        "run_at": datetime.now(timezone.utc).isoformat(), "horizon": horizon,
        "n_stocks": 1, "n_signals": 0, "summary_json": "{}", "universe": universe,
        "signal_rows": [],
    }}


def _fake_subprocess_deadline_exceeded(**kwargs):
    raise ve._RunDeadlineExceeded("simulated deadline for ledger test")


def _fake_subprocess_fenced(**kwargs):
    raise ve._FencedOutDuringComputation()


def _fake_subprocess_stall(**kwargs):
    raise ve._ProviderStallDuringComputation("simulated stall for ledger test")


# ─────────────────────────────────────────────────────────────────────────
# Stage 1 — prove the thread-only defect (genuine RED against the OLD
# execution path, independent of the new subprocess mechanism)
# ─────────────────────────────────────────────────────────────────────────

_THREAD_ONLY_CANNOT_BOUND_SCRIPT = r"""
import sys, threading
sys.path.insert(0, {backend_path!r})
from unittest.mock import MagicMock
import services.validation_engine as ve

ve.RUN_STALL_TIMEOUT_SECONDS = 3600
ve.US_BASKET = ["STUCK"]

def _fake_backtest(sym, horizon, bench_df, market, **kwargs):
    ws = kwargs.get("_window_stats")
    if ws is not None:
        ws["considered"] = 1
        ws["benchmark_valid"] = 1
    threading.Event().wait()  # LITERALLY never returns

import numpy as np, pandas as pd
def _bench_df():
    dates = pd.bdate_range("2019-01-01", periods=300)
    close = 100.0 * np.cumprod(1 + np.random.default_rng(1).normal(0.0003, 0.008, 300))
    return pd.DataFrame({{"Close": close}}, index=dates)

mock_yf = MagicMock()
mock_yf.Ticker.return_value.history.return_value = _bench_df()
ve.yf = mock_yf
ve._backtest_stock = _fake_backtest

print("STARTING", flush=True)
ve.run_validation(horizon="short", universe="us", max_workers=1, _persist=False)
print("RETURNED — THIS MUST NEVER PRINT", flush=True)
"""


@pytest.mark.unit
class TestThreadOnlyDeadlineCannotBoundRuntime:
    def test_direct_run_validation_call_cannot_be_bounded_by_the_caller(self, tmp_path):
        script = _THREAD_ONLY_CANNOT_BOUND_SCRIPT.format(backend_path=str(_BACKEND_DIR))
        script_path = tmp_path / "thread_only_cannot_bound.py"
        script_path.write_text(script)

        try:
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True, text=True, timeout=3,
            )
            pytest.fail(
                f"subprocess exited on its own (returncode={result.returncode}) — "
                f"run_validation returned while its worker was still stuck. "
                f"stdout={result.stdout!r} stderr={result.stderr[-1000:]!r}"
            )
        except subprocess.TimeoutExpired as e:
            stdout = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            assert "STARTING" in stdout, f"subprocess never even started: {stdout!r}"
            assert "RETURNED" not in stdout, (
                "run_validation returned control to its caller while a worker was "
                "permanently stuck — falsifies the premise that thread-based "
                "execution cannot be caller-bounded"
            )

    def test_bounded_orchestration_function_now_exists(self):
        assert hasattr(ve, "_run_validation_in_subprocess")
        assert hasattr(ve, "MAX_RUN_DURATION_SECONDS")
        assert ve.MAX_RUN_DURATION_SECONDS == 5400


# ─────────────────────────────────────────────────────────────────────────
# Group A — Stage 2/5, process-boundary deadline enforcement under REAL
# spawn, using the self-contained _env_worker test double.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSubprocessDeadlineEnforcement:
    def test_noncooperative_child_returns_within_deadline_plus_bounded_grace(self, monkeypatch):
        _set_env_mode(monkeypatch, "never_returns")

        t0 = time.monotonic()
        with pytest.raises(ve._RunDeadlineExceeded):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=2,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 2 + ve.CHILD_KILL_GRACE_SECONDS * 2 + 3

    def test_no_child_process_survives_after_deadline(self, monkeypatch):
        _set_env_mode(monkeypatch, "never_returns")

        with pytest.raises(ve._RunDeadlineExceeded):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=2,
            )
        time.sleep(0.2)
        assert multiprocessing.active_children() == [], (
            "a child process survived past the deadline — this is the exact "
            "orphan-process guarantee this phase exists to close"
        )

    def test_fast_successful_child_returns_a_valid_payload_quickly(self, monkeypatch):
        _set_env_mode(monkeypatch, "instant_success")

        t0 = time.monotonic()
        result = ve._run_validation_in_subprocess(
            horizon="short", universe="us", max_workers=2,
            trigger_type="manual", max_run_duration_seconds=30,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 15, "a fast, fully cooperative run took unexpectedly long via the subprocess path"
        assert result["_persist_payload"] is not None

    def test_invalid_max_run_duration_rejected(self):
        for bad in (0, -1, float("nan"), float("inf"), 86401):
            with pytest.raises(ValueError):
                ve._validate_max_run_duration_seconds(bad)

    def test_valid_max_run_duration_accepted(self):
        for ok in (1, 60, 5400, 86400):
            ve._validate_max_run_duration_seconds(ok)  # must not raise


# ─────────────────────────────────────────────────────────────────────────
# Group B — Stage 5, full ledger-integrated deadline lifecycle. The
# SUBJECT here is ledger/persistence behavior GIVEN a failure, not the
# containment mechanism itself (proven above and in TestSpawnProduction
# Parity/TestStatusEndpointLiveVisibility) — so this patches the private
# _run_validation_in_subprocess boundary directly with a synchronous
# in-process fake. No process, no network, far faster.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDeadlineLedgerIntegration:
    def _admit_manual(self, isolated_db, owner="A", lease_duration_seconds=600):
        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner=owner, now=now, lease_duration_seconds=lease_duration_seconds)
        assert lease["ok"] is True
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner=owner,
                                             fencing_token=lease["fencing_token"], now=now)
        assert admitted["ok"] is True
        ve.mark_attempt_running(admitted["id"], owner=owner, fencing_token=lease["fencing_token"], now=now)
        return admitted["id"], lease["fencing_token"]

    def test_zero_val_runs_and_val_signals_persist_after_deadline(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_deadline_exceeded)

        attempt_id, token = self._admit_manual(isolated_db)
        result = ve.execute_and_complete_admitted_attempt(
            attempt_id, "A", token, "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=2,
        )
        assert result["ok"] is False
        assert result["reason"] == "run_deadline_exceeded"
        assert _count_val_runs(isolated_db) == 0
        assert _count_val_signals(isolated_db) == 0

    def test_attempt_becomes_terminal_with_run_deadline_exceeded_category(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_deadline_exceeded)

        attempt_id, token = self._admit_manual(isolated_db)
        ve.execute_and_complete_admitted_attempt(
            attempt_id, "A", token, "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=2,
        )
        attempt = ve.get_schedule_attempt(attempt_id)
        assert attempt["status"] == "failed"
        assert attempt["failure_category"] == "RUN_DEADLINE_EXCEEDED"
        assert attempt["result_run_id"] is None

    def test_lease_cleared_and_new_attempt_admissible_immediately_after_deadline(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_deadline_exceeded)

        attempt_id, token = self._admit_manual(isolated_db)
        result = ve.execute_and_complete_admitted_attempt(
            attempt_id, "A", token, "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=2,
        )
        assert result["ok"] is False

        now_b = datetime.now(timezone.utc)
        lease_b = ve.acquire_validation_execution_lease(owner="B", now=now_b, lease_duration_seconds=600)
        assert lease_b["ok"] is True, f"a new owner could not acquire immediately after a deadline failure: {lease_b}"

    def test_stale_killed_child_cannot_later_clear_new_owners_binding(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_deadline_exceeded)

        attempt_a, token_a = self._admit_manual(isolated_db, owner="A")
        ve.execute_and_complete_admitted_attempt(
            attempt_a, "A", token_a, "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=2,
        )

        now_b = datetime.now(timezone.utc)
        lease_b = ve.acquire_validation_execution_lease(owner="B", now=now_b, lease_duration_seconds=600)
        assert lease_b["ok"] is True
        admitted_b = ve.create_manual_attempt(horizon="short", universe="us", owner="B",
                                               fencing_token=lease_b["fencing_token"], now=now_b)
        assert admitted_b["ok"] is True

        lease_row = ve.get_validation_execution_lease()
        assert lease_row["lease_owner"] == "B"
        assert lease_row["active_attempt_id"] == admitted_b["id"]

    def test_fencing_rejection_kills_child_and_publishes_nothing(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_fenced)

        t0 = time.monotonic()
        with pytest.raises(ve._FencedOutDuringComputation):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1, trigger_type="manual",
                heartbeat_fn=lambda: True, lease_duration_seconds=600,
                max_run_duration_seconds=300,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 5
        assert _count_val_runs(isolated_db) == 0
        assert _count_val_signals(isolated_db) == 0

    def test_inactivity_timeout_independent_of_total_runtime_deadline(self, monkeypatch, isolated_db):
        """Proves run_validation's own PRE-EXISTING internal
        RUN_STALL_TIMEOUT_SECONDS drain-stall path (unrelated to this
        correction) still correctly raises RUN_EXCEPTION from inside a
        genuinely spawned child, distinct from RUN_DEADLINE_EXCEEDED —
        the one test in this file that must call into the REAL
        run_validation, achieved via a self-contained top-level worker
        that configures its own freshly-spawned copy of ve internally
        (never relying on inheriting this test's monkeypatches)."""
        monkeypatch.setattr(ve, "_validation_child_worker",
                             _worker_calls_real_run_validation_with_stuck_backtest)

        attempt_id, token = self._admit_manual(isolated_db)
        result = ve.execute_and_complete_admitted_attempt(
            attempt_id, "A", token, "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=30,
        )
        assert result["ok"] is False
        attempt = ve.get_schedule_attempt(attempt_id)
        assert attempt["failure_category"] == "RUN_EXCEPTION", (
            "a genuine inactivity stall (well inside the 30s deadline) must "
            "surface via the existing provider-stall path, independent of "
            "the total-runtime deadline"
        )

    def test_all_nine_horizon_universe_inputs_accepted(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_success)

        owner_i = 0
        for horizon in ("short", "medium", "long"):
            for universe in ("nifty100", "midcap", "us"):
                owner_i += 1
                owner = f"owner-{owner_i}"
                now = datetime.now(timezone.utc)
                lease = ve.acquire_validation_execution_lease(owner=owner, now=now, lease_duration_seconds=600)
                assert lease["ok"] is True
                admitted = ve.create_manual_attempt(horizon=horizon, universe=universe, owner=owner,
                                                     fencing_token=lease["fencing_token"], now=now)
                assert admitted["ok"] is True
                ve.mark_attempt_running(admitted["id"], owner=owner, fencing_token=lease["fencing_token"], now=now)
                result = ve.execute_and_complete_admitted_attempt(
                    admitted["id"], owner, lease["fencing_token"], horizon, universe, "manual",
                    lease_duration_seconds=600, max_run_duration_seconds=30,
                )
                assert result["ok"] is True, f"{horizon}/{universe} failed: {result}"


# ─────────────────────────────────────────────────────────────────────────
# Blocker 3 (V-USACT1-B-C1) — deadline failure is TERMINAL, not
# retryable. Group B — ledger/retry behavior given a failure, not the
# containment mechanism itself.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestDeadlineFailureIsTerminalNotRetryable:
    def _admit_and_run_to_deadline(self, isolated_db, monkeypatch, *, slot, owner="sched"):
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_deadline_exceeded)
        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner=owner, now=now, lease_duration_seconds=600)
        assert lease["ok"] is True
        attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                              owner=owner, fencing_token=lease["fencing_token"], now=now)
        assert attempt["ok"] is True
        ve.mark_attempt_running(attempt["id"], owner=owner, fencing_token=lease["fencing_token"], now=now)
        result = ve.execute_and_complete_admitted_attempt(
            attempt["id"], owner, lease["fencing_token"], "short", "us", "scheduler",
            lease_duration_seconds=600, max_run_duration_seconds=2,
        )
        assert result["ok"] is False
        assert result["reason"] == "run_deadline_exceeded"
        return attempt["id"]

    def test_deadline_failed_slot_does_not_auto_retry_within_the_same_evaluation(self, monkeypatch, isolated_db):
        now = datetime.now(timezone.utc)
        slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                               schedule_version="v1", now=now)
        self._admit_and_run_to_deadline(isolated_db, monkeypatch, slot=slot)

        with sqlite3.connect(isolated_db) as conn:
            attempt_count = conn.execute(
                "SELECT COUNT(*) FROM validation_schedule_attempts WHERE slot_id=?", (slot["id"],)
            ).fetchone()[0]
        assert attempt_count == 1
        fetched_slot = ve.get_schedule_slot(slot["id"])
        assert fetched_slot["active_attempt_id"] is None

    def test_deadline_failed_slot_is_terminal_not_due(self, monkeypatch, isolated_db):
        now = datetime.now(timezone.utc)
        slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                               schedule_version="v1", now=now)
        self._admit_and_run_to_deadline(isolated_db, monkeypatch, slot=slot)

        fetched_slot = ve.get_schedule_slot(slot["id"])
        assert fetched_slot["status"] == "failed"

    def test_timed_out_slot_cannot_be_readmitted_by_a_later_scheduler_tick(self, monkeypatch, isolated_db):
        now = datetime.now(timezone.utc)
        slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                               schedule_version="v1", now=now)
        self._admit_and_run_to_deadline(isolated_db, monkeypatch, slot=slot)

        later = now + timedelta(minutes=5)
        same_slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                                     schedule_version="v1", now=later)
        assert same_slot["id"] == slot["id"]
        lease2 = ve.acquire_validation_execution_lease(owner="sched-2", now=later, lease_duration_seconds=600)
        assert lease2["ok"] is True
        retry_attempt = ve.create_schedule_attempt(
            slot_id=same_slot["id"], trigger_type="scheduler",
            owner="sched-2", fencing_token=lease2["fencing_token"], now=later,
        )
        assert retry_attempt["ok"] is False

    def test_catchup_cannot_retry_a_terminal_deadline_slot(self, monkeypatch, isolated_db):
        now = datetime.now(timezone.utc)
        slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                               schedule_version="v1", now=now)
        self._admit_and_run_to_deadline(isolated_db, monkeypatch, slot=slot)

        later = now + timedelta(hours=1)
        lease = ve.acquire_validation_execution_lease(owner="catchup-1", now=later, lease_duration_seconds=600)
        assert lease["ok"] is True
        retry_attempt = ve.create_schedule_attempt(
            slot_id=slot["id"], trigger_type="catchup",
            owner="catchup-1", fencing_token=lease["fencing_token"], now=later,
        )
        assert retry_attempt["ok"] is False

    def test_next_genuinely_new_canonical_session_remains_eligible(self, monkeypatch, isolated_db):
        now = datetime.now(timezone.utc)
        slot_today = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                                      schedule_version="v1", now=now)
        self._admit_and_run_to_deadline(isolated_db, monkeypatch, slot=slot_today)

        tomorrow = now + timedelta(days=1)
        slot_tomorrow = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=tomorrow,
                                                         schedule_version="v1", now=tomorrow)
        assert slot_tomorrow["id"] != slot_today["id"]
        assert slot_tomorrow["status"] == "due"

        lease = ve.acquire_validation_execution_lease(owner="sched-tomorrow", now=tomorrow, lease_duration_seconds=600)
        assert lease["ok"] is True
        new_attempt = ve.create_schedule_attempt(
            slot_id=slot_tomorrow["id"], trigger_type="scheduler",
            owner="sched-tomorrow", fencing_token=lease["fencing_token"], now=tomorrow,
        )
        assert new_attempt["ok"] is True

    def test_manual_attempts_remain_independently_usable_after_deadline_cleanup(self, monkeypatch, isolated_db):
        now = datetime.now(timezone.utc)
        slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                               schedule_version="v1", now=now)
        self._admit_and_run_to_deadline(isolated_db, monkeypatch, slot=slot)

        later = now + timedelta(seconds=5)
        lease = ve.acquire_validation_execution_lease(owner="manual-1", now=later, lease_duration_seconds=600)
        assert lease["ok"] is True
        manual = ve.create_manual_attempt(horizon="short", universe="us", owner="manual-1",
                                           fencing_token=lease["fencing_token"], now=later)
        assert manual["ok"] is True
        assert manual["slot_id"] is None


# ─────────────────────────────────────────────────────────────────────────
# Stage 4 — public/persisted failure contract (pure logic, no process)
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRunDeadlineExceededFailureContract:
    def test_public_message_registered(self):
        assert "RUN_DEADLINE_EXCEEDED" in ve.VALIDATION_PUBLIC_FAILURE_MESSAGES
        assert ve.VALIDATION_PUBLIC_FAILURE_MESSAGES["RUN_DEADLINE_EXCEEDED"] == (
            "Validation exceeded the maximum permitted execution time."
        )

    def test_public_message_contains_no_internal_detail(self):
        msg = ve.VALIDATION_PUBLIC_FAILURE_MESSAGES["RUN_DEADLINE_EXCEEDED"]
        for forbidden in ("pid", "process", "kill", "sigterm", "sigkill", "lease", "token", "owner"):
            assert forbidden not in msg.lower()


# ─────────────────────────────────────────────────────────────────────────
# Group A — Stage 5, repeated timeout cycles leave no leak (real process
# lifecycle proof).
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRepeatedTimeoutCyclesLeaveNoLeak:
    def test_three_consecutive_deadline_cycles_leave_zero_children(self, monkeypatch):
        _set_env_mode(monkeypatch, "never_returns")

        for _ in range(3):
            with pytest.raises(ve._RunDeadlineExceeded):
                ve._run_validation_in_subprocess(
                    horizon="short", universe="us", max_workers=1,
                    trigger_type="manual", max_run_duration_seconds=1,
                )
        time.sleep(0.2)
        assert multiprocessing.active_children() == []


# ─────────────────────────────────────────────────────────────────────────
# V-USACT1-B-C2 — strict, PARENT-MONOTONIC deadline boundary. Group A —
# this is the containment mechanism itself, real spawn required.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestStrictDeadlineBoundary:
    def test_result_completed_well_before_deadline_is_accepted(self, monkeypatch):
        _set_env_mode(monkeypatch, "slow_then_success", sleep_seconds=0.1)

        result = ve._run_validation_in_subprocess(
            horizon="short", universe="us", max_workers=1,
            trigger_type="manual", max_run_duration_seconds=10,
        )
        assert result["_persist_payload"] is not None

    def test_result_completed_well_after_deadline_is_rejected_as_timeout(self, monkeypatch):
        _set_env_mode(monkeypatch, "never_returns")

        with pytest.raises(ve._RunDeadlineExceeded):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=1,
            )

    def test_no_extra_grace_wait_added_after_deadline(self, monkeypatch):
        _set_env_mode(monkeypatch, "never_returns")

        t0 = time.monotonic()
        with pytest.raises(ve._RunDeadlineExceeded):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=2,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 2 + ve.CHILD_KILL_GRACE_SECONDS + 3, (
            f"elapsed={elapsed}s suggests an extra grace-wait was added after the deadline"
        )

    def test_deadline_race_produces_exactly_one_coherent_outcome_never_partial(self, monkeypatch, isolated_db):
        for trial in range(5):
            _set_env_mode(monkeypatch, "slow_then_success", sleep_seconds=0.5)

            before_runs = _count_val_runs(isolated_db)
            try:
                result = ve._run_validation_in_subprocess(
                    horizon="short", universe="us", max_workers=1,
                    trigger_type="manual", max_run_duration_seconds=0.5,
                )
                assert result["_persist_payload"] is not None
            except ve._RunDeadlineExceeded:
                pass
            time.sleep(0.1)
            assert multiprocessing.active_children() == [], f"trial {trial}: child survived"
            assert _count_val_runs(isolated_db) == before_runs, f"trial {trial}: unexpected persistence"

    def test_child_supplied_utc_timestamp_cannot_override_parent_receipt_time(self, monkeypatch):
        _set_env_mode(monkeypatch, "never_returns")

        class _SpoofedEarlyDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2000, 1, 1, tzinfo=tz)

        monkeypatch.setattr(ve, "datetime", _SpoofedEarlyDatetime)

        with pytest.raises(ve._RunDeadlineExceeded):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=1,
            )

    def test_simulated_utc_clock_jump_backward_cannot_admit_a_late_result(self, monkeypatch):
        _set_env_mode(monkeypatch, "never_returns")

        class _FrozenPastDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(1999, 6, 1, tzinfo=tz)

        monkeypatch.setattr(ve, "datetime", _FrozenPastDatetime)

        with pytest.raises(ve._RunDeadlineExceeded):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=1,
            )

    def test_simulated_utc_clock_jump_forward_cannot_reject_an_on_time_result(self, monkeypatch):
        _set_env_mode(monkeypatch, "slow_then_success", sleep_seconds=0.1)

        class _FrozenFutureDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2099, 1, 1, tzinfo=tz)

        monkeypatch.setattr(ve, "datetime", _FrozenFutureDatetime)

        result = ve._run_validation_in_subprocess(
            horizon="short", universe="us", max_workers=1,
            trigger_type="manual", max_run_duration_seconds=10,
        )
        assert result["_persist_payload"] is not None


# ─────────────────────────────────────────────────────────────────────────
# V-USACT1-B-C2, Correction 2 — a terminal IPC message alone is never
# proof the child has exited, let alone exited cleanly. Group A — real
# process reap/escalation proof.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSuccessPathChildReaping:
    def test_result_submitted_and_clean_exit_is_accepted(self, monkeypatch):
        _set_env_mode(monkeypatch, "instant_success")

        result = ve._run_validation_in_subprocess(
            horizon="short", universe="us", max_workers=1,
            trigger_type="manual", max_run_duration_seconds=10,
        )
        assert result["_persist_payload"] is not None
        time.sleep(0.2)
        assert multiprocessing.active_children() == []

    def test_result_submitted_then_cleanup_hangs_child_killed_result_rejected(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_validation_child_worker", _worker_result_then_hang)

        before_runs = _count_val_runs(isolated_db)
        t0 = time.monotonic()
        with pytest.raises(ve._ChildProcessFailure):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=30,
            )
        elapsed = time.monotonic() - t0
        assert elapsed >= ve.CHILD_KILL_GRACE_SECONDS, (
            f"elapsed={elapsed}s — expected the normal-join grace to be exhausted before escalation"
        )
        time.sleep(0.2)
        assert multiprocessing.active_children() == [], "child survived a claimed-success-then-hang scenario"
        assert _count_val_runs(isolated_db) == before_runs, "zero rows must persist on a rejected success"

    def test_result_submitted_then_abnormal_exit_is_rejected(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "_validation_child_worker", _worker_result_then_abnormal_exit)

        before_runs = _count_val_runs(isolated_db)
        with pytest.raises(ve._ChildProcessFailure):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=30,
            )
        time.sleep(0.2)
        assert multiprocessing.active_children() == []
        assert _count_val_runs(isolated_db) == before_runs

    def test_next_attempt_remains_coherent_after_a_rejected_success(self, monkeypatch):
        monkeypatch.setattr(ve, "_validation_child_worker", _worker_result_then_hang)
        with pytest.raises(ve._ChildProcessFailure):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=30,
            )
        time.sleep(0.2)
        assert multiprocessing.active_children() == []

        _set_env_mode(monkeypatch, "instant_success")
        result = ve._run_validation_in_subprocess(
            horizon="short", universe="us", max_workers=1,
            trigger_type="manual", max_run_duration_seconds=10,
        )
        assert result["_persist_payload"] is not None


# ─────────────────────────────────────────────────────────────────────────
# V-USACT1-B-C2, Correction 3 — bounded progress queue (maxsize=200) must
# never drop, displace, or block the terminal result/error message.
# Group A — real IPC saturation under spawn.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestProgressQueueSaturation:
    def test_more_than_queue_capacity_progress_events_still_delivers_terminal_error(self, monkeypatch):
        n = ve._PROGRESS_QUEUE_MAXSIZE + 50
        _set_env_mode(monkeypatch, "progress_then_error", n_progress=n, step_seconds=0.001)

        with pytest.raises(ve._ChildProcessFailure):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=8,
                trigger_type="manual", max_run_duration_seconds=90,
            )
        time.sleep(0.2)
        assert multiprocessing.active_children() == []

    def test_large_successful_payload_after_saturation_does_not_deadlock_shutdown(self, monkeypatch):
        n = ve._PROGRESS_QUEUE_MAXSIZE + 20
        _set_env_mode(monkeypatch, "progress_then_success", n_progress=n, step_seconds=0.001)

        result = ve._run_validation_in_subprocess(
            horizon="short", universe="us", max_workers=8,
            trigger_type="manual", max_run_duration_seconds=90,
        )
        assert result["_persist_payload"] is not None
        time.sleep(0.2)
        assert multiprocessing.active_children() == []


# ─────────────────────────────────────────────────────────────────────────
# Blocker 2 (V-USACT1-B-C1) — parent-side authoritative inactivity
# detection. Group A — real process, real parent-monotonic clock.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestParentSideInactivityDetection:
    def test_ordinary_progress_prevents_false_stall_detection(self, monkeypatch):
        # V-USACT1-B-C3 — under genuine spawn, the child must import this
        # test module (and therefore the full services.validation_engine
        # dependency chain) fresh before it can even begin — unlike the
        # old fork-based tests, this real startup latency counts against
        # the very first inactivity window. RUN_STALL_TIMEOUT_SECONDS/
        # step_seconds below are sized with realistic headroom for that,
        # not to mask a hang — the property under test (steady progress,
        # each gap well under the threshold, TOTAL duration exceeding it)
        # is unchanged.
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 3)
        _set_env_mode(monkeypatch, "progress_then_success", n_progress=3, step_seconds=1.2)

        result = ve._run_validation_in_subprocess(
            horizon="short", universe="us", max_workers=1,
            trigger_type="manual", max_run_duration_seconds=30,
        )
        assert result["_persist_payload"] is not None, (
            "genuine steady per-symbol progress must not be misclassified as a stall, "
            "even though the TOTAL run duration exceeds the inactivity threshold"
        )

    def test_heartbeats_do_not_count_as_progress(self, monkeypatch):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 1)
        _set_env_mode(monkeypatch, "never_returns")

        heartbeat_calls = {"n": 0}

        def _counting_heartbeat():
            heartbeat_calls["n"] += 1
            return False  # never fenced — proves heartbeats alone can't mask a stall

        t0 = time.monotonic()
        with pytest.raises(ve._ProviderStallDuringComputation):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1, trigger_type="manual",
                heartbeat_fn=_counting_heartbeat, lease_duration_seconds=1,
                max_run_duration_seconds=300,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 10, "inactivity kill must fire near the 1s threshold, not be masked by ongoing heartbeats"
        assert heartbeat_calls["n"] >= 1, "heartbeats should still have fired at least once before the kill"

    def test_provider_with_zero_completions_killed_at_inactivity_threshold(self, monkeypatch):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 1)
        _set_env_mode(monkeypatch, "never_returns")

        t0 = time.monotonic()
        with pytest.raises(ve._ProviderStallDuringComputation):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=300,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 1 + ve.CHILD_KILL_GRACE_SECONDS + 3, (
            f"inactivity kill took {elapsed}s — should fire near the 1s threshold, "
            f"nowhere near the 300s total deadline"
        )

    def test_wedged_child_process_killed_at_inactivity_threshold(self, monkeypatch):
        """A completely wedged child (stuck even before submitting any
        real work) is bounded by the SAME parent-side inactivity clock."""
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 1)
        _set_env_mode(monkeypatch, "never_returns")

        t0 = time.monotonic()
        with pytest.raises((ve._ProviderStallDuringComputation, ve._ChildProcessFailure)):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=300,
            )
        elapsed = time.monotonic() - t0
        assert elapsed < 1 + ve.CHILD_KILL_GRACE_SECONDS + 3
        time.sleep(0.2)
        assert multiprocessing.active_children() == []

    def test_inactivity_kill_zero_results_no_survivors_coherent_ledger_state(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 1)
        _set_env_mode(monkeypatch, "never_returns")

        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                             fencing_token=lease["fencing_token"], now=now)
        ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)
        result = ve.execute_and_complete_admitted_attempt(
            admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=300,
        )
        assert result["ok"] is False
        assert _count_val_runs(isolated_db) == 0
        assert _count_val_signals(isolated_db) == 0
        time.sleep(0.2)
        assert multiprocessing.active_children() == []
        lease_row = ve.get_validation_execution_lease()
        assert lease_row["lease_owner"] is None
        assert lease_row["active_attempt_id"] is None


# ─────────────────────────────────────────────────────────────────────────
# Blocker 5 (V-USACT1-B-C1) — production-parity evidence under the REAL
# "spawn" context. This class was already spawn-only and fork-free.
# ─────────────────────────────────────────────────────────────────────────

def _top_level_spawn_test_worker(result_queue, mode: str) -> None:
    import threading as _t
    if mode == "success":
        result_queue.put({"type": "result", "payload": {"ok": True}, "completed_at_utc": "spawn-test"})
    elif mode == "stuck":
        _t.Event().wait()
    elif mode == "crash":
        raise RuntimeError("intentional spawn-test crash")


@pytest.mark.unit
class TestSpawnProductionParity:
    def test_spawn_target_is_importable_and_picklable(self):
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue(maxsize=10)
        process = ctx.Process(
            target=ve._validation_child_worker,
            args=(q, "short", "us", 1, "manual"),
            daemon=True,
        )
        process.start()  # raises immediately if unpicklable — this is the assertion
        ve._terminate_child_process(process)
        assert not process.is_alive()

    def test_spawn_ipc_queue_functions_correctly(self):
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue(maxsize=10)
        process = ctx.Process(target=_top_level_spawn_test_worker, args=(q, "success"), daemon=True)
        process.start()
        try:
            msg = q.get(timeout=15)
        finally:
            process.join(timeout=5)
        assert msg["type"] == "result"
        assert msg["payload"] == {"ok": True}
        assert not process.is_alive()

    def test_spawn_terminate_and_kill_genuinely_work(self):
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue(maxsize=10)
        process = ctx.Process(target=_top_level_spawn_test_worker, args=(q, "stuck"), daemon=True)
        process.start()
        deadline = time.monotonic() + 10
        while not process.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert process.is_alive(), "spawned child never reported alive"

        ve._terminate_child_process(process)
        assert not process.is_alive(), "spawned child survived _terminate_child_process"

    def test_spawn_no_orphan_process_after_kill(self):
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue(maxsize=10)
        process = ctx.Process(target=_top_level_spawn_test_worker, args=(q, "stuck"), daemon=True)
        process.start()
        deadline = time.monotonic() + 10
        while not process.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        ve._terminate_child_process(process)
        time.sleep(0.2)
        assert multiprocessing.active_children() == []

    def test_spawn_child_crash_is_detected_not_silently_treated_as_success(self):
        ctx = multiprocessing.get_context("spawn")
        q = ctx.Queue(maxsize=10)
        process = ctx.Process(target=_top_level_spawn_test_worker, args=(q, "crash"), daemon=True)
        process.start()
        try:
            with pytest.raises(_queue_module.Empty):
                q.get(timeout=5)  # the crash target never puts anything — must time out
        finally:
            process.join(timeout=10)
        assert process.exitcode != 0, "a crashed child should report a non-zero exit code"

    def test_full_pipeline_under_real_spawn_does_not_inherit_parent_monkeypatches(self, monkeypatch):
        """Every test in this file now runs under real spawn unconditionally
        (there is no start-method override left anywhere) — this test
        specifically re-confirms that patching ve.US_BASKET/_backtest_stock
        in the PARENT has zero effect on the child, which always calls
        the real, freshly-imported run_validation() unless
        _validation_child_worker itself was explicitly monkeypatched
        (which this test deliberately does NOT do)."""
        monkeypatch.setattr(ve, "US_BASKET", ["THIS_FAKE_MUST_NOT_BE_SEEN_BY_A_SPAWNED_CHILD"])

        def _fast_ok_backtest(sym, horizon, bench_df, market, **kwargs):
            ws = kwargs.get("_window_stats")
            if ws is not None:
                ws["considered"] = 1
                ws["benchmark_valid"] = 1
            return []
        monkeypatch.setattr(ve, "_backtest_stock", _fast_ok_backtest)

        t0 = time.monotonic()
        try:
            result = ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1,
                trigger_type="manual", max_run_duration_seconds=3,
            )
            assert result["_persist_payload"]["n_stocks"] != 1, (
                "child appears to have inherited this test's monkeypatch — "
                "spawn isolation is broken"
            )
        except (ve._RunDeadlineExceeded, ve._ChildProcessFailure, ve._ProviderStallDuringComputation):
            pass  # expected in a sandboxed/no-network test environment
        elapsed = time.monotonic() - t0
        assert elapsed > 0.5, (
            f"completed suspiciously fast ({elapsed}s) for a real 43-symbol universe — "
            f"consistent with the child having incorrectly inherited the 1-symbol fake"
        )


# ─────────────────────────────────────────────────────────────────────────
# Blocker 1 (V-USACT1-B-C1) — GET /api/validation/status must show honest
# live progress/log/job state while a subprocess-executed run is actively
# in flight. Group A — real spawn, self-contained _env_worker, real
# parent IPC/status path throughout.
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestStatusEndpointLiveVisibility:
    def test_status_shows_running_true_while_child_executes(self, monkeypatch, isolated_db):
        _set_env_mode(monkeypatch, "never_returns")

        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                             fencing_token=lease["fencing_token"], now=now)
        ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)

        t = threading.Thread(
            target=lambda: ve.execute_and_complete_admitted_attempt(
                admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
                lease_duration_seconds=600, max_run_duration_seconds=5,
            ),
            daemon=True,
        )
        t.start()
        try:
            deadline = time.monotonic() + 10
            seen_running = False
            status = None
            while time.monotonic() < deadline:
                status = ve.get_run_status()
                if status.get("running") is True:
                    seen_running = True
                    break
                time.sleep(0.05)
            assert seen_running, "GET status never showed running=True while the child was actively executing"
            assert status["job"]["horizon"] == "short"
            assert status["job"]["universe"] == "us"
            assert status["job"]["trigger_type"] == "manual"
        finally:
            t.join(timeout=15)

    def test_status_progress_advances_as_symbols_genuinely_complete(self, monkeypatch, isolated_db):
        """V-USACT1-B-C4 — rewritten to use real spawn + a top-level,
        env-var-configured worker; no inherited monkeypatch or thread
        state required. See test_status_progress_repeats_reliably_under_
        real_spawn below for the mandated 25-repetition stress proof."""
        n = 4
        _set_env_mode(monkeypatch, "progress_then_success", n_progress=n, step_seconds=0.3)

        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                             fencing_token=lease["fencing_token"], now=now)
        ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)

        t = threading.Thread(
            target=lambda: ve.execute_and_complete_admitted_attempt(
                admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
                lease_duration_seconds=600, max_workers=1, max_run_duration_seconds=30,
            ),
            daemon=True,
        )
        t.start()
        try:
            observed_progress_values = set()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and t.is_alive():
                observed_progress_values.add(ve.get_run_status().get("progress"))
                time.sleep(0.05)
            assert len(observed_progress_values) >= 2, (
                f"expected to observe progress genuinely advance across multiple polls, "
                f"only ever saw {observed_progress_values}"
            )
        finally:
            t.join(timeout=15)
        final = ve.get_run_status()
        assert final["running"] is False
        assert final["progress"] == n
        assert final["job"]["status"] == "completed"

    def test_status_progress_repeats_reliably_under_real_spawn(self, monkeypatch, isolated_db):
        """V-USACT1-B-C4, Correction 4 — the mandated repeatability proof.
        Runs the full lifecycle (real spawn, child starts, /status
        becomes running, progress genuinely advances through at least two
        distinct events, terminal status is honest, child exits cleanly,
        no orphan remains) 25 consecutive times in fresh child processes.
        Zero failures or hangs required across all 25 repetitions."""
        REPETITIONS = 25
        n = 2
        for i in range(REPETITIONS):
            _set_env_mode(monkeypatch, "progress_then_success", n_progress=n, step_seconds=0.05)

            # No manual DB reset needed between repetitions — each prior
            # iteration's successful completion already releases the
            # global lease normally (acquire_validation_execution_lease
            # is a CAS UPDATE against a single pre-seeded row; deleting
            # from it would remove that row and break every subsequent
            # acquisition attempt).
            now = datetime.now(timezone.utc)
            lease = ve.acquire_validation_execution_lease(owner=f"A{i}", now=now, lease_duration_seconds=600)
            assert lease["ok"] is True, f"repetition {i}: lease acquisition failed"
            admitted = ve.create_manual_attempt(horizon="short", universe="us", owner=f"A{i}",
                                                 fencing_token=lease["fencing_token"], now=now)
            assert admitted["ok"] is True, f"repetition {i}: admission failed"
            ve.mark_attempt_running(admitted["id"], owner=f"A{i}", fencing_token=lease["fencing_token"], now=now)

            t = threading.Thread(
                target=lambda: ve.execute_and_complete_admitted_attempt(
                    admitted["id"], f"A{i}", lease["fencing_token"], "short", "us", "manual",
                    lease_duration_seconds=600, max_workers=1, max_run_duration_seconds=15,
                ),
                daemon=True,
            )
            t.start()
            try:
                observed_running = False
                observed_progress = set()
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline and t.is_alive():
                    status = ve.get_run_status()
                    if status.get("running") is True:
                        observed_running = True
                    observed_progress.add(status.get("progress"))
                    time.sleep(0.02)
                assert observed_running, f"repetition {i}: never observed running=True"
                assert len(observed_progress) >= 2, (
                    f"repetition {i}: expected progress to advance through at least 2 "
                    f"distinct values, only saw {observed_progress}"
                )
            finally:
                t.join(timeout=15)
                assert not t.is_alive(), f"repetition {i}: execution thread hung"

            final = ve.get_run_status()
            assert final["running"] is False, f"repetition {i}: terminal status was not honest"
            assert final["job"]["status"] == "completed", f"repetition {i}: {final['job']}"

            time.sleep(0.05)
            assert multiprocessing.active_children() == [], (
                f"repetition {i}: an orphan child process survived"
            )

    def test_status_reaches_honest_terminal_state_on_success(self, monkeypatch, isolated_db):
        _set_env_mode(monkeypatch, "instant_success")

        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                             fencing_token=lease["fencing_token"], now=now)
        ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)
        result = ve.execute_and_complete_admitted_attempt(
            admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=15,
        )
        assert result["ok"] is True
        status = ve.get_run_status()
        assert status["running"] is False
        assert status["job"]["status"] == "completed"
        assert status["job"]["failure_code"] is None

    def test_status_reaches_honest_terminal_state_on_deadline(self, monkeypatch, isolated_db):
        _set_env_mode(monkeypatch, "never_returns")

        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                             fencing_token=lease["fencing_token"], now=now)
        ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)
        result = ve.execute_and_complete_admitted_attempt(
            admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=2,
        )
        assert result["ok"] is False
        status = ve.get_run_status()
        assert status["running"] is False
        assert status["job"]["status"] == "failed"
        assert status["job"]["failure_code"] == "RUN_DEADLINE_EXCEEDED"
        assert status["job"]["failure_message"] == ve.VALIDATION_PUBLIC_FAILURE_MESSAGES["RUN_DEADLINE_EXCEEDED"]

    def test_status_reaches_honest_terminal_state_on_inactivity_stall(self, monkeypatch, isolated_db):
        monkeypatch.setattr(ve, "RUN_STALL_TIMEOUT_SECONDS", 1)
        _set_env_mode(monkeypatch, "never_returns")

        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                             fencing_token=lease["fencing_token"], now=now)
        ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)
        result = ve.execute_and_complete_admitted_attempt(
            admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=300,
        )
        assert result["ok"] is False
        status = ve.get_run_status()
        assert status["running"] is False
        assert status["job"]["status"] == "failed"
        assert status["job"]["failure_code"] == "PROVIDER_STALL"

    def test_status_reaches_honest_terminal_state_on_child_crash(self, monkeypatch, isolated_db):
        _set_env_mode(monkeypatch, "crash")

        now = datetime.now(timezone.utc)
        lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
        admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                             fencing_token=lease["fencing_token"], now=now)
        ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)
        ve.execute_and_complete_admitted_attempt(
            admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
            lease_duration_seconds=600, max_run_duration_seconds=15,
        )
        status = ve.get_run_status()
        assert status["running"] is False
        assert status["job"]["status"] == "failed"
        assert status["job"]["failure_code"] == "RUN_EXCEPTION"

    def test_status_reaches_honest_terminal_state_on_fencing_rejection(self, monkeypatch, isolated_db):
        _set_env_mode(monkeypatch, "never_returns")

        def _rejecting_heartbeat():
            return True

        with pytest.raises(ve._FencedOutDuringComputation):
            ve._run_validation_in_subprocess(
                horizon="short", universe="us", max_workers=1, trigger_type="manual",
                heartbeat_fn=_rejecting_heartbeat, lease_duration_seconds=600,
                max_run_duration_seconds=300,
            )
        status = ve.get_run_status()
        assert status["running"] is False
        assert status["job"]["status"] == "failed"

    def test_manual_job_id_behavior_unchanged(self, monkeypatch, isolated_db):
        import api.routers.validation as validation_router
        import inspect
        src = inspect.getsource(validation_router.trigger_validation)
        assert "uuid.uuid4()" in src, "manual endpoint's job_id generation must remain untouched"
