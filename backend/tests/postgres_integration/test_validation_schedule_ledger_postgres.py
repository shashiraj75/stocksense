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
    # mark_attempt_failed_retryable() clears the lease's active_attempt_id
    # binding but does NOT release ownership itself — the lease row is
    # still owned by "w2" and still unexpired (600s duration). Without an
    # explicit release here, w3's acquisition below correctly returns
    # {"ok": False, "reason": "already_leased"} (a real, correct rejection,
    # not a bug) and indexing ["fencing_token"] on that result is the
    # actual defect this correction fixes — not a production issue.
    released_b = ve.release_validation_execution_lease(
        owner="w2", fencing_token=lease_b["fencing_token"], now=T0 + timedelta(minutes=1, seconds=3),
    )
    assert released_b["ok"] is True
    slot_c = ve.get_or_create_schedule_slot(
        horizon="medium", universe="nifty100", scheduled_slot=T0 + timedelta(minutes=2),
        schedule_version="v1", now=T0 + timedelta(minutes=2),
    )
    lease_c = ve.acquire_validation_execution_lease(owner="w3", now=T0 + timedelta(minutes=2), lease_duration_seconds=600)
    assert lease_c["ok"] is True
    attempt_c = ve.create_schedule_attempt(slot_id=slot_c["id"], trigger_type="scheduler", owner="w3",
                                            fencing_token=lease_c["fencing_token"], now=T0 + timedelta(minutes=2))
    ve.mark_attempt_running(attempt_c["id"], owner="w3", fencing_token=lease_c["fencing_token"],
                             now=T0 + timedelta(minutes=2))
    dup = ve.complete_attempt_with_result(attempt_c["id"], owner="w3", fencing_token=lease_c["fencing_token"],
                                           result_run_id=run_id, now=T0 + timedelta(minutes=2, seconds=1))
    assert dup["ok"] is False
    assert dup["reason"] == "result_already_linked"


# ─────────────────────────────────────────────────────────────────────────
# V-SCHED1C2B — automatic short-horizon scheduler foundation. No new
# ledger primitives were added for short — horizon is just a string field
# already flowing unmodified through every existing primitive — so these
# tests prove the already-proven mechanisms (uniqueness, global lease,
# concurrency, stale recovery, atomic completion, no-orphan) behave
# IDENTICALLY for horizon="short" as they already do for medium/long,
# real PostgreSQL evidence included per the RED matrix items 47-54.
# ─────────────────────────────────────────────────────────────────────────

def test_repeated_canonical_short_slot_creation_is_idempotent_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    first = ve.get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                            scheduled_slot=T0, schedule_version="v1", now=T0)
    second = ve.get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                             scheduled_slot=T0, schedule_version="v1", now=T0)
    assert first["id"] == second["id"]
    count = pg_conn.execute(
        "SELECT COUNT(*) FROM validation_schedule_slots WHERE horizon='short' AND universe='nifty100'"
    ).fetchone()[0]
    assert count == 1


