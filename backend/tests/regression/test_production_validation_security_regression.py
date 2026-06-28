"""
Production Validation Sprint — Security Regression reproduction.

Validation-only: this file adds evidence for the specific attack vectors
named in the Production Validation Sprint's checklist that weren't already
covered by test_security_auth.py / test_security_jwt_signing_keys.py
(forged JWT, modified payload, wrong audience, wrong issuer, wrong
signature, algorithm confusion, missing header, malformed bearer
formatting). No production code is modified by this file — it only
reproduces attacks against the fix already shipped in Sprint #001 and its
ES256/JWKS follow-up.

UPDATE (Security Closure Sprint, Task 1): at the time this file was first
written, `test_wrong_issuer_NOT_REJECTED_finding` documented a genuine gap —
`iss` was never validated. That gap is now closed in `services/auth.py`
(`decode_supabase_jwt` validates `issuer=` for both branches); the test
below has been updated to confirm the fix rather than document the gap —
renamed to `test_wrong_issuer_now_rejected`.
"""

import base64
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec

TEST_SECRET = "validation-sprint-jwt-secret-at-least-32-bytes-long"
KID = "validation-test-kid"
TEST_SUPABASE_URL = "https://test-project.supabase.co"
TEST_ISSUER = f"{TEST_SUPABASE_URL}/auth/v1"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_JWT_SECRET", TEST_SECRET)
    monkeypatch.setenv("SUPABASE_URL", TEST_SUPABASE_URL)


@pytest.fixture(scope="module")
def keypair():
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def _hs256_token(sub="user-aaa", aud="authenticated", exp_delta=3600, iss=TEST_ISSUER, **extra_claims) -> str:
    claims = {"sub": sub, "aud": aud, "iss": iss, "exp": time.time() + exp_delta, **extra_claims}
    return jwt.encode(claims, TEST_SECRET, algorithm="HS256")


def _tamper_payload(token: str, **overrides) -> str:
    """Decodes a token's payload, overrides claims, re-encodes WITHOUT
    re-signing — the exact "modified JWT payload" attack: changing a claim
    (e.g. sub) on an otherwise-valid, correctly-signed token."""
    header_b64, payload_b64, sig_b64 = token.split(".")
    pad = "=" * (-len(payload_b64) % 4)
    payload = json.loads(base64.urlsafe_b64decode(payload_b64 + pad))
    payload.update(overrides)
    new_payload_b64 = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    return f"{header_b64}.{new_payload_b64}.{sig_b64}"


