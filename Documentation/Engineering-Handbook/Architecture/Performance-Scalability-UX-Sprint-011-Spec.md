# Sprint 011 — Performance, Scalability & User Experience: Functional Specification

## 0. Status and Purpose

**Status: Planned / Not Started. This is a specification only — no production code, configuration, infrastructure, or database change described in this document has been made.** Nothing here authorizes implementation, deployment, dependency changes, infrastructure provisioning, or schema migration.

**Naming note (read first).** This is a standalone, cross-cutting sprint number ("Sprint 011") — it is **not** the 11th sprint of any single existing Epic's own sprint sequence (Epic 002, 003, 004, and 005 each already have their own independently-numbered Sprint #001–#012 series; see `INDEX.md`). To avoid a repeat of the Epic 007 naming collision documented in [`Current-Release-Status.md`](../Operations/Current-Release-Status.md), whoever schedules this work should confirm at planning time whether it should instead be folded into an existing Epic's sequence or kept as its own cross-cutting initiative, as this document assumes.

This specification audits and plans performance, scalability, and UX improvements across the whole platform (frontend, backend, database, infrastructure, and the intelligence engines specifically), following the same evidence-before-implementation discipline used throughout this codebase's engineering history (e.g. `Sprint-Epic002-012-Prediction-Pipeline-Performance.md`, the only prior dedicated performance sprint on record).

## 1. Vision

StockSense360 should feel fast and predictable at every scale it actually operates at today, and should have a documented, evidence-based plan for the scale it does not yet operate at. Performance and scalability work must be validated the same way every intelligence engine in this codebase has been — real measurement first, optimization second, never a speculative rewrite justified only by intuition.

**Guiding principle, carried over from this codebase's established practice:** no claim that something is "slow" or "will not scale" may be made without a number attached to it, and no fix may be described as complete without a before/after number attached to it either (mirroring SES-001's evidence-over-assertion rule).

## 2. Success Metrics

| Area | Metric | Target (illustrative — to be confirmed against real baseline in Phase 0) |
|---|---|---|
| Frontend | Largest Contentful Paint (LCP), Stock Detail / Daily Picks pages | < 2.5s on a throttled 4G profile |
| Frontend | Time to Interactive | < 3.5s |
| Frontend | Bundle size (initial JS, Daily Picks route) | Documented baseline first; reduction target set only after Phase 0 audit |
| Backend | `/api/predictions/*` p95 latency (cache hit) | < 200ms |
| Backend | `/api/predictions/*` p95 latency (cache miss / cold fetch) | Documented baseline first — this path's cost is provider-fetch-dominated, not code-dominated, per prior sprint findings |
| Daily Picks | Full generation job duration (India / US) | Documented current baseline first (known from Section 10 to be tens of minutes); reduction target set only after profiling |
| Database | p95 query latency for hot-path reads (`/api/picks/status`, `/api/predictions/debug/state`) | < 50ms |
| Availability | Backend uptime during a Daily Picks generation window | No regression from current observed behavior |
| Mobile | Core Web Vitals on a mid-tier Android device profile | Documented baseline first — no mobile-specific performance measurement exists today (confirmed gap, Section 13) |

Every target above marked "documented baseline first" reflects an honest current-state gap: **no systematic performance baseline exists today across most of these dimensions.** This document does not fabricate targets from assumption; Phase 0 of the rollout plan (Section 17) exists specifically to establish real numbers before any target is treated as binding.

## 3. Current Architecture Review

Per [SSDS-000 — System Architecture](../SSDS/SSDS-000-StockSense360-System-Architecture.md):

