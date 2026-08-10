"""
Daily Picks Scheduler & Completion Reliability Hardening (2026-08) —
POST /api/picks/recover.

This endpoint is a thin, generic HTTP wrapper over the EXISTING
services.daily_picks.attempt_governed_recovery() (already used internally
by the US premarket finalizer since the 2026-07-22 incident) — it adds no
new recovery logic. These tests verify the wrapper's own behavior (auth,
market normalization, pass-through of attempt_governed_recovery's outcome)
with attempt_governed_recovery itself mocked, since its own semantics are
already covered by tests/regression/test_daily_picks_orphan_reconciliation.py
and similar.

Fully mocked — no real DB, no external providers, no live network (SES-003).
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "recover-test-picks-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", TEST_SECRET)
    import importlib
    import api.routers.picks as picks_router
    importlib.reload(picks_router)
    from api.main import app
    return TestClient(app)


@pytest.mark.integration
class TestRecoverEndpointAuth:
    def test_missing_secret_is_rejected(self, client):
        resp = client.post("/api/picks/recover", params={"market": "IN"})
        assert resp.status_code == 401

    def test_wrong_secret_is_rejected(self, client):
        resp = client.post("/api/picks/recover", params={"market": "IN"},
                            headers={"x-secret": "wrong"})
        assert resp.status_code == 401

    def test_invalid_market_is_rejected(self, client):
        resp = client.post("/api/picks/recover", params={"market": "XX"},
                            headers={"x-secret": TEST_SECRET})
        assert resp.status_code == 400


@pytest.mark.integration
class TestRecoverEndpointPassThrough:
    """The endpoint must call attempt_governed_recovery with the normalized
    market and the given reason, and pass its result straight through —
    never re-deciding or re-interpreting the outcome itself."""

    def test_triggered_recovery_is_passed_through(self, client):
        with patch("services.daily_picks.attempt_governed_recovery",
                   return_value={"triggered": True, "job_id": "job-in-recover-1"}) as mock_recover:
            resp = client.post("/api/picks/recover",
                                params={"market": "IN", "reason": "in_watchdog_missing_or_stale"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code == 200
        body = resp.json()
        assert body["triggered"] is True
        assert body["job_id"] == "job-in-recover-1"
        assert body["market"] == "IN"
        mock_recover.assert_called_once_with("IN", reason="in_watchdog_missing_or_stale")

    def test_already_fresh_noop_is_passed_through(self, client):
        with patch("services.daily_picks.attempt_governed_recovery",
                   return_value={"triggered": False, "reason": "already_fresh"}):
            resp = client.post("/api/picks/recover", params={"market": "IN"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code == 200
        body = resp.json()
        assert body["triggered"] is False
        assert body["reason"] == "already_fresh"

    def test_already_running_noop_is_passed_through_no_duplicate(self, client):
        with patch("services.daily_picks.attempt_governed_recovery",
                   return_value={"triggered": False, "reason": "already_running"}) as mock_recover:
            resp = client.post("/api/picks/recover", params={"market": "IN"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code == 200
        assert resp.json()["reason"] == "already_running"
        # Only ever one call — the wrapper itself never retries/loops.
        mock_recover.assert_called_once()

    def test_max_attempts_reached_is_passed_through_as_failure_reason(self, client):
        with patch("services.daily_picks.attempt_governed_recovery",
                   return_value={"triggered": False, "reason": "max_attempts_reached",
                                 "attempts_today": 3}):
            resp = client.post("/api/picks/recover", params={"market": "IN"},
                                headers={"x-secret": TEST_SECRET})
        body = resp.json()
        assert body["triggered"] is False
        assert body["reason"] == "max_attempts_reached"
        assert body["attempts_today"] == 3

    def test_durable_state_unavailable_is_passed_through(self, client):
        with patch("services.daily_picks.attempt_governed_recovery",
                   return_value={"triggered": False, "reason": "durable_job_state_unavailable"}):
            resp = client.post("/api/picks/recover", params={"market": "US"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code == 200
        assert resp.json()["reason"] == "durable_job_state_unavailable"

    def test_default_reason_is_watchdog_check(self, client):
        with patch("services.daily_picks.attempt_governed_recovery",
                   return_value={"triggered": False, "reason": "already_fresh"}) as mock_recover:
            client.post("/api/picks/recover", params={"market": "US"},
                         headers={"x-secret": TEST_SECRET})
        mock_recover.assert_called_once_with("US", reason="watchdog_check")

    def test_us_market_also_supported(self, client):
        """This endpoint is market-generic — attempt_governed_recovery()
        itself is already market-generic (see its market param), so no
        market-specific branching was added here."""
        with patch("services.daily_picks.attempt_governed_recovery",
                   return_value={"triggered": True, "job_id": "job-us-recover-1"}) as mock_recover:
            resp = client.post("/api/picks/recover", params={"market": "us"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code == 200
        mock_recover.assert_called_once_with("US", reason="watchdog_check")


@pytest.mark.integration
class TestExistingEndpointsUnaffectedByRecover:
    def test_generate_endpoint_unaffected(self, client, monkeypatch):
        import services.daily_picks as dp
        monkeypatch.setenv("USE_POSTGRES", "1")
        with patch.object(dp, "generate_picks"), \
             patch("services.postgres_store.try_reserve_daily_picks_job_with_lease", return_value="started"), \
             patch("services.postgres_store.release_heavy_workload_lease"), \
             patch("services.daily_picks.picks_generated_today", return_value=False):
            resp = client.post("/api/picks/generate", params={"market": "IN"},
                                headers={"x-secret": TEST_SECRET})
        assert resp.status_code in (200, 202)

    def test_status_endpoint_unaffected(self, client):
        resp = client.get("/api/picks/status", params={"market": "IN"})
        assert resp.status_code == 200
