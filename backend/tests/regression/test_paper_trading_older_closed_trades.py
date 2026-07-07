"""
Paper Trading Trade History Architecture Hardening — regression tests for
GET /api/paper-trading/closed-trades/older: the lazy, bounded, market- and
horizon-scoped endpoint that serves rows beyond the initial 5 returned by
GET /portfolio's closed_trade_history_by_horizon. Verifies market/horizon
scoping is actually sent to the DB layer, keyset-pagination cursor
correctness (no duplicate/skipped row across two pages), the has_more
exhausted signal, and that OPEN trades can never be returned (baked into
the query, not filtered client-side).
"""
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


def _auth(sub: str = "user-aaa") -> dict:
    token = jwt.encode(
        {"sub": sub, "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
        TEST_SECRET, algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


class _RecordingConn:
    def __init__(self, fetchall_results=None):
        self.calls = []
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _row(id, market="IN", horizon="short", closed_at_str="2026-01-01 00:00:00+00"):
    """Matches _TRADE_ROW_COLUMNS order exactly: id, symbol, market, quantity,
    entry_price, exit_price, status, signal, horizon, opened_at, closed_at,
    stop_loss, target_price, trade_management_mode, exit_reason."""
    import datetime
    closed = datetime.datetime.fromisoformat(closed_at_str)
    return (id, "AAPL", market, 1, 100.0, 110.0, "CLOSED", "BUY", horizon,
            closed, closed, 90.0, 120.0, "manual", "TARGET_HIT")


@pytest.mark.regression
class TestOlderClosedTradesAuth:
    def test_missing_token_rejected_before_any_db_call(self, client):
        with patch("api.routers.paper_trading._conn") as mock_conn:
            resp = client.get("/api/paper-trading/closed-trades/older", params={"market": "IN", "horizon": "short"})
            assert resp.status_code == 401
            mock_conn.assert_not_called()


@pytest.mark.regression
class TestOlderClosedTradesScoping:
    def test_market_and_horizon_are_sent_as_query_params_to_the_db(self, client):
        conn = _RecordingConn(fetchall_results=[[_row(1)]])
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/closed-trades/older",
                params={"market": "US", "horizon": "medium"},
                headers=_auth(),
            )
        assert resp.status_code == 200
        sql, params = conn.calls[0]
        assert "market = %s" in sql
        assert "status = 'CLOSED'" in sql
        assert "horizon = %s" in sql
        # user_id, market, horizon, [limit] — market/horizon values present verbatim
        assert "US" in params
        assert "medium" in params
        body = resp.json()
        assert body["market"] == "US"
        assert body["horizon"] == "medium"

    def test_unclassified_horizon_uses_not_in_clause_not_a_bound_param(self, client):
        conn = _RecordingConn(fetchall_results=[[]])
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/closed-trades/older",
                params={"market": "IN", "horizon": "unclassified"},
                headers=_auth(),
            )
        assert resp.status_code == 200
        sql, _params = conn.calls[0]
        assert "horizon IS NULL OR horizon NOT IN" in sql

    def test_query_always_restricts_to_closed_status(self, client):
        # The endpoint must never be able to return an OPEN trade — this is
        # enforced by the SQL itself (status = 'CLOSED'), not a client-side
        # filter, so a malicious or buggy caller can't bypass it.
        conn = _RecordingConn(fetchall_results=[[]])
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            client.get(
                "/api/paper-trading/closed-trades/older",
                params={"market": "IN", "horizon": "short"},
                headers=_auth(),
            )
        sql, _params = conn.calls[0]
        assert "status = 'CLOSED'" in sql


@pytest.mark.regression
class TestOlderClosedTradesPagination:
    def test_has_more_true_when_extra_row_fetched(self, client):
        # limit=2 requested; DB asked for limit+1=3 and returns exactly 3 —
        # the 3rd is trimmed off and signals more remain.
        conn = _RecordingConn(fetchall_results=[[_row(3), _row(2), _row(1)]])
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/closed-trades/older",
                params={"market": "IN", "horizon": "short", "limit": 2},
                headers=_auth(),
            )
        body = resp.json()
        assert len(body["trades"]) == 2
        assert body["has_more"] is True
        assert body["next_cursor"] is not None
        assert body["next_cursor"]["before_id"] == body["trades"][-1]["id"]

    def test_has_more_false_on_final_page(self, client):
        conn = _RecordingConn(fetchall_results=[[_row(2), _row(1)]])
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/closed-trades/older",
                params={"market": "IN", "horizon": "short", "limit": 5},
                headers=_auth(),
            )
        body = resp.json()
        assert len(body["trades"]) == 2
        assert body["has_more"] is False
        assert body["next_cursor"] is None

    def test_cursor_params_are_forwarded_for_the_next_page(self, client):
        # Simulates requesting page 2 using page 1's returned cursor —
        # confirms the keyset values reach the query with no duplication of
        # the already-seen row (page 2's WHERE clause excludes it via the
        # (closed_at, id) < (cursor) comparison, not by re-filtering rows
        # already returned in page 1 client-side).
        conn = _RecordingConn(fetchall_results=[[_row(1)]])
        with patch("api.routers.paper_trading._conn", side_effect=lambda: conn):
            resp = client.get(
                "/api/paper-trading/closed-trades/older",
                params={
                    "market": "IN", "horizon": "short",
                    "before_closed_at": "2026-01-05T00:00:00+00:00",
                    "before_id": 5,
                },
                headers=_auth(),
            )
        assert resp.status_code == 200
        sql, params = conn.calls[0]
        assert "(closed_at, id) < (%s::timestamptz, %s)" in sql
        assert "2026-01-05T00:00:00+00:00" in params
        assert 5 in params
