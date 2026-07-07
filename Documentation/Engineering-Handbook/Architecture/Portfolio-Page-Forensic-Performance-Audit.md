# Portfolio Page — Forensic Performance & UX Audit

## 0. Status and Purpose

**Status: Audit complete for the current, live Portfolio Tracker feature. This document also specifies a future capability (Section 10) that does not exist yet.** No production code was modified to produce this audit — every finding below is from direct source-code inspection (full-file reads of the frontend page, its hooks/utils, the backend router, and the database schema) plus read-only `GET` checks against the live production endpoint. Nothing in Section 10 is implemented; it is a functional specification for future work, following this codebase's established documentation-before-implementation discipline (see [EPIC-008](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md), the [Stock Movement Explanation Engine spec](Research-Analyst-Stock-Movement-Explanation-Spec.md), and the [Sprint 011 spec](Performance-Scalability-UX-Sprint-011-Spec.md) for precedent).

**Scope of the audit (Sections 1-9):** the live Portfolio Tracker at `stocksense360.com/portfolio` — `frontend/src/app/portfolio/page.tsx`, its supporting hooks/utils/components, `backend/api/routers/portfolio.py`, and the `portfolio_holdings` table.

**Scope of the specification (Section 10):** a future "Portfolio Daily Performance & Attribution Intelligence" capability — the eventual home for daily P&L, attribution, health scoring, and historical timeline features, as a major component of the future **Portfolio Copilot** (Epic 007).

## 1. What Works Perfectly

- **App-wide React Query cache config is already sound.** `frontend/src/app/providers.tsx:57-68` sets a single global `QueryClient` with `staleTime: 60_000`, `gcTime: 30 * 60_000`, `refetchOnWindowFocus: false`, `refetchOnReconnect: false`. Quote (`staleTime: 5min`) and prediction (`staleTime: 15min`) queries inherit this — tab-switching does **not** trigger a refetch storm, and cached data survives 30 minutes across navigation.
- **`useStaggeredQueries`** (`frontend/src/hooks/useStaggeredQueries.ts`) is a deliberate, documented fix for a real browser per-origin connection-limit problem, throttling concurrency to 8 and unlocking more as batches settle, backed by real load-test evidence in its own comment ("25 concurrent prediction requests completed server-side in ~2.5s").
- **`fetchPrediction`'s adaptive polling** (`frontend/src/utils/api.ts:179-210`) polls faster (2s) for the first 4 attempts then falls back to server-suggested pacing — a deliberate improvement over naive fixed-interval polling.
- **`portfolio_holdings` schema** already has the correct hot-path index (`idx_portfolio_holdings_user` on `user_id`) and RLS enabled, created idempotently.
- **Auth/ownership enforcement** (`require_owner`) and per-user rate limiting are correctly wired on every portfolio endpoint.
- **Await-before-mutate discipline** on delete/edit (`page.tsx:334-350`) — the backend call is awaited before local state changes, explicitly avoiding a stale-revert bug class already fixed once in Alerts.

## 2. What Is Partially Working

- **Loading UX is functional but not polished** — per-cell "…" pulse placeholders work, `useStaggeredQueries` correctly keeps queued rows showing as loading, but there is no page-level skeleton; a large portfolio visibly loads top-to-bottom in batches of 8.
- **Holdings list fetch has two independent code paths** (mount `useEffect`, `page.tsx:251-274`; and `refetchHoldings`, `276-281`) doing the same `GET /api/portfolio/{userId}`, instead of one shared query with `invalidateQueries`.
- **Bulk import** (`import_holdings`, `portfolio.py:122-180`) is efficient for today's realistic import sizes but not batched at the SQL level.

## 3. What Is Broken or Inefficient

**(a) Full Prediction Engine invoked per holding just to show a signal badge.** `page.tsx:297-308` calls `fetchPrediction(symbol, market, "medium")` → `GET /api/predictions/{symbol}`, the same heavy, multi-engine endpoint the Stock Detail page uses, purely to extract `signal`+`confidence` for a small badge. Confirmed live: `GET /api/predictions/AAPL?market=US&horizon=medium` returned HTTP 202 (still computing) on a cold hit.

