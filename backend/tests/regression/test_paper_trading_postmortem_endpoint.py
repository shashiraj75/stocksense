"""
Trade Postmortem Engine, Phase 1 — regression tests for
GET /api/paper-trading/postmortem/{trade_id}.

Follows the exact mocking pattern established in
test_paper_trading_provenance.py: a fake psycopg-shaped connection recorder
patched over api.routers.paper_trading._conn, so these tests never touch a
real database or network — proving the endpoint itself makes no external
calls, not just asserting it in prose.
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
    def __init__(self, fetchone_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

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
    closed_at=dt.datetime(2026, 6, 2, tzinfo=dt.timezone.utc),
    trade_management_mode="manual",
    exit_reason="MANUAL",
):
    return (
        trade_id, owner, status, symbol, market, quantity, entry_price, exit_price,
        stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason,
        None, None, None, None, None, None, None, None, None, None, None,
    )


def _get_postmortem(client, trade_id, fetchone_results, auth_sub="user-aaa"):
    recorder = _RecordingConn(fetchone_results=fetchone_results)

    @contextmanager
    def _fake_conn():
        yield recorder

    with patch.object(
        __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _fake_conn
    ):
        resp = client.get(f"/api/paper-trading/postmortem/{trade_id}", headers=_auth(auth_sub))
    return resp, recorder


@pytest.mark.regression
class TestPostmortemEndpoint:
    def test_closed_winning_trade_returns_computed_postmortem(self, client):
        resp, _ = _get_postmortem(client, 1, [_row(exit_price=120.0)])
        assert resp.status_code == 200
        body = resp.json()
        assert body["outcome"] == "WIN"
        assert body["realized_pnl_abs"] == pytest.approx(200.0)
        assert body["realized_pnl_pct"] == pytest.approx(20.0)
        assert body["schema_version"]
        assert body["calculation_version"]
        assert body["evidence_completeness"] == "LIMITED"

    def test_closed_losing_trade(self, client):
        resp, _ = _get_postmortem(client, 1, [_row(exit_price=80.0)])
        assert resp.status_code == 200
        assert resp.json()["outcome"] == "LOSS"

    def test_trade_not_found_returns_404(self, client):
        resp, _ = _get_postmortem(client, 999, [None])
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_trade_belonging_to_another_user_returns_identical_404(self, client):
        """Stricter than this router's other trade-scoped endpoints (which
        return 403 'Not your trade', confirming the ID exists) — this
        endpoint must never let a caller distinguish 'not found' from
        'exists but isn't yours'."""
        not_found_resp, _ = _get_postmortem(client, 999, [None])
        other_user_resp, _ = _get_postmortem(client, 1, [_row(owner="someone-else")])
        assert other_user_resp.status_code == 404
        assert other_user_resp.json() == not_found_resp.json()

    def test_open_trade_returns_409_with_stable_error_code(self, client):
        resp, _ = _get_postmortem(client, 1, [_row(status="OPEN")])
        assert resp.status_code == 409
        detail = resp.json()["detail"]
        assert detail["error_code"] == "TRADE_NOT_CLOSED"

    def test_missing_auth_header_returns_401(self, client):
        resp = client.get("/api/paper-trading/postmortem/1")
        assert resp.status_code == 401

    def test_only_select_queries_no_writes(self, client):
        """No database writes — the postmortem endpoint never persists
        anything. Stage 2 adds a second SELECT (the entry-snapshot fetch,
        by paper_trade_id) alongside the original trade-row SELECT — both
        read-only, so this now asserts on that pair rather than a single
        query."""
        resp, recorder = _get_postmortem(client, 1, [_row()])
        assert resp.status_code == 200
        assert len(recorder.calls) == 2
        for sql, _ in recorder.calls:
            assert sql.strip().upper().startswith("SELECT")
            for verb in ("INSERT", "UPDATE", "DELETE"):
                assert verb not in sql.upper()

    def test_manual_close_evidence(self, client):
        resp, _ = _get_postmortem(client, 1, [_row(exit_reason="MANUAL", trade_management_mode="manual")])
        body = resp.json()
        assert body["exit_mechanism"] == "MANUAL"
        assert body["auto_close_timing_evidence"] == "NOT_APPLICABLE"

    def test_never_invokes_price_path_enhancement(self, client, monkeypatch):
        """Trade Postmortem Sprint 3A, Stage H2C — GET remains read-only
        even when the price-path flag is on: it must never acquire
        market data, claim a lease, create evidence, or generate a
        report."""
        import api.routers.paper_trading as ptr
        monkeypatch.setenv("TRADE_POSTMORTEM_PRICE_PATH_ENABLED", "1")
        spy_called = []
        monkeypatch.setattr(ptr, "_attempt_price_path_enhancement", lambda **kw: spy_called.append(kw) or None)
        resp, recorder = _get_postmortem(client, 1, [_row()])
        assert resp.status_code == 200
        assert spy_called == []
        assert len(recorder.calls) == 2  # unchanged — no new query was added

    def test_auto_target_close_evidence(self, client):
        resp, _ = _get_postmortem(
            client, 1, [_row(exit_reason="TARGET_HIT", trade_management_mode="auto")]
        )
        body = resp.json()
        assert body["exit_mechanism"] == "TARGET_HIT"
        assert body["auto_close_timing_evidence"] == "CLIENT_REPORTED_UNVERIFIED"
