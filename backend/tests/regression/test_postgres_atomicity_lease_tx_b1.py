"""
LEASE-TX-B1 (2026-07-20) — multi-statement atomicity for the three
postgres_store.py sites that remained after LEASE-TX-A1's atomic job+lease
reservation fix (see test_weekly_multibagger_lifecycle.py's own LEASE-TX-A1
section for that earlier fix and its FakeAutocommitConnection precedent,
which this file follows the same approach as):

1. try_claim_queued_multibagger_job() — SELECT ... FOR UPDATE SKIP LOCKED
   followed by an UPDATE. Under this module's autocommit=True pool, a bare
   SELECT's implicit transaction ends (releasing the row lock) the instant
   it returns, before the following UPDATE ever runs — so two concurrent
   claimants could both lock and claim the same row.
2. reconcile_stale_multibagger_jobs() — marks stale jobs 'interrupted' then
   releases their leases. Without a shared transaction, a crash between the
   two UPDATEs can leave a job 'interrupted' with its lease still active.
3. reconcile_stale_daily_picks_jobs() — same gap as #2, for Daily Picks.

All three are fixed by wrapping their statements in a real
`with conn.transaction():` block (psycopg3's documented way to get BEGIN/
COMMIT/ROLLBACK on an autocommit connection).

No real DB, no external providers, no live network. The fake connection
below models the two behaviors that actually matter: (1) a bare execute()
outside a transaction() block persists immediately and releases any lock
it took, exactly like real autocommit; (2) execute() inside a
`with conn.transaction():` block is only durable (and only releases its
locks) when that block exits cleanly, and is fully undone if an exception
propagates out of it. A plain MagicMock cannot distinguish these two cases
— which is exactly how the original defects went undetected.
"""
import copy
from unittest.mock import patch

import pytest


# ─── shared fake DB + connection ────────────────────────────────────────────

class _SharedDB:
    """Represents server-side state shared across separate simulated
    connections — same as real Postgres, where row locks and committed rows
    are visible across connections, not scoped to one Python object."""

    def __init__(self):
        self.multibagger_jobs = []   # {job_id, status, is_stale, last_error, completed_at}
        self.daily_picks_jobs = []   # same shape
        self.leases = []             # {resource, owner_job_id, released_at}
        self.locked_job_ids = set()  # SELECT ... FOR UPDATE locks currently held


