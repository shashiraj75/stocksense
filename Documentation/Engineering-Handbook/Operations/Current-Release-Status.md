# StockSense360 — Current Release Status

**Purpose:** This document is the authoritative operational-status register for live and pending releases. It records what is deployed, what remains disabled, what is pending validation, and which future actions require explicit approval.

**Use this document for current state.** Historical sprint reports, Epic closures, SSDS documents, and audit reports remain authoritative evidence for their own completed scope, but they do not automatically describe the current production operating state.

**As of:** 2026-07-16 — maintained as a live operational register

---

## Intelligence Engine V1 — Universe Builder (Instrument Type / Tradability / Liquidity / Data Confidence Gates)

**Naming note (read first):** this work was referred to informally as "Epic 007" throughout its own task history — that number is **already allocated** in `MASTER-ROADMAP.md` Section 11 to *Portfolio and Watchlist Intelligence* (a separate, unrelated, still-"Planned / Not Started" initiative). This entry does not claim the Epic 007 number; it is recorded here under its own name to avoid the collision. Whoever formally assigns this work an Epic number later should reconcile the two.

**Status:** Deployed to production — shadow-only, zero effect on published Daily Picks, Heatmap, or Portfolio. **Runtime validation complete (2026-07-08)** — see [Intelligence Engine V1 — Runtime Validation Closure](../Releases/Intelligence-Engine-V1-Runtime-Validation-Closure.md); formal closure remains a separate, explicit decision.

- **Architecture**: `backend/services/intelligence_engine/` — a standalone package (Instrument Type Gate, Tradability Gate, Liquidity Gate, Data Confidence scoring, shadow-run orchestration, telemetry persistence). Universe Builder is one component inside this engine, not the whole system. Gated end-to-end by `INTELLIGENCE_ENGINE_SHADOW_ENABLED` (default off); with the flag off, `daily_picks.py` never even imports the package — confirmed directly via `sys.modules` inspection, not just by code review.
- **Phase 3A (Instrument Type Gate)**: implemented and **production runtime-validated**. First real India shadow telemetry captured 2026-07-06: `raw_count: 2402`, `passed_count: 2400` (plain equities), `excluded_count: 2` (`GOLDBEES`, `SILVERBEES` — correctly classified as ETFs).
- **Phase 3B-A (Liquidity / Tradability / Data Confidence gate framework)**: implemented and deployed. All three gates are pure, unit-tested functions with configurable (env-driven, not hardcoded) thresholds.
- **Phase 3B-B (selected-pick market data wiring)**: implemented and deployed (commit `bb5d3cf8247ce829ca9d0e06d4048fc7aa7b740e`). Derives real price/freshness/completeness data for the gates from the Daily Picks payload already returned by `generate_picks()` — no new provider/API calls, no production function's return contract changed. Coverage is currently limited to the symbols selected into `payload["picks"]` (~18 per run), not the full Phase-0/Phase-1 candidate pool scanned before narrowing to those picks — a disclosed limitation, not an oversight.
- **Runtime validation complete (2026-07-08)**: both closure criteria verified via direct, read-only production API calls — IN telemetry (run 2026-07-07T22:25 UTC) shows `source_commit = d81363e` (verified descendant of `bb5d3cf` by git ancestry check) and US telemetry (run 2026-07-07T06:04 UTC) shows `source_commit = bb5d3cf` exactly; both markets report `tradability.available = true`, `liquidity.available = true`, `data_confidence.available = true`. Full evidence, sanity notes, and named residual risks (picks-only gate coverage, single-run evidence, zero-failure gates as weak positive evidence): [Intelligence Engine V1 — Runtime Validation Closure](../Releases/Intelligence-Engine-V1-Runtime-Validation-Closure.md).
- **Formal closure not yet declared** — runtime validation met the register's two named criteria, but declaring the initiative closed (and reconciling the Epic-number collision above) remains an explicit decision, not an automatic consequence of this evidence.

## Release 12B — Daily Picks Universe and Reliability Validation

**Status:** Deployed — India and US natural-generation evidence now recorded (2026-07-14); scheduler-timing reliability remains a separate open item.

Original gate criteria, reviewed individually rather than declared passed as a block:

