"""
Real PostgreSQL Verification / Retention phase, Stage 12 — regression
tests for the market-specific reset's idempotency cleanup fix.

Finding: a market-specific `POST /reset?market=IN` (or `US`) previously
left EVERY idempotency row untouched, including COMPLETED rows whose
stored response referenced a trade in the market just being wiped — a
stale replay of such a key would return a response pointing at a
paper_trade_id that no longer exists. Fixed by deleting only COMPLETED
rows whose stored `response_body->>'market'` matches the reset market
(the only reliable per-row market signal available without a schema
change — `paper_trade_idempotency_key` has no `market` column by design;
see the ADR in the Stage 12 report).
"""
import json as _json
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


class _ResetFakeConn:
    """Minimal fake covering exactly the statements POST /reset issues,
    plus a pre-seeded set of idempotency rows to prove which ones survive."""

    def __init__(self, idem_rows):
        self.idem_rows = list(idem_rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        stripped = sql.strip()
        if stripped.startswith("DELETE FROM paper_trade_idempotency_key"):
            if len(params) == 2:
                # market=ALL branch — unconditional delete of every status
                # for this user (no market filter).
                user_id, op_type = params
                self.idem_rows = [r for r in self.idem_rows if r["user_id"] != user_id]
            else:
                # market-specific branch — COMPLETED rows for this market only.
                user_id, op_type, market = params
                self.idem_rows = [
                    r for r in self.idem_rows
                    if not (
                        r["user_id"] == user_id and r["status"] == "COMPLETED"
                        and _json.loads(r["response_body"]).get("market") == market
                    )
                ]
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    @contextmanager
    def transaction(self):
        yield self


def _reset(client, market, idem_rows):
    conn = _ResetFakeConn(idem_rows)

    def factory():
        return conn

    with patch.object(__import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory):
        resp = client.post(f"/api/paper-trading/reset?market={market}", headers=_auth())
    return resp, conn


def _completed_row(user_id, market, key="key-1"):
    return {
        "user_id": user_id, "status": "COMPLETED", "idempotency_key": key,
        "response_body": _json.dumps({"trade_id": 1, "market": market, "symbol": "AAPL"}),
    }


@pytest.mark.regression
class TestMarketScopedResetIdempotencyCleanup:
    def test_completed_row_for_reset_market_is_removed(self, client):
        rows = [_completed_row("user-aaa", "US")]
        resp, conn = _reset(client, "US", rows)
        assert resp.status_code == 200
        assert conn.idem_rows == []

    def test_completed_row_for_other_market_survives(self, client):
        rows = [_completed_row("user-aaa", "IN")]
        resp, conn = _reset(client, "US", rows)
        assert resp.status_code == 200
        assert len(conn.idem_rows) == 1

    def test_pending_row_is_never_touched_by_reset(self, client):
        rows = [{
            "user_id": "user-aaa", "status": "PENDING", "idempotency_key": "key-2",
            "response_body": None,
        }]
        resp, conn = _reset(client, "US", rows)
        assert resp.status_code == 200
        assert len(conn.idem_rows) == 1

    def test_full_reset_clears_every_status_regardless_of_market(self, client):
        rows = [_completed_row("user-aaa", "US"), _completed_row("user-aaa", "IN")]
        conn = _ResetFakeConn(rows)

        def factory():
            return conn

        with patch.object(__import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", factory):
            resp = client.post("/api/paper-trading/reset?market=ALL", headers=_auth())
        assert resp.status_code == 200
        assert conn.idem_rows == []  # both markets' rows cleared by the unconditional full-reset delete
