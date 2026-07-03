"""
Security Release 15F-A1 — regression tests locking in the fix for the
Landing-Page/Authentication audit's confirmed finding: Paper Trading
accepted a client-supplied `user_id` (and, for buy/portfolio, `email`)
with zero JWT verification. These tests assert every route now derives
identity exclusively from `Depends(get_current_user_id)`, that a
missing/invalid token is rejected before any DB call, and that a
forged legacy `user_id` cannot target another user's records.
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


def _token(sub: str = "user-aaa", exp_delta: float = 3600, aud: str = "authenticated", iss: str = TEST_ISSUER) -> str:
    return jwt.encode(
        {"sub": sub, "aud": aud, "iss": iss, "exp": time.time() + exp_delta},
        TEST_SECRET,
        algorithm="HS256",
    )


def _auth(sub: str = "user-aaa", **kwargs) -> dict:
    return {"Authorization": f"Bearer {_token(sub, **kwargs)}"}


class _RecordingConn:
    """Records every SQL statement + params executed against it, and lets a
    test script canned fetchone/fetchall results per call, without any real
    DB connection."""

    def __init__(self, fetchone_results=None, fetchall_results=None):
        self.calls = []
        self._fetchone_results = list(fetchone_results or [])
        self._fetchall_results = list(fetchall_results or [])

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return self

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None

    def fetchall(self):
        return self._fetchall_results.pop(0) if self._fetchall_results else []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@contextmanager
def _fake_conn(fetchone_results=None, fetchall_results=None):
    conn = _RecordingConn(fetchone_results=fetchone_results, fetchall_results=fetchall_results)
    yield conn


PAPER_TRADING_ROUTES = [
    ("get", "/api/paper-trading/portfolio", None),
    ("post", "/api/paper-trading/buy", {"symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0}),
    ("post", "/api/paper-trading/sell/1", {"price": 100.0}),
    ("patch", "/api/paper-trading/trade/1", {"stop_loss": 90.0}),
    ("post", "/api/paper-trading/reset", None),
]


def _call(client, method, path, body, headers):
    fn = getattr(client, method)
    if method in ("post", "patch") and body is not None:
        return fn(path, json=body, headers=headers)
    return fn(path, headers=headers)


@pytest.mark.regression
class TestPaperTradingAuth:
    @pytest.mark.parametrize("method,path,body", PAPER_TRADING_ROUTES)
    def test_missing_token_rejected_before_any_db_call(self, client, method, path, body):
        with patch("api.routers.paper_trading._conn") as mock_conn:
            resp = _call(client, method, path, body, {})
            assert resp.status_code == 401
            mock_conn.assert_not_called()

    @pytest.mark.parametrize("method,path,body", PAPER_TRADING_ROUTES)
    def test_invalid_token_rejected_before_any_db_call(self, client, method, path, body):
        with patch("api.routers.paper_trading._conn") as mock_conn:
            resp = _call(client, method, path, body, {"Authorization": "Bearer garbage.not.a.jwt"})
            assert resp.status_code == 401
            mock_conn.assert_not_called()

    @pytest.mark.parametrize("method,path,body", PAPER_TRADING_ROUTES)
    def test_expired_token_rejected_before_any_db_call(self, client, method, path, body):
        with patch("api.routers.paper_trading._conn") as mock_conn:
            resp = _call(client, method, path, body, _auth("user-aaa", exp_delta=-10))
            assert resp.status_code == 401
            mock_conn.assert_not_called()

    def test_forged_legacy_user_id_in_body_does_not_select_another_user(self, client):
        """Buy no longer even accepts a `user_id` field, but confirm a forged
        one silently sent by an old client is not used anywhere: assert the
        SQL bound to _conn only ever carries the token's own subject."""
        # First _conn() call is _ensure_portfolio's own `with` block (one
        # SELECT fetchone); second is paper_buy's own `with` block (debit
        # fetchone, then insert-trade fetchone).
        made_conns = []
        pending = [
            _RecordingConn(fetchone_results=[(1000000.0, 100000.0)]),
            _RecordingConn(fetchone_results=[(999900.0,), (1,)]),
        ]

        def _conn_factory():
            conn = pending.pop(0)
            made_conns.append(conn)
            return conn

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory
        ), patch("api.routers.paper_trading._is_market_open", return_value=True):
            body = {
                "symbol": "AAPL", "market": "US", "quantity": 1, "price": 100.0,
                "user_id": "user-victim", "email": "victim@example.com",
            }
            resp = client.post("/api/paper-trading/buy", json=body, headers=_auth("user-attacker"))
        # Pydantic ignores the unknown extra fields; request proceeds authorized as user-attacker.
        assert resp.status_code == 200
        found_attacker = False
        for conn in made_conns:
            for _, params in conn.calls:
                assert "user-victim" not in (params or ())
                if params and "user-attacker" in params:
                    found_attacker = True
        assert found_attacker

    def test_sell_cross_user_blocked(self, client):
        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]),
            "_conn",
            lambda: _fake_conn(fetchone_results=[("user-victim", "AAPL", 1, 100.0, "OPEN", "US")]),
        ):
            resp = client.post(
                "/api/paper-trading/sell/1", json={"price": 110.0}, headers=_auth("user-attacker")
            )
        assert resp.status_code == 403

    def test_edit_cross_user_blocked(self, client):
        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]),
            "_conn",
            lambda: _fake_conn(fetchone_results=[("user-victim", "OPEN", 100.0, 1, "US")]),
        ):
            resp = client.patch(
                "/api/paper-trading/trade/1", json={"stop_loss": 90.0}, headers=_auth("user-attacker")
            )
        assert resp.status_code == 403

    def test_reset_scopes_only_to_authenticated_user(self, client):
        recorded = {}

        def _conn_factory():
            conn = _RecordingConn()
            recorded["conn"] = conn
            return conn

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory
        ):
            resp = client.post("/api/paper-trading/reset", headers=_auth("user-aaa"))
        assert resp.status_code == 200
        for sql, params in recorded["conn"].calls:
            assert "user-victim" not in (params or ())
            if params:
                assert "user-aaa" in params

    def test_portfolio_valid_token_allowed_through(self, client):
        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]),
            "_conn",
            lambda: _fake_conn(fetchone_results=[(1000000.0, 100000.0)], fetchall_results=[[]]),
        ):
            resp = client.get("/api/paper-trading/portfolio", headers=_auth("user-aaa"))
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "user-aaa"

    def test_portfolio_legacy_query_user_id_and_email_ignored(self, client):
        made_conns = []

        def _conn_factory():
            conn = _RecordingConn(fetchone_results=[(1000000.0, 100000.0)], fetchall_results=[[]])
            made_conns.append(conn)
            return conn

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory
        ):
            resp = client.get(
                "/api/paper-trading/portfolio",
                params={"user_id": "user-victim", "email": "victim@example.com"},
                headers=_auth("user-attacker"),
            )
        assert resp.status_code == 200
        assert resp.json()["user_id"] == "user-attacker"
        found_attacker = False
        for conn in made_conns:
            for _, params in conn.calls:
                assert "user-victim" not in (params or ())
                assert "victim@example.com" not in (params or ())
                if params and "user-attacker" in params:
                    found_attacker = True
        assert found_attacker

    def test_reset_legacy_query_user_id_ignored(self, client):
        recorded = {}

        def _conn_factory():
            conn = _RecordingConn()
            recorded["conn"] = conn
            return conn

        with patch.object(
            __import__("api.routers.paper_trading", fromlist=["_conn"]), "_conn", _conn_factory
        ):
            resp = client.post(
                "/api/paper-trading/reset",
                params={"user_id": "user-victim"},
                headers=_auth("user-attacker"),
            )
        assert resp.status_code == 200
        found_attacker = False
        for sql, params in recorded["conn"].calls:
            assert "user-victim" not in (params or ())
            if params and "user-attacker" in params:
                found_attacker = True
        assert found_attacker
