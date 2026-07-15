"""
Product Integrity Workstream #008 — US workload overlap protection.

Verifies the fail-closed concurrency guard between US Daily Picks (base
generation + Premarket Finalizer) and the US Multibagger fundamentals
refresh, added because cron separation alone (see
.github/workflows/multibagger_refresh_us.yml) is not sufficient — GitHub
Actions can dispatch late.

All tests are deterministic and fully mocked — no real DB, no external
providers, no live network, no Daily Picks/Multibagger runs.
"""
import os
import uuid
from unittest.mock import MagicMock, patch

import pytest


_UNSET = object()


def _mock_pool(rowcount=None, row=_UNSET, raise_on_connect=None):
    mock_conn = MagicMock()
    if raise_on_connect:
        mock_pool = MagicMock()
        mock_pool.connection.side_effect = raise_on_connect
        return mock_pool
    mock_cursor = MagicMock()
    if rowcount is not None:
        mock_cursor.rowcount = rowcount
    if row is not _UNSET:
        mock_cursor.fetchone.return_value = row
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn
    return mock_pool


# ─── postgres_store: durable multibagger job state ────────────────────────

def test_try_reserve_multibagger_job_returns_true_on_insert():
    from services.postgres_store import try_reserve_multibagger_job
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=1)):
        assert try_reserve_multibagger_job("job-1", "US", "runner-1") is True


def test_try_reserve_multibagger_job_returns_false_on_conflict():
    from services.postgres_store import try_reserve_multibagger_job
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(rowcount=0)):
        assert try_reserve_multibagger_job("job-2", "US", "runner-1") is False


def test_try_reserve_multibagger_job_raises_on_genuine_db_error():
    from services.postgres_store import try_reserve_multibagger_job
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("connection refused"))):
        with pytest.raises(Exception):
            try_reserve_multibagger_job("job-3", "US", "runner-1")


def test_has_active_daily_picks_job_or_unknown_true_when_row_exists():
    from services.postgres_store import has_active_daily_picks_job_or_unknown
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(row=(1,))):
        assert has_active_daily_picks_job_or_unknown("US") is True


def test_has_active_daily_picks_job_or_unknown_false_when_no_row():
    from services.postgres_store import has_active_daily_picks_job_or_unknown
    with patch("services.postgres_store._get_pool", return_value=_mock_pool(row=None)):
        assert has_active_daily_picks_job_or_unknown("US") is False


def test_has_active_daily_picks_job_or_unknown_fails_closed_on_db_error():
    """A lookup failure must be treated as a possible conflict, not as 'no conflict'."""
    from services.postgres_store import has_active_daily_picks_job_or_unknown
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db unreachable"))):
        assert has_active_daily_picks_job_or_unknown("US") is True


def test_get_active_multibagger_job_swallows_db_errors_returns_none():
    """Matches get_active_daily_picks_job's existing display-only contract."""
    from services.postgres_store import get_active_multibagger_job
    with patch("services.postgres_store._get_pool",
               return_value=_mock_pool(raise_on_connect=Exception("db unreachable"))):
        assert get_active_multibagger_job("US") is None


# ─── POST /api/multibagger/refresh?market=US: yields to Daily Picks ───────

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


