# Production Validation Sprint — Final Gate Before Epic 003

**Status:** Complete, with two genuine findings (one Medium, one Low) and several explicit scope limitations honestly named rather than assumed. **No code was redesigned, refactored, or "improved" — this is a validation-only sprint**, exactly as instructed. Validates [Security Remediation Sprint #001](Security-Remediation-Sprint-001.md) and its [ES256/JWKS follow-up](Security-Remediation-Sprint-001.md#follow-up--supabase-jwt-signing-keys-compatibility) for regressions.

**Scope boundary, stated honestly upfront:** this validation had no browser-automation tool connected to the live deployed frontend, no production Railway URL configured in this repository, and no production database credentials. Everywhere that matters, this report says so explicitly rather than fabricating a "verified" result — per this engagement's own evidence-over-assumption discipline. What follows is real evidence from: the full backend test suite (524/524), direct git-diff forensics across all three security commits, code-level review of every touched router, a live (read-only, unauthenticated) check against the actual production Supabase JWKS endpoint, and in-process latency microbenchmarks.

---

## Authentication

| Check | Result |
|---|---|
| Login / session establishment | Out of repository scope — handled entirely by Supabase Auth client-side (`@supabase/ssr`); no backend code path was touched by either security sprint. **No production defect found** (nothing in scope changed). |
| Logout | `AuthContext.tsx`'s `signOut()` is unmodified by either sprint (confirmed via diff). **No production defect found.** |
| Session refresh | Supabase's own SDK handles refresh; the new axios interceptor (`utils/api.ts`) calls `supabase.auth.getSession()` fresh on every request, which returns a refreshed token transparently if the SDK already refreshed it. **No production defect found** in the code path touched. |
| Expired token handling | Directly tested: an expired token (HS256 and ES256) is rejected with 401 by `get_current_user_id`. **No production defect found.** |
| Browser refresh / multiple tabs / remembered sessions | **Scope limitation — not independently verified.** This requires live browser interaction against the deployed frontend; no browser-automation tool was connected and no production URL was available in this session. Supabase's own session storage (cookie-based via `@supabase/ssr`, confirmed in this engagement's earlier audit) is designed to survive refresh/multi-tab/persistence by default and was not modified by either security sprint — but this is a reasoned inference from unmodified code, not a live-observed result, and is named as such. |

## Portfolio

| Check | Result |
|---|---|
| Add / Edit / Delete (authenticated) | Confirmed via the regression suite end-to-end through `require_owner`: matching user succeeds, cross-user blocked (403), missing/invalid/expired token blocked (401). **No production defect found.** |
| Import | `import_holdings` confirmed unchanged in business logic (merge-not-replace, original-symbol cleanup) — only the `Depends(require_owner)` parameter was added. **No production defect found.** |
| Export | Confirmed entirely client-side (`utils/portfolioExport.ts`, generates an `.xlsx` from already-loaded browser state) — makes no backend API call at all, so it cannot be affected by any authentication change. **No production defect found.** |
| Cross-user isolation | Confirmed by 20+ parametrized tests across every Portfolio route — a token authenticated as one user cannot read/write/delete another user's holdings. **No production defect found.** |
| Duplicate prevention | **Finding (pre-existing, not a regression) — see Findings §1.** |
| Invalid symbols | **Finding (pre-existing, not a regression) — see Findings §2.** |
| Concurrent edits | `update_holding`/`delete_holding` use a single atomic `UPDATE/DELETE ... WHERE id = %s AND user_id = %s` — standard last-write-wins, unmodified by either security sprint. No optimistic-locking or versioning exists, but this was never present before either sprint, so there is no regression. **No production defect found** relative to this sprint's actual change. |

## Watchlist

| Check | Result |
|---|---|
| Add / Remove | Confirmed via regression suite — only `Depends(require_owner)` was added; the `ON CONFLICT (user_id, symbol, market) DO NOTHING` dedup logic and the file-fallback path are byte-for-byte unchanged except for that one new parameter. **No production defect found.** |
| Duplicate handling | DB-level `ON CONFLICT ... DO NOTHING` confirmed present and unmodified. **No production defect found.** |
| Cross-user isolation | Confirmed by parametrized tests. **No production defect found.** |
| Persistence after logout/login | Backend-side: rows persist in Postgres regardless of session state (no session-scoped storage). Client-side persistence across a real logout/login cycle is a live-browser behavior — same scope limitation as Authentication's multi-tab item above. |

