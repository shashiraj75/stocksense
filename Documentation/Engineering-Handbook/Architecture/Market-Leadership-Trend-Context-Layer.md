# Market Leadership and Trend Context Layer — Current-State Investigation and Architecture

**Status:** PR [#22](https://github.com/shashiraj75/stocksense/pull/22) MERGED to `main` (merge
commit `67a1f13`) and automatically deployed DORMANT — all six flags OFF, no scoring influence,
UI not exposed. See [Current-Release-Status.md](../Operations/Current-Release-Status.md) for the
authoritative, up-to-date lifecycle status; this document's own narrative sections below describe
the investigation and design as originally authored and are not re-edited for lifecycle state.
**Branch:** `feat/market-leadership-trend-context` (from `origin/main` @ `29539d6`)
**Date:** 2026-07-24

This document records (1) the code-level current-state investigation performed before any
implementation, (2) the verified root cause of the validation job-identity defect, and
(3) the architecture and methodology of the new Market Leadership and Trend Context Layer.
All methodology and naming here is original to StockSense360 — it uses generally known
quantitative concepts (relative strength, percentile ranks, moving-average structure,
breadth) with independently designed formulations, thresholds, state names, and contracts.

---

## 1. Baseline evidence

| Item | Value |
|---|---|
| Repository | `git@github.com:shashiraj75/stocksense.git` (primary clone: `StockSense360/stock-predictor`) |
| Feature worktree | `StockSense360/stocksense-market-leadership-trend-context` |
| Branch / HEAD | `feat/market-leadership-trend-context` @ `29539d6114364d04faa485a101e8b8f5bc7ea34e` (= `origin/main` at branch time) |
| Local `main` state | 48 commits behind `origin/main`; 13 pre-existing tracked modifications + untracked files in `stock-predictor` — **untouched by this work** (dedicated worktree used instead) |
| Python | 3.14.5 (venv: `stock-predictor/backend/venv`) |
| Node / npm | v24.16.0 / 11.13.0 |
| Backend test command | `python -m pytest` (from `backend/`, pytest 9.1.1) |
| Frontend commands | `npx tsc --noEmit` · `npx vitest run` · `npx next build` |
| Baseline backend suite | **3553 passed, 0 failed**, 22 warnings (pre-existing: sklearn ConvergenceWarning, jwt InsecureKeyLengthWarning, numpy divide RuntimeWarning), 88s |
| Baseline frontend | typecheck / vitest / production build — recorded in the release evidence section of the final report |

Known pre-existing working-tree artifacts in `stock-predictor` (modified `alpha_engine.db`,
`telegram_bot.py`, several frontend files; untracked `picks_cache*.json`, `validation_results.db`,
`regime_kmeans.pkl`, `.claude/`) were identified and deliberately left untouched.

---

## 2. Verified current-state matrix

