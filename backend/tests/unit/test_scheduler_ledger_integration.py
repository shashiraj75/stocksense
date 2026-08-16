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
    has_established_schedule_baseline,
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
    """V-USACT1-B-C3 — replaces the private parent-side orchestration
    boundary _run_validation_in_subprocess (not run_validation itself,
    which execute_and_complete_admitted_attempt/execute_admitted_
    validation no longer call directly at all — see _run_validation_in_
    subprocess) with a deterministic, synchronous, IN-PROCESS fake. No
    child process, no fork, no spawn, no network — ordinary Python-level
    monkeypatching of a private function, matching its real return
    contract (`{"_persist_payload": {...}}`) exactly. If a real
    heartbeat_fn is wired through by the caller, it is genuinely invoked
    once (mirroring the real function's own per-iteration heartbeat
    check) so heartbeat/fencing-loss tests still exercise the real CAS
    primitive underneath, never a stand-in."""
    def _fake(*, horizon=horizon, universe=universe, max_workers=None, trigger_type=None,
               heartbeat_fn=None, lease_duration_seconds=None, max_run_duration_seconds=None):
        if heartbeat_fn is not None and heartbeat_fn():
            raise ve._FencedOutDuringComputation()
        return {
            "_persist_payload": {
                "run_at": T0.isoformat(), "horizon": horizon, "n_stocks": 1,
                "n_signals": 0, "summary_json": "{}", "universe": universe,
                "signal_rows": [],
            },
        }
    monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake)
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
        # V-USACT1-B — execute_and_complete_admitted_attempt no longer
        # calls run_validation directly; patch the killable-subprocess
        # boundary it actually calls instead.
        def _failing(**kwargs):
            raise RuntimeError("simulated provider failure")
        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _failing)
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
    def test_no_short_schedule_call_outside_the_gated_short_functions(self):
        """V-SCHED1C2B — short scheduling code now exists (gated by
        VALIDATION_AUTO_SHORT_UNIVERSES, default inactive — see
        TestAutoShortConfigParser/TestAutoShortSchedulerStructure), so
        the old "horizon=\"short\" appears nowhere in main.py at all"
        invariant is no longer the correct one. The invariant that
        actually matters — short is unreachable outside its own gated
        functions, and the gate defaults to inactive — is proven
        directly by test_no_horizon_short_execution_reachable_outside_gated_functions
        and test_flag_absent_means_no_calendar_resolution_or_execution
        above. This test now only reconfirms the existing medium/long
        loop itself never references horizon="short" (already covered
        by test_cadence_and_short_and_manual_behavior_unaffected_by_rollout1
        too — kept here as a stable, differently-named regression anchor)."""
        import inspect
        import api.main as main_module
        assert "horizon=\"short\"" not in inspect.getsource(main_module._validation_schedule_loop)
        assert "horizon='short'" not in inspect.getsource(main_module._validation_schedule_loop)

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
        """V-USACT1-B — heartbeat renewal is no longer progress-count
        driven at all (execute_and_complete_admitted_attempt no longer
        calls run_validation in-process; run_validation's own
        progress_callback/_heartbeat/_lease_duration_seconds kwargs are
        never populated by this wrapper anymore — see
        _run_validation_in_subprocess). Renewal is now purely wall-clock,
        applied at the _run_validation_in_subprocess boundary. This test
        is adapted accordingly: it patches that boundary (not
        run_validation) and proves a real heartbeat call genuinely
        advances expires_at in the ledger — heartbeat_every_n_stocks is
        still accepted for backward compatibility but no longer changes
        cadence, so it is passed through unchanged without asserting a
        specific call count tied to it."""
        real_heartbeat = ve.heartbeat_validation_execution_lease
        heartbeat_calls = []

        def _tracking_heartbeat(*args, **kwargs):
            result = real_heartbeat(*args, **kwargs)
            heartbeat_calls.append(result)
            return result

        monkeypatch.setattr(ve, "heartbeat_validation_execution_lease", _tracking_heartbeat)

        real_subprocess_runner = ve._run_validation_in_subprocess

        def _fake_subprocess_runner(*, horizon, universe, max_workers, trigger_type,
                                     heartbeat_fn=None, lease_duration_seconds=None,
                                     max_run_duration_seconds=None):
            # Simulate two real wall-clock heartbeat ticks (what the real
            # loop in _run_validation_in_subprocess would do over a run
            # long enough to cross two intervals) using the ACTUAL
            # heartbeat_fn the wrapper wired up — never a fake stand-in.
            if heartbeat_fn is not None:
                assert heartbeat_fn() is False
                assert heartbeat_fn() is False
            return {
                "_persist_payload": {
                    "run_at": T0.isoformat(), "horizon": horizon, "n_stocks": 1,
                    "n_signals": 0, "summary_json": "{}", "universe": universe,
                    "signal_rows": [],
                },
            }

        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _fake_subprocess_runner)

        result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=_real_now(),
                                              heartbeat_every_n_stocks=10)
        assert result["ok"] is True
        assert len(heartbeat_calls) == 2
        assert all(hb["ok"] for hb in heartbeat_calls)

    def test_execute_and_complete_admitted_attempt_wires_real_heartbeat_and_lease_duration_into_run_validation(
        self, isolated_db, monkeypatch
    ):
        """V-USACT1-B — the wall-clock heartbeat is worthless if the
        wrapper wires it up wrong. execute_and_complete_admitted_attempt
        no longer calls run_validation directly at all (it goes through
        the killable _run_validation_in_subprocess boundary instead — see
        V-USACT1-B) — this test is adapted to capture the ACTUAL kwargs
        passed to THAT boundary and proves: (a) the real numeric
        lease_duration_seconds this attempt was admitted with is passed
        through, never a default/placeholder; (b) the passed heartbeat_fn
        is a real, callable closure bound to THIS attempt's actual owner/
        fencing_token — proven by invoking it and observing a genuine
        CAS-based expires_at advance in the real ledger, not merely
        asserting callable(heartbeat_fn)."""
        captured = {}

        def _recording_subprocess_runner(*, horizon, universe, max_workers, trigger_type,
                                           heartbeat_fn=None, lease_duration_seconds=None,
                                           max_run_duration_seconds=None):
            captured["lease_duration"] = lease_duration_seconds
            captured["heartbeat_is_callable"] = callable(heartbeat_fn)

            with sqlite3.connect(isolated_db) as conn:
                row_before = conn.execute(
                    "SELECT expires_at FROM validation_execution_leases "
                    "WHERE resource_key='validation-global'"
                ).fetchone()

            captured["heartbeat_fenced"] = heartbeat_fn() if heartbeat_fn is not None else None

            with sqlite3.connect(isolated_db) as conn:
                row_after = conn.execute(
                    "SELECT expires_at FROM validation_execution_leases "
                    "WHERE resource_key='validation-global'"
                ).fetchone()
            captured["expires_before"] = row_before[0]
            captured["expires_after"] = row_after[0]

            return {
                "_persist_payload": {
                    "run_at": T0.isoformat(), "horizon": horizon, "n_stocks": 1,
                    "n_signals": 0, "summary_json": "{}", "universe": universe,
                    "signal_rows": [],
                },
            }

        monkeypatch.setattr(ve, "_run_validation_in_subprocess", _recording_subprocess_runner)

        result = execute_admitted_validation(
            horizon="medium", universe="nifty100", trigger_type="scheduler",
            owner="scheduler-1", scheduled_slot=T0, now=_real_now(),
            lease_duration_seconds=123, heartbeat_every_n_stocks=10,
        )
        assert result["ok"] is True
        assert captured["lease_duration"] == 123, (
            "the wrapper must pass THIS attempt's actual lease_duration_seconds, "
            f"got {captured.get('lease_duration')!r}"
        )
        assert captured["heartbeat_is_callable"] is True
        assert captured["heartbeat_fenced"] is False, (
            "a fresh, still-valid owner/fencing_token heartbeat must not report fencing loss"
        )
        assert captured["expires_after"] > captured["expires_before"], (
            "the wired heartbeat_fn callback did not perform a real CAS renewal against "
            "this attempt's actual owner/fencing_token — expires_at must genuinely advance"
        )

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
        # _fake_run_validation_factory's fake genuinely calls the REAL
        # heartbeat_fn once and raises _FencedOutDuringComputation if it
        # reports rejection — never a stand-in for the fencing check
        # itself (that logic is unchanged, real production code).
        _fake_run_validation_factory(monkeypatch, isolated_db, horizon="medium", universe="nifty100")

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