@pytest.mark.regression
class TestProductionSecurityRegression:
    def test_forged_jwt_garbage_string_rejected(self):
        """Reproduction: an attacker submits an arbitrary string as a bearer
        token, not even JWT-shaped."""
        from services.auth import get_current_user_id
        with pytest.raises(Exception) as exc:
            get_current_user_id("Bearer this-is-not-a-jwt-at-all")
        assert exc.value.status_code == 401  # Evidence: rejected as expected.

    def test_modified_jwt_payload_rejected(self):
        """Reproduction: take a validly-signed token, change `sub` to a
        different user without re-signing (the actual attack a stolen-but-
        editable token would attempt)."""
        from services.auth import get_current_user_id
        valid = _hs256_token(sub="user-aaa")
        tampered = _tamper_payload(valid, sub="user-victim")
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {tampered}")
        assert exc.value.status_code == 401  # Evidence: signature mismatch caught, rejected.

    def test_wrong_signature_rejected(self):
        """Reproduction: token signed by a secret the server doesn't recognize."""
        from services.auth import get_current_user_id
        forged = jwt.encode(
            {"sub": "user-aaa", "aud": "authenticated", "exp": time.time() + 3600},
            "an-attacker-controlled-secret-not-the-real-one",
            algorithm="HS256",
        )
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {forged}")
        assert exc.value.status_code == 401  # Evidence: rejected.

    def test_expired_jwt_rejected(self):
        from services.auth import get_current_user_id
        expired = _hs256_token(exp_delta=-60)
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {expired}")
        assert exc.value.status_code == 401  # Evidence: rejected.

    def test_wrong_audience_rejected(self):
        """Reproduction: a correctly-signed token, but for a different
        audience than this backend expects (e.g. a token minted for a
        different Supabase consumer/service)."""
        from services.auth import get_current_user_id
        wrong_aud = _hs256_token(aud="some-other-service")
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {wrong_aud}")
        assert exc.value.status_code == 401  # Evidence: rejected (PyJWT's own aud check).

    def test_wrong_issuer_now_rejected(self):
        """
        Security Closure Sprint, Task 1: this used to be a documented
        FINDING (the Production Validation Sprint found `iss` was never
        checked). `decode_supabase_jwt` now passes `issuer=` to `jwt.decode`
        for both branches — this test confirms the fix: a correctly-signed,
        non-expired, correct-audience token whose `iss` claims to be a
        completely unrelated project is now REJECTED.
        """
        from services.auth import get_current_user_id
        wrong_issuer_token = _hs256_token(iss="https://a-completely-different-project.supabase.co/auth/v1")
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {wrong_issuer_token}")
        assert exc.value.status_code == 401  # Evidence: rejected.

    def test_missing_issuer_rejected(self):
        """A token with no `iss` claim at all — PyJWT's `issuer=` check
        requires the claim to be present, not just non-mismatched."""
        from services.auth import get_current_user_id
        no_issuer_token = jwt.encode(
            {"sub": "user-aaa", "aud": "authenticated", "exp": time.time() + 3600},
            TEST_SECRET, algorithm="HS256",
        )
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {no_issuer_token}")
        assert exc.value.status_code == 401  # Evidence: rejected.

    def test_malformed_issuer_rejected(self):
        """A non-string `iss` claim (e.g. an integer) — confirms the check
        rejects type-malformed issuers, not only string mismatches. Built
        by hand (raw HMAC, not jwt.encode) because PyJWT's own encoder
        refuses to build a token with a non-string `iss` at all — a real
        attacker isn't constrained by that safety rail, so this constructs
        the malformed token the way one actually could."""
        from services.auth import get_current_user_id
        import hashlib
        import hmac as hmac_lib

        header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps({"sub": "user-aaa", "aud": "authenticated", "iss": 12345, "exp": time.time() + 3600}).encode()
        ).rstrip(b"=").decode()
        signing_input = f"{header_b64}.{payload_b64}".encode()
        sig = hmac_lib.new(TEST_SECRET.encode(), signing_input, hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        malformed_token = f"{header_b64}.{payload_b64}.{sig_b64}"

        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {malformed_token}")
        assert exc.value.status_code == 401  # Evidence: rejected.

    def test_correct_issuer_hs256_accepted(self):
        """Confirms the fix doesn't break the legitimate HS256 case."""
        from services.auth import get_current_user_id
        token = _hs256_token()  # default iss=TEST_ISSUER, matching SUPABASE_URL
        assert get_current_user_id(f"Bearer {token}") == "user-aaa"

    def test_correct_issuer_es256_accepted(self, keypair):
        """Confirms the fix doesn't break the legitimate ES256/JWKS case."""
        from services.auth import get_current_user_id
        import services.auth as auth
        from unittest.mock import patch as _patch

        private_key, public_key = keypair
        token = jwt.encode(
            {"sub": "user-aaa", "aud": "authenticated", "iss": TEST_ISSUER, "exp": time.time() + 3600},
            private_key, algorithm="ES256", headers={"kid": KID},
        )

        class _FakeKey:
            key = public_key

        class _FakeClient:
            def get_signing_key_from_jwt(self, _t):
                return _FakeKey()

        with _patch.object(auth, "_jwks_client", return_value=_FakeClient()):
            assert get_current_user_id(f"Bearer {token}") == "user-aaa"

    def test_issuer_unconfigured_fails_closed(self, monkeypatch):
        """SUPABASE_URL unset entirely — no expected issuer can be derived,
        so the request must be rejected, not silently allowed through with
        the issuer check skipped."""
        from services.auth import get_current_user_id
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        token = _hs256_token()
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {token}")
        assert exc.value.status_code == 401  # Evidence: fails closed.

    def test_algorithm_confusion_hs256_with_public_key_as_secret_rejected(self, keypair):
        """
        Reproduction of the classic JWKS algorithm-confusion attack: take
        the server's own (public, by definition) ES256 public key bytes and
        use them as an HMAC secret to forge an HS256-signed token, hoping a
        naive verifier reuses the same key material across both algorithm
        branches. Confirms it doesn't: the HS256 branch in
        `decode_supabase_jwt` only ever uses `SUPABASE_JWT_SECRET`, never
        the JWKS public key, so this forged token is rejected for a
        completely different reason (wrong secret), not coincidentally.

        Built by hand (raw HMAC, not jwt.encode) because PyJWT's own
        `encode()` refuses to sign with a PEM-shaped key as a safety rail —
        a real attacker isn't constrained by that library's protections, so
        this constructs the forged token the way an attacker actually
        would, to test our *verification* path honestly rather than testing
        PyJWT's encoder.
        """
        from services.auth import get_current_user_id
        from cryptography.hazmat.primitives import serialization
        import hashlib
        import hmac as hmac_lib

        _, public_key = keypair
        public_pem = public_key.public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        payload_b64 = base64.urlsafe_b64encode(
            json.dumps({"sub": "user-attacker", "aud": "authenticated", "exp": time.time() + 3600}).encode()
        ).rstrip(b"=").decode()
        signing_input = f"{header_b64}.{payload_b64}".encode()
        sig = hmac_lib.new(public_pem, signing_input, hashlib.sha256).digest()
        sig_b64 = base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        forged = f"{header_b64}.{payload_b64}.{sig_b64}"

        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {forged}")
        assert exc.value.status_code == 401  # Evidence: rejected.

    def test_algorithm_none_rejected(self):
        """Reproduction: the classic `alg: none` unsigned-token attack."""
        from services.auth import get_current_user_id
        header = base64.urlsafe_b64encode(json.dumps({"alg": "none", "typ": "JWT"}).encode()).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "user-attacker", "aud": "authenticated", "exp": time.time() + 3600}).encode()
        ).rstrip(b"=").decode()
        none_token = f"{header}.{payload}."
        with pytest.raises(Exception) as exc:
            get_current_user_id(f"Bearer {none_token}")
        assert exc.value.status_code == 401  # Evidence: rejected (falls to InvalidAlgorithmError).

    def test_missing_authorization_header_rejected(self):
        from services.auth import get_current_user_id
        with pytest.raises(Exception) as exc:
            get_current_user_id(None)
        assert exc.value.status_code == 401

    @pytest.mark.parametrize("malformed_header", [
        "NotBearer sometoken",
        "Bearer",        # no token at all
        "Bearer ",       # whitespace only
        "bearer abc123", # wrong case — RFC 6750 requires exact "Bearer"
        "Token abc123",
    ])
    def test_malformed_bearer_formatting_rejected(self, malformed_header):
        from services.auth import get_current_user_id
        with pytest.raises(Exception) as exc:
            get_current_user_id(malformed_header)
        assert exc.value.status_code == 401