**(b) Unmemoized recalculation on every render.** `rows` (`page.tsx:358-373`) and `HoldingsTable`'s `sortedRows` (`184-195`) recompute on every render with no `useMemo`. Since `sym`/`qty`/`avgPrice` form inputs are sibling `useState` in the same component (`240-243`), typing a character in the Add-Holding form re-runs the full P&L/allocation/sort computation for every row.

**(c) N+1 DB writes in bulk import.** `import_holdings` (`portfolio.py:144-176`) issues one `UPDATE`/`INSERT`/`DELETE` per imported row inside a Python loop, not batched (`executemany`/multi-row `VALUES`).

**(d) Sequential migration path.** The one-time localStorage→Postgres migration (`page.tsx:263-269`) awaits one holding at a time in a `for` loop — N sequential round-trips.

**(e) Redundant per-request DDL statements.** Every `GET /api/portfolio/{user_id}` re-issues `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` + `ALTER TABLE ... ENABLE ROW LEVEL SECURITY` (`portfolio.py:89`, `_ensure_table()`) before the real `SELECT` — three no-op DDL round-trips per read, not just at startup.

**(f) Horizon hardcoded to `"medium"`.** `page.tsx:299` hardcodes the prediction horizon, so the Portfolio page's cached prediction for a symbol is never shared with that same symbol's Stock Detail page if the user has a different horizon selected there — a deliberate product simplification, not necessarily a bug, but worth naming.

**(g) No portfolio-level analysis exists at all today.** There is no risk score, diversification score, sector exposure, or day-over-day P&L anywhere in the current feature — confirmed by full-file review. The page computes only: current price, invested/current value, cumulative P&L amount/%, and allocation-by-value. This is the gap Section 10 specifies filling.

## 4. Exact Files/Components Involved

| Layer | File | Role |
|---|---|---|
| Frontend page | `frontend/src/app/portfolio/page.tsx` (576 lines) | All fetching, calculation, rendering |
| Frontend hook | `frontend/src/hooks/useStaggeredQueries.ts` | Concurrency throttling for quote/prediction fan-out |
| Frontend util | `frontend/src/utils/api.ts` (`fetchQuote` L171-172, `fetchPrediction` L179-210) | HTTP calls |
| Frontend component | `frontend/src/components/PortfolioAllocationChart.tsx` | Allocation % (unmemoized, computed twice per render) |
| Frontend component | `frontend/src/components/ImportPortfolioModal.tsx` | Bulk import UI |
| Frontend provider | `frontend/src/app/providers.tsx` | Global QueryClient defaults |
| Backend router | `backend/api/routers/portfolio.py` (211 lines) | CRUD only — no valuation/scoring logic |
| Backend router | `backend/api/routers/stocks.py` (`GET /quote/{symbol}`) | Live quote, 60s in-memory TTL cache |
| Backend router | `backend/api/routers/predictions.py` | Full Prediction Engine, 15-min in-memory TTL cache |
| Database | `portfolio_holdings` table (defined inline in `portfolio.py:28-46`) | Holdings only — no cached valuation |

## 5. API Calls and Timings

| Call | Trigger | Confirmed timing | Cache |
|---|---|---|---|
| `GET /api/portfolio/{userId}` | mount + manual refetch | not load-tested (small payload) | none server-side |
| `GET /api/stocks/quote/{symbol}` ×N holdings | mount, staggered 8-at-a-time | live: 5.8s cold / 0.5s warm | 60s in-memory (server) |
| `GET /api/predictions/{symbol}` ×N holdings | mount, staggered 8-at-a-time | live: 202 (computing) on cold hit; prior sprint evidence: 3-4s cold, sub-ms warm | 15-min in-memory (server) |
| `POST/PATCH/DELETE /api/portfolio/{userId}[/{id}]` | user action | not measured (simple single-row writes) | n/a |
| `POST /api/portfolio/{userId}/import` | user action | not measured; N sequential DB statements | n/a |

## 6. Root Causes

