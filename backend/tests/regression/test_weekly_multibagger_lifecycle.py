"""
Product Integrity #009/#010 — weekly Multibagger refresh: durable
lifecycle, weekly-period idempotency, atomic job+lease reservation,
resumable staged worker, orphan recovery, legacy-schema repair, and
removal of the #008 "cooperative stop" coupling.

All tests are deterministic and fully mocked — no real DB, no external
providers, no live network, no Multibagger/Daily Picks generation runs.
"""
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest

_UNSET = object()


def _mock_pool(rowcount=None, row=_UNSET, rows=None, raise_on_connect=None):
    if raise_on_connect:
        mock_pool = MagicMock()
        mock_pool.connection.side_effect = raise_on_connect
        return mock_pool
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    if rowcount is not None:
        mock_cursor.rowcount = rowcount
    if row is not _UNSET:
        mock_cursor.fetchone.return_value = row
    if rows is not None:
        mock_cursor.fetchall.return_value = rows
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    return mock_pool


def _mock_pool_sequenced(*cursors, raise_on_connect=None):
    """A connection whose .execute() returns a different mock cursor each call, in order."""
    if raise_on_connect:
        mock_pool = MagicMock()
        mock_pool.connection.side_effect = raise_on_connect
        return mock_pool
    mock_conn = MagicMock()
    mock_conn.execute.side_effect = list(cursors)
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    return mock_pool, mock_conn


def _cursor(rowcount=None, row=_UNSET, rows=None):
    c = MagicMock()
    if rowcount is not None:
        c.rowcount = rowcount
    if row is not _UNSET:
        c.fetchone.return_value = row
    if rows is not None:
        c.fetchall.return_value = rows
    return c


# ─── Product Integrity #010 §8: legacy-schema repair (explicit, not IF NOT EXISTS) ─

def test_schema_sql_explicitly_repairs_the_market_check_constraint():
    """
    CREATE TABLE IF NOT EXISTS is a name-existence check only — it never
    repairs an already-existing table's constraints. Confirmed via direct
    production introspection that #008's original CHECK (market = 'US')
    was still live after #009 deployed. This test locks in the explicit
    DROP-then-ADD repair so it can never silently regress back to a no-op.
    """
    import services.postgres_store as store
    assert "DROP CONSTRAINT IF EXISTS multibagger_refresh_jobs_market_check" in store.SCHEMA_SQL
    assert "ADD CONSTRAINT multibagger_refresh_jobs_market_check" in store.SCHEMA_SQL
    assert "CHECK (market IN ('IN', 'US'))" in store.SCHEMA_SQL


def test_schema_sql_explicitly_repairs_the_active_job_index_predicate():
    """Same class of bug: CREATE INDEX IF NOT EXISTS kept the #008-era status='running'-only predicate."""
    import services.postgres_store as store
    assert "DROP INDEX IF EXISTS idx_multibagger_refresh_jobs_one_active_per_market" in store.SCHEMA_SQL
    assert "CREATE UNIQUE INDEX idx_multibagger_refresh_jobs_one_active_per_market" in store.SCHEMA_SQL


# ─── atomic job+lease reservation (Product Integrity #010 §9) ─────────────

def test_atomic_reserve_started_when_both_inserts_succeed():
    from services.postgres_store import try_reserve_multibagger_job_with_lease
    pool, conn = _mock_pool_sequenced(_cursor(rowcount=1), _cursor(rowcount=1))
    with patch("services.postgres_store._get_pool", return_value=pool):
        outcome = try_reserve_multibagger_job_with_lease(
            "job-1", "IN", "runner-1", "scheduled", "2026-07-18", "IN_SCREENER_HEAVY")
    assert outcome == "started"
    conn.commit.assert_called_once()
    conn.rollback.assert_not_called()


