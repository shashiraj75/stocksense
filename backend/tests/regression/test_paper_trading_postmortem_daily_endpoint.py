"""
Trade Postmortem Engine, Stage 3 (Sprint 1 evidence-provenance rewrite) —
regression tests for GET /api/paper-trading/postmortem/daily.

Follows the exact fake-connection mocking pattern established in
test_paper_trading_postmortem_endpoint.py: no real database, no network.
"""
import datetime as dt
import time
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
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )


def _auth(sub: str = "user-aaa") -> dict:
    return {"Authorization": f"Bearer {_token(sub)}"}


class _RecordingConn:
    """Supports both `.fetchall()` (the daily trade-list query) and
    `.fetchone()` (the per-trade entry-snapshot lookup) against the same
    patched connection."""

    def __init__(self, main_rows, snapshot_rows_by_trade_id=None):
        self.calls = []
        self._main_rows = list(main_rows)
        self._snapshot_rows = snapshot_rows_by_trade_id or {}
        self._last_params = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._last_params = params
        return self

    def fetchall(self):
        return list(self._main_rows)

    def fetchone(self):
        trade_id = self._last_params[0] if self._last_params else None
        return self._snapshot_rows.get(trade_id)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _row(
    *,
    trade_id=1,
    owner="user-aaa",
    status="CLOSED",
    symbol="AAPL",
    market="US",
    quantity=10,
    entry_price=100.0,
    exit_price=120.0,
    stop_loss=90.0,
    target_price=130.0,
    opened_at=dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc),
    closed_at=dt.datetime(2026, 6, 2, 15, 0, tzinfo=dt.timezone.utc),
    trade_management_mode="manual",
    exit_reason="MANUAL",
):
    return (
        trade_id, owner, status, symbol, market, quantity, entry_price, exit_price,
        stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason,
        None, None, None, None, None, None, None, None, None, None, None,
    )


def _get_daily(client, params, main_rows, snapshot_rows=None, auth_sub="user-aaa"):
    recorder = _RecordingConn(main_rows=main_rows, snapshot_rows_by_trade_id=snapshot_rows)

    @contextmanager
    def _fake_conn():
        yield recorder

    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
    ):
        resp = client.get("/api/paper-trading/postmortem/daily", params=params, headers=_auth(auth_sub))
    return resp, recorder


@pytest.mark.regression
class TestDailyPostmortemEndpoint:
    def test_multi_trade_day_returns_200_with_evidence_attribution(self, client):
        rows = [
            _row(trade_id=1, exit_price=120.0, closed_at=dt.datetime(2026, 6, 2, 15, 0, tzinfo=dt.timezone.utc)),
            _row(trade_id=2, exit_price=80.0, closed_at=dt.datetime(2026, 6, 2, 16, 0, tzinfo=dt.timezone.utc)),
        ]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == "2.0.0"
        assert body["summary"]["trade_count"] == 2
        assert body["summary"]["win_count"] == 1
        assert body["summary"]["loss_count"] == 1
        assert len(body["trades"]) == 2
        for t in body["trades"]:
            attribution = t["attribution"]
            assert "claims" in attribution
            assert "signal_scorecard" in attribution
            assert "contributor_assessments" in attribution
            assert "primary_contributor" in attribution
            assert "thesis_verdict" in attribution
            # Every claim, per Sprint 1 contract, must have a rule ID.
            for claim in attribution["claims"]:
                assert claim["rule_id"]
                assert claim["rule_version"]

    def test_response_no_longer_has_narrative_or_root_cause_fields(self, client):
        rows = [_row(trade_id=1, exit_price=120.0)]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        body = resp.json()
        t = body["trades"][0]
        assert "narrative" not in t
        assert "root_cause_breakdown" not in body["summary"]

    def test_empty_day_returns_200_not_404(self, client):
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [])
        assert resp.status_code == 200
        body = resp.json()
        assert body["summary"]["trade_count"] == 0
        assert body["trades"] == []

    def test_malformed_date_returns_400(self, client):
        resp, _ = _get_daily(client, {"date": "not-a-date", "market": "US"}, [])
        assert resp.status_code == 400

    def test_cross_user_isolation_filters_by_authenticated_user(self, client):
        rows = [_row(trade_id=1, owner="user-aaa", exit_price=120.0)]
        resp, recorder = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows, auth_sub="user-aaa")
        assert resp.status_code == 200
        sql, params = recorder.calls[0]
        assert "user_id = %s" in sql
        assert params[0] == "user-aaa"

    def test_other_users_trade_excluded_even_within_window(self, client):
        rows = [_row(trade_id=1, owner="someone-else", exit_price=120.0)]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        assert resp.status_code == 200
        assert resp.json()["trades"] == []

    def test_market_filter_adds_sql_predicate(self, client):
        resp, recorder = _get_daily(client, {"date": "2026-06-02", "market": "IN"}, [])
        assert resp.status_code == 200
        sql, params = recorder.calls[0]
        assert "AND market = %s" in sql
        assert params[-1] == "IN"

    def test_market_all_omits_market_predicate(self, client):
        resp, recorder = _get_daily(client, {"date": "2026-06-02", "market": "ALL"}, [])
        assert resp.status_code == 200
        sql, params = recorder.calls[0]
        assert "market = %s" not in sql

    def test_market_local_day_boundary_us_trade_after_local_midnight_excluded(self, client):
        still_june2_et = _row(trade_id=1, market="US", closed_at=dt.datetime(2026, 6, 3, 3, 0, tzinfo=dt.timezone.utc))
        already_june3_et = _row(trade_id=2, market="US", closed_at=dt.datetime(2026, 6, 3, 5, 30, tzinfo=dt.timezone.utc))

        resp_june2, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, [still_june2_et, already_june3_et])
        assert {t["trade_id"] for t in resp_june2.json()["trades"]} == {1}

        resp_june3, _ = _get_daily(client, {"date": "2026-06-03", "market": "US"}, [still_june2_et, already_june3_et])
        assert {t["trade_id"] for t in resp_june3.json()["trades"]} == {2}

    def test_missing_auth_header_returns_401(self, client):
        resp = client.get("/api/paper-trading/postmortem/daily", params={"date": "2026-06-02"})
        assert resp.status_code == 401

    def test_daily_summary_uses_recurring_supported_contributors_not_root_cause(self, client):
        rows = [_row(trade_id=1, exit_price=95.0, stop_loss=10.0, target_price=101.0, exit_reason="MANUAL")]
        resp, _ = _get_daily(client, {"date": "2026-06-02", "market": "US"}, rows)
        body = resp.json()
        assert "recurring_supported_contributors" in body["summary"]
        assert "recurring_conflicting_contributors" in body["summary"]
        assert "recurring_not_assessable_count" in body["summary"]
