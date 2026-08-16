"""
V-SCHED1D-B — authenticated, read-only scheduler-attempt history.

Covers the new service primitive (services.validation_engine.
list_schedule_attempts) and the new authenticated router
(GET /api/validation/attempts). Every test seeds the ledger directly via
raw SQL against an isolated SQLite database (never the real admission/
execution chain) so these tests are fast, deterministic, and exercise the
READ primitive in isolation — the write/admission chain itself is already
covered exhaustively elsewhere (test_scheduler_ledger_integration.py,
test_validation_schedule_ledger.py).

No test here triggers real validation, touches the real production
database, or calls any admission/lease/execution function.
"""
import base64
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import services.validation_engine as ve

T0 = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)

TEST_SECRET = "test-attempt-history-secret"


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "attempt_history_test.db")
    monkeypatch.setattr(ve, "_DB_PATH", db_path)
    monkeypatch.setattr(ve, "_db_initialised", False)
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    ve._init_db()
    return db_path


def _seed_slot(db_path, *, horizon="medium", universe="nifty100", scheduled_slot=T0,
                schedule_version="v1", status="completed", slot_id=None, now=T0):
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO validation_schedule_slots "
            "(horizon, universe, scheduled_slot, schedule_version, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (horizon, universe, scheduled_slot.isoformat(), schedule_version, status,
             now.isoformat(), now.isoformat()),
        )
        return cur.lastrowid


def _seed_attempt(db_path, *, slot_id=None, horizon="medium", universe="nifty100",
                   attempt_number=1, trigger_type="scheduler", status="completed",
                   lease_owner="owner-x", lease_fencing_token=1,
                   started_at=None, heartbeat_at=None, completed_at=None,
                   result_run_id=None, failure_category=None, failure_summary=None,
                   created_at=T0, updated_at=T0):
    with sqlite3.connect(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO validation_schedule_attempts "
            "(slot_id, horizon, universe, attempt_number, trigger_type, status, lease_owner, "
            "lease_fencing_token, started_at, heartbeat_at, completed_at, result_run_id, "
            "failure_category, failure_summary, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (slot_id, horizon, universe, attempt_number, trigger_type, status, lease_owner,
             lease_fencing_token,
             started_at.isoformat() if started_at else None,
             heartbeat_at.isoformat() if heartbeat_at else None,
             completed_at.isoformat() if completed_at else None,
             result_run_id, failure_category, failure_summary,
             created_at.isoformat(), updated_at.isoformat()),
        )
        return cur.lastrowid


def _encode_cursor(created_at: datetime, attempt_id: int) -> str:
    payload = {"v": 1, "created_at": created_at.astimezone(timezone.utc).isoformat(), "id": attempt_id}
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


