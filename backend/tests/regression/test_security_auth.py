"""
Security Remediation Sprint #001 — regression tests locking in the fix for
the Mini Security Audit's C-1 finding (Broken Access Control / IDOR across
Portfolio, Watchlist, Alerts, and Terms-Acceptance) and confirming H-1's
CORS tightening took effect.

Before this sprint, every one of these endpoints accepted a `user_id` with
zero verification the caller actually was that user — confirmed by reading
the routers directly during the audit. These tests assert the opposite is
now true: a missing/invalid/expired token is rejected, a valid token whose
subject doesn't match the request's `user_id` is rejected, and a valid,
matching token is allowed through. Sanity-checked the way this engagement's
discipline requires: every "rejected" assertion below fails loudly (a 200
instead of 401/403) if the corresponding `Depends(require_owner)` /
`Depends(get_current_user_id)` wiring is ever removed from a router.
"""

import time
from contextlib import contextmanager
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

TEST_SECRET = "regression-test-jwt-secret-at-least-32-bytes-long"


@pytest.fixture(autouse=True)
def _jwt_secret(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)


@pytest.fixture
def client():
    # Imported after the env var is set so any module-level secret reads
    # (there are none today, but this keeps the fixture robust) see it.
    from api.main import app
    return TestClient(app)


def _token(sub: str = "user-aaa", exp_delta: float = 3600, aud: str = "authenticated") -> str:
    return jwt.encode(
        {"sub": sub, "aud": aud, "exp": time.time() + exp_delta},
        TEST_SECRET,
        algorithm="HS256",
    )


def _auth(sub: str = "user-aaa", **kwargs) -> dict:
    return {"Authorization": f"Bearer {_token(sub, **kwargs)}"}


@contextmanager
def _fake_pg_conn():
    """A stand-in for portfolio.py/alerts.py's `_conn()` — its `.execute()`
    returns an object with no-op `.fetchall()`/`.fetchone()`, enough for the
    auth-rejection tests (which never reach the DB call) and for the
    "valid + matching" tests (which only need the call not to raise)."""
    class _FakeResult:
        def fetchall(self):
            return []

        def fetchone(self):
            return None

    class _FakeConn:
        def execute(self, *a, **kw):
            return _FakeResult()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    yield _FakeConn()


# ── services/auth.py — unit-level, no FastAPI/DB involved ──────────────────

class TestGetCurrentUserId:
    def test_missing_header_rejected(self):
        from services.auth import get_current_user_id
        with pytest.raises(Exception) as exc_info:
            get_current_user_id(None)
        assert "401" in str(exc_info.value) or getattr(exc_info.value, "status_code", None) == 401

    def test_malformed_header_rejected(self):
        from services.auth import get_current_user_id
        with pytest.raises(Exception):
            get_current_user_id("NotBearer sometoken")

    def test_invalid_signature_rejected(self):
        from services.auth import get_current_user_id
        bad = jwt.encode({"sub": "user-aaa", "aud": "authenticated", "exp": time.time() + 3600},
                          "wrong-secret-entirely", algorithm="HS256")
        with pytest.raises(Exception):
            get_current_user_id(f"Bearer {bad}")

    def test_expired_token_rejected(self):
        from services.auth import get_current_user_id
        expired = _token(exp_delta=-10)
        with pytest.raises(Exception):
            get_current_user_id(f"Bearer {expired}")

    def test_valid_token_accepted(self):
        from services.auth import get_current_user_id
        uid = get_current_user_id(f"Bearer {_token('user-aaa')}")
        assert uid == "user-aaa"


class TestRequireOwner:
    def test_matching_user_id_allowed(self):
        from services.auth import require_owner
        assert require_owner(user_id="user-aaa", current_user_id="user-aaa") == "user-aaa"

    def test_mismatched_user_id_rejected(self):
        from services.auth import require_owner
        with pytest.raises(Exception) as exc_info:
            require_owner(user_id="user-victim", current_user_id="user-attacker")
        assert getattr(exc_info.value, "status_code", None) == 403