1. **Over-fetching for under-use** — `/api/predictions/{symbol}` returns a full multi-engine payload; Portfolio needs only `signal`+`confidence`.
2. **No memoization discipline** in `page.tsx` — calculation logic was written inline in the render body.
3. **No portfolio-level backend computation at all** — `portfolio.py` was designed purely as a sync-across-devices CRUD store.
4. **Bulk-import endpoint written for correctness/mergeability first**, not batch-write performance.
5. **`_ensure_table()` called defensively on every request** rather than once at startup, a conservative but wasteful choice.

## 7. Performance Risks

- Cold-cache portfolio load scales linearly with holding count for both quote and prediction fetches.
- Unmemoized render-body calculations will start to matter once portfolios grow past a few dozen holdings.
- Bulk import's per-row DB loop will scale to seconds for very large broker-export files.

## 8. UX Risks

- No distinction today between "loading for the first time" and "recalculating" since there's no server-cached "last analyzed" concept.
- Staggered batch loading is honest but could look like a stall on very large, cold-cache portfolios.
- No dedicated empty/error states beyond a single "No holdings yet" block and inline row-level error text.

## 9. Recommended Fixes, Ranked by Priority

1. **High** — add a lightweight signal-only endpoint (or extend `/api/stocks/quote/{symbol}`) so Portfolio doesn't invoke the full multi-engine Prediction pipeline per holding just for a badge.
2. **High** — memoize `rows`/`sortedRows`/allocation calculations with `useMemo`.
3. **Medium** — batch the bulk-import writes (multi-row `INSERT ... VALUES`, bulk `UPDATE`, or `executemany`).
4. **Medium** — parallelize the one-time local→Postgres migration (`Promise.all` instead of sequential `await`).
5. **Low** — consolidate the two independent holdings-fetch code paths into one shared query/hook.
6. **Low** — guard `_ensure_table()` with an in-process boolean set once at startup instead of re-running on every GET.
7. **Future/design work, not a fix** — the risk/diversification/sector-exposure/daily-P&L capability described in Section 10 doesn't exist yet and needs its own phased build, reusing the Intelligence Engine's evidence primitives rather than being a new isolated calculation.

## 10. Future Capability — Portfolio Daily Performance & Attribution Intelligence

**Status: Planned / Not Started. Documentation only — no implementation, scoring, or UI has been built.** This is a major future capability of **Portfolio Copilot** (Epic 007, [`MASTER-ROADMAP.md`](../../MASTER-ROADMAP.md) Section 11), not a standalone feature. It directly addresses the gap named in Section 3(g) above and depends on Portfolio Copilot's own not-yet-built foundation (portfolio-aware context, per [EPIC-008 §3](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md)).

### 10.1 Daily Portfolio Performance

Planned summary metrics, in addition to the existing cumulative Invested/Current/P&L/P&L%:
- Today's Gain/Loss (Amount and %)
- Week-to-Date, Month-to-Date, Year-to-Date, Since Inception

