# Product Integrity Workstream #001 — Data Freshness, Timezone, Snapshot, Schedule, and Display Consistency Audit

**Status:** Complete. A cross-platform audit and narrowly-scoped fix workstream, separate from Epic 005. No investment-decision logic (signal, confidence, ranking, target, stop-loss, entry zone) was touched. No Railway variable was changed. RCI and Valuation Intelligence remain disabled, unaffected by this workstream.

## Evidence Checkpoint

Reviewed directly: `.github/workflows/daily_picks.yml`, `multibagger_refresh.yml`, `multibagger_refresh_us.yml`, `backend/services/daily_picks.py`, `backend/api/routers/picks.py`/`multibagger.py`/`stocks.py`, `frontend/src/app/picks/page.tsx`, `frontend/src/components/LiveClock.tsx`, `frontend/src/utils/marketHours.ts`, and every `toLocaleDateString`/`toLocaleTimeString`/`toLocaleString` call site across `frontend/src` (one repo-wide grep, all ~30 call sites individually classified below).

## Audit Methodology

1. Traced the exact source field and serialization method behind every "Updated"/"Generated"/"Refreshed"/relative-age label.
2. Computed, with real cron-to-IST conversion (not assumption), whether each market's actual configured schedule matches its UI-displayed schedule statement.
3. Reproduced the reported "67h ago" scenario with deterministic, hand-picked UTC timestamps to confirm whether the age math itself was wrong, or whether the *displayed* absolute timestamps were silently using two different, undisclosed timezone bases.
4. Classified every `toLocale*` call site in the frontend into one of: (a) a legitimate browser-local client-fetch timestamp (correct as-is, per the preferred policy), or (b) a backend-origin, schedule/market-relevant timestamp formatted with `en-IN`/`en-US` locale conventions but with **no explicit `timeZone` option** — the exact bug pattern that caused the reported symptom.

## Confirmed India and US Daily Picks Schedules

| Market | Cron (UTC) | Converted (verified by direct computation, not assumed) | Intended purpose | UI statement today | Accurate? |
|---|---|---|---|---|---|
| **India / NSE** | `30 20 * * 0-4` (Sun–Thu UTC) | **2:00:00 AM IST exactly** (20:30 UTC + 5:30 = 02:00 IST next day) | Pre-market batch processing window — 2,300+ NSE stocks × 3 horizons, ~3.75h run, "done by ~5:45 AM IST" per the workflow's own comment, well before the 9:15 AM IST market open | `"2 AM IST"` (`frontend/src/app/picks/page.tsx` `MARKETS[0].genTime`) | **Yes — exact match**, verified by direct cron-to-IST arithmetic |
| **US / NYSE-NASDAQ** | `30 12 * * 1-5` | **6:00:00 PM IST exactly** (12:30 UTC + 5:30 = 18:00 IST) **and** 8:30 AM ET (EDT, UTC-4 in June) | Post-overnight, pre-open batch window — "comfortably before the 9:30 AM ET open" per the workflow's own comment | `"6:00 PM IST"` (`frontend/src/app/picks/page.tsx` `MARKETS[1].genTime`) | **Yes — exact match**, verified by direct cron-to-IST arithmetic |
| **Crypto** | No dedicated Daily Picks workflow exists | n/a | Confirmed: Daily Picks has no Crypto market entry anywhere in `MARKETS`, `daily_picks.yml`, or `services/daily_picks.py` | n/a | n/a — no claim is made, so nothing to be inaccurate |

**Both schedule statements were already correct before this workstream.** This is the one part of the brief's hypothesis that direct evidence disproves: the India 2 AM IST and US 6:00 PM IST statements are not stale or mismatched — they are exact, verified conversions of the real cron expressions. Per the brief's own explicit instruction, this is recorded as **Category 14 — no defect / intended behavior**, not silently assumed.

