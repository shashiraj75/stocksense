"""
Trade Postmortem Engine, Sprint 2 correction phase (Stage 3) —
regression tests for POST /api/paper-trading/postmortem/{trade_id}/
generate, now aligned with the lease-based outbox lifecycle: it finds
or creates an outbox row for the current version triple, returns an
already-persisted report idempotently, claims and generates for a
PENDING/eligible-retry/stale-lease row, and returns a stable
IN_PROGRESS response rather than running concurrent duplicate work when
another valid lease currently owns the row.

Uses a fuller in-memory stateful fake (one shared dict of tables) rather
than a simple fetchone queue, since this endpoint now issues several
interdependent statements (trade SELECT, idempotent outbox insert, report
lookup, lease claim, report insert, outbox mark-terminal) whose ordering
and content depend on each other.
"""
import datetime as dt
import json as _json
import time as _time
from contextlib import contextmanager
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "regression-test-jwt-secret-at-least-32-bytes-long"
TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _token(sub: str = "user-aaa") -> str:
    return jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": _time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


def _trade_row(
    *, trade_id=1, owner="user-aaa", status="CLOSED", symbol="AAPL", market="US",
    quantity=10, entry_price=100.0, exit_price=120.0, stop_loss=90.0, target_price=130.0,
    opened_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    closed_at=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
    trade_management_mode="manual", exit_reason="MANUAL",
):
    return (
        trade_id, owner, status, symbol, market, quantity, entry_price, exit_price,
        stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason,
        None, None, None, None, None, None, None, None, None, None, None,
    )


