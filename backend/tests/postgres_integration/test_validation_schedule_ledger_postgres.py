"""
V-SCHED1B — real-PostgreSQL behavioral proof for the durable validation
scheduling ledger's dialect-specific concerns that SQLite's single-process
tests cannot fully exercise: genuine concurrent-connection contention on
global-lease admission and slot claiming (SELECT ... FOR UPDATE), atomic
UPDATE...WHERE...RETURNING, ON CONFLICT DO NOTHING slot dedup, and
CHECK/FK-constraint enforcement under real PostgreSQL.

Requires POSTGRES_INTEGRATION_TEST_DATABASE_URL — see conftest.py for the
production-isolation guard. NOT EXECUTED as part of this phase's own
verification — no local PostgreSQL instance was available in this
environment (no pg_isready/postgres/initdb/docker found). Written to the
repository's existing convention so it runs under CI/whenever a
disposable Postgres instance is present. Whether GitHub CI actually
collects the `postgres_integration` marker was not independently
confirmed in this phase.
"""
import threading
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.postgres_integration

T0 = datetime(2026, 8, 13, 0, 30, tzinfo=timezone.utc)


def _reset_ledger_tables(pg_conn):
    pg_conn.execute("DELETE FROM validation_schedule_attempts")
    pg_conn.execute("DELETE FROM validation_schedule_slots")
    pg_conn.execute("DELETE FROM val_runs")
    pg_conn.execute(
        "UPDATE validation_execution_leases SET lease_owner=NULL, fencing_token=0, "
        "acquired_at=NULL, heartbeat_at=NULL, expires_at=NULL, active_attempt_id=NULL "
        "WHERE resource_key='validation-global'"
    )


def _insert_val_run(pg_conn, horizon="medium", universe="nifty100"):
    row = pg_conn.execute(
        "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
        "VALUES (now(), %s, 1, 0, '{}', %s) RETURNING id",
        (horizon, universe),
    ).fetchone()
    return row[0]


def test_schema_present_after_init_db(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    ve._db_initialised = False
    ve._init_db()
    tables = {
        r[0] for r in pg_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'"
        ).fetchall()
    }
    assert {"validation_schedule_slots", "validation_schedule_attempts", "validation_execution_leases"} <= tables
    cols = {
        r[0] for r in pg_conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='validation_execution_leases'"
        ).fetchall()
    }
    assert "active_attempt_id" in cols


def test_slot_on_conflict_dedup_under_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    first = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    second = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    assert first["id"] == second["id"]


def test_invalid_slot_status_rejected_by_check_constraint(pg_conn, pg_database_url):
    _reset_ledger_tables(pg_conn)
    pg_conn.execute(
        "INSERT INTO validation_schedule_slots "
        "(horizon, universe, scheduled_slot, schedule_version, status, created_at, updated_at) "
        "VALUES ('medium','nifty100', now(), 'v1', 'due', now(), now())"
    )
    with pytest.raises(Exception):
        pg_conn.execute(
            "UPDATE validation_schedule_slots SET status='not_a_real_status' "
            "WHERE horizon='medium' AND universe='nifty100'"
        )


def test_scheduled_attempt_without_lease_fails_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    result = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                         owner="w1", fencing_token=1, now=T0)
    assert result["ok"] is False
    fetched = pg_conn.execute(
        "SELECT status FROM validation_schedule_slots WHERE id=%s", (slot["id"],)
    ).fetchone()
    assert fetched[0] == "due", "slot must not activate without global-lease admission"