def test_atomic_reserve_lease_conflict_rolls_back_job_insert_too():
    """
    The core #010 fix: when the lease insert conflicts, the job insert from
    the SAME transaction must be rolled back — no orphaned queued row left
    behind with no lease backing it.
    """
    from services.postgres_store import try_reserve_multibagger_job_with_lease
    pool, conn = _mock_pool_sequenced(_cursor(rowcount=1), _cursor(rowcount=0))
    with patch("services.postgres_store._get_pool", return_value=pool):
        outcome = try_reserve_multibagger_job_with_lease(
            "job-1", "US", "runner-1", "scheduled", "2026-07-19", "US_YFINANCE_HEAVY")
    assert outcome == "resource_busy"
    conn.rollback.assert_called_once()
    conn.commit.assert_not_called()


def test_atomic_reserve_job_conflict_scheduled_reports_period_completed():
    from services.postgres_store import try_reserve_multibagger_job_with_lease
    pool, conn = _mock_pool_sequenced(_cursor(rowcount=0))
    with patch("services.postgres_store._get_pool", return_value=pool):
        outcome = try_reserve_multibagger_job_with_lease(
            "job-1", "IN", "runner-1", "scheduled", "2026-07-18", "IN_SCREENER_HEAVY")
    assert outcome == "already_completed_for_period"
    conn.rollback.assert_called_once()


def test_atomic_reserve_job_conflict_manual_reports_already_running():
    from services.postgres_store import try_reserve_multibagger_job_with_lease
    pool, conn = _mock_pool_sequenced(_cursor(rowcount=0))
    with patch("services.postgres_store._get_pool", return_value=pool):
        outcome = try_reserve_multibagger_job_with_lease(
            "job-1", "US", "runner-1", "manual", None, "US_YFINANCE_HEAVY")
    assert outcome == "already_running"


def test_atomic_reserve_raises_on_genuine_db_error():
    from services.postgres_store import try_reserve_multibagger_job_with_lease
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("connection refused"))):
        with pytest.raises(Exception):
            try_reserve_multibagger_job_with_lease("job-4", "US", "runner-1", "scheduled", "2026-07-19", "US_YFINANCE_HEAVY")


def test_atomic_daily_picks_reserve_started():
    from services.postgres_store import try_reserve_daily_picks_job_with_lease
    pool, conn = _mock_pool_sequenced(_cursor(rowcount=1), _cursor(rowcount=1))
    with patch("services.postgres_store._get_pool", return_value=pool):
        outcome = try_reserve_daily_picks_job_with_lease("job-dp-1", "US", "runner-1", "US_YFINANCE_HEAVY")
    assert outcome == "started"
    conn.commit.assert_called_once()


def test_atomic_daily_picks_reserve_resource_busy_rolls_back():
    from services.postgres_store import try_reserve_daily_picks_job_with_lease
    pool, conn = _mock_pool_sequenced(_cursor(rowcount=1), _cursor(rowcount=0))
    with patch("services.postgres_store._get_pool", return_value=pool):
        outcome = try_reserve_daily_picks_job_with_lease("job-dp-1", "IN", "runner-1", "IN_SCREENER_HEAVY")
    assert outcome == "resource_busy"
    conn.rollback.assert_called_once()


# ─── resumable claim (SELECT FOR UPDATE SKIP LOCKED) ───────────────────────

def test_claim_queued_job_succeeds_when_row_locked_successfully():
    from services.postgres_store import try_claim_queued_multibagger_job
    pool, conn = _mock_pool_sequenced(_cursor(row=("job-1",)), _cursor(rowcount=1))
    with patch("services.postgres_store._get_pool", return_value=pool):
        assert try_claim_queued_multibagger_job("job-1") is True


def test_claim_queued_job_fails_when_no_row_found():
    """SKIP LOCKED means a second concurrent claimant sees no row, not a block or an error."""
    from services.postgres_store import try_claim_queued_multibagger_job
    pool, conn = _mock_pool_sequenced(_cursor(row=None))
    with patch("services.postgres_store._get_pool", return_value=pool):
        assert try_claim_queued_multibagger_job("job-1") is False


def test_claim_queued_job_uses_for_update_skip_locked():
    import inspect
    from services.postgres_store import try_claim_queued_multibagger_job
    src = inspect.getsource(try_claim_queued_multibagger_job)
    assert "FOR UPDATE SKIP LOCKED" in src