def test_different_short_universes_share_close_timestamp_without_collision_real_postgres(pg_conn, pg_database_url):
    """Distinct universes resolving to the same calendar close instant
    (e.g. nifty100 and midcap both close at the same NSE session close)
    must not collide — the UNIQUE constraint is (horizon, universe,
    scheduled_slot, schedule_version), universe is part of the key."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot_nifty = ve.get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                                  scheduled_slot=T0, schedule_version="v1", now=T0)
    slot_midcap = ve.get_or_create_schedule_slot(horizon="short", universe="midcap",
                                                   scheduled_slot=T0, schedule_version="v1", now=T0)
    assert slot_nifty["id"] != slot_midcap["id"]
    count = pg_conn.execute(
        "SELECT COUNT(*) FROM validation_schedule_slots WHERE horizon='short' AND scheduled_slot=%s",
        (T0,),
    ).fetchone()[0]
    assert count == 2


def test_scheduled_short_admission_obeys_global_lease_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    ve.acquire_validation_execution_lease(owner="other", now=T0, lease_duration_seconds=600)
    result = ve.admit_validation_attempt(horizon="short", universe="nifty100", trigger_type="scheduler",
                                          owner="scheduler-short-1", scheduled_slot=T0, now=T0 + timedelta(seconds=1))
    assert result["ok"] is False


def test_two_concurrent_replicas_admit_at_most_one_active_short_attempt_real_postgres(pg_conn, pg_database_url):
    import queue
    import psycopg
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    results = queue.Queue()
    barrier = threading.Barrier(2)

    def replica(owner):
        barrier.wait()
        try:
            r = ve.admit_validation_attempt(horizon="short", universe="nifty100", trigger_type="scheduler",
                                             owner=owner, scheduled_slot=T0, now=T0, lease_duration_seconds=600)
            results.put((owner, r, None))
        except Exception as e:
            results.put((owner, None, e))

    t1 = threading.Thread(target=replica, args=("replica-a",))
    t2 = threading.Thread(target=replica, args=("replica-b",))
    t1.start(); t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)
    assert not t1.is_alive() and not t2.is_alive()

    collected = {}
    while not results.empty():
        owner, r, err = results.get()
        collected[owner] = (r, err)

    for owner, (r, err) in collected.items():
        if isinstance(err, psycopg.errors.DeadlockDetected):
            pytest.fail(f"{owner} hit a real PostgreSQL deadlock — lock ordering regression")
        assert err is None

    ok_count = sum(1 for r, _ in collected.values() if r["ok"])
    assert ok_count == 1, "exactly one replica must win global admission for the identical short slot"


def test_scheduled_versus_manual_short_contention_preserves_single_admission_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    scheduled = ve.admit_validation_attempt(horizon="short", universe="nifty100", trigger_type="scheduler",
                                             owner="scheduler-short-1", scheduled_slot=T0, now=T0)
    assert scheduled["ok"] is True
    manual = ve.admit_validation_attempt(horizon="short", universe="us", trigger_type="manual",
                                          owner="manual-1", now=T0 + timedelta(seconds=1))
    assert manual["ok"] is False  # global lease already held


def test_stale_recovery_and_retry_behave_identically_for_short_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot = ve.get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                           scheduled_slot=T0, schedule_version="v1", now=T0)
    lease1 = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=1)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease1["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease1["fencing_token"], now=T0)

    expired = T0 + timedelta(seconds=2)
    recovered = ve.admit_validation_attempt(horizon="short", universe="nifty100", trigger_type="scheduler",
                                             owner="w2", scheduled_slot=T0, now=expired, lease_duration_seconds=600)
    assert recovered["ok"] is True
    assert ve.get_schedule_attempt(attempt["id"])["status"] == "abandoned"


def test_completion_links_exactly_one_result_to_correct_short_slot_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot = ve.get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                           scheduled_slot=T0, schedule_version="v1", now=T0)
    lease = ve.acquire_validation_execution_lease(owner="w1", now=T0, lease_duration_seconds=600)
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="w1", fencing_token=lease["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt["id"], owner="w1", fencing_token=lease["fencing_token"], now=T0)
    completion = ve.complete_running_attempt_with_computed_result(
        attempt["id"], owner="w1", fencing_token=lease["fencing_token"],
        horizon="short", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row(horizon="short")],
        now=T0 + timedelta(seconds=30),
    )
    assert completion["ok"] is True
    fetched = pg_conn.execute(
        "SELECT status, result_run_id FROM validation_schedule_attempts WHERE id=%s", (attempt["id"],)
    ).fetchone()
    assert fetched == ("completed", completion["run_id"])
    fetched_slot = pg_conn.execute(
        "SELECT status FROM validation_schedule_slots WHERE id=%s", (slot["id"],)
    ).fetchone()
    assert fetched_slot[0] == "completed"


def test_no_orphan_short_result_survives_fencing_loss_real_postgres(pg_conn, pg_database_url):
    """Reuses the exact V-SCHED1C1-proven no-orphan mechanism for
    horizon="short" — a stale worker fenced out by a genuine reclaim
    creates zero val_runs/val_signals rows."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    pre_existing = _insert_val_run(pg_conn, horizon="short", universe="nifty100")

    slot = ve.get_or_create_schedule_slot(horizon="short", universe="nifty100",
                                           scheduled_slot=T0, schedule_version="v1", now=T0)
    lease_a = ve.acquire_validation_execution_lease(owner="worker-a", now=T0, lease_duration_seconds=1)
    attempt_a = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                            owner="worker-a", fencing_token=lease_a["fencing_token"], now=T0)
    ve.mark_attempt_running(attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"], now=T0)

    reclaim_time = T0 + timedelta(seconds=2)
    admitted_b = ve.admit_validation_attempt(
        horizon="short", universe="midcap", trigger_type="scheduler", owner="worker-b",
        scheduled_slot=T0, now=reclaim_time, lease_duration_seconds=600,
    )
    assert admitted_b["ok"] is True

    completion_a = ve.complete_running_attempt_with_computed_result(
        attempt_a["id"], owner="worker-a", fencing_token=lease_a["fencing_token"],
        horizon="short", universe="nifty100", run_at=T0.isoformat(),
        n_stocks=1, n_signals=1, summary_json="{}", signal_rows=[_signal_row(horizon="short")],
        now=reclaim_time + timedelta(seconds=1),
    )
    assert completion_a["ok"] is False
    assert _val_runs_count(pg_conn) == 1  # only the pre-existing row
    assert _val_signals_count(pg_conn) == 0


# ─────────────────────────────────────────────────────────────────────────
# V-USCAP6 — positive-renewal evidence under real PostgreSQL. The existing
# tests above (test_expiry_reclaim_with_stale_attempt_real_postgres,
# test_fenced_recovery_real_postgres) only proved the NEGATIVE cases —
# stale-heartbeat rejection and rejected-competitor-after-expiry. Flagged
# as a Medium gap by the V-USCAP5 independent review: nothing proved, under
# real Postgres CAS semantics, that a VALID current-owner/token heartbeat
# actually advances expires_at, nor that a competitor is rejected while
# that RENEWED lease is still current (as opposed to merely after the
# ORIGINAL expiry with no renewal in between — the scenario every other
# test here already covers). This is the exact composed scenario the
# wall-clock heartbeat added in this correction depends on. Controlled
# timestamps throughout — no real sleeps.
# ─────────────────────────────────────────────────────────────────────────

def test_valid_heartbeat_extends_expiry_and_blocks_competitor_until_renewed_expiry_real_postgres(
    pg_conn, pg_database_url
):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    # 1. Owner A acquires a short, controlled lease.
    lease_a = ve.acquire_validation_execution_lease(owner="A", now=T0, lease_duration_seconds=2)
    assert lease_a["ok"] is True
    token_a = lease_a["fencing_token"]

    # 2. Record the original expires_at and fencing token.
    row_before = pg_conn.execute(
        "SELECT lease_owner, fencing_token, expires_at FROM validation_execution_leases "
        "WHERE resource_key=%s", (ve.GLOBAL_LEASE_RESOURCE_KEY,),
    ).fetchone()
    assert row_before[0] == "A"
    assert row_before[1] == token_a
    original_expires_at = row_before[2]
    assert original_expires_at == T0 + timedelta(seconds=2)

    # 3. Before original expiry, A performs a valid heartbeat using the
    # exact owner/token — this is what the new wall-clock schedule now
    # calls throughout ordinary execution, not only during a drain.
    heartbeat_time = T0 + timedelta(seconds=1)
    hb = ve.heartbeat_validation_execution_lease(
        owner="A", fencing_token=token_a, now=heartbeat_time, lease_duration_seconds=2,
    )
    assert hb["ok"] is True

    # 4. Confirm expires_at genuinely advanced (not merely unchanged) and
    # the owner/token binding is untouched by a heartbeat, only the
    # expiry.
    row_after_heartbeat = pg_conn.execute(
        "SELECT lease_owner, fencing_token, expires_at FROM validation_execution_leases "
        "WHERE resource_key=%s", (ve.GLOBAL_LEASE_RESOURCE_KEY,),
    ).fetchone()
    renewed_expires_at = row_after_heartbeat[2]
    assert renewed_expires_at > original_expires_at, (
        f"heartbeat did not advance expires_at under real Postgres: "
        f"before={original_expires_at} after={renewed_expires_at}"
    )
    assert renewed_expires_at == heartbeat_time + timedelta(seconds=2)
    assert row_after_heartbeat[0] == "A", "heartbeat must never change the owner"
    assert row_after_heartbeat[1] == token_a, "heartbeat must never change the fencing token"

    # 5. After the ORIGINAL expiry but BEFORE the RENEWED expiry, Owner B
    # attempts acquisition — this is the composed scenario no existing
    # test covered: the CAS predicate (`expires_at <= now`) must evaluate
    # against the genuinely-renewed row, not a stale in-memory value.
    between_original_and_renewed = T0 + timedelta(seconds=2, milliseconds=500)
    assert original_expires_at < between_original_and_renewed < renewed_expires_at
    lease_b_too_early = ve.acquire_validation_execution_lease(
        owner="B", now=between_original_and_renewed, lease_duration_seconds=600,
    )

    # 6. Confirm B is rejected as already leased.
    assert lease_b_too_early["ok"] is False
    assert lease_b_too_early["reason"] == "already_leased"

    # 7. Confirm A remains the owner and its fencing token/binding are
    # unchanged by B's rejected attempt.
    row_after_rejected_b = pg_conn.execute(
        "SELECT lease_owner, fencing_token, expires_at FROM validation_execution_leases "
        "WHERE resource_key=%s", (ve.GLOBAL_LEASE_RESOURCE_KEY,),
    ).fetchone()
    assert row_after_rejected_b[0] == "A"
    assert row_after_rejected_b[1] == token_a
    assert row_after_rejected_b[2] == renewed_expires_at

    # 8. After the RENEWED expiry, B may acquire using the normal
    # expiry/reclaim contract.
    after_renewed_expiry = renewed_expires_at + timedelta(seconds=1)
    lease_b = ve.acquire_validation_execution_lease(
        owner="B", now=after_renewed_expiry, lease_duration_seconds=600,
    )
    assert lease_b["ok"] is True
    assert lease_b["fencing_token"] > token_a

    # 9. A's stale heartbeat (old token) is then rejected.
    stale_hb = ve.heartbeat_validation_execution_lease(
        owner="A", fencing_token=token_a, now=after_renewed_expiry, lease_duration_seconds=600,
    )
    assert stale_hb["ok"] is False

    # 10. No deadlock or unexpected transaction state — the connection is
    # still usable for further queries and the row is in a single,
    # coherent final state (B's, not a mix of A/B fields).
    final_row = pg_conn.execute(
        "SELECT lease_owner, fencing_token FROM validation_execution_leases "
        "WHERE resource_key=%s", (ve.GLOBAL_LEASE_RESOURCE_KEY,),
    ).fetchone()
    assert final_row[0] == "B"
    assert final_row[1] == lease_b["fencing_token"]


# ─────────────────────────────────────────────────────────────────────────
# V-SCHED1D-B — list_schedule_attempts() under real PostgreSQL. Proves the
# same read primitive the new authenticated GET /api/validation/attempts
# route uses behaves identically to its already-tested SQLite path: filters,
# schedule-slot LEFT JOIN, combined filters, deterministic (created_at, id)
# cursor pagination stable under concurrent insert, manual-attempt null
# slot fields, no internal-field leakage, and — since this primitive is
# read-only by construction — no write/lock/deadlock of any kind.
# ─────────────────────────────────────────────────────────────────────────

def _insert_attempt(pg_conn, *, slot_id=None, horizon="medium", universe="nifty100",
                     attempt_number=1, trigger_type="scheduler", status="completed",
                     result_run_id=None, failure_category=None, created_at=T0):
    row = pg_conn.execute(
        "INSERT INTO validation_schedule_attempts "
        "(slot_id, horizon, universe, attempt_number, trigger_type, status, "
        "lease_owner, lease_fencing_token, result_run_id, failure_category, created_at, updated_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,'seed-owner',1,%s,%s,%s,%s) RETURNING id",
        (slot_id, horizon, universe, attempt_number, trigger_type, status,
         result_run_id, failure_category, created_at, created_at),
    ).fetchone()
    return row[0]


def test_list_schedule_attempts_filters_and_join_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    slot = ve.get_or_create_schedule_slot(horizon="medium", universe="nifty100",
                                           scheduled_slot=T0, schedule_version="v1", now=T0)
    _insert_attempt(pg_conn, slot_id=slot["id"], horizon="medium", universe="nifty100",
                     trigger_type="scheduler", status="completed", result_run_id=None,
                     created_at=T0)
    _insert_attempt(pg_conn, slot_id=None, horizon="short", universe="us",
                     trigger_type="manual", status="failed", failure_category="RUN_EXCEPTION",
                     created_at=T0 + timedelta(seconds=1))

    all_rows = ve.list_schedule_attempts(limit=10)
    assert len(all_rows) == 2

    scheduler_only = ve.list_schedule_attempts(trigger_type="scheduler", limit=10)
    assert len(scheduler_only) == 1
    assert scheduler_only[0]["slot_id"] == slot["id"]
    assert scheduler_only[0]["scheduled_slot"] is not None
    assert scheduler_only[0]["schedule_version"] == "v1"
    assert scheduler_only[0]["slot_status"] is not None

    manual_only = ve.list_schedule_attempts(trigger_type="manual", limit=10)
    assert len(manual_only) == 1
    assert manual_only[0]["slot_id"] is None
    assert manual_only[0]["scheduled_slot"] is None
    assert manual_only[0]["schedule_version"] is None
    assert manual_only[0]["slot_status"] is None

    combined = ve.list_schedule_attempts(
        horizon="short", universe="us", trigger_type="manual", status="failed",
        failure_category="RUN_EXCEPTION", limit=10,
    )
    assert len(combined) == 1

    for row in all_rows:
        assert "lease_owner" not in row
        assert "lease_fencing_token" not in row
        assert "failure_summary" not in row
        for field in ("started_at", "heartbeat_at", "completed_at", "created_at", "updated_at"):
            if row[field] is not None:
                assert isinstance(row[field], str), f"{field} must be normalized to an ISO string, got {type(row[field])}"


def test_list_schedule_attempts_cursor_pagination_stable_under_concurrent_insert_real_postgres(
    pg_conn, pg_database_url
):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    ids = []
    for i in range(5):
        ids.append(_insert_attempt(pg_conn, slot_id=None, trigger_type="manual",
                                    attempt_number=i + 1, created_at=T0 + timedelta(seconds=i)))

    page1 = ve.list_schedule_attempts(limit=3)
    assert [r["id"] for r in page1] == list(reversed(ids))[:3]
    last = page1[-1]

    # A brand-new attempt is inserted between page requests — it sorts
    # AHEAD of everything already paged and must never appear on the next
    # (strictly older) page, and no already-paged row may be duplicated
    # or skipped.
    _insert_attempt(pg_conn, slot_id=None, trigger_type="manual", attempt_number=99,
                     created_at=T0 + timedelta(seconds=100))

    from datetime import datetime as _dt
    page2 = ve.list_schedule_attempts(
        limit=3,
        cursor_created_at=_dt.fromisoformat(last["created_at"]),
        cursor_id=last["id"],
    )
    assert [r["id"] for r in page2] == list(reversed(ids))[3:]


def test_list_schedule_attempts_performs_no_write_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    _insert_attempt(pg_conn, slot_id=None, trigger_type="manual", created_at=T0)
    before = pg_conn.execute("SELECT COUNT(*) FROM validation_schedule_attempts").fetchone()[0]

    ve.list_schedule_attempts(limit=10)
    ve.list_schedule_attempts(horizon="short", limit=10)
    ve.list_schedule_attempts(status="failed", limit=10)

    after = pg_conn.execute("SELECT COUNT(*) FROM validation_schedule_attempts").fetchone()[0]
    assert after == before, "list_schedule_attempts must never write — row count changed"

    # Still usable afterward — no lingering lock, no deadlock.
    lease = ve.acquire_validation_execution_lease(owner="post-read-check", now=T0, lease_duration_seconds=60)
    assert lease["ok"] is True


# ─────────────────────────────────────────────────────────────────────────
# V-USACT1-B — killable process-boundary deadline, real PostgreSQL evidence.
# execute_and_complete_admitted_attempt() now runs validation inside a
# genuinely killable child OS process (see _run_validation_in_subprocess)
# and enforces a hard wall-clock MAX_RUN_DURATION_SECONDS deadline. These
# tests prove the same lifecycle already proven under SQLite
# (test_validation_run_deadline.py) holds under real PostgreSQL CAS/
# transactional semantics: zero result rows on deadline, coherent
# terminal attempt/slot/lease state, immediate re-admission, a stale
# killed child cannot link a result, and the new RUN_DEADLINE_EXCEEDED
# category is queryable through list_schedule_attempts.
#
# V-USACT1-B-C3 — no fork anywhere: every real child here runs under
# genuine production "spawn" via a top-level, self-contained worker
# (_worker_stuck_real_pg / _worker_near_boundary_real_pg) that configures
# its OWN freshly-spawned copy of services.validation_engine internally,
# never relying on inheriting this file's module-level attribute
# mutations into a forked child.
# ─────────────────────────────────────────────────────────────────────────

def _stuck_backtest_real_pg(sym, horizon, bench_df, market, **kwargs):
    import threading
    threading.Event().wait()  # genuinely never returns


def _worker_stuck_real_pg(result_queue, horizon, universe, max_workers, trigger_type):
    """V-USACT1-B-C3 — top-level, spawn-safe worker replacing the old
    fork-dependent approach: configures its OWN freshly-spawned copy of
    services.validation_engine internally (never relies on inheriting
    any monkeypatch/attribute mutation from the parent test process) and
    calls the REAL run_validation() with a genuinely non-cooperative
    per-symbol worker."""
    import threading as _t
    from unittest.mock import MagicMock as _EMagicMock
    import numpy as _enp
    import pandas as _epd
    from datetime import datetime as _edatetime, timezone as _etimezone
    import services.validation_engine as _ve

    _ve.RUN_STALL_TIMEOUT_SECONDS = 3600
    _ve.US_BASKET = ["STUCK"]

    def _stuck(sym, horizon, bench_df, market, **kwargs):
        _t.Event().wait()  # genuinely never returns
    _ve._backtest_stock = _stuck

    def _bench_df():
        dates = _epd.bdate_range("2019-01-01", periods=300)
        close = 100.0 * _enp.cumprod(1 + _enp.random.default_rng(1).normal(0.0003, 0.008, 300))
        return _epd.DataFrame({"Close": close}, index=dates)
    mock_yf = _EMagicMock()
    mock_yf.Ticker.return_value.history.return_value = _bench_df()
    _ve.yf = mock_yf

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


def _worker_near_boundary_real_pg(result_queue, horizon, universe, max_workers, trigger_type):
    """V-USACT1-B-C3 — top-level, spawn-safe worker for the deadline-
    versus-success race test: configures its own freshly-spawned copy of
    services.validation_engine internally and calls the REAL
    run_validation() with a worker duration set close to the test's own
    configured deadline."""
    import threading as _t
    from unittest.mock import MagicMock as _EMagicMock
    import numpy as _enp
    import pandas as _epd
    from datetime import datetime as _edatetime, timezone as _etimezone
    import services.validation_engine as _ve

    _ve.RUN_STALL_TIMEOUT_SECONDS = 3600
    _ve.US_BASKET = ["S1"]

    def _near_boundary(sym, horizon, bench_df, market, **kwargs):
        ws = kwargs.get("_window_stats")
        if ws is not None:
            ws["considered"] = 1
            ws["benchmark_valid"] = 1
        _t.Event().wait(0.5)
        return []
    _ve._backtest_stock = _near_boundary

    def _bench_df():
        dates = _epd.bdate_range("2019-01-01", periods=300)
        close = 100.0 * _enp.cumprod(1 + _enp.random.default_rng(1).normal(0.0003, 0.008, 300))
        return _epd.DataFrame({"Close": close}, index=dates)
    mock_yf = _EMagicMock()
    mock_yf.Ticker.return_value.history.return_value = _bench_df()
    _ve.yf = mock_yf

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


def _install_stuck_provider(ve):
    # V-USACT1-B-C3 — no fork anywhere: the real per-attempt child now
    # runs _worker_stuck_real_pg under genuine "spawn", which configures
    # its own copy of ve internally rather than relying on inheriting
    # this attribute mutation into a forked child.
    ve._validation_child_worker = _worker_stuck_real_pg


def _worker_fast_success_real_pg(result_queue, horizon, universe, max_workers, trigger_type):
    """V-USACT1-B-CI1 — top-level, spawn-safe worker for a genuinely
    fast, successful attempt (used by tests that first install a stuck
    worker for one attempt, then need a real, independent fast-success
    child for a SECOND, later attempt). Reassigning ve._backtest_stock/
    ve.US_BASKET directly (the pre-C3 pattern) has no effect once
    ve._validation_child_worker itself has been replaced by a dedicated
    top-level worker that configures its own internal state — this
    worker must instead be explicitly installed as
    ve._validation_child_worker in its own right before the second
    attempt runs. Emits genuine progress/result IPC messages via the
    real run_validation() so the parent-side orchestration and atomic
    completion path are exercised exactly as in production; no network
    access, no direct database write, no inherited monkeypatch."""
    import threading as _t
    from unittest.mock import MagicMock as _EMagicMock
    import numpy as _enp
    import pandas as _epd
    from datetime import datetime as _edatetime, timezone as _etimezone
    import services.validation_engine as _ve

    _ve.RUN_STALL_TIMEOUT_SECONDS = 3600
    _ve.US_BASKET = ["OK1"]

    def _fast_ok(sym, horizon, bench_df, market, **kwargs):
        ws = kwargs.get("_window_stats")
        if ws is not None:
            ws["considered"] = 1
            ws["benchmark_valid"] = 1
        return []
    _ve._backtest_stock = _fast_ok

    def _bench_df():
        dates = _epd.bdate_range("2019-01-01", periods=300)
        close = 100.0 * _enp.cumprod(1 + _enp.random.default_rng(1).normal(0.0003, 0.008, 300))
        return _epd.DataFrame({"Close": close}, index=dates)
    mock_yf = _EMagicMock()
    mock_yf.Ticker.return_value.history.return_value = _bench_df()
    _ve.yf = mock_yf

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


def test_run_deadline_exceeded_zero_result_rows_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    _install_stuck_provider(ve)

    now = T0
    lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
    assert lease["ok"] is True
    admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                         fencing_token=lease["fencing_token"], now=now)
    assert admitted["ok"] is True
    ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)

    result = ve.execute_and_complete_admitted_attempt(
        admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
        lease_duration_seconds=600, max_run_duration_seconds=2,
    )
    assert result["ok"] is False
    assert result["reason"] == "run_deadline_exceeded"
    assert _val_runs_count(pg_conn) == 0
    assert _val_signals_count(pg_conn) == 0


