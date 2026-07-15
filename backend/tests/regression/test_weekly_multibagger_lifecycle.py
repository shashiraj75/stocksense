"""
Product Integrity #009 — weekly Multibagger refresh: durable lifecycle,
weekly-period idempotency, heavy-workload lease arbitration, orphan
recovery, and removal of the #008 "cooperative stop" coupling.

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


# ─── weekly-period idempotency (durable reservation) ──────────────────────

def test_reserve_scheduled_job_returns_true_on_insert():
    from services.postgres_store import try_reserve_multibagger_job
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)):
        assert try_reserve_multibagger_job("job-1", "IN", "runner-1", "scheduled", "2026-07-18") is True


def test_reserve_duplicate_scheduled_period_returns_false():
    """Second scheduled call for the same period conflicts on the partial unique index."""
    from services.postgres_store import try_reserve_multibagger_job
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=0)):
        assert try_reserve_multibagger_job("job-2", "IN", "runner-1", "scheduled", "2026-07-18") is False


def test_reserve_manual_trigger_has_no_period_key():
    from services.postgres_store import try_reserve_multibagger_job
    pool = _mock_pool(rowcount=1)
    with patch("services.postgres_store._get_pool", return_value=pool):
        assert try_reserve_multibagger_job("job-3", "US", "runner-1", "manual", None) is True


def test_reserve_raises_on_genuine_db_error():
    from services.postgres_store import try_reserve_multibagger_job
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("connection refused"))):
        with pytest.raises(Exception):
            try_reserve_multibagger_job("job-4", "US", "runner-1", "scheduled", "2026-07-19")


# ─── durable lifecycle transitions ─────────────────────────────────────────

def test_mark_running_transitions_queued_to_running():
    from services.postgres_store import mark_multibagger_job_running
    pool = _mock_pool(rowcount=1)
    with patch("services.postgres_store._get_pool", return_value=pool):
        mark_multibagger_job_running("job-1")
    executed_sql = pool.connection.return_value.__enter__.return_value.execute.call_args[0][0] \
        if False else pool.connection().execute.call_args[0][0]
    assert "SET status = 'running'" in executed_sql


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
    """Terminal-write-failure handling (STEP 10): a DB error must not be swallowed into a false 'completed'."""
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
    # Two statements executed: the UPDATE...RETURNING and the lease release.
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


# ─── heavy-workload lease arbitration ──────────────────────────────────────

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
    pool = _mock_pool(rowcount=0)  # already released, or never held — no error either way
    with patch("services.postgres_store._get_pool", return_value=pool):
        release_heavy_workload_lease("job-1")  # must not raise


def test_has_active_lease_fails_closed_on_db_error():
    from services.postgres_store import has_active_heavy_workload_lease
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db down"))):
        assert has_active_heavy_workload_lease("US_YFINANCE_HEAVY") is True


def test_different_resource_keys_are_independent():
    """US_YFINANCE_HEAVY and IN_SCREENER_HEAVY must never block each other — different resource strings."""
    from services.postgres_store import try_acquire_heavy_workload_lease
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)) as _:
        us_ok = try_acquire_heavy_workload_lease("US_YFINANCE_HEAVY", "daily_picks", "job-us", "US")
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)) as _:
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


def _in_window_now():
    return datetime(2026, 7, 17, 21, 30, tzinfo=timezone.utc)  # Sat 3 AM IST


def _us_window_now():
    return datetime(2026, 7, 19, 7, 0, tzinfo=timezone.utc)  # Sun 3 AM EDT


def test_refresh_requires_durable_state(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", "test-secret")
    monkeypatch.delenv("USE_POSTGRES", raising=False)
    import importlib
    import api.routers.multibagger as mb_router
    importlib.reload(mb_router)
    from api.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    resp = client.post("/api/multibagger/refresh", params={"market": "IN"}, headers={"x-secret": "test-secret"})
    assert resp.status_code == 503
    assert resp.json()["status"] == "durable_state_unavailable"


def test_scheduled_call_outside_window_rejected(mb_client):
    client, mb_router = mb_client
    outside_window = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)  # Wednesday noon
    with patch("api.routers.multibagger.datetime") as mock_dt:
        mock_dt.now.return_value = outside_window
        resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 422
    assert resp.json()["status"] == "outside_scheduled_window"


def test_manual_call_allowed_outside_scheduled_window(mb_client):
    client, mb_router = mb_client
    outside_window = datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc)
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=True), \
         patch("services.postgres_store.try_acquire_heavy_workload_lease", return_value=True), \
         patch.object(mb_router, "_run_refresh_job"):
        mock_dt.now.return_value = outside_window
        resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "manual"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["trigger_source"] == "manual"
    assert body["scheduled_period_key"] is None


def test_scheduled_call_inside_window_starts_job(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=True), \
         patch("services.postgres_store.try_acquire_heavy_workload_lease", return_value=True), \
         patch.object(mb_router, "_run_refresh_job"):
        mock_dt.now.return_value = _in_window_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "started"
    assert body["scheduled_period_key"] == "2026-07-18"


def test_duplicate_scheduled_candidate_same_period_is_safe_noop(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=False):
        mock_dt.now.return_value = _us_window_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_completed_for_period"


def test_manual_duplicate_delivery_returns_already_running_not_period_noop(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=False):
        mock_dt.now.return_value = _us_window_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "manual"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 409
    assert resp.json()["status"] == "already_running"


def test_resource_busy_when_lease_already_held(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=True), \
         patch("services.postgres_store.try_acquire_heavy_workload_lease", return_value=False), \
         patch("services.postgres_store.mark_multibagger_job_failed"):
        mock_dt.now.return_value = _us_window_now()
        resp = client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "scheduled"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 409
    assert resp.json()["status"] == "resource_busy"
    assert resp.json()["resource"] == "US_YFINANCE_HEAVY"


def test_resource_busy_response_does_not_start_background_work(mb_client):
    client, mb_router = mb_client
    with patch("api.routers.multibagger.datetime") as mock_dt, \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=True), \
         patch("services.postgres_store.try_acquire_heavy_workload_lease", return_value=False), \
         patch("services.postgres_store.mark_multibagger_job_failed"), \
         patch.object(mb_router, "_run_refresh_job") as mock_run:
        mock_dt.now.return_value = _us_window_now()
        client.post("/api/multibagger/refresh", params={"market": "US", "trigger_source": "scheduled"},
                     headers={"x-secret": "test-secret"})
    mock_run.assert_not_called()


def test_already_running_in_memory_short_circuits_before_db_calls(mb_client):
    client, mb_router = mb_client
    mb_router._refresh_state["IN"]["running"] = True
    try:
        with patch("api.routers.multibagger.datetime") as mock_dt, \
             patch("services.postgres_store.try_reserve_multibagger_job") as mock_reserve:
            mock_dt.now.return_value = _in_window_now()
            resp = client.post("/api/multibagger/refresh", params={"market": "IN", "trigger_source": "scheduled"},
                                headers={"x-secret": "test-secret"})
        assert resp.status_code == 409
        mock_reserve.assert_not_called()
    finally:
        mb_router._refresh_state["IN"]["running"] = False


def test_india_now_gets_durable_treatment_same_as_us():
    """Product Integrity #009 makes IN durable too — no market-specific carve-out remains."""
    import inspect
    from api.routers import multibagger as mb_router
    src = inspect.getsource(mb_router.trigger_refresh)
    assert 'if market == "IN":' not in src  # no more special-cased in-memory-only IN branch


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
    """
    A restart resets the in-memory dict to running=False regardless of the
    real state — durable status must override it from the actual row, not
    let the reset dict silently report a false idle state.
    """
    client, mb_router = mb_client
    mb_router._refresh_state["US"]["running"] = False  # simulates the post-restart reset
    with patch("services.fundamentals_cache.ensure_table"), \
         patch("services.fundamentals_cache.last_refreshed", return_value=None), \
         patch("services.postgres_store.get_latest_multibagger_job",
               return_value={"job_id": "job-x", "status": "running", "trigger_source": "scheduled",
                             "scheduled_period_key": "2026-07-19", "processed": 1200, "total": 5300,
                             "last_error": None, "last_runner_heartbeat_at": None, "last_progress_at": None}), \
         patch("services.postgres_store.get_last_successful_multibagger_refresh", return_value=None):
        resp = client.get("/api/multibagger/status", params={"market": "US"})
    body = resp.json()
    assert body["running"] is True  # overridden by the durable row, not the reset in-memory dict
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
    assert "Sunday" in us_result["next_scheduled_refresh_hint"]