class _FakeConn:
    """One shared in-memory state dict (trades/outbox_rows/report_rows)
    per test, mutated across however many _FakeConn instances the router
    constructs (this endpoint only ever uses one `with _conn() as conn:`
    block, so one instance suffices, but the shape mirrors the other
    stateful fakes in this test suite for consistency)."""

    def __init__(self, shared: dict):
        self.shared = shared
        self.calls = []
        self._pending = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._pending = self._process(sql, params or ())
        return self

    def fetchone(self):
        return self._pending

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self

    def _process(self, sql, params):
        shared = self.shared
        stripped = sql.strip()

        if stripped.startswith("SELECT id, user_id, status, symbol, market, quantity, entry_price, exit_price"):
            (trade_id,) = params
            return shared["trades"].get(trade_id)

        if stripped.startswith("SELECT") and "paper_trade_entry_snapshot" in sql:
            return None  # no entry snapshot in any of these tests

        if stripped.startswith("SELECT") and "paper_trade_exit_snapshot" in sql:
            return None  # no exit snapshot — LIMITED_EVIDENCE is the expected report status

        if stripped.startswith("INSERT INTO paper_trade_postmortem_outbox"):
            trade_id, user_id, schema_v, calc_v, rules_v, source_request_id = params
            key = (trade_id, schema_v, calc_v, rules_v)
            if key in shared["outbox_by_key"]:
                return None
            new_id = shared["next_outbox_id"]
            shared["next_outbox_id"] += 1
            shared["outbox_by_key"][key] = new_id
            shared["outbox_rows"][new_id] = {
                "id": new_id, "paper_trade_id": trade_id, "user_id": user_id,
                "requested_report_schema_version": schema_v, "requested_calculation_version": calc_v,
                "requested_rules_version": rules_v, "status": "PENDING", "attempt_count": 0,
                "source_request_id": source_request_id, "claimed_by": None,
                "next_attempt_at": None, "lease_expires_at": None,
            }
            return (new_id,)

        if stripped.startswith("SELECT id FROM paper_trade_postmortem_outbox"):
            trade_id, schema_v, calc_v, rules_v = params
            existing_id = shared["outbox_by_key"].get((trade_id, schema_v, calc_v, rules_v))
            return (existing_id,) if existing_id is not None else None

        if "WITH claimable AS" in sql:
            max_attempts, outbox_id, user_id, lease_seconds, claimant = params
            row = shared["outbox_rows"].get(outbox_id)
            if row is None or row["user_id"] != user_id:
                return None
            now = dt.datetime.now(dt.timezone.utc)
            claimable = (
                row["status"] == "PENDING"
                or (row["status"] == "FAILED_RETRYABLE"
                    and (row["next_attempt_at"] is None or row["next_attempt_at"] <= now))
                or (row["status"] == "GENERATING"
                    and row["lease_expires_at"] is not None and row["lease_expires_at"] <= now)
            )
            if not claimable:
                return None
            row["attempt_count"] += 1
            if row["attempt_count"] > max_attempts:
                row["status"] = "FAILED_TERMINAL"
                row["claimed_by"] = None
            else:
                row["status"] = "GENERATING"
                row["claimed_by"] = claimant
                row["lease_expires_at"] = now + dt.timedelta(seconds=lease_seconds)
            return (
                row["id"], row["paper_trade_id"], row["user_id"], row["requested_report_schema_version"],
                row["requested_calculation_version"], row["requested_rules_version"], row["status"],
                row["attempt_count"], row["source_request_id"], row["claimed_by"],
            )

        if stripped.startswith("SELECT id, paper_trade_id, user_id, market, report_trading_date") or (
            stripped.startswith("SELECT") and "paper_trade_postmortem_report" in sql and "user_id = %s" in sql
        ):
            paper_trade_id, user_id, schema_v, calc_v, rules_v = params
            for row in shared["report_rows"].values():
                if (row[1], row[2], row[6], row[7], row[8]) == (paper_trade_id, user_id, schema_v, calc_v, rules_v):
                    return row
            return None

        if stripped.startswith("SELECT") and "paper_trade_postmortem_report" in sql and "WHERE paper_trade_id = %s AND report_schema_version" in sql:
            paper_trade_id, schema_v, calc_v, rules_v = params
            for row in shared["report_rows"].values():
                if (row[1], row[6], row[7], row[8]) == (paper_trade_id, schema_v, calc_v, rules_v):
                    return row
            return None

        if stripped.startswith("INSERT INTO paper_trade_postmortem_report"):
            (paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v, bundle_v,
             ev_hash, status, structured, ev_items, claims, manifest, gaps, warnings, supersedes_id) = params
            key = (paper_trade_id, schema_v, calc_v, rules_v)
            for row in shared["report_rows"].values():
                if (row[1], row[6], row[7], row[8]) == key:
                    return None
            new_id = shared["next_report_id"]
            shared["next_report_id"] += 1
            row = (new_id, paper_trade_id, user_id, market, trading_date, tz, schema_v, calc_v, rules_v,
                   bundle_v, ev_hash, status, _json.loads(structured), _json.loads(ev_items),
                   _json.loads(claims), _json.loads(manifest), _json.loads(gaps), _json.loads(warnings), supersedes_id)
            shared["report_rows"][new_id] = row
            return row

        if "SET status = %s, completed_at = now()" in sql:
            status, outbox_id, claimed_by = params
            row = shared["outbox_rows"].get(outbox_id)
            if row is None or row["status"] != "GENERATING" or row["claimed_by"] != claimed_by:
                return None
            row["status"] = status
            row["claimed_by"] = None
            return (outbox_id,)

        return None


def _new_shared(trades: list[dict]) -> dict:
    return {
        "trades": {t["trade_id"]: _trade_row(**t) for t in trades},
        "outbox_by_key": {}, "outbox_rows": {}, "next_outbox_id": 1,
        "report_rows": {}, "next_report_id": 1,
    }


def _generate(client, trade_id, shared, auth_sub="user-aaa"):
    conn = _FakeConn(shared)

    @contextmanager
    def _fake_conn():
        yield conn

    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
    ):
        resp = client.post(f"/api/paper-trading/postmortem/{trade_id}/generate", headers=_auth(auth_sub))
    return resp, conn