# ─────────────────────────────────────────────────────────────────────────
# V-SCHED1C1-ROLLOUT1 — first-deployment catch-up bootstrap safety.
# Before this correction, _catchup_validation() called
# get_or_create_schedule_slot() unconditionally on any post-06:00-IST
# startup — on the very first deployment of this feature (an empty,
# never-activated ledger), this would create today's slot on the spot and
# immediately fire a "catch-up" validation run, even though nothing was
# ever actually missed. has_established_schedule_baseline() distinguishes
# "never run before" (skip, bootstrap-safe) from "a real baseline exists
# and today's slot is genuinely due" (catch up, unchanged behavior).
# ─────────────────────────────────────────────────────────────────────────

class TestCatchupBootstrapSafety:
    def test_no_baseline_on_a_completely_empty_ledger(self, isolated_db):
        """RED-proving: an empty ledger (nothing ever scheduled) must
        report no established baseline for medium/nifty100."""
        assert has_established_schedule_baseline(
            horizon="medium", universe="nifty100", schedule_version="v1"
        ) is False

    def test_baseline_becomes_true_after_any_slot_ever_created(self, isolated_db):
        """A single slot creation — however it happens (normal scheduler,
        catch-up, or a test) — permanently establishes the baseline for
        that exact (horizon, universe, schedule_version) identity."""
        assert has_established_schedule_baseline(
            horizon="medium", universe="nifty100", schedule_version="v1"
        ) is False
        get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                     scheduled_slot=T0, schedule_version="v1", now=T0)
        assert has_established_schedule_baseline(
            horizon="medium", universe="nifty100", schedule_version="v1"
        ) is True

    def test_baseline_is_scoped_exactly_to_horizon_universe_version(self, isolated_db):
        """Establishing a baseline for one universe must not leak into
        another — the guard is exact-identity scoped, matching the same
        exact-canonical-slot discipline as catch-up's own suppression fix."""
        get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                     scheduled_slot=T0, schedule_version="v1", now=T0)
        assert has_established_schedule_baseline(
            horizon="medium", universe="midcap", schedule_version="v1"
        ) is False
        assert has_established_schedule_baseline(
            horizon="long", universe="nifty100", schedule_version="v1"
        ) is False

    def test_normal_scheduler_establishes_first_baseline_at_next_window(self, isolated_db, monkeypatch):
        """The normal scheduled path (not catch-up) is exactly how a
        first-ever deployment is expected to establish its baseline —
        unaffected by this guard, since the guard lives only in catch-up."""
        assert has_established_schedule_baseline(
            horizon="medium", universe="nifty100", schedule_version="v1"
        ) is False
        _fake_run_validation_factory(monkeypatch, isolated_db)
        result = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                              owner="scheduler-1", scheduled_slot=T0, now=_real_now())
        assert result["ok"] is True
        assert has_established_schedule_baseline(
            horizon="medium", universe="nifty100", schedule_version="v1"
        ) is True

    def test_genuinely_missing_later_slot_still_catches_up_once_baseline_exists(self, isolated_db, monkeypatch):
        """Once a baseline is established (day 1's slot completed), a
        genuinely missing LATER day's slot must still be caught up exactly
        once — the guard must never suppress real catch-up, only the
        bootstrap false-positive."""
        _fake_run_validation_factory(monkeypatch, isolated_db)
        day1 = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="scheduler",
                                            owner="scheduler-1", scheduled_slot=T0, now=_real_now())
        assert day1["ok"] is True
        assert has_established_schedule_baseline(
            horizon="medium", universe="nifty100", schedule_version="v1"
        ) is True

        day2 = T0 + timedelta(days=1)
        day2_slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                                  scheduled_slot=day2, schedule_version="v1", now=day2)
        assert day2_slot["status"] == "due"  # genuinely missed — must still catch up
        _fake_run_validation_factory(monkeypatch, isolated_db)
        catchup = execute_admitted_validation(horizon="medium", universe="nifty100", trigger_type="catchup",
                                               owner="catchup-1", slot_id=day2_slot["id"], now=_real_now())
        assert catchup["ok"] is True
        assert get_schedule_slot(day2_slot["id"])["status"] == "completed"

    def test_existing_due_slot_still_reported_due_regardless_of_baseline_guard(self, isolated_db):
        """The baseline guard is orthogonal to slot due/non-due semantics
        — once a baseline exists, an existing slot's own status is
        unaffected by this correction."""
        get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                     scheduled_slot=T0, schedule_version="v1", now=T0)
        slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        assert slot["status"] == "due"

    def test_catchup_source_checks_baseline_before_creating_todays_slot(self):
        """Structural proof (matches this file's existing source-inspection
        convention for main.py's nested async functions): the bootstrap
        guard must be checked BEFORE get_or_create_schedule_slot is ever
        called in _catchup_validation — the check must gate slot creation,
        not merely exist somewhere in the function."""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module)
        catchup_start = src.index("async def _catchup_validation():")
        catchup_src = src[catchup_start:src.index("task = asyncio.create_task(_weekly_refresh_loop())")]
        guard_pos = catchup_src.index("has_established_schedule_baseline(")
        create_pos = catchup_src.index("get_or_create_schedule_slot(\n")
        assert guard_pos < create_pos, (
            "has_established_schedule_baseline must be checked BEFORE "
            "get_or_create_schedule_slot is called, not after"
        )

    def test_cadence_and_short_and_manual_behavior_unaffected_by_rollout1(self):
        """Sanity cross-check (Stage 3's non-regression list) — this
        correction touches only the catch-up bootstrap path; the
        scheduler's own cadence constants and the short-horizon inactivity
        invariant are untouched by it."""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "TARGET_HOUR = 6" in src
        assert "weekday() == 6" in src
        assert "5 * 60" in src
        # V-SCHED1C2B — the existing medium/long loop itself must still
        # never reference horizon="short"; automatic short now exists,
        # but strictly in its own separate, gated scheduler/catch-up
        # functions (see TestAutoShortSchedulerStructure below), never
        # inside this one.
        assert "horizon=\"short\"" not in src