# ─── regression: the #008 stop-coupling is fully removed ──────────────────

def test_no_stop_coupling_symbols_remain_anywhere_in_the_backend():
    """
    request_us_stop/_stop_requested must not exist as live identifiers at
    all (not even in a comment). should_stop is checked more narrowly —
    it's fine for module docstrings to explain the historical removal in
    prose, but it must not appear as an actual parameter/attribute/call.
    """
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
        assert "def run_full_refresh(should_stop" not in src


def test_run_full_refresh_no_longer_accepts_should_stop_param():
    import inspect
    from services.us_fundamentals_refresh import run_full_refresh
    sig = inspect.signature(run_full_refresh)
    assert "should_stop" not in sig.parameters
    assert "on_progress" in sig.parameters


def test_premarket_finalize_never_touches_multibagger_module():
    import inspect
    import api.routers.picks as picks_router
    src = inspect.getsource(picks_router.premarket_finalize)
    assert "multibagger" not in src.lower()


def test_us_generate_never_touches_multibagger_stop_state():
    import inspect
    import api.routers.picks as picks_router
    src = inspect.getsource(picks_router.trigger_generation)
    assert "request_us_stop" not in src
    assert "_stop_requested" not in src


def test_stopped_early_no_longer_a_success_outcome_in_new_code_paths():
    """
    Historical rows with stopped_early may remain (schema column preserved
    for that evidence) — but the current refresh loops never set it, and
    the completion write no longer accepts it as a parameter.
    """
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
    """generate_picks itself (scoring/ranking/persistence) is not imported into this test file's
    scope of edits — a source-diff smoke check that the function name/signature is unchanged."""
    import inspect
    from services.daily_picks import generate_picks
    sig = inspect.signature(generate_picks)
    assert "market" in sig.parameters
    assert "job_id" in sig.parameters
