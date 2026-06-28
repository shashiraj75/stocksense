# Security Remediation Sprint #001 — Fix Critical Access Control and CORS Risks

**Status:** Complete. Fixes the Mini Security Audit's [C-1 and H-1 findings](Security-Audit-Pre-Epic-003.md). **No business logic, intelligence engines, providers, or Daily Picks/recommendation logic were touched** — confirmed by the diff being scoped entirely to four routers, one new auth module, one new rate-limit module, CORS configuration, and the frontend's shared API client.

---

## 1. Security Fix Summary

Every user-data endpoint now requires a verified Supabase JWT, and rejects any request where the token's subject doesn't match the `user_id` the request targets. The CORS policy no longer trusts Vercel's shared wildcard domain. Basic per-IP rate limiting is now in place on the same endpoints.

## 2. Affected Endpoint List

| Router | Endpoints | Fix applied |
|---|---|---|
| `api/routers/portfolio.py` | `GET/POST /{user_id}`, `POST /{user_id}/import`, `PATCH/DELETE /{user_id}/{holding_id}` | `Depends(require_owner)` |
| `api/routers/watchlist.py` | `GET/POST /{user_id}`, `DELETE /{user_id}/{symbol}` | `Depends(require_owner)` |
| `api/routers/alerts.py` | `GET/POST /{user_id}`, `PATCH/DELETE /{user_id}/{alert_id}` | `Depends(require_owner)` |
| `api/routers/auth.py` | `POST /api/auth/accept-terms` (body `user_id`) | `Depends(get_current_user_id)` + `require_matching_body_user` |
| `api/routers/auth.py` | `GET /api/auth/terms-status/{user_id}` (path `user_id`) | `Depends(require_owner)` |

**Deliberately out of scope:** `api/routers/paper_trading.py`. The audit's L-1 finding noted this surface shares the same unauthenticated-`user_id` pattern but is architecturally different — its `user_id` is a locally-generated, pseudonymous `paper_session_id`, not a real Supabase identity (no PII or real financial data exposed). The remediation brief's own scope list named Portfolio, Watchlist, Alerts, and Terms specifically; paper trading was a named Low-severity, lower-priority item in the audit, addressed separately per "fix security issues narrowly."

## 3. Auth Implementation Summary

New module: [`backend/services/auth.py`](../../../backend/services/auth.py).