# ─── staging and atomic promotion (Product Integrity #010 §11-12) ─────────

def test_stage_symbol_upserts_on_conflict():
    from services.postgres_store import stage_multibagger_symbol
    pool = _mock_pool(rowcount=1)
    with patch("services.postgres_store._get_pool", return_value=pool):
        stage_multibagger_symbol("job-1", "US", "AAPL", "refreshed", {"pe_ratio": 25.0}, False)
    sql = pool.connection().execute.call_args[0][0]
    assert "ON CONFLICT (job_id, symbol) DO UPDATE" in sql


def test_get_staged_symbols_returns_a_set():
    from services.postgres_store import get_staged_symbols
    pool = _mock_pool(rows=[("AAPL",), ("MSFT",)])
    with patch("services.postgres_store._get_pool", return_value=pool):
        result = get_staged_symbols("job-1")
    assert result == {"AAPL", "MSFT"}


def test_count_staged_outcomes_groups_by_outcome():
    from services.postgres_store import count_staged_outcomes
    pool = _mock_pool(rows=[("refreshed", 5000), ("skipped", 200), ("failed", 10)])
    with patch("services.postgres_store._get_pool", return_value=pool):
        result = count_staged_outcomes("job-1")
    assert result == {"refreshed": 5000, "skipped": 200, "failed": 10}


def test_promote_staged_symbols_only_reads_refreshed_outcome():
    from services.postgres_store import promote_staged_symbols
    pool = _mock_pool(rowcount=42)
    with patch("services.postgres_store._get_pool", return_value=pool):
        promoted = promote_staged_symbols("job-1", "US")
    sql = pool.connection().execute.call_args[0][0]
    assert "s.outcome = 'refreshed'" in sql
    assert promoted == 42


def test_promote_staged_symbols_builds_columns_from_canonical_field_map():
    """
    Uses fundamentals_cache.FIELD_MAP as the single source of truth instead
    of a hand-duplicated column list — a field added to FIELD_MAP is picked
    up automatically rather than silently promoting NULL for it.
    """
    from services.fundamentals_cache import FIELD_MAP
    from services.postgres_store import promote_staged_symbols
    pool = _mock_pool(rowcount=0)
    with patch("services.postgres_store._get_pool", return_value=pool):
        promote_staged_symbols("job-1", "IN")
    sql = pool.connection().execute.call_args[0][0]
    for column in FIELD_MAP.values():
        assert column in sql


def test_promote_never_deletes_or_overwrites_skipped_or_failed_symbols():
    """A temporary provider failure for one symbol must not erase its last-known-good cache row."""
    from services.postgres_store import promote_staged_symbols
    pool = _mock_pool(rowcount=0)
    with patch("services.postgres_store._get_pool", return_value=pool):
        promote_staged_symbols("job-1", "US")
    sql = pool.connection().execute.call_args[0][0]
    assert "DELETE" not in sql.upper()


def test_run_refresh_job_raises_on_incomplete_universe_traversal(monkeypatch):
    """
    STEP 13: a run may only complete once the full intended universe has
    been traversed. If staged rows are short of the summary's total (e.g.
    the process died mid-loop before returning), promotion/completion must
    not happen.
    """
    import api.routers.multibagger as mb_router
    monkeypatch.setattr(mb_router, "_refresh_state", {"US": {"running": False, "last_summary": None}})
    with patch("services.postgres_store.try_claim_queued_multibagger_job", return_value=True), \
         patch("services.postgres_store.get_staged_symbols", return_value=set()), \
         patch("services.postgres_store.record_multibagger_job_progress"), \
         patch("services.postgres_store.stage_multibagger_symbol"), \
         patch("services.us_fundamentals_refresh.run_full_refresh",
               return_value={"total": 5300, "refreshed": 100, "skipped": 0, "failed": 0, "resumed_from_checkpoint": 0}), \
         patch("services.postgres_store.count_staged_outcomes", return_value={"refreshed": 100}), \
         patch("services.postgres_store.promote_staged_symbols") as mock_promote, \
         patch("services.postgres_store.mark_multibagger_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_multibagger_job_failed", return_value=True) as mock_failed, \
         patch("services.postgres_store.release_heavy_workload_lease"):
        mb_router._run_refresh_job("US", "job-incomplete")
    mock_promote.assert_not_called()
    mock_completed.assert_not_called()
    mock_failed.assert_called_once()
    assert "incomplete universe traversal" in mock_failed.call_args[0][1]


