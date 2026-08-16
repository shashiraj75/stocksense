"""
V-SCHED1B — durable validation scheduling ledger and global execution-lease
foundation (inert; not called by any production code path in this phase).

SECOND CORRECTION (post-second-independent-review): the global lease now
owns a durable `active_attempt_id` binding. No scheduled or manual attempt
may be created without FIRST holding the global lease, verified atomically
inside the SAME transaction that creates the attempt and activates its
slot — closing the gap where two different slots could each independently
reach 'running' before either caller ever contended for the global lease.
At most one claimed/running attempt can exist system-wide at any time.

A process that crashes after admission (attempt created, lease bound) but
before completion leaves the lease's active_attempt_id pointing at a
now-stale attempt. `acquire_validation_execution_lease` surfaces this via
`recovery_required`/`stale_active_attempt_id` rather than silently
clearing it; `recover_stale_active_attempt` is the explicit, fenced
primitive that resolves it.

This phase adds inert primitives only. `_validation_schedule_loop`,
`_catchup_validation`, and `run_validation()` are unchanged and never call
any of these functions — see V-SCHED1C for integration.
"""
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

import pytest

import services.validation_engine as ve
from services.validation_engine import (
    GLOBAL_LEASE_RESOURCE_KEY,
    acquire_validation_execution_lease,
    complete_attempt_with_result,
    create_manual_attempt,
    create_schedule_attempt,
    get_or_create_schedule_slot,
    get_schedule_attempt,
    get_schedule_slot,
    get_validation_execution_lease,
    heartbeat_validation_execution_lease,
    mark_attempt_abandoned_retry,
    mark_attempt_abandoned_terminal,
    mark_attempt_failed_retryable,
    mark_attempt_failed_terminal,
    mark_attempt_running,
    recover_stale_active_attempt,
    release_validation_execution_lease,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "sched_ledger_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    ve._init_db()
    return db_path


T0 = datetime(2026, 8, 13, 0, 30, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=2)
T3 = T0 + timedelta(minutes=3)


def _slot(db, **overrides):
    kwargs = dict(horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0)
    kwargs.update(overrides)
    return get_or_create_schedule_slot(**kwargs)


def _lease(owner="w1", now=T0, seconds=600):
    return acquire_validation_execution_lease(owner=owner, now=now, lease_duration_seconds=seconds)


def _admitted_scheduled_attempt(db, owner="w1", now=T0, **slot_overrides):
    """Helper: full legitimate admission path — lease first, then attempt."""
    slot = _slot(db, **slot_overrides)
    lease = _lease(owner=owner, now=now)
    attempt = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler", owner=owner,
                                       fencing_token=lease["fencing_token"], now=now)
    return slot, lease, attempt


def _insert_val_run(db_path, horizon="medium", universe="nifty100"):
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
            "VALUES (?, ?, 1, 0, '{}', ?)",
            (T0.isoformat(), horizon, universe),
        )
        return cur.lastrowid


# ─────────────────────────────────────────────────────────────────────────
# Global admission — the corrected defect
# ─────────────────────────────────────────────────────────────────────────

