"""
Security Release 15F-A1 — regression tests locking in the fix for the
Landing-Page/Authentication audit's confirmed finding: Feedback/NPS
routes accepted a client-supplied `user_id` with zero JWT verification.
Signal/NPS submission and private reads now derive identity exclusively
from `Depends(get_current_user_id)`; the public aggregate summary route
remains intentionally unauthenticated.
"""

import time
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


PRIVATE_FEEDBACK_ROUTES = [
    ("post", "/api/feedback/signal", {"symbol": "AAPL", "market": "US", "horizon": "medium", "signal": "BUY", "vote": 1}),
    ("get", "/api/feedback/signal/AAPL", None),
    ("post", "/api/feedback/nps", {"score": 8}),
    ("get", "/api/feedback/nps/due", None),
]


def _call(client, method, path, body, headers):
    fn = getattr(client, method)
    if method == "post" and body is not None:
        return fn(path, json=body, headers=headers)
    return fn(path, headers=headers)


@pytest.mark.regression
class TestFeedbackAuth:
    @pytest.mark.parametrize("method,path,body", PRIVATE_FEEDBACK_ROUTES)
    def test_missing_token_rejected_before_any_db_call(self, client, method, path, body):
        with patch("api.routers.feedback._conn") as mock_conn:
            resp = _call(client, method, path, body, {})
            assert resp.status_code == 401
            mock_conn.assert_not_called()

    @pytest.mark.parametrize("method,path,body", PRIVATE_FEEDBACK_ROUTES)
    def test_invalid_token_rejected_before_any_db_call(self, client, method, path, body):
        with patch("api.routers.feedback._conn") as mock_conn:
            resp = _call(client, method, path, body, {"Authorization": "Bearer garbage.not.a.jwt"})
            assert resp.status_code == 401
            mock_conn.assert_not_called()

    @pytest.mark.parametrize("method,path,body", PRIVATE_FEEDBACK_ROUTES)
    def test_expired_token_rejected_before_any_db_call(self, client, method, path, body):
        with patch("api.routers.feedback._conn") as mock_conn:
            resp = _call(client, method, path, body, _auth("user-aaa", exp_delta=-10))
            assert resp.status_code == 401
            mock_conn.assert_not_called()

    def test_signal_feedback_write_uses_only_verified_subject(self, client):
        made = []

        def _conn_factory():
            conn = _RecordingConn()
            made.append(conn)
            return conn

        with patch("api.routers.feedback._conn", _conn_factory):
            body = {
                "symbol": "AAPL", "market": "US", "horizon": "medium", "signal": "BUY", "vote": 1,
                "user_id": "user-victim",  # legacy/forged field — model no longer declares it
            }
            resp = client.post("/api/feedback/signal", json=body, headers=_auth("user-attacker"))
        assert resp.status_code == 200
        sql, params = made[0].calls[0]
        assert "user-attacker" in params
        assert "user-victim" not in params

    def test_signal_feedback_read_scoped_to_verified_subject(self, client):
        made = []

        def _conn_factory():
            conn = _RecordingConn(fetchone_results=[None])
            made.append(conn)
            return conn

        with patch("api.routers.feedback._conn", _conn_factory):
            resp = client.get(
                "/api/feedback/signal/AAPL",
                params={"user_id": "user-victim"},
                headers=_auth("user-attacker"),
            )
        assert resp.status_code == 200
        sql, params = made[0].calls[0]
        assert "user-attacker" in params
        assert "user-victim" not in params

    def test_nps_submit_uses_only_verified_subject(self, client):
        made = []

        def _conn_factory():
            conn = _RecordingConn()
            made.append(conn)
            return conn

        with patch("api.routers.feedback._conn", _conn_factory):
            resp = client.post(
                "/api/feedback/nps",
                json={"score": 9, "user_id": "user-victim"},
                headers=_auth("user-attacker"),
            )
        assert resp.status_code == 200
        sql, params = made[0].calls[0]
        assert "user-attacker" in params
        assert "user-victim" not in params

    def test_nps_due_scoped_to_verified_subject(self, client):
        made = []

        def _conn_factory():
            conn = _RecordingConn(fetchone_results=[None])
            made.append(conn)
            return conn

        with patch("api.routers.feedback._conn", _conn_factory):
            resp = client.get(
                "/api/feedback/nps/due",
                params={"user_id": "user-victim"},
                headers=_auth("user-attacker"),
            )
        assert resp.status_code == 200
        for sql, params in made[0].calls:
            assert "user-attacker" in params
            assert "user-victim" not in params

    def test_public_signal_summary_remains_public(self, client):
        with patch("api.routers.feedback._conn", lambda: _RecordingConn(fetchone_results=[(3, 1)])):
            resp = client.get("/api/feedback/signal/summary/AAPL")
        assert resp.status_code == 200
        assert resp.json()["thumbs_up"] == 3