def test_run_refresh_job_promotes_and_completes_on_full_traversal(monkeypatch):
    import api.routers.multibagger as mb_router
    monkeypatch.setattr(mb_router, "_refresh_state", {"IN": {"running": False, "last_summary": None}})
    with patch("services.postgres_store.try_claim_queued_multibagger_job", return_value=True), \
         patch("services.postgres_store.get_staged_symbols", return_value=set()), \
         patch("services.postgres_store.record_multibagger_job_progress"), \
         patch("services.postgres_store.stage_multibagger_symbol"), \
         patch("services.fundamentals_refresh.run_full_refresh",
               return_value={"total": 2300, "refreshed": 2200, "skipped": 90, "failed": 10, "resumed_from_checkpoint": 0}), \
         patch("services.postgres_store.count_staged_outcomes",
               return_value={"refreshed": 2200, "skipped": 90, "failed": 10}), \
         patch("services.postgres_store.promote_staged_symbols", return_value=2200) as mock_promote, \
         patch("services.postgres_store.mark_multibagger_job_completed", return_value=True) as mock_completed, \
         patch("services.postgres_store.release_heavy_workload_lease"):
        mb_router._run_refresh_job("IN", "job-complete")
    mock_promote.assert_called_once_with("job-complete", "IN")
    mock_completed.assert_called_once_with("job-complete")


def test_run_refresh_job_skips_work_when_claim_fails(monkeypatch):
    """A job that could not be claimed (already claimed by another worker, or not queued) must not run."""
    import api.routers.multibagger as mb_router
    with patch("services.postgres_store.try_claim_queued_multibagger_job", return_value=False), \
         patch("services.us_fundamentals_refresh.run_full_refresh") as mock_refresh:
        mb_router._run_refresh_job("US", "job-unclaimed")
    mock_refresh.assert_not_called()


def test_run_refresh_job_resumes_with_already_staged_symbols(monkeypatch):
    import api.routers.multibagger as mb_router
    monkeypatch.setattr(mb_router, "_refresh_state", {"US": {"running": False, "last_summary": None}})
    with patch("services.postgres_store.try_claim_queued_multibagger_job", return_value=True), \
         patch("services.postgres_store.get_staged_symbols", return_value={"AAPL", "MSFT"}), \
         patch("services.postgres_store.record_multibagger_job_progress"), \
         patch("services.us_fundamentals_refresh.run_full_refresh") as mock_refresh, \
         patch("services.postgres_store.count_staged_outcomes", return_value={"refreshed": 5300}), \
         patch("services.postgres_store.promote_staged_symbols", return_value=5300), \
         patch("services.postgres_store.mark_multibagger_job_completed", return_value=True), \
         patch("services.postgres_store.release_heavy_workload_lease"):
        mock_refresh.return_value = {"total": 5300, "refreshed": 5300, "skipped": 0, "failed": 0, "resumed_from_checkpoint": 2}
        mb_router._run_refresh_job("US", "job-resume")
    _, kwargs = mock_refresh.call_args
    assert kwargs["already_staged"] == {"AAPL", "MSFT"}


# ─── durable lifecycle transitions ─────────────────────────────────────────

def test_mark_running_transitions_queued_to_running():
    from services.postgres_store import mark_multibagger_job_running
    pool = _mock_pool(rowcount=1)
    with patch("services.postgres_store._get_pool", return_value=pool):
        mark_multibagger_job_running("job-1")
    sql = pool.connection().execute.call_args[0][0]
    assert "SET status = 'running'" in sql


