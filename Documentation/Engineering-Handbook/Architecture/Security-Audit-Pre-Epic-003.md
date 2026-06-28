# StockSense360 Mini Security Audit — Pre-Epic 003

**Status:** Audit only. No production code, tests, providers, or intelligence engines were modified. Per this audit's own explicit rule, a Critical finding was discovered and the audit paused to report it before any further work — the user instructed continuing the full review before remediation, which this document reflects.
**Scope boundary, stated honestly:** this audit reviewed everything visible in the repository — backend, frontend, CI workflows, dependency manifests, and git history. It could **not** directly inspect live Railway/Vercel/Supabase dashboard configuration (actual production environment variables, Supabase RLS policy definitions as currently configured, live HTTP response headers, GitHub branch-protection settings) — these are named explicitly as unverified, not assumed safe or unsafe.

---

## 1. Executive Security Summary

StockSense360's intelligence engines (Prediction Engine, Business Quality, Financial Strength) and provider layer (SEC EDGAR, yfinance, screener.in) show no security defects — secrets are handled correctly everywhere reviewed, SQL is parameterized everywhere user input reaches it, and no hardcoded credentials exist in current code or git history. **The platform's serious risk is concentrated entirely in one place: the user-data API layer has no authentication or authorization enforcement at all.** Every endpoint that reads or writes a specific user's data (Portfolio, Watchlist, Price Alerts, and the Terms-Acceptance PII record) trusts a `user_id` value supplied directly by the caller, with zero verification that the caller is actually that user. This is compounded by a CORS policy permissive enough that, if a session-cookie-based fix were added without also tightening CORS, it would immediately become exploitable cross-origin. No rate limiting exists anywhere in the API, and verbose internal error messages are returned to clients.

---

## 2. Critical Risks

### C-1. Broken Access Control / IDOR across all user-data endpoints

**Confirmed live in source, not assumed.** `api/routers/portfolio.py`, `watchlist.py`, `alerts.py`, and `auth.py`'s terms-acceptance endpoints all accept `user_id` as a plain path/query/body parameter. None of them contain a `Depends(...)` auth check, an `Authorization` header read, or any token verification — confirmed by direct grep across all four files (zero matches for any of those patterns).

**Confirmed exploitable end-to-end, not theoretical:** the frontend (`portfolio/page.tsx:236`) reads the real, Supabase-authenticated `user.id` and places it directly in the URL (`/api/portfolio/${userId}`) with **no `Authorization`/`Bearer` token attached to the request at all** — confirmed by a repository-wide search for `Authorization`/`Bearer`/`access_token` across every frontend page, which found zero such usage anywhere a backend API call is made. Watchlist and Alerts follow the identical pattern (`useAuth()` → `user.id` → URL path param).

**Impact:** any party who learns another user's Supabase `user_id` (a UUID, visible in that user's own browser URL bar at minimum, and potentially leakable via referrer headers, shared links, or browser history) can read and **write** that user's portfolio holdings, watchlist, and price alerts, and read their terms-acceptance PII (name, mobile, country, email) — with no login of their own required.

**No defense-in-depth underneath this either, confirmed by this engagement's own prior architecture documentation:** the backend connects to Supabase Postgres using the `postgres` role, which carries `BYPASSRLS` by default (confirmed in SSDS-000 §5, by direct reading of `postgres_store.py` during this engagement's earlier work). Even if Row Level Security policies exist in the Supabase project, this backend's own database connection ignores them entirely.

**OWASP mapping:** A01:2021 — Broken Access Control. **Severity: Critical.**

---

## 3. High Risks

### H-1. Permissive CORS policy that would become directly exploitable if a cookie-based fix to C-1 is added without also fixing this

`api/main.py`: `allow_origin_regex=r"https://.*\.vercel\.app"` combined with `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`. The regex matches **any** app hosted on Vercel's shared `*.vercel.app` domain — not just this project's own deployment. Today this is lower-impact than it looks, because the backend issues no session cookie of its own (confirmed — there's nothing for a malicious `*.vercel.app` page to "ride along" with yet, given C-1's finding that no token is even checked). **But this is a real, named forward-looking risk:** if C-1 is fixed by adding a backend session/cookie mechanism without first tightening this CORS policy to the project's actual frontend domain only, any attacker-controlled site on `*.vercel.app` would immediately be able to issue credentialed cross-origin requests on a victim's behalf. **Severity: High** (not yet directly exploitable today, but a real trap for the most likely remediation path).

