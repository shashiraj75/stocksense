"""
Security Remediation Follow-up — Supabase JWT Signing Keys compatibility.

Root cause this locks in: `services/auth.py` originally only attempted
HS256 verification against `SUPABASE_JWT_SECRET`. Supabase's current JWT
Signing Keys feature issues access tokens signed ES256, with a `kid` in the
header — production confirmed (via DevTools) the Authorization header was
present and correctly formed, but every request still got 401, because the
backend never even tried the JWKS path for a non-HS256 token; it had none.

These tests use a locally-generated EC keypair (no real network call, no
live Supabase project needed) standing in for a Supabase JWT Signing Key,
and mock `services.auth._jwks_client` to return it — confirming the new
ES256/JWKS branch in `decode_supabase_jwt` actually verifies the signature
against the right key (and rejects it against the wrong one), independent
of the pre-existing HS256 legacy-path tests in `test_security_auth.py`,
which remain unmodified and still pass.
"""

import time
from unittest.mock import patch

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

KID = "test-signing-key-1"
TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"


@pytest.fixture(scope="module")
def keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


@pytest.fixture(autouse=True)
def _no_hs256_secret(monkeypatch):
    # Confirms the ES256 path doesn't depend on the HS256 legacy secret
    # being configured at all — they're independent verification paths.
    monkeypatch.delenv("SUPABASE_JWT_SECRET", raising=False)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)


@pytest.fixture
def client():
    from api.main import app
    return TestClient(app)


def _es256_token(private_key, sub: str = "user-aaa", exp_delta: float = 3600,
                  aud: str = "authenticated", kid: str = KID, iss: str = TEST_ISSUER) -> str:
    # Security Closure Sprint, Task 1: decode_supabase_jwt now validates
    # `iss` too — every token here includes one matching SUPABASE_URL,
    # mirroring a real Supabase-issued ES256 token.
    return jwt.encode(
        {"sub": sub, "aud": aud, "iss": iss, "exp": time.time() + exp_delta},
        private_key,
        algorithm="ES256",
        headers={"kid": kid},
    )


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    """Stands in for jwt.PyJWKClient — returns the test public key for any kid,
    mirroring what a real Supabase JWKS fetch would resolve to for a token
    actually signed by that key."""

    def __init__(self, public_key):
        self._public_key = public_key

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._public_key)


def _patched_jwks(public_key):
    import services.auth as auth
    return patch.object(auth, "_jwks_client", return_value=_FakeJWKClient(public_key))


@pytest.mark.regression
class TestES256SigningKeyVerification:
    def test_valid_es256_token_accepted(self, keypair):
        from services.auth import get_current_user_id
        private_key, public_key = keypair
        token = _es256_token(private_key)
        with _patched_jwks(public_key):
            assert get_current_user_id(f"Bearer {token}") == "user-aaa"

    def test_missing_token_rejected(self):
        from services.auth import get_current_user_id
        with pytest.raises(Exception) as exc_info:
            get_current_user_id(None)
        assert getattr(exc_info.value, "status_code", None) == 401

    def test_invalid_token_rejected(self, keypair):
        """An ES256 token signed by a DIFFERENT key than the one the JWKS
        endpoint serves for its kid — the actual attack this verification
        exists to prevent (a forged or unrelated key claiming a real kid)."""
        from services.auth import get_current_user_id
        _, real_public_key = keypair
        attacker_private_key = ec.generate_private_key(ec.SECP256R1())
        forged_token = _es256_token(attacker_private_key)
        with _patched_jwks(real_public_key):
            with pytest.raises(Exception) as exc_info:
                get_current_user_id(f"Bearer {forged_token}")
            assert getattr(exc_info.value, "status_code", None) == 401

    def test_malformed_token_rejected(self):
        from services.auth import get_current_user_id
        with pytest.raises(Exception) as exc_info:
            get_current_user_id("Bearer not-a-real-jwt-at-all")
        assert getattr(exc_info.value, "status_code", None) == 401

    def test_expired_es256_token_rejected(self, keypair):
        from services.auth import get_current_user_id
        private_key, public_key = keypair
        expired_token = _es256_token(private_key, exp_delta=-10)
        with _patched_jwks(public_key):
            with pytest.raises(Exception) as exc_info:
                get_current_user_id(f"Bearer {expired_token}")
            assert getattr(exc_info.value, "status_code", None) == 401

    def test_jwks_unconfigured_rejected(self, keypair, monkeypatch):
        """SUPABASE_URL unset entirely — must fail closed, not fall through
        to treating the token as somehow valid."""
        from services.auth import get_current_user_id
        import services.auth as auth
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        auth._jwks_client.cache_clear()
        private_key, _ = keypair
        token = _es256_token(private_key)
        with pytest.raises(Exception) as exc_info:
            get_current_user_id(f"Bearer {token}")
        assert getattr(exc_info.value, "status_code", None) == 401
        auth._jwks_client.cache_clear()

    def test_mismatched_user_id_rejected_with_403(self, keypair):
        from services.auth import require_owner
        with pytest.raises(Exception) as exc_info:
            require_owner(user_id="user-victim", current_user_id="user-attacker")
        assert getattr(exc_info.value, "status_code", None) == 403


@pytest.mark.regression
class TestES256EndToEndThroughRouter:
    """Confirms the ES256/JWKS path works through the full FastAPI dependency
    chain, not just at the services.auth unit level — using Portfolio as the
    representative router (same require_owner wiring as Watchlist/Alerts/Terms)."""

    def test_valid_es256_token_reaches_route_get_holdings(self, client, keypair):
        private_key, public_key = keypair
        token = _es256_token(private_key, sub="user-aaa")
        with _patched_jwks(public_key), \
             patch("api.routers.portfolio._conn"), \
             patch("api.routers.portfolio._ensure_table", lambda: None):
            resp = client.get(
                "/api/portfolio/user-aaa",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code != 401 and resp.status_code != 403

    def test_cross_user_es256_token_blocked(self, client, keypair):
        private_key, public_key = keypair
        token = _es256_token(private_key, sub="user-attacker")
        with _patched_jwks(public_key):
            resp = client.get(
                "/api/portfolio/user-victim",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert resp.status_code == 403

    def test_missing_token_still_rejected_on_router(self, client):
        resp = client.get("/api/portfolio/user-aaa")
        assert resp.status_code == 401