def test_concurrent_different_slot_admission_exactly_one_winner_real_postgres(pg_conn, pg_database_url):
    """The specific correction this file exists to prove under real
    PostgreSQL concurrency: two callers targeting two DIFFERENT slots,
    contending for the SAME global lease admission, must yield exactly
    one globally-admitted attempt — not two slots simultaneously running."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot_a = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    slot_b = ve.get_or_create_schedule_slot(
        horizon="long", universe="us", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)

    results = []
    barrier = threading.Barrier(2)

    def claim(slot_id):
        barrier.wait()
        results.append(ve.create_schedule_attempt(
            slot_id=slot_id, trigger_type="scheduler", owner="w1",
            fencing_token=lease["fencing_token"], now=T0,
        ))

    t1 = threading.Thread(target=claim, args=(slot_a["id"],))
    t2 = threading.Thread(target=claim, args=(slot_b["id"],))
    t1.start(); t2.start()
    t1.join(); t2.join()

    winners = [r for r in results if r["ok"]]
    assert len(winners) == 1, "exactly one attempt may be globally admitted, regardless of which slot it targets"


def test_scheduled_versus_manual_contention_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    scheduled = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                            owner="w1", fencing_token=lease["fencing_token"], now=T0)
    assert scheduled["ok"] is True
    manual = ve.create_manual_attempt(horizon="medium", universe="us",
                                       owner="w1", fencing_token=lease["fencing_token"], now=T0)
    assert manual["ok"] is False
    assert manual["reason"] == "active_attempt_already_bound"


def test_concurrent_retry_number_allocation_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    a1 = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                     owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.mark_attempt_running(a1["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.mark_attempt_failed_retryable(a1["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.release_validation_execution_lease(owner="w1", fencing_token=lease["fencing_token"], now=T0)

    results = []
    barrier = threading.Barrier(2)

    def retry(owner):
        barrier.wait()
        lease2 = ve.acquire_validation_execution_lease(owner=owner, now=T0 + timedelta(minutes=1),
                                                         lease_duration_seconds=600)
        if not lease2["ok"]:
            results.append(lease2)
            return
        results.append(ve.create_schedule_attempt(
            slot_id=slot["id"], trigger_type="scheduler", owner=owner,
            fencing_token=lease2["fencing_token"], now=T0 + timedelta(minutes=1),
        ))

    t1 = threading.Thread(target=retry, args=("w2",))
    t2 = threading.Thread(target=retry, args=("w3",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    winners = [r for r in results if r.get("ok")]
    assert len(winners) == 1
    if "attempt_number" in winners[0]:
        assert winners[0]["attempt_number"] == 2
    pg_conn.execute("SELECT 1").fetchone()  # connection remains usable — no aborted-transaction state left


def test_atomic_rollback_across_attempt_and_slot_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T0)

    real_compound = ve._compound_transition

    def poisoned(*args, **kwargs):
        kwargs = dict(kwargs)
        kwargs["new_slot_status_if_bound"] = "not_a_real_status"  # violates the CHECK constraint mid-transaction
        return real_compound(*args, **kwargs)

    ve._compound_transition = poisoned
    try:
        with pytest.raises(Exception):
            ve.mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
    finally:
        ve._compound_transition = real_compound

    fetched_attempt = pg_conn.execute(
        "SELECT status FROM validation_schedule_attempts WHERE id=%s", (attempt["id"],)
    ).fetchone()
    assert fetched_attempt[0] == "claimed", "attempt write must roll back with the failed slot write"
    fetched_lease = pg_conn.execute(
        "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
    ).fetchone()
    assert fetched_lease[0] == attempt["id"], "lease binding must be unaffected by the rolled-back transition"


def test_expiry_reclaim_with_stale_attempt_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease1 = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=1)
    stale = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                        owner="w1", fencing_token=lease1["fencing_token"], now=T0)
    expired = T0 + timedelta(seconds=2)
    lease2 = ve.acquire_validation_execution_lease(owner="w2", now=expired, lease_duration_seconds=600)
    assert lease2["fencing_token"] > lease1["fencing_token"]
    assert lease2["recovery_required"] is True
    assert lease2["stale_active_attempt_id"] == stale["id"]

    stale_hb = ve.heartbeat_validation_execution_lease(
        owner="w1", fencing_token=lease1["fencing_token"], now=expired, lease_duration_seconds=600
    )
    assert stale_hb["ok"] is False


def test_fenced_recovery_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease1 = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=1)
    stale = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                        owner="w1", fencing_token=lease1["fencing_token"], now=T0)
    expired = T0 + timedelta(seconds=2)
    lease2 = ve.acquire_validation_execution_lease(owner="w2", now=expired, lease_duration_seconds=600)

    result = ve.recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
    assert result["ok"] is True
    assert result["recovered_attempt_id"] == stale["id"]

    fetched_attempt = pg_conn.execute(
        "SELECT status, lease_owner FROM validation_schedule_attempts WHERE id=%s", (stale["id"],)
    ).fetchone()
    assert fetched_attempt[0] == "abandoned"
    assert fetched_attempt[1] == "w1", "historical claimer identity preserved, not overwritten by recovering owner"

    fetched_slot = pg_conn.execute(
        "SELECT status, active_attempt_id FROM validation_schedule_slots WHERE id=%s", (slot["id"],)
    ).fetchone()
    assert fetched_slot[0] == "due"
    assert fetched_slot[1] is None

    retry = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                        owner="w2", fencing_token=lease2["fencing_token"], now=expired)
    assert retry["ok"] is True
    assert retry["attempt_number"] == 2


def test_result_fk_enforcement_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)

    result = ve.complete_attempt_with_result(
        attempt["id"], owner="w1", fencing_token=lease["fencing_token"], result_run_id=99999999, now=T0
    )
    assert result["ok"] is False
    assert result["reason"] == "result_run_id_not_found"


def test_terminal_slot_protection_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    run_id = _insert_val_run(pg_conn, horizon="medium", universe="nifty100")
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.complete_attempt_with_result(attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
                                     result_run_id=run_id, now=T0)

    # w1's lease is still held and unexpired after completion — release it
    # explicitly (as the real lifecycle requires) before w2 can acquire it.
    release_result = ve.release_validation_execution_lease(
        owner="w1", fencing_token=lease["fencing_token"], now=T0 + timedelta(minutes=1)
    )
    assert release_result["ok"] is True

    lease2 = ve.acquire_validation_execution_lease(owner="w2", now=T0 + timedelta(minutes=1), lease_duration_seconds=600)
    assert lease2["ok"] is True
    result = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler", owner="w2",
                                         fencing_token=lease2["fencing_token"], now=T0 + timedelta(minutes=1))
    assert result["ok"] is False
    assert result["reason"] == "slot_not_claimable"


def test_retry_following_recovery_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease1 = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=1)
    stale = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                        owner="w1", fencing_token=lease1["fencing_token"], now=T0)
    expired = T0 + timedelta(seconds=2)
    lease2 = ve.acquire_validation_execution_lease(owner="w2", now=expired, lease_duration_seconds=600)
    ve.recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)

    retry = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                        owner="w2", fencing_token=lease2["fencing_token"], now=expired)
    assert retry["ok"] is True
    assert retry["attempt_number"] == 2

    fetched_stale = pg_conn.execute(
        "SELECT status FROM validation_schedule_attempts WHERE id=%s", (stale["id"],)
    ).fetchone()
    assert fetched_stale[0] == "abandoned", "historical abandoned attempt row remains intact after retry"


# ─────────────────────────────────────────────────────────────────────────
# THIRD CORRECTION — lock-order/deadlock avoidance, real PostgreSQL evidence
# ─────────────────────────────────────────────────────────────────────────

def test_stale_worker_and_recovery_no_deadlock_real_postgres(pg_conn, pg_database_url):
    """The specific interleaving the second independent review identified
    as a real AB-BA deadlock hazard before this correction: an old
    (stale-leased) worker attempts a terminal transition on its own
    attempt at the same moment a new owner (post-reclaim) performs stale
    recovery on that SAME attempt. Before the fix, _compound_transition
    locked attempt-then-lease while recover_stale_active_attempt locked
    lease-then-attempt — a classic deadlock cycle PostgreSQL's detector
    would abort with SQLSTATE 40P01. After the fix both lock lease-then-
    attempt-then-slot, so no cycle can form. Uses two independent
    connections and a barrier for genuine concurrency, not sleep-based
    synchronization, with a bounded overall test timeout so a real
    regression fails fast in CI rather than hanging."""
    import queue
    import psycopg
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease1 = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=1)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease1["fencing_token"], now=T0)
    expired = T0 + timedelta(seconds=2)
    lease2 = ve.acquire_validation_execution_lease(owner="w2", now=expired, lease_duration_seconds=600)
    assert lease2["recovery_required"] is True

    results = queue.Queue()
    barrier = threading.Barrier(2)

    def old_worker():
        barrier.wait()
        try:
            r = ve.mark_attempt_failed_retryable(attempt["id"], owner="w1",
                                                  fencing_token=lease1["fencing_token"], now=expired)
            results.put(("old_worker", r, None))
        except Exception as e:
            results.put(("old_worker", None, e))

    def new_owner():
        barrier.wait()
        try:
            r = ve.recover_stale_active_attempt(owner="w2", fencing_token=lease2["fencing_token"], now=expired)
            results.put(("new_owner", r, None))
        except Exception as e:
            results.put(("new_owner", None, e))

    t1 = threading.Thread(target=old_worker)
    t2 = threading.Thread(target=new_owner)
    t1.start(); t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert not t1.is_alive() and not t2.is_alive(), "operation hung — possible undetected lock contention"

    collected = {}
    while not results.empty():
        name, r, err = results.get()
        collected[name] = (r, err)

    for name, (r, err) in collected.items():
        assert err is None, f"{name} raised an exception (deadlock or otherwise): {err!r}"
        if isinstance(err, psycopg.errors.DeadlockDetected):
            pytest.fail(f"{name} hit a real PostgreSQL deadlock — lock ordering regression")

    # old worker's stale transition must lose to fencing; new owner's recovery must win
    assert collected["old_worker"][0]["ok"] is False
    assert collected["new_owner"][0]["ok"] is True

    fetched_attempt = pg_conn.execute(
        "SELECT status FROM validation_schedule_attempts WHERE id=%s", (attempt["id"],)
    ).fetchone()
    fetched_slot = pg_conn.execute(
        "SELECT status, active_attempt_id FROM validation_schedule_slots WHERE id=%s", (slot["id"],)
    ).fetchone()
    fetched_lease = pg_conn.execute(
        "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
    ).fetchone()
    assert fetched_attempt[0] == "abandoned"
    assert fetched_slot[0] == "due"
    assert fetched_slot[1] is None
    assert fetched_lease[0] is None, "lease, attempt and slot bindings must all agree after the race resolves"


# ─────────────────────────────────────────────────────────────────────────
# THIRD CORRECTION — one-to-one result linkage, real PostgreSQL evidence
# ─────────────────────────────────────────────────────────────────────────

def test_named_unique_index_present_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    ve._db_initialised = False
    ve._init_db()
    row = pg_conn.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE tablename='validation_schedule_attempts' AND indexname='idx_vsa_result_unique'"
    ).fetchone()
    assert row is not None
    assert "UNIQUE" in row[1].upper()


def test_repeated_schema_init_with_unique_index_idempotent_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    ve._db_initialised = False
    ve._init_db()
    ve._db_initialised = False
    ve._init_db()  # must not raise


def test_duplicate_result_link_rejected_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot_a = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    run_id = _insert_val_run(pg_conn, horizon="medium", universe="nifty100")
    lease_a = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    a = ve.create_schedule_attempt(slot_id=slot_a["id"], trigger_type="scheduler",
                                    owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
    ve.mark_attempt_running(a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
    r1 = ve.complete_attempt_with_result(a["id"], owner="w1", fencing_token=lease_a["fencing_token"],
                                          result_run_id=run_id, now=T0)
    assert r1["ok"] is True
    ve.release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"], now=T0 + timedelta(minutes=1))

    slot_b = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0 + timedelta(minutes=1),
        schedule_version="v1", now=T0 + timedelta(minutes=1)
    )
    lease_b = ve.acquire_validation_execution_lease(owner="w2", now=T0 + timedelta(minutes=1), lease_duration_seconds=600)
    b = ve.create_schedule_attempt(slot_id=slot_b["id"], trigger_type="scheduler", owner="w2",
                                    fencing_token=lease_b["fencing_token"], now=T0 + timedelta(minutes=1))
    ve.mark_attempt_running(b["id"], owner="w2", fencing_token=lease_b["fencing_token"], now=T0 + timedelta(minutes=1))
    r2 = ve.complete_attempt_with_result(b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
                                          result_run_id=run_id, now=T0 + timedelta(minutes=1))
    assert r2["ok"] is False
    assert r2["reason"] == "result_already_linked"

    fetched_b = pg_conn.execute(
        "SELECT status, result_run_id FROM validation_schedule_attempts WHERE id=%s", (b["id"],)
    ).fetchone()
    assert fetched_b[0] == "running"
    assert fetched_b[1] is None


def test_multiple_null_result_links_allowed_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot_a = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease_a = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    a = ve.create_schedule_attempt(slot_id=slot_a["id"], trigger_type="scheduler",
                                    owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
    ve.mark_attempt_failed_retryable(a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
    ve.release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"], now=T0 + timedelta(minutes=1))

    slot_b = ve.get_or_create_schedule_slot(
        horizon="medium", universe="midcap", scheduled_slot=T0 + timedelta(minutes=1),
        schedule_version="v1", now=T0 + timedelta(minutes=1)
    )
    lease_b = ve.acquire_validation_execution_lease(owner="w2", now=T0 + timedelta(minutes=1), lease_duration_seconds=600)
    b = ve.create_schedule_attempt(slot_id=slot_b["id"], trigger_type="scheduler", owner="w2",
                                    fencing_token=lease_b["fencing_token"], now=T0 + timedelta(minutes=1))
    ve.mark_attempt_failed_retryable(b["id"], owner="w2", fencing_token=lease_b["fencing_token"], now=T0 + timedelta(minutes=1))

    count_null = pg_conn.execute(
        "SELECT COUNT(*) FROM validation_schedule_attempts WHERE result_run_id IS NULL"
    ).fetchone()[0]
    assert count_null >= 2, "unique index must permit multiple NULL result_run_id rows"


# ─────────────────────────────────────────────────────────────────────────
# V-SCHED1C1-C2 — real PostgreSQL evidence for
# complete_running_attempt_with_computed_result(), the single atomic
# primitive that turns a COMPUTED (not-yet-persisted) validation result
# into a durable, linked, completed attempt. Unlike complete_attempt_with_
# result() above (which links an ALREADY-persisted val_runs row), this
# primitive performs the val_runs/val_signals INSERT itself, inside the
# same transaction as the fencing re-check — the correction for the
# Critical defect where a stale/fenced-out worker could otherwise create a
# permanently orphaned, publicly-visible result row.
# ─────────────────────────────────────────────────────────────────────────

def _signal_row(symbol="SYM", horizon="medium"):
    return (symbol, horizon, "2026-08-01", 1.0, 1.0, 1.0, 1.0, 1.0,
            "up", 1.0, None, None, "up", 1)


def _val_runs_count(pg_conn):
    return pg_conn.execute("SELECT COUNT(*) FROM val_runs").fetchone()[0]


def _val_signals_count(pg_conn):
    return pg_conn.execute("SELECT COUNT(*) FROM val_signals").fetchone()[0]


def _latest_val_run_id(pg_conn, horizon="medium", universe="nifty100"):
    row = pg_conn.execute(
        "SELECT id FROM val_runs WHERE horizon=%s AND universe=%s ORDER BY id DESC LIMIT 1",
        (horizon, universe),
    ).fetchone()
    return row[0] if row else None


def test_atomic_completion_inserts_run_signals_links_and_clears_bindings_real_postgres(pg_conn, pg_database_url):
    """(A) One successful atomic completion, verified against every
    documented side effect: exactly one val_runs row, its val_signals,
    result_run_id linkage, attempt/slot completion, and cleared bindings —
    all in a single real PostgreSQL transaction."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)

    completion = ve.complete_running_attempt_with_computed_result(
        attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
        horizon="medium", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row()],
        now=T0 + timedelta(seconds=30),
    )
    assert completion["ok"] is True
    run_id = completion["run_id"]

    assert _val_runs_count(pg_conn) == 1
    signals = pg_conn.execute("SELECT symbol, run_id FROM val_signals").fetchall()
    assert len(signals) == 1
    assert signals[0][0] == "SYM"
    assert signals[0][1] == run_id

    fetched_attempt = pg_conn.execute(
        "SELECT status, result_run_id FROM validation_schedule_attempts WHERE id=%s", (attempt["id"],)
    ).fetchone()
    assert fetched_attempt[0] == "completed"
    assert fetched_attempt[1] == run_id

    fetched_slot = pg_conn.execute(
        "SELECT status, active_attempt_id FROM validation_schedule_slots WHERE id=%s", (slot["id"],)
    ).fetchone()
    assert fetched_slot[0] == "completed"
    assert fetched_slot[1] is None

    fetched_lease = pg_conn.execute(
        "SELECT active_attempt_id, lease_owner, fencing_token FROM validation_execution_leases "
        "WHERE resource_key='validation-global'"
    ).fetchone()
    assert fetched_lease[0] is None
    # owner/fencing history preserved on the row itself (not wiped) — only
    # the active-attempt binding is cleared, matching complete_attempt_
    # with_result's own documented behavior.
    assert fetched_lease[1] == "w1"
    assert fetched_lease[2] == lease["fencing_token"]


