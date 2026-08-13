"""
V-SCHED1C1 — connects the V-SCHED1B durable scheduling ledger and global
execution lease to real validation execution.

Covers the shared admission/execution orchestration primitives added in
validation_engine.py (admit_validation_attempt, execute_and_complete_
admitted_attempt, execute_admitted_validation) that main.py's scheduler,
catch-up, and api/routers/validation.py's manual trigger all now call
instead of maintaining separate admission logic.

Every test here mocks run_validation() itself (the actual walk-forward
backtest is covered exhaustively elsewhere) — these tests are about the
admission/execution/completion LIFECYCLE around it, not the model.
"""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

import services.validation_engine as ve
from services.validation_engine import (
    acquire_validation_execution_lease,
    admit_validation_attempt,
    create_manual_attempt,
    execute_admitted_validation,
    execute_and_complete_admitted_attempt,
    get_or_create_schedule_slot,
    get_schedule_attempt,
    get_schedule_slot,
    get_validation_execution_lease,
    mark_attempt_running,
    recover_stale_active_attempt,
    release_validation_execution_lease,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "scheduler_integration_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    ve._init_db()
    return db_path


T0 = datetime(2026, 8, 13, 0, 30, tzinfo=timezone.utc)  # 06:00 IST
T1 = T0 + timedelta(minutes=1)


def _real_now():
    """Real wall-clock time — used for admission's `now` in tests that go
    through execute_admitted_validation/execute_and_complete_admitted_
    attempt, since that internal execution phase always heartbeats/
    completes using real datetime.now(timezone.utc), not an injected
    fixed clock. Using a stale fixed T0 for admission while the internal
    phase uses real time would desync the lease's expires_at from what
    the fencing checks compare against later — this keeps them coherent.
    Tests that only exercise admit_validation_attempt/the ledger
    primitives directly (never the full execute path) correctly keep
    using the fully-deterministic fixed T0/T1 constants."""
    return datetime.now(timezone.utc)


def _insert_val_run(db_path, horizon="medium", universe="nifty100"):
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
            "VALUES (?, ?, 1, 0, '{}', ?)",
            (T0.isoformat(), horizon, universe),
        )
        return cur.lastrowid


def _fake_run_validation_factory(monkeypatch, isolated_db, horizon="medium", universe="nifty100"):
    """Replaces run_validation with a deterministic fake mirroring the real
    _persist=False contract used by the ledger-backed execution path
    (V-SCHED1C1-C1 correction): computation only, no val_runs/val_signals
    write of its own — it returns a `_persist_payload` for the caller to
    hand to complete_running_attempt_with_computed_result(), which performs
    the actual (real, isolated-db) insert. Also honors `_fence_check` at
    each simulated checkpoint, exactly like the real run_validation loop,
    so heartbeat-loss tests can exercise the same abort path. Legacy
    `_persist=True` callers (direct/non-ledger tests) still get the old
    immediate-insert behavior."""
    def _fake(horizon=horizon, universe=universe, max_workers=6, trigger_type="internal",
               _claimed_job=None, progress_callback=None, _persist=True, _fence_check=None):
        for done, total in ((5, 10), (10, 10)):
            if progress_callback is not None:
                progress_callback(done, total)
            if _fence_check is not None and _fence_check():
                raise ve._FencedOutDuringComputation()
        if not _persist:
            return {
                "horizon": horizon, "universe": universe,
                "_persist_payload": {
                    "run_at": T0.isoformat(), "horizon": horizon, "n_stocks": 1,
                    "n_signals": 0, "summary_json": "{}", "universe": universe,
                    "signal_rows": [],
                },
            }
        run_id = _insert_val_run(isolated_db, horizon=horizon, universe=universe)
        return {"run_id": run_id, "horizon": horizon, "universe": universe}
    monkeypatch.setattr(ve, "run_validation", _fake)
    return _fake


# ─────────────────────────────────────────────────────────────────────────
# 1-5: admission gating and rejection reporting
# ─────────────────────────────────────────────────────────────────────────