def test_us_refresh_requires_durable_state(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", "test-secret")
    monkeypatch.delenv("USE_POSTGRES", raising=False)
    import importlib
    import api.routers.multibagger as mb_router
    importlib.reload(mb_router)
    from api.main import app
    from fastapi.testclient import TestClient
    client = TestClient(app)

    resp = client.post("/api/multibagger/refresh", params={"market": "US"},
                        headers={"x-secret": "test-secret"})
    assert resp.status_code == 503
    assert resp.json()["status"] == "durable_job_state_unavailable"


def test_us_refresh_defers_when_daily_picks_active(mb_client):
    client, mb_router = mb_client
    with patch("services.postgres_store.has_active_daily_picks_job_or_unknown", return_value=True):
        resp = client.post("/api/multibagger/refresh", params={"market": "US"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 409
    assert resp.json()["status"] == "deferred_daily_picks_priority"


def test_us_refresh_defers_when_conflict_check_itself_fails(mb_client):
    """Fail-closed: an exception from the conflict check must also refuse to start."""
    client, mb_router = mb_client
    with patch("services.postgres_store.has_active_daily_picks_job_or_unknown",
               side_effect=Exception("db down")):
        resp = client.post("/api/multibagger/refresh", params={"market": "US"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 503


def test_us_refresh_starts_when_no_conflict(mb_client):
    client, mb_router = mb_client
    with patch("services.postgres_store.has_active_daily_picks_job_or_unknown", return_value=False), \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=True), \
         patch.object(mb_router, "_run_refresh_job"):
        resp = client.post("/api/multibagger/refresh", params={"market": "US"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 202
    assert resp.json()["status"] == "started"


def test_us_refresh_already_running_in_memory_is_409(mb_client):
    client, mb_router = mb_client
    mb_router._refresh_state["US"]["running"] = True
    try:
        resp = client.post("/api/multibagger/refresh", params={"market": "US"},
                            headers={"x-secret": "test-secret"})
        assert resp.status_code == 409
        assert resp.json()["status"] == "already_running"
    finally:
        mb_router._refresh_state["US"]["running"] = False


def test_us_refresh_duplicate_delivery_stays_idempotent(mb_client):
    """A second POST while the durable reservation is already held returns
    409 already_running rather than starting a second concurrent refresh."""
    client, mb_router = mb_client
    with patch("services.postgres_store.has_active_daily_picks_job_or_unknown", return_value=False), \
         patch("services.postgres_store.try_reserve_multibagger_job", return_value=False):
        resp = client.post("/api/multibagger/refresh", params={"market": "US"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 409
    assert resp.json()["status"] == "already_running"


def test_in_refresh_is_completely_unaffected_by_us_guard(mb_client):
    """IN must never be blocked by US-only state — no durable check, no
    USE_POSTGRES requirement, original in-memory-only contract preserved."""
    client, mb_router = mb_client
    with patch.object(mb_router, "_run_refresh_job"):
        resp = client.post("/api/multibagger/refresh", params={"market": "IN"},
                            headers={"x-secret": "test-secret"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_in_refresh_does_not_call_daily_picks_conflict_check(mb_client):
    client, mb_router = mb_client
    with patch("services.postgres_store.has_active_daily_picks_job_or_unknown") as mock_check, \
         patch.object(mb_router, "_run_refresh_job"):
        client.post("/api/multibagger/refresh", params={"market": "IN"},
                     headers={"x-secret": "test-secret"})
    mock_check.assert_not_called()


# ─── us_fundamentals_refresh: cooperative stop signal ─────────────────────

def test_run_full_refresh_honors_should_stop_at_symbol_boundary():
    from services.us_fundamentals_refresh import run_full_refresh

    calls = {"n": 0}

    def should_stop():
        calls["n"] += 1
        return calls["n"] > 2  # stop after the 2nd symbol is checked

    with patch("services.us_fundamentals_refresh.US_STOCKS",
               [("AAA", "Alpha Inc"), ("BBB", "Beta Inc"), ("CCC", "Gamma Inc")]), \
         patch("services.us_fundamentals_refresh.cache.ensure_table"), \
         patch("services.us_fundamentals_refresh.cache.upsert"), \
         patch("services.us_fundamentals_refresh.fetch_us_fundamentals",
               return_value={"available": True, "sector": "Tech"}), \
         patch("services.us_fundamentals_refresh.time.sleep"):
        summary = run_full_refresh(should_stop=should_stop)

    assert summary["stopped_early"] is True
    assert summary["refreshed"] < summary["total"]


def test_run_full_refresh_without_should_stop_runs_to_completion():
    from services.us_fundamentals_refresh import run_full_refresh

    with patch("services.us_fundamentals_refresh.US_STOCKS",
               [("AAA", "Alpha Inc"), ("BBB", "Beta Inc")]), \
         patch("services.us_fundamentals_refresh.cache.ensure_table"), \
         patch("services.us_fundamentals_refresh.cache.upsert"), \
         patch("services.us_fundamentals_refresh.fetch_us_fundamentals",
               return_value={"available": True, "sector": "Tech"}), \
         patch("services.us_fundamentals_refresh.time.sleep"):
        summary = run_full_refresh()

    assert summary["stopped_early"] is False
    assert summary["refreshed"] == summary["total"] == 2


# ─── picks.py: requests the refresh to stop, never blocks itself ─────────

@pytest.fixture
def picks_client(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", "test-secret")
    monkeypatch.setenv("USE_POSTGRES", "1")
    import importlib
    import api.routers.picks as picks_router
    importlib.reload(picks_router)
    from api.main import app
    from fastapi.testclient import TestClient
    yield TestClient(app)


def test_us_generate_requests_multibagger_stop_when_running(picks_client):
    import api.routers.multibagger as mb_router
    mb_router._refresh_state["US"]["running"] = True
    mb_router._stop_requested["US"] = False
    try:
        with patch("services.daily_picks.picks_generated_today", return_value=False), \
             patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True), \
             patch("services.daily_picks.generate_picks"):
            resp = picks_client.post("/api/picks/generate", params={"market": "US"},
                                      headers={"x-secret": "test-secret"})
        assert resp.status_code in (200, 202)
        assert mb_router._stop_requested["US"] is True
    finally:
        mb_router._refresh_state["US"]["running"] = False
        mb_router._stop_requested["US"] = False


def test_us_generate_does_not_request_stop_when_multibagger_idle(picks_client):
    import api.routers.multibagger as mb_router
    mb_router._refresh_state["US"]["running"] = False
    mb_router._stop_requested["US"] = False
    with patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True), \
         patch("services.daily_picks.generate_picks"):
        picks_client.post("/api/picks/generate", params={"market": "US"},
                           headers={"x-secret": "test-secret"})
    assert mb_router._stop_requested["US"] is False


def test_us_generate_proceeds_immediately_rather_than_waiting_for_refresh(picks_client):
    """Daily Picks has priority — it must not block on the refresh finishing,
    only request it to stop and continue with its own reservation immediately."""
    import api.routers.multibagger as mb_router
    mb_router._refresh_state["US"]["running"] = True
    try:
        with patch("services.daily_picks.picks_generated_today", return_value=False), \
             patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True) as mock_reserve, \
             patch("services.daily_picks.generate_picks"):
            resp = picks_client.post("/api/picks/generate", params={"market": "US"},
                                      headers={"x-secret": "test-secret"})
        assert resp.status_code in (200, 202)
        mock_reserve.assert_called_once()
    finally:
        mb_router._refresh_state["US"]["running"] = False
        mb_router._stop_requested["US"] = False


def test_in_generate_never_touches_multibagger_state(picks_client):
    """India generation must be completely unaffected by the US-only guard."""
    import api.routers.multibagger as mb_router
    with patch("api.routers.multibagger.request_us_stop") as mock_stop, \
         patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True), \
         patch("services.daily_picks.generate_picks"):
        picks_client.post("/api/picks/generate", params={"market": "IN"},
                           headers={"x-secret": "test-secret"})
    mock_stop.assert_not_called()


def test_premarket_finalize_also_requests_multibagger_stop_when_running(picks_client):
    import api.routers.multibagger as mb_router
    mb_router._refresh_state["US"]["running"] = True
    mb_router._stop_requested["US"] = False
    try:
        with patch("services.premarket_finalizer.finalize_premarket",
                   return_value={"status": "skipped"}):
            resp = picks_client.post("/api/picks/premarket-finalize", params={"market": "US"},
                                      headers={"x-secret": "test-secret"})
        assert resp.status_code == 200
        assert mb_router._stop_requested["US"] is True
    finally:
        mb_router._refresh_state["US"]["running"] = False
        mb_router._stop_requested["US"] = False


# ─── No deadlock, no waiting, restart/orphan behavior is defined ─────────

def test_stop_request_is_non_blocking():
    """request_us_stop() must be a plain synchronous flag set — no lock
    acquisition, no wait, so it can never deadlock the calling request."""
    import inspect
    from api.routers import multibagger as mb_router
    src = inspect.getsource(mb_router.request_us_stop)
    assert "lock" not in src.lower()
    assert "wait" not in src.lower()
    assert "sleep" not in src.lower()


def test_multibagger_running_row_has_no_automatic_orphan_recovery_by_design():
    """Documents the known, disclosed limitation (matches daily_picks_jobs'
    own existing 'interrupted is manual-only' precedent, see
    postgres_store.py) rather than claiming an unproven auto-recovery
    mechanism: a hard-killed process leaves the row 'running' until an
    operator manually updates it. Multibagger jobs are bounded (~5-6h, one
    nightly run) so this is a narrow, already-accepted risk class, not a
    new one introduced by this release."""
    import inspect
    from services import postgres_store
    src = inspect.getsource(postgres_store)
    assert "multibagger_refresh_jobs" in src
    # No status value in the CHECK constraint implies automatic timeout/orphan handling.
    assert "status IN ('running', 'completed', 'failed')" in src