def test_stale_worker_after_reclaim_persists_nothing_real_postgres(pg_conn, pg_database_url):
    """(B) Two genuine connections/workers. A finishes computing but has
    not yet persisted; A's lease is then genuinely reclaimed by B with a
    newer fencing token; A invokes the atomic primitive with its now-stale
    token. A must be rejected, create nothing, and B's binding must remain
    untouched — the public latest-result selection must be unaffected."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    pre_existing = _insert_val_run(pg_conn, horizon="medium", universe="nifty100")

    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    # A admits with a short lease so its expiry is genuine and controlled
    # by timestamp, not a sleep.
    lease_a = ve.acquire_validation_execution_lease(owner="worker-a", now=T0, lease_duration_seconds=1)
    attempt_a = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                            owner="worker-a", fencing_token=lease_a["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"], now=T0)

    # B genuinely reclaims after real (controlled-timestamp) expiry —
    # admit_validation_attempt's own recovery path resolves A's stale
    # binding as part of B's admission, exactly like production.
    reclaim_time = T0 + timedelta(seconds=2)
    admitted_b = ve.admit_validation_attempt(
        horizon="medium", universe="midcap", trigger_type="scheduler", owner="worker-b",
        scheduled_slot=T0, now=reclaim_time, lease_duration_seconds=600,
    )
    assert admitted_b["ok"] is True
    assert ve.get_schedule_attempt(attempt_a["id"])["status"] == "abandoned"

    # A "wakes up" and attempts atomic persistence with its stale token.
    completion_a = ve.complete_running_attempt_with_computed_result(
        attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"],
        horizon="medium", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row()],
        now=reclaim_time + timedelta(seconds=1),
    )
    assert completion_a["ok"] is False
    assert completion_a["reason"] in ("not_owner_or_expired_lease", "attempt_not_found", "illegal_transition")

    assert _val_runs_count(pg_conn) == 1  # only the pre-existing row
    assert _val_signals_count(pg_conn) == 0
    assert _latest_val_run_id(pg_conn) == pre_existing

    fetched_lease = pg_conn.execute(
        "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
    ).fetchone()
    assert fetched_lease[0] == admitted_b["attempt_id"], "A must not have clobbered B's binding"


def _setup_race_fixture(pg_conn):
    """Shared setup for all three completion-vs-reclaim tests below: a
    running attempt A bound to a 1-second lease. A's own completion call
    must use a `now` still inside that 1-second window (e.g. T0 + 0.5s) —
    genuinely valid at the instant it's evaluated, not already-expired
    regardless of interleaving. B's reclaim call must use a `now` past the
    expiry (e.g. T0 + 2s) — genuinely eligible to reclaim once it acquires
    the lease row's lock, regardless of whether A has already completed
    (acquire_validation_execution_lease's CAS never touches expires_at
    itself, so B's own later `now` remains valid for reclaim whether it
    runs before or after A)."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease_a = ve.acquire_validation_execution_lease(owner="worker-a", now=T0, lease_duration_seconds=1)
    attempt_a = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                            owner="worker-a", fencing_token=lease_a["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"], now=T0)
    return ve, slot, lease_a, attempt_a