## Alerts

| Check | Result |
|---|---|
| Create / Update / Delete | Confirmed via regression suite — same pattern as Portfolio/Watchlist. **No production defect found.** |
| Trigger logic | `services/price_alert_notifier.py` (the background checker referenced in `alerts.py`'s own comments) was not touched by either security commit — confirmed via the diff in §0 of the methodology. **No production defect found.** |
| Ownership enforcement | Confirmed — `require_owner` wired identically to Portfolio/Watchlist. **No production defect found.** |

## Prediction Engine

Confirmed via direct `git diff --name-only` across all three security commits (`7b44955`, `4461c26`, `37c42f2`): **zero files matching `prediction_engine`, `business_quality`, `financial_strength`, `daily_picks`, `quality_factors`, `multibagger_scorecard`, or `case_generator` appear anywhere in the diff.** The only files touched were: 4 routers (portfolio/watchlist/alerts/auth), `api/main.py` (CORS + rate-limiter registration only), 2 new `services/` modules (`auth.py`, `rate_limit.py`), `requirements*.txt`, `.env.example`, one frontend file (`utils/api.ts`, interceptor only), and documentation. **No production defect found — predictions, confidence, Financial Strength integration, and Daily Picks are provably unchanged, not just assumed unchanged.**

## Database

| Check | Result |
|---|---|
| No orphan rows | **Scope limitation — not independently verified.** No production database credentials were available to this session; querying the live Supabase Postgres instance was not attempted. |
| User ownership preserved | Confirmed at the **application layer** — every write still includes `user_id` exactly as before either sprint; the security work added an authorization *check* in front of existing writes, it did not change what gets written. **No production defect found** in the code touched. |
| No API bypass | Confirmed — every Portfolio/Watchlist/Alerts/Terms route requires `Depends(require_owner)` or equivalent; `paper_trading.py` is the one router still unauthenticated, but that is a documented, deliberate scope decision from Sprint #001 (its `user_id` is a pseudonymous session token, not a real identity), not a bypass of the fix. **No production defect found** relative to the fix's own stated scope. |
| No broken foreign keys | **Finding (pre-existing, not a regression) — see Findings §3.** |

## Performance

All measurements below are **real, executed benchmarks**, not estimates — with the explicit caveat that absolute network latency depends on Railway's actual network path to Supabase, which cannot be measured from this session; only the *mechanism's* behavior (cold vs. warm, verification CPU cost) is measured directly.

| Measurement | Result |
|---|---|
| Pure ES256 signature verification (key already warm/cached) | **0.075ms/request**, averaged over 1,000 iterations |
| JWKS key lookup, cold (first request — real network fetch to the actual production Supabase project's JWKS endpoint, confirmed live and reachable) | **310ms** (one-time cost; this specific number reflects this session's network path, not Railway's) |
| JWKS key lookup, warm (`PyJWKClient`'s own in-process cache, `cache_keys=True`) | **0.10ms** — a ~3,000x reduction after the first request |
| Total authentication overhead, steady state (warm cache — the case for all but the very first request after a process restart) | **~0.2ms**, negligible next to typical API/DB latency (tens to hundreds of ms) |
| Portfolio / Watchlist / Alerts API latency | **Scope limitation — not independently measured.** No live production URL was available to this session; these endpoints' DB-call latency was not changed by this sprint's work (no new queries were added to the hot path), so no regression is expected, but this is not a live-measured result. |

**No unacceptable degradation found** in the mechanism actually changed (JWT verification + JWKS lookup) — the steady-state overhead is negligible. The cold-start cost (~310ms, once per process lifetime per unique `kid`) is a one-time cost paid by whichever request happens to be first after a deploy/restart, not a per-request tax.

## Browser Validation

**Scope limitation — not independently verified.** No browser-automation tool was connected to the live deployed site in this session, and no production frontend URL was available in this repository's environment files (only `http://localhost:3000` is configured locally). The user's own brief states Chrome/Safari/Firefox/mobile were already manually verified in production after deployment — this report does not re-verify that claim, since doing so would require live browser access this session does not have. Stated honestly rather than assumed.

## Error Handling

| Code | Result |
|---|---|
| 401 | Confirmed correct for: missing header, malformed header, garbage token, wrong signature, modified payload, expired token, wrong audience, `alg: none`, algorithm-confusion attempt, all malformed `Bearer` formatting variants (wrong case, empty, wrong scheme). 14 dedicated regression tests, all passing. |
| 403 | Confirmed correct for: any authenticated-but-mismatched `user_id`, across every Portfolio/Watchlist/Alerts/Terms route. |
| 404 | **Finding (pre-existing, not a regression) — see Findings §4.** |
| 500 | Existing routers still wrap DB exceptions in `HTTPException(500, detail=str(e))` — unchanged by either security sprint (this was M-1 in the original Mini Security Audit, explicitly out of scope for Sprint #001's narrow fix). **No regression** — this is the same pre-existing behavior, not new. |
| Network failures | Not independently reproducible without live production access — the frontend's `acceptTerms`/`getTermsStatus`/portfolio calls use the existing axios client's default error propagation, unchanged by the interceptor addition (the interceptor only adds a header; it does not alter error handling). **No production defect found** in the code touched. |

## Security Regression

All of the specifically-named attack vectors were reproduced and confirmed handled correctly, **except one genuine gap**, found and reported rather than fixed (validation-only sprint):

| Attack | Result |
|---|---|
| Cross-user portfolio/watchlist/alerts access | **Blocked (403).** Confirmed across 20+ parametrized tests. |
| Forged JWT (garbage string) | **Blocked (401).** |
| Modified JWT payload (tampered `sub`, signature not re-computed) | **Blocked (401)** — signature check catches it. |
| Expired JWT | **Blocked (401).** |
| Wrong audience | **Blocked (401)** — PyJWT's own `audience=` check. |
| Wrong issuer | **NOT blocked — genuine finding. See Findings §5.** |
| Wrong signature | **Blocked (401).** |
| Algorithm confusion (HS256 forged using the ES256 public key as an HMAC secret, hand-built to bypass PyJWT's own encoder safety rail) | **Blocked (401)** — confirmed the HS256 and ES256/RS256 branches use entirely separate key material; the public key is never reachable from the HS256 code path. |
| `alg: none` | **Blocked (401).** |
| Missing Authorization header | **Blocked (401).** |
| Bearer formatting errors (wrong case, empty, wrong scheme) | **Blocked (401),** 5 variants tested. |

## Documentation

`INDEX.md` updated to reference this report. No other documentation required genuine changes — Sprint #001's and the JWKS follow-up's existing reports already accurately describe the shipped fix; this validation sprint adds new evidence, not new corrections to prior claims.

---

## Findings

### Finding 1 — Portfolio single-add has no duplicate prevention (Low, pre-existing, not a regression)

**Reproduction:** `POST /api/portfolio/{user_id}` (single-holding add) always `INSERT`s a new row; unlike `import_holdings` (which merges via an `existing_map`), it never checks whether a holding for the same symbol+market already exists for that user.
**Evidence:** `api/routers/portfolio.py`, `add_holding` — confirmed no existence check; confirmed unchanged by either security commit (the only diff to this function was adding the `Depends(require_owner)` parameter).
**Severity:** Low. Pre-existing product behavior, not introduced or affected by the security work.
**Recommendation:** Out of scope for this validation sprint. If product wants single-add deduplication to match import's behavior, that's a separate, scoped feature request — not a defect this sprint's security changes caused.

### Finding 2 — No symbol-format validation on Portfolio/Watchlist/Alerts (Low, pre-existing, not a regression)

**Reproduction:** `symbol: str` (Pydantic) accepts any string, including empty strings or non-ticker garbage, on all three routers.
**Evidence:** No regex/whitelist validation found in any of the three Pydantic models across the three routers; confirmed unchanged by either security commit.
**Severity:** Low. Pre-existing, unrelated to authentication/authorization.
**Recommendation:** Out of scope for this sprint.

### Finding 3 — No foreign-key constraint from `user_id` to Supabase's `auth.users` (Low, pre-existing, not a regression)

**Reproduction:** `portfolio_holdings`, `price_alerts`, and `watchlist`'s `CREATE TABLE` statements declare `user_id TEXT NOT NULL` with no `REFERENCES auth.users(id)` and no `ON DELETE CASCADE`.
**Evidence:** Read directly from each router's `_ensure_table()` (portfolio.py, alerts.py) and the watchlist schema referenced in `postgres_store.py`.
**Severity:** Low. If a Supabase user is ever deleted, their rows in these three tables become orphaned (not automatically cleaned up) — a real data-hygiene gap, but pre-existing and not something either security sprint touched or introduced.
**Recommendation:** Out of scope for this validation sprint; a future, separately-scoped migration could add the FK + cascade if product wants automatic cleanup on user deletion.

### Finding 4 — Update/Delete on a non-existent `holding_id`/`alert_id` returns 200, not 404 (Low, pre-existing, not a regression)

**Reproduction:** `update_holding`/`delete_holding` (and the Alerts equivalents) issue a single `UPDATE/DELETE ... WHERE id = %s AND user_id = %s` and unconditionally return `{"ok": True}` regardless of whether any row actually matched.
**Evidence:** Confirmed by reading `portfolio.py`/`alerts.py`'s update/delete handlers directly; confirmed unchanged by either security commit except for the new ownership-check parameter.
**Severity:** Low. Means a client can't distinguish "successfully deleted" from "nothing matched" — a minor error-handling fidelity gap, pre-existing.
**Recommendation:** Out of scope for this validation sprint.

### Finding 5 — JWT `iss` (issuer) claim is never validated (Low/Medium, defense-in-depth gap, confirmed by direct reproduction)

**Reproduction:** A correctly-signed, non-expired, correct-audience HS256 token whose `iss` claims to be `https://a-completely-different-project.supabase.co/auth/v1` is **currently accepted** by `get_current_user_id` — reproduced directly in `tests/regression/test_production_validation_security_regression.py::test_wrong_issuer_NOT_REJECTED_finding`.
**Evidence:** `services/auth.py`'s `decode_supabase_jwt` never passes an `issuer=` argument to either `jwt.decode()` call (HS256 or ES256/RS256 branch) — confirmed by direct code review.
**Severity:** Low/Medium. Practical exploitability is genuinely low for the ES256/JWKS path specifically: the public key resolved for a given `kid` is already scoped to *this* Supabase project's own JWKS document (fetched from `<SUPABASE_URL>/.../jwks.json`), so a token that actually passes signature verification already had to be signed by this project's real private key — an attacker can't get a different project's token to pass signature verification here regardless of what `iss` it claims. The legacy HS256 path has slightly more exposure in principle (the secret is a single shared value with no per-project key separation enforced by the verification code itself), but exploiting it would still require already knowing `SUPABASE_JWT_SECRET`, at which point the attacker already has everything needed to forge any claim, `iss` included.
**Recommendation:** Add `issuer=f"{SUPABASE_URL}/auth/v1"` to both `jwt.decode()` calls as a genuine, narrow, low-risk defense-in-depth improvement — worth doing in a small follow-up, not blocking Epic 003.

### Finding 6 — The rate limiter likely buckets all users together behind Railway's reverse proxy (Medium, confirmed by code evidence, not a security hole but a reliability risk)

**Reproduction/Evidence:** `services/rate_limit.py`'s `Limiter(key_func=get_remote_address)` uses slowapi's default `get_remote_address`, which reads `request.client.host` — the immediate TCP peer, not the real client IP, when a platform like Railway proxies HTTP traffic. **This codebase already had to work around exactly this** — `api/routers/auth.py`'s pre-existing `accept_terms` route reads `request.headers.get("x-forwarded-for", request.client.host if request.client else "unknown")` specifically because `request.client.host` alone isn't reliable in this deployment. The rate limiter added in Sprint #001 does not apply the same `X-Forwarded-For` handling.
**Severity:** Medium. Not a security vulnerability (it doesn't weaken authentication or authorization) — it's a reliability/availability risk: if `request.client.host` resolves to Railway's proxy address for every request, the intended "60 requests/minute *per user*" limit instead becomes "60 requests/minute combined *across all users*," meaning a small number of concurrently active users could cause unrelated users to receive false 429 responses.
**Recommendation:** Replace `get_remote_address` with a custom key function that checks `X-Forwarded-For` first (mirroring the existing, working pattern already in `auth.py`'s `accept_terms`), falling back to `request.client.host`. Worth a small, narrowly-scoped follow-up fix before relying on the rate limiter at any real scale — not necessarily a hard blocker for Epic 003 itself, since Epic 003 doesn't touch these routes, but should be fixed before the user base using Portfolio/Watchlist/Alerts grows materially.

---

## Required Validation — Results

- **Full test suite:** 524/524 passing (510 from the two security sprints + 14 new validation-sprint security-regression reproductions added in this sprint).
- **Production smoke tests:** Not run against a live production URL — scope limitation, stated above; this sprint's evidence is code-forensic plus one live, read-only check against the real production Supabase JWKS endpoint (confirmed reachable, serving exactly one ES256 key).
- **Regression tests:** 524/524, including new coverage for every named attack vector.
- **GitHub Actions verification:** Confirmed green for the current `main` HEAD (`37c42f2`) — the commit currently deployed.

---

## Deliverables

**Executive Summary:** The two security sprints did exactly what they claimed and introduced no regressions to Portfolio, Watchlist, Alerts, Terms, or the Prediction Engine/intelligence layer — confirmed by direct diff forensics, not assumed. Every named attack vector in this sprint's checklist was reproduced and confirmed blocked, with one real defense-in-depth gap found (JWT issuer not validated — Low/Medium, low practical exploitability) and one real reliability risk found (the rate limiter likely doesn't see real per-client IPs behind Railway's proxy — Medium, not a security hole). Four additional Low-severity items were confirmed pre-existing and unrelated to either security sprint (Portfolio duplicate prevention, symbol validation, missing FK constraints, 200-instead-of-404 on no-op updates/deletes) — named for completeness per the checklist, not recommended for action now. Browser-level, live-production-latency, and database-content verification were out of this session's reach and are named as explicit scope limitations rather than claimed as verified.

- **Production Readiness Score: 8/10** — the two findings that matter (issuer validation, rate-limiter IP detection) are real but neither is currently exploitable at meaningful severity; the gap from 10 reflects those two items plus the honest scope limitations (live browser/production-latency/DB verification not performed this session).
- **Security Score: 8/10** — every named attack vector blocked correctly except the low-exploitability issuer gap; no regression to the prior sprints' fixes.
- **Stability Score: 9/10** — zero functional regressions found anywhere in scope; the one deduction is the rate-limiter reliability risk (Finding 6), which could cause false 429s under concurrent load, not data loss or corruption.
- **Performance Score: 9/10** — measured authentication overhead is negligible (~0.2ms steady-state); the one-point deduction reflects that live production API latency wasn't independently measured this session.

**Regression status:** None found in Portfolio, Watchlist, Alerts, Terms, CORS, or the Prediction Engine/intelligence layer. Two genuine, narrowly-scoped findings — neither a regression of the security sprints' own stated goals, both candidates for a small, separately-scoped follow-up.

**Recommendation: Ready after minor fixes.** Specifically: Finding 6 (rate-limiter IP detection) is worth fixing before relying on the rate limiter at scale, and Finding 5 (issuer validation) is a cheap, low-risk hardening addition — both small, narrow, non-architectural changes consistent with this engagement's "fix narrowly" discipline. Neither blocks Epic 003 itself, since Epic 003 doesn't touch any of the code in question — but both should be closed out before this sprint is considered fully done, rather than carried forward indefinitely.

---

*This was a validation-only sprint. No production code was modified — only a new test file reproducing the checklist's named attack vectors was added, and this report. Every "No production defect found" line reflects either a passing automated test or direct, cited code-diff evidence; every scope limitation is named explicitly rather than silently assumed.*