| Required capability | Existing implementation | Reusable | Needs refactor | Missing | Evidence |
|---|---|---|---|---|---|
| Market selection / isolation | `market: "IN"\|"US"` threaded explicitly through engines; `UNIVERSE_MARKET` map in `validation_engine.py` (fail-closed `_require_known_universe`) | Yes | No | — | `validation_engine.py:248-279`, `prediction_engine.predict(symbol, market, horizon)` |
| Symbol universes | `NIFTY_100`, `NSE_MIDCAP`, `US_BASKET` static lists in `validation_engine.py`; `stock_universe.py` full search lists; Daily Picks uses `stock_fundamentals_cache` | Yes (as v1 universe source) | No | **Point-in-time universe membership does not exist** — disclosed limitation | `validation_engine.py:100-151`, Sprint #014 |
| Benchmark mappings | `^NSEI` for India universes, `^GSPC` for US, in `run_validation` | Yes (convention) | No | — | `validation_engine.py:1184-1186` |
| Sector / industry classification | `INDIA_SECTORS` / `US_SECTORS` curated static maps in `heatmap_service.py` (thematic groups, overlapping membership, no effective dates) | Yes, as `classification_source="stocksense_heatmap_v1"` | No | **Point-in-time classification history does not exist** — disclosed limitation | `heatmap_service.py:20-86` |
| Heatmap calculations | `get_heatmap(market)` — 1-day % change per group, TTL cache, last-good persistence | Yes (pattern) | No | Multi-window group leadership absent | `heatmap_service.py:88-230` |
| Relative performance | `rs_score` inside validation `_score_at` (per-signal, vs benchmark) | Partially (concept only) | — | No universe percentile RS, no RS rank | `validation_engine.py:370+` |
| Momentum / moving averages | `technical_indicators.compute_indicators` (`ta` lib: SMA/EMA/RSI/MACD...) | Yes | No | — | `technical_indicators.py:6-52` |
| Volume calculations | `get_volume_signal`, OBV/MFI in indicators | Yes | No | — | `technical_indicators.py:114+` |
| Market regime detection | `alpha_engine/regime_cluster.detect_regime` (KMeans + semantic anchors) | Yes (context only — kept separate from breadth) | No | — | `regime_cluster.py:347` |
| Breadth calculations | **None** (only prose references) | — | — | **Missing — built new** | `grep -rin breadth backend/` → comments only |
| Daily Picks scoring | `daily_picks._zscore_and_rank` etc. | Not touched | No | — | Sprint #014; flags-off equivalence enforced by tests |
| Multibagger scoring | `multibagger_scorecard.py` | Not touched | No | — | — |
| Stock Detail prediction | `prediction_engine.predict` + `_pred_cache` (shared by reference — mutation hazard documented by RCI Sprint #007) | Yes (read-only) | No | — | `prediction_engine.py:175,399-410` |
| Validation job creation / progress | Module-global `_run_status` dict — **defect, see §3** | — | **Yes (fixed)** | Job identity metadata | `validation_engine.py:46,1175-1182` |
| Validation persistence | `val_runs` / `val_signals`, dual Postgres+SQLite, idempotent ALTER migrations | Yes (pattern reused) | Additive columns | `market` column absent (derivable via universe) | `validation_engine.py:62-84,289-351` |
| PIT fundamentals | US: `sec_pit_store` (append-only SEC facts, replay); India: DP-026 — **no PIT fundamentals provider exists** | Yes (US), disclosed (IN) | No | — | `sec_pit_store.py`, `_backtest_stock` market branch |
| Immutable fact store / replay | `sec_pit_store`, `instrument_master` manifest | Yes (pattern) | No | — | `instrument_master/manifest.py` |
| Historical pricing / corp actions | yfinance auto-adjusted history via `_fetch_history` (validation + prediction paths); NSE bhavcopy last-resort (PI-012) | Yes | No | Delisted-security history not available from provider — disclosed | `prediction_engine.py:420` |
| Outcome resolution | `postgres_store.log_outcome`, `execute_outcome_writes_transactional` | Yes (untouched) | No | — | `postgres_store.py:881,925` |
| Factor IC | `alpha_engine/ic_engine.py` | Yes (untouched) | No | — | — |
| Learning-engine activation | `alpha_engine` weight adapter, containment | Not touched | No | — | — |
| Feature flags | Env-var convention: `os.getenv("X_ENABLED") == "1"`, default OFF (`INTELLIGENCE_ENGINE_SHADOW_ENABLED` precedent) | Yes (pattern reused) | No | — | `daily_picks.py:1430` |
| DB init / migrations | `postgres_store.init_db` + per-module `_init_db`, DROP-then-ADD idempotent pattern (PI-010/#016) | Yes (pattern reused) | No | — | `postgres_store.py:700+` |
| Operational telemetry | `intelligence_engine/telemetry.py` (`persist_shadow_run`) | Yes (pattern reused) | No | — | `telemetry.py:24` |
| Session / calendar handling | `get_expected_latest_completed_nse_session` (PI-011, backend); `market_hours.py` | Yes | No | US equivalent added in new module (conservative rule) | PI-011 record |

**Duplicate-avoidance rule applied:** calculations reuse yfinance history fetching conventions and
`technical_indicators` where semantics match; nothing existing was re-implemented. Where an existing
primitive's semantics did **not** match (heatmap 1-day change ≠ multi-window leadership; validation
`rs_score` ≠ universe-percentile RS), a new, separately named implementation was built instead of
overloading the old one.

---

## 3. Validation job-identity defect — verified root cause

**Observed:** Validation UI with *Nifty 100 + Short* selected displayed an active run processing
US symbols (V, MA, JNJ, PYPL, UNH, PFE).

**Verified root cause (code-level, not hypothesis):**

1. `backend/services/validation_engine.py:46` — the active-run state is a **module-global dict**
   `_run_status = {"running", "progress", "total", "started_at", "log"}` carrying **no job_id, no
   market, no universe, no horizon**. A US run and an India run are indistinguishable in state.
2. `backend/api/routers/validation.py` `/status` returns that global verbatim; the response cannot
   be attributed to any market/universe/horizon by the client.
3. `frontend/src/app/validation/page.tsx:168-171` — the status query key is `["validation-status"]`
   with no universe/horizon; `isRunning`, the progress counter, and the log panel render under
   whichever tab the user currently has selected. Symbol names leak from the global `log` lines.
4. Compounding defects: `/run`'s response message hardcodes "across all Nifty 100 stocks" for every
   universe (`validation.py:60`); `get_last_run_time(horizon)` ignores universe entirely
   (`validation_engine.py:1490-1493`).

**Ruled out:** stale frontend cache (the poll is live, 3s), cache-key collision (there is no key),
websocket cross-binding (polling only), persistence ambiguity (`val_runs` stores universe+horizon
correctly at completion — completed results were never cross-contaminated; the defect is confined
to the *active-run* state and its rendering).

**Correction (implemented):** an immutable job-identity contract created once per run under the
status lock and never mutated afterward — `job_id`, `market`, `universe_id`, `universe_version`,
`benchmark`, `horizon`, `started_at`, `completed_at`, `status` (`queued|running|completed|failed`),
`processed`, `total`, `current_symbol`, `source_commit`, `model_version`, `methodology_version`,
`data_cutoff`, `requested_by`, `trigger_type`, `created_at`, `updated_at`, `failure_code`,
`failure_message`. `/status` returns the full identity; the identity is persisted in the run
summary at completion; the API never infers market/universe from frontend tab state. The frontend
distinguishes *selected view*, *active running job*, and *last completed result*, and shows an
explicit cross-universe banner ("A US S&P 500 validation is currently running. You are viewing
Nifty 100.") instead of relabeling a foreign job. Regression tests reproduce the original defect
and fail on the pre-fix behavior.

---

## 4. Architecture

New bounded module — `backend/services/market_leadership/`:

```
contracts.py          # typed dataclass contracts + reason-code registry (no I/O)
configuration.py      # feature flags, thresholds, methodology/universe versions
relative_strength.py  # pure RS calculations (DataFrame in → contract out)
group_leadership.py   # pure sector/industry leadership aggregation
trend_lifecycle.py    # pure deterministic lifecycle classifier
breadth.py            # pure market/sector breadth calculations + states
explanation.py        # "Why Now?" — structured fields only, no free generation
orchestration.py      # acquisition adapter + batch snapshot assembly (only I/O site)
persistence.py        # dual Postgres/SQLite snapshot storage, idempotent migrations
telemetry.py          # shadow-run telemetry (mirrors intelligence_engine pattern)
```

Principles: calculations are pure and deterministic (no network, no clock reads inside
calculation functions — `as_of` is always an explicit argument); acquisition is isolated in
`orchestration.py` behind an adapter; India and US never share a percentile universe; every
output carries `methodology_version`, `universe_id`, `universe_version`, `calculation_as_of`,
`data_coverage`, and `data_quality_status`; missing data yields `INSUFFICIENT_DATA`, never a
silently neutral score.

### 4.1 Relative Strength Rank (methodology `ml-rs-1.0.0`)

For stock *s* with total-return-adjusted closes (yfinance auto-adjust) and market benchmark *B*
(India `^NSEI`, US `^GSPC`), over windows W = {21, 63, 126, 252} trading days (1/3/6/12 months):

- `rel_ret_w(s) = total_return_w(s) − total_return_w(B)` (benchmark-relative, percentage points)
- Composite relative return `C(s) = Σ k_w · rel_ret_w(s)` with **equal weights k_w = 0.25**
  (windows with insufficient history are excluded and weights renormalized **only if** ≥ 2
  windows including the 63d window are available; otherwise `INSUFFICIENT_DATA`).
- `stock_rs_percentile` = percentile rank of `C(s)` within the market's eligible universe
  (average-rank tie handling, 0–100).
- `stock_rs_score` = the same percentile (integer 0–100) — score and percentile are deliberately
  the same number in v1: one interpretable quantity, no hidden rescaling.
- `rs_trend`: sign of (mean of last 10 sessions' 21d rel_ret − mean of prior 10 sessions' 21d
  rel_ret) with a ±0.5pp dead-band → `IMPROVING | FLAT | WEAKENING`.
- `rs_acceleration`: annualized `rel_ret_21` − annualized `rel_ret_126` (is near-term relative
  performance running ahead of medium-term?).

**Why equal weights:** candidate formulations (equal, recency-weighted, volatility-adjusted,
residual momentum, percentile aggregation) are all implemented in the shadow-experiment harness
(`scripts/leadership_shadow_experiment.py`). Equal weighting is v1's default because it has the
fewest tuned parameters, is the most interpretable, and weight selection by in-sample return
ranking is exactly the overfitting Section-15 evidence rules forbid. Any reweighting requires
walk-forward evidence through the harness and a methodology-version bump. Scoring influence is
OFF regardless.

Gates: instrument-type (reuses intelligence-engine gate semantics — equities only), minimum
median daily traded value, minimum bar coverage (≥ 90% of expected sessions in each used window),
explicit `data_quality_status ∈ {OK, PARTIAL_HISTORY, STALE, FAILED}`. IPOs: scored only when the
63d window is fully covered; otherwise `INSUFFICIENT_DATA` with reason `INSUFFICIENT_HISTORY`.

### 4.2 Sector and Industry Leadership (methodology `ml-gl-1.0.0`)

Per market, per group (v1 groups = `stocksense_heatmap_v1` classification; sector==industry in v1
— the classification source has one level; both fields are emitted with the same value and
`classification_version` so a finer industry source can slot in later):

- `median_member_rs` (equal-weight center), `weighted_member_rs` (cap-proxy weights capped at 20%
  per member so one mega-cap cannot dominate — cap weights derived from `stock_fundamentals_cache`
  market caps where available, else equal).
- `group_rs_score` = percentile of `median_member_rs` across groups in the same market.
- `leadership_breadth` = share of members with `stock_rs_percentile ≥ 60`.
- `participation_ratio` = share of members above their own 50-day MA.
- `improving_member_ratio` = share with `rs_trend = IMPROVING`.
- `new_high_participation` = share within 5% of their 252d high.
- `group_trend` = direction of the group's median 21d relative return vs its prior value.
- Ranks (`sector_rank`, `industry_rank`) by `group_rs_score` descending; `*_rank_change` vs the
  previous persisted snapshot.
- `group_state` (deterministic): `LEADING` (score ≥ 70 ∧ breadth ≥ 0.5), `IMPROVING`
  (trend up ∧ improving_ratio ≥ 0.5), `WEAKENING` (trend down ∧ score ≥ 40), `LAGGING`
  (score < 40 ∧ not improving), `INSUFFICIENT_DATA` (< 5 scored members or member coverage < 60%).
  Leadership concentration is labeled `BROAD / MODERATE / CONCENTRATED / DETERIORATING` from
  breadth and the equal-vs-capped-weight gap.

Point-in-time honesty: the classification has **no effective-date history**; every group output
carries `classification_source`, `classification_version`, and `pit_safe: false` so historical
validation can never silently pretend otherwise.

### 4.3 Trend Lifecycle (methodology `ml-tl-1.0.0`)

Deterministic, auditable rules on completed sessions (inputs: 20/50/200-day MAs and slopes,
price vs MAs, 252d high/low distance, drawdown, 21d volume-confirmation ratio (up-day volume /
down-day volume), higher-high/higher-low structure over 63d, RS trend). States and core rules:

- `BASE_FORMING` — price within ±7.5% of flat (|slope| < 0.02%/day) 200MA, 252d drawdown ≥ 15%
  recovered to < 10% band, volatility contracting.
- `EARLY_ADVANCE` — price above rising 50MA, 50MA crossed above 200MA within last 63d or price
  0–15% above 200MA, RS improving.
- `CONFIRMED_ADVANCE` — MA order 20>50>200 all rising, higher-high/higher-low structure, price
  ≤ 25% above 200MA.
- `EXTENDED_ADVANCE` — CONFIRMED_ADVANCE structure but price > 25% above 200MA or > 40% above
  252d low in last 63d — strong trend, elevated extension risk (`extension_risk: HIGH`).
- `DISTRIBUTION_RISK` — above 200MA but ≥ 2 high-volume decline sessions (volume ≥ 1.5× 50d avg,
  close −2% or worse) in last 21d, or 20MA below 50MA with RS weakening.
- `DECLINING` — price below falling 200MA, or below both 50 and 200 MA with lower-low structure.
- `INSUFFICIENT_DATA` — < 210 completed sessions.

Every classification returns `reason_codes` (e.g. `ABOVE_RISING_LONG_MA`,
`SHORT_MA_ABOVE_MEDIUM_MA`, `RS_IMPROVING`, `BREAKOUT_CONFIRMED`, `VOLUME_CONFIRMATION`,
`EXCESSIVE_EXTENSION`, `HIGH_VOLUME_DECLINES`, `LONG_MA_FALLING`, `INSUFFICIENT_HISTORY`) plus
`evidence_completeness` (fraction of rule inputs that were computable). Trend strength and
extension risk are separate fields — a strong trend is never presented as an entry signal, and
wording never claims "institutional accumulation"; only direct price-volume facts are stated.
No ML classifier in v1 (auditable by construction).

### 4.4 Market Breadth (methodology `ml-br-1.0.0`)

Per market, completed sessions only (India: `get_expected_latest_completed_nse_session` port; US:
conservative equivalent — a session counts only after 00:00 UTC of the following calendar day):

Components (each persisted with numerator, denominator, coverage): % above 20/50/200-day MA;
21d advance/decline ratio; 63d new-highs vs new-lows; % of members with improving RS; % of groups
improving; breadth-vs-benchmark divergence (benchmark 21d return sign vs median member 21d return
sign). States (level + direction, deterministic):

- `HEALTHY` — ≥ 60% above 200MA ∧ ≥ 50% above 50MA ∧ A/D ≥ 1.
- `NARROWING` — benchmark 21d return positive while < 45% above 50MA or median member return
  negative (divergence).
- `DETERIORATING` — % above 50MA falling across last 5 sessions ∧ A/D < 1 ∧ new lows > new highs.
- `RECOVERY` — % above 20MA rising from < 30% through ≥ 40% within 21d ∧ A/D ≥ 1.2.
- `WASHED_OUT` — ≤ 15% above 200MA ∧ new lows ≥ 5× new highs.
- `INSUFFICIENT_DATA` — coverage < 60% of the universe or < 210 sessions of history.

Rule precedence is fixed (WASHED_OUT > RECOVERY > DETERIORATING > NARROWING > HEALTHY) so states
are mutually exclusive and reproducible. Sample-size disclosure is mandatory (`coverage` on every
component); a state computed from < 60% coverage is refused, not published.

### 4.5 "Why Now?" explanation contract

`explanation.py` renders exclusively from verified structured fields — a fixed template registry
keyed by reason codes; no LLM, no free text generation, no numeric value that is not present in
the input contract. Output: `summary`, `supporting_factors[]`, `caution_factors[]`,
`stock_rs_context`, `industry_context`, `sector_context`, `trend_context`, `breadth_context`,
`extension_risk`, `data_freshness`, `data_quality`, `calculation_as_of`, `methodology_version`,
`reason_codes[]`. Wording never claims certainty or guaranteed returns; insufficient-data
conditions surface as explicit caution factors.

### 4.6 Persistence (dual Postgres / SQLite)

Tables (created via the module's own idempotent `init_leadership_db()`, mirroring
`validation_engine._init_db`'s proven pattern; **not** wired into production `init_db()` startup
— local/shadow only until deployment is approved):

- `ml_rs_snapshots` — UNIQUE `(market, symbol, as_of_session, universe_version, methodology_version)`
- `ml_group_snapshots` — UNIQUE `(market, group_name, group_kind, as_of_session, universe_version, methodology_version)`
- `ml_breadth_snapshots` — UNIQUE `(market, as_of_session, universe_version, methodology_version)`

Each row: full contract JSON + `status` (`COMPLETE|PARTIAL`) + `created_at`. `PARTIAL` snapshots
are never returned by read paths (`WHERE status='COMPLETE'`) — a partial calculation cannot
publish as complete. Duplicate insert = upsert only while `PARTIAL`; a `COMPLETE` snapshot is
immutable (insert-or-ignore).

### 4.7 Feature flags — five backend capability flags, all default OFF

| Flag | Effect when `"1"` |
|---|---|
| `MARKET_LEADERSHIP_ENGINE_ENABLED` | master gate — module may compute at all |
| `MARKET_LEADERSHIP_SHADOW_ENABLED` | shadow snapshot computation + telemetry persistence |
| `MARKET_LEADERSHIP_UI_ENABLED` | API returns payloads to the frontend component |
| `MARKET_LEADERSHIP_VALIDATION_ENABLED` | shadow-experiment harness may read persisted snapshots |
| `MARKET_LEADERSHIP_SCORING_ENABLED` | **reserved — no code path consumes it in this release**; scoring influence requires Gate 7 |

With every flag OFF: no new imports execute in Daily Picks/Multibagger/Portfolio/Alerts/Paper
Trading paths, and the API endpoint answers `{"status": "disabled"}` with zero acquisition,
calculation, or persistence — each property locked by regression tests.

### 4.7a Frontend presentation gate — one fail-closed public flag, separate from the five above

`NEXT_PUBLIC_MARKET_LEADERSHIP_UI_ENABLED` is a **sixth, frontend-only** gate — the browser-side
mirror of the backend's `MARKET_LEADERSHIP_UI_ENABLED`, not a duplicate or a replacement for it.
Found necessary during a pre-publication audit: the component's `useQuery()` previously always ran,
so every eligible Stock Detail page view issued a real browser request to
`GET /api/leadership/context` even with all five backend flags OFF — the backend safely answered
`{"status":"disabled"}` with no backend-side work, but the request itself still happened on every
page view. `isMarketLeadershipUiEnabled()` (`frontend/src/utils/marketLeadership.ts`) now gates the
query's own `enabled` option, so with the flag off the browser makes **no request at all**, not just
an empty-handed one.

- Fail-closed: only the exact string `"1"` enables it — absent, empty, `"0"`, `"true"`, or any other
  value (including trailing whitespace) is treated as disabled.
- Contains no secret — `NEXT_PUBLIC_*` variables are inlined into the client bundle by Next.js and
  are visible to anyone regardless of this flag's own logic; it exists purely to suppress an
  unnecessary request, not to hide anything.
- **Three independent gates** must all permit operation before any leadership data is ever visible
  to a user: (1) this frontend flag, (2) `MARKET_LEADERSHIP_ENGINE_ENABLED`, (3)
  `MARKET_LEADERSHIP_UI_ENABLED` (backend). Enabling only the frontend flag still produces zero
  visible data, since the backend gates remain the authoritative source of truth and still answer
  `{"status":"disabled"}`. Enabling only the backend flags still produces zero browser request and
  zero rendered component, since the frontend gate is checked first and the query never fires.
  Merging this PR with no environment variable changed anywhere adds **zero** new leadership browser
  requests to any existing page view — the flag is not set in any `.env` file committed to this
  repository, local, preview, or production environment.

### 4.8 API (additive)

`GET /api/leadership/context?symbol=&market=` → canonical structured context (stock RS + group +
trend + breadth + explanation) with `status ∈ {ok, disabled, unavailable, insufficient_data,
stale, error}`, `as_of`, `expected_session`, `freshness`, `coverage`, `methodology_version`,
`universe_version`, `reason_codes`. No existing endpoint's schema changes. Reads persisted
snapshots only — never recalculates per request.

### 4.9 Frontend

`MarketLeadershipContext` component on the Stock Detail page, rendered only when the API returns
`status: "ok"` (flag-gated server-side): RS percentile, industry/sector rank + direction, trend
lifecycle, market breadth, extension risk, Why Now, freshness, methodology tooltip, and a
permanent **"Experimental — not investment advice; does not affect BUY/HOLD/SELL"** label.
Market isolation follows the page's existing market context; India context is never rendered for
a US symbol and vice versa (component test).

---

## 5. Point-in-time and survivorship controls

- All calculations take `as_of` explicitly; bars after `as_of` are excluded by slicing before any
  indicator computation (leakage test: adding future bars must not change the as-of result).
- Completed sessions only (see §4.4 session rules); same-day partial bars excluded.
- Universe membership: v1 universes are versioned (`universe_version = "ul-2026.07"`), and
  **historical validation labels every result `universe_pit: false`** — current membership applied
  to past dates is a disclosed survivorship limitation (no historical membership source exists in
  the repository; building one is out of scope and would require a new data source).
- Sector classification: same disclosure (`pit_safe: false`), source + version stored.
- No later-restated fundamentals are consumed at all (the layer is price/volume only in v1 —
  fundamentals deliberately excluded until a PIT-safe India source exists, per DP-026).
- Offline replay: the shadow harness runs from persisted snapshots/fixtures with zero live calls;
  identical inputs → identical outputs (property test).
- Methodology/universe/model versions locked into every persisted row and every API payload.

## 6. Rollback plan

All changes are additive and flag-gated. Rollback = revert the feature commits (no data
migration to unwind in production — production DB untouched; new tables are created only when the
module itself runs with flags on). The validation job-identity fix is independent and can be
reverted separately; its schema change is additive JSON-summary metadata only.

## 7. Regulatory note

Outputs are research intelligence, not advice; the layer adds context, never a recommendation.
Security-specific BUY/HOLD/SELL labels, target prices, and ranked picks (pre-existing product
surface) remain flagged for specialist securities-law review before wider public monetisation —
this layer does not weaken existing disclaimers and introduces none of its own advice claims.