def test_concurrent_completion_versus_reclaim_genuinely_order_dependent_real_postgres(pg_conn, pg_database_url):
    """(C) A's atomic completion (using a `now` still inside its own
    lease's validity window) and B's valid reclaim (using a `now` past
    that same lease's expiry) race on separate real connections/threads,
    released simultaneously via a barrier. UNLIKE the prior version of
    this test, A's `now` is NOT already past its own lease's expiry —
    whichever side's row-locking operation (A's SELECT...FOR UPDATE vs
    B's UPDATE) genuinely acquires the lease row's lock first determines
    which of the two REACHABLE outcomes occurs; the outcome is not
    predetermined by the timestamps alone, only bounded to two safe
    possibilities by them. Both outcomes are asserted with full database
    state, not merely "no exception was thrown". Bounded join timeout;
    explicit failure on DeadlockDetected."""
    import queue
    import psycopg
    ve, slot, lease_a, attempt_a = _setup_race_fixture(pg_conn)

    a_now = T0 + timedelta(seconds=0, milliseconds=500)   # still inside lease_a's 1s validity
    b_now = T0 + timedelta(seconds=2)                      # genuinely past lease_a's expiry
    results = queue.Queue()
    barrier = threading.Barrier(2)

    def complete_a():
        barrier.wait()
        try:
            r = ve.complete_running_attempt_with_computed_result(
                attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"],
                horizon="medium", universe="nifty100", run_at=T0.isoformat(),
                n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row()],
                now=a_now,
            )
            results.put(("complete_a", r, None))
        except Exception as e:
            results.put(("complete_a", None, e))

    def reclaim_b():
        barrier.wait()
        try:
            r = ve.admit_validation_attempt(
                horizon="medium", universe="midcap", trigger_type="scheduler", owner="worker-b",
                scheduled_slot=T0, now=b_now, lease_duration_seconds=600,
            )
            results.put(("reclaim_b", r, None))
        except Exception as e:
            results.put(("reclaim_b", None, e))

    t1 = threading.Thread(target=complete_a)
    t2 = threading.Thread(target=reclaim_b)
    t1.start(); t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert not t1.is_alive() and not t2.is_alive(), "operation hung — possible undetected lock contention"

    collected = {}
    while not results.empty():
        name, r, err = results.get()
        collected[name] = (r, err)

    for name, (r, err) in collected.items():
        if isinstance(err, psycopg.errors.DeadlockDetected):
            pytest.fail(f"{name} hit a real PostgreSQL deadlock — lock ordering regression")
        assert err is None, f"{name} raised an unexpected exception: {err!r}"

    a_result, b_result = collected["complete_a"][0], collected["reclaim_b"][0]
    assert _val_runs_count(pg_conn) <= 1, "no orphan/duplicate val_runs row may appear from this race"

    if a_result["ok"]:
        # Outcome 1 — A genuinely acquired the lease row's lock first,
        # completed while still legitimately valid, and B's reclaim
        # (necessarily observed AFTER A's commit released the lock)
        # legitimately took over the now-unbound lease afterward.
        assert b_result["ok"] is True, (
            "if A won, B's reclaim must still succeed afterward — B's own "
            "now is unconditionally past the lease's fixed expiry"
        )
        run_id = a_result["run_id"]
        assert _val_runs_count(pg_conn) == 1
        assert _val_signals_count(pg_conn) == 1
        assert _latest_val_run_id(pg_conn) == run_id
        fetched_attempt = pg_conn.execute(
            "SELECT status, result_run_id FROM validation_schedule_attempts WHERE id=%s", (attempt_a["id"],)
        ).fetchone()
        assert fetched_attempt == ("completed", run_id), "B must not have corrupted A's completed attempt"
        fetched_slot = pg_conn.execute(
            "SELECT status FROM validation_schedule_slots WHERE id=%s", (slot["id"],)
        ).fetchone()
        assert fetched_slot[0] == "completed"
        fetched_lease = pg_conn.execute(
            "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
        ).fetchone()
        assert fetched_lease[0] == b_result["attempt_id"], "B's reclaim must be the current binding, not A's"
    else:
        # Outcome 2 — B's reclaim genuinely acquired the lock first,
        # superseding A's fencing token before A's completion could run;
        # A must be rejected and create nothing.
        assert b_result["ok"] is True
        assert a_result.get("reason") in (
            "not_owner_or_expired_lease", "attempt_not_found", "illegal_transition",
            "not_active_attempt_for_slot", "result_identity_mismatch",
        )
        assert _val_runs_count(pg_conn) == 0
        assert _val_signals_count(pg_conn) == 0
        assert ve.get_schedule_attempt(attempt_a["id"])["status"] == "abandoned"
        fetched_lease = pg_conn.execute(
            "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
        ).fetchone()
        assert fetched_lease[0] == b_result["attempt_id"], "A must not have cleared or corrupted B's binding"