class TestAdmissionGating:
    def test_scheduled_medium_cannot_begin_without_global_lease(self, isolated_db):
        """Simulates the lease already being held by someone else — a
        scheduled admission attempt must be rejected, not silently start."""
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        held = acquire_validation_execution_lease(owner="other", now=T0, lease_duration_seconds=600)
        assert held["ok"] is True
        result = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                           owner="scheduler-1", scheduled_slot=T0, now=T1)
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_scheduled_long_cannot_begin_without_global_lease(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="long", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        acquire_validation_execution_lease(owner="other", now=T0, lease_duration_seconds=600)
        result = admit_validation_attempt(horizon="long", universe="nifty100", trigger_type="scheduler",
                                           owner="scheduler-1", scheduled_slot=T0, now=T1)
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_manual_execution_contends_for_same_global_lease(self, isolated_db):
        acquire_validation_execution_lease(owner="scheduler-1", now=T0, lease_duration_seconds=600)
        result = admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                           owner="manual-1", now=T1)
        assert result["ok"] is False

    def test_scheduled_and_manual_cannot_run_simultaneously(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        scheduled = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=T0)
        assert scheduled["ok"] is True
        manual = admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                           owner="manual-1", now=T1)
        assert manual["ok"] is False

    def test_rejected_admission_is_reported_as_rejected_not_complete(self, isolated_db):
        """The exact defect the forensic report found: a rejected claim
        must never be indistinguishable from a genuine completion."""
        acquire_validation_execution_lease(owner="other", now=T0, lease_duration_seconds=600)
        result = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                           owner="scheduler-1", scheduled_slot=T0, now=T1)
        assert result["ok"] is False
        assert "reason" in result
        assert result.get("attempt_id") is None


# ─────────────────────────────────────────────────────────────────────────
# 6-9: slot resolution, running transition, result linkage, completion
# ─────────────────────────────────────────────────────────────────────────

class TestSlotAndCompletionLifecycle:
    def test_scheduled_run_resolves_canonical_slot_before_execution(self, isolated_db, monkeypatch):
        _fake_run_validation_factory(monkeypatch, isolated_db)
        result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=_real_now())
        assert result["ok"] is True
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        assert slot["status"] == "completed"

    def test_attempt_becomes_running_only_after_successful_admission(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        admitted = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                             owner="scheduler-1", scheduled_slot=T0, now=T0)
        assert admitted["ok"] is True
        fetched = get_schedule_attempt(admitted["attempt_id"])
        assert fetched["status"] == "running"

    def test_result_run_id_linked_exactly_once_to_admitted_attempt(self, isolated_db, monkeypatch):
        _fake_run_validation_factory(monkeypatch, isolated_db)
        result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=_real_now())
        assert result["ok"] is True
        attempt = get_schedule_attempt(result["attempt_id"])
        assert attempt["result_run_id"] == result["run_id"]
        with sqlite3.connect(isolated_db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM validation_schedule_attempts WHERE result_run_id=?", (result["run_id"],)
            ).fetchone()[0]
        assert count == 1

    def test_successful_completion_atomically_releases_active_attempt_binding(self, isolated_db, monkeypatch):
        _fake_run_validation_factory(monkeypatch, isolated_db)
        result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=_real_now())
        assert result["ok"] is True
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_retryable_failure_returns_slot_to_due(self, isolated_db, monkeypatch):
        def _failing(**kwargs):
            raise RuntimeError("simulated provider failure")
        monkeypatch.setattr(ve, "run_validation", _failing)
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=_real_now())
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_terminal_failure_leaves_slot_terminal(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        admitted = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                             owner="scheduler-1", scheduled_slot=T0, now=T0)
        ve.mark_attempt_failed_terminal(admitted["attempt_id"], owner="scheduler-1",
                                         fencing_token=admitted["fencing_token"], now=T1)
        assert get_schedule_slot(slot["id"])["status"] == "failed"


# ─────────────────────────────────────────────────────────────────────────
# 12-13: stale recovery and fencing during execution
# ─────────────────────────────────────────────────────────────────────────

