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

Verification strategy (Security Remediation Follow-up — Supabase JWT
Signing Keys compatibility): Supabase now issues access tokens signed
with project-specific **JWT Signing Keys** — asymmetric (ES256, `kid`
in the header), verifiable via the project's public JWKS endpoint
(`<SUPABASE_URL>/auth/v1/.well-known/jwks.json`) — rather than only the
legacy shared HS256 secret. A production rollout surfaced this: real
Supabase tokens carried `alg=ES256` with a `kid`, but this module only
ever attempted HS256 verification against `SUPABASE_JWT_SECRET`, so
every legitimate request was rejected (401) despite a correct,
unmodified `Authorization` header.

Both paths are supported, dispatched by the token's own (unverified)
`alg` header — used only to select which verification material to
fetch, never to weaken the check itself: each branch still locks
`jwt.decode`'s own `algorithms=[...]` to exactly one specific algorithm
and uses the key type that actually matches it (symmetric secret for
HS256, the matching public key from JWKS for ES256/RS256) — an HS256
token can never be "verified" against asymmetric key material or vice
versa, which is what actually prevents algorithm-confusion attacks.

- **ES256/RS256 (current Supabase default):** the public key for the
  token's `kid` is fetched from `SUPABASE_URL`'s JWKS endpoint via
  `jwt.PyJWKClient`, which caches keys in-process — no network round
  trip on every request once warm.
- **HS256 (legacy, optional):** preserved only if `SUPABASE_JWT_SECRET`
  is explicitly configured — for projects that haven't rotated to JWT
  Signing Keys yet. Never assumed; absent the secret, HS256 tokens are
  rejected rather than silently allowed through some other path.

Fails CLOSED, not open: if neither `SUPABASE_URL` (for ES256/RS256) nor
`SUPABASE_JWT_SECRET` (for HS256) is configured for the algorithm a
given token actually uses, that request is rejected (401) rather than
silently accepted unverified — the opposite of the bug this sprint and
this follow-up both fix.
"""

import functools
import logging
import os

import jwt
from fastapi import Depends, Header, HTTPException

log = logging.getLogger(__name__)

# Supabase's own default audience claim for browser-issued session tokens.
_EXPECTED_AUDIENCE = "authenticated"

# Asymmetric algorithms Supabase's JWT Signing Keys feature issues today
# (ES256) plus RS256 for forward compatibility with other key types
# Supabase's JWKS endpoint can serve — never expanded to include "none" or
# any symmetric algorithm here, which is what keeps the JWKS branch safe
# from algorithm-confusion attacks.
_JWKS_ALGORITHMS = {"ES256", "RS256"}


def _jwt_secret() -> str:
    secret = os.getenv("SUPABASE_JWT_SECRET", "")
    if not secret:
        log.warning(
            "[auth] SUPABASE_JWT_SECRET not set — HS256 tokens will be rejected "
            "(fail-closed by design). This is expected if the Supabase project "
            "has migrated to JWT Signing Keys (ES256) and only ES256 tokens are issued."
        )
    return secret


@functools.lru_cache(maxsize=1)
def _jwks_client() -> "jwt.PyJWKClient | None":
    """
    Lazily constructed, cached for the process lifetime — `PyJWKClient`
    itself caches fetched keys in-process, so this avoids re-fetching the
    JWKS document on every request once warm. `lru_cache` here only avoids
    reconstructing the client object itself; SUPABASE_URL is treated as
    fixed for the process's lifetime, exactly like every other env-derived
    config in this codebase.
    """
    supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
    if not supabase_url:
        log.warning(
            "[auth] SUPABASE_URL not set — ES256/RS256 (JWT Signing Keys) tokens "
            "will be rejected (fail-closed by design)."
        )
        return None
    jwks_url = f"{supabase_url}/auth/v1/.well-known/jwks.json"
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


def decode_supabase_jwt(token: str) -> dict:
    """
    Verifies a Supabase-issued access token's signature, expiration
    (`exp`, verified automatically by PyJWT — this is what makes an
    expired token rejected, not just a malformed one), and audience.
    Raises jwt.PyJWTError (or a subclass — including jwt.PyJWKClientError,
    confirmed a PyJWTError subclass, so callers' existing `except
    jwt.PyJWTError` handling already covers JWKS lookup failures too) on
    any failure — callers must not swallow this into a default-allow path.

    Dispatches on the token's own (unverified) `alg` header to choose
    HS256 (legacy secret) vs. ES256/RS256 (JWKS) — see module docstring
    for why this dispatch doesn't weaken the actual verification.

    Never logs the token itself (per this sprint's explicit
    "do not log JWTs, service keys, or secrets" rule) — only a
    truncated, non-reversible indicator on failure, for operational
    debugging without disclosing the credential.
    """
    header = jwt.get_unverified_header(token)  # raises jwt.DecodeError on malformed tokens
    alg = header.get("alg")

    if alg == "HS256":
        secret = _jwt_secret()
        if not secret:
            raise jwt.PyJWTError("server JWT secret not configured")
        return jwt.decode(token, secret, algorithms=["HS256"], audience=_EXPECTED_AUDIENCE)

    if alg in _JWKS_ALGORITHMS:
        client = _jwks_client()
        if client is None:
            raise jwt.PyJWTError("server JWKS endpoint not configured")
        signing_key = client.get_signing_key_from_jwt(token)
        return jwt.decode(token, signing_key.key, algorithms=[alg], audience=_EXPECTED_AUDIENCE)

    raise jwt.InvalidAlgorithmError(f"unsupported token algorithm: {alg}")


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