| Criterion (as originally stated) | Classification | Evidence |
|---|---|---|
| "India validation requires a genuine fresh post-release generation window." | **Passed by the 2026-07-14 India evidence** | India's scheduled Daily Picks run completed on the current deployed code (commit `36d4b33`, includes both Sprint #014's stratified universe and Phase 1A.6): `has_today: true`, `last_error: null`, `universe_degraded: false`. This is a genuine natural (scheduler-fired, not manually triggered) generation window post-release. |
| "US validation requires a normal US market session and a separate controlled validation." | **Passed by the 2026-07-14 US evidence** | A separate scheduled US job (`942231a1`) completed on the same commit on a live US market day: `has_today: true`, `last_error: null`, `universe_used: "fundamentals_cache"`, `universe_degraded: false`, `universe_candidate_count: 400` (matches Sprint #014's `_TARGET_UNIVERSE_SIZE`). This is also the first live confirmation Sprint #014's stratified universe completes end-to-end for US. |
| "No Daily Picks scheduler enablement is approved until India and US validations both pass." | **Superseded by later implementation** | There is no separate application-level "scheduler enabled" flag to approve — GitHub Actions cron for both markets already exists, is active, and both markets' triggers are what produced the passing evidence above. What remains genuinely open is scheduler-*timing* reliability (see below), not a gate on whether the pipeline itself may run. |
| "No validation result may be described as passed until its release-specific evidence record is complete." | **Still governs — satisfied for this update** | Evidence for both markets is recorded in [Product Integrity #003](../Releases/Product-Integrity-003-Phase-1A6-Production-Migration-and-Natural-Run-Verification.md). This rule itself remains standing policy. |

- **Superseded scope note (2026-07-10):** this release's original universe-construction logic (`yf.screen()`-based, market-cap-descending with a hard cutoff) has been replaced entirely — see the Daily Picks Large/Mid/Small-Cap Stratification entry in `STOCKSENSE_DOCUMENTATION.md` §27 Session 10. The 2026-07-14 evidence above is scoped against this replacement (`stock_fundamentals_cache`-sourced, tier-stratified universe), not the old Yahoo-screener logic — resolving the concern this note originally flagged.
- **Hardening preflight recorded (2026-07-12, markets closed):** a planning-only preflight for runtime/provider hardening (timeouts, retry/backoff, per-symbol failure isolation, stale-cache detection, and related areas) was recorded at [Preflight — Daily Picks Runtime/Provider Hardening](../Releases/Preflight-Daily-Picks-Runtime-Provider-Hardening.md). No code from that preflight has been implemented; it remains a planning record only, unaffected by the 2026-07-14 evidence above.
- **What "natural generation works" does NOT mean:** it does not mean all Daily Picks runtime/provider hardening work is complete (the 2026-07-12 preflight above remains unimplemented), and it does not mean granular output quality (real tier diversity, confidence distribution, total runtime) has been inspected — see the still-open items in `Releases/Sprint-014-Daily-Picks-Cap-Stratification-and-Confidence-Priority.md`'s own Recommendations section.
- **Scheduler reliability — separately gated, open work.** GitHub Actions' scheduled cron for both markets has repeatedly fired hours later than its nominal time (US observations below were recorded against the then-nominal 04:00 UTC base cron; the base schedule has since moved to **06:00 UTC** — see Product Integrity #007 — so these specific dispatch-delay timestamps are historical evidence of the delay pattern, not a statement of the current nominal time): observed firing at 06:04 UTC on 2026-07-14, 06:45 UTC on 2026-07-13, 07:27 UTC on 2026-07-10, 15:33 UTC on 2026-07-09 (see [Product Integrity #003](../Releases/Product-Integrity-003-Phase-1A6-Production-Migration-and-Natural-Run-Verification.md)). A GitHub Actions run reporting "success" only certifies that the asynchronous trigger `POST` was accepted (202) — it does **not** certify that the downstream generation job actually completed; those are two different, decoupled facts. Scheduler timing and end-to-end completion monitoring require a separate forensic design review — not solved by, and not blocking, the evidence recorded above.
- **Product Integrity #007 / #008 — US schedule and workload-overlap correction (2026-07-15).** US Daily Picks base generation moved to 06:00 UTC and the Premarket Finalizer to a 6:00 AM ET target (6:00-7:30 AM ET acceptance window); the US Multibagger refresh moved from 02:00 UTC to 08:00 UTC to stop colliding with the new base schedule. **The 6:00 AM ET finalizer schedule change (#007) is deployed and awaiting natural-run verification. #008's daily-weekday US Multibagger schedule and its "cooperative stop" concurrency mechanism have both since been superseded by #009 below — do not treat this entry as describing the current Multibagger schedule.** See [Product Integrity #007](../Releases/Product-Integrity-007-US-Premarket-Finalizer-6am-ET-Schedule-Change.md) and [Product Integrity #008](../Releases/Product-Integrity-008-US-Scheduler-Workload-Overlap-Compatibility.md).
- **Product Integrity #009 — weekly Multibagger refresh and durable lifecycle (2026-07-16).** Converted the full-universe fundamentals refresh from nightly (India) / daily-weekday (US) to weekly for both markets and made both markets durable. **A production forensic audit for #010 below found this release's own schema migration silently failed to apply for two objects (the market constraint stayed US-only, the active-job index kept its old predicate) — India Multibagger was non-functional in production for the full day #009 was live. See #010 for the fix; this entry's schedule/design details (the two-DST-candidate US cron in particular) are also superseded below.** See [Product Integrity #009](../Releases/Product-Integrity-009-Weekly-Multibagger-Refresh-and-Durable-Lifecycle.md).
- **Product Integrity #010 — Multibagger production hardening and legacy-schema repair (2026-07-16).** Repaired the #009 migration gap above via explicit `DROP`-then-`ADD`/`CREATE` statements (verified against production: market constraint now `IN ('IN','US')`, active-job index now `WHERE status IN ('queued','running')`, both confirmed via direct read-only introspection post-deploy). Simplified US Multibagger to a single fixed `0 8 * * 0` cron (3:00 AM EST / 4:00 AM EDT, truthfully disclosed in user-facing copy) instead of two DST candidates. Job reservation and heavy-workload lease acquisition are now atomic (one transaction). Added a resumable worker claim (`SELECT ... FOR UPDATE SKIP LOCKED`) and per-symbol staging with atomic cache promotion — a partial/failed run never touches the active `stock_fundamentals_cache`. India/US Daily Picks and the Premarket Finalizer schedules were explicitly frozen and regression-tested (unchanged: `56 21 * * 0-4`, `0 6 * * 1-5`, `0 10 * * 1-5` + `0 11 * * 1-5`). **Deployed, awaiting the first natural India Daily Picks run, US Daily Picks/Finalizer sequence, India Saturday Multibagger run, and US Sunday Multibagger run — none yet observed.** See [Product Integrity #010](../Releases/Product-Integrity-010-Multibagger-Production-Hardening-and-Legacy-Schema-Repair.md).

## Product Integrity #011 — India Session-Freshness Backend Gate

**Status:** Deployed to production (2026-07-16, commit `ec3cef0`). See [Product Integrity #011](../Releases/Product-Integrity-011-India-Session-Freshness-Backend-Gate.md).

- Root cause: per-symbol Yahoo/yfinance price-data lag for India Daily Picks (already forensically identified by Product Integrity #004, which built a frontend-only disclosure banner but explicitly deferred a backend gate). Triggered by a 2026-07-16 production screenshot review showing every India Daily Pick flagged stale.
- Adds `get_expected_latest_completed_nse_session()` (backend port of the frontend's existing session-freshness algorithm), a retry-on-stale branch inside `_fetch_history()`'s existing 3-attempt budget, honest `is_stale`/`expected_session` labeling on `price_reference` at generation time, and a fix so error-shaped `predict()` results are excluded from the candidate pool instead of silently polluting it with `None` fields.
- India (`market == "IN"`) only — does not change scoring, ranking, or universe selection.
- Does not eliminate staleness entirely on its own — see Product Integrity #012 immediately below, which adds a real second data source for the cases retry alone can't resolve. Natural-run verification (comparing the live stale-rate against the 2026-07-16 baseline) is pending the next scheduled India generation.

## Product Integrity #012 — NSE Bhavcopy Last-Resort Price Correction

**Status:** Deployed to production (2026-07-16, commit `b83b12a`). See [Product Integrity #012](../Releases/Product-Integrity-012-NSE-Bhavcopy-Price-Correction-Fallback.md).

- Builds on #011: when yfinance's 3-attempt retry budget is exhausted and a bar is still stale, adds one last-resort lookup against NSE's own official daily bhavcopy archive (`https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv`) before accepting the stale price.
- Reachability from Railway's production network was verified directly (not assumed) via a read-only probe run from inside the actual production container: `HTTP_CODE: 200`, real ~369KB daily file returned.
- **Meaningfully changes #011's stated scope**: when a bhavcopy correction fires, `current_price` — and therefore entry/target/stop-loss trade levels, not just the displayed reference price — is computed from NSE's corrected close rather than the stale Yahoo one. This is deliberate and disclosed, not incidental.
- Does not touch OHLC history or technical indicators (bhavcopy has no history, only a single day's close) — a corrected pick's price is accurate; its technical indicators may still be computed from history that includes a stale last bar. Does not add a US equivalent.
- Natural-run verification (checking for `generation_reference_source: "nse_bhavcopy"` on any pick) is pending the next scheduled India generation.

## Product Integrity #013 — India Daily Picks Schedule Move (2:07 AM IST) and Stale-Copy Repair

**Status:** Deployed to production. See [Product Integrity #013](../Releases/Product-Integrity-013-India-Daily-Picks-Schedule-Move-and-Stale-Copy-Repair.md).

- India Daily Picks cron moved from `56 21 * * 0-4` UTC (3:26 AM IST) to `37 20 * * 0-4` UTC (**2:07 AM IST**), by explicit user request.
- In the process, found and fixed a pre-existing, silent staleness: the frontend's `"generated daily at 2 AM IST"` label had been wrong for about a week (since the cron moved to 3:26 AM IST on 2026-07-09 without the label following). Now correctly reads `"2:07 AM IST"`, matching the new live schedule.
- Confirmed (did not need to fix) that `"screened from N eligible NSE stocks"` is not a hardcoded/stale number — it's a live template driven by the backend's actual `screened_from` field each run.
- Corrected a related functional drift in `main.py`'s startup catch-up threshold, which had assumed generation starts near 2 AM IST while the real cron had drifted to 3:26 AM IST — the schedule move itself resolves this (2:07 AM IST is close enough to the existing hour=2 threshold), the code comment was updated to state the current reality rather than the stale one.
- Two "frozen schedule" regression tests (Product Integrity #010) were deliberately updated to the new cron — that freeze exists to prevent *accidental* drift from unrelated work, not to block an explicit, user-directed scheduling decision like this one.
- Scope was explicitly limited to functional code (workflow YAML, frontend label, backend strings/comments, tests) — the ~12 `Documentation/*.md`/`README.md` prose references to "2 AM IST" were left as-is per user direction, and remain a known stale-reference risk for a future doc pass.

## Product Integrity #014 — Stock Detail Page Forensic Audit, HIGH-Severity Fixes

**Status:** Deployed to production (commit `645a7c8`). See [Product Integrity #014](../Releases/Product-Integrity-014-Stock-Detail-Page-Forensic-Audit-HIGH-Severity-Fixes.md).

- Full forensic audit of `stocksense360.com/stock/{symbol}` (~2,100-line page, live quote + per-horizon AI predictions + fundamentals + backtest + score history + news/sentiment) triggered by a user-reported ambiguity: the AI Signal card silently showed Medium Term data on the Fundamentals tab with no disclosure. Five parallel read-only audits found **23 distinct findings** (7 HIGH, 10 MEDIUM, 6 LOW) plus a documented list of verified-clean areas.
- This release fixed the 7 HIGH-severity findings: (1) AI Signal card now discloses "· Medium Term" on Fundamentals/History tabs; (2) 52W High/Low pills null-guarded (previously could render the literal string "₹undefined"); (3) AI Prediction card's signal strip now uses `getSignalTone` so a low-confidence BUY mutes consistently instead of showing a muted badge next to a bright-green strip; (4) Trade Levels card discloses when it's computed off a stale `prediction.current_price` vs. the live quote price; (5) Paper Trade button disables during a background horizon refetch instead of risking a trade against the wrong horizon's data; (6) Backtest results now clear on symbol/market navigation instead of potentially showing a previous stock's results under a new stock's header.
- 12 new regression tests, full frontend suite 291/291 passing, clean typecheck. No backend changes.
- The remaining 16 MEDIUM/LOW findings are addressed in [Product Integrity #015](../Releases/Product-Integrity-015-Stock-Detail-Page-Forensic-Audit-MEDIUM-LOW-Severity-Fixes.md) immediately below.

## Product Integrity #015 — Stock Detail Page Forensic Audit, MEDIUM/LOW-Severity Fixes

**Status:** Deployed to production (commit `1e3c627`). See [Product Integrity #015](../Releases/Product-Integrity-015-Stock-Detail-Page-Forensic-Audit-MEDIUM-LOW-Severity-Fixes.md).

- Follows PI-014 directly — fixed 15 of the 16 remaining findings from the same audit (10 MEDIUM, 5 of 6 LOW).
- Highlights: Market Regime/Evidence/Research panels gated to horizon tabs only (previously leaked onto Fundamentals/History/Backtest unlabeled); Take Profit color now reflects win/loss instead of raw price-direction (was showing red for a SELL's successful target, indistinguishable from its stop-loss); Mkt Cap uses ₹ Cr for India instead of a contradicting T/B/M convention elsewhere on the same page; History chart dates pinned to UTC (could previously show the wrong calendar day for non-IST viewers); Debt/Equity convention unified with the Multibagger page (× ratio instead of raw-scale %); several backend-computed US fundamentals fields (ROCE, EV/EBITDA, OPM%, interest coverage, P/S) surfaced for the first time.
- Finding #15 was excluded (backend DB schema change, different risk category) — see [Product Integrity #016](../Releases/Product-Integrity-016-Score-Snapshots-Market-Scoping.md) immediately below.
- 21 new regression tests, full frontend suite 312/312 passing, clean typecheck. No backend changes.

## Product Integrity #016 — Score Snapshots Market Scoping

**Status:** Deployed to production (commit `883b304`) — migration confirmed live via direct DB query (market column present, new constraint/index in place, old ones gone). See [Product Integrity #016](../Releases/Product-Integrity-016-Score-Snapshots-Market-Scoping.md).

- Closes finding #15 from the Stock Detail page audit — `score_snapshots` had no `market` column.
- **A direct production audit found this wasn't hypothetical**: 225 symbols genuinely exist in both IN and US markets (AAPL, ADBE, ABT, and others), and since the old unique constraint was `(symbol, horizon, snapshot_date)` with `ON CONFLICT DO UPDATE`, this means one market's score-history data may have been silently overwritten by the other's for any of those symbols scored by both markets on the same day — not just a display bug.
- Migration: nullable `market` column added (no backfill guess for existing rows — genuinely unrecoverable which market a legacy colliding row belongs to), unique constraint and index rebuilt to include `market`, using the DROP-then-ADD idempotent pattern established in Product Integrity #010 (not relying on `IF NOT EXISTS` alone). Self-applied via `init_db()` on backend startup, same mechanism PI-010 used.
- Write path (`daily_picks.py`) and both read paths (History tab chart, Portfolio's Signal-column fallback — a second caller with the identical pre-existing gap, found in the same pass) now require `market`, matching `market IS NULL` too so the ~95% of symbols that never collided keep their pre-migration history intact.
- 16 new regression tests (12 backend, 4 frontend), full suites passing (2169/2169 backend, 316/316 frontend), clean typecheck.

## Product Integrity #017 — AI Prediction Confidence Display Fixes

**Status:** Deployed to production (commit `58632de`). See [Product Integrity #017](../Releases/Product-Integrity-017-AI-Prediction-Confidence-Display-Fixes.md).

- User feedback on a live low-confidence pick (DIXON, 12% confidence BUY): the "AI Prediction" card showed Confidence twice in a row (Signal Strip + a redundant standalone `ConfidenceMeter` using a *different* muting threshold than the strip); Trade Levels rendered a fully precise, confident-looking setup with no acknowledgment the underlying signal was only 12% confident.
- Fixes: removed the duplicate `ConfidenceMeter`; added a low-confidence warning banner to Trade Levels (`confidence < 45`, the same threshold `getSignalTone` already uses to mute a weak BUY) — discloses rather than hides the numbers, since they're still mathematically valid ATR-based levels.
- 5 new regression tests, full frontend suite 321/321 passing, clean typecheck. No backend changes.
- Follow-up: the user's own sharper question ("how is 12% confidence + 15.8% upside possible?") led directly to Product Integrity #018 below, which fixes the actual root cause rather than just disclosing it.

## Product Integrity #018 — Confidence-Scaled Target Price Floors

**Status:** Deployed to production (commit `970e0d6`). See [Product Integrity #018](../Releases/Product-Integrity-018-Confidence-Scaled-Target-Price-Floors.md).

- **Root cause of PI-017's reported mismatch**: `_estimate_target()`'s BUY/SELL minimum target floors (e.g. "a long-term BUY always shows at least +15% upside") were flat constants, completely decoupled from `confidence` — a 12%-confidence BUY and a 90%-confidence BUY got the identical guaranteed floor.
- Fix: `conf_factor`'s floor lowered from 0.5 to 0.2 and applied consistently to every BUY/SELL floor across short/medium/long horizons (medium and long previously had no confidence scaling on their floors at all). At full confidence, floors match the original flat constants exactly — zero behavior change for high-confidence picks. HOLD's target band is intentionally untouched (no directional-conviction claim to scale).
- This is core `predict()` logic, not just Stock Detail page display — the fix propagates automatically into Daily Picks, Multibagger, and backtest target-price consumers, since they all read the same `target_price`/`trade_levels` fields. Verified (not assumed): `_trade_levels()` takes `target` directly as its `take_profit` value, and its own stop-loss-tightening logic already has a "surface the honest sub-1.5 risk/reward rather than fake the take-profit" fallback, so Risk/Reward now also honestly reflects weak signals as a side effect, no extra code needed.
- 8 new behavioral tests (real numeric assertions on `_estimate_target()`, not source-text checks), full backend suite 2177/2177 passing. No frontend changes.

## Product Integrity #019 — News & Sentiment Pipeline Freshness Fixes

**Status:** Deployed to production (commit `fa0afe3`) — live-verified against the real production news endpoint. See [Product Integrity #019](../Releases/Product-Integrity-019-News-Sentiment-Pipeline-Freshness-Fixes.md).

- User reported DIXON's News & Sentiment section showed only 4-8 month old articles and "Insufficient fresh company-specific news evidence" despite genuine same-day coverage existing. Also asked whether this affects the AI signal — **verified via code trace: it does not**, missing news evidence is excluded and its weight redistributed to technicals/fundamentals rather than defaulting to neutral or degrading confidence.
- Four independent, confirmed bugs found (not thin coverage): (A) Google News RSS query had no recency operator — ranked by relevance not date; (B) the company-relevance classifier required the full run-on company-name phrase including the trailing country word, so ordinary title-case headlines like "Dixon Technologies shares rally 5%" were excluded; (C) the Economic Times per-symbol RSS feed is dead (verified live — returns generic homepage HTML); (D) the Yahoo Finance RSS feed is deprecated (verified live — returns generic homepage HTML). C and D were silently contributing zero articles for every symbol, not just DIXON.
- Fixes: added `when:14d` to Google News queries (matching the existing freshness window); accepted the already-computed 2-word company-name prefix as an additional relevance-match path (ticker case-sensitivity deliberately untouched — an existing test protects it against a specific false-positive); removed both dead feeds.
- **Live end-to-end verified**, not just unit-tested: 3 DIXON articles now correctly classify as fresh + company-specific, all from the prior week.
- 10 new regression tests, all 22 pre-existing news-relevance tests still passing, full backend suite 2187/2187. No frontend changes.

## Product Integrity #020 — SEC EDGAR Facts Cache Memory Cap

**Status:** Deployed to production (commit `0af3dbd`). See [Product Integrity #020](../Releases/Product-Integrity-020-SEC-EDGAR-Facts-Cache-Memory-Cap.md).

- User noticed US Daily Picks hadn't refreshed in 54+ hours. Direct production DB read found a US job stuck `running` for ~5 hours (finalized as a separate direct data correction, not part of this release), and a prior US job that failed the day before with a confirmed Railway OOM.
- Root cause found: `sec_edgar_adapter.py`'s `_facts_cache` was the one cross-run cache in the prediction pipeline with no size cap or eviction, unlike `prediction_engine.py`'s `_pred_cache`/`_regime_cache` (already capped at 300 specifically to prevent OOM on Render's free 512MB tier). With the US universe at 400 symbols, a single run could grow this cache unboundedly, each entry holding a full multi-year SEC EDGAR companyfacts payload.
- Fix: added `_FACTS_CACHE_MAX = 300` (matching the already-proven-safe cap elsewhere) and a `_facts_cache_set()` helper that evicts the oldest entry when the cap is reached; `fetch_company_facts()` now writes through it instead of a direct dict assignment.
- 6 new tests (cap value, eviction order, 400-symbol simulated run, re-insert behavior, write-through regression guard), all 5 pre-existing SEC EDGAR test files re-verified passing (39/39), full backend suite 2193/2193. No frontend changes.
- Natural-run verification: a manually-triggered US Daily Picks run was used as the first real-world test of this fix (same day as deploy, since the automatic GitHub Actions cron had already fired once for the day before the fix landed).

## Product Integrity #021 — High Conviction Picks Filter

**Status:** Deployed to production (commit `37cf159`) — verified live on stocksense360.com. See [Product Integrity #021](../Releases/Product-Integrity-021-High-Conviction-Picks-Filter.md).

- User asked how to find Daily Picks with >85% AI confidence — there was no way to do this; picks could only be scanned manually per horizon tab.
- Added a "High Conviction Only (≥85%)" toggle to the Picks page, alongside the existing horizon tabs. Filters the current horizon's picks to `confidence >= 85` and sorts highest-first; composes with (doesn't replace) the horizon tabs. Distinct empty state when a horizon has picks but none clear the bar.
- Pure client-side view filter — no backend/ranking/confidence-computation change.
- 13 new tests (wiring + real behavioral assertions on the exact filter/sort logic, including the 84%/85% boundary), full frontend suite 334/334 passing, clean typecheck, clean production build. No backend changes.
- **Live-verified in browser**: toggle renders, activates with correct styling, filters/sorts correctly (confirmed against real India Long Term data — LUPIN 91%, NATIONALUM 88%), and the freshness-notice count correctly follows the filtered set.

## Product Integrity #022 — Risk-Based Position Sizing in Paper Trade

**Status:** Deployed to production (commit `0ef83a7`) — confirmed live via the deployed JS bundle. See [Product Integrity #022](../Releases/Product-Integrity-022-Risk-Based-Position-Sizing.md).

- Follow-up to a user question about why US paper trading had a 75.6% win rate but a net loss while India (61.4% win rate) was profitable. Pulling the user's actual closed trades showed the cause: a flat share count (e.g. "10 shares") carries wildly different dollar risk by stock price — the 3 largest US losses ($1,439/$604/$944) were all large-notional positions (10 shares of $1,189/$824/$751 stocks), dwarfing many small wins on cheap stocks.
- Added a risk-based quantity suggestion to the Paper Trade modal: sizes the position so a stop-loss hit costs ~1% of available virtual capital, capped at what the account can afford. Auto-fills Quantity (editable, same pattern as the existing AI stop-loss/target pre-fill) with a visible "risks ~$X (1% of $Y available)" hint.
- Pure client-side suggestion — no backend or trade-execution logic change; purely additive to the existing manual Buy flow.
- 13 new tests on the extracted pure sizing function, including a reproduction of the user's real MU incident confirming the fix would have suggested far fewer than the 10 flat shares that caused the actual loss. Full frontend suite 347/347 passing, clean typecheck, clean production build. No backend changes.

## Phase 1A.6 — Market Integrity Hardening and Database-Default Closure

**Status:** Deployed and verified live (2026-07-14). Write-boundary and schema-default closure is **COMPLETE**. Historical contamination repair is **NOT STARTED**. Canonical clean learning-dataset construction is **NOT STARTED**. Learning re-enablement is **NOT AUTHORIZED**.

- Commit `36d4b338377026ad7aa69014cf3efe4edc45a572` deployed to Railway (deployment `07ff8c6d`, `SUCCESS`/Online); live logs confirm the new market-integrity containment logic is active against real production traffic.
- `predictions.market` and `outcomes.market`'s database-level `DEFAULT 'IN'` was dropped on 2026-07-14T05:54:02Z under a separately authorized, evidenced production migration. Both columns remain `NOT NULL`. Pre/post row counts are identical for both tables — zero historical rows were changed, relabeled, or repaired by this action.
- Production learning remains quarantined: `production_learning_enabled: false`, `production_alpha_source: "fixed_academic_prior"`, `learning_dataset_version: "legacy-quarantined-2026-07-12"` — unaffected by the above.
- Full detail: [Phase 1A.6 architecture document](../Architecture/Phase-1A6-Market-Integrity-Hardening-and-Repair-Planning.md) (design/implementation/closure) and [Product Integrity #003](../Releases/Product-Integrity-003-Phase-1A6-Production-Migration-and-Natural-Run-Verification.md) (production evidence).
- Historical contamination repair, the unreconciled Phase 1A.5-vs-planner duplicate-count discrepancy, and canonical clean learning-dataset construction all remain separately gated, future, explicitly-authorized work — none of it began or was implied by the above.

## Release 13C — Recommendation Consolidation Observability

**Status:** Deployed — RCI remains disabled.

- Aggregate RCI composition-success and fail-open counters are deployed for controlled operational observation.
- Counters reset on service restart and are not per-symbol, per-user, or persistent telemetry.
- Counter availability does not itself prove RCI correctness or authorize activation.

## Release 13D — Recommendation Consolidation Activation Readiness

**Status:** Runbook complete — activation not approved.

- `RCI_LIVE_STOCK_ANALYSIS_ENABLED` remains disabled.
- The existing Evidence Summary frontend consumer is already deployed.
- Any future RCI activation is user-visible on the Stock Detail page; it is not a backend-only dark launch.
- RCI activation, Daily Picks validation, and scheduler enablement remain separate approval decisions.

## Release 14B — Debug Endpoint Security Hardening

**Status:** Deployed — protected-endpoint runtime verification passed.

- `/api/predictions/debug/state` requires a configured non-empty `PICKS_SECRET` and a matching non-empty `X-Secret` header.
- Read-only negative-path verification confirmed that missing, blank, and deliberately incorrect secret values fail closed with the generic `401` response `{"detail":"Invalid secret"}`.
- Read-only authenticated verification confirmed HTTP `200` and the approved aggregate-only response shape: operational counts, cache-age summary, thread count, and RCI observability counters only.
- Verification confirmed no raw cache identifiers, in-flight identifiers, symbol/market/horizon identifiers, background-log content, or exception text are exposed.
- Verification did not change Release 12B validation, RCI activation, Daily Picks scheduler state, configuration, or deployment state.

## Operational Safety Rules

- Daily Picks generation itself is live for both markets, with recorded India and US natural-run evidence (2026-07-14) — see Release 12B above. This does **not** authorize skipping evidence-based validation for future generation-logic changes, and does **not** resolve the separately-tracked GitHub Actions scheduler-timing reliability issue (see Release 12B).
- No RCI feature-flag change without explicit approval.
- No production-status documentation may claim a validation passed without recorded evidence.
- Historical test totals remain historical snapshots. Current test status must be taken from the latest validated release or CI evidence.
- Intelligence Engine V1's runtime-validation criteria (IN and US shadow telemetry at `source_commit = bb5d3cf` or later, with `tradability`/`liquidity`/`data_confidence` all `available = true`) were met on 2026-07-08 — evidence recorded in the Runtime Validation Closure record. Formal closure of the initiative remains a separate, explicit decision.
- No historical market-integrity contamination repair, backfill, or relabeling may be executed without a separate, explicit authorization — see Phase 1A.6 above. Production learning remains quarantined (`production_learning_enabled: false`) until a canonical clean learning dataset is separately constructed and validated.