@pytest.mark.regression
class TestGenerateEndpoint:
    def test_closed_trade_generates_report(self, client):
        shared = _new_shared([{"trade_id": 1}])
        resp, _ = _generate(client, 1, shared)
        assert resp.status_code == 200
        body = resp.json()
        assert body["trade_id"] == 1
        assert body["generation_status"] == "GENERATED"
        assert body["generated"] is True
        assert body["status"] == "LIMITED_EVIDENCE"  # no entry/exit snapshot in this fake

    def test_repeated_call_is_idempotent_already_complete(self, client):
        shared = _new_shared([{"trade_id": 1}])
        first, _ = _generate(client, 1, shared)
        second, _ = _generate(client, 1, shared)
        assert first.json()["generated"] is True
        assert second.json()["generation_status"] == "ALREADY_COMPLETE"
        assert second.json()["generated"] is False
        assert len(shared["report_rows"]) == 1

    def test_trade_not_found_404(self, client):
        shared = _new_shared([])
        resp, _ = _generate(client, 999, shared)
        assert resp.status_code == 404

    def test_trade_belonging_to_another_user_identical_404(self, client):
        shared_a = _new_shared([])
        not_found, _ = _generate(client, 999, shared_a)
        shared_b = _new_shared([{"trade_id": 1, "owner": "someone-else"}])
        other_user, _ = _generate(client, 1, shared_b)
        assert other_user.status_code == 404
        assert other_user.json() == not_found.json()

    def test_open_trade_returns_409(self, client):
        shared = _new_shared([{"trade_id": 1, "status": "OPEN"}])
        resp, _ = _generate(client, 1, shared)
        assert resp.status_code == 409
        assert resp.json()["detail"]["error_code"] == "TRADE_NOT_CLOSED"

    def test_missing_auth_401(self, client):
        resp = client.post("/api/paper-trading/postmortem/1/generate")
        assert resp.status_code == 401

    def test_never_alters_the_trade_row(self, client):
        shared = _new_shared([{"trade_id": 1}])
        resp, conn = _generate(client, 1, shared)
        assert resp.status_code == 200
        for sql, _ in conn.calls:
            assert "UPDATE paper_trades" not in sql

    def test_in_progress_lease_returns_stable_status_no_duplicate_generation(self, client):
        """A non-expired GENERATING lease from a concurrent attempt must
        never be raced — the endpoint returns IN_PROGRESS instead."""
        shared = _new_shared([{"trade_id": 1}])
        # Pre-seed an outbox row already GENERATING under a valid lease.
        shared["outbox_by_key"][(1, "1.0.0", "1.1.0", "2.0.0")] = 1
        shared["outbox_rows"][1] = {
            "id": 1, "paper_trade_id": 1, "user_id": "user-aaa",
            "requested_report_schema_version": "1.0.0", "requested_calculation_version": "1.1.0",
            "requested_rules_version": "2.0.0", "status": "GENERATING", "attempt_count": 1,
            "source_request_id": None, "claimed_by": "other-worker-token",
            "next_attempt_at": None,
            "lease_expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60),
        }
        shared["next_outbox_id"] = 2
        resp, _ = _generate(client, 1, shared)
        assert resp.status_code == 200
        body = resp.json()
        assert body["generation_status"] == "GENERATION_IN_PROGRESS"
        assert body["generated"] is False
        assert len(shared["report_rows"]) == 0