def test_record_progress_updates_heartbeat_too():
    from services.postgres_store import record_multibagger_job_progress
    pool = _mock_pool(rowcount=1)
    with patch("services.postgres_store._get_pool", return_value=pool):
        record_multibagger_job_progress("job-1", 500, 2300)
    sql = pool.connection().execute.call_args[0][0]
    assert "processed" in sql and "last_progress_at" in sql and "last_runner_heartbeat_at" in sql


def test_mark_completed_returns_true_on_successful_write():
    from services.postgres_store import mark_multibagger_job_completed
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)):
        assert mark_multibagger_job_completed("job-1") is True


def test_mark_completed_returns_false_on_write_failure():
    """Terminal-write-failure handling: a DB error must not be swallowed into a false 'completed'."""
    from services.postgres_store import mark_multibagger_job_completed
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert mark_multibagger_job_completed("job-1") is False


def test_mark_failed_returns_true_on_successful_write():
    from services.postgres_store import mark_multibagger_job_failed
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)):
        assert mark_multibagger_job_failed("job-1", "provider error") is True


def test_mark_failed_returns_false_when_failure_write_itself_fails():
    """Refresh exception plus failure-write failure — both terminal writes can fail; caller must know."""
    from services.postgres_store import mark_multibagger_job_failed
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert mark_multibagger_job_failed("job-1", "provider error") is False


def test_get_latest_multibagger_job_swallows_errors_returns_none():
    from services.postgres_store import get_latest_multibagger_job
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert get_latest_multibagger_job("US") is None


def test_get_last_successful_refresh_swallows_errors_returns_none():
    from services.postgres_store import get_last_successful_multibagger_refresh
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert get_last_successful_multibagger_refresh("IN") is None


# ─── orphan / restart recovery ─────────────────────────────────────────────

def test_reconcile_stale_jobs_reclassifies_and_releases_lease():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    mock_conn = MagicMock()
    stale_cursor = MagicMock()
    stale_cursor.fetchall.return_value = [("job-orphan-1",)]
    mock_conn.execute.return_value = stale_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        count = reconcile_stale_multibagger_jobs()
    assert count == 1
    assert mock_conn.execute.call_count == 2


def test_reconcile_stale_jobs_no_op_when_nothing_stale():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    mock_conn = MagicMock()
    empty_cursor = MagicMock()
    empty_cursor.fetchall.return_value = []
    mock_conn.execute.return_value = empty_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        count = reconcile_stale_multibagger_jobs()
    assert count == 0


def test_reconcile_stale_jobs_swallows_db_errors():
    from services.postgres_store import reconcile_stale_multibagger_jobs
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert reconcile_stale_multibagger_jobs() == 0


def test_reconcile_uses_timeout_longer_than_a_credible_symbol_operation():
    import services.postgres_store as store
    assert store._MULTIBAGGER_ORPHAN_TIMEOUT_HOURS >= 6  # longer than the ~5-6h full US refresh


def test_reconciliation_wired_into_startup():
    """Backend startup must call the reconciliation pass exactly once per boot."""
    import inspect
    import api.main as main_mod
    assert "reconcile_stale_multibagger_jobs()" in inspect.getsource(main_mod)


def test_status_endpoint_does_not_call_reconciliation(mb_client):
    """GET /status must never mutate lifecycle state — verified behaviorally, not just by source grep."""
    client, mb_router = mb_client
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None), \
         patch("services.postgres_store.get_latest_multibagger_job", return_value=None), \
         patch("services.postgres_store.get_last_successful_multibagger_refresh", return_value=None), \
         patch("services.postgres_store.reconcile_stale_multibagger_jobs") as mock_reconcile:
        client.get("/api/multibagger/status", params={"market": "US"})
    mock_reconcile.assert_not_called()


# ─── heavy-workload lease arbitration (direct function tests) ─────────────

def test_acquire_lease_returns_true_on_insert():
    from services.postgres_store import try_acquire_heavy_workload_lease
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)):
        assert try_acquire_heavy_workload_lease("US_YFINANCE_HEAVY", "multibagger", "job-1", "US") is True