class _FakeCursor:
    def __init__(self, rowcount=0, row=None, rows=None):
        self.rowcount = rowcount
        self._row = row
        self._rows = rows if rows is not None else []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _FakeTxnCtx:
    """Models psycopg3's conn.transaction(): BEGIN on enter, COMMIT on a
    clean exit, ROLLBACK if an exception propagates out — regardless of the
    connection's autocommit setting. Locks taken via this connection during
    the block are released at exit either way, exactly like real Postgres
    (a rollback releases locks too; it does not "keep them held")."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        assert not self._conn._in_txn, "nested transaction() not modeled — not needed here"
        self._conn._in_txn = True
        self._conn._locked_this_txn = set()
        self._conn._snapshot = copy.deepcopy(
            (self._conn.db.multibagger_jobs, self._conn.db.daily_picks_jobs, self._conn.db.leases)
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        conn = self._conn
        conn.db.locked_job_ids -= conn._locked_this_txn
        conn._in_txn = False
        if exc_type is None:
            conn.commit_count += 1
            return False
        conn.db.multibagger_jobs, conn.db.daily_picks_jobs, conn.db.leases = conn._snapshot
        conn.rollback_count += 1
        return False


class FakeAtomicityConn:
    def __init__(self, shared_db=None, on_after_select=None):
        self.db = shared_db if shared_db is not None else _SharedDB()
        self.commit_count = 0
        self.rollback_count = 0
        self.explicit_commit_calls = 0
        self.explicit_rollback_calls = 0
        self._in_txn = False
        self._locked_this_txn = set()
        self._snapshot = None
        self.raise_on_select = False
        self.raise_on_claim_update = False
        self.raise_on_job_update = False
        self.raise_on_lease_release = False
        self._on_after_select = on_after_select

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return _FakeTxnCtx(self)

    def commit(self):
        self.explicit_commit_calls += 1

    def rollback(self):
        self.explicit_rollback_calls += 1

    def execute(self, sql, params=None):
        norm = " ".join(sql.split())
        if "FOR UPDATE SKIP LOCKED" in norm:
            return self._select_for_update(params)
        if "SET status = 'running'" in norm:
            return self._claim_update(params)
        if "UPDATE multibagger_refresh_jobs" in norm and "'interrupted'" in norm:
            return self._reconcile_update(self.db.multibagger_jobs)
        if "UPDATE daily_picks_jobs" in norm and "'interrupted'" in norm:
            return self._reconcile_update(self.db.daily_picks_jobs)
        if "UPDATE heavy_workload_leases" in norm:
            return self._release_leases(params)
        raise AssertionError(f"FakeAtomicityConn: unrecognized SQL: {norm[:120]}")

    def _select_for_update(self, params):
        if self.raise_on_select:
            raise RuntimeError("connection reset by peer")
        (job_id,) = params
        row = next(
            (r for r in self.db.multibagger_jobs if r["job_id"] == job_id and r["status"] == "queued"),
            None,
        )
        if row is None or job_id in self.db.locked_job_ids:
            return _FakeCursor(row=None)
        self.db.locked_job_ids.add(job_id)
        if self._in_txn:
            self._locked_this_txn.add(job_id)
        else:
            # Bare autocommit statement: its implicit transaction commits
            # (and the lock releases) server-side as part of executing this
            # one statement — before control ever returns to the calling
            # Python code, i.e. before anything below can interleave. This
            # ordering (release BEFORE the hook fires) is what makes the
            # base defect (no explicit transaction) reproducible: a second
            # claimant interleaved right here sees no lock at all.
            self.db.locked_job_ids.discard(job_id)
        hook, self._on_after_select = self._on_after_select, None
        if hook is not None:
            hook()
        return _FakeCursor(row=(job_id,))

    def _claim_update(self, params):
        if self.raise_on_claim_update:
            raise RuntimeError("connection reset by peer")
        (job_id,) = params
        row = next(r for r in self.db.multibagger_jobs if r["job_id"] == job_id)
        row["status"] = "running"
        return _FakeCursor(rowcount=1)

    def _reconcile_update(self, jobs):
        if self.raise_on_job_update:
            raise RuntimeError("db error on job-status update")
        matched = [r for r in jobs if r["status"] in ("queued", "running") and r.get("is_stale")]
        for r in matched:
            r["status"] = "interrupted"
            r["completed_at"] = "NOW"
            r["last_error"] = "reconciled: no heartbeat for over Nh"
        return _FakeCursor(rows=[(r["job_id"],) for r in matched])

    def _release_leases(self, params):
        if self.raise_on_lease_release:
            raise RuntimeError("lease release failed")
        (job_ids,) = params
        released = 0
        for lease in self.db.leases:
            if lease["released_at"] is None and lease["owner_job_id"] in job_ids:
                lease["released_at"] = "NOW"
                released += 1
        return _FakeCursor(rowcount=released)


def _pool_for(conn):
    class _Pool:
        def connection(self):
            return conn
    return _Pool()


def _raising_pool(exc):
    class _Pool:
        def connection(self):
            raise exc
    return _Pool()


# ── proof the fake genuinely models autocommit + real transaction semantics ──

def test_fake_models_autocommit_lock_released_immediately_outside_transaction():
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    cursor = conn._select_for_update(("job-1",))
    assert cursor.fetchone() == ("job-1",)
    assert "job-1" not in conn.db.locked_job_ids, "a bare autocommit statement must not hold its lock afterward"


def test_fake_transaction_holds_lock_until_block_exits():
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    with conn.transaction():
        conn._select_for_update(("job-1",))
        assert "job-1" in conn.db.locked_job_ids, "the lock must still be held inside the open transaction"
    assert "job-1" not in conn.db.locked_job_ids, "the lock releases once the transaction block exits"


def test_fake_transaction_rolls_back_job_status_on_exception():
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "job-1", "status": "queued", "is_stale": True})
    with pytest.raises(ValueError):
        with conn.transaction():
            conn._reconcile_update(conn.db.multibagger_jobs)
            raise ValueError("boom")
    assert conn.db.multibagger_jobs[0]["status"] == "queued", "an exception must discard everything buffered"
    assert conn.rollback_count == 1
    assert conn.commit_count == 0


# ─── try_claim_queued_multibagger_job ───────────────────────────────────────
# (spec items 1-6; item 7 — removing the transaction breaks test #6 — is
# proven in the Step 9 mutation pass, not a separate standalone test)

def test_claim_one_queued_row_is_claimed_and_updated():
    from services.postgres_store import try_claim_queued_multibagger_job
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        result = try_claim_queued_multibagger_job("job-1")
    assert result is True
    assert conn.db.multibagger_jobs[0]["status"] == "running"
    assert conn.commit_count == 1


def test_claim_no_eligible_row_returns_existing_empty_result():
    from services.postgres_store import try_claim_queued_multibagger_job
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "job-1", "status": "running"})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        result = try_claim_queued_multibagger_job("job-1")
    assert result is False
    assert conn.db.multibagger_jobs[0]["status"] == "running", "no row change when nothing eligible"


def test_claim_select_exception_preserves_existing_error_behavior():
    from services.postgres_store import try_claim_queued_multibagger_job
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    conn.raise_on_select = True
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        with pytest.raises(RuntimeError, match="connection reset by peer"):
            try_claim_queued_multibagger_job("job-1")
    assert conn.db.multibagger_jobs[0]["status"] == "queued"


def test_claim_update_exception_leaves_row_queued():
    from services.postgres_store import try_claim_queued_multibagger_job
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    conn.raise_on_claim_update = True
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        with pytest.raises(RuntimeError, match="connection reset by peer"):
            try_claim_queued_multibagger_job("job-1")
    assert conn.db.multibagger_jobs[0]["status"] == "queued", "the UPDATE exception must leave the row queued"
    assert conn.rollback_count == 1


def test_claim_select_lock_remains_held_through_the_update():
    """Proves the lock taken by SELECT is still held immediately afterward,
    before UPDATE runs — a second connection's own SELECT must see it as
    unavailable (SKIP LOCKED) at that exact moment."""
    from services.postgres_store import try_claim_queued_multibagger_job
    shared = _SharedDB()
    shared.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    observed = {}

    def _peek():
        peeker = FakeAtomicityConn(shared)
        cursor = peeker._select_for_update(("job-1",))
        observed["row_visible_to_second_conn"] = cursor.fetchone()

    conn = FakeAtomicityConn(shared, on_after_select=_peek)
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        result = try_claim_queued_multibagger_job("job-1")
    assert result is True
    assert observed["row_visible_to_second_conn"] is None, (
        "a second connection's SELECT ... FOR UPDATE SKIP LOCKED must see no row while the "
        "first connection's transaction (holding the lock) is still open"
    )


def test_claim_two_simulated_claimants_cannot_both_claim_the_same_row():
    from services.postgres_store import try_claim_queued_multibagger_job
    shared = _SharedDB()
    shared.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    nested_result = {}

    def _run_second_claimant():
        conn_b = FakeAtomicityConn(shared)
        with patch("services.postgres_store._get_pool", return_value=_pool_for(conn_b)):
            nested_result["b"] = try_claim_queued_multibagger_job("job-1")

    conn_a = FakeAtomicityConn(shared, on_after_select=_run_second_claimant)
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn_a)):
        result_a = try_claim_queued_multibagger_job("job-1")

    assert result_a is True
    assert nested_result["b"] is False, "the second claimant must not also claim the same row"
    assert shared.multibagger_jobs[0]["status"] == "running"
    assert sum(1 for r in shared.multibagger_jobs if r["status"] == "running") == 1


# ─── reconcile_stale_multibagger_jobs ───────────────────────────────────────
# (spec items 8-13; item 14 proven in Step 9)

def test_multibagger_reconcile_stale_job_and_lease_released_together():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "mb-1", "status": "running", "is_stale": True})
    conn.db.leases.append({"resource": "US_YFINANCE_HEAVY", "owner_job_id": "mb-1", "released_at": None})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_multibagger_jobs()
    assert count == 1
    assert conn.db.multibagger_jobs[0]["status"] == "interrupted"
    assert conn.db.leases[0]["released_at"] == "NOW"
    assert conn.commit_count == 1


def test_multibagger_reconcile_multiple_stale_jobs_and_leases():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs = [
        {"job_id": "mb-1", "status": "running", "is_stale": True},
        {"job_id": "mb-2", "status": "queued", "is_stale": True},
    ]
    conn.db.leases = [
        {"resource": "US_YFINANCE_HEAVY", "owner_job_id": "mb-1", "released_at": None},
        {"resource": "IN_SCREENER_HEAVY", "owner_job_id": "mb-2", "released_at": None},
    ]
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_multibagger_jobs()
    assert count == 2
    assert all(r["status"] == "interrupted" for r in conn.db.multibagger_jobs)
    assert all(l["released_at"] == "NOW" for l in conn.db.leases)


def test_multibagger_reconcile_no_stale_returns_zero_no_changes():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "mb-1", "status": "running", "is_stale": False})
    conn.db.leases.append({"resource": "US_YFINANCE_HEAVY", "owner_job_id": "mb-1", "released_at": None})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_multibagger_jobs()
    assert count == 0
    assert conn.db.multibagger_jobs[0]["status"] == "running"
    assert conn.db.leases[0]["released_at"] is None


def test_multibagger_reconcile_job_update_exception_changes_neither_job_nor_lease():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "mb-1", "status": "running", "is_stale": True})
    conn.db.leases.append({"resource": "US_YFINANCE_HEAVY", "owner_job_id": "mb-1", "released_at": None})
    conn.raise_on_job_update = True
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_multibagger_jobs()
    assert count == 0, "the function's own try/except must swallow the error and return 0"
    assert conn.db.multibagger_jobs[0]["status"] == "running"
    assert conn.db.leases[0]["released_at"] is None


def test_multibagger_reconcile_lease_exception_rolls_back_job_status_changes():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "mb-1", "status": "running", "is_stale": True})
    conn.db.leases.append({"resource": "US_YFINANCE_HEAVY", "owner_job_id": "mb-1", "released_at": None})
    conn.raise_on_lease_release = True
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_multibagger_jobs()
    assert count == 0
    assert conn.db.multibagger_jobs[0]["status"] == "running", (
        "a lease-release failure must roll back the job-status change made earlier in the same transaction"
    )
    assert conn.db.leases[0]["released_at"] is None
    assert conn.rollback_count == 1


def test_multibagger_reconcile_unrelated_lease_remains_unchanged():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    conn = FakeAtomicityConn()
    conn.db.multibagger_jobs.append({"job_id": "mb-1", "status": "running", "is_stale": True})
    conn.db.leases = [
        {"resource": "US_YFINANCE_HEAVY", "owner_job_id": "mb-1", "released_at": None},
        {"resource": "IN_SCREENER_HEAVY", "owner_job_id": "unrelated-job", "released_at": None},
    ]
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        reconcile_stale_multibagger_jobs()
    unrelated = next(l for l in conn.db.leases if l["owner_job_id"] == "unrelated-job")
    assert unrelated["released_at"] is None


# ─── reconcile_stale_daily_picks_jobs ───────────────────────────────────────
# (spec items 15-20; item 21 proven in Step 9)

def test_daily_picks_reconcile_stale_job_and_lease_released_together():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    conn = FakeAtomicityConn()
    conn.db.daily_picks_jobs.append({"job_id": "dp-1", "status": "running", "is_stale": True})
    conn.db.leases.append({"resource": "US_YFINANCE_HEAVY", "owner_job_id": "dp-1", "released_at": None})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_daily_picks_jobs()
    assert count == 1
    assert conn.db.daily_picks_jobs[0]["status"] == "interrupted"
    assert conn.db.leases[0]["released_at"] == "NOW"


def test_daily_picks_reconcile_ghost_job_without_lease_is_harmless_no_op():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    conn = FakeAtomicityConn()
    conn.db.daily_picks_jobs.append({"job_id": "dp-ghost", "status": "running", "is_stale": True})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_daily_picks_jobs()
    assert count == 1
    assert conn.db.daily_picks_jobs[0]["status"] == "interrupted"
    assert conn.db.leases == [], "lease release for a job with no lease row must be a harmless no-op"


def test_daily_picks_reconcile_no_stale_rows_returns_zero():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    conn = FakeAtomicityConn()
    conn.db.daily_picks_jobs.append({"job_id": "dp-1", "status": "running", "is_stale": False})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_daily_picks_jobs()
    assert count == 0
    assert conn.db.daily_picks_jobs[0]["status"] == "running"


def test_daily_picks_reconcile_job_update_exception_changes_nothing():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    conn = FakeAtomicityConn()
    conn.db.daily_picks_jobs.append({"job_id": "dp-1", "status": "running", "is_stale": True})
    conn.db.leases.append({"resource": "US_YFINANCE_HEAVY", "owner_job_id": "dp-1", "released_at": None})
    conn.raise_on_job_update = True
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_daily_picks_jobs()
    assert count == 0
    assert conn.db.daily_picks_jobs[0]["status"] == "running"
    assert conn.db.leases[0]["released_at"] is None


def test_daily_picks_reconcile_lease_exception_rolls_back_job_status_changes():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    conn = FakeAtomicityConn()
    conn.db.daily_picks_jobs.append({"job_id": "dp-1", "status": "running", "is_stale": True})
    conn.db.leases.append({"resource": "US_YFINANCE_HEAVY", "owner_job_id": "dp-1", "released_at": None})
    conn.raise_on_lease_release = True
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        count = reconcile_stale_daily_picks_jobs()
    assert count == 0
    assert conn.db.daily_picks_jobs[0]["status"] == "running", (
        "a lease-release failure must roll back the job-status change made earlier in the same transaction"
    )
    assert conn.db.leases[0]["released_at"] is None
    assert conn.rollback_count == 1


def test_daily_picks_reconcile_unrelated_job_and_lease_remain_unchanged():
    from services.postgres_store import reconcile_stale_daily_picks_jobs
    conn = FakeAtomicityConn()
    conn.db.daily_picks_jobs = [
        {"job_id": "dp-1", "status": "running", "is_stale": True},
        {"job_id": "dp-unrelated", "status": "running", "is_stale": False},
    ]
    conn.db.leases = [
        {"resource": "US_YFINANCE_HEAVY", "owner_job_id": "dp-1", "released_at": None},
        {"resource": "IN_SCREENER_HEAVY", "owner_job_id": "dp-unrelated", "released_at": None},
    ]
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn)):
        reconcile_stale_daily_picks_jobs()
    unrelated_job = next(r for r in conn.db.daily_picks_jobs if r["job_id"] == "dp-unrelated")
    unrelated_lease = next(l for l in conn.db.leases if l["owner_job_id"] == "dp-unrelated")
    assert unrelated_job["status"] == "running"
    assert unrelated_lease["released_at"] is None


# ─── compatibility (spec items 22-25) ───────────────────────────────────────

def test_timeout_values_remain_unchanged():
    import services.postgres_store as store
    assert store._MULTIBAGGER_ORPHAN_TIMEOUT_HOURS == 7
    assert store._DAILY_PICKS_ORPHAN_TIMEOUT_HOURS == 6


def test_last_error_text_remains_unchanged():
    import inspect
    from services.postgres_store import reconcile_stale_multibagger_jobs, reconcile_stale_daily_picks_jobs
    assert "reconciled: no heartbeat for over {_MULTIBAGGER_ORPHAN_TIMEOUT_HOURS}h" \
        in inspect.getsource(reconcile_stale_multibagger_jobs)
    assert "reconciled: no heartbeat for over {_DAILY_PICKS_ORPHAN_TIMEOUT_HOURS}h" \
        in inspect.getsource(reconcile_stale_daily_picks_jobs)


def test_both_reconciliation_functions_still_swallow_genuine_db_errors_and_return_zero():
    from services.postgres_store import reconcile_stale_multibagger_jobs, reconcile_stale_daily_picks_jobs
    with patch("services.postgres_store._get_pool",
               return_value=_raising_pool(Exception("db down"))):
        assert reconcile_stale_multibagger_jobs() == 0
        assert reconcile_stale_daily_picks_jobs() == 0


def test_claim_function_public_return_shape_is_still_a_plain_bool():
    from services.postgres_store import try_claim_queued_multibagger_job
    conn_true = FakeAtomicityConn()
    conn_true.db.multibagger_jobs.append({"job_id": "job-1", "status": "queued"})
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn_true)):
        result_true = try_claim_queued_multibagger_job("job-1")
    conn_false = FakeAtomicityConn()
    with patch("services.postgres_store._get_pool", return_value=_pool_for(conn_false)):
        result_false = try_claim_queued_multibagger_job("job-1")
    assert result_true is True and type(result_true) is bool
    assert result_false is False and type(result_false) is bool