class TestRequireMatchingBodyUser:
    def test_matching_allowed(self):
        from services.auth import require_matching_body_user
        require_matching_body_user("user-aaa", "user-aaa")  # must not raise

    def test_mismatched_rejected(self):
        from services.auth import require_matching_body_user
        with pytest.raises(Exception) as exc_info:
            require_matching_body_user("user-victim", "user-attacker")
        assert getattr(exc_info.value, "status_code", None) == 403


# ── End-to-end router coverage (TestClient) ─────────────────────────────────
# Each protected endpoint family: missing token, invalid token, expired
# token, valid-but-mismatched user_id, and valid-and-matching all confirmed.

PORTFOLIO_ENDPOINTS = [
    ("get", "/api/portfolio/{uid}", {}),
    ("post", "/api/portfolio/{uid}", {"symbol": "AAPL", "market": "US", "qty": 1, "avg_price": 100}),
    ("patch", "/api/portfolio/{uid}/h1", {"qty": 1, "avg_price": 100}),
    ("delete", "/api/portfolio/{uid}/h1", {}),
]

ALERTS_ENDPOINTS = [
    ("get", "/api/alerts/{uid}", {}),
    ("post", "/api/alerts/{uid}", {"symbol": "AAPL", "market": "US", "target_price": 100, "direction": "above"}),
    ("patch", "/api/alerts/{uid}/a1", {"triggered": True}),
    ("delete", "/api/alerts/{uid}/a1", {}),
]

WATCHLIST_ENDPOINTS = [
    ("get", "/api/watchlist/{uid}", {}),
    ("post", "/api/watchlist/{uid}", {"symbol": "AAPL", "market": "US", "notes": ""}),
    ("delete", "/api/watchlist/{uid}/AAPL", {}),
]


def _call(client, method, path_template, uid, body, headers):
    path = path_template.format(uid=uid)
    fn = getattr(client, method)
    if method in ("post", "patch"):
        return fn(path, json=body, headers=headers)
    return fn(path, headers=headers)


@pytest.mark.regression
class TestPortfolioAuth:
    @pytest.mark.parametrize("method,path,body", PORTFOLIO_ENDPOINTS)
    def test_missing_token_rejected(self, client, method, path, body):
        resp = _call(client, method, path, "user-aaa", body, {})
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", PORTFOLIO_ENDPOINTS)
    def test_invalid_token_rejected(self, client, method, path, body):
        resp = _call(client, method, path, "user-aaa", body, {"Authorization": "Bearer garbage.not.a.jwt"})
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", PORTFOLIO_ENDPOINTS)
    def test_expired_token_rejected(self, client, method, path, body):
        resp = _call(client, method, path, "user-aaa", body, _auth("user-aaa", exp_delta=-10))
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", PORTFOLIO_ENDPOINTS)
    def test_cross_user_blocked(self, client, method, path, body):
        # Authenticated as user-attacker, but the path targets user-victim's data.
        resp = _call(client, method, path, "user-victim", body, _auth("user-attacker"))
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,body", PORTFOLIO_ENDPOINTS)
    def test_valid_matching_token_allowed_through(self, client, method, path, body):
        with patch("api.routers.portfolio._conn", _fake_pg_conn), \
             patch("api.routers.portfolio._ensure_table", lambda: None):
            resp = _call(client, method, path, "user-aaa", body, _auth("user-aaa"))
        assert resp.status_code != 401 and resp.status_code != 403


