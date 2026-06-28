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

## Follow-up — Supabase JWT Signing Keys Compatibility

**Status:** Complete. Fixes a production-only defect surfaced after this sprint's deploy: legitimate, correctly-formed requests were rejected with 401 despite a present, correct `Authorization` header.

### Root Cause

`services/auth.py`'s `decode_supabase_jwt` only ever attempted HS256 verification against `SUPABASE_JWT_SECRET`. Confirmed via DevTools that production Supabase tokens carry `alg=ES256` with a `kid` in the header — issued by Supabase's current **JWT Signing Keys** feature, an asymmetric scheme this module had no path for at all. Every real token was rejected not because the signature was invalid, but because the verification function never tried the algorithm the token actually used.

### Minimal Fix

`decode_supabase_jwt` now dispatches on the token's own (unverified) `alg` header to select verification material, while keeping each branch locked to exactly one algorithm via `jwt.decode`'s `algorithms=[...]` parameter — an HS256 token can never be verified against asymmetric key material or vice versa, which is what actually prevents algorithm-confusion attacks (the dispatch only picks *which* key to fetch, never weakens the check):

- **ES256/RS256** (current default): public key fetched by `kid` from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json` via `jwt.PyJWKClient` (in-process key caching, no per-request network round trip once warm).
- **HS256** (legacy): preserved only if `SUPABASE_JWT_SECRET` is explicitly configured, for projects that haven't migrated. Not assumed present — absent the secret, HS256 tokens are still rejected, never silently allowed through.

New env var: `SUPABASE_URL` (documented in `.env.example`, same value as the frontend's `NEXT_PUBLIC_SUPABASE_URL`). New dependency: `PyJWT[crypto]` (adds `cryptography`, required for ES256 signature verification).

Authentication was never disabled or bypassed, CORS was not loosened, and `user_id` ownership checks (`require_owner`/`require_matching_body_user`) are untouched — this fix is entirely inside `decode_supabase_jwt`, the one function responsible for verifying a token's signature.

### Verification

| Case | Result |
|---|---|
| Valid ES256 Supabase-shaped token (local EC keypair standing in for a real Signing Key, mocked JWKS client) | Accepted |
| Missing token | 401 |
| Invalid token (malformed string) | 401 |
| Invalid token (ES256, signed by the wrong key for its claimed `kid`) | 401 |
| Expired ES256 token | 401 |
| `SUPABASE_URL` unconfigured, ES256 token presented | 401 (fails closed) |
| Valid ES256 token, mismatched `user_id` | 403 |
| End-to-end through `require_owner` on the Portfolio router (representative — same wiring as Watchlist/Alerts/Terms) | Allowed for matching user, 403 for cross-user, 401 for missing token |
| Existing HS256 legacy-path tests (`test_security_auth.py`) | Unmodified, still pass |

### Tests Added

New file: [`backend/tests/regression/test_security_jwt_signing_keys.py`](../../../backend/tests/regression/test_security_jwt_signing_keys.py) — 10 tests, using a locally-generated EC keypair (no live Supabase project or network call needed) and a mocked `PyJWKClient` stand-in. **Sanity-checked** by reverting `decode_supabase_jwt` to its original HS256-only form and re-running: exactly the 3 "valid ES256 token accepted" tests failed (the other 7 — missing/malformed/expired/mismatched-user/unconfigured-JWKS — still passed, correctly, since they don't depend on the ES256 branch existing), reproducing the exact production symptom. Restored the fix; all 10 passed again.

### Full Suite

510/510 passing (500 from Sprint #001 + 10 new).

### Production Verification

Pending deployment of `SUPABASE_URL` to Railway's production environment — recorded here once confirmed live.

### GitHub Actions Result

Recorded below, after this follow-up's commit is pushed and confirmed.

### Final Commit Hash

Recorded below, after this follow-up's commit.

---

## Security Closure Sprint — Final Hardening

**Status:** Complete. Resolves the two genuine findings the [Production Validation Sprint](Production-Validation-Sprint-Pre-Epic-003.md) confirmed remained — the only two items standing between "fixed" and "fully closed." **Security Workstream Complete** — see verdict below.

### Task 1 — JWT Issuer Validation

**Root cause:** `decode_supabase_jwt` never passed an `issuer=` argument to `jwt.decode()`, so the `iss` claim was accepted unconditionally.

**Fix:** both branches (HS256 legacy, ES256/RS256 JWKS) now validate `issuer=f"{SUPABASE_URL}/auth/v1"` — Supabase's issuer is the same Auth-service URL regardless of signing method, so one derived value covers both; no new config knob. Fails closed exactly like the existing secret/JWKS checks: if `SUPABASE_URL` is unconfigured, no expected issuer can be derived and the request is rejected, not silently let through. `SUPABASE_URL` is already a documented production requirement for the ES256 path (confirmed live and reachable during the Production Validation Sprint), so this introduces no practical regression for this deployment — only a named tradeoff for a hypothetical HS256-only deployment that never set it.

**Tests:** 9 tests across `test_security_auth.py` (existing fixtures updated to include a matching `iss`, mirroring real Supabase tokens), `test_security_jwt_signing_keys.py` (same), and `test_production_validation_security_regression.py` (correct/wrong/missing/malformed issuer, both algorithms, plus fail-closed-when-unconfigured — the former "finding" test renamed to confirm the fix). **Sanity-checked**: reverted the issuer check, confirmed exactly the 4 new issuer-specific tests failed, restored it, all pass.

### Task 2 — Railway Rate Limiter Client-IP Detection

**Root cause, confirmed not assumed:** `services/rate_limit.py` used slowapi's default `get_remote_address`, which reads `request.client.host` — Railway's edge-proxy address, not the real client's. This codebase's own pre-existing `accept_terms` route already needed `X-Forwarded-For` for correct IP logging for the identical reason, confirming the failure mode was real for this exact deployment, not theoretical.

**Fix:** new `get_client_ip()` reads the **rightmost** entry in `X-Forwarded-For` when present (Railway's own proxy appends the real connecting peer to the end of the chain — confirmed against Railway's own support guidance for exactly this spoofing concern: "the rightmost value... is trustworthy," a more specific answer than their separate cross-routing-path "take the first IP" recommendation, which doesn't address spoofing). Falls back to `request.client.host` when `X-Forwarded-For` is absent — unchanged behavior for local development and any non-Railway deployment. **One assumption is named explicitly in the code**, per the brief's own requirement: this trusts Railway as the sole reverse-proxy hop in front of the service; if a future topology change ever exposed a path that bypassed Railway's proxy, this assumption would need re-verification.

**Tests:** 7 tests in `test_rate_limiter_client_ip.py` — local request, Railway-forwarded single client, multiple clients correctly distinguished, malformed header, missing header/client, and two spoofing-attempt variants (the exact attack the fix defends against). **Sanity-checked**: reverted to the naive `client.host`-only behavior, confirmed exactly the 4 tests that depend on `X-Forwarded-For` failed (the two-client test produced the literal symptom: both "different" clients resolved to the same key), restored the fix, all 7 pass.

### Performance Impact

| Measurement | Result |
|---|---|
| ES256 verification + audience + issuer check (warm key) | **0.075ms/request** — identical to the pre-issuer-check baseline; adding the issuer comparison is not measurably different from noise. |
| `get_client_ip()` overhead | **0.0006ms/call** — header-string parsing, negligible next to anything else in the request path. |

**No measurable performance regression from either fix.**

### Test Summary

536/536 full backend suite passing (529 after Task 1 + 7 new for Task 2). Every new test sanity-checked by reverting its corresponding fix and confirming the expected subset fails.

### Production Validation

Re-ran the full regression suite covering Portfolio, Watchlist, Alerts, and Authentication after both fixes — all pass, confirming neither change broke the behavior the Production Validation Sprint had already confirmed working. Live, production smoke testing against the deployed Railway URL was not re-run this session (no browser/production-URL access available, same scope limitation as the Production Validation Sprint) — this sprint's evidence is the full regression suite plus the two sanity-check reverts above.

### Remaining Technical Debt

None identified as security-relevant. The four pre-existing, Low-severity, unrelated items named in the Production Validation Sprint (Portfolio duplicate prevention, symbol validation, missing FK constraints, 200-not-404 on no-op updates) remain open as product/data-hygiene items, not security debt — unchanged by this sprint, per its explicit "only address the two confirmed findings" scope.

### Final Recommendation

**Security Workstream Complete.** Both genuine findings from the Production Validation Sprint are now resolved, tested, sanity-checked, and confirmed to introduce no regression and no measurable performance cost. No further genuine security issues remain identified. **Recommend beginning Epic 003 — Growth Intelligence.**

---

*This sprint fixed exactly the Critical and High findings named in the Mini Security Audit, plus H-2's rate-limiting recommendation as explicitly scoped in its own brief. No investment logic, scoring, recommendation, confidence calculation, explainability, or provider logic was touched — confirmed by the diff's scope, not assumed. The JWT Signing Keys follow-up fixed a production-only compatibility defect inside the same `decode_supabase_jwt` function this sprint introduced, without touching authentication's existence, CORS, or any ownership check. The Security Closure Sprint resolved the two remaining genuine findings (JWT issuer validation, rate-limiter client-IP detection) with the same narrow, evidence-backed discipline, closing the security workstream ahead of Epic 003.*