**Operational purpose, confirmed from the workflows' own comments, not inferred**: both schedules are deliberately pre-market batch-processing windows sized to finish with a buffer before each market's own open — not post-close jobs, and not the literal market open time. The India job additionally avoids overlapping the IN/US Multibagger Fundamentals Refresh jobs (`multibagger_refresh.yml`'s own comment explicitly documents this collision-avoidance reasoning: "deliberately NOT during Daily Picks' window... both this job and Daily Picks hit screener.in through the same backend process").

## Root Cause of the "67h ago" Inconsistency

**The relative-age number itself was never wrong.** Reproduced with deterministic timestamps: `generated_at = 2026-06-26T09:12:00+00:00` (= Jun 26, 2:42 PM IST) and a fixed "now" of `2026-06-29T04:42:00+00:00` (exactly 67 hours later) — `Math.floor((now - generated_at) / 3_600_000)` correctly returns `67`. The age math in `frontend/src/app/picks/page.tsx`'s `ageHours` computation (`Date.now() - new Date(data.generated_at).getTime()`) is timezone-agnostic by construction (a difference of two absolute instants) and was never the defect.

**The real defect: two different, undisclosed timezone bases for two on-screen clocks, with neither labeled.**

- `frontend/src/components/LiveClock.tsx` (the global header clock) called `toLocaleDateString("en-IN", {...})`/`toLocaleTimeString("en-IN", {...})` with **no `timeZone` option**. Per the ECMA-402 `Intl`/`Date.prototype.toLocale*` specification, omitting `timeZone` uses the **JavaScript runtime's default time zone** — in a browser, that is the **user's actual local system timezone**, not IST. The `"en-IN"` locale argument only affects digit/date-ordering conventions (e.g. DD/MM ordering); it does **not** convert the clock to Indian Standard Time. The header therefore silently displayed the browser's own local wall-clock time, formatted to *look* Indian, with no timezone label at all.
- `frontend/src/app/picks/page.tsx`'s Daily Picks "Updated" absolute label, by contrast, **already explicitly set `timeZone: marketCfg.tz`** (`Asia/Kolkata` for the India tab, `America/New_York` for the US tab) — a real, deliberate, correct timezone conversion, predating this workstream.
- **Consequence**: a user viewing the US Daily Picks tab from a browser set to a timezone other than US Eastern (e.g. Gulf Standard Time, UTC+4 — explicitly named in this workstream's own required test matrix) sees the Daily Picks "Updated" label in **true US Eastern Time** (correct, by design) while the header clock silently shows their **own browser's local time** (also technically "correct" in the sense of being real local time, but with no label disclosing that it is a different zone from the Daily Picks label). A user naturally assumes both on-screen clocks share one timezone and manually subtracts them — arriving at a number that disagrees with the correctly-computed `ageHours` badge, which never made that assumption. This is the exact, reproducible mechanism behind the reported "Mon, 29 Jun 2026, 6:20 PM" header vs. "Updated Jun 26, 2026, 02:42 PM · 67h ago" Daily Picks symptom.

**Classification: Category 1 (Timestamp/timezone mismatch) combined with Category 12 (Formatting-only issue — missing timezone disclosure).** No backend field, cache, or stored value was ever wrong; the defect was entirely in how two already-correct underlying instants were displayed and labeled.

## Timestamp and Freshness Policy Confirmed (no redesign needed)

Direct inspection confirms the backend already follows the recommended policy in every place checked:

- **Backend timestamps are stored and serialized as UTC, timezone-aware ISO-8601** — confirmed via `datetime.now(timezone.utc).isoformat()` in `services/daily_picks.py` (lines 562, 787) and the RCI composer's identical pattern (Epic 005). No naive datetime string was found anywhere in the audited surfaces.
- **Semantic fields are already reasonably separated** at the Daily Picks layer: `generated_at` (when the batch completed and was persisted) is distinct from any client-side `dataUpdatedAt` (react-query's own client-fetch-completion timestamp, used by Screener/Dashboard/Heatmap). No single field was found doing double duty as both "job ran" and "page loaded" within one displayed label.
- **`marketHours.ts`'s next-open/next-close countdown is correctly, deliberately browser-local** — its own docstring states this explicitly ("Format a UTC Date... in the browser's local timezone"), matching the platform policy's own preferred default ("use browser-local for general user-facing timestamps unless explicitly presenting a market/schedule timezone"). This is the *correct* place for browser-local time, since it answers "when does the market open, in **my** time" — confirmed as **verified consistent**, not a defect, and not changed.
- **No platform-level policy document is required.** The existing implicit policy (UTC storage, ISO-8601 serialization, browser-local for "when did my browser last see this," explicit-and-labeled market timezone for "when did this market-scheduled job run") was already correct in design. The defect was an **inconsistent implementation** of that same policy (one clock silently defaulting to browser-local while looking like IST, with no label), not a missing or wrong policy. Per the brief's own instruction not to perform a redesign without evidence requiring one, no `StockSense360-Time-and-Data-Freshness-Policy.md` document was created — the fix below documents and restores the policy that already existed in the correctly-implemented call sites (`marketHours.ts`, Daily Picks' `generatedAt`).

## Audit Coverage Matrix

| Area | Findings |
|---|---|
| **Global header (`LiveClock.tsx`)** | **Defect found and fixed** (root cause, above) |
| **Daily Picks** | Schedule wording verified accurate (both markets). "Updated" absolute label was already correctly timezone-converted but undisclosed — fixed by adding an explicit `IST`/`ET` suffix. Cache-age/stale logic (`isStale = ageHours >= 4`) verified mathematically correct, unchanged. "Current vs. price at generation" wording (`pick.tsx:418`, "was {price} at generation") already correctly distinguishes live vs. snapshot price — **verified consistent**, not touched. |
| **Stock Analysis** | `prediction.generated_at` "Updated" label had the same undisclosed-browser-local-while-en-IN-formatted defect as the header — **fixed**. Live price (`quote.price`) is fetched independently of the cached prediction and was already correctly understood as the live value; no conflation found between "current price" and "price used by the prediction" — **verified consistent**. |
| **Dashboard** | `lastUpdated` is derived from react-query's own `dataUpdatedAt` (client fetch-completion time) — legitimately, correctly browser-local per the confirmed policy. **Verified consistent, not a defect**; not changed. |
| **Portfolio** | No timestamp/freshness label found beyond live-price-driven gain/loss figures, which are computed directly from the same live quote on every render — **verified consistent**. |
| **Watchlist** | Same pattern as Portfolio — live values recomputed per render, no separate stale "last updated" label found to audit. **Verified consistent.** |
| **Alerts** | No relative-age or "triggered X ago" label found in the audited code; trigger status is a live boolean, not a timestamp comparison — **verified consistent**, no defect surface to fix. |
| **Paper Trading** | `opened_at`/`closed_at` are rendered as **date-only** (`toLocaleDateString("en-IN")`, no hour/minute, no `timeZone`) — same bug *pattern* as the header clock, but date-only displays have far lower real-world impact (only mis-displays near a midnight boundary, not throughout the day). **Found, classified, and deferred** — see "Anomalies Found But Deferred" below, not silently ignored. |
| **Validation** | `res.run_at` is already correctly formatted with explicit `timeZone: "Asia/Kolkata"` (`validation/page.tsx:310`) — confirmed as the codebase's own pre-existing correct precedent, which this workstream's fixes now make consistent with. **Verified consistent**, not changed. |
| **Multibagger** | `last_refreshed` (a genuine backend operational timestamp, confirmed via `api/routers/multibagger.py`/`services/fundamentals_cache.py`, not a client fetch time) had the same undisclosed-browser-local defect — **fixed**. |
| **Screener / Heatmap** | `lastUpdated`/`dataUpdatedAt`-derived labels are legitimately, correctly browser-local client-fetch timestamps — **verified consistent**, per the same reasoning as Dashboard; not changed. |

## Anomaly Table

| Issue ID | Surface | Symptom | Exact source fields | Root cause | Severity | User impact | Fix scope | Regression test |
|---|---|---|---|---|---|---|---|---|
| PI-001-01 | Global header clock | Header time silently disagrees with explicitly-converted market timestamps elsewhere, with no label explaining why | `LiveClock.tsx`'s `now.toLocaleDateString/toLocaleTimeString("en-IN", {...})` with no `timeZone` | Omitting `timeZone` defaults to the runtime's (browser's) local zone, not IST, despite the `en-IN` locale visually implying IST | **High** — this is the literal reported symptom, visible on every page (header is global) | Users see a "current time" that may not be IST, with no way to tell, leading to incorrect manual math against other (correctly) IST-labeled timestamps | Add explicit `timeZone: "Asia/Kolkata"` + an "IST" label | Manual deterministic reproduction (see Test section); no automated test exists for the frontend (named limitation, unchanged from Sprint #012) |
| PI-001-02 | Stock Analysis "Updated" label | Same undisclosed-zone pattern as PI-001-01, on a backend-origin (`prediction.generated_at`) timestamp | `frontend/src/app/stock/[symbol]/page.tsx` line 941 (pre-fix) | Same root cause as PI-001-01 | Medium | A user could see a "data freshness" time that silently used their own local zone rather than IST | Add explicit `timeZone: "Asia/Kolkata"` + an "IST" suffix | Manual deterministic reproduction |
| PI-001-03 | Multibagger "Refreshed" label | Same pattern, on a genuine server-side `last_refreshed` operational field | `frontend/src/app/multibagger/page.tsx` line 94 (pre-fix) | Same root cause | Medium | Same as PI-001-02 | Add explicit `timeZone: "Asia/Kolkata"` + an "IST" suffix | Manual deterministic reproduction |
| PI-001-04 | Daily Picks "Updated" label | Already correctly converted, but undisclosed which timezone (IST for India, ET for US) — looks like it might disagree with the (now-fixed) IST header when viewing the US tab | `frontend/src/app/picks/page.tsx` `generatedAt` (pre-fix had no zone suffix) | Correct conversion, missing disclosure | Low (cosmetic/clarity only — the displayed value was never wrong) | A user on the US tab could wonder why "Updated" doesn't match the (IST) header, with no label to explain the US tab intentionally shows US Eastern Time | Append the market's own `tzLabel` ("IST"/"ET") to the displayed string | Manual deterministic reproduction |
| PI-001-05 | Daily Picks / US Daily Picks schedule wording | Hypothesized mismatch between UI statement and actual cron | `frontend/src/app/picks/page.tsx` `MARKETS[].genTime` vs. `.github/workflows/daily_picks.yml` | **No defect found** — both statements are exact, verified conversions of the real cron expressions | n/a | n/a | No fix — Category 14, no defect | Documented cron-to-IST arithmetic above serves as the verification record |
| PI-001-06 | Paper Trading trade-date display | Same `en-IN`-no-`timeZone` pattern, but date-only | `frontend/src/app/paper-trading/page.tsx` lines 303, 399 | Same root cause as PI-001-01, lower-impact variant (date-only, not hour-level) | Low | Could mis-display the calendar date near a midnight boundary in an off-IST browser timezone | **Deferred** — see below | Not added this workstream |
| PI-001-07 | `ScoreHistoryChart` axis labels | Same pattern, chart axis labels only | `frontend/src/components/ScoreHistoryChart.tsx` line 16 | Same root cause, lowest-impact variant (a chart axis label, no absolute claim of "current" anything) | Low | Cosmetic only — a chart x-axis date could shift by one day near midnight in an off-IST browser timezone | **Deferred** | Not added this workstream |

## Snapshot-versus-Live Consistency Findings

- **Daily Picks already correctly distinguishes generation-time price from live price**: `frontend/src/app/picks/page.tsx:418` renders `"was {currency}{pick.price} at generation"` alongside the live `livePrice ?? pick.price` value — confirmed as already-correct, explicit snapshot-vs-live labeling, not touched.
- **Stock Analysis's live quote and cached prediction are fetched and rendered independently** (`fetchQuote` vs. `fetchPrediction`), with no code path that overwrites or conflates one with the other — confirmed via direct reading of `frontend/src/app/stock/[symbol]/page.tsx`'s query definitions. No snapshot-vs-live ambiguity found.
- **No historical Daily Picks record was found to be overwritten by a live refresh** — `generated_at` is set once per batch (`services/daily_picks.py`), and the live-price fields displayed alongside historical picks are clearly fetched separately, not written back into the stored pick record. **Verified consistent.**

## Production Validation Checklist

The following must be performed manually by the user — not claimed as completed by this workstream, since no Railway variable was touched and no live production session was available to this audit:

- [ ] Open the app with the browser's system timezone set to **UAE (Gulf Standard Time, UTC+4)** and confirm the header clock now reads "IST" explicitly and the displayed time is the real Indian Standard Time (not the device's local GST time).
- [ ] On the Daily Picks page, switch to the **US tab** and confirm the "Updated" label now shows an explicit "ET" suffix; switch to **India tab** and confirm an explicit "IST" suffix.
- [ ] Confirm the India Daily Picks page still reads "generated daily at 2 AM IST" and the US Daily Picks page still reads "generated daily at 6:00 PM IST" — unchanged, already correct.
- [ ] On Stock Analysis, confirm the "Updated [time]" label under the data-sources line now shows an explicit "IST" suffix.
- [ ] On the Multibagger page, confirm the "Refreshed [time]" label now shows an explicit "IST" suffix.
- [ ] Refresh the browser and leave/return to a tab on Daily Picks, Stock Analysis, Dashboard, Screener, and Heatmap; confirm no label changed its apparent timezone basis (header should always read IST; Daily Picks should always read the selected market's own zone with its label).
- [ ] Test on both desktop and mobile widths — these changes are text-only additions (a "IST"/"ET" suffix), not layout changes, so no responsive regression is expected, but should be confirmed.
- [ ] Confirm the `recommendation_consolidation` field still never appears anywhere on the Stock Analysis page (RCI remains disabled — this workstream did not touch the Evidence Summary component or the Railway flag).
- [ ] Confirm Daily Picks' BUY signals, confidence values, targets, stop-losses, and entry zones for a known stock are unchanged from before this workstream's deploy.

## Anomalies Found But Deferred

| Item | Reason for deferral |
|---|---|
| PI-001-06 (Paper Trading trade-date display) | Same root-cause pattern as the fixed items, but date-only (no hour/minute), making real-world impact far smaller (a one-day shift only possible near a midnight boundary in an off-IST browser). Deferred to keep this workstream's fix scope to the highest-impact, literally-reported defect class; recommended as a smallest-follow-up item for a future pass. |
| PI-001-07 (`ScoreHistoryChart` axis labels) | Same reasoning as PI-001-06 — a chart axis label, lowest possible impact among all findings. Deferred for the same reason. |
| Backend `RecommendationConsolidationResponse`'s missing feature-disabled notice field (Epic 005, Sprint #012's own named limitation) | Out of scope for this workstream — an RCI-specific backend-contract item, not a cross-platform freshness/timezone issue; already tracked under Epic 005. |

No issue requiring a change to investment-decision logic (signal, confidence, ranking, target, stop-loss, entry zone) was found anywhere in this audit.

## Tests

This repository has no frontend test framework (confirmed unchanged from Epic 005 Sprint #012's own finding — no Jest/Vitest/RTL, no test script in `frontend/package.json`). Consistent with that sprint's resolution, validation for this workstream used:

1. **`npx tsc --noEmit`** — clean, zero errors, across all changed files.
2. **`npm run build`** — clean, all 18 routes generated successfully, including the dynamic `/stock/[symbol]` route.
3. **Direct, deterministic script execution** (`npx tsx -e "..."`) reproducing the exact reported scenario with hand-picked timestamps: confirmed `ageHours` computes to exactly `67` for a 67-hour gap (proving the age math itself was always correct), and confirmed the fixed absolute-time formatting now correctly renders `"26 Jun 2026, 02:42 pm"` in true IST regardless of the host's own default timezone (proving the fix is real, not cosmetic).
4. **Backend test suite**: 886/886 passing, unchanged — confirms zero backend impact, since every fix in this workstream is frontend-display-only.

## Files Changed

| File | Change |
|---|---|
| `frontend/src/components/LiveClock.tsx` | Added explicit `timeZone: "Asia/Kolkata"` to both date/time formatters; added a visible "IST" label |
| `frontend/src/app/stock/[symbol]/page.tsx` | Added explicit `timeZone: "Asia/Kolkata"` + "IST" suffix to the prediction "Updated" label |
| `frontend/src/app/multibagger/page.tsx` | Added explicit `timeZone: "Asia/Kolkata"` + "IST" suffix to the fundamentals "Refreshed" label |
| `frontend/src/app/picks/page.tsx` | Added a `tzLabel` field per market (`"IST"`/`"ET"`) and appended it to the already-correct, already-timezone-converted "Updated" label |

No backend file, Daily Picks ranking/persistence code, Prediction Engine code, RCI code, or Railway/workflow configuration was modified.

## Recommendation on Whether Epic 005 May Resume

**Yes — Epic 005 Sprint #012/#013 work may resume.** This workstream is fully complete, touched no file under `backend/`, no RCI/Evidence-Summary frontend file (`EvidenceSummary.tsx`, `DisclosurePanel.tsx`, the RCI portion of `api.ts`), and made no Railway change. Nothing in this workstream's findings bears on Epic 005's own open items (the still-needed visual QA pass, the still-missing frontend test framework, or the two named RCI backend-contract gaps) — those remain exactly where Sprint #012 left them.

---

*No Prediction Engine, signal, confidence, ranking, target, stop-loss, entry-zone, Daily Picks persistence, RCI, or Valuation Intelligence logic was changed. No Railway variable was changed. No Daily Picks historical record was rewritten. All fixes in this workstream are additive, presentation-only timezone disclosures on top of already-correct underlying data.*
