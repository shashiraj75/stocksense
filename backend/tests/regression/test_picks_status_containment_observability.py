"""
Learning Alpha Engine remediation, Phase 1 — GET /api/picks/status
containment observability, and backward compatibility of the existing
response contract.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from services.alpha_engine import containment


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(containment.ENV_VAR, raising=False)
    monkeypatch.delenv("USE_POSTGRES", raising=False)


_EXISTING_TOP_LEVEL_KEYS = {
    "market", "generating", "has_today", "last_error", "last_trigger_received_at",
}


def test_containment_block_present_with_no_job_and_no_postgres(client):
    """The live containment config must be readable even when USE_POSTGRES
    isn't set and no job has ever run — it never triggers generation."""
    with patch("services.daily_picks.picks_generated_today", return_value=False):
        resp = client.get("/api/picks/status", params={"market": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert _EXISTING_TOP_LEVEL_KEYS.issubset(body.keys()), "existing response contract must stay intact"
    assert body["containment"] == {
        "production_learning_enabled": False,
        "production_alpha_source": "fixed_academic_prior",
        "containment_reason": containment.containment_reason(),
        "learning_dataset_version": containment.LEARNING_DATASET_VERSION,
        "graduation_progress": [],  # USE_POSTGRES unset — never queried
        "graduation_progress_note": (
            "Row/run counts accumulated per market/horizon in the clean "
            "alpha_observations table. Necessary but not sufficient: this "
            "table has no outcome-resolution pipeline yet, so these "
            "counts alone cannot support real IC computation."
        ),
    }


def test_containment_block_reflects_explicit_enable(client, monkeypatch):
    monkeypatch.setenv(containment.ENV_VAR, "1")
    with patch("services.daily_picks.picks_generated_today", return_value=False):
        resp = client.get("/api/picks/status", params={"market": "US"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["containment"]["production_learning_enabled"] is True
    assert body["containment"]["production_alpha_source"] == "live_ic_meta_model"
    assert body["containment"]["containment_reason"] is None


def test_per_run_observability_surfaces_from_job_row_when_present(client, monkeypatch):
    monkeypatch.setenv("USE_POSTGRES", "1")
    fake_job = {
        "job_id": "job-1", "status": "completed", "phase": "done",
        "processed": 10, "total": 10,
        "last_runner_heartbeat_at": None, "last_progress_at": None,
        "universe_used": "screener", "universe_degraded": False,
        "screener_raw_count": None, "universe_candidate_count": None,
        "universe_selection_attempts": None, "universe_selection_reason": None,
        "universe_selection_error_category": None,
        "phase_task_processed": None, "phase_task_total": None,
        "production_alpha_source": "fixed_academic_prior",
        "shadow_ic_available": {"short": False, "medium": False, "long": False},
        "shadow_meta_model_available": {"short": False, "medium": False, "long": False},
        "containment_reason": "quarantined for test",
        "learning_dataset_version": "legacy-quarantined-2026-07-12",
    }
    with patch("services.postgres_store.get_latest_daily_picks_job", return_value=fake_job), \
         patch("services.daily_picks.picks_generated_today", return_value=True):
        resp = client.get("/api/picks/status", params={"market": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert _EXISTING_TOP_LEVEL_KEYS.issubset(body.keys())
    assert body["job_id"] == "job-1"  # pre-existing field, unaffected
    assert body["production_alpha_source"] == "fixed_academic_prior"
    assert body["shadow_ic_available"] == {"short": False, "medium": False, "long": False}
    assert body["containment_reason"] == "quarantined for test"
    assert body["learning_dataset_version"] == "legacy-quarantined-2026-07-12"


def test_job_row_recorded_before_this_release_reports_none_not_fabricated(client, monkeypatch):
    monkeypatch.setenv("USE_POSTGRES", "1")
    legacy_job = {
        "job_id": "job-old", "status": "completed", "phase": "done",
        "processed": 5, "total": 5,
        "last_runner_heartbeat_at": None, "last_progress_at": None,
        "universe_used": "screener", "universe_degraded": False,
        "screener_raw_count": None, "universe_candidate_count": None,
        "universe_selection_attempts": None, "universe_selection_reason": None,
        "universe_selection_error_category": None,
        "phase_task_processed": None, "phase_task_total": None,
        "production_alpha_source": None,
        "shadow_ic_available": None,
        "shadow_meta_model_available": None,
        "containment_reason": None,
        "learning_dataset_version": None,
    }
    with patch("services.postgres_store.get_latest_daily_picks_job", return_value=legacy_job), \
         patch("services.daily_picks.picks_generated_today", return_value=True):
        resp = client.get("/api/picks/status", params={"market": "IN"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["production_alpha_source"] is None
    assert body["shadow_ic_available"] is None