def test_acquire_lease_returns_false_when_already_held():
    from services.postgres_store import try_acquire_heavy_workload_lease
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=0)):
        assert try_acquire_heavy_workload_lease("US_YFINANCE_HEAVY", "daily_picks", "job-2", "US") is False


def test_acquire_lease_raises_on_db_error_fails_closed():
    from services.postgres_store import try_acquire_heavy_workload_lease
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        with pytest.raises(Exception):
            try_acquire_heavy_workload_lease("IN_SCREENER_HEAVY", "multibagger", "job-3", "IN")


def test_release_lease_is_idempotent():
    from services.postgres_store import release_heavy_workload_lease
    pool = _mock_pool(rowcount=0)
    with patch("services.postgres_store._get_pool", return_value=pool):
        release_heavy_workload_lease("job-1")


def test_has_active_lease_fails_closed_on_db_error():
    from services.postgres_store import has_active_heavy_workload_lease
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert has_active_heavy_workload_lease("US_YFINANCE_HEAVY") is True


def test_different_resource_keys_are_independent():
    from services.postgres_store import try_acquire_heavy_workload_lease
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)):
        us_ok = try_acquire_heavy_workload_lease("US_YFINANCE_HEAVY", "daily_picks", "job-us", "US")
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)):
        in_ok = try_acquire_heavy_workload_lease("IN_SCREENER_HEAVY", "multibagger", "job-in", "IN")
    assert us_ok is True and in_ok is True


# ─── POST /api/multibagger/refresh — endpoint contract ────────────────────

@pytest.fixture
def mb_client(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", "test-secret")
    monkeypatch.setenv("USE_POSTGRES", "1")
    import importlib
    import api.routers.multibagger as mb_router
    importlib.reload(mb_router)
    from api.main import app
    from fastapi.testclient import TestClient
    yield TestClient(app), mb_router


def _in_scheduled_day_now():
    return datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)  # Saturday IST


def _us_scheduled_day_now():
    return datetime(2026, 7, 19, 8, 0, tzinfo=timezone.utc)  # Sunday ET (single fixed cron)


def test_trigger_source_is_required_no_default(mb_client):
    """No default trigger_source — a caller must explicitly say scheduled or manual."""
    client, mb_router = mb_client
    resp = client.post("/api/multibagger/refresh", params={"market": "IN"},
                        headers={"x-secret": "test-secret"})
    assert resp.status_code == 422  # FastAPI validation error: missing required query param


def test_refresh_requires_durable_state(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", "test-secret")
    monkeypatch.delenv("USE_POSTGRES", raising=False)
    import importlib
    import api.routers.multibagger as mb_router
    importlib.reload(mb_router)
    from api.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                        headers={"x-secret": "test-secret"})
    assert resp.status_code == 503
    assert resp.json()["status"] == "durable_state_unavailable"


def test_scheduled_call_wrong_weekday_rejected(mb_client):
    client, mb_router = mb_client
    wednesday = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    with patch("api.routers.multibagger.datetime") as mock_dt:
        mock_dt.now.return_value = wednesday
        resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 422
    assert resp.json()["status"] == "outside_scheduled_day"


def test_scheduled_call_accepted_anytime_on_the_correct_day(mb_client):
    """Day-only acceptance (#010): a delayed dispatch late in the day is still accepted, not just a narrow window."""
    client, mb_router = mb_client
    late_saturday_ist = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)  # 3:30 PM IST Saturday
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job_with_lease", return_value="started"), \
         patch.object(mb_router, "_run_refresh_job"):
        mock_dt.now.return_value = late_saturday_ist
        resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 202


def test_manual_call_allowed_on_any_day(mb_client):
    client, mb_router = mb_client
    wednesday = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job_with_lease", return_value="started"), \
         patch.object(mb_router, "_run_refresh_job"):
        mock_dt.now.return_value = wednesday
        resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "manual"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["trigger_source"] == "manual"
    assert body["scheduled_period_key"] is None