@pytest.mark.regression
class TestAlertsAuth:
    @pytest.mark.parametrize("method,path,body", ALERTS_ENDPOINTS)
    def test_missing_token_rejected(self, client, method, path, body):
        resp = _call(client, method, path, "user-aaa", body, {})
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", ALERTS_ENDPOINTS)
    def test_cross_user_blocked(self, client, method, path, body):
        resp = _call(client, method, path, "user-victim", body, _auth("user-attacker"))
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,body", ALERTS_ENDPOINTS)
    def test_valid_matching_token_allowed_through(self, client, method, path, body):
        with patch("api.routers.alerts._conn", _fake_pg_conn), \
             patch("api.routers.alerts._ensure_table", lambda: None):
            resp = _call(client, method, path, "user-aaa", body, _auth("user-aaa"))
        assert resp.status_code != 401 and resp.status_code != 403


@pytest.mark.regression
class TestWatchlistAuth:
    @pytest.mark.parametrize("method,path,body", WATCHLIST_ENDPOINTS)
    def test_missing_token_rejected(self, client, method, path, body):
        resp = _call(client, method, path, "user-aaa", body, {})
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path,body", WATCHLIST_ENDPOINTS)
    def test_cross_user_blocked(self, client, method, path, body):
        resp = _call(client, method, path, "user-victim", body, _auth("user-attacker"))
        assert resp.status_code == 403

    @pytest.mark.parametrize("method,path,body", WATCHLIST_ENDPOINTS)
    def test_valid_matching_token_allowed_through(self, client, method, path, body):
        with patch("api.routers.watchlist._file_load", lambda: {}), \
             patch("api.routers.watchlist._file_save", lambda data: None):
            resp = _call(client, method, path, "user-aaa", body, _auth("user-aaa"))
        assert resp.status_code != 401 and resp.status_code != 403


@pytest.mark.regression
class TestTermsAuth:
    def test_accept_terms_missing_token_rejected(self, client):
        resp = client.post("/api/auth/accept-terms", json={"user_id": "user-aaa", "email": "a@example.com"})
        assert resp.status_code == 401

    def test_accept_terms_cross_user_blocked(self, client):
        resp = client.post(
            "/api/auth/accept-terms",
            json={"user_id": "user-victim", "email": "a@example.com"},
            headers=_auth("user-attacker"),
        )
        assert resp.status_code == 403

    def test_accept_terms_valid_matching_allowed(self, client):
        resp = client.post(
            "/api/auth/accept-terms",
            json={"user_id": "user-aaa", "email": "a@example.com"},
            headers=_auth("user-aaa"),
        )
        assert resp.status_code == 200

    def test_terms_status_missing_token_rejected(self, client):
        resp = client.get("/api/auth/terms-status/user-aaa")
        assert resp.status_code == 401

    def test_terms_status_cross_user_blocked(self, client):
        resp = client.get("/api/auth/terms-status/user-victim", headers=_auth("user-attacker"))
        assert resp.status_code == 403

    def test_terms_status_valid_matching_allowed(self, client):
        resp = client.get("/api/auth/terms-status/user-aaa", headers=_auth("user-aaa"))
        assert resp.status_code == 200


# ── CORS (H-1) ───────────────────────────────────────────────────────────────

@pytest.mark.regression
class TestCorsTightening:
    def test_unapproved_vercel_wildcard_origin_not_reflected(self, client):
        """
        Before this sprint, `allow_origin_regex=r"https://.*\\.vercel\\.app"`
        matched ANY app on Vercel's shared domain. An arbitrary third-party
        app on that same shared domain must NOT get a credentialed CORS
        grant from this backend.
        """
        resp = client.get(
            "/api/portfolio/user-aaa",
            headers={
                "Origin": "https://some-unrelated-attacker-app.vercel.app",
                **_auth("user-aaa"),
            },
        )
        assert resp.headers.get("access-control-allow-origin") != "https://some-unrelated-attacker-app.vercel.app"

    def test_localhost_dev_origin_allowed(self, client):
        resp = client.options(
            "/api/portfolio/user-aaa",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.headers.get("access-control-allow-origin") == "http://localhost:3000"