**OWASP mapping:** A05:2021 — Security Misconfiguration.

### H-2. No rate limiting anywhere in the API

Confirmed by repository-wide search: no `slowapi`, no rate-limiting middleware, no per-IP/per-user throttling anywhere in `api/`. This directly compounds C-1 — there is nothing slowing down an attempt to enumerate or brute-force `user_id` values, and nothing protecting expensive endpoints (`/api/picks`, prediction generation) from being hammered. **Severity: High**, specifically because of its interaction with C-1; would be Medium in isolation.

**OWASP mapping:** A04:2021 — Insecure Design (no abuse-case protection).

---

## 4. Medium Risks

### M-1. Verbose internal error disclosure
`raise HTTPException(status_code=500, detail=str(e))` appears repeatedly across `portfolio.py`, `alerts.py`, and others — returning raw Python exception text (potentially internal details: SQL fragments, file paths, library-specific messages) directly to API clients. **OWASP A05.**

### M-2. No security headers set by the backend
No `X-Frame-Options`, `Content-Security-Policy`, `Strict-Transport-Security`, or `X-Content-Type-Options` set anywhere in `api/main.py`. **Not independently verified at the live HTTP layer** — Railway's edge or a CDN in front of it may add some of these; this is named as unconfirmed, not assumed broken. Recommend checking live response headers directly. **OWASP A05.**

### M-3. No `permissions:` block in any GitHub Actions workflow
All four workflow files (`daily_picks.yml`, `multibagger_refresh.yml`, `multibagger_refresh_us.yml`, `backend_tests.yml`) omit an explicit `permissions:` block, relying on whatever the repository's default `GITHUB_TOKEN` permissions are — not a confirmed misconfiguration (default could already be read-only at the org level, unverified from the repo alone), but a real hardening gap against the principle of least privilege. **OWASP A05.**

### M-4. Browser-storage residue of user financial data after sign-out
`portfolio/page.tsx` and `alerts/page.tsx` cache holdings/alerts in `localStorage` as a local mirror/fallback. No code path was found that clears this on sign-out, meaning a shared or public computer could retain a previous user's cached portfolio/alert data in browser storage after they log out. **OWASP A04 (Insecure Design) / privacy-adjacent.**

### M-5. Dependency currency — not CVE-matched, named as a process gap
No automated CVE-matching tool (`pip-audit`, `npm audit`, Dependabot) was run as part of this audit — I do not have live CVE-database access, and fabricating specific CVE numbers from memory would violate this engagement's own evidence-over-assumption discipline. Versions reviewed (`fastapi>=0.111.0`, `requests>=2.31.0`, `next@^16.2.9`, `@supabase/supabase-js@^2.108.2`) appear current by inspection, but this is a **process gap, not a clean bill of health** — recommend enabling Dependabot/`pip-audit` in CI rather than relying on point-in-time manual review.

---

## 5. Low Risks

### L-1. Paper-trading's anonymous session ID is a real but lower-severity variant of C-1
`useSessionId()` generates a client-side random UUID stored in `localStorage` (`paper_session_id`) — used for the paper-trading flow specifically, confirmed to be architecturally pseudonymous by design (not tied to a real Supabase identity). The same lack of server-side ownership verification exists here too, but exploitability requires guessing a high-entropy random UUID rather than a known, real user's ID — materially lower likelihood than C-1, though the same class of gap.