def test_completion_wins_when_lock_acquired_before_reclaim_real_postgres(pg_conn, pg_database_url):
    """Deterministic control for Outcome 1 (Stage 4) — A's completion is
    called and fully committed BEFORE B's reclaim is even attempted,
    forcing (not merely permitting) the "A wins" interleaving so this
    branch is proven on every CI run, not left to thread-scheduling luck
    in the genuinely-racing test above."""
    ve, slot, lease_a, attempt_a = _setup_race_fixture(pg_conn)
    a_now = T0 + timedelta(milliseconds=500)
    b_now = T0 + timedelta(seconds=2)

    completion = ve.complete_running_attempt_with_computed_result(
        attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"],
        horizon="medium", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row()],
        now=a_now,
    )
    assert completion["ok"] is True
    run_id = completion["run_id"]

    reclaim = ve.admit_validation_attempt(
        horizon="medium", universe="midcap", trigger_type="scheduler", owner="worker-b",
        scheduled_slot=T0, now=b_now, lease_duration_seconds=600,
    )
    assert reclaim["ok"] is True

    assert _val_runs_count(pg_conn) == 1
    assert _val_signals_count(pg_conn) == 1
    assert _latest_val_run_id(pg_conn) == run_id
    fetched_attempt = pg_conn.execute(
        "SELECT status, result_run_id FROM validation_schedule_attempts WHERE id=%s", (attempt_a["id"],)
    ).fetchone()
    assert fetched_attempt == ("completed", run_id)
    fetched_lease = pg_conn.execute(
        "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
    ).fetchone()
    assert fetched_lease[0] == reclaim["attempt_id"]