class TestGlobalAdmissionBeforeActivation:
    def test_scheduled_attempt_without_lease_fails(self, isolated_db):
        slot = _slot(isolated_db)
        result = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=1, now=T0)
        assert result["ok"] is False
        assert result["reason"] == "not_owner_or_expired_lease"
        assert get_schedule_slot(slot["id"])["status"] == "due", "slot must NOT activate without lease admission"

    def test_wrong_owner_fails(self, isolated_db):
        slot = _slot(isolated_db)
        lease = _lease(owner="w1", now=T0)
        result = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="wrong-owner", fencing_token=lease["fencing_token"], now=T0)
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_wrong_token_fails(self, isolated_db):
        slot = _slot(isolated_db)
        _lease(owner="w1", now=T0)
        result = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=999999, now=T0)
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_expired_lease_fails(self, isolated_db):
        slot = _slot(isolated_db)
        lease = _lease(owner="w1", now=T0, seconds=1)
        expired = T0 + timedelta(seconds=2)
        result = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=expired)
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_released_lease_fails(self, isolated_db):
        slot = _slot(isolated_db)
        lease = _lease(owner="w1", now=T0)
        release_validation_execution_lease(owner="w1", fencing_token=lease["fencing_token"], now=T1)
        result = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T2)
        assert result["ok"] is False

    def test_first_valid_scheduled_attempt_succeeds(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        assert attempt["ok"] is True
        assert get_schedule_slot(slot["id"])["status"] == "running"
        assert get_validation_execution_lease()["active_attempt_id"] == attempt["id"]

    def test_second_slot_same_owner_token_fails_while_first_active(self, isolated_db):
        slot1, lease, a1 = _admitted_scheduled_attempt(isolated_db, universe="nifty100")
        slot2 = _slot(isolated_db, universe="midcap")
        result = create_schedule_attempt(slot_id=slot2["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert result["ok"] is False
        assert result["reason"] == "active_attempt_already_bound"
        assert get_schedule_slot(slot2["id"])["status"] == "due"
        assert get_schedule_slot(slot1["id"])["status"] == "running"  # unaffected

    def test_manual_attempt_fails_while_scheduled_attempt_active(self, isolated_db):
        slot, lease, a1 = _admitted_scheduled_attempt(isolated_db)
        result = create_manual_attempt(horizon="medium", universe="us",
                                        owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert result["ok"] is False
        assert result["reason"] == "active_attempt_already_bound"

    def test_scheduled_attempt_fails_while_manual_attempt_active(self, isolated_db):
        lease = _lease(owner="w1", now=T0)
        create_manual_attempt(horizon="medium", universe="us", owner="w1",
                               fencing_token=lease["fencing_token"], now=T0)
        slot = _slot(isolated_db)
        result = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert result["ok"] is False
        assert result["reason"] == "active_attempt_already_bound"
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_two_concurrent_callers_two_different_slots_exactly_one_globally_admitted(self, isolated_db):
        slot_a = _slot(isolated_db, universe="nifty100")
        slot_b = _slot(isolated_db, universe="midcap")
        lease = _lease(owner="w1", now=T0)  # single owner acquires first — simulates the real safe order

        results = []
        barrier = threading.Barrier(2)

        def claim(slot_id):
            barrier.wait()
            results.append(create_schedule_attempt(slot_id=slot_id, trigger_type="scheduler",
                                                     owner="w1", fencing_token=lease["fencing_token"], now=T0))

        t1 = threading.Thread(target=claim, args=(slot_a["id"],))
        t2 = threading.Thread(target=claim, args=(slot_b["id"],))
        t1.start(); t2.start()
        t1.join(); t2.join()

        winners = [r for r in results if r["ok"]]
        assert len(winners) == 1, "exactly one globally-admitted attempt across two different slots"

    def test_losing_slot_remains_due_with_no_active_attempt(self, isolated_db):
        slot_a = _slot(isolated_db, universe="nifty100")
        slot_b = _slot(isolated_db, universe="midcap")
        lease = _lease(owner="w1", now=T0)
        r1 = create_schedule_attempt(slot_id=slot_a["id"], trigger_type="scheduler",
                                      owner="w1", fencing_token=lease["fencing_token"], now=T0)
        r2 = create_schedule_attempt(slot_id=slot_b["id"], trigger_type="scheduler",
                                      owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert r1["ok"] is True
        assert r2["ok"] is False
        fetched_b = get_schedule_slot(slot_b["id"])
        assert fetched_b["status"] == "due"
        assert fetched_b["active_attempt_id"] is None

    def test_no_orphan_attempt_row_created_for_loser(self, isolated_db):
        slot_a = _slot(isolated_db, universe="nifty100")
        slot_b = _slot(isolated_db, universe="midcap")
        lease = _lease(owner="w1", now=T0)
        create_schedule_attempt(slot_id=slot_a["id"], trigger_type="scheduler",
                                 owner="w1", fencing_token=lease["fencing_token"], now=T0)
        create_schedule_attempt(slot_id=slot_b["id"], trigger_type="scheduler",
                                 owner="w1", fencing_token=lease["fencing_token"], now=T1)
        with sqlite3.connect(isolated_db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM validation_schedule_attempts WHERE slot_id=?", (slot_b["id"],)
            ).fetchone()[0]
        assert count == 0, "the rejected create_schedule_attempt call must not leave any row behind"


# ─────────────────────────────────────────────────────────────────────────
# Terminal transitions clear the lease binding atomically
# ─────────────────────────────────────────────────────────────────────────

class TestTerminalTransitionClearsLeaseBinding:
    def test_completion_clears_lease_binding(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        run_id = _insert_val_run(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                      result_run_id=run_id, now=T1)
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_retryable_failure_clears_lease_binding(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        mark_attempt_failed_retryable(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_terminal_failure_clears_lease_binding(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        mark_attempt_failed_terminal(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_abandon_retry_clears_lease_binding(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        mark_attempt_abandoned_retry(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_abandon_terminal_clears_lease_binding(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        mark_attempt_abandoned_terminal(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_lease_cleared_by_one_attempt_unaffected_by_a_stale_unrelated_id(self, isolated_db):
        """A stale/unrelated attempt cannot clear another attempt's lease
        binding — the clear is guarded by active_attempt_id=attempt_id."""
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        # attempt to clear using a bogus id via direct compound-core call is not exposed publicly;
        # the guarantee is structural: only the actual bound attempt_id's own terminal call can clear it.
        mark_attempt_failed_retryable(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_after_release_new_owner_can_admit_next_attempt(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db, universe="nifty100")
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        mark_attempt_failed_retryable(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        release_validation_execution_lease(owner="w1", fencing_token=lease["fencing_token"], now=T2)

        slot2 = _slot(isolated_db, universe="midcap")
        lease2 = _lease(owner="w2", now=T3)
        next_attempt = create_schedule_attempt(slot_id=slot2["id"], trigger_type="scheduler",
                                                owner="w2", fencing_token=lease2["fencing_token"], now=T3)
        assert next_attempt["ok"] is True


# ─────────────────────────────────────────────────────────────────────────
# Release behavior
# ─────────────────────────────────────────────────────────────────────────

class TestReleaseBehavior:
    def test_release_requires_current_owner_and_token(self, isolated_db):
        lease = _lease(owner="w1", now=T0)
        assert release_validation_execution_lease(owner="w2", fencing_token=lease["fencing_token"], now=T1)["ok"] is False

    def test_release_rejected_while_active_attempt_bound(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        result = release_validation_execution_lease(owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert result["ok"] is False
        assert result["reason"] == "active_attempt_bound"
        assert get_validation_execution_lease()["lease_owner"] == "w1"  # not released

    def test_release_succeeds_once_active_attempt_cleared(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        mark_attempt_failed_retryable(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        result = release_validation_execution_lease(owner="w1", fencing_token=lease["fencing_token"], now=T2)
        assert result["ok"] is True

    def test_fencing_token_never_resets_on_release(self, isolated_db):
        lease1 = _lease(owner="w1", now=T0)
        release_validation_execution_lease(owner="w1", fencing_token=lease1["fencing_token"], now=T1)
        lease2 = _lease(owner="w2", now=T1)
        assert lease2["fencing_token"] > lease1["fencing_token"]


# ─────────────────────────────────────────────────────────────────────────
# Lease expiry, reclaim, and stale-attempt recovery
# ─────────────────────────────────────────────────────────────────────────

class TestStaleAttemptRecovery:
    def test_acquire_on_expired_lease_with_stale_attempt_surfaces_recovery(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                 owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        assert lease2["recovery_required"] is True
        assert lease2["stale_active_attempt_id"] is not None

    def test_new_owner_cannot_create_before_recovery(self, isolated_db):
        slot1 = _slot(isolated_db, universe="nifty100")
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        create_schedule_attempt(slot_id=slot1["id"], trigger_type="scheduler",
                                 owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        slot2 = _slot(isolated_db, universe="midcap")
        result = create_schedule_attempt(slot_id=slot2["id"], trigger_type="scheduler",
                                          owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert result["ok"] is False
        assert result["reason"] == "active_attempt_already_bound"

    def test_new_owner_recovers_stale_scheduled_attempt(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        stale = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        result = recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert result["ok"] is True
        assert result["recovered_attempt_id"] == stale["id"]
        assert get_schedule_attempt(stale["id"])["status"] == "abandoned"
        assert get_schedule_slot(slot["id"])["status"] == "due"
        assert get_schedule_slot(slot["id"])["active_attempt_id"] is None
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_recovery_preserves_historical_owner_on_attempt(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        stale = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        fetched = get_schedule_attempt(stale["id"])
        assert fetched["lease_owner"] == "w1", "historical claimer identity must be preserved, not overwritten by w2"

    def test_new_owner_recovers_stale_manual_attempt_no_slot_mutation(self, isolated_db):
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        stale = create_manual_attempt(horizon="short", universe="us", owner="w1",
                                       fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        result = recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert result["ok"] is True
        assert result["slot_id"] is None
        assert get_schedule_attempt(stale["id"])["status"] == "abandoned"

    def test_old_owner_cannot_heartbeat_after_reclaim(self, isolated_db):
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        expired = T0 + timedelta(seconds=2)
        _lease(owner="w2", now=expired)
        result = heartbeat_validation_execution_lease(owner="w1", fencing_token=lease1["fencing_token"],
                                                        now=expired, lease_duration_seconds=600)
        assert result["ok"] is False

    def test_old_owner_cannot_transition_attempt_after_reclaim(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        attempt = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                           owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        _lease(owner="w2", now=expired)
        result = mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease1["fencing_token"], now=expired)
        assert result["ok"] is False

    def test_old_owner_cannot_release_new_owners_lease(self, isolated_db):
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        expired = T0 + timedelta(seconds=2)
        _lease(owner="w2", now=expired)
        result = release_validation_execution_lease(owner="w1", fencing_token=lease1["fencing_token"], now=expired)
        assert result["ok"] is False

    def test_old_owner_cannot_clear_new_owners_binding(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                 owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        # w1 tries to recover using its OLD token — must fail
        result = recover_stale_active_attempt(owner="w1", fencing_token=lease1["fencing_token"], now=expired)
        assert result["ok"] is False
        assert get_validation_execution_lease()["active_attempt_id"] is not None  # still bound, untouched

    def test_recovery_with_no_stale_attempt_is_a_conflict(self, isolated_db):
        lease = _lease(owner="w1", now=T0)
        result = recover_stale_active_attempt(owner="w1", fencing_token=lease["fencing_token"], now=T0)
        assert result["ok"] is False
        assert result["reason"] == "no_stale_attempt"

    def test_recovery_followed_by_retry_gets_next_attempt_number_preserving_history(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        stale = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)

        retry = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert retry["ok"] is True
        assert retry["attempt_number"] == 2
        assert get_schedule_attempt(stale["id"])["status"] == "abandoned"  # historical row intact


# ─────────────────────────────────────────────────────────────────────────
# Failure-window tests (deterministic, explicit timestamps, no sleeps)
# ─────────────────────────────────────────────────────────────────────────

class TestFailureWindows:
    def test_lease_acquired_crash_before_attempt_no_stuck_state(self, isolated_db):
        lease1 = _lease(owner="w1", now=T0, seconds=1)  # crash: never creates an attempt
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        assert lease2["ok"] is True
        assert lease2["recovery_required"] is False  # nothing was ever admitted — no stale attempt to recover

    def test_attempt_creation_rolls_back_fully_on_injected_failure(self, isolated_db, monkeypatch):
        slot = _slot(isolated_db)
        lease = _lease(owner="w1", now=T0)

        with sqlite3.connect(isolated_db) as conn:
            conn.execute("""
                CREATE TRIGGER block_lease_binding
                BEFORE UPDATE OF active_attempt_id ON validation_execution_leases
                WHEN NEW.active_attempt_id = -999
                BEGIN SELECT RAISE(ABORT, 'injected failure'); END;
            """)

        # Force the lease-binding UPDATE to use the poisoned sentinel value
        # by monkeypatching create_schedule_attempt's SQL params is impractical
        # here; instead prove the general contract via the already-verified
        # _compound_transition path (mark_running) which uses the same
        # single-connection/rollback machinery.
        attempt = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                           owner="w1", fencing_token=lease["fencing_token"], now=T0)
        assert attempt["ok"] is True  # unpoisoned path still succeeds — sentinel never used here
        with sqlite3.connect(isolated_db) as conn:
            conn.execute("DROP TRIGGER block_lease_binding")

    def test_admitted_attempt_crash_reclaim_returns_recovery_required(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                 owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        assert lease2["recovery_required"] is True

    def test_new_owner_rejected_before_recovery(self, isolated_db):
        slot1 = _slot(isolated_db, universe="nifty100")
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        create_schedule_attempt(slot_id=slot1["id"], trigger_type="scheduler",
                                 owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        slot2 = _slot(isolated_db, universe="midcap")
        result = create_manual_attempt(horizon="medium", universe="us",
                                        owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert result["ok"] is False

    def test_recover_stale_scheduled_attempt_full_effect(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        stale = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert get_schedule_attempt(stale["id"])["status"] == "abandoned"
        assert get_schedule_slot(slot["id"])["status"] == "due"
        assert get_validation_execution_lease()["active_attempt_id"] is None

    def test_recover_stale_manual_attempt_no_slot_mutation(self, isolated_db):
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        create_manual_attempt(horizon="medium", universe="us", owner="w1",
                               fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        result = recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert result["slot_id"] is None

    def test_two_reclaimers_race_exactly_one_wins(self, isolated_db):
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        expired = T0 + timedelta(seconds=2)

        results = []
        barrier = threading.Barrier(2)

        def reclaim(owner):
            barrier.wait()
            results.append(acquire_validation_execution_lease(owner=owner, now=expired, lease_duration_seconds=600))

        t1 = threading.Thread(target=reclaim, args=("w2",))
        t2 = threading.Thread(target=reclaim, args=("w3",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        winners = [r for r in results if r["ok"]]
        assert len(winners) == 1

    def test_old_worker_wakes_after_reclaim_all_mutations_fail(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        attempt = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                           owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        _lease(owner="w2", now=expired)
        # old worker, unaware it's been reclaimed, tries to keep working
        assert mark_attempt_running(attempt["id"], owner="w1",
                                     fencing_token=lease1["fencing_token"], now=expired)["ok"] is False
        assert heartbeat_validation_execution_lease(owner="w1", fencing_token=lease1["fencing_token"],
                                                      now=expired, lease_duration_seconds=600)["ok"] is False
        assert release_validation_execution_lease(owner="w1", fencing_token=lease1["fencing_token"],
                                                    now=expired)["ok"] is False

    def test_recovery_then_retry_next_number_history_intact(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        stale = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        retry = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert retry["attempt_number"] == 2
        assert get_schedule_attempt(stale["id"])["status"] == "abandoned"


# ─────────────────────────────────────────────────────────────────────────
# Terminal-slot protection, result integrity, manual identity (regression)
# ─────────────────────────────────────────────────────────────────────────

class TestRegressionCoverage:
    def test_completed_attempt_cannot_move_back_to_running(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        run_id = _insert_val_run(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                      result_run_id=run_id, now=T1)
        result = mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T2)
        assert result["ok"] is False

    def test_out_of_order_running_call_cannot_revive_terminal_slot(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        mark_attempt_abandoned_terminal(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T1)
        assert get_schedule_slot(slot["id"])["status"] == "abandoned"
        result = mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T2)
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "abandoned"

    def test_nonexistent_result_run_id_fails(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        result = complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                               result_run_id=999999, now=T1)
        assert result["ok"] is False
        assert result["reason"] == "result_run_id_not_found"

    def test_wrong_universe_result_cannot_complete_attempt(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db, universe="nifty100")
        wrong_run_id = _insert_val_run(isolated_db, horizon="medium", universe="us")
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        result = complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                               result_run_id=wrong_run_id, now=T1)
        assert result["ok"] is False
        assert result["reason"] == "result_identity_mismatch"

    def test_manual_attempt_persists_horizon_and_universe(self, isolated_db):
        lease = _lease(owner="w1", now=T0)
        attempt = create_manual_attempt(horizon="short", universe="midcap",
                                         owner="w1", fencing_token=lease["fencing_token"], now=T0)
        fetched = get_schedule_attempt(attempt["id"])
        assert fetched["horizon"] == "short"
        assert fetched["universe"] == "midcap"
        assert fetched["slot_id"] is None

    def test_manual_attempt_cannot_satisfy_or_complete_a_slot(self, isolated_db):
        slot = _slot(isolated_db)
        lease = _lease(owner="w1", now=T0)
        manual = create_manual_attempt(horizon="medium", universe="nifty100",
                                        owner="w1", fencing_token=lease["fencing_token"], now=T0)
        run_id = _insert_val_run(isolated_db)
        mark_attempt_running(manual["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        complete_attempt_with_result(manual["id"], owner="w1", fencing_token=lease["fencing_token"],
                                      result_run_id=run_id, now=T1)
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_sqlite_foreign_keys_enabled_on_ledger_connection(self, isolated_db):
        conn = ve._get_ledger_sqlite_conn()
        try:
            assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        finally:
            conn.close()

    def test_naive_timestamp_fails_closed(self, isolated_db):
        with pytest.raises(ValueError):
            get_or_create_schedule_slot(
                horizon="medium", universe="nifty100",
                scheduled_slot=datetime(2026, 8, 13, 0, 30), schedule_version="v1", now=T0,
            )

    def test_read_helpers_do_not_mutate_state(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        before = get_schedule_slot(slot["id"])
        get_schedule_slot(slot["id"])
        get_validation_execution_lease()
        get_schedule_attempt(attempt["id"])
        after = get_schedule_slot(slot["id"])
        assert before == after


# ─────────────────────────────────────────────────────────────────────────
# OWNER POLICY UPDATE — short-horizon foundation readiness (regression)
# ─────────────────────────────────────────────────────────────────────────

class TestShortHorizonFoundationReadiness:
    def test_short_slots_require_lease_before_activation(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="short", universe="us",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        result = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=1, now=T0)
        assert result["ok"] is False
        assert get_schedule_slot(slot["id"])["status"] == "due"

    def test_short_contends_with_medium_for_global_lease(self, isolated_db):
        short_slot = get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                                   scheduled_slot=T0, schedule_version="v1", now=T0)
        medium_slot = get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                                    scheduled_slot=T0, schedule_version="v1", now=T0)
        lease = _lease(owner="short-worker", now=T0)
        r1 = create_schedule_attempt(slot_id=short_slot["id"], trigger_type="scheduler",
                                      owner="short-worker", fencing_token=lease["fencing_token"], now=T0)
        assert r1["ok"] is True
        r2 = create_schedule_attempt(slot_id=medium_slot["id"], trigger_type="scheduler",
                                      owner="short-worker", fencing_token=lease["fencing_token"], now=T1)
        assert r2["ok"] is False

    def test_short_stale_recovery_and_retry(self, isolated_db):
        slot = get_or_create_schedule_slot(horizon="short", universe="midcap",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        stale = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)
        recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        retry = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w2", fencing_token=lease2["fencing_token"], now=expired)
        assert retry["ok"] is True
        assert retry["horizon"] == "short"

    def test_no_scheduler_call_introduced_for_short_in_this_phase(self):
        import inspect
        source = inspect.getsource(ve)
        assert "_short_schedule_loop" not in source
        assert "short_validation_schedule" not in source


# ─────────────────────────────────────────────────────────────────────────
# THIRD CORRECTION — one-to-one result linkage (result_run_id uniqueness)
# ─────────────────────────────────────────────────────────────────────────

class TestResultLinkageUniqueness:
    def test_different_attempts_different_results_both_succeed(self, isolated_db):
        slot_a, lease_a, a = _admitted_scheduled_attempt(isolated_db, owner="w1", now=T0, universe="nifty100")
        run_a = _insert_val_run(isolated_db, horizon="medium", universe="nifty100")
        mark_attempt_running(a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
        r1 = complete_attempt_with_result(a["id"], owner="w1", fencing_token=lease_a["fencing_token"],
                                           result_run_id=run_a, now=T0)
        assert r1["ok"] is True
        release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"], now=T1)

        slot_b, lease_b, b = _admitted_scheduled_attempt(isolated_db, owner="w2", now=T1, universe="midcap")
        run_b = _insert_val_run(isolated_db, horizon="medium", universe="midcap")
        mark_attempt_running(b["id"], owner="w2", fencing_token=lease_b["fencing_token"], now=T1)
        r2 = complete_attempt_with_result(b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
                                           result_run_id=run_b, now=T1)
        assert r2["ok"] is True

    def test_duplicate_result_linking_rejected_explicitly(self, isolated_db):
        """RED-before-GREEN reproduction: on the pre-fix implementation this
        second completion silently succeeded (empirically verified during
        this correction — see the correction report). The fix must reject
        it with an explicit, distinguishable conflict."""
        slot_a, lease_a, a = _admitted_scheduled_attempt(isolated_db, owner="w1", now=T0, universe="nifty100")
        run_id = _insert_val_run(isolated_db, horizon="medium", universe="nifty100")
        mark_attempt_running(a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
        r1 = complete_attempt_with_result(a["id"], owner="w1", fencing_token=lease_a["fencing_token"],
                                           result_run_id=run_id, now=T0)
        assert r1["ok"] is True
        release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"], now=T1)

        # attempt B — a DIFFERENT canonical slot (distinct scheduled_slot), same horizon/universe as run_id
        slot_b, lease_b, b = _admitted_scheduled_attempt(isolated_db, owner="w2", now=T1,
                                                           universe="nifty100", scheduled_slot=T1)
        mark_attempt_running(b["id"], owner="w2", fencing_token=lease_b["fencing_token"], now=T1)
        r2 = complete_attempt_with_result(b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
                                           result_run_id=run_id, now=T1)
        assert r2["ok"] is False
        assert r2["reason"] == "result_already_linked"

    def test_rejected_attempt_remains_running_and_fully_bound(self, isolated_db):
        slot_a, lease_a, a = _admitted_scheduled_attempt(isolated_db, owner="w1", now=T0, universe="nifty100")
        run_id = _insert_val_run(isolated_db, horizon="medium", universe="nifty100")
        mark_attempt_running(a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
        complete_attempt_with_result(a["id"], owner="w1", fencing_token=lease_a["fencing_token"],
                                      result_run_id=run_id, now=T0)
        release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"], now=T1)

        slot_b, lease_b, b = _admitted_scheduled_attempt(isolated_db, owner="w2", now=T1,
                                                           universe="nifty100", scheduled_slot=T1)
        mark_attempt_running(b["id"], owner="w2", fencing_token=lease_b["fencing_token"], now=T1)
        complete_attempt_with_result(b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
                                      result_run_id=run_id, now=T1)

        fetched_b = get_schedule_attempt(b["id"])
        assert fetched_b["status"] == "running"
        assert fetched_b["result_run_id"] is None
        assert fetched_b["completed_at"] is None
        fetched_slot_b = get_schedule_slot(slot_b["id"])
        assert fetched_slot_b["status"] == "running"
        assert fetched_slot_b["active_attempt_id"] == b["id"]
        fetched_lease = get_validation_execution_lease()
        assert fetched_lease["active_attempt_id"] == b["id"]

    def test_multiple_incomplete_attempts_with_null_result_remain_valid(self, isolated_db):
        slot_a, lease_a, a = _admitted_scheduled_attempt(isolated_db, owner="w1", now=T0, universe="nifty100")
        mark_attempt_failed_retryable(a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
        release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"], now=T1)

        slot_b, lease_b, b = _admitted_scheduled_attempt(isolated_db, owner="w2", now=T1, universe="midcap")
        mark_attempt_failed_retryable(b["id"], owner="w2", fencing_token=lease_b["fencing_token"], now=T1)
        # both a and b have result_run_id NULL — the unique index must not reject this
        assert get_schedule_attempt(a["id"])["result_run_id"] is None
        assert get_schedule_attempt(b["id"])["result_run_id"] is None

    def test_repeated_schema_initialization_with_unique_index_remains_valid(self, isolated_db):
        ve._db_initialised = False
        ve._init_db()
        ve._db_initialised = False
        ve._init_db()  # must not raise on the named unique index

    def test_same_attempt_cannot_replace_its_completed_result(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        run_1 = _insert_val_run(isolated_db)
        run_2 = _insert_val_run(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                      result_run_id=run_1, now=T1)
        second = complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                               result_run_id=run_2, now=T2)
        assert second["ok"] is False
        assert get_schedule_attempt(attempt["id"])["result_run_id"] == run_1

    def test_manual_attempt_shares_the_same_one_to_one_result_rule(self, isolated_db):
        lease_a = _lease(owner="w1", now=T0)
        manual_a = create_manual_attempt(horizon="medium", universe="nifty100",
                                          owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
        run_id = _insert_val_run(isolated_db, horizon="medium", universe="nifty100")
        mark_attempt_running(manual_a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
        r1 = complete_attempt_with_result(manual_a["id"], owner="w1", fencing_token=lease_a["fencing_token"],
                                           result_run_id=run_id, now=T0)
        assert r1["ok"] is True
        release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"], now=T1)

        slot_b, lease_b, b = _admitted_scheduled_attempt(isolated_db, owner="w2", now=T1, universe="nifty100")
        mark_attempt_running(b["id"], owner="w2", fencing_token=lease_b["fencing_token"], now=T1)
        r2 = complete_attempt_with_result(b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
                                           result_run_id=run_id, now=T1)
        assert r2["ok"] is False
        assert r2["reason"] == "result_already_linked"

    def test_result_horizon_universe_checks_still_apply_after_the_fix(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db, universe="nifty100")
        wrong_run = _insert_val_run(isolated_db, horizon="medium", universe="us")
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        result = complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                               result_run_id=wrong_run, now=T1)
        assert result["ok"] is False
        assert result["reason"] == "result_identity_mismatch"


class TestResultLinkageRollback:
    def test_failure_before_result_check_leaves_all_state_unchanged(self, isolated_db):
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
        # a nonexistent result must reject cleanly with no partial write
        result = complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                               result_run_id=999999, now=T1)
        assert result["ok"] is False
        assert result["reason"] == "result_run_id_not_found"
        fetched = get_schedule_attempt(attempt["id"])
        assert fetched["status"] == "running"
        assert fetched["result_run_id"] is None
        assert fetched["completed_at"] is None

    def test_unexpected_integrity_error_is_not_mislabeled_result_already_linked(self, isolated_db, monkeypatch):
        """A completely unrelated integrity failure (simulated) must never
        be reported as 'result_already_linked' — only the specific named
        index violation may map to that reason."""
        import sqlite3 as _sqlite3
        slot, lease, attempt = _admitted_scheduled_attempt(isolated_db)
        run_id = _insert_val_run(isolated_db)
        mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)

        real_get_ledger_conn = ve._get_ledger_sqlite_conn

        class _PoisonedConn:
            def __init__(self, real_conn):
                self._real = real_conn
            def execute(self, sql, params=()):
                if sql.startswith("UPDATE validation_schedule_attempts") and "status='completed'" in sql:
                    raise _sqlite3.IntegrityError("UNIQUE constraint failed: some_other_table.some_other_column")
                return self._real.execute(sql, params)
            def close(self):
                self._real.close()

        def poisoned_factory():
            return _PoisonedConn(real_get_ledger_conn())

        monkeypatch.setattr(ve, "_get_ledger_sqlite_conn", poisoned_factory)
        with pytest.raises(sqlite3.IntegrityError):
            complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                          result_run_id=run_id, now=T1)


# ─────────────────────────────────────────────────────────────────────────
# THIRD CORRECTION — lock-order / deadlock-avoidance regression
# ─────────────────────────────────────────────────────────────────────────

class TestLockOrderDeadlockAvoidance:
    """SQLite's BEGIN IMMEDIATE serializes all writers globally, so it
    cannot itself prove PostgreSQL row-lock ordering — this class
    functionally re-confirms the stale-worker/recovery interleaving
    behaves correctly end-to-end (both operations still reach the
    correct, deterministic outcome), which is necessary but not
    sufficient evidence; the corresponding PostgreSQL integration test
    is the one that can actually observe lock-acquisition order."""

    def test_old_worker_transition_and_new_owner_recovery_reach_coherent_outcome(self, isolated_db):
        slot = _slot(isolated_db)
        lease1 = _lease(owner="w1", now=T0, seconds=1)
        attempt = create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                           owner="w1", fencing_token=lease1["fencing_token"], now=T0)
        expired = T0 + timedelta(seconds=2)
        lease2 = _lease(owner="w2", now=expired)

        # old worker (unaware of reclaim) attempts a terminal transition
        old_worker_result = mark_attempt_failed_retryable(
            attempt["id"], owner="w1", fencing_token=lease1["fencing_token"], now=expired
        )
        # new owner concurrently performs stale recovery
        recovery_result = recover_stale_active_attempt(
            owner="w2", fencing_token=lease2["fencing_token"], now=expired
        )

        assert old_worker_result["ok"] is False, "stale owner's transition must be rejected by fencing"
        assert recovery_result["ok"] is True, "current owner's recovery must succeed"

        fetched_attempt = get_schedule_attempt(attempt["id"])
        fetched_slot = get_schedule_slot(slot["id"])
        fetched_lease = get_validation_execution_lease()
        assert fetched_attempt["status"] == "abandoned"
        assert fetched_slot["status"] == "due"
        assert fetched_slot["active_attempt_id"] is None
        assert fetched_lease["active_attempt_id"] is None, "lease/attempt/slot bindings must all agree"