class TestStaleRecoveryDuringExecution:
    def test_expired_stale_attempt_must_be_recovered_before_new_admission(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        # Admit with a short lease duration so it genuinely expires shortly
        # after — simulating a worker that crashes immediately after admission.
        stale = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                          owner="scheduler-1", scheduled_slot=T0, now=T0,
                                          lease_duration_seconds=1)
        assert stale["ok"] is True
        # a new owner reclaims after expiry — admit_validation_attempt
        # performs the acquire itself, sees recovery_required, and
        # recovers the stale attempt (via the fenced primitive) before
        # admitting the new one, all within this one call.
        reclaim_time = T0 + timedelta(seconds=2)
        second_slot = get_or_create_schedule_slot(horizon="medium", universe="midcap",
                                                    scheduled_slot=T0, schedule_version="v1", now=T0)
        result = admit_validation_attempt(horizon="medium", universe="midcap", trigger_type="scheduler",
                                           owner="scheduler-2", scheduled_slot=T0, now=reclaim_time)
        assert result["ok"] is True
        assert get_schedule_attempt(stale["attempt_id"])["status"] == "abandoned"
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_stale_worker_cannot_attach_result_after_fencing_supersedes_it(self, isolated_db, monkeypatch):
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        # Short lease so it genuinely expires — a voluntary release would
        # be correctly rejected here anyway (an attempt is still bound to
        # it), which is exactly why expiry-based reclaim, not release, is
        # the realistic way a worker's ownership gets superseded mid-run.
        admitted = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                             owner="scheduler-1", scheduled_slot=T0, now=T0,
                                             lease_duration_seconds=1)
        run_id = _insert_val_run(isolated_db)

        # simulate reclaim happening mid-execution (worker stalled, lease
        # expired), then the old worker tries to complete using its
        # now-superseded token
        expired = T0 + timedelta(seconds=2)
        acquire_validation_execution_lease(owner="scheduler-2", now=expired, lease_duration_seconds=600)

        completion = ve.complete_attempt_with_result(
            admitted["attempt_id"], owner="scheduler-1", fencing_token=admitted["fencing_token"],
            result_run_id=run_id, now=expired,
        )
        assert completion["ok"] is False
        fetched = get_schedule_attempt(admitted["attempt_id"])
        assert fetched["result_run_id"] is None


# ─────────────────────────────────────────────────────────────────────────
# 14: catch-up identity fix — activity on one universe must not suppress
# a due slot for another
# ─────────────────────────────────────────────────────────────────────────

class TestCatchupSlotIdentity:
    def test_catchup_uses_exact_canonical_slot_not_any_universe(self, isolated_db, monkeypatch):
        """The forensically-confirmed 'any-universe suppression' defect:
        the old get_last_run_time('medium') lookup (universe-unscoped)
        could suppress catch-up for medium/nifty100 just because
        medium/midcap ran more recently. The fixed catch-up path checks
        the CANONICAL SLOT's own status, scoped exactly to
        (medium, nifty100, today's slot, v1) — midcap activity must not
        affect it."""
        _fake_run_validation_factory(monkeypatch, isolated_db, universe="midcap")
        # medium/midcap already completed today
        execute_admitted_validation(horizon="medium", universe="midcap", trigger_type="scheduler",
                                     owner="scheduler-1", scheduled_slot=T0, now=_real_now())

        # medium/nifty100's OWN canonical slot is still due — must not be
        # suppressed by midcap's unrelated activity
        nifty_slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                                   scheduled_slot=T0, schedule_version="v1", now=T0)
        assert nifty_slot["status"] == "due"

        _fake_run_validation_factory(monkeypatch, isolated_db, universe="nifty100")
        catchup_result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="catchup",
                                                       owner="catchup-1", slot_id=nifty_slot["id"], now=_real_now())
        assert catchup_result["ok"] is True


# ─────────────────────────────────────────────────────────────────────────
# 15-16: existing auth/no-public-trigger regression is covered by the
# updated test_validation_run_endpoint_auth.py — not duplicated here.
# ─────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────
# 17: automatic short remains inactive — structural proof
# ─────────────────────────────────────────────────────────────────────────