### L-2. SQL f-string usage, confirmed safe but stylistically worth tightening
Three call sites (`fundamentals_cache.py:160,178`, `postgres_store.py:451`) build SQL via f-strings rather than pure parameterization. **Confirmed not exploitable**, directly traced: every interpolated value originates from a fixed, internal, hardcoded vocabulary (a module-level `FIELD_MAP`, a hardcoded `_SCREENS` dict's keys, a 3-key `{"short":...}` dict) — never free-form user text. Named as a style/defense-in-depth recommendation (prefer fully parameterized SQL even when current inputs are safe), not a real vulnerability.

---

## 6. Security Score: **4/10**

Driven down almost entirely by C-1's severity and breadth (it affects every piece of real user data the product persists) and H-1's forward-looking exploitability trap. Everything *outside* the user-data API layer — secrets management, the intelligence engines, the provider layer, SQL-injection resistance, frontend XSS resistance — scored cleanly with real evidence and would independently justify a much higher number. The score reflects that one finding's severity, not an average across all categories.

## 7. Production Readiness Score: **3/10**

A product that persists real user financial data (portfolio holdings, price targets) cannot be considered production-ready while any party can read and modify that data without authentication. This score is specifically about *this platform serving real users in production* — it is not a judgment on the intelligence engines' own quality, which this audit found no issues with.

---

## 8. Immediate Fixes (recommended — not applied, per this audit's explicit rule)

1. **Add real backend authentication to every user-data route.** Verify the Supabase-issued JWT (sent as an `Authorization: Bearer` header — which the frontend does not currently send and would need to start sending) on `portfolio.py`, `watchlist.py`, `alerts.py`, and `auth.py`'s terms endpoints, and confirm the token's subject matches the `user_id` in the request before allowing the operation. This is the single fix that resolves C-1.
2. **Tighten the CORS policy before or together with fix #1** — restrict `allow_origin_regex` to the project's actual frontend domain(s) only, not the shared `*.vercel.app` wildcard, specifically *before* any cookie-based session mechanism is introduced (resolves H-1's forward-looking trap).
3. **Add basic rate limiting** (e.g., `slowapi`) to the user-data routes at minimum, ideally platform-wide.

## 9. Recommended Security Improvements (broader hardening, not urgent blockers on their own)

- Replace `detail=str(e)` with a generic client-facing message; log the real exception server-side only.
- Add baseline security headers (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, a starting CSP) via FastAPI middleware.
- Add `permissions:` blocks to every GitHub Actions workflow, scoped to the minimum each job needs.
- Clear `localStorage` portfolio/alerts caches on sign-out.
- Enable Dependabot (or run `pip-audit`/`npm audit` in CI) for ongoing dependency-CVE visibility.
- Independently verify live production HTTP response headers and confirm HTTPS is enforced end-to-end (Railway + Vercel) — not verifiable from the repository alone.
- Independently verify Supabase's actual RLS policy configuration in the live project, even though the backend's current `BYPASSRLS` role means RLS isn't currently StockSense360's protection layer for backend-mediated access — RLS still matters if anything ever queries Supabase directly from the frontend with the anon key.

## 10. Security Roadmap

| Phase | Scope | Sequencing rationale |
|---|---|---|
| **Phase 1 (before any further user-facing rollout)** | Immediate Fixes #1–#3 above | C-1 and H-1 are the only findings that block real users' data being safe — everything else is hardening on top of an already-sound foundation. |
| **Phase 2** | M-1 through M-4 | Real but lower-severity; reasonable to batch into one focused hardening sprint once Phase 1 lands. |
| **Phase 3** | M-5, dependency-scanning automation | A process improvement, not a point-in-time fix — set it up once and it keeps paying off. |
| **Phase 4** | L-1, L-2 | Low priority; address opportunistically, e.g. alongside other work touching the same files. |

---

## 11. Is StockSense360 Ready to Begin Epic 003?

**Yes, from an engineering-architecture standpoint — this audit found nothing in the Prediction Engine, Business Quality Engine, Financial Strength Engine, the Data Fabric, or Daily Picks that would block or complicate Epic 003's design work.** The Critical/High findings are entirely confined to the user-account-data API layer, a different subsystem from the intelligence engines Epic 003 will build on. Epic 003 (Growth Intelligence) can proceed on schedule.

## 12. Should Any Findings Block the Private Beta?

**Yes — C-1 should block any Private Beta that involves real users entering real portfolio, watchlist, or alert data.** A beta where any participant can read or modify another participant's financial data is not a safe beta, regardless of how few users are involved — the exposure is per-user-data, not volume-dependent. **H-1 should be fixed in the same pass**, specifically because it is the most likely way C-1's own fix could be deployed unsafely. None of the Medium/Low findings need to block a beta on their own, though M-1 (error disclosure) is cheap enough to fix in the same pass.

**If the beta is strictly limited to anonymous, session-based features only (e.g., paper trading via the existing local `paper_session_id` mechanism, with no real Supabase-authenticated portfolio/watchlist/alerts data ever entered), C-1's blast radius is reduced but not eliminated (L-1 still applies) — this is a narrower, evidence-based exception, not a blanket "it's fine."**

---

*This document is an audit only. No production code, tests, providers, or intelligence engines were modified in producing it. The Critical finding (C-1) was discovered and reported before the audit continued, per this audit's own explicit instruction; the user directed the audit to continue to completion before any remediation work begins.*