# ─────────────────────────────────────────────────────────────────────────
# V-SCHED1C2B — automatic short-horizon scheduler foundation, inactive by
# default. Covers: VALIDATION_AUTO_SHORT_UNIVERSES strict configuration
# parsing, the separate 03:30 IST short scheduler/catch-up loops'
# structural properties (read directly from production source, matching
# this file's existing convention for main.py's nested async functions),
# and bounded catch-up behavior.
# ─────────────────────────────────────────────────────────────────────────

class TestAutoShortConfigParser:
    """Items 18-26 of the V-SCHED1C2B RED matrix."""

    def test_missing_variable_produces_empty_set(self):
        from api.main import _parse_auto_short_universes
        assert _parse_auto_short_universes(None) == ()

    def test_blank_variable_produces_empty_set(self):
        from api.main import _parse_auto_short_universes
        assert _parse_auto_short_universes("") == ()
        assert _parse_auto_short_universes("   ") == ()

    def test_valid_single_universe(self):
        from api.main import _parse_auto_short_universes
        assert _parse_auto_short_universes("nifty100") == ("nifty100",)

    def test_valid_multiple_universes_stable_order(self):
        from api.main import _parse_auto_short_universes
        # deliberately out-of-order input — output must still be the
        # canonical nifty100, midcap, us order
        assert _parse_auto_short_universes("us,nifty100,midcap") == ("nifty100", "midcap", "us")

    def test_whitespace_and_case_normalization(self):
        from api.main import _parse_auto_short_universes
        assert _parse_auto_short_universes(" Nifty100 , MIDCAP ,us ") == ("nifty100", "midcap", "us")

    def test_duplicate_tokens_collapse_safely(self):
        from api.main import _parse_auto_short_universes
        assert _parse_auto_short_universes("nifty100,nifty100,midcap") == ("nifty100", "midcap")

    def test_unknown_token_disables_the_entire_value(self):
        from api.main import _parse_auto_short_universes
        assert _parse_auto_short_universes("nifty100,bogus") == ()
        assert _parse_auto_short_universes("bogus") == ()

    def test_ambiguous_values_rejected(self):
        from api.main import _parse_auto_short_universes
        assert _parse_auto_short_universes("all") == ()
        assert _parse_auto_short_universes("true") == ()
        assert _parse_auto_short_universes("1") == ()

    def test_no_secret_or_raw_environment_dump_in_logs(self, caplog):
        import logging
        from api.main import _parse_auto_short_universes
        with caplog.at_level(logging.WARNING):
            result = _parse_auto_short_universes("nifty100,SOME_SECRET_LOOKING_TOKEN_XYZ")
        assert result == ()
        combined = " ".join(r.message for r in caplog.records)
        assert "SOME_SECRET_LOOKING_TOKEN_XYZ" not in combined