class TestShortRemainsInactive:
    def test_no_short_schedule_call_anywhere_in_main(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module)
        assert "horizon=\"short\"" not in src
        assert "horizon='short'" not in src

    def test_short_slots_remain_structurally_creatable_but_never_scheduled(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="short", universe="us",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        assert slot["horizon"] == "short"
        assert slot["status"] == "due"  # nothing ever admits it automatically


# ─────────────────────────────────────────────────────────────────────────
# 18: cadence unchanged — structural proof (byte-for-byte on the timing
# constants, behaviorally unchanged on ordering/spacing)
# ─────────────────────────────────────────────────────────────────────────

class TestCadenceUnchanged:
    def test_medium_daily_long_sunday_constants_unchanged(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "TARGET_HOUR = 6" in src
        assert "weekday() == 6" in src
        assert "5 * 60" in src  # 5-minute inter-universe gap preserved

    def test_universe_ordering_unchanged(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert '("nifty100", "midcap", "us")' in src


# ─────────────────────────────────────────────────────────────────────────
# Manual-trigger-specific: two-phase admission (Stage 8)
# ─────────────────────────────────────────────────────────────────────────

class TestManualTwoPhaseAdmission:
    def test_manual_attempt_has_slot_id_null(self, isolated_db):
        admitted = admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                             owner="manual-1", now=T0)
        assert admitted["ok"] is True
        fetched = get_schedule_attempt(admitted["attempt_id"])
        assert fetched["slot_id"] is None

    def test_failed_admission_never_yields_a_runnable_attempt(self, isolated_db):
        acquire_validation_execution_lease(owner="other", now=T0, lease_duration_seconds=600)
        admitted = admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                             owner="manual-1", now=T1)
        assert admitted["ok"] is False
        assert "attempt_id" not in admitted

    def test_phase_two_completes_the_exact_admitted_attempt(self, isolated_db, monkeypatch):
        _fake_run_validation_factory(monkeypatch, isolated_db, horizon="short", universe="us")
        admitted = admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                             owner="manual-1", now=T0)
        assert admitted["ok"] is True
        result = execute_and_complete_admitted_attempt(
            admitted["attempt_id"], admitted["owner"], admitted["fencing_token"],
            "short", "us", "manual",
        )
        assert result["ok"] is True
        assert result["attempt_id"] == admitted["attempt_id"]


# ─────────────────────────────────────────────────────────────────────────
# Cooperative heartbeat tied to progress (Stage 6) — proven via a real
# multi-stock fake with observable progress checkpoints
# ─────────────────────────────────────────────────────────────────────────

class TestCooperativeHeartbeat:
    def test_heartbeat_extends_lease_expiry_as_progress_is_made(self, isolated_db, monkeypatch):
        heartbeat_calls = []
        real_heartbeat = ve.heartbeat_validation_execution_lease

        def _tracking_heartbeat(*args, **kwargs):
            result = real_heartbeat(*args, **kwargs)
            heartbeat_calls.append(result)
            return result

        monkeypatch.setattr(ve, "heartbeat_validation_execution_lease", _tracking_heartbeat)

        def _fake(horizon="medium", universe="nifty100", max_workers=6, trigger_type="internal",
                   _claimed_job=None, progress_callback=None, _persist=True, _fence_check=None):
            for i in range(1, 21):
                if progress_callback is not None:
                    progress_callback(i, 20)
                if _fence_check is not None and _fence_check():
                    raise ve._FencedOutDuringComputation()
            return {
                "horizon": horizon, "universe": universe,
                "_persist_payload": {
                    "run_at": T0.isoformat(), "horizon": horizon, "n_stocks": 1,
                    "n_signals": 0, "summary_json": "{}", "universe": universe,
                    "signal_rows": [],
                },
            }
        monkeypatch.setattr(ve, "run_validation", _fake)

        result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=_real_now(),
                                              heartbeat_every_n_stocks=10)
        assert result["ok"] is True
        # heartbeat_every_n_stocks=10 over 20 stocks -> triggers at 10 and 20
        assert len(heartbeat_calls) == 2
        assert all(hb["ok"] for hb in heartbeat_calls)

    def test_stalled_worker_eventually_loses_lease_not_renewed_blindly(self, isolated_db):
        """No progress = no heartbeat call at all — proving renewal is
        tied to observable forward progress, not a background timer."""
        lease = acquire_validation_execution_lease(owner="scheduler-1", now=T0, lease_duration_seconds=1)
        stalled_time = T0 + timedelta(seconds=2)
        # nobody heartbeats — the lease is simply expired and reclaimable
        reclaimer = acquire_validation_execution_lease(owner="scheduler-2", now=stalled_time,
                                                         lease_duration_seconds=600)
        assert reclaimer["ok"] is True
        assert reclaimer["fencing_token"] > lease["fencing_token"]


