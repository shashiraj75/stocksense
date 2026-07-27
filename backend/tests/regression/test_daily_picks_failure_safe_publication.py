"""
US Daily Picks generation-reliability incident (2026-07-22) — failure-safe
publication contract.

Root cause of the recurring blank US Daily Picks page: a failed generation
attempt (crash, or the memory circuit breaker's controlled abort) used to
write an empty, error-tagged payload to BOTH the disk cache file and
Postgres's daily_picks_cache "latest payload" table. Since
get_cached_picks()/load_picks_from_db() served whatever was most recently
written regardless of success/failure, this silently shadowed the last
genuinely successful payload — every user saw a blank page with zero
picks, indistinguishable from a legitimate zero-BUY day, until a NEW
successful run happened to complete.

Fix: generate_picks()'s except-branch no longer writes to either store —
the failure is durably recorded exclusively via mark_daily_picks_job_failed
(daily_picks_jobs). daily_picks_cache gained a `status` column
('success' | 'failed_attempt'); save_picks_to_db always writes
status='success' (the only status any live code path writes going
forward); load_picks_from_db filters WHERE status='success', so it always
returns the last genuinely successful payload — however old — never an
empty stand-in. A migration-safe backfill retroactively corrects any
pre-existing row that carries the old failure marker (payload ? 'error'),
making the fix self-healing for data already polluted by the incident.

Distinct from a legitimate zero-BUY successful day, which is real,
completed, status='success', and must never be conflated with failure.
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────
# generate_picks() must never persist a failed attempt's payload
# ─────────────────────────────────────────────────────────────────────────

def test_failed_generation_does_not_call_save_picks_to_db():
    """The except-branch in generate_picks() must never call
    save_picks_to_db — that call, on the OLD code, is exactly what
    overwrote the last successful payload with an empty error stand-in."""
    import services.daily_picks as dp

    with patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch.object(dp, "_generate_picks_inner", side_effect=RuntimeError("boom")), \
         patch("services.postgres_store.mark_daily_picks_job_running"), \
         patch("services.postgres_store.mark_daily_picks_job_failed") as mock_failed, \
         patch("services.postgres_store.record_daily_picks_job_heartbeat"), \
         patch("services.postgres_store.save_picks_to_db") as mock_save, \
         patch("threading.Thread"):
        dp.generate_picks("US", job_id="job-crash-1")

    mock_failed.assert_called_once()
    mock_save.assert_not_called()


def test_failed_generation_does_not_overwrite_the_disk_cache_file():
    """The except-branch must never write to the disk cache file either —
    that's the non-Postgres fallback path, and it has the identical
    shadowing defect if left in place."""
    import services.daily_picks as dp

    with patch.dict(os.environ, {}, clear=True), \
         patch.object(dp, "_generate_picks_inner", side_effect=RuntimeError("boom")), \
         patch("builtins.open", MagicMock()) as mock_open, \
         patch("json.dump") as mock_json_dump:
        dp.generate_picks("IN", job_id=None)

    mock_open.assert_not_called()
    mock_json_dump.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────
# postgres_store: status column semantics
# ─────────────────────────────────────────────────────────────────────────

def test_save_picks_to_db_always_writes_status_success_by_default():
    import services.postgres_store as ps

    captured = {}
    fake_conn = MagicMock()

    def fake_execute(sql, params=None):
        if "INSERT INTO daily_picks_cache" in sql:
            captured["sql"] = sql
            captured["params"] = params
        return MagicMock()

    fake_conn.execute.side_effect = fake_execute
    fake_pool = MagicMock()
    fake_pool.connection.return_value.__enter__.return_value = fake_conn
    fake_pool.connection.return_value.__exit__.return_value = False

    with patch.object(ps, "_get_pool", return_value=fake_pool):
        ok = ps.save_picks_to_db({"picks": {"short": [], "medium": [], "long": []}}, market="US")

    assert ok is True
    assert "status" in captured["sql"]
    assert captured["params"][-1] == "success"


def test_load_picks_from_db_filters_to_status_success_only():
    import services.postgres_store as ps

    captured = {}
    fake_conn = MagicMock()

    def fake_execute(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        m = MagicMock()
        m.fetchone.return_value = None
        return m

    fake_conn.execute.side_effect = fake_execute
    fake_pool = MagicMock()
    fake_pool.connection.return_value.__enter__.return_value = fake_conn
    fake_pool.connection.return_value.__exit__.return_value = False

    with patch.object(ps, "_get_pool", return_value=fake_pool):
        ps.load_picks_from_db(market="US")

    assert "status = 'success'" in captured["sql"]


# ─────────────────────────────────────────────────────────────────────────
# get_generation_attempt_status / picks_generated_today — legitimate zero-
# pick day vs. failure, and staleness detection
# ─────────────────────────────────────────────────────────────────────────

def test_picks_generated_today_true_for_legitimate_zero_pick_success():
    """A genuine, successful, zero-BUY day (status='success' by
    construction, since it went through the normal completion path) must
    report picks_generated_today=True — never conflated with failure."""
    import services.daily_picks as dp

    today_iso = datetime.now(timezone.utc).isoformat()
    with patch.object(dp, "get_cached_picks",
                       return_value={"generated_at": today_iso,
                                     "picks": {"short": [], "medium": [], "long": []}}):
        assert dp.picks_generated_today("US") is True


def test_picks_generated_today_false_when_only_stale_payload_exists():
    stale_iso = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    import services.daily_picks as dp
    with patch.object(dp, "get_cached_picks",
                       return_value={"generated_at": stale_iso,
                                     "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []}}):
        assert dp.picks_generated_today("US") is False


def test_get_generation_attempt_status_reports_stale_with_session_date():
    import services.daily_picks as dp

    stale_dt = datetime.now(timezone.utc) - timedelta(days=2)
    with patch.object(dp, "get_cached_picks",
                       return_value={"generated_at": stale_dt.isoformat(),
                                     "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []}}), \
         patch.dict(os.environ, {"USE_POSTGRES": "1"}), \
         patch("services.postgres_store.get_latest_daily_picks_job",
               return_value={"status": "failed", "last_error": "memory usage 81.0% >= abort threshold 80%",
                             "started_at": stale_dt}):
        result = dp.get_generation_attempt_status("US")

    assert result["stale"] is True
    assert result["serving_stale_payload"] is True
    assert result["has_today"] is False
    assert result["last_attempt_status"] == "failed"
    assert result["last_attempt_error_category"] == "memory_limit_exceeded"
    # Never the raw traceback/error text — bounded category only.
    assert "81.0%" not in str(result["last_attempt_error_category"])


def test_get_generation_attempt_status_not_stale_for_todays_success():
    import services.daily_picks as dp

    today_iso = datetime.now(timezone.utc).isoformat()
    with patch.object(dp, "get_cached_picks",
                       return_value={"generated_at": today_iso,
                                     "picks": {"short": [{"symbol": "AAPL"}], "medium": [], "long": []}}), \
         patch.dict(os.environ, {}, clear=True):
        result = dp.get_generation_attempt_status("US")

    assert result["stale"] is False
    assert result["has_today"] is True


def test_categorize_error_never_leaks_raw_text():
    from services.daily_picks import _categorize_error

    assert _categorize_error(None) is None
    assert _categorize_error("") is None
    assert _categorize_error("memory usage 80.6% >= abort threshold 80% at phase=phase_1") == "memory_limit_exceeded"
    assert _categorize_error("persistence_failed: Postgres picks save returned False") == "persistence_failed"
    assert _categorize_error("HTTPError 429 Too Many Requests") == "provider_rate_limited"
    weird = _categorize_error("Traceback (most recent call last):\n  File \"/app/x.py\"...")
    assert weird == "unknown"
    assert "/app/x.py" not in weird
