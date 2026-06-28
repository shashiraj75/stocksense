"""
StockSense360 Backend Authentication & Authorization
(Security Remediation Sprint #001 — fixes the Mini Security Audit's
C-1 finding: every user-data API route accepted a `user_id` with zero
verification that the caller actually was that user).

This module is the ONLY place that verifies a Supabase-issued JWT or
checks that a request's `user_id` matches the authenticated caller.
Narrowly scoped: it does not touch the Prediction Engine, Business
Quality, Financial Strength, Daily Picks, the Data Fabric, or any
provider — those are unaffected by this sprint, per its explicit rule.

Verification strategy: Supabase issues JWTs signed with the project's
own JWT secret (HS256) — confirmed the standard, documented Supabase
pattern for a backend that wants to verify tokens itself without an
extra network round-trip to Supabase on every request. The secret is
read from SUPABASE_JWT_SECRET (Supabase dashboard > Project Settings >
API > JWT Settings) — never logged, never hardcoded, exactly like
every other credential in this codebase (SES-002 §1's "no bare
hardcoded thresholds" extended to secrets, and the existing
SCREENER_EMAIL/SEC_EDGAR_CONTACT pattern this module mirrors).

Fails CLOSED, not open: if SUPABASE_JWT_SECRET isn't configured, every
protected request is rejected (401) rather than silently accepted
unverified — the opposite of the bug this sprint fixes.
"""

import logging
import os

import jwt
from fastapi import Depends, Header, HTTPException

log = logging.getLogger(__name__)

# Supabase's own default audience claim for browser-issued session tokens.
_EXPECTED_AUDIENCE = "authenticated"


def _jwt_secret() -> str:
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        log.error(
            "[auth] SUPABASE_JWT_SECRET not set — every protected request will be "
            "rejected (fail-closed by design, never fail-open on a missing secret)."
        )
    return secret


def decode_supabase_jwt(token: str) -> dict:
    """
    Verifies a Supabase-issued access token's signature, expiration
    (`exp`, verified automatically by PyJWT — this is what makes an
    expired token rejected, not just a malformed one), and audience.
    Raises jwt.PyJWTError (or a subclass) on any failure — callers
    must not swallow this into a default-allow path.

    Never logs the token itself (per this sprint's explicit
    "do not log JWTs, service keys, or secrets" rule) — only a
    truncated, non-reversible indicator on failure, for operational
    debugging without disclosing the credential.
    """
    secret = _jwt_secret()
    if not secret:
        raise jwt.PyJWTError("server JWT secret not configured")
    return jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        audience=_EXPECTED_AUDIENCE,
    )


def get_current_user_id(authorization: str | None = Header(default=None)) -> str:
    """
    FastAPI dependency — extracts and verifies the caller's identity
    from the `Authorization: Bearer <token>` header. Use this on every
    route that reads or writes a specific user's data.

    Rejects (401), per Task 4's exact requirement:
      - a missing Authorization header
      - a header that isn't the `Bearer <token>` shape
      - a token with a missing/invalid signature
      - an expired token (PyJWT's own `exp` check)
      - a token missing the `sub` claim (no identity to authorize against)

    Returns the verified `sub` claim — the authenticated user's real
    Supabase user ID — never a value the caller can simply supply.
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    token = authorization[len("Bearer "):].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")

    try:
        claims = decode_supabase_jwt(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.PyJWTError as e:
        # Never include the token or the raw library exception text (which
        # can echo back parts of the input) in the response — a generic,
        # specific-enough-to-debug-server-side message only.
        log.warning("[auth] JWT verification failed: %s", type(e).__name__)
        raise HTTPException(status_code=401, detail="Invalid or malformed token")

    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject claim")

    return user_id


def require_owner(user_id: str, current_user_id: str = Depends(get_current_user_id)) -> str:
    """
    FastAPI dependency — for routes whose path already declares a
    `user_id` parameter (Portfolio, Watchlist, Alerts). FastAPI binds
    this dependency's own `user_id` parameter from that same path
    value automatically (standard FastAPI sub-dependency resolution —
    it matches by parameter name across the whole dependency tree for
    one request, not by call site), then compares it against the
    verified JWT subject from `get_current_user_id`.

    Rejects (403) — per Task 5's exact requirement — the moment the
    path's user_id doesn't match the authenticated caller. A user can
    never read, create, update, or delete another user's data through
    a route using this dependency, regardless of what `user_id` value
    is in the URL.
    """
    if current_user_id != user_id:
        log.warning("[auth] ownership check failed: path user_id != authenticated subject")
        raise HTTPException(status_code=403, detail="Cannot access another user's data")
    return current_user_id


def require_matching_body_user(body_user_id: str, current_user_id: str) -> None:
    """
    For the body-based equivalent of `require_owner` (Terms Acceptance,
    whose `user_id` lives inside a Pydantic request body, not the URL
    path — FastAPI's name-based auto-binding only matches top-level
    path/query parameters, not nested body fields, so this is called
    explicitly inside the route handler instead of as a `Depends`).
    Same rejection behavior as `require_owner`.
    """
    if body_user_id != current_user_id:
        log.warning("[auth] ownership check failed: body user_id != authenticated subject")
        raise HTTPException(status_code=403, detail="Cannot access another user's data")