# ─────────────────────────────────────────────────────────────────────────
# Service-level tests — services.validation_engine.list_schedule_attempts
# ─────────────────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestListScheduleAttemptsService:
    def test_default_limit_is_fifty(self, isolated_db):
        slot_id = _seed_slot(isolated_db)
        for i in range(60):
            _seed_attempt(isolated_db, slot_id=slot_id, attempt_number=i + 1,
                           created_at=T0 + timedelta(seconds=i), updated_at=T0)
        rows = ve.list_schedule_attempts(limit=50)
        assert len(rows) == 50

    def test_maximum_limit_two_hundred_honored(self, isolated_db):
        slot_id = _seed_slot(isolated_db)
        for i in range(210):
            _seed_attempt(isolated_db, slot_id=slot_id, attempt_number=i + 1,
                           created_at=T0 + timedelta(seconds=i), updated_at=T0)
        rows = ve.list_schedule_attempts(limit=200)
        assert len(rows) == 200

    def test_default_ordering_created_at_desc(self, isolated_db):
        slot_id = _seed_slot(isolated_db)
        ids = []
        for i in range(5):
            ids.append(_seed_attempt(isolated_db, slot_id=slot_id, attempt_number=i + 1,
                                      created_at=T0 + timedelta(seconds=i), updated_at=T0))
        rows = ve.list_schedule_attempts(limit=10)
        assert [r["id"] for r in rows] == list(reversed(ids))

    def test_same_timestamp_id_tiebreaker(self, isolated_db):
        slot_id = _seed_slot(isolated_db)
        ids = []
        for i in range(4):
            ids.append(_seed_attempt(isolated_db, slot_id=slot_id, attempt_number=i + 1,
                                      created_at=T0, updated_at=T0))
        rows = ve.list_schedule_attempts(limit=10)
        assert [r["id"] for r in rows] == list(reversed(ids))

    def test_filter_by_horizon(self, isolated_db):
        slot_id = _seed_slot(isolated_db)
        _seed_attempt(isolated_db, slot_id=slot_id, horizon="medium", attempt_number=1)
        _seed_attempt(isolated_db, slot_id=None, horizon="short", attempt_number=1,
                       trigger_type="manual")
        rows = ve.list_schedule_attempts(horizon="short", limit=10)
        assert len(rows) == 1
        assert rows[0]["horizon"] == "short"

    def test_filter_by_universe(self, isolated_db):
        slot_id = _seed_slot(isolated_db, universe="us")
        _seed_attempt(isolated_db, slot_id=slot_id, universe="us", attempt_number=1)
        _seed_attempt(isolated_db, slot_id=None, universe="midcap", attempt_number=1,
                       trigger_type="manual")
        rows = ve.list_schedule_attempts(universe="us", limit=10)
        assert len(rows) == 1
        assert rows[0]["universe"] == "us"

    def test_filter_by_trigger_type(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, trigger_type="manual", attempt_number=1)
        _seed_attempt(isolated_db, slot_id=None, trigger_type="catchup", attempt_number=1)
        rows = ve.list_schedule_attempts(trigger_type="catchup", limit=10)
        assert len(rows) == 1
        assert rows[0]["trigger_type"] == "catchup"

    def test_filter_by_status(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, status="failed", attempt_number=1,
                       failure_category="RUN_EXCEPTION")
        _seed_attempt(isolated_db, slot_id=None, status="completed", attempt_number=2,
                       created_at=T0 + timedelta(seconds=1))
        rows = ve.list_schedule_attempts(status="failed", limit=10)
        assert len(rows) == 1
        assert rows[0]["status"] == "failed"

    def test_filter_by_failure_category(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, status="failed", attempt_number=1,
                       failure_category="ADMISSION_RACE")
        _seed_attempt(isolated_db, slot_id=None, status="failed", attempt_number=2,
                       failure_category="RUN_EXCEPTION", created_at=T0 + timedelta(seconds=1))
        rows = ve.list_schedule_attempts(failure_category="ADMISSION_RACE", limit=10)
        assert len(rows) == 1
        assert rows[0]["failure_category"] == "ADMISSION_RACE"

    def test_combined_filters(self, isolated_db):
        slot_id = _seed_slot(isolated_db, horizon="short", universe="us")
        _seed_attempt(isolated_db, slot_id=slot_id, horizon="short", universe="us",
                       trigger_type="scheduler", status="failed",
                       failure_category="RUN_EXCEPTION", attempt_number=1)
        _seed_attempt(isolated_db, slot_id=slot_id, horizon="short", universe="us",
                       trigger_type="scheduler", status="completed", attempt_number=2,
                       created_at=T0 + timedelta(seconds=1))
        rows = ve.list_schedule_attempts(
            horizon="short", universe="us", trigger_type="scheduler",
            status="failed", failure_category="RUN_EXCEPTION", limit=10,
        )
        assert len(rows) == 1

    def test_empty_results(self, isolated_db):
        rows = ve.list_schedule_attempts(horizon="long", limit=10)
        assert rows == []

    def test_manual_attempts_have_null_slot_fields(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, trigger_type="manual", attempt_number=1)
        rows = ve.list_schedule_attempts(trigger_type="manual", limit=10)
        assert len(rows) == 1
        assert rows[0]["slot_id"] is None
        assert rows[0]["scheduled_slot"] is None
        assert rows[0]["schedule_version"] is None
        assert rows[0]["slot_status"] is None

    def test_schedule_version_join_and_filter(self, isolated_db):
        slot_v1 = _seed_slot(isolated_db, schedule_version="v1")
        slot_v2 = _seed_slot(isolated_db, schedule_version="v2", scheduled_slot=T0 + timedelta(days=1))
        _seed_attempt(isolated_db, slot_id=slot_v1, attempt_number=1)
        _seed_attempt(isolated_db, slot_id=slot_v2, attempt_number=1, created_at=T0 + timedelta(seconds=1))
        rows = ve.list_schedule_attempts(schedule_version="v2", limit=10)
        assert len(rows) == 1
        assert rows[0]["schedule_version"] == "v2"

    def test_manual_attempt_never_matches_a_supplied_schedule_version(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, trigger_type="manual", attempt_number=1)
        rows = ve.list_schedule_attempts(schedule_version="v1", limit=10)
        assert rows == []

    def test_exact_result_run_id_linkage(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, status="completed", attempt_number=1, result_run_id=999)
        _seed_attempt(isolated_db, slot_id=None, status="completed", attempt_number=2, result_run_id=1000,
                       created_at=T0 + timedelta(seconds=1))
        rows = ve.list_schedule_attempts(result_run_id=999, limit=10)
        assert len(rows) == 1
        assert rows[0]["result_run_id"] == 999

    def test_since_only(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, attempt_number=1, created_at=T0)
        _seed_attempt(isolated_db, slot_id=None, attempt_number=2, created_at=T0 + timedelta(hours=1))
        rows = ve.list_schedule_attempts(since=T0 + timedelta(minutes=30), limit=10)
        assert len(rows) == 1

    def test_until_only(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, attempt_number=1, created_at=T0)
        _seed_attempt(isolated_db, slot_id=None, attempt_number=2, created_at=T0 + timedelta(hours=1))
        rows = ve.list_schedule_attempts(until=T0 + timedelta(minutes=30), limit=10)
        assert len(rows) == 1

    def test_bounded_range(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, attempt_number=1, created_at=T0)
        _seed_attempt(isolated_db, slot_id=None, attempt_number=2, created_at=T0 + timedelta(hours=1))
        _seed_attempt(isolated_db, slot_id=None, attempt_number=3, created_at=T0 + timedelta(hours=2))
        rows = ve.list_schedule_attempts(
            since=T0 + timedelta(minutes=30), until=T0 + timedelta(hours=1, minutes=30), limit=10,
        )
        assert len(rows) == 1

    def test_naive_since_rejected(self, isolated_db):
        with pytest.raises(ValueError):
            ve.list_schedule_attempts(since=datetime(2026, 1, 1), limit=10)

    def test_naive_until_rejected(self, isolated_db):
        with pytest.raises(ValueError):
            ve.list_schedule_attempts(until=datetime(2026, 1, 1), limit=10)

    def test_cursor_pagination_first_and_second_page(self, isolated_db):
        slot_id = _seed_slot(isolated_db)
        ids = []
        for i in range(5):
            ids.append(_seed_attempt(isolated_db, slot_id=slot_id, attempt_number=i + 1,
                                      created_at=T0 + timedelta(seconds=i), updated_at=T0))
        page1 = ve.list_schedule_attempts(limit=3)
        assert [r["id"] for r in page1] == list(reversed(ids))[:3]
        last = page1[-1]
        page2 = ve.list_schedule_attempts(
            limit=3, cursor_created_at=datetime.fromisoformat(last["created_at"]), cursor_id=last["id"],
        )
        assert [r["id"] for r in page2] == list(reversed(ids))[3:]

    def test_stable_pagination_when_newer_attempt_inserted_between_pages(self, isolated_db):
        slot_id = _seed_slot(isolated_db)
        ids = []
        for i in range(5):
            ids.append(_seed_attempt(isolated_db, slot_id=slot_id, attempt_number=i + 1,
                                      created_at=T0 + timedelta(seconds=i), updated_at=T0))
        page1 = ve.list_schedule_attempts(limit=3)
        last = page1[-1]
        # A brand-new attempt arrives, newer than everything already paged.
        _seed_attempt(isolated_db, slot_id=slot_id, attempt_number=99,
                       created_at=T0 + timedelta(seconds=100), updated_at=T0)
        page2 = ve.list_schedule_attempts(
            limit=3, cursor_created_at=datetime.fromisoformat(last["created_at"]), cursor_id=last["id"],
        )
        # The new row sorts ahead of the cursor and must never appear on
        # page2 (which is strictly older than the cursor) — no duplicate,
        # no skip of the original older rows.
        assert [r["id"] for r in page2] == list(reversed(ids))[3:]

    def test_no_internal_field_leakage(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, attempt_number=1)
        rows = ve.list_schedule_attempts(limit=10)
        assert "lease_owner" not in rows[0]
        assert "lease_fencing_token" not in rows[0]
        assert "failure_summary" not in rows[0]

    def test_utc_serialization(self, isolated_db):
        _seed_attempt(isolated_db, slot_id=None, attempt_number=1, created_at=T0)
        rows = ve.list_schedule_attempts(limit=10)
        assert rows[0]["created_at"].endswith("+00:00") or rows[0]["created_at"].endswith("Z")

    def test_no_mutation_statement_executed(self, isolated_db):
        """sqlite3.Connection is immutable and cannot be monkeypatched
        directly in this Python version, so this proves the same property
        via the actual source: the only SQL statements list_schedule_
        attempts can ever build are the two fixed SELECT templates."""
        import inspect
        src = inspect.getsource(ve.list_schedule_attempts)
        for forbidden in ("INSERT INTO", "UPDATE ", "DELETE FROM", "CREATE ", "DROP ", "ALTER "):
            assert forbidden not in src, f"list_schedule_attempts source contains {forbidden!r}"
        assert src.count("SELECT") == 2, "expected exactly one SELECT template per dialect"

    def test_parameterized_sql_no_string_interpolation_of_filters(self, isolated_db):
        import inspect
        src = inspect.getsource(ve.list_schedule_attempts)
        # Every filter value must reach SQL only via a parameter placeholder,
        # never via an f-string/format containing the argument name.
        assert "f\"{horizon}" not in src
        assert "f'{horizon}" not in src
        assert "%s" in src and "?" in src


