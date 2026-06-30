"""
Product Integrity Workstream #001B — regression test locking in the new
`last_trigger_received_at` observability field.

Root cause this addresses: when a US Daily Picks scheduled run produced no
new record, the only available evidence was `generated_at` staying stale —
which proves SOMETHING didn't happen, but can't distinguish "the GitHub
Actions trigger never reached the backend" from "it reached the backend
but `generate_picks()` crashed before its own except-handler could write a
fresh timestamp." This test confirms a valid POST /api/picks/generate
request now records an acceptance timestamp immediately, before the
background task runs, and that /api/picks/status exposes it — independent
of whether generation itself ever completes.
"""

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "regression-test-picks-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("PICKS_SECRET", TEST_SECRET)
    import importlib

    import api.routers.picks as picks_router

    importlib.reload(picks_router)
    from api.main import app

    return TestClient(app)


def test_valid_trigger_records_received_at_before_generation_runs(client, monkeypatch):
    import services.daily_picks as dp

    # #002D-G Fix 3: USE_POSTGRES=1 is now mandatory; test must reflect that.
    monkeypatch.setenv("USE_POSTGRES", "1")
    dp._last_trigger_received_at["US"] = None
    with patch.object(dp, "generate_picks"), \
         patch("services.postgres_store.try_reserve_daily_picks_job", return_value=True), \
         patch("services.daily_picks.picks_generated_today", return_value=False):
        resp = client.post("/api/picks/generate", params={"market": "US"}, headers={"x-secret": TEST_SECRET})
    assert resp.status_code in (200, 202)  # 202 = accepted, 200 = already_fresh
    # Recorded synchronously, in the request itself — not dependent on the
    # background task (mocked above) ever running.
    assert dp._last_trigger_received_at["US"] is not None


def test_invalid_secret_does_not_record_a_trigger(client):
    import services.daily_picks as dp

    dp._last_trigger_received_at["US"] = None
    resp = client.post("/api/picks/generate", params={"market": "US"}, headers={"x-secret": "wrong"})
    assert resp.status_code == 401
    assert dp._last_trigger_received_at["US"] is None


def test_status_endpoint_exposes_last_trigger_received_at(client):
    import services.daily_picks as dp

    dp._last_trigger_received_at["US"] = "2026-06-29T12:30:05+00:00"
    resp = client.get("/api/picks/status", params={"market": "US"})
    assert resp.status_code == 200
    assert resp.json()["last_trigger_received_at"] == "2026-06-29T12:30:05+00:00"