def test_run_deadline_exceeded_coherent_terminal_state_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    _install_stuck_provider(ve)

    now = T0
    lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
    admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                         fencing_token=lease["fencing_token"], now=now)
    ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)

    ve.execute_and_complete_admitted_attempt(
        admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
        lease_duration_seconds=600, max_run_duration_seconds=2,
    )

    row = pg_conn.execute(
        "SELECT status, failure_category, result_run_id FROM validation_schedule_attempts WHERE id=%s",
        (admitted["id"],),
    ).fetchone()
    assert row[0] == "failed"
    assert row[1] == "RUN_DEADLINE_EXCEEDED"
    assert row[2] is None

    lease_row = pg_conn.execute(
        "SELECT lease_owner, active_attempt_id FROM validation_execution_leases WHERE resource_key=%s",
        (ve.GLOBAL_LEASE_RESOURCE_KEY,),
    ).fetchone()
    assert lease_row == (None, None)


def test_new_attempt_admissible_immediately_after_deadline_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    _install_stuck_provider(ve)

    now = T0
    lease_a = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
    admitted_a = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                           fencing_token=lease_a["fencing_token"], now=now)
    ve.mark_attempt_running(admitted_a["id"], owner="A", fencing_token=lease_a["fencing_token"], now=now)
    result = ve.execute_and_complete_admitted_attempt(
        admitted_a["id"], "A", lease_a["fencing_token"], "short", "us", "manual",
        lease_duration_seconds=600, max_run_duration_seconds=2,
    )
    assert result["ok"] is False

    now_b = now + timedelta(seconds=5)
    lease_b = ve.acquire_validation_execution_lease(owner="B", now=now_b, lease_duration_seconds=600)
    assert lease_b["ok"] is True, f"B could not acquire immediately after A's deadline failure: {lease_b}"
    admitted_b = ve.create_manual_attempt(horizon="short", universe="us", owner="B",
                                           fencing_token=lease_b["fencing_token"], now=now_b)
    assert admitted_b["ok"] is True