def test_scheduled_call_on_correct_day_starts_job(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job_with_lease", return_value="started"), \
         patch.object(mb_router, "_run_refresh_job"):
        mock_dt.now.return_value = _in_scheduled_day_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "started"
    assert body["scheduled_period_key"] == "2026-07-18"


def test_duplicate_scheduled_candidate_same_period_is_safe_noop(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job_with_lease",
               return_value="already_completed_for_period"):
        mock_dt.now.return_value = _us_scheduled_day_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_completed_for_period"


def test_manual_duplicate_delivery_returns_already_running(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job_with_lease", return_value="already_running"):
        mock_dt.now.return_value = _us_scheduled_day_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "manual"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 409
    assert resp.json()["status"] == "already_running"


def test_resource_busy_when_lease_already_held(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job_with_lease", return_value="resource_busy"):
        mock_dt.now.return_value = _us_scheduled_day_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 409
    assert resp.json()["status"] == "resource_busy"
    assert resp.json()["resource"] == "US_YFINANCE_HEAVY"


def test_resource_busy_response_does_not_start_background_work(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job_with_lease", return_value="resource_busy"), \
         patch.object(mb_router, "_run_refresh_job") as mock_run:
        mock_dt.now.return_value = _us_scheduled_day_now()
        client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "scheduled"},
                     headers={"x-secret": "test-secret"})
    mock_run.assert_not_called()


def test_already_running_in_memory_short_circuits_before_db_calls(mb_client):
    client, mb_router = mb_client
    mb_router._refresh_state["IN"]["running"] = True
    try:
        with patch("api.routers.multibagger.datetime") as mock_dt, \
             patch("services.postgres_store.try_reserve_multibagger_job_with_lease") as mock_reserve:
            mock_dt.now.return_value = _in_scheduled_day_now()
            resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                                headers={"x-secret": "test-secret"})
        assert resp.status_code == 409
        mock_reserve.assert_not_called()
    finally:
        mb_router._refresh_state["IN"]["running"] = False


def test_india_gets_durable_treatment_same_as_us():
    """No market-specific carve-out remains — both markets go through the same reservation path."""
    import inspect
    from api.routers import multibagger as mb_router
    src = inspect.getsource(mb_router.trigger_refresh)
    assert 'if market == "IN":' not in src


# ─── GET /status — durable-first, truthful on restart ─────────────────────

def test_status_durable_state_unavailable_when_postgres_disabled(monkeypatch):
    monkeypatch.delenv("USE_POSTGRES", raising=False)
    import importlib
    import api.routers.multibagger as mb_router
    importlib.reload(mb_router)
    from api.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None):
        resp = client.get("/api/multibagger/status", params={"market": "IN"})
    assert resp.status_code == 200
    assert resp.json()["durable_state_available"] is False


def test_status_does_not_fabricate_running_false_after_restart(mb_client):
    client, mb_router = mb_client
    mb_router._refresh_state["US"]["running"] = False
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None), \
         patch("services.postgres_store.get_latest_multibagger_job",
               return_value={"job_id": "job-x", "status": "running", "trigger_source": "scheduled",
                             "scheduled_period_key": "2026-07-19", "processed": 1200, "total": 5300,
                             "last_error": None, "last_runner_heartbeat_at": None, "last_progress_at": None}), \
         patch("services.postgres_store.get_last_successful_multibagger_refresh", return_value=None):
        resp = client.get("/api/multibagger/status", params={"market": "US"})
    body = resp.json()
    assert body["running"] is True
    assert body["job_status"] == "running"


def test_status_reports_stale_when_last_success_older_than_threshold(mb_client):
    client, mb_router = mb_client
    old_completion = datetime.now(timezone.utc) - timedelta(days=10)
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None), \
         patch("services.postgres_store.get_latest_multibagger_job", return_value=None), \
         patch("services.postgres_store.get_last_successful_multibagger_refresh",
               return_value={"job_id": "job-old", "completed_at": old_completion}):
        resp = client.get("/api/multibagger/status", params={"market": "IN"})
    body = resp.json()
    assert body["is_stale"] is True
    assert body["last_successful_refresh_at"] is not None