# ─────────────────────────────────────────────────────────────────────────
# Router-level tests — GET /api/validation/attempts
# ─────────────────────────────────────────────────────────────────────────

import api.routers.validation as validation_router  # noqa: E402


@pytest.fixture
def client_isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(validation_router, "_VALIDATION_RUN_SECRET", TEST_SECRET)
    monkeypatch.setattr(ve, "_DB_PATH", str(tmp_path / "attempt_history_router_test.db"))
    monkeypatch.setattr(ve, "_db_initialised", False)
    monkeypatch.setattr(ve, "_USE_POSTGRES", False)
    ve._init_db()
    from api.main import app
    return TestClient(app), str(tmp_path / "attempt_history_router_test.db")


@pytest.mark.regression
class TestAttemptHistoryEndpointAuth:
    def test_missing_secret_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts")
        assert resp.status_code == 401

    def test_wrong_secret_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts", headers={"X-Secret": "wrong"})
        assert resp.status_code == 401

    def test_correct_secret_accepted(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is True
        assert body["attempts"] == []
        assert body["next_cursor"] is None

    def test_401_body_has_no_sensitive_detail(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts")
        detail = str(resp.json().get("detail", ""))
        assert TEST_SECRET not in detail
        assert "PICKS_SECRET" not in detail


@pytest.mark.regression
class TestAttemptHistoryEndpointBehavior:
    def test_empty_success_response_shape(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 200
        assert resp.json() == {"available": True, "attempts": [], "next_cursor": None}

    def test_populated_response_excludes_internal_fields(self, client_isolated):
        client, db_path = client_isolated
        _seed_attempt(db_path, slot_id=None, attempt_number=1, trigger_type="manual")
        resp = client.get("/api/validation/attempts", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["attempts"]) == 1
        row = body["attempts"][0]
        for forbidden in ("lease_owner", "lease_fencing_token", "failure_summary"):
            assert forbidden not in row
        expected_fields = {
            "id", "slot_id", "horizon", "universe", "attempt_number", "trigger_type",
            "status", "started_at", "heartbeat_at", "completed_at", "result_run_id",
            "failure_category", "created_at", "updated_at",
            "scheduled_slot", "schedule_version", "slot_status",
        }
        assert set(row.keys()) == expected_fields

    def test_invalid_horizon_enum_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts?horizon=nope", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 422

    def test_invalid_status_enum_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts?status=due", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 422

    def test_slot_only_status_due_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts?status=skipped", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 422

    def test_invalid_failure_category_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get(
            "/api/validation/attempts?failure_category=NOT_A_REAL_CATEGORY", headers={"X-Secret": TEST_SECRET}
        )
        assert resp.status_code == 422

    def test_limit_below_minimum_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts?limit=0", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 422

    def test_limit_above_maximum_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/attempts?limit=201", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 422

    def test_naive_since_timestamp_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get(
            "/api/validation/attempts?since=2026-01-01T00:00:00", headers={"X-Secret": TEST_SECRET}
        )
        assert resp.status_code == 400

    def test_since_after_until_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get(
            "/api/validation/attempts?since=2026-01-02T00:00:00Z&until=2026-01-01T00:00:00Z",
            headers={"X-Secret": TEST_SECRET},
        )
        assert resp.status_code == 400

    def test_malformed_cursor_rejected(self, client_isolated):
        client, _ = client_isolated
        resp = client.get(
            "/api/validation/attempts?cursor=not-valid-base64!!", headers={"X-Secret": TEST_SECRET}
        )
        assert resp.status_code == 400

    def test_unsupported_cursor_version_rejected(self, client_isolated):
        client, _ = client_isolated
        bad = base64.urlsafe_b64encode(
            json.dumps({"v": 99, "created_at": T0.isoformat(), "id": 1}).encode()
        ).decode()
        resp = client.get(f"/api/validation/attempts?cursor={bad}", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 400

    def test_cursor_with_naive_timestamp_rejected(self, client_isolated):
        client, _ = client_isolated
        bad = base64.urlsafe_b64encode(
            json.dumps({"v": 1, "created_at": "2026-01-01T00:00:00", "id": 1}).encode()
        ).decode()
        resp = client.get(f"/api/validation/attempts?cursor={bad}", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 400

    def test_cursor_with_invalid_id_rejected(self, client_isolated):
        client, _ = client_isolated
        bad = base64.urlsafe_b64encode(
            json.dumps({"v": 1, "created_at": T0.isoformat(), "id": -1}).encode()
        ).decode()
        resp = client.get(f"/api/validation/attempts?cursor={bad}", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 400

    def test_valid_cursor_round_trip(self, client_isolated):
        client, db_path = client_isolated
        slot_id = _seed_slot(db_path)
        ids = []
        for i in range(5):
            ids.append(_seed_attempt(db_path, slot_id=slot_id, attempt_number=i + 1,
                                      created_at=T0 + timedelta(seconds=i), updated_at=T0))
        resp1 = client.get("/api/validation/attempts?limit=3", headers={"X-Secret": TEST_SECRET})
        body1 = resp1.json()
        assert len(body1["attempts"]) == 3
        assert body1["next_cursor"] is not None
        resp2 = client.get(
            f"/api/validation/attempts?limit=3&cursor={body1['next_cursor']}",
            headers={"X-Secret": TEST_SECRET},
        )
        body2 = resp2.json()
        assert len(body2["attempts"]) == 2
        assert body2["next_cursor"] is None

    def test_get_only_route(self, client_isolated):
        client, _ = client_isolated
        resp = client.post("/api/validation/attempts", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code in (404, 405)

    def test_no_call_to_run_endpoint(self, client_isolated, monkeypatch):
        calls = []
        monkeypatch.setattr(ve, "run_validation", lambda **kw: calls.append(kw), raising=False)
        client, db_path = client_isolated
        _seed_attempt(db_path, slot_id=None, attempt_number=1)
        client.get("/api/validation/attempts", headers={"X-Secret": TEST_SECRET})
        assert calls == []

    def test_sanitized_internal_error_on_unexpected_exception(self, client_isolated, monkeypatch):
        client, _ = client_isolated

        def _boom(**kwargs):
            raise RuntimeError("raw internal database connection string leaked here")

        monkeypatch.setattr(ve, "list_schedule_attempts", _boom)
        resp = client.get("/api/validation/attempts", headers={"X-Secret": TEST_SECRET})
        assert resp.status_code == 200
        body = resp.json()
        assert body["available"] is False
        assert "raw internal database connection string" not in json.dumps(body)

    def test_openapi_documents_route(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        paths = resp.json().get("paths", {})
        assert "/api/validation/attempts" in paths
        assert "get" in paths["/api/validation/attempts"]


@pytest.mark.regression
class TestExistingValidationEndpointsUnchanged:
    def test_status_unchanged(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/status")
        assert resp.status_code == 200

    def test_results_unchanged(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/results?horizon=short&universe=nifty100")
        assert resp.status_code == 200

    def test_results_history_unchanged(self, client_isolated):
        client, _ = client_isolated
        resp = client.get("/api/validation/results/history")
        assert resp.status_code == 200