**Prerequisite not yet satisfied:** none of these require new external data feasibility work (unlike Section 10.4's news-driven explanation), but they do require a **daily portfolio-value snapshot mechanism** (Section 10.6) that does not exist today — "Today's Gain/Loss" requires knowing yesterday's closing portfolio value, which is not currently persisted anywhere.

### 10.2 Stock-Level Daily Performance

Per holding, in addition to today's existing invested/current/P&L/P&L%/signal columns:
- Today's P/L (amount and %) — requires the prior trading day's close per holding, not just the live quote (a new data need vs. today's `fetchQuote`, which returns only current price)
- Lifetime P/L (amount and %) — already computable today as cumulative P&L, just needs relabeling for clarity once "Today's P/L" exists alongside it
- Daily contribution to portfolio, and portfolio weight — both derivable arithmetically from existing holding-level data once a prior-day baseline exists
- Today's volume — already available from the existing quote/OHLCV data path, not currently surfaced on this page
- **AI Confidence** — already computed today (`sig?.confidence`, `page.tsx:371`) and already passed into `SignalBadge`, but not currently surfaced as its own labeled column; a display-only addition, not a new calculation
- **"Why did this move today?" link to Research Analyst** — this is a direct UI integration point for the [Stock Movement Explanation Engine](Research-Analyst-Stock-Movement-Explanation-Spec.md), reusing that engine's existing planned API contract (Section 10 of that spec) rather than building a second explanation mechanism

### 10.3 Portfolio Attribution

Planned attribution views: best/worst performer, largest contributor/detractor, and contribution rollups by sector, industry, country, and market-cap bucket (mirroring the existing Heatmap's sector-grouping infrastructure, `backend/services/heatmap_service.py`, rather than inventing a second sector taxonomy — the same reuse principle already established for the Stock Movement Explanation Engine).

**Currency-mixing constraint carried over from existing product rules:** attribution totals must never mix ₹ and $ holdings into one number without explicit FX conversion — the current Portfolio page already enforces "never mix ₹ and $ into one number" (`page.tsx:354`) for invested/current totals, and this rule must extend unchanged to attribution rollups.

### 10.4 Daily Portfolio Intelligence

"Why did my portfolio move today?" as a **portfolio-level rollup of the Stock Movement Explanation Engine's per-holding output** (see [Research-Analyst-Stock-Movement-Explanation-Spec.md §14](Research-Analyst-Stock-Movement-Explanation-Spec.md), which already names this exact rollup as a future enhancement) — not a separately-built explanation engine. Must preserve that engine's confirmed/likely/unknown tiering discipline at the portfolio level, not collapse it into a single confident narrative.

### 10.5 Portfolio Health Dashboard

Planned future scores: Portfolio Health Score, Risk Score, Diversification Score, Concentration Risk, Quality Score, Growth Score, Valuation Score, Momentum Score, Expected CAGR, Worst/Best Case, Maximum Drawdown Risk, AI Conviction Score, and — for future dividend-focused investors — an **Income Score** (dividend yield, payout sustainability, and dividend-growth consistency aggregated across holdings; should read from Valuation Intelligence's existing Dividend Sustainability category, `payoutRatio`/`dividendYield` fields already confirmed live for both markets per SSDS-008/its India Feasibility Study, rather than a new dividend-data pipeline).

**Explicit reuse requirement, not a new scoring stack:** Quality Score, Growth Score, and Valuation Score must read from the already-implemented, already-validated Business Quality Engine, Growth Intelligence Engine, and Valuation Intelligence Engine outputs (aggregated across holdings), not recompute equivalent logic — the same "consume validated evidence rather than recreate it" rule [EPIC-008 §3](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md) already establishes for the Research Analyst. Risk Score and Concentration Risk are the direct subject of the not-yet-built Epic 007 Portfolio Intelligence scope ([`MASTER-ROADMAP.md` Section 11](../../MASTER-ROADMAP.md)) and must not be built as a duplicate, parallel scoring effort. Expected CAGR / Worst Case / Best Case / Maximum Drawdown Risk are forward-looking projections and must carry the same non-advisory disclaimer discipline as every other predictive surface in this codebase — never presented as a guarantee.

### 10.6 Historical Timeline

Planned daily snapshot persistence: portfolio value, daily return, **daily %**, cumulative return, allocation, sector allocation, cash, top/worst contributors, health score, risk score — queryable by daily/weekly/monthly/yearly/custom range.

**This is the foundational prerequisite for Sections 10.1–10.3** — none of "Today's Gain/Loss," attribution, or historical charting is computable without a persisted daily snapshot. A new table (e.g. `portfolio_daily_snapshots`, keyed on `(user_id, snapshot_date)`, mirroring `portfolio_holdings`'s existing `user_id`-indexed pattern) would need to be designed — no schema exists for this today, confirmed absent from `portfolio.py` and `postgres_store.py`.

### 10.7 Refresh Strategy

Recommended architecture, directly addressing this audit's Section 3(a)/3(g) findings and the [Sprint 011 spec's](Performance-Scalability-UX-Sprint-011-Spec.md) general refresh-strategy principle:

```
Open Portfolio
  -> instant load from cached portfolio analysis (last snapshot + last computed scores)
  -> refresh live quotes quietly (existing 60s server / 5min client cache, unchanged)
  -> update today's P/L immediately (arithmetic against the cached prior-day snapshot, cheap)
  -> refresh AI analysis (health/risk/attribution/explanation) only when:
       - user clicks "Refresh Analysis"
       - holdings actually change (add/remove/edit)
       - a scheduled background refresh runs (e.g. once daily, alongside Daily Picks' own cron)
```

This must **not** recompute the full portfolio-level analysis on every page visit — the same principle already established for the Stock Movement Explanation Engine (explanations are computed once per trading day, not per page load) applies here at the portfolio level too. A "Last analyzed at…" timestamp, displayed per the existing product convention (Daily Picks' own "Updated" timestamp label), should accompany any cached analysis.

### 10.8 Integration

- **Research Analyst (Epic 008):** consumes this feature's structured per-holding and portfolio-level evidence once Epic 008 reaches a phase that can accept portfolio context (008E, gated on Epic 007 per [EPIC-008 §11](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md)) — this feature does not itself implement conversational analysis.
- **Intelligence Engine:** Health/Quality/Growth/Valuation scores should read from Intelligence Engine and existing engine outputs (Section 10.5), not recompute them; the "why did this move" explanation (Section 10.4) rolls up the Stock Movement Explanation Engine's own planned home inside `backend/services/intelligence_engine/` (per that spec's §15.1).
- **Daily Picks:** read-only, one-directional only — this feature may reference whether a holding also appears in a recent Daily Pick (for narrative context), but must never write back into `daily_picks.py`'s payload, cache, or ranking, mirroring the same constraint already established in the Stock Movement Explanation Engine spec (§15.2).
- **Paper Trading:** a natural, later extension would let a user apply the same daily-performance/attribution view to a Paper Trading position's history — read-only, never influencing simulated fills or trade triggers, mirroring §15.4 of the Stock Movement Explanation Engine spec.
- **Portfolio Copilot:** this capability **is** the first major concrete scope item of the future Portfolio Copilot (Epic 007) — not a separate, competing initiative. Any future Epic 007 sprint plan should treat this document's Section 10 as an input to (not a replacement for) `MASTER-ROADMAP.md` Section 11's existing sprint plan.

### 10.9 Priority

**Phase 2 enhancement, sequenced after Sprint 011** ([`Performance-Scalability-UX-Sprint-011-Spec.md`](Performance-Scalability-UX-Sprint-011-Spec.md)). Rationale: Sprint 011's own Phase 0/1 baseline-and-prioritization work (frontend/backend/DB performance audit) should land first, since several of this section's dependencies (an efficient signal-only fetch path, memoized calculations, a resolved DDL-per-request inefficiency) are Sprint 011 deliverables this feature would otherwise have to duplicate or build on top of an unoptimized foundation. No phase of Section 10 is authorized by this document — each requires its own separate implementation-sprint approval, per this codebase's established phased-delivery convention.

## 11. Portfolio Copilot Vision

**Status: Planned / Not Started. Vision-level documentation only — none of the capabilities below have a design study, contract, or implementation yet.** This section names where the Portfolio page is ultimately headed, beyond Section 10's daily-performance/attribution scope, so future sprint planning has a stated destination rather than an open-ended series of unrelated additions.

The Portfolio page is expected to evolve into **Portfolio Copilot** — the same capability already named (and confirmed not to exist today) in `MASTER-ROADMAP.md` Section 2/Section 11 and referenced throughout this codebase's engine documentation (e.g. SSDS-003 §"Portfolio Copilot", SSDS-000 §"Portfolio Copilot — Future"). Future capabilities, none authorized or scoped by this document:

- **Portfolio Health Review** — a narrative synthesis of Section 10.5's Health/Risk/Diversification/Quality/Growth/Valuation scores, in the same evidence-tiered, non-advisory voice already established for the Stock Movement Explanation Engine and EPIC-008.
- **Portfolio Risk Review** — a deeper narrative treatment of concentration risk, correlation, and drawdown exposure than the raw scores in Section 10.5 alone convey.
- **AI Recommendations** — subject in full to EPIC-008 §5/§8's prohibited-response-class rules ("What should I buy today?", "Should I sell everything?" remain restricted response classes); any future recommendation surface must not promise returns or bypass those boundaries.
- **Suggested Rebalancing** — allocation-drift-aware suggestions against the user's own stated (not inferred) targets; explicitly not an auto-executing feature — any execution remains a distinct, separately-approved capability, never silently bundled with a suggestion.
- **Position Sizing Advice** — informational only, framed as "what a given sizing would imply," never as an instruction, consistent with EPIC-008 §12's "no action language stronger than the underlying validated evidence actually supports."
- **Diversification Analysis** — expands Section 10.5's Diversification Score into a full breakdown (by sector/industry/country/market-cap, reusing Section 10.3's attribution taxonomy rather than a second one).
- **Correlation Analysis** — a genuinely new data/compute capability not covered by any existing engine; would need its own feasibility study (historical price correlation matrix across holdings) before being scoped, mirroring this codebase's established practice of a feasibility study before full-scope coding (e.g. Epic 003's India Feasibility Study precedent).
- **Sector Rotation Suggestions** — reuses the existing Heatmap sector-performance infrastructure (`backend/services/heatmap_service.py`) and the Stock Movement Explanation Engine's sector-evidence category, rather than a new sector-timing model.
- **Better Investment Alternatives** — comparing a held position against other candidates using existing, already-validated engine outputs (Business Quality/Growth/Valuation/RCI) — never inventing a new comparative scoring method separate from those engines.
- **Portfolio Stress Testing** — scenario-based, explicitly labelled hypothetical per EPIC-008 §7's "scenario-based education, clearly labelled as hypothetical and not a prediction" permitted-response class.
- **What-if Scenarios** ("what if I sold X and bought Y") — same hypothetical-only framing as Stress Testing; must never be presented as a recommendation to actually take the action.
- **AI Chat for Portfolio** — this is, concretely, Epic 008's 008E phase ("Portfolio-Aware Research," [EPIC-008 §16](EPIC-008-AI-Research-Analyst-Concept-and-Safety-Specification.md#16-phased-future-delivery-model)) applied to the Portfolio page specifically — not a separate chat feature to be built outside Epic 008's own safety/evidence/evaluation-gate requirements. It inherits every restriction in EPIC-008 in full (no portfolio-awareness claims before Epic 007 is delivered and separately approved, per EPIC-008 §11).

**Central constraint governing all of the above:** none of these capabilities may be built as a new, independent scoring or reasoning engine. Every one of them must be a consumer of already-validated outputs (Business Quality, Financial Strength, Growth Intelligence, Valuation Intelligence, RCI, the Intelligence Engine's gates, and the Stock Movement Explanation Engine) — see Section 12.

## 12. Long-Term Architecture

```
Portfolio (holdings + daily performance/attribution, Section 10)
        |
        v
Intelligence Engine (backend/services/intelligence_engine/ — tiered evidence,
                      data-confidence, gates; Stock Movement Explanation Engine's
                      planned home per its own §15.1)
        |
        v
Research Analyst (Epic 008 — conversational layer, consumes Intelligence Engine
                   and existing engine outputs as evidence; never recomputes them)
        |
        v
Portfolio Copilot (Section 11 — portfolio-aware application of the Research
                    Analyst, gated on Epic 007 delivering real portfolio context)
```

**The rule this diagram exists to enforce: no duplicate scoring engines.** Every layer above consumes the layer below's already-validated output rather than recomputing an equivalent. Concretely: Portfolio's Health/Quality/Growth/Valuation scores (Section 10.5) must read from the existing, already-closed Business Quality/Growth Intelligence/Valuation Intelligence Engines; Portfolio's "why did this move" (Section 10.4) must read from the Stock Movement Explanation Engine rather than a second explanation mechanism; and Portfolio Copilot (Section 11) must read from the Research Analyst's own evidence contract (EPIC-008 §6) rather than inventing a portfolio-specific evidence model in parallel. This is the same architectural discipline already applied when the Stock Movement Explanation Engine was specified as "not a standalone hardcoded widget" — extended here to the entire Portfolio Copilot vision, not just one feature.