# ─────────────────────────────────────────────────────────────────────────
# V-SCHED1C1-C1 — the genuine RED regression for the Critical defect found
# by the independent review: run_validation() used to insert+commit
# val_runs/val_signals unconditionally, before any fencing check, so a
# stale/fenced-out worker could create a permanently orphaned result row
# that public "latest result" reads (ORDER BY id DESC) would still surface.
# These tests fail against the pre-correction code (persist happens inside
# run_validation itself, unconditionally) and pass only once persistence is
# moved into complete_running_attempt_with_computed_result(), one atomic
# transaction that re-verifies fencing immediately before its own insert.
# ─────────────────────────────────────────────────────────────────────────

def _val_runs_count(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM val_runs").fetchone()[0]


def _val_signals_count(db_path):
    with sqlite3.connect(db_path) as conn:
        return conn.execute("SELECT COUNT(*) FROM val_signals").fetchone()[0]


def _latest_val_run_id(db_path, horizon="medium", universe="nifty100"):
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT id FROM val_runs WHERE horizon=? AND universe=? ORDER BY id DESC LIMIT 1",
            (horizon, universe),
        ).fetchone()
        return row[0] if row else None


def _payload_with_signal(horizon, universe):
    return {
        "run_at": T0.isoformat(), "horizon": horizon, "n_stocks": 1, "n_signals": 1,
        "summary_json": "{}", "universe": universe,
        "signal_rows": [("SYM", horizon, "2026-08-01", 1.0, 1.0, 1.0, 1.0, 1.0,
                          "up", 1.0, None, None, "up", 1)],
    }