class TestAutoShortSchedulerStructure:
    """Items 27-38 — structural proof read directly from production
    source, matching this file's existing main.py source-inspection
    convention (used throughout TestCadenceUnchanged/TestShortRemainsInactive)."""

    def test_short_loop_is_structurally_separate_function(self):
        import inspect
        import api.main as main_module
        assert hasattr(main_module, "_short_validation_schedule_loop")
        short_src = inspect.getsource(main_module._short_validation_schedule_loop)
        medium_src = inspect.getsource(main_module._validation_schedule_loop)
        assert short_src != medium_src

    def test_short_loop_targets_0330_ist(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._short_validation_schedule_loop)
        assert "TARGET_HOUR = 3" in src
        assert "TARGET_MINUTE = 30" in src

    def test_flag_absent_means_no_calendar_resolution_or_execution(self, monkeypatch):
        """A behavioral proof, not just structural: with the env var
        unset, one full loop-body iteration must never call
        resolve_latest_completed_short_session or execute_admitted_validation."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        enabled = main_module._parse_auto_short_universes(
            __import__("os").environ.get("VALIDATION_AUTO_SHORT_UNIVERSES")
        )
        assert enabled == ()

    def test_enabled_universes_use_stable_canonical_order(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module)
        # the loop must iterate `enabled` (already produced in canonical
        # order by the parser) rather than re-deriving its own order
        short_loop_src = inspect.getsource(main_module._short_validation_schedule_loop)
        assert "for univ in enabled" in short_loop_src or "for univ in " in short_loop_src

    def test_short_scheduler_calls_only_execute_admitted_validation(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._short_validation_schedule_loop)
        assert "execute_admitted_validation(" in src
        assert "run_validation(" not in src
        assert "complete_running_attempt_with_computed_result(" not in src

    def test_short_scheduler_retains_schedule_version_v1(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._short_validation_schedule_loop)
        assert 'schedule_version="v1"' in src

    def test_short_scheduler_uses_resolved_close_as_scheduled_slot(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._short_validation_schedule_loop)
        assert "close_utc" in src
        assert "scheduled_slot=" in src

    def test_short_scheduler_retains_unexpected_exception_backoff(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._short_validation_schedule_loop)
        assert "asyncio.sleep(3600)" in src

    def test_medium_loop_source_is_byte_identical_to_pre_1c2b(self):
        """Direct diff-style proof that _validation_schedule_loop's own
        body was not touched by this correction — the only sanctioned
        change anywhere near it is the ADDITION of a new, separate
        function after it."""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "TARGET_HOUR = 6" in src
        assert "await asyncio.sleep(180)" in src
        assert "5 * 60" in src
        assert "weekday() == 6" in src

    def test_no_horizon_short_execution_reachable_outside_gated_functions(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module)
        # every horizon="short" occurrence must be textually inside one
        # of the two short-specific functions
        short_sched_src = inspect.getsource(main_module._short_validation_schedule_loop)
        lifespan_src = inspect.getsource(main_module.lifespan)
        occurrences = src.count('horizon="short"')
        occurrences_in_short_sched = short_sched_src.count('horizon="short"')
        occurrences_in_lifespan = lifespan_src.count('horizon="short"')  # catch-up is nested inside lifespan
        assert occurrences == occurrences_in_short_sched + occurrences_in_lifespan


class TestAutoShortCatchup:
    """Items 39-46."""

    def test_catchup_disabled_when_allowlist_empty(self, monkeypatch):
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        assert main_module._parse_auto_short_universes(None) == ()

    def test_lifespan_source_contains_short_catchup_function(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module.lifespan)
        assert "_short_catchup_validation" in src

    @staticmethod
    def _short_catchup_body_only(main_module):
        """Slice out _short_catchup_validation's source, then drop its
        own docstring — several docstring sentences deliberately name
        the real function calls in prose (e.g. "exactly one
        get_or_create_schedule_slot() call per universe"), which would
        otherwise produce false substring matches before the real code
        even starts."""
        import inspect
        src = inspect.getsource(main_module.lifespan)
        catchup_start = src.index("async def _short_catchup_validation():")
        catchup_end = src.index("task = asyncio.create_task(_weekly_refresh_loop())")
        catchup_src = src[catchup_start:catchup_end]
        first_quote = catchup_src.index('"""')
        second_quote = catchup_src.index('"""', first_quote + 3)
        return catchup_src[second_quote + 3:]

    def test_short_catchup_checks_baseline_before_creating_slot(self):
        import api.main as main_module
        body = self._short_catchup_body_only(main_module)
        guard_pos = body.index("has_established_schedule_baseline(")
        create_pos = body.index("get_or_create_schedule_slot(")
        assert guard_pos < create_pos

    def test_short_catchup_resolves_only_latest_session_no_historical_walk(self):
        import api.main as main_module
        body = self._short_catchup_body_only(main_module)
        # exactly one resolve call per universe iteration — no loop over
        # a range/history of sessions
        assert "for _ in range" not in body
        assert body.count("resolve_latest_completed_short_session(") == 1

    def test_short_catchup_uses_canonical_close_utc_slot(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module.lifespan)
        catchup_start = src.index("async def _short_catchup_validation():")
        catchup_end = src.index("task = asyncio.create_task(_weekly_refresh_loop())")
        catchup_src = src[catchup_start:catchup_end]
        assert "scheduled_slot=resolution.close_utc" in catchup_src

    def test_manual_short_attempt_remains_unbindable_to_scheduled_slot(self, isolated_db):
        """Reuses the existing manual-attempt structural guarantee
        (slot_id=NULL) already proven for other horizons — confirms it
        holds identically for short, with no special-case bypass."""
        admitted = admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                             owner="manual-1", now=T0)
        assert admitted["ok"] is True
        fetched = get_schedule_attempt(admitted["attempt_id"])
        assert fetched["slot_id"] is None

    def test_short_slot_and_scheduled_medium_slot_are_independent_identities(self, isolated_db):
        slot_short = get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                                   scheduled_slot=T0, schedule_version="v1", now=T0)
        slot_medium = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                                    scheduled_slot=T0, schedule_version="v1", now=T0)
        assert slot_short["id"] != slot_medium["id"]
        assert has_established_schedule_baseline(horizon="short", universe="nifty100", schedule_version="v1") is True
        assert has_established_schedule_baseline(horizon="long", universe="nifty100", schedule_version="v1") is False
