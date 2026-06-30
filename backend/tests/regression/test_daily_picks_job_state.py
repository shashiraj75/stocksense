"""
Product Integrity Workstream #002D-E — regression tests for durable job-state layer.

All tests are deterministic and fully mocked — no real DB, no external providers.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call
import pytest


# ─── postgres_store helper contracts ──────────────────────────────────────────

def test_try_reserve_returns_true_on_insert():
    """try_reserve_daily_picks_job returns True when rowcount == 1."""
    from services.postgres_store import try_reserve_daily_picks_job

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 1
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        result = try_reserve_daily_picks_job("job-1", "IN", "runner-1")

    assert result is True


def test_try_reserve_returns_false_on_conflict():
    """try_reserve_daily_picks_job returns False when rowcount != 1 (INSERT conflict)."""
    from services.postgres_store import try_reserve_daily_picks_job

    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.rowcount = 0
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        result = try_reserve_daily_picks_job("job-2", "IN", "runner-1")

    assert result is False


def test_try_reserve_raises_on_genuine_db_error():
    """try_reserve_daily_picks_job raises on genuine DB errors so callers can return 503."""
    from services.postgres_store import try_reserve_daily_picks_job

    mock_pool = MagicMock()
    mock_pool.connection.side_effect = Exception("connection refused")

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        with pytest.raises(Exception, match="connection refused"):
            try_reserve_daily_picks_job("job-3", "IN", "runner-1")


def test_save_picks_to_db_returns_true_on_success():
    """save_picks_to_db returns True when both INSERT and DELETE succeed."""
    from services.postgres_store import save_picks_to_db

    mock_conn = MagicMock()
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.connection.return_value = mock_conn

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        result = save_picks_to_db({"generated_at": "2026-06-30T00:00:00Z"}, market="IN")

    assert result is True


def test_save_picks_to_db_returns_false_on_exception():
    """save_picks_to_db returns False (not raises) when DB write fails."""
    from services.postgres_store import save_picks_to_db

    mock_pool = MagicMock()
    mock_pool.connection.side_effect = Exception("disk full")

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        result = save_picks_to_db({"generated_at": "2026-06-30T00:00:00Z"}, market="IN")

    assert result is False


def test_get_active_daily_picks_job_returns_none_on_exception():
    """get_active_daily_picks_job swallows errors and returns None."""
    from services.postgres_store import get_active_daily_picks_job

    mock_pool = MagicMock()
    mock_pool.connection.side_effect = Exception("timeout")

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        result = get_active_daily_picks_job("IN")

    assert result is None


def test_get_latest_daily_picks_job_returns_none_on_exception():
    """get_latest_daily_picks_job swallows errors and returns None."""
    from services.postgres_store import get_latest_daily_picks_job

    mock_pool = MagicMock()
    mock_pool.connection.side_effect = Exception("timeout")

    with patch("services.postgres_store._get_pool", return_value=mock_pool):
        result = get_latest_daily_picks_job("IN")

    assert result is None


# ─── generate_picks lifecycle ─────────────────────────────────────────────────

def _make_mock_pg_helpers(reserved=True, save_ok=True):
    """Return a patch dict for all postgres_store helpers used by generate_picks."""
    return {
        "services.postgres_store.try_reserve_daily_picks_job": MagicMock(return_value=reserved),
        "services.postgres_store.mark_daily_picks_job_running": MagicMock(),
        "services.postgres_store.mark_daily_picks_job_completed": MagicMock(),
        "services.postgres_store.mark_daily_picks_job_failed": MagicMock(),
        "services.postgres_store.record_daily_picks_job_heartbeat": MagicMock(),
        "services.postgres_store.record_daily_picks_job_progress": MagicMock(),
        "services.postgres_store.save_picks_to_db": MagicMock(return_value=save_ok),
    }


def test_generate_picks_marks_completed_when_save_succeeds():
    """generate_picks calls mark_completed when _generate_picks_inner returns a persisted_at."""
    import services.daily_picks as _dp

    fake_payload = {"generated_at": "2026-06-30T00:00:00Z", "picks": {}}
    fake_persisted_at = datetime(2026, 6, 30, tzinfo=timezone.utc)

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(_dp, "_generate_picks_inner",
                      return_value=(fake_payload, fake_persisted_at)), \
         patch("services.postgres_store.mark_daily_picks_job_running") as mock_running, \
         patch("services.postgres_store.mark_daily_picks_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("threading.Thread"):  # suppress heartbeat daemon

        _dp.generate_picks("IN", job_id="job-abc")

    mock_running.assert_called_once_with("job-abc")
    mock_completed.assert_called_once()
    mock_failed.assert_not_called()


def test_generate_picks_marks_failed_when_save_returns_none():
    """generate_picks calls mark_failed when persisted_at is None (save returned False)."""
    import services.daily_picks as _dp

    fake_payload = {"generated_at": "2026-06-30T00:00:00Z", "picks": {}}

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(_dp, "_generate_picks_inner",
                      return_value=(fake_payload, None)), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("threading.Thread"):

        _dp.generate_picks("IN", job_id="job-abc")

    mock_completed.assert_not_called()
    mock_failed.assert_called_once()


def test_generate_picks_marks_failed_on_inner_exception():
    """generate_picks calls mark_failed when _generate_picks_inner raises."""
    import services.daily_picks as _dp

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.object(_dp, "_generate_picks_inner",
                      side_effect=RuntimeError("network timeout")), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("threading.Thread"):

        result = _dp.generate_picks("IN", job_id="job-abc")

    mock_completed.assert_not_called()
    mock_failed.assert_called_once()
    assert result.get("error") is not None


def test_generate_picks_no_job_id_skips_lifecycle():
    """generate_picks with no job_id skips all mark_* calls (legacy/no-pg path)."""
    import services.daily_picks as _dp

    fake_payload = {"generated_at": "2026-06-30T00:00:00Z", "picks": {}}

    with patch.dict("os.environ", {"USE_POSTGRES": "0"}), \
         patch.object(_dp, "_generate_picks_inner",
                      return_value=(fake_payload, None)), \
         patch("services.postgres_store.mark_daily_picks_job_running") as mock_running, \
         patch("services.postgres_store.mark_daily_picks_job_completed") as mock_completed, \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("threading.Thread"):

        _dp.generate_picks("IN", job_id=None)

    mock_running.assert_not_called()
    mock_completed.assert_not_called()
    mock_failed.assert_not_called()


# ─── POST /generate HTTP contract ────────────────────────────────────────────

def _picks_client():
    """Return a TestClient with PICKS_SECRET patched to 'secret'."""
    from fastapi.testclient import TestClient
    from api.main import app
    import api.routers.picks as _picks_router
    # PICKS_SECRET is read at module import time, so patch the module attribute
    _picks_router.PICKS_SECRET = "secret"
    return TestClient(app)


def test_post_generate_returns_200_when_already_fresh():
    """POST /generate returns HTTP 200 when picks already exist for today."""
    with patch("services.daily_picks.picks_generated_today", return_value=True), \
         patch.dict("os.environ", {"USE_POSTGRES": "1"}):
        resp = _picks_client().post("/api/picks/generate?market=IN",
                                    headers={"x-secret": "secret"})

    assert resp.status_code == 200
    assert resp.json()["status"] == "already_fresh"


def test_post_generate_returns_409_when_already_running_in_memory():
    """POST /generate returns 409 when in-memory _generating flag is True."""
    import services.daily_picks as _dp

    with patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch.dict(_dp._generating, {"IN": True}):
        resp = _picks_client().post("/api/picks/generate?market=IN",
                                    headers={"x-secret": "secret"})

    assert resp.status_code == 409
    assert resp.json()["status"] == "already_running"


def test_post_generate_returns_409_when_db_reserve_conflict():
    """POST /generate returns 409 when try_reserve returns False (DB conflict)."""
    with patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.try_reserve_daily_picks_job", return_value=False), \
         patch("services.postgres_store.get_active_daily_picks_job",
               return_value={"job_id": "other-job"}):
        resp = _picks_client().post("/api/picks/generate?market=IN",
                                    headers={"x-secret": "secret"})

    assert resp.status_code == 409
    data = resp.json()
    assert data["status"] == "already_running"
    assert data.get("job_id") == "other-job"


def test_post_generate_returns_503_when_db_raises():
    """POST /generate returns 503 when try_reserve_daily_picks_job raises."""
    with patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.try_reserve_daily_picks_job",
               side_effect=Exception("DB down")):
        resp = _picks_client().post("/api/picks/generate?market=IN",
                                    headers={"x-secret": "secret"})

    assert resp.status_code == 503
    assert resp.json()["status"] == "durable_job_state_unavailable"


def test_post_generate_returns_202_on_success():
    """POST /generate returns 202 with job_id when reservation succeeds."""
    with patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True), \
         patch("services.daily_picks.generate_picks"):
        resp = _picks_client().post("/api/picks/generate?market=IN",
                                    headers={"x-secret": "secret"})

    assert resp.status_code == 202
    data = resp.json()
    assert data["status"] == "accepted"
    assert "job_id" in data


def test_post_generate_returns_401_on_bad_secret():
    """POST /generate returns 401 when X-Secret is wrong."""
    import api.routers.picks as _picks_router
    _picks_router.PICKS_SECRET = "secret"
    from fastapi.testclient import TestClient
    from api.main import app
    resp = TestClient(app).post("/api/picks/generate?market=IN",
                                headers={"x-secret": "WRONG"})
    assert resp.status_code == 401


# ─── GET /status additive fields ─────────────────────────────────────────────

def test_get_status_includes_job_fields_when_pg_enabled():
    """GET /status includes job_id, job_status, phase, etc. when USE_POSTGRES=1."""
    from fastapi.testclient import TestClient
    from api.main import app

    fake_job = {
        "job_id": "job-xyz",
        "status": "running",
        "current_phase": "phase_1",
        "processed": 30,
        "total": 150,
        "last_runner_heartbeat_at": datetime.now(timezone.utc).isoformat(),
        "last_progress_at": datetime.now(timezone.utc).isoformat(),
        "universe_used": "screener",
        "universe_degraded": False,
    }

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch("services.daily_picks.picks_generated_today", return_value=False), \
         patch("api.routers.picks.get_latest_daily_picks_job", fake_job.__class__,
               create=True), \
         patch("services.postgres_store.get_latest_daily_picks_job", return_value=fake_job):
        client = TestClient(app)
        resp = client.get("/api/picks/status?market=IN")

    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == "job-xyz"
    assert data["job_status"] == "running"
    assert data["phase"] == "phase_1"
    assert data["processed"] == 30
    assert data["total"] == 150
    assert "derived_job_health" in data


def test_get_status_omits_job_fields_when_no_job():
    """GET /status omits job fields when no job exists."""
    from fastapi.testclient import TestClient
    from api.main import app

    with patch.dict("os.environ", {"USE_POSTGRES": "1"}), \
         patch("services.daily_picks.picks_generated_today", return_value=True), \
         patch("services.postgres_store.get_latest_daily_picks_job", return_value=None):
        client = TestClient(app)
        resp = client.get("/api/picks/status?market=IN")

    assert resp.status_code == 200
    data = resp.json()
    assert "job_id" not in data
    assert "derived_job_health" not in data


# ─── _derive_job_health ───────────────────────────────────────────────────────

def test_derive_job_health_ok_for_recent_heartbeat():
    from api.routers.picks import _derive_job_health
    job = {
        "status": "running",
        "last_runner_heartbeat_at": datetime.now(timezone.utc).isoformat(),
    }
    assert _derive_job_health(job) == "ok"


def test_derive_job_health_slow_for_old_heartbeat():
    from datetime import timedelta
    from api.routers.picks import _derive_job_health
    old = (datetime.now(timezone.utc) - timedelta(seconds=100)).isoformat()
    job = {"status": "running", "last_runner_heartbeat_at": old}
    assert _derive_job_health(job) == "slow"


def test_derive_job_health_unresponsive_for_very_old_heartbeat():
    from datetime import timedelta
    from api.routers.picks import _derive_job_health
    old = (datetime.now(timezone.utc) - timedelta(seconds=200)).isoformat()
    job = {"status": "running", "last_runner_heartbeat_at": old}
    assert _derive_job_health(job) == "unresponsive"


def test_derive_job_health_none_for_non_running_job():
    from api.routers.picks import _derive_job_health
    job = {"status": "completed", "last_runner_heartbeat_at": datetime.now(timezone.utc).isoformat()}
    assert _derive_job_health(job) is None