def test_stale_killed_child_cannot_link_a_result_real_postgres(pg_conn, pg_database_url):
    """After A is killed for exceeding the deadline, no code path in A's
    process tree ever held a lease credential capable of calling
    complete_running_attempt_with_computed_result — proven here by
    confirming B's subsequent, entirely separate successful completion is
    the only val_runs row that exists, and it is correctly linked to B's
    own attempt, never A's."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    _install_stuck_provider(ve)

    now = T0
    lease_a = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
    admitted_a = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                           fencing_token=lease_a["fencing_token"], now=now)
    ve.mark_attempt_running(admitted_a["id"], owner="A", fencing_token=lease_a["fencing_token"], now=now)
    ve.execute_and_complete_admitted_attempt(
        admitted_a["id"], "A", lease_a["fencing_token"], "short", "us", "manual",
        lease_duration_seconds=600, max_run_duration_seconds=2,
    )

    # B runs a genuinely fast, successful attempt afterward.
    # V-USACT1-B-CI1 — reassigning ve._backtest_stock/ve.US_BASKET
    # directly (the pre-C3 pattern) has no effect anymore: A's setup
    # above already replaced ve._validation_child_worker itself with the
    # top-level _worker_stuck_real_pg, which configures its own internal
    # state inside each spawned child regardless of what this test
    # process's own ve module attributes say. B's attempt must instead
    # install its own dedicated, genuinely spawn-safe fast-success
    # top-level worker as ve._validation_child_worker in its own right.
    ve._validation_child_worker = _worker_fast_success_real_pg

    now_b = now + timedelta(seconds=5)
    lease_b = ve.acquire_validation_execution_lease(owner="B", now=now_b, lease_duration_seconds=600)
    assert lease_b["ok"] is True
    admitted_b = ve.create_manual_attempt(horizon="short", universe="us", owner="B",
                                           fencing_token=lease_b["fencing_token"], now=now_b)
    ve.mark_attempt_running(admitted_b["id"], owner="B", fencing_token=lease_b["fencing_token"], now=now_b)
    result_b = ve.execute_and_complete_admitted_attempt(
        admitted_b["id"], "B", lease_b["fencing_token"], "short", "us", "manual",
        lease_duration_seconds=600, max_run_duration_seconds=30,
    )
    assert result_b["ok"] is True

    assert _val_runs_count(pg_conn) == 1
    row = pg_conn.execute(
        "SELECT result_run_id FROM validation_schedule_attempts WHERE id=%s", (admitted_b["id"],),
    ).fetchone()
    assert row[0] == result_b["run_id"]
    row_a = pg_conn.execute(
        "SELECT result_run_id FROM validation_schedule_attempts WHERE id=%s", (admitted_a["id"],),
    ).fetchone()
    assert row_a[0] is None


def test_run_deadline_exceeded_attempt_history_filter_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    _install_stuck_provider(ve)

    now = T0
    lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
    admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                         fencing_token=lease["fencing_token"], now=now)
    ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)
    ve.execute_and_complete_admitted_attempt(
        admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
        lease_duration_seconds=600, max_run_duration_seconds=2,
    )

    rows = ve.list_schedule_attempts(failure_category="RUN_DEADLINE_EXCEEDED", limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == admitted["id"]
    assert "lease_owner" not in rows[0]
    assert "lease_fencing_token" not in rows[0]
    assert "failure_summary" not in rows[0]


# ─────────────────────────────────────────────────────────────────────────
# V-USACT1-B-C1 Blocker 3/7 — terminal (non-retryable) deadline failure and
# deadline-versus-completion race, under real PostgreSQL. Uses controlled
# timestamps/durations and bounded joins — no arbitrary sleeps for
# synchronization.
# ─────────────────────────────────────────────────────────────────────────

def test_deadline_failed_slot_is_terminal_and_cannot_be_readmitted_real_postgres(pg_conn, pg_database_url):
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)
    _install_stuck_provider(ve)

    now = T0
    slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                           schedule_version="v1", now=now)
    lease = ve.acquire_validation_execution_lease(owner="sched-1", now=now, lease_duration_seconds=600)
    assert lease["ok"] is True
    attempt = ve.create_schedule_attempt(slot_id=slot["id"], trigger_type="scheduler",
                                          owner="sched-1", fencing_token=lease["fencing_token"], now=now)
    assert attempt["ok"] is True
    ve.mark_attempt_running(attempt["id"], owner="sched-1", fencing_token=lease["fencing_token"], now=now)

    result = ve.execute_and_complete_admitted_attempt(
        attempt["id"], "sched-1", lease["fencing_token"], "short", "us", "scheduler",
        lease_duration_seconds=600, max_run_duration_seconds=2,
    )
    assert result["ok"] is False
    assert result["reason"] == "run_deadline_exceeded"

    slot_row = pg_conn.execute(
        "SELECT status, active_attempt_id FROM validation_schedule_slots WHERE id=%s", (slot["id"],),
    ).fetchone()
    assert slot_row[0] == "failed", "slot must be TERMINAL, never 'due', after a deadline timeout"
    assert slot_row[1] is None

    # A later scheduler tick re-resolving the SAME canonical slot and
    # attempting to admit against it must be refused by the real
    # primitive under real Postgres.
    later = now + timedelta(minutes=5)
    same_slot = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=now,
                                                 schedule_version="v1", now=later)
    assert same_slot["id"] == slot["id"]
    lease2 = ve.acquire_validation_execution_lease(owner="sched-2", now=later, lease_duration_seconds=600)
    assert lease2["ok"] is True
    retry = ve.create_schedule_attempt(slot_id=same_slot["id"], trigger_type="scheduler",
                                        owner="sched-2", fencing_token=lease2["fencing_token"], now=later)
    assert retry["ok"] is False, f"a terminal deadline-failed slot must refuse re-admission: {retry}"

    # The NEXT genuinely new canonical session (a different scheduled_slot)
    # remains fully eligible.
    tomorrow = now + timedelta(days=1)
    slot_tomorrow = ve.get_or_create_schedule_slot(horizon="short", universe="us", scheduled_slot=tomorrow,
                                                     schedule_version="v1", now=tomorrow)
    assert slot_tomorrow["id"] != slot["id"]
    lease3 = ve.acquire_validation_execution_lease(owner="sched-3", now=tomorrow, lease_duration_seconds=600)
    assert lease3["ok"] is True
    new_attempt = ve.create_schedule_attempt(slot_id=slot_tomorrow["id"], trigger_type="scheduler",
                                              owner="sched-3", fencing_token=lease3["fencing_token"], now=tomorrow)
    assert new_attempt["ok"] is True


def test_deadline_versus_successful_completion_race_exactly_one_outcome_no_deadlock_real_postgres(
    pg_conn, pg_database_url
):
    """A worker whose duration is deliberately set very close to the
    configured deadline — proving, under real Postgres transactional
    semantics (not SQLite), that whichever side of the race it lands on,
    exactly one coherent outcome results: either a genuine result linked
    to exactly one val_runs row, or a clean rejection with zero orphan
    val_runs/val_signals rows and a coherent terminal attempt/slot/lease
    state — and that the connection remains fully usable afterward (no
    DeadlockDetected, no lingering lock)."""
    import services.validation_engine as ve
    ve._USE_POSTGRES = True
    _reset_ledger_tables(pg_conn)

    from unittest.mock import MagicMock
    import numpy as np
    import pandas as pd
    import threading as _threading

    def _bench_df():
        dates = pd.bdate_range("2019-01-01", periods=300)
        close = 100.0 * np.cumprod(1 + np.random.default_rng(1).normal(0.0003, 0.008, 300))
        return pd.DataFrame({"Close": close}, index=dates)

    # V-USACT1-B-C3 — no fork: the real child runs
    # _worker_near_boundary_real_pg under genuine "spawn", which
    # configures its own freshly-imported copy of ve internally.
    ve._validation_child_worker = _worker_near_boundary_real_pg

    now = T0
    lease = ve.acquire_validation_execution_lease(owner="A", now=now, lease_duration_seconds=600)
    assert lease["ok"] is True
    admitted = ve.create_manual_attempt(horizon="short", universe="us", owner="A",
                                         fencing_token=lease["fencing_token"], now=now)
    assert admitted["ok"] is True
    ve.mark_attempt_running(admitted["id"], owner="A", fencing_token=lease["fencing_token"], now=now)

    # V-USACT1-B-C2, Correction 4 — explicit, bounded-join execution with
    # an explicit failure on psycopg.errors.DeadlockDetected, matching
    # the pattern already established for the other concurrency tests in
    # this file, rather than relying on an uncaught exception to
    # eventually surface as a generic pytest error.
    outcome = {}

    def _run():
        try:
            outcome["result"] = ve.execute_and_complete_admitted_attempt(
                admitted["id"], "A", lease["fencing_token"], "short", "us", "manual",
                lease_duration_seconds=600, max_run_duration_seconds=0.5,
            )
        except Exception as e:
            outcome["error"] = e

    t = _threading.Thread(target=_run)
    t.start()
    t.join(timeout=30)
    assert not t.is_alive(), "execute_and_complete_admitted_attempt hung — possible undetected lock contention"

    if "error" in outcome:
        err = outcome["error"]
        if isinstance(err, psycopg.errors.DeadlockDetected):
            pytest.fail(f"deadline-vs-success race hit a real PostgreSQL deadlock: {err!r}")
        raise err
    result = outcome["result"]

    if result["ok"]:
        assert _val_runs_count(pg_conn) == 1
        row = pg_conn.execute(
            "SELECT result_run_id, status FROM validation_schedule_attempts WHERE id=%s", (admitted["id"],),
        ).fetchone()
        assert row[0] == result["run_id"]
        assert row[1] == "completed"
    else:
        assert result["reason"] == "run_deadline_exceeded"
        assert _val_runs_count(pg_conn) == 0
        assert _val_signals_count(pg_conn) == 0
        row = pg_conn.execute(
            "SELECT result_run_id, status, failure_category FROM validation_schedule_attempts WHERE id=%s",
            (admitted["id"],),
        ).fetchone()
        assert row[0] is None
        assert row[1] == "failed"
        assert row[2] == "RUN_DEADLINE_EXCEEDED"

    # No DeadlockDetected, no lingering lock — the connection is still
    # fully usable for further real queries/transactions immediately
    # afterward.
    lease_check = ve.acquire_validation_execution_lease(
        owner="post-race-check", now=now + timedelta(seconds=5), lease_duration_seconds=60,
    )
    assert lease_check["ok"] is True
    still_usable = pg_conn.execute("SELECT 1").fetchone()
    assert still_usable[0] == 1