def test_reclaim_wins_when_lock_acquired_before_completion_real_postgres(pg_conn, pg_database_url):
    """Deterministic control for Outcome 2 (Stage 4) — B's reclaim is
    called and fully committed BEFORE A's (now-stale) completion is even
    attempted, forcing the "B wins" interleaving so this branch is proven
    on every CI run, not left to thread-scheduling luck."""
    ve, slot, lease_a, attempt_a = _setup_race_fixture(pg_conn)
    a_now = T0 + timedelta(milliseconds=500)
    b_now = T0 + timedelta(seconds=2)

    reclaim = ve.admit_validation_attempt(
        horizon="medium", universe="midcap", trigger_type="scheduler", owner="worker-b",
        scheduled_slot=T0, now=b_now, lease_duration_seconds=600,
    )
    assert reclaim["ok"] is True
    assert ve.get_schedule_attempt(attempt_a["id"])["status"] == "abandoned"

    completion = ve.complete_running_attempt_with_computed_result(
        attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"],
        horizon="medium", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row()],
        now=a_now,
    )
    assert completion["ok"] is False
    assert completion["reason"] in (
        "not_owner_or_expired_lease", "attempt_not_found", "illegal_transition",
        "not_active_attempt_for_slot", "result_identity_mismatch",
    )
    assert _val_runs_count(pg_conn) == 0
    assert _val_signals_count(pg_conn) == 0
    fetched_lease = pg_conn.execute(
        "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
    ).fetchone()
    assert fetched_lease[0] == reclaim["attempt_id"], "A must not have cleared or corrupted B's binding"