def test_status_stale_boundary_uses_precise_timedelta_not_integer_days():
    """9 days and 23 hours must still be considered stale-boundary-adjacent correctly, not truncated to '9 days'."""
    import api.routers.multibagger as mb_router
    just_under_threshold = datetime.now(timezone.utc) - timedelta(days=9, hours=23, minutes=59)
    with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None), \
         patch("services.postgres_store.get_latest_multibagger_job", return_value=None), \
         patch("services.postgres_store.get_last_successful_multibagger_refresh",
               return_value={"job_id": "job-edge", "completed_at": just_under_threshold}):
        result = mb_router.refresh_status(market="US")
    assert result["is_stale"] is True  # 9d23h59m > 9 days exactly


def test_status_reports_fresh_when_recent_success():
    import api.routers.multibagger as mb_router
    recent_completion = datetime.now(timezone.utc) - timedelta(days=2)
    with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None), \
         patch("services.postgres_store.get_latest_multibagger_job", return_value=None), \
         patch("services.postgres_store.get_last_successful_multibagger_refresh",
               return_value={"job_id": "job-recent", "completed_at": recent_completion}):
        result = mb_router.refresh_status(market="US")
    assert result["is_stale"] is False


def test_status_no_successful_refresh_is_stale_by_default():
    import api.routers.multibagger as mb_router
    with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None), \
         patch("services.postgres_store.get_latest_multibagger_job", return_value=None), \
         patch("services.postgres_store.get_last_successful_multibagger_refresh", return_value=None):
        result = mb_router.refresh_status(market="IN")
    assert result["is_stale"] is True
    assert result["last_successful_refresh_at"] is None


def test_status_advertises_weekly_frequency_and_schedule_hint():
    import api.routers.multibagger as mb_router
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("USE_POSTGRES", None)
        with patch("services.fundamentals_cache.ensure_table"), \
             patch("services.fundamentals_cache.last_refreshed", return_value=None):
            in_result = mb_router.refresh_status(market="IN")
            us_result = mb_router.refresh_status(market="US")
    assert in_result["schedule_frequency"] == "weekly"
    assert "Saturday" in in_result["next_scheduled_refresh_hint"]
    assert us_result["schedule_frequency"] == "weekly"
    assert "08:00 UTC" in us_result["next_scheduled_refresh_hint"]
    assert "EST" in us_result["next_scheduled_refresh_hint"] and "EDT" in us_result["next_scheduled_refresh_hint"]


# ─── regression: the #008 stop-coupling stays fully removed ───────────────

def test_no_stop_coupling_symbols_remain_anywhere_in_the_backend():
    import inspect
    import api.routers.multibagger as mb_router
    import api.routers.picks as picks_router
    import services.us_fundamentals_refresh as us_refresh_module

    for module in (mb_router, picks_router, us_refresh_module):
        src = inspect.getsource(module)
        assert "request_us_stop" not in src
        assert "_stop_requested" not in src
        assert "should_stop=" not in src
        assert "should_stop:" not in src


def test_premarket_finalize_never_touches_multibagger_module():
    import inspect
    import api.routers.picks as picks_router
    src = inspect.getsource(picks_router.premarket_finalize)
    assert "multibagger" not in src.lower()


def test_stopped_early_no_longer_a_success_outcome_in_new_code_paths():
    import inspect
    from services.postgres_store import mark_multibagger_job_completed
    sig = inspect.signature(mark_multibagger_job_completed)
    assert "stopped_early" not in sig.parameters


# ─── India/US isolation and unrelated-scope regression ────────────────────

def test_in_and_us_use_independent_heavy_resources():
    from api.routers.multibagger import _RESOURCE_FOR_MARKET
    assert _RESOURCE_FOR_MARKET["IN"] == "IN_SCREENER_HEAVY"
    assert _RESOURCE_FOR_MARKET["US"] == "US_YFINANCE_HEAVY"
    assert _RESOURCE_FOR_MARKET["IN"] != _RESOURCE_FOR_MARKET["US"]


def test_daily_picks_generation_logic_untouched_by_this_release():
    import inspect
    from services.daily_picks import generate_picks
    sig = inspect.signature(generate_picks)
    assert "market" in sig.parameters
    assert "job_id" in sig.parameters