- `get_current_user_id()` — a FastAPI dependency that reads `Authorization: Bearer <token>`, verifies it against `SUPABASE_JWT_SECRET` (HS256 — Supabase's standard self-verifiable JWT signing secret, from Project Settings > API > JWT Settings), checks the `authenticated` audience claim, and returns the token's `sub` (the real Supabase user ID). Rejects (401): missing header, malformed header, invalid signature, expired token (`exp`, verified by PyJWT itself), missing `sub` claim.
- **Fails closed:** if `SUPABASE_JWT_SECRET` isn't configured, every protected request is rejected rather than silently accepted — the opposite failure mode from the bug this sprint fixes.
- Never logs the token, the secret, or raw library exception text — only a generic message client-side and the exception's type name server-side, per the sprint's explicit "do not log JWTs, service keys, or secrets" rule.

## 4. Authorization Implementation Summary

- `require_owner(user_id, current_user_id=Depends(get_current_user_id))` — for the five routes whose `user_id` is already a URL path parameter (Portfolio, Watchlist, Alerts, Terms-Status). FastAPI's own sub-dependency name-matching binds this dependency's `user_id` from the same path value the route itself sees; the dependency then compares it against the verified JWT subject and rejects (403) on any mismatch.
- `require_matching_body_user(body_user_id, current_user_id)` — for Terms-Acceptance, whose `user_id` is a field inside a Pydantic request body, not a path parameter (FastAPI's name-based auto-binding only reaches path/query parameters, not nested body fields). Called explicitly inside the route handler; same 403 behavior.
- **Backend service-role access does not bypass this.** This authorization check happens entirely at the API layer, before any database call — it has nothing to do with the database role's privileges (the audit separately confirmed the `postgres` role's `BYPASSRLS` means Supabase RLS was never the protection layer here; this sprint's fix is the actual protection layer, at the only boundary that matters for this backend's own connections).

## 5. CORS Changes

`api/main.py`: removed `allow_origin_regex=r"https://.*\.vercel\.app"` (H-1 — matched any app on Vercel's shared domain, not just this project's). Replaced with:
- `FRONTEND_URL` (required in production) — the actual deployed frontend origin.
- `STAGING_FRONTEND_URL` (optional) — a staging frontend, if one exists, documented separately from production.
- `VERCEL_PREVIEW_ORIGIN_REGEX` (optional, unset by default) — for teams that want preview-deployment access, scoped to this project's own preview-URL naming pattern, never a bare `.*\.vercel\.app` wildcard. The code comment at the definition site names H-1 explicitly so a future change doesn't reintroduce it.
- `http://localhost:3000` / `https://localhost:3000` remain allowed unconditionally — the local-development exception, documented inline and here, separately from the production/staging origins.

## 6. Rate Limiting

New module: [`backend/services/rate_limit.py`](../../../backend/services/rate_limit.py) — a `slowapi` `Limiter` keyed on remote address, applied via `@limiter.limit("60/minute")` to every route listed in §2. Registered globally in `api/main.py` (`app.state.limiter`, `RateLimitExceeded` exception handler). Scoped narrowly to the four routers this sprint touched, per H-2's own framing in the audit (its severity was driven by compounding C-1, not a platform-wide redesign mandate).

## 7. Tests Added

New file: [`backend/tests/regression/test_security_auth.py`](../../../backend/tests/regression/test_security_auth.py) — 58 tests.

| Scenario | Coverage |
|---|---|
| Missing token rejected | `services/auth.py` unit-level + every Portfolio/Watchlist/Alerts/Terms endpoint |
| Invalid token rejected | unit-level + Portfolio (representative) |
| Expired token rejected | unit-level + Portfolio (representative) |
| Valid token accepted | unit-level |
| Valid token, mismatched `user_id`, rejected | `require_owner`/`require_matching_body_user` unit-level |
| Cross-user read/write/update/delete blocked | every Portfolio/Watchlist/Alerts/Terms endpoint, parametrized across GET/POST/PATCH/DELETE |
| Valid + matching token allowed through | every endpoint (DB layer mocked — no live Postgres needed in CI) |
| CORS rejects unapproved origin | confirmed an attacker-controlled `*.vercel.app` origin is not reflected in `Access-Control-Allow-Origin` |
| CORS allows approved origin | confirmed `localhost:3000` still works |

**Sanity-checked per this engagement's standing discipline:** temporarily removed `require_owner` from `portfolio.py`'s five routes and re-ran the suite — 16 of 20 portfolio-specific tests failed exactly as expected (the four "valid + matching" tests still passed, correctly, since they don't depend on the ownership check existing). Restored the fix; all 58 passed again. This confirms the tests actually exercise the fix rather than passing vacuously.

## 8. Before/After Security Validation

| | Before | After |
|---|---|---|
| `GET /api/portfolio/{any_user_id}` with no token | 200, returns that user's holdings | **401** |
| Same, with another user's valid token | 200, returns the *target* user's holdings | **403** |
| Same, with that user's own valid token | 200 | 200 (unchanged for the legitimate case) |
| CORS preflight from `https://attacker-app.vercel.app` | Reflected, credentialed | **Not reflected** |
| CORS from `http://localhost:3000` | Reflected | Reflected (unchanged) |
| Rate limit on these 5 endpoint families | None | 60/minute per IP |

## 9. Remaining Risks

- **M-1 through M-5 and L-1/L-2 from the original audit are unchanged** — this sprint scoped narrowly to C-1/H-1 (plus H-2's rate-limiting recommendation) per the remediation brief's explicit instruction not to redesign the platform. Verbose error disclosure, missing security headers, GitHub Actions `permissions:` blocks, localStorage sign-out residue, and dependency-CVE scanning automation remain open, as Phase 2/3/4 of the audit's own roadmap.
- **`paper_trading.py` (L-1) remains unauthenticated**, by the explicit scoping decision in §2 — its pseudonymous session model is a different, lower-priority risk class, not yet addressed.
- **`SUPABASE_JWT_SECRET` must be set in the Railway production environment** for this fix to take effect live — this sprint cannot confirm that from the repository alone; it's a deployment step, named here explicitly rather than assumed done.
- **The frontend's interceptor fetches a fresh session on every request** (`supabase.auth.getSession()`); this was not benchmarked for added latency — likely negligible (no network call when a valid session is already cached client-side by the Supabase SDK) but not measured.

## 10. Can the Local Audit Report Now Be Pushed?

**Yes, as-is, without sanitization.** The audit document ([Security-Audit-Pre-Epic-003.md](Security-Audit-Pre-Epic-003.md)) was held back specifically because it described an *unfixed* Critical vulnerability in exploitable detail. That vulnerability is now fixed, tested, and (pending the deployment step in §9) ready to go live — the document is now a historical record of a finding that has been remediated, not a live exploit guide. Recommend pushing both this report and the audit report together in the same push, so the history never shows the vulnerability described without its fix alongside it.

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

*This sprint fixed exactly the Critical and High findings named in the Mini Security Audit, plus H-2's rate-limiting recommendation as explicitly scoped in its own brief. No investment logic, scoring, recommendation, confidence calculation, explainability, or provider logic was touched — confirmed by the diff's scope, not assumed.*
