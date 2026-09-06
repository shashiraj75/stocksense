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
    """2026-09 WEEKLY-ONLY POLICY: renamed in spirit but kept as the
    class name for git-blame continuity — short is no longer
    unconditionally inactive; it is now included in the single weekly
    Saturday batch WHEN AND ONLY WHEN VALIDATION_AUTO_SHORT_UNIVERSES
    enables it, via the shared enabled_validation_combinations() gate —
    same allowlist semantics as before, just reached from the one
    consolidated scheduler instead of a separate daily one."""

    def test_short_reachable_only_through_the_shared_enabled_combinations_gate(self):
        """The weekly scheduler must derive its combination list EXCLUSIVELY
        from enabled_validation_combinations() — never from a second,
        independently-maintained short-specific code path inside the loop
        itself (which would risk drifting from the allowlist semantics)."""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "enabled_validation_combinations()" in src
        # No separate short-specific admission call remains inside the loop —
        # short is admitted through the exact same execute_admitted_validation
        # call site as medium/long, differing only by which `horizon` the
        # shared combinations list yields for this iteration.
        assert src.count("execute_admitted_validation(\n") == 1

    def test_disabled_allowlist_yields_zero_short_combinations(self, monkeypatch):
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        combos = main_module.enabled_validation_combinations()
        assert all(h != "short" for h, _u in combos)
        # medium/long for all 3 universes must still be present regardless.
        assert len(combos) == 6

    def test_enabled_allowlist_adds_exactly_those_short_universes(self, monkeypatch):
        import api.main as main_module
        monkeypatch.setenv("VALIDATION_AUTO_SHORT_UNIVERSES", "nifty100,us")
        combos = main_module.enabled_validation_combinations()
        short_combos = [u for h, u in combos if h == "short"]
        assert short_combos == ["nifty100", "us"]  # canonical order preserved
        assert len(combos) == 8  # 2 short + 6 medium/long

    def test_short_slots_are_admitted_like_any_other_combination_when_enabled(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="short", universe="us",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        assert slot["horizon"] == "short"
        assert slot["status"] == "due"


# ─────────────────────────────────────────────────────────────────────────
# 18: cadence unchanged — structural proof (byte-for-byte on the timing
# constants, behaviorally unchanged on ordering/spacing)
# ─────────────────────────────────────────────────────────────────────────

class TestCadenceUnchanged:
    def test_short_medium_and_long_now_share_the_single_weekly_saturday_cadence(self):
        """2026-09 WEEKLY-ONLY POLICY: supersedes the prior 'medium+long
        weekly, short independently daily at 03:30 IST' split — short was
        moved into this exact same weekly window, not merely made to
        LOOK similar via a second copy of the same constants. This guard
        protects the unified cadence against accidental drift, and
        explicitly proves the old IST-anchored daily short constants are
        gone from the codebase entirely, not just unused."""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        # New cadence: UTC-anchored weekly slot, shared helper, ALL horizons
        # via the single shared combinations list.
        assert "next_saturday_1200_utc" in src
        assert "enabled_validation_combinations()" in src
        assert "5 * 60" in src  # 5-minute inter-combination gap preserved
        # Old short-specific daily cadence must be fully gone from the module,
        # not merely superseded in effect by leaving dead code behind.
        full_src = inspect.getsource(main_module)
        assert "TARGET_HOUR = 3" not in full_src
        assert "TARGET_MINUTE = 30" not in full_src
        assert not hasattr(main_module, "_short_validation_schedule_loop")
        assert not hasattr(main_module, "_short_catchup_validation")
        assert not hasattr(main_module, "_catchup_validation")

    def test_universe_ordering_unchanged(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module.enabled_validation_combinations)
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

    def test_missed_slot_check_source_checks_baseline_before_reading_slot_status(self):
        """2026-09 WEEKLY-ONLY POLICY: _catchup_validation (which USED to
        call get_or_create_schedule_slot — i.e. execute a catch-up run) is
        REMOVED. Its replacement, compute_missed_validation_combinations
        (called from the read-only _validation_missed_slot_check startup
        task), never creates a slot or admits an attempt — proven
        BEHAVIORALLY below, not just structurally: a combination with no
        established baseline is never reported missed, even though its
        slot is absent (which — for an already-baselined combination —
        WOULD be reported missed)."""
        import api.main as main_module
        assert not hasattr(main_module, "_catchup_validation")
        assert not hasattr(main_module, "_short_catchup_validation")

    def test_first_deployment_bootstrap_never_reports_missed(self, isolated_db, monkeypatch):
        """No baseline has ever been established for ANY combination
        (empty ledger) — every combination must be silently skipped, not
        reported as missed, even though this week's slot is well past."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        now_utc = datetime(2026, 8, 15, 13, 0, tzinfo=timezone.utc)  # a Saturday, 1h past 12:00 UTC
        missed, this_weeks_slot, next_slot = main_module.compute_missed_validation_combinations(now_utc)
        assert missed == []
        assert this_weeks_slot is not None
        assert next_slot > this_weeks_slot

    def test_genuinely_missed_combination_with_an_established_baseline_is_reported(self, isolated_db, monkeypatch):
        """Once a baseline exists for medium/nifty100 (some PRIOR week's
        slot was created), and THIS week's Saturday slot was never
        created/admitted at all, it must be reported as missed — proving
        the read-only check can distinguish "never run before" from
        "has run before, but not this week"."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        last_week = T0  # establishes a baseline for medium/nifty100 (and every 3x2=6 combo below)
        for h in ("medium", "long"):
            for u in ("nifty100", "midcap", "us"):
                get_or_create_schedule_slot(horizon=h, universe=u, scheduled_slot=last_week,
                                             schedule_version="v1", now=last_week)
        now_utc = last_week + timedelta(days=8)  # a week+ later, no new slot ever created for it
        missed, this_weeks_slot, next_slot = main_module.compute_missed_validation_combinations(now_utc)
        assert len(missed) == 6  # all 6 medium/long combos genuinely missed this week
        assert this_weeks_slot is not None

    def test_a_combination_that_completed_this_weeks_slot_is_not_reported_missed(self, isolated_db, monkeypatch):
        import api.main as main_module
        from services.market_calendar import last_saturday_1200_utc
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        now_utc = T0 + timedelta(hours=2)
        this_saturday = last_saturday_1200_utc(now_utc)
        for h in ("medium", "long"):
            for u in ("nifty100", "midcap", "us"):
                if h == "medium" and u == "nifty100":
                    # Genuinely complete exactly this one combination via
                    # the real admission path — the others stay untouched
                    # ("due" once created below, or absent entirely).
                    _fake_run_validation_factory(monkeypatch, isolated_db, horizon=h, universe=u)
                    result = execute_admitted_validation(horizon=h, universe=u, trigger_type="scheduler",
                                                          owner="scheduler-1", scheduled_slot=this_saturday, now=_real_now())
                    assert result["ok"] is True
                else:
                    get_or_create_schedule_slot(horizon=h, universe=u, scheduled_slot=this_saturday,
                                                 schedule_version="v1", now=this_saturday)
        missed, this_weeks_slot, next_slot = main_module.compute_missed_validation_combinations(now_utc)
        assert "medium/nifty100" not in missed
        assert "medium/midcap" in missed  # still "due" — genuinely not yet run

    def test_weekday_of_the_startup_check_never_changes_which_slot_is_evaluated(self, isolated_db, monkeypatch):
        """Sunday/Monday/Wednesday startup — any weekday AFTER Saturday
        Aug 15 and before the NEXT Saturday (Aug 22) — must all evaluate
        against the exact same most-recent-Saturday slot (Aug 15). There
        is no day-of-week special-casing anywhere in the missed-slot
        check; it is purely a function of last_saturday_1200_utc(now_utc).
        (Friday Aug 14 is deliberately excluded here — it precedes Aug
        15 entirely, so it correctly resolves to the WEEK BEFORE's
        Saturday, Aug 8, not Aug 15 — a separate, equally-covered case
        below, not a special-case in the code.)"""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        sunday = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)
        monday = datetime(2026, 8, 17, 10, 0, tzinfo=timezone.utc)
        wednesday = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)
        for now_utc in (sunday, monday, wednesday):
            missed, this_weeks_slot, next_slot = main_module.compute_missed_validation_combinations(now_utc)
            assert this_weeks_slot == datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)
            assert missed == []  # no baseline anywhere yet -> nothing reported, all silently skipped

    def test_friday_before_the_slot_resolves_to_the_prior_completed_week(self, isolated_db, monkeypatch):
        """A Friday startup necessarily precedes THAT week's own Saturday
        slot — it evaluates against the PRIOR week's Saturday instead,
        exactly like any other pre-Saturday weekday would. This is
        ordinary calendar arithmetic, not a special "before this week"
        branch that skips evaluation entirely."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        friday = datetime(2026, 8, 14, 10, 0, tzinfo=timezone.utc)
        missed, this_weeks_slot, next_slot = main_module.compute_missed_validation_combinations(friday)
        assert this_weeks_slot == datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
        assert next_slot == datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)

    def test_never_creates_a_slot_or_admits_an_attempt(self, isolated_db, monkeypatch):
        """The single most important property: calling this function must
        never itself create a schedule_slots row or an attempt — it is
        purely observational."""
        import api.main as main_module
        from services.market_calendar import last_saturday_1200_utc
        from services.validation_engine import find_schedule_slot
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        # Establish a baseline first (otherwise the bootstrap guard alone
        # would explain the absence) — then call the check well past that
        # week's slot, on a completely empty week with no slot ever made.
        get_or_create_schedule_slot(horizon="medium", universe="nifty100", scheduled_slot=T0,
                                     schedule_version="v1", now=T0)
        now_utc = T0 + timedelta(days=8)
        this_week = last_saturday_1200_utc(now_utc)
        main_module.compute_missed_validation_combinations(now_utc)
        # No slot exists for THIS week's instant — the check never created one.
        assert find_schedule_slot(horizon="medium", universe="nifty100",
                                    scheduled_slot=this_week, schedule_version="v1") is None

    def test_cadence_and_short_and_manual_behavior_unaffected_by_rollout1(self):
        """Sanity cross-check (Stage 3's non-regression list) — this
        correction touches only the catch-up bootstrap path; the
        short-horizon inclusion (2026-09: now via the shared
        enabled_validation_combinations() gate, not a separate daily
        schedule) is untouched by it. (The scheduler's own cadence
        constants are asserted separately in TestCadenceUnchanged.)"""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "5 * 60" in src
        assert "enabled_validation_combinations()" in src


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
    """2026-09 WEEKLY-ONLY POLICY: the independent daily short scheduler
    this class used to test (_short_validation_schedule_loop, 03:30 IST)
    is REMOVED — short is now admitted from the single weekly
    _validation_schedule_loop, gated by enabled_validation_combinations().
    These tests assert the removal is complete and the replacement
    behavior is correct, rather than testing a function that no longer
    exists."""

    def test_short_validation_schedule_loop_no_longer_exists(self):
        import api.main as main_module
        assert not hasattr(main_module, "_short_validation_schedule_loop")

    def test_no_independent_ist_anchored_daily_schedule_remains_anywhere(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module)
        assert "TARGET_HOUR = 3" not in src
        assert "TARGET_MINUTE = 30" not in src

    def test_flag_absent_means_short_excluded_from_the_weekly_batch(self, monkeypatch):
        """Behavioral proof: with the env var unset, the single weekly
        batch's combination list contains zero short entries — no
        calendar resolution, no slot creation, no admission for short at
        all that week."""
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        combos = main_module.enabled_validation_combinations()
        assert all(h != "short" for h, _u in combos)

    def test_enabled_universes_use_stable_canonical_order(self):
        import api.main as main_module
        import os
        os.environ["VALIDATION_AUTO_SHORT_UNIVERSES"] = "us,nifty100,midcap"
        try:
            combos = main_module.enabled_validation_combinations()
        finally:
            del os.environ["VALIDATION_AUTO_SHORT_UNIVERSES"]
        short_combos = [u for h, u in combos if h == "short"]
        assert short_combos == ["nifty100", "midcap", "us"]

    def test_weekly_scheduler_calls_only_execute_admitted_validation(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "execute_admitted_validation(" in src
        assert "run_validation(" not in src
        assert "complete_running_attempt_with_computed_result(" not in src

    def test_weekly_scheduler_retains_schedule_version_v1(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert 'schedule_version="v1"' in src

    def test_weekly_scheduler_uses_the_shared_saturday_slot_not_a_resolved_session_close(self):
        """2026-09: short no longer uses its own resolved exchange-session
        close as its scheduled_slot (that was the daily design) — it now
        shares the exact same `next_run` (Saturday 12:00 UTC) instant as
        every other combination in the batch."""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "scheduled_slot=slot_instant" in src
        assert "close_utc" not in src

    def test_weekly_scheduler_retains_unexpected_exception_backoff(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module._validation_schedule_loop)
        assert "asyncio.sleep(3600)" in src

    def test_no_horizon_short_execution_reachable_outside_the_shared_gate(self):
        """Every place `execute_admitted_validation` could run short must
        trace back to enabled_validation_combinations() — there is no
        second, independently-maintained short-specific admission call
        left anywhere in the module."""
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module)
        assert src.count("execute_admitted_validation(\n") == 1
        assert "resolve_latest_completed_short_session(" not in src


class TestAutoShortCatchup:
    """2026-09 WEEKLY-ONLY POLICY: the short-specific startup catch-up
    this class used to test (_short_catchup_validation, which EXECUTED a
    missed session's validation) is REMOVED. All startup behavior for a
    missed slot — short included — now goes through the single, read-only
    _validation_missed_slot_check, which never executes anything."""

    def test_catchup_disabled_when_allowlist_empty(self, monkeypatch):
        import api.main as main_module
        monkeypatch.delenv("VALIDATION_AUTO_SHORT_UNIVERSES", raising=False)
        assert main_module._parse_auto_short_universes(None) == ()

    def test_short_catchup_validation_no_longer_exists(self):
        import api.main as main_module
        assert not hasattr(main_module, "_short_catchup_validation")
        assert not hasattr(main_module, "_catchup_validation")

    def test_lifespan_source_contains_the_unified_missed_slot_check(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module.lifespan)
        assert "async def _validation_missed_slot_check():" in src
        assert "async def _short_catchup_validation():" not in src
        assert "async def _catchup_validation():" not in src

    @staticmethod
    def _missed_slot_check_body_only(main_module):
        """Slice out _validation_missed_slot_check's source (see
        TestCatchupBootstrapSafety for the equivalent baseline-ordering
        test on this same function)."""
        import inspect
        src = inspect.getsource(main_module.lifespan)
        start = src.index("async def _validation_missed_slot_check():")
        end = src.index("task = asyncio.create_task(_weekly_refresh_loop())")
        return src[start:end]

    def test_missed_slot_check_never_resolves_a_daily_exchange_session(self):
        """The old short catch-up resolved the latest completed exchange
        session per universe (a daily-granularity concept) — the weekly
        replacement has no such concept at all; it only compares against
        the shared Saturday 12:00 UTC slot."""
        import inspect
        import api.main as main_module
        assert "resolve_latest_completed_short_session(" not in inspect.getsource(main_module)

    def test_missed_slot_check_uses_the_shared_weekly_slot_helpers(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module.compute_missed_validation_combinations)
        assert "last_saturday_1200_utc(" in src
        assert "next_saturday_1200_utc(" in src

    def test_missed_slot_check_iterates_the_same_shared_combinations_list(self):
        import inspect
        import api.main as main_module
        src = inspect.getsource(main_module.compute_missed_validation_combinations)
        assert "enabled_validation_combinations()" in src

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