def test_mid_transaction_failure_rolls_back_everything_real_postgres(pg_conn, pg_database_url):
    """(D) A genuine PostgreSQL constraint violation (NOT NULL on
    val_signals.symbol — production's real constraint, not weakened for
    this test) forced partway through signal insertion, after the val_runs
    insert has already happened inside the same transaction. Confirms the
    whole transaction — run, signals, attempt, slot, lease — rolls back
    together, and that the connection/module remain usable for a
    subsequent legitimate completion."""
    import psycopg
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)

    bad_rows = [_signal_row(symbol=None)]  # NOT NULL violation, genuine constraint

    with pytest.raises(psycopg.errors.NotNullViolation):
        ve.complete_running_attempt_with_computed_result(
            attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
            horizon="medium", universe="nifty100", run_at=T0.isoformat(),
            n_stocks=1, n_signals=1, summary_json="{}", signal_rows=bad_rows,
            now=T0 + timedelta(seconds=30),
        )

    assert _val_runs_count(pg_conn) == 0
    assert _val_signals_count(pg_conn) == 0
    fetched_attempt = pg_conn.execute(
        "SELECT status, result_run_id FROM validation_schedule_attempts WHERE id=%s", (attempt["id"],)
    ).fetchone()
    assert fetched_attempt[0] == "running"
    assert fetched_attempt[1] is None
    fetched_slot = pg_conn.execute(
        "SELECT status, active_attempt_id FROM validation_schedule_slots WHERE id=%s", (slot["id"],)
    ).fetchone()
    assert fetched_slot[0] == "running"
    assert fetched_slot[1] == attempt["id"]
    fetched_lease = pg_conn.execute(
        "SELECT active_attempt_id FROM validation_execution_leases WHERE resource_key='validation-global'"
    ).fetchone()
    assert fetched_lease[0] == attempt["id"]

    # connection/module still usable — a subsequent legitimate completion
    # (this time with a valid signal row) succeeds normally.
    retry = ve.complete_running_attempt_with_computed_result(
        attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
        horizon="medium", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row()],
        now=T0 + timedelta(seconds=31),
    )
    assert retry["ok"] is True
    assert _val_runs_count(pg_conn) == 1
    assert _val_signals_count(pg_conn) == 1