@pytest.mark.regression
class TestPricePathEnhancementWiring:
    """Trade Postmortem Sprint 3A, Stage H2 — proves the endpoint calls
    (or correctly does NOT call) _attempt_price_path_enhancement,
    without re-exercising that function's own internals (already
    covered by tests/unit/test_price_path_generation.py and
    tests/postgres_integration/test_price_path_generation.py)."""

    def test_flag_off_by_default_never_calls_enhancement(self, client, monkeypatch):
        import api.routers.paper_trading as ptr
        spy_called = []
        monkeypatch.setattr(ptr, "_attempt_price_path_enhancement", lambda **kw: spy_called.append(kw) or None)
        shared = _new_shared([{"trade_id": 1}])
        resp, _ = _generate(client, 1, shared)
        assert resp.status_code == 200
        assert spy_called == []

    def test_flag_on_generated_path_calls_enhancement_after_connection_released(self, client, monkeypatch):
        import api.routers.paper_trading as ptr
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        spy_called = []

        def _spy(**kw):
            spy_called.append(kw)
            return None

        monkeypatch.setattr(ptr, "_attempt_price_path_enhancement", _spy)
        shared = _new_shared([{"trade_id": 1}])
        resp, _ = _generate(client, 1, shared)
        assert resp.status_code == 200
        assert len(spy_called) == 1
        assert spy_called[0]["trade_id"] == 1
        assert spy_called[0]["user_id"] == "user-aaa"

    def test_flag_on_already_complete_path_also_calls_enhancement(self, client, monkeypatch):
        import api.routers.paper_trading as ptr
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        spy_called = []
        monkeypatch.setattr(ptr, "_attempt_price_path_enhancement", lambda **kw: spy_called.append(kw) or None)
        shared = _new_shared([{"trade_id": 1}])
        _generate(client, 1, shared)  # first call: GENERATED
        spy_called.clear()
        resp, _ = _generate(client, 1, shared)  # second call: ALREADY_COMPLETE
        assert resp.status_code == 200
        assert resp.json()["generation_status"] == "ALREADY_COMPLETE"
        assert len(spy_called) == 1

    def test_flag_on_in_progress_never_calls_enhancement(self, client, monkeypatch):
        import api.routers.paper_trading as ptr
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        spy_called = []
        monkeypatch.setattr(ptr, "_attempt_price_path_enhancement", lambda **kw: spy_called.append(kw) or None)
        shared = _new_shared([{"trade_id": 1}])
        shared["outbox_by_key"][(1, "1.0.0", "1.1.0", "2.0.0")] = 1
        shared["outbox_rows"][1] = {
            "id": 1, "paper_trade_id": 1, "user_id": "user-aaa",
            "requested_report_schema_version": "1.0.0", "requested_calculation_version": "1.1.0",
            "requested_rules_version": "2.0.0", "status": "GENERATING", "attempt_count": 1,
            "source_request_id": None, "claimed_by": "other-worker-token",
            "next_attempt_at": None,
            "lease_expires_at": dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=60),
        }
        shared["next_outbox_id"] = 2
        resp, _ = _generate(client, 1, shared)
        assert resp.json()["generation_status"] == "GENERATION_IN_PROGRESS"
        assert spy_called == []

    def test_enhancement_result_overlays_response_fields(self, client, monkeypatch):
        import api.routers.paper_trading as ptr
        from services.postmortem.report_store import PersistedReport
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")

        def _fake_enhanced(**kw):
            return PersistedReport(
                id=999, paper_trade_id=kw["trade_id"], user_id=kw["user_id"], market="US",
                report_trading_date=dt.date(2026, 6, 2), market_timezone="America/New_York",
                report_schema_version="1.1.0", calculation_version="enhanced-calc-version",
                attribution_rules_version="2.0.0", evidence_bundle_version="1.0.0",
                evidence_hash="deadbeef", status="LIMITED_EVIDENCE", structured_report={},
                evidence_items=[], claims=[], source_manifest={}, evidence_gaps=[], warnings=[],
                supersedes_report_id=1,
            )

        monkeypatch.setattr(ptr, "_attempt_price_path_enhancement", _fake_enhanced)
        shared = _new_shared([{"trade_id": 1}])
        resp, _ = _generate(client, 1, shared)
        body = resp.json()
        assert body["report_schema_version"] == "1.1.0"
        assert body["calculation_version"] == "enhanced-calc-version"
        assert body["generation_status"] == "GENERATED"  # unchanged — enhancement doesn't invent a new status

    def test_enhancement_returning_none_leaves_response_unchanged(self, client, monkeypatch):
        import api.routers.paper_trading as ptr
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        monkeypatch.setattr(ptr, "_attempt_price_path_enhancement", lambda **kw: None)
        shared = _new_shared([{"trade_id": 1}])
        resp, _ = _generate(client, 1, shared)
        body = resp.json()
        assert body["report_schema_version"] == "1.0.0"
        assert body["generation_status"] == "GENERATED"