- **Frontend:** Next.js 14+ with TailwindCSS, hosted on Vercel.
- **Backend:** FastAPI, hosted on Railway, single-service deployment (no confirmed horizontal replica scaling in place today).
- **Database:** PostgreSQL via Supabase, accessed through Supabase's transaction-mode connection pooler (port 6543), using the `postgres` role.
- **Background jobs:** Daily Picks generation triggered via GitHub Actions cron (`20:30 UTC` / `12:30 UTC`), running as a long-lived in-process job on the Railway backend, with in-memory job-status tracking (`last_trigger_received_at`, per-market `generating`/`job_status` flags — confirmed live in `services/daily_picks.py`).
- **Caching:** an in-process prediction cache (`_pred_cache`, ~15-minute TTL, confirmed shared between the live `/predict` route and Daily Picks' own direct-cache-read path — see the RCI cache-mutation-risk finding in [Recommendation Consolidation Live Stock Analysis Integration Readiness](Recommendation-Consolidation-Live-Stock-Analysis-Integration-Readiness.md)); a separate ~4-hour cache for India's screener.in fetch.
- **Model artifacts:** trained `meta_model` `.pkl` files currently live on local disk on the Railway container — **do not survive a redeploy and would not be shared across horizontally-scaled replicas** (a confirmed, named architectural constraint, SSDS-000, ROADMAP item 5.3 — directly relevant to Section 8's horizontal-scaling discussion below).
- **Intelligence Engine V1** (`backend/services/intelligence_engine/`) runs its shadow evaluation on a background daemon thread inside the same request/job lifecycle as Daily Picks generation — not a separate worker process or queue.

This review finds **no dedicated caching layer (Redis/Memcached), no message queue, no worker pool, and no CDN-level edge caching configuration beyond what Vercel provides by default** — all confirmed by their absence from the architecture diagram and dependency manifests, not assumed. Any scalability plan (Section 14) must treat these as real, current gaps, not oversights to casually dismiss.

## 4. Frontend Performance Audit

**Confirmed facts today:**
- No frontend test framework exists (`package.json` confirmed to have no test runner/script — a repeatedly-confirmed finding across multiple prior sprints, e.g. Sprint #012 RCI Frontend Implementation).
- No `next lint`/ESLint configuration exists today (confirmed broken, unrelated to any specific sprint).
- No confirmed frontend performance-monitoring tooling (no Web Vitals reporting, no Lighthouse CI, no bundle-analyzer configuration confirmed present).

**Audit scope (to be executed in Phase 0, Section 17 — not performed by this document itself):**
- Lighthouse/Web Vitals baseline for Daily Picks, Stock Detail, Portfolio, and Paper Trading pages, both desktop and throttled-mobile profiles.
- Bundle-size analysis per route (Next.js's own build output already reports per-route JS size — a baseline snapshot of current `next build` output should be captured first, since it costs nothing to gather).
- Client-side data-fetching audit: identify any waterfalling `@tanstack/react-query` calls that could be parallelized, and any component re-render inefficiencies in high-frequency-update components (e.g. `LiveClock.tsx`, `MarketStatusBar`).
- Image and static-asset optimization review (Next.js `<Image>` usage consistency).
- Third-party script audit (any analytics/tracking scripts and their load-blocking behavior).

**This document does not claim any specific frontend defect exists** beyond the confirmed absences above — every other frontend finding must come from the Phase 0 audit's real measurement, not assumption.

**Update — Portfolio page audit now complete (no longer purely Phase 0 scope for this one page).** A dedicated forensic audit of the Portfolio page has since been performed: see [Portfolio Page — Forensic Performance & UX Audit](Portfolio-Page-Forensic-Performance-Audit.md). Confirmed findings include an unmemoized per-render P&L/allocation/sort recalculation, a per-holding full-Prediction-Engine fetch used only for a signal badge, and redundant per-request DDL statements on the backend (`_ensure_table()` re-run on every `GET`). These are folded into this sprint's Phase 1 prioritization (Section 17) alongside the rest of Section 4's Phase 0 findings, rather than tracked as a separate workstream.

**Update — Daily Picks page audit now complete.** A dedicated forensic audit of the Daily Picks page (`/picks`) has since been performed: see [Daily Picks Page — Forensic Performance & UX Audit](Daily-Picks-Page-Forensic-Performance-Audit.md). Confirmed findings include no concurrency stagger on per-card live-quote fetches (the same pattern Portfolio already solved via `useStaggeredQueries`), a confusing three-way "how many stocks" wording (Top 6 / screened-from-N / n_buy-of-n_scored), and — the audit's single lowest-risk, highest-confidence finding — `/api/picks/status` already computes rich universe-quality telemetry (`universe_used`, `universe_degraded`, exclusion reasons) that the page never fetches or displays at all. These are likewise folded into this sprint's Phase 1 prioritization rather than tracked as a separate workstream.

## 5. Backend Performance Audit

**Confirmed facts today (from prior sprints, not re-derived here):**
- JWT verification cost measured at ~0.075ms; rate-limiter key lookup at ~0.0006ms (Security Closure Sprint) — both negligible, already validated, not a target for this sprint.
- RCI composer overhead measured at 0.1–0.41ms against a 3-4s cold-fetch baseline (Sprint #008 RCI Live Stock Analysis) — the cold-fetch cost is the real bottleneck, not RCI's own logic.
- Growth Intelligence, Valuation Intelligence, and Financial Strength engine/adapter overhead all separately measured as sub-millisecond to low-single-digit-millisecond, with the dominant real cost being the underlying yfinance/screener.in fetch, not engine computation — a consistent pattern across every prior integration sprint.

**Audit scope (Phase 0):**
- End-to-end request tracing for `/api/predictions/*` (cold and warm) to confirm the cold-fetch-dominated latency pattern still holds at current scale.
- Profiling of `generate_picks()`'s full execution path (Section 10) — the one area with a known, large, currently-undocumented-in-detail duration (tens of minutes per market).
- Async/concurrency review: confirm all provider fetches that can run concurrently (`asyncio.gather`, per `prediction_engine.py`'s existing Round-2 pattern) actually do, and identify any newly-introduced sequential bottleneck since the last such review.
- Memory-profile of the long-running Daily Picks job, given it runs as a single long-lived Railway process rather than a bounded worker task.

## 6. Database Optimization

**Confirmed facts today:**
- Connected via Supabase's transaction-mode pooler (port 6543) — appropriate for short-lived transactional queries; long-running or session-state-dependent queries would need the session-mode pooler instead (not confirmed to be needed anywhere today, but worth explicit confirmation in Phase 0).
- `intelligence_engine_shadow_runs` and related Intelligence Engine tables use plain `INSERT` with no `ON CONFLICT`/upsert (a deliberate, already-validated design choice — not a performance concern, since these are periodic, deduplicated-by-application-logic writes, not a hot path).
- No confirmed index audit exists for the `paper_trades`, `paper_portfolio`, `portfolio_holdings`, `watchlist`, or `intelligence_engine_shadow_runs` tables.

**Planned optimization scope:**
- `EXPLAIN ANALYZE` review of the actual hot-path queries behind `/api/picks/status`, `/api/predictions/debug/state`, and any Paper Trading list/history endpoints, once real query plans are captured (Phase 0).
- Confirm appropriate indexes exist on `(market, run_at)` for the Intelligence Engine shadow-run table's own `ORDER BY run_at DESC LIMIT 1` query pattern (already known from `telemetry.py`'s `get_latest_shadow_run`) — a likely-cheap, high-confidence win once measured, not yet confirmed necessary.
- Connection-pool sizing review against Railway's and Supabase's current plan limits, particularly during a Daily Picks generation window where a long-lived job may hold connections open longer than typical request-scoped queries.
- No schema migration, index addition, or connection-pool configuration change is authorized by this document — all require their own reviewed, tested migration in a future implementation sprint.

## 7. API Optimization

- Confirm every read-only, additive endpoint added by recent Intelligence Engine and RCI work (`/api/picks/intelligence-shadow`, `/predict`'s RCI-augmented response) degrades gracefully and cheaply on failure, per their own already-documented contracts — no new work needed here beyond a periodic confirmation check.
- Response-payload size review: confirm no endpoint is over-fetching (e.g., returning full engine internals when a consumer only needs a summary), following the same discipline that led to RCI's own composer being a dedicated, minimal response shape rather than a raw internal dump.
- HTTP caching headers (`Cache-Control`, `ETag`) review for genuinely cacheable, non-personalized endpoints (e.g., Daily Picks' own published results once generated) — not confirmed to exist today.
- Rate-limiting review: confirm the existing per-IP rate limiter (`services/rate_limit.py`, already fixed for `X-Forwarded-For` correctness in the Security Closure Sprint) is tuned appropriately for expected legitimate burst traffic (e.g., a user rapidly switching between Stock Detail pages) without being loosened in a way that reopens any prior security finding.

## 8. Infrastructure Optimization

- **Railway (backend):** confirm current instance sizing (CPU/memory) against real Daily Picks generation resource usage (Section 5's memory-profile task); evaluate whether a dedicated, separately-sized worker process for Daily Picks generation (rather than running it in-process alongside request handling) would reduce contention — a real architectural option, not a foregone conclusion, to be evaluated with real resource-usage data, not assumed necessary.
- **Vercel (frontend):** confirm build/deploy configuration uses appropriate caching (ISR/static generation where applicable) versus fully dynamic rendering for pages that don't need per-request personalization.
- **Supabase (database):** confirm current plan tier's connection-limit headroom against Section 6's pool-sizing findings.
- **Model artifact storage:** the confirmed local-disk `.pkl` storage gap (Section 3) is the single clearest infrastructure-level scalability blocker on record — it blocks both horizontal replica scaling and safe redeploys mid-training-cycle. This sprint should produce a concrete migration plan (e.g., to Supabase Storage or another object store) as a deliverable, even if the migration itself is scheduled as a later implementation sprint.
- No infrastructure change (instance resize, service split, storage migration) is authorized by this document.

## 9. Background Jobs

- **Current state:** Daily Picks generation is the only significant background job, triggered by GitHub Actions cron and executed synchronously (from the triggering request's perspective) inside the Railway backend process, with in-memory (not persisted-queue-based) job-status tracking.
- **Known risk, already documented operationally:** a Railway redeploy during an in-progress Daily Picks job can kill it mid-run (a risk this session has itself worked around multiple times by deferring commits/pushes until a job completes) — this is a real, already-experienced operational cost of the current in-process job design, not a hypothetical one.
- **Planned evaluation:** whether a dedicated job queue (e.g., a lightweight Postgres-backed queue, given no Redis/task-queue infrastructure exists today) would remove the "don't deploy while a job is running" operational constraint, versus the cost/complexity of introducing new infrastructure. This is an architecture decision requiring its own evidence-gathering (Phase 0/1), not a foregone recommendation.
- Intelligence Engine's shadow-run daemon thread (Section 3) should be re-evaluated under the same lens once Daily Picks' own job model is decided, since it currently piggybacks on the same process lifecycle.

## 10. Daily Picks Optimization

- **Known baseline (from this session's own direct observation):** India generation has taken up to ~80 minutes; US generation has taken roughly 10–20 minutes in recent controlled triggers. No systematic, repeated-measurement baseline exists beyond these individual observed runs — Phase 0 should capture a proper distribution (multiple runs, both markets) rather than relying on anecdotal single-run timings.
- **Known past fix already delivered:** `_get_universe_by_mcap()`'s `count=1000` parameter was found and fixed to `count=250` after discovering Yahoo's `yf.screen()` hard-rejects `count > 250`, which had been silently falling back to the full unfiltered universe on every run — a confirmed prior contributor to abnormally long runtimes (Product Integrity Workstream #002A). This sprint should verify that fix's measured effect on total job duration wasn't yet formally captured, and capture it now as part of Phase 0.
- **Candidate optimization areas (not yet validated):** batch-size and concurrency tuning for per-symbol screening/prediction calls; whether India's screener.in fetch and US's yfinance fetch could be further parallelized across symbols within already-established per-ticker error isolation (Product Integrity Workstream #002A confirmed per-ticker isolation already works correctly — this is about throughput, not correctness); whether the existing 4-hour screener.in cache window could be extended or made incremental to avoid re-fetching unchanged data on back-to-back runs.
- Any change here must preserve the already-validated freshness-guard, per-ticker isolation, and crash-handler-writes-a-record behaviors — this sprint optimizes speed, it does not change Daily Picks' correctness contracts.

## 11. Paper Trading Optimization

- **Current state:** Paper Trading (`backend/api/routers/paper_trading.py`) is implemented and live; no confirmed performance issue has been reported or measured against it in any prior sprint.
- **Planned scope:** a query-pattern review of trade-history/position-listing endpoints (likely to grow linearly with user activity — worth an index/pagination review before it becomes a problem, per this sprint's forward-looking scalability mandate), and confirmation that Paper Trading's own known, previously-deferred timestamp-formatting issue (the "same-pattern, date-only instances" named but explicitly deferred in Product Integrity Workstream #001) is either addressed here or explicitly re-deferred with a reason.
- No change to trade execution, simulated-fill logic, or notification triggers is in scope for this sprint — this is a read/query-performance and pagination review only.

## 12. Intelligence Engine Optimization

- **Current state:** Intelligence Engine V1's shadow evaluation runs the Instrument Type Gate over the full static universe (2,400+ India symbols) synchronously on a background daemon thread once per Daily Picks generation — already measured as fast relative to the overall job (see Current-Release-Status.md's Phase 3A telemetry: `raw_count: 2402`, completed without incident).
- **Planned scope:** confirm the shadow-run's daemon-thread execution doesn't contend for CPU/memory with the main generation job during Section 9's job-model evaluation; evaluate whether Phase 3B-B's known coverage limitation (only the ~18 selected picks get real market-data-driven gates, not the full candidate pool — a disclosed limitation, not a performance issue per se) has a performance-motivated path to wider coverage now that `candidate_data.py`'s derivation pattern is proven, or whether that remains a data-availability question rather than a performance one.
- No change to the Intelligence Engine's shadow-only, zero-production-impact design guarantee (Current-Release-Status.md) is authorized by this document.

## 13. Mobile Optimization

- **Confirmed gap:** no mobile-specific performance measurement, responsive-layout audit, or mobile Core Web Vitals baseline exists today for this platform, across any prior sprint on record.
- **Planned scope (Phase 0):** a dedicated mobile-viewport audit (throttled network + CPU profile, per Section 2's success metrics) across the same core pages as the frontend audit (Section 4); a touch-target and layout-overflow review distinct from a pure performance audit, since UX and performance are both named in this sprint's own title.
- This is a net-new audit area for this codebase — every finding here should be treated as a first-time baseline, not a regression check against a prior measurement.

## 14. Scalability Plan

- **Horizontal scaling blocker (confirmed, Section 3/8):** local-disk model-artifact storage must be resolved before the backend can safely run more than one Railway replica. This is the single highest-priority scalability item this sprint should produce a concrete plan for.
- **Stateful in-memory job tracking:** Daily Picks' `generating`/`job_status`/`last_trigger_received_at` state is currently in-process memory (confirmed via `services/daily_picks.py`) — this would not survive or coordinate correctly across multiple replicas without a shared store (Postgres-backed, given no Redis exists today). A concrete migration plan (not implementation) should be produced.
- **Prediction cache (`_pred_cache`):** also in-process memory, shared today only because Daily Picks and the live `/predict` route run in the same process — a future multi-replica architecture would need either a shared cache (Postgres- or Redis-backed) or an explicit acceptance that each replica maintains its own independent cache with correspondingly lower hit rates. Both options should be named with tradeoffs, not silently assumed away.
- **Database connection scaling:** revisit Section 6's pool-sizing findings under a multi-replica assumption, since each replica would open its own pool against the same Supabase pooler limit.
- This section is a plan, not a decision — the actual choice of when/whether to scale horizontally depends on real load data this sprint should also help establish (Section 15).

## 15. Monitoring & Observability

- **Confirmed gap:** no APM (Application Performance Monitoring) tool, no structured metrics dashboard, and no alerting system beyond the existing `/api/predictions/debug/state` aggregate-counters endpoint (Release 14B) is confirmed to exist today.
- **Planned scope:** evaluate a lightweight, low-cost observability addition (e.g., Railway's own built-in metrics, or a free/low-tier APM) sufficient to answer "is the backend healthy right now" and "how long did the last Daily Picks run actually take" without requiring a full enterprise observability stack — proportionate to this platform's current scale, not over-engineered.
- Extend the existing Debug Endpoint (Release 14B, already hardened and access-controlled) with additional aggregate-only performance counters (e.g., last N request latencies, last Daily Picks job duration) rather than building a second, parallel monitoring surface — reusing established, already-security-reviewed infrastructure.
- Any new monitoring surface must follow Release 14B's own precedent: aggregate-only, no per-user/per-symbol identifiable data, secret-protected if it exposes anything beyond what's already public.

## 16. Performance Testing

- **Confirmed gap:** no load-testing tooling or process exists today (no k6/Locust/JMeter configuration found in the repository).
- **Planned scope:** introduce a minimal, repeatable load-testing script (tool choice to be decided in Phase 1) targeting the confirmed hot paths (`/api/predictions/*`, `/api/picks/status`) at a traffic level proportionate to this platform's real current usage, not an arbitrary large number chosen without justification.
- Daily Picks generation itself is not a candidate for traditional load testing (it's a scheduled batch job, not a request-rate-scaling concern) — its performance testing is the repeated-run timing baseline described in Section 10, a different kind of measurement.
- Frontend performance testing should integrate Lighthouse CI (or equivalent) into the existing GitHub Actions workflow, given GitHub Actions is already the platform's established CI mechanism (Daily Picks' own cron trigger already lives there).

## 17. Rollout Plan

No phase below is authorized by this document; each requires its own separate review and approval, per this codebase's established phased-delivery convention.

- **Phase 0 — Baseline Measurement (no code changes).** Execute every "Phase 0" audit task named in Sections 4–6, 10, and 13 above, against the real, current, unmodified production and local environments. Produce a single baseline report with real numbers for every "documented baseline first" row in Section 2's success-metrics table. This phase is measurement-only — read-only API calls, Lighthouse runs, `EXPLAIN ANALYZE` queries, and log/telemetry review — no code or configuration change.
- **Phase 1 — Design Study for Prioritized Fixes.** Using Phase 0's real numbers, rank candidate optimizations by (measured impact × implementation cost), the same prioritization discipline this codebase's prior epic-selection decisions used (e.g. `StockSense360-Next-Intelligence-Epic-Decision.md`). Produce a scoped implementation plan for the top-ranked items only — not an attempt to fix everything in this document at once.
- **Phase 2 — Low-Risk, High-Confidence Fixes.** Implement the fixes Phase 1 identifies as low-risk (e.g., missing database indexes, HTTP caching headers, obvious frontend bundle-size wins) behind the platform's established validation discipline (before/after measurement, full regression suite, no scope creep).
- **Phase 3 — Structural Changes (job model, model-artifact storage, monitoring).** Any change touching Daily Picks' job architecture, model-artifact storage location, or new infrastructure (queue, cache, APM) is scoped as its own dedicated implementation sprint, given its higher risk and cross-cutting blast radius — not bundled into Phase 2.
- **Phase 4 — Load Testing and Scalability Validation.** Only after Phases 2–3 land, run the load-testing tooling from Section 16 against the improved system to confirm the scalability plan's (Section 14) assumptions hold under real, measured load — not assumed correct from the design alone.

## 18. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Optimizing based on assumption rather than Phase 0's real data, repeating a mistake this codebase has explicitly avoided elsewhere (e.g. Sprint #008 RCI's own transparency note about a validation-script bug that first looked like a real ranking regression) | Medium if Phase 0 is skipped | High — wasted effort, possible regression | Hard gate: no Phase 2+ work begins without a Phase 0 baseline number backing it, mirroring this codebase's evidence-over-assertion standard (SES-001) |
| A Daily Picks job-model change (Section 9/14) introduces a new failure mode in the freshness guard, crash-handler, or per-ticker isolation behaviors already hard-won across multiple Product Integrity Workstreams | Medium | High — would regress already-validated correctness guarantees | Any Phase 3 job-model change must include a dedicated regression suite proving every existing Daily Picks correctness guarantee (freshness guard, crash-handler-always-writes, per-ticker isolation) still holds, before any performance claim is made |
| Introducing new infrastructure (queue, cache, APM) that this platform's current scale doesn't yet justify, adding operational complexity without a corresponding measured benefit | Medium | Medium | Phase 1's own cost/impact ranking must explicitly weigh "do nothing" as a valid outcome for any given candidate, not assume infrastructure addition is always the answer |
| Horizontal-scaling changes (Section 14) shipped before the model-artifact storage and in-memory-state blockers are actually resolved | Low if sequencing in Section 17 is followed | High — would produce a broken multi-replica deployment | Explicit dependency ordering: no replica-count increase is authorized before Phase 3's storage/state migration is validated |
| Mobile audit (Section 13) surfaces a large number of net-new findings with no prior baseline to compare against, risking scope creep into a much larger UX overhaul than this sprint intends | Medium | Medium | Phase 1's prioritization applies equally to mobile findings — not every finding becomes a Phase 2 fix in this sprint |

## 19. Future Enhancements

- A dedicated, always-on job-queue architecture for Daily Picks and any future batch workloads (e.g., a bulk historical backtest run), once Section 9's evaluation concludes it's justified by real scale.
- Edge-caching or CDN-level optimization for genuinely public, non-personalized data (e.g., published Daily Picks results) once Section 7's HTTP-caching-header work establishes which responses are safely cacheable.
- A full APM/observability platform (beyond Section 15's proportionate near-term addition) if and when real usage scale justifies the added cost and complexity.
- Extending Section 16's load-testing practice into a continuous, scheduled performance-regression check (not just an ad hoc pre-release run), once a stable baseline and CI integration exist.
- Revisiting the Intelligence Engine's Phase 3B-B coverage limitation (Section 12) for full candidate-pool visibility, if a future data-availability change (not a performance change) makes that data retrievable without a production function contract change.

## 20. Sequenced Follow-On — Portfolio Daily Performance & Attribution Intelligence

**Status: Planned / Not Started. Documentation only.** A major future Portfolio Copilot (Epic 007) capability — daily/weekly/monthly/YTD portfolio and per-holding performance, attribution by sector/industry/country/market-cap, a portfolio-level "why did this move today?" rollup of the Stock Movement Explanation Engine, a Portfolio Health Dashboard (risk/diversification/quality/growth/valuation/momentum scores), and a persisted historical daily-snapshot timeline — is fully specified in [Portfolio Page — Forensic Performance & UX Audit §10](Portfolio-Page-Forensic-Performance-Audit.md#10-future-capability--portfolio-daily-performance--attribution-intelligence).

**Explicitly sequenced as a Phase 2 enhancement after this sprint**, not folded into Sprint 011's own Phase 2/3 (Section 17): several of Section 20's prerequisites are direct outputs of this sprint's own Phase 0/1/2 work — a memoized, efficient render path (Section 4's Portfolio findings) and a lightweight signal-only fetch path (avoiding the full Prediction Engine call per holding) should land first, so the future daily-performance/attribution feature is built on an already-optimized foundation rather than duplicating this sprint's own fixes. This sprint does not implement any part of Section 20; it only establishes the performance baseline that feature depends on.

### 20.1 Portfolio Performance Strategy

**Documentation only — no caching layer, endpoint, or schema described below is implemented.** This subsection lays out, in more detail than Section 20's summary, the specific caching/refresh architecture the future Portfolio Daily Performance & Attribution Intelligence capability (and today's existing Portfolio Tracker) should follow, so a future implementation sprint has a concrete design to build against rather than re-deriving one.

- **Client-side caching.** Continue using the existing global `QueryClient` defaults (`frontend/src/app/providers.tsx:57-68` — `staleTime: 60_000`, `gcTime: 30 * 60_000`, `refetchOnWindowFocus: false`) as the floor; quote/prediction queries keep their existing per-query `staleTime` overrides (5min/15min). A future portfolio-analysis query (health/risk/attribution) should get its own long `staleTime` (proposed: aligned to the daily-snapshot cadence, e.g. until the next scheduled background refresh) rather than inheriting the 60s floor, since it changes at most once per trading day, not per request.
- **Server-side caching.** Portfolio-level analysis (health/risk/attribution scores, Section 10.5 of the audit) should be computed once per user per day and persisted (Section 20.1's "Historical snapshot architecture" below), not recomputed synchronously on every API request — the same in-memory-TTL-cache pattern already used for quotes (60s) and predictions (15min) is the wrong shape for a computation this expensive; a persisted daily row is the correct cache, not a short-TTL in-memory dict.
- **Background analysis.** The daily portfolio-analysis computation should run as a background job (mirroring Daily Picks' own cron-triggered, backend-executed model, Section 9 above) rather than inline in a request-response cycle — triggered once per day per active user (or lazily, on first Portfolio-page visit of the day, whichever the future Design Study determines is more efficient given expected active-user counts).
- **Manual "Refresh Analysis."** A user-triggered action that forces an immediate recomputation and re-snapshot, bypassing the daily cadence — the only case where full analysis recomputation happens synchronously in the request path, and only because the user explicitly asked for it.
- **Live quote refresh.** Unchanged from today's existing, already-sound behavior (Section 1 of the linked audit) — quotes refresh quietly via the existing 60s server-side / 5min client-side cache, independent of whether the heavier daily analysis has been recomputed.
- **React Query optimization.** Consolidate the Portfolio page's two independent holdings-fetch code paths (audit Section 2) into one `useQuery`-backed hook with `invalidateQueries` on mutation, replacing the current manual `useState`+`axios` pattern for holdings CRUD — bringing holdings fetching in line with how quotes/predictions are already fetched.
- **Memoization strategy.** Wrap the per-render P&L/allocation/sort calculations (audit Section 3(b)) in `useMemo`, keyed on `holdings` + quote/prediction query data + sort state — the direct fix for the confirmed unmemoized-recalculation finding.
- **API optimization — lightweight signal endpoint.** Add a new, additive, read-only endpoint (or a query-param addition to the existing `/api/stocks/quote/{symbol}`) returning only `{signal, confidence}` sourced from the existing prediction cache, rather than requiring Portfolio to invoke the full `/api/predictions/{symbol}` multi-engine payload per holding (audit Section 3(a)) — must not change `/api/predictions/{symbol}`'s existing contract, purely additive.
- **Historical snapshot architecture.** A new `portfolio_daily_snapshots` table (proposed shape: `user_id`, `snapshot_date`, `portfolio_value`, `daily_return`, `daily_pct`, `cumulative_return`, `allocation` (JSON), `sector_allocation` (JSON), `cash`, `top_contributors` (JSON), `worst_contributors` (JSON), `health_score`, `risk_score`), indexed on `(user_id, snapshot_date)` mirroring `portfolio_holdings`'s existing `user_id`-indexed pattern (audit Section 3, no composite index currently needed there but would be needed here for range queries). This table is the single foundation Section 20's daily/weekly/monthly/yearly/custom-range historical timeline depends on — no other part of Section 20 is buildable without it existing first.

## 21. Sequenced Follow-On — Daily Picks Selection Funnel, Research Analyst, History, and Universe Transparency

**Status: Planned / Not Started. Documentation only.** A set of future Daily Picks capabilities — a unified Selection Funnel view, a Daily-Picks-specific application of the future Research Analyst, a yesterday-comparison diff, a daily Timeline/Diary, a page-level Daily Market Intelligence Summary, and full Universe Transparency — is fully specified in [Daily Picks Page — Forensic Performance & UX Audit §12-§17](Daily-Picks-Page-Forensic-Performance-Audit.md#12-future-capability--daily-picks-selection-funnel).

**Universe Transparency (§17 of that audit) is explicitly named there as the single lowest-risk, highest-confidence item of the six** — `/api/picks/status` already computes every field it needs (`universe_used`, `universe_degraded`, exclusion reasons); no new backend logic is required, only a new frontend fetch and a small UI addition. This sprint's own Phase 1 (Section 17 above) should absorb that one item directly, ahead of the rest of §12-§16, which involve either a shared daily-snapshot architecture (§14, dependent on this sprint's own §20.1 snapshot design) or dependency on Epic 008's Research Analyst reaching a usable phase (§13). None of §12-§17 is implemented or authorized by this document or the linked audit.