class TestOrphanResultPrevention:
    def test_fenced_out_worker_creates_zero_val_runs_and_zero_val_signals_rows(self, isolated_db, monkeypatch):
        """Worker A is admitted, computes fully, but is fenced out (lease
        reclaimed by B) at its final checkpoint before it can persist. The
        RED assertion: A's fencing loss must result in ZERO val_runs and
        ZERO val_signals rows, no attempt linkage, and no change to the
        publicly-selected latest result — not merely an unlinked orphan."""
        pre_existing_latest = _insert_val_run(isolated_db, horizon="medium", universe="nifty100")

        admitted = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                             owner="worker-a", scheduled_slot=T0, now=_real_now(),
                                             lease_duration_seconds=600)
        assert admitted["ok"] is True

        # Worker B reclaims worker A's still-unexpired lease is impossible
        # by design (fenced) — the realistic supersession path is A's own
        # heartbeat being rejected once B has legitimately reclaimed after
        # real expiry. Simulate that directly: force A's heartbeat to
        # report rejection on its very first check, mimicking "B already
        # holds the lease by the time A's next checkpoint runs".
        def _fake(horizon="medium", universe="nifty100", max_workers=6, trigger_type="internal",
                   _claimed_job=None, progress_callback=None, _persist=True, _fence_check=None):
            if progress_callback is not None:
                progress_callback(1, 1)
            if _fence_check is not None and _fence_check():
                raise ve._FencedOutDuringComputation()
            # unreachable if fencing is honored — proves the pre-correction
            # code path (which ignored the fence_check's abort signal and
            # persisted anyway) is exactly what this test guards against
            return {"_persist_payload": _payload_with_signal("medium", "nifty100")}
        monkeypatch.setattr(ve, "run_validation", _fake)

        def _rejecting_heartbeat(*args, **kwargs):
            return {"ok": False, "reason": "fenced_out"}
        monkeypatch.setattr(ve, "heartbeat_validation_execution_lease", _rejecting_heartbeat)

        result = execute_and_complete_admitted_attempt(
            admitted["attempt_id"], admitted["owner"], admitted["fencing_token"],
            "medium", "nifty100", "scheduler",
        )

        assert result["ok"] is False
        assert result["reason"] == "fenced_out_during_execution"
        assert "orphaned_run_id" not in result

        assert _val_runs_count(isolated_db) == 1  # only the pre-existing row
        assert _val_signals_count(isolated_db) == 0
        assert _latest_val_run_id(isolated_db) == pre_existing_latest
        attempt = get_schedule_attempt(admitted["attempt_id"])
        assert attempt["result_run_id"] is None

    def test_stale_worker_wakes_after_reclaim_and_still_persists_nothing(self, isolated_db, monkeypatch):
        """Worker A is admitted with a short lease, 'goes to sleep' (never
        heartbeats), B legitimately reclaims after real expiry, then A
        'wakes up' and attempts to complete using its now-superseded
        token. The atomic primitive's own fencing check — independent of
        whatever A's heartbeat did or didn't do — must reject it and
        persist nothing."""
        pre_existing_latest = _insert_val_run(isolated_db, horizon="medium", universe="nifty100")

        admitted = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                             owner="worker-a", scheduled_slot=T0, now=T0,
                                             lease_duration_seconds=1)
        assert admitted["ok"] is True

        # B legitimately reclaims after real expiry (admit performs its
        # own recovery of the stale attempt as part of admission).
        b_admitted = admit_validation_attempt(horizon="medium", universe="midcap", trigger_type="scheduler",
                                               owner="worker-b", scheduled_slot=T0,
                                               now=T0 + timedelta(seconds=2), lease_duration_seconds=600)
        assert b_admitted["ok"] is True

        # A wakes up and attempts atomic persistence with its stale token —
        # this is the exact call the pre-correction code never made this
        # safely, because it had already committed val_runs before any
        # fencing check ran at all.
        payload = _payload_with_signal("medium", "nifty100")
        completion = ve.complete_running_attempt_with_computed_result(
            admitted["attempt_id"], owner="worker-a", fencing_token=admitted["fencing_token"],
            horizon=payload["horizon"], universe=payload["universe"], run_at=payload["run_at"],
            n_stocks=payload["n_stocks"], n_signals=payload["n_signals"],
            summary_json=payload["summary_json"], signal_rows=payload["signal_rows"],
            now=T0 + timedelta(seconds=3),
        )

        assert completion["ok"] is False
        assert completion["reason"] == "not_owner_or_expired_lease"
        assert _val_runs_count(isolated_db) == 1  # only the pre-existing row
        assert _val_signals_count(isolated_db) == 0
        assert _latest_val_run_id(isolated_db) == pre_existing_latest
        # A must not have clobbered B's binding
        assert get_validation_execution_lease()["active_attempt_id"] == b_admitted["attempt_id"]

    def test_successful_atomic_completion_inserts_exactly_one_run_and_its_signals(self, isolated_db):
        admitted = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                             owner="worker-a", scheduled_slot=T0, now=T0,
                                             lease_duration_seconds=600)
        assert admitted["ok"] is True
        payload = _payload_with_signal("medium", "nifty100")
        completion = ve.complete_running_attempt_with_computed_result(
            admitted["attempt_id"], owner="worker-a", fencing_token=admitted["fencing_token"],
            horizon=payload["horizon"], universe=payload["universe"], run_at=payload["run_at"],
            n_stocks=payload["n_stocks"], n_signals=payload["n_signals"],
            summary_json=payload["summary_json"], signal_rows=payload["signal_rows"],
            now=T1,
        )
        assert completion["ok"] is True
        assert _val_runs_count(isolated_db) == 1
        assert _val_signals_count(isolated_db) == 1
        attempt = get_schedule_attempt(admitted["attempt_id"])
        assert attempt["result_run_id"] == completion["run_id"]
        assert attempt["status"] == "completed"

    def test_injected_signal_insert_failure_rolls_back_run_signals_attempt_slot_and_lease(
        self, isolated_db, monkeypatch,
    ):
        """A failure partway through the atomic transaction (simulated by
        a signal row with a NOT NULL column violation) must roll back
        EVERYTHING — the val_runs row, any signals already staged, and all
        ledger transitions — leaving the attempt exactly as it was before
        this call, retryable."""
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        admitted = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                             owner="worker-a", scheduled_slot=T0, now=T0,
                                             lease_duration_seconds=600)
        assert admitted["ok"] is True

        # symbol is required (NOT NULL) — omit it via None to force a
        # genuine sqlite3.IntegrityError partway through the executemany.
        bad_rows = [(None, "medium", "2026-08-01", 1.0, 1.0, 1.0, 1.0, 1.0,
                     "up", 1.0, None, None, "up", 1)]

        with pytest.raises(sqlite3.IntegrityError):
            ve.complete_running_attempt_with_computed_result(
                admitted["attempt_id"], owner="worker-a", fencing_token=admitted["fencing_token"],
                horizon="medium", universe="nifty100", run_at=T0.isoformat(),
                n_stocks=1, n_signals=1, summary_json="{}", signal_rows=bad_rows,
                now=T1,
            )

        assert _val_runs_count(isolated_db) == 0
        assert _val_signals_count(isolated_db) == 0
        attempt = get_schedule_attempt(admitted["attempt_id"])
        assert attempt["status"] == "running"
        assert attempt["result_run_id"] is None
        assert get_schedule_slot(slot["id"])["status"] == "running"
        assert get_validation_execution_lease()["active_attempt_id"] == admitted["attempt_id"]

    def test_retry_after_stale_recovery_produces_one_legitimate_newer_result(self, isolated_db):
        """After A's stale attempt is recovered/abandoned and B legitimately
        admits+completes, exactly one new result exists and it is B's."""
        admitted_a = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                               owner="worker-a", scheduled_slot=T0, now=T0,
                                               lease_duration_seconds=1)
        assert admitted_a["ok"] is True
        admitted_b = admit_validation_attempt(horizon="medium", universe="midcap", trigger_type="scheduler",
                                               owner="worker-b", scheduled_slot=T0,
                                               now=T0 + timedelta(seconds=2), lease_duration_seconds=600)
        assert admitted_b["ok"] is True
        assert get_schedule_attempt(admitted_a["attempt_id"])["status"] == "abandoned"

        # B must finish (complete) its own bound midcap attempt first — the
        # global lease holds exactly one active attempt at a time — before
        # it can be admitted again for the nifty100 retry.
        midcap_payload = _payload_with_signal("medium", "midcap")
        midcap_completion = ve.complete_running_attempt_with_computed_result(
            admitted_b["attempt_id"], owner="worker-b", fencing_token=admitted_b["fencing_token"],
            horizon=midcap_payload["horizon"], universe=midcap_payload["universe"],
            run_at=midcap_payload["run_at"], n_stocks=midcap_payload["n_stocks"],
            n_signals=midcap_payload["n_signals"], summary_json=midcap_payload["summary_json"],
            signal_rows=midcap_payload["signal_rows"], now=T0 + timedelta(seconds=2, milliseconds=500),
        )
        assert midcap_completion["ok"] is True
        release = ve.release_validation_execution_lease(
            owner="worker-b", fencing_token=admitted_b["fencing_token"], now=T0 + timedelta(seconds=2, milliseconds=600),
        )
        assert release["ok"] is True

        # B now retries the SAME (medium, nifty100) slot on its own lease.
        retry = admit_validation_attempt(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                          owner="worker-b", scheduled_slot=T0,
                                          now=T0 + timedelta(seconds=3), lease_duration_seconds=600)
        assert retry["ok"] is True
        payload = _payload_with_signal("medium", "nifty100")
        completion = ve.complete_running_attempt_with_computed_result(
            retry["attempt_id"], owner="worker-b", fencing_token=retry["fencing_token"],
            horizon=payload["horizon"], universe=payload["universe"], run_at=payload["run_at"],
            n_stocks=payload["n_stocks"], n_signals=payload["n_signals"],
            summary_json=payload["summary_json"], signal_rows=payload["signal_rows"],
            now=T0 + timedelta(seconds=4),
        )
        assert completion["ok"] is True
        assert _val_runs_count(isolated_db) == 2  # midcap's + this nifty100 retry's
        assert _latest_val_run_id(isolated_db, horizon="medium", universe="nifty100") == completion["run_id"]