def test_atomic_primitive_respects_result_uniqueness_real_postgres(pg_conn, pg_database_url):
    """(E) The atomic primitive creates a fresh val_runs row per call, so
    the one-to-one result_run_id constraint can never fire against it in
    normal operation (each linkage targets a brand-new, never-before-
    linked run id) — this test instead confirms an UNRELATED integrity
    failure (the NOT NULL violation from test D) is never misclassified
    as 'result_already_linked', and that a genuine duplicate-link attempt
    via the pre-existing complete_attempt_with_result() path is still
    correctly rejected against a run the atomic primitive created."""
    import psycopg
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot_a = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0, schedule_version="v1", now=T0
    )
    lease_a = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    attempt_a = ve.create_schedule_attempt(slot_id=slot_a["id"], trigger_type="scheduler",
                                            owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt_a["id"], owner="w1", fencing_token=lease_a["fencing_token"], now=T0)
    completion = ve.complete_running_attempt_with_computed_result(
        attempt_a["id"], owner="w1", fencing_token=lease_a["fencing_token"],
        horizon="medium", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row()],
        now=T0 + timedelta(seconds=30),
    )
    assert completion["ok"] is True
    run_id = completion["run_id"]
    ve.release_validation_execution_lease(owner="w1", fencing_token=lease_a["fencing_token"],
                                           now=T0 + timedelta(minutes=1))

    # Genuine NOT NULL violation raised by a DIFFERENT (fresh) attempt must
    # still surface as the real exception, never silently reclassified as
    # 'result_already_linked'.
    slot_b = ve.get_or_create_schedule_slot(
        horizon="medium", universe="midcap", scheduled_slot=T0 + timedelta(minutes=1),
        schedule_version="v1", now=T0 + timedelta(minutes=1),
    )
    lease_b = ve.acquire_validation_execution_lease(owner="w2", now=T0 + timedelta(minutes=1), lease_duration_seconds=600)
    attempt_b = ve.create_schedule_attempt(slot_id=slot_b["id"], trigger_type="scheduler", owner="w2",
                                            fencing_token=lease_b["fencing_token"], now=T0 + timedelta(minutes=1))
    ve.mark_attempt_running(attempt_b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
                             now=T0 + timedelta(minutes=1))
    with pytest.raises(psycopg.errors.NotNullViolation):
        ve.complete_running_attempt_with_computed_result(
            attempt_b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
            horizon="medium", universe="midcap", run_at=T0.isoformat(),
            n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row(symbol=None)],
            now=T0 + timedelta(minutes=1, seconds=1),
        )
    assert ve.get_schedule_attempt(attempt_b["id"])["status"] == "running"

    # A genuine duplicate-link attempt against the run the atomic primitive
    # already created (via the pre-existing linkage-only primitive) is
    # still correctly rejected as 'result_already_linked', not silently
    # accepted or misclassified.
    ve.mark_attempt_failed_retryable(attempt_b["id"], owner="w2", fencing_token=lease_b["fencing_token"],
                                      now=T0 + timedelta(minutes=1, seconds=2))
    slot_c = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0 + timedelta(minutes=2),
        schedule_version="v1", now=T0 + timedelta(minutes=2),
    )
    lease_c = ve.acquire_validation_execution_lease(owner="w3", now=T0 + timedelta(minutes=2), lease_duration_seconds=600)
    attempt_c = ve.create_schedule_attempt(slot_id=slot_c["id"], trigger_type="scheduler", owner="w3",
                                            fencing_token=lease_c["fencing_token"], now=T0 + timedelta(minutes=2))
    ve.mark_attempt_running(attempt_c["id"], owner="w3", fencing_token=lease_c["fencing_token"],
                             now=T0 + timedelta(minutes=2))
    dup = ve.complete_attempt_with_result(attempt_c["id"], owner="w3", fencing_token=lease_c["fencing_token"],
                                           result_run_id=run_id, now=T0 + timedelta(minutes=2, seconds=1))
    assert dup["ok"] is False
    assert dup["reason"] == "result_already_linked"
