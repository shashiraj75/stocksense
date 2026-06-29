# Product Integrity Workstream #001B — US Daily Picks Runtime Verification

**Status:** Complete. A forensic, evidence-first runtime investigation. One narrow, additive observability fix was applied after the root cause's outcome category was confirmed by direct, live production evidence. No schedule, cron, workflow, ranking, signal, confidence, target, stop-loss, entry-zone, RCI, or Valuation Intelligence logic was touched.

## Evidence Checkpoint

This investigation used **direct, live production evidence** wherever possible, not assumption: the production API itself (`https://stocksense-production-7e0d.up.railway.app`, the same URL `.github/workflows/daily_picks.yml` targets) was queried read-only, multiple times, during this session. `gh` CLI is confirmed not installed/authenticated in this environment (reconfirmed, consistent with every prior sprint in this engagement) — **GitHub Actions run history and logs could not be directly inspected**, and Railway's own logs/dashboard were not accessible from this environment either. Both are named explicitly as genuine, unresolved observability gaps below, not guessed around.

### A. Scheduled Run — confirmed from code

`.github/workflows/daily_picks.yml`:
- **US job**: `cron: "30 12 * * 1-5"` → **12:30 UTC, Monday–Friday (UTC weekdays)**.
- Converted, by direct arithmetic (not assumed): 12:30 UTC = **18:00:00 IST exactly** and **08:30 ET exactly** (EDT, UTC-4, applicable in late June).
- Weekday check: **29 June 2026 is a Monday** (confirmed via direct date computation) — within the `1-5` (Mon–Fri) range, so the US batch **was** expected to fire today.
- No holiday exclusion exists in this workflow for either market (confirmed — `daily_picks.yml` has no calendar/holiday logic at all; that logic exists only in `frontend/src/utils/marketHours.ts`, used solely for the market-status display, not for gating this workflow).
- The workflow is enabled (it is the same, unmodified file already relied upon and verified working for the India job today — see below).

**Direct answer: yes, the US batch was expected to begin at 6:00 PM IST / 8:30 AM ET on Monday, 29 June 2026.**

### B–D. Actual Trigger, Processing, and Persistence — confirmed via live production API

Queried directly, read-only, at **2026-06-29T14:52:05Z** and again at **14:54:00Z** (i.e., ~2h22m–2h24m after the US trigger time):

```
GET /api/picks/daily?market=US  → generated_at: "2026-06-26T18:42:25.966836+00:00"
GET /api/picks/status?market=US → {"market":"US","generating":false,"has_today":false,"last_error":null}
GET /api/picks/daily?market=IN  → generated_at: "2026-06-28T21:56:46.246406+00:00"
GET /api/picks/status?market=IN → {"market":"IN","generating":false,"has_today":true,"last_error":null}
GET /health                     → {"status":"ok","version":"1.0.0"}
```

**This is decisive, direct evidence, not inference from a screenshot.** Key findings:

1. **The persisted US record is unchanged from Friday, 26 June** (`2026-06-26T18:42:25 UTC`, which is **2:42:25 PM US Eastern Time** — confirmed by direct conversion, 18:42 UTC − 4h EDT = 14:42 ET — matching the screenshot's "Jun 26, 2026, 02:42 PM" exactly, since the frontend displays this field in `America/New_York`, the US tab's own market timezone).
2. **`has_today: false` for US** — confirmed via `picks_generated_today()`'s own logic (`backend/services/daily_picks.py:856-876`), which correctly compares `generated_at`'s date *in the US's own local trading-day timezone* (`ZoneInfo("America/New_York")`) against today's date in that same zone — read directly, no bug found in this check itself.
3. **`generating: false` and `last_error: None` for US** — no generation is currently running, and no in-memory crash was recorded.
4. **Crucially, `generate_picks()`'s own exception handler (`services/daily_picks.py:532-575`) ALWAYS writes a fresh `generated_at` timestamp (today's date) to the cache, even on a crash** — confirmed by direct code reading: `payload = {"generated_at": datetime.now(timezone.utc).isoformat(), "picks": {...empty...}, "error": str(e)}` is written to disk (and Postgres, if configured) inside the `except` block. **This means a crash inside `generate_picks()` would have left a Monday-dated, empty-picks, error-flagged record — not Friday's unchanged record.** Since the actual persisted record is byte-for-byte Friday's original (same exact microsecond-precision timestamp across two separate live queries), **`generate_picks("US")` was never invoked at all for Monday** — ruling out "started but crashed inside the function" as the cause.
5. **India's comparison run, on the same day, on the same Railway service, succeeded**: `has_today: true`, `generated_at = 2026-06-28T21:56:46 UTC` = **03:26:46 AM IST on 29 June** — close to, and consistent with, the documented "~3.75h run, done by ~5:45 AM IST" window for the 2:00 AM IST trigger. **This rules out a platform-wide outage or a global Railway failure today** — the same backend, same day, correctly ran and persisted the India batch. The defect is specific to the US trigger/run, not the whole system.

**Direct answer: the US batch did not run.** The available evidence cannot, from this environment, distinguish *why* between two remaining possibilities — both are documented honestly as an unresolved observability gap, not guessed:
- The GitHub Actions schedule did not fire, or its own health-check/trigger step failed before the `POST /api/picks/generate?market=US` request was ever sent or successfully delivered (e.g., a Railway cold-start response slower than the trigger curl's own 30s `--max-time`, or a transient network failure) — in which case the backend genuinely never received a trigger.
- The request was received but something prevented it from ever reaching `generate_picks()`'s own try block (e.g., a process-level crash between accepting the request and the background task executing) — considered less likely, since FastAPI `BackgroundTasks` callbacks are reliably invoked once a request is accepted, but not ruled out with certainty without Railway's own process logs.

**Without GitHub Actions run logs or Railway logs, this cannot be narrowed further from this environment.** This is the genuine missing-observability gap this workstream's fix (below) targets, per its own explicit instruction not to guess.

### E. API and Cache — confirmed via direct code reading

- **Endpoint**: `GET /api/picks/daily?market=US`, served by `backend/api/routers/picks.py:17-34`.
- **No API-layer cache exists in front of this endpoint** — it reads directly from `services.daily_picks.get_cached_picks(market)`, which (confirmed by reading `daily_picks.py`) reads from a per-market on-disk JSON cache file (and/or Postgres if `USE_POSTGRES=1`), not a TTL-based cache layer. There is therefore **no stale-cache-vs-fresh-database scenario to investigate here** — the API is returning the literal, currently-persisted record, which is genuinely still Friday's, not a cache artifact masking a fresher Monday record.
- **No CDN or Next.js server-side caching is involved** — this is a client-side `fetch` (via `useQuery`) directly against the FastAPI backend, confirmed by `frontend/src/app/picks/page.tsx`'s `queryFn: () => api.get(...)`.
- **Cache keys cannot mix India and US** — `get_cached_picks(market)` and the underlying cache file path are both explicitly keyed by `market` (confirmed: `_cache_file(market)` in `daily_picks.py`), and the live evidence above (IN ≠ US, correctly distinct values) directly demonstrates no cross-market contamination occurred.
- **Conclusion: this is not a Scenario 4 (job completed/persisted, but API served old data) situation** — the API is correctly, faithfully serving the actual, currently-persisted record. The record itself is what's stale, not the API's view of it.

### F. Frontend / Browser State — confirmed via direct code reading

- `frontend/src/app/picks/page.tsx`'s `useQuery` has `refetchInterval` of 5 minutes when idle (60s while `generating`), `staleTime: 55_000`, `refetchOnWindowFocus: false`. A page left open for the ~2h22m+ window between the scheduled trigger and the observed screenshot would have refetched roughly 26+ times in that window — **a stale, un-refreshed browser tab is not the explanation** here, since even a long-idle tab would have re-polled the same (genuinely unchanged) backend record many times over.
- A hard refresh would retrieve the same record, since the backend itself has not changed it — confirmed by the live API check above returning the identical Friday timestamp independent of any frontend caching behavior.
- **Conclusion: this is not a Scenario 5 (API returned new data, but browser showed old data) situation either** — there was no new data for the browser to have missed displaying.

## Timeline Reconstruction

| Event | UTC | IST | New York Time | Evidence Source | Result |
|---|---|---|---|---|---|
| Scheduled trigger (US) | 2026-06-29 12:30:00 | 18:00:00 | 08:30:00 | `.github/workflows/daily_picks.yml` cron, direct arithmetic | Expected to fire (Monday, within `1-5` range) |
| Actual job start | **Unknown — not directly observable from this environment** | — | — | No `gh`/Railway log access (named gap) | **Not confirmed to have occurred** |
| US processing began | **Unknown** | — | — | Same gap | **Not confirmed** |
| Data retrieval completed | **Unknown** | — | — | Same gap | **Not confirmed** |
| Picks generation completed | **Did not occur for Monday** | — | — | `generated_at` unchanged from Friday across two live checks | **Confirmed did not happen** |
| Persistence completed | **Did not occur for Monday** | — | — | Same | **Confirmed did not happen** |
| Last actual US persistence | 2026-06-26 18:42:25.966836 | 2026-06-27 00:12:25 | 2026-06-26 14:42:25 | Live `GET /api/picks/daily?market=US` | Friday's record, genuinely 67h+ old by the screenshot time |
| Cache invalidated/refreshed | n/a — no cache layer exists at this endpoint (direct DB/file read) | — | — | Direct code reading, `daily_picks.py` | No caching defect possible here |
| API first returned latest record | n/a — API has always faithfully returned whatever is persisted | — | — | Direct code reading | Not a cache/API bug |
| Browser screenshot time (reconstructed) | ~2026-06-29 13:50:00 | ~19:20:00 | ~09:50:00 | User's "approx. 6:20 PM UAE / 7:20 PM IST" statement, converted | Matches "67h ago" exactly (see below) |
| Live evidence-gathering query (this workstream) | 2026-06-29 14:52:05 / 14:54:00 | ~20:22 / 20:24 | ~10:52 / 10:54 | Direct `curl` against production, this session | US record still Friday's; IN record correctly Monday's |

**Direct answer: at the time the user viewed the page (~13:50 UTC, ~1h20m after the US trigger time), Monday's US Daily Picks should plausibly have already been visible** — the documented US run is described as "ready ahead of the 9:30 AM ET open" (13:30 UTC), itself only 1 hour after the 12:30 UTC trigger; by 13:50 UTC the batch should have been complete under normal operation. **It was not visible because it never ran**, not because of a timing/staleness illusion.

## Decision Tree — Selected Branch

**Scenario D — US batch did not start despite the schedule**, with the specific sub-cause unresolved due to a genuine, named observability gap (this environment cannot inspect GitHub Actions run history or Railway process logs). This conclusion is reached by elimination, with direct evidence ruling out every other scenario:

- **Not Scenario 1's "cron timezone misunderstanding"** — the cron's UTC-to-IST/ET conversion is exact and correct, confirmed by direct arithmetic; the schedule itself is not misconfigured.
- **Not Scenario 2 (triggered but did not complete inside `generate_picks`)** — ruled out directly: the function's own crash-handler always writes a fresh, today-dated record, even on failure, and no such record exists.
- **Not Scenario 3 (completed but did not persist)** — same reasoning; "completed" implies `generate_picks` ran to either its success or except path, both of which write *something* dated today.
- **Not Scenario 4 (persisted, but API served old data)** — no cache layer exists at this endpoint; the API faithfully reflects what's actually persisted.
- **Not Scenario 5 (API fresh, browser stale)** — the query's own refetch interval would have caught a real update within 5 minutes; 2h22m+ of staleness cannot be explained by frontend polling behavior.
- **Not Scenario 6 (screenshot captured before completion)** — explicitly checked: the screenshot's reconstructed time (~13:50 UTC) is well after the documented expected completion window, and this workstream's own live re-check 1h+ later (14:52–14:54 UTC) shows the same unchanged Friday record, ruling out "just needed more time."

**Outcome Category: D.**

## Relative-Age Verification

| Record timestamp UTC | Browser instant UTC (reconstructed) | Browser timezone | Absolute label shown | Expected elapsed age | Displayed relative age | Correct? |
|---|---|---|---|---|---|---|
| `2026-06-26T18:42:25.966836+00:00` | `~2026-06-29T13:50:00Z` (from "~7:20 PM IST") | UAE (GST, UTC+4) per the user's own device | "Jun 26, 2026, 02:42 PM" (ET) | 67h 7m 34s → floors to **67h** | **67h ago** | **Yes — exact match** |

Direct answers:
1. **Was `67h ago` mathematically accurate at the actual screenshot instant?** **Yes.** Recomputed directly from the real, live-confirmed `generated_at` (`2026-06-26T18:42:25.966836+00:00`) against the reconstructed screenshot instant (~13:50 UTC, 29 June): elapsed = 67h 7m 34s, and `Math.floor(...)` over hours correctly yields `67`.
2. **Was the relative label stale because the page remained open?** **No evidence of this** — even a long-open tab would have re-polled the unchanged backend record every 5 minutes; the displayed value was a faithful, current reflection of the real, unchanged `generated_at`, not a frozen client-side snapshot.
3. **Did the absolute timestamp and relative age use the same record field?** **Yes** — both `generatedAt` (the formatted absolute string) and `ageHours` are computed directly from `data.generated_at` in the same render (`frontend/src/app/picks/page.tsx:647-651` and `:685`), confirmed by direct code reading.
4. **Did the header and Daily Picks page use different timezones?** **Yes, by design, and now disclosed** — the Daily Picks "Updated" label for the US tab correctly shows US Eastern Time (the US market's own zone); the global header (post-Product-Integrity-Workstream-#001) correctly shows IST. These are deliberately different, labeled zones for different purposes — not a bug, and not the cause of the underlying staleness.
5. **Was the underlying record correctly refreshed, or still genuinely old?** **Genuinely, confirmedly old.** The US Daily Picks record was, and — as of this workstream's own live re-check — still is, Friday's data. **This is the central finding of this workstream: Product Integrity Workstream #001's timezone-disclosure fix was correct and necessary, but it is not, and was never claimed to be, proof that the underlying data had actually refreshed.** That distinction is exactly what this follow-up investigation was tasked to verify, and the evidence confirms the data genuinely had not refreshed — a separate, real defect from the earlier timezone-formatting one.

## Root Cause

**The US Daily Picks batch did not execute on Monday, 29 June 2026, despite being correctly scheduled to do so at 12:30 UTC (6:00 PM IST / 8:30 AM ET).** The exact mechanical reason (GitHub Actions trigger failure vs. a delivery failure to Railway vs. some other pre-`generate_picks()` failure) **cannot be determined from this environment**, because this environment has no `gh` CLI authentication and no Railway log/dashboard access — a genuine, named observability gap, not a guess dressed up as a conclusion. India's same-day, same-infrastructure success rules out a platform-wide cause.

## Fix Applied

Per the brief's own explicit instruction ("If the missing evidence is due to insufficient logs or observability, do not guess. Recommend the smallest safe observability improvement"), **one narrow, additive fix was made**:

1. **`backend/services/daily_picks.py`**: added `_last_trigger_received_at: dict[str, str | None]`, an in-memory, per-market timestamp (mirroring the existing `_generating`/`_last_error` pattern already used in this exact file).
2. **`backend/api/routers/picks.py`**: `POST /api/picks/generate` now records `_last_trigger_received_at[market]` **synchronously, the instant a valid (correctly-secreted) request is accepted — before the background task runs**. `GET /api/picks/status` now exposes this field.
3. **A one-line, no-behavior-change comment fix**: `trigger_generation`'s own docstring previously said "US at 13:00 UTC (9 AM ET)" — confirmed, by direct comparison with the actual `daily_picks.yml` cron (`30 12 * * 1-5` = 12:30 UTC), to be a stale, incorrect comment (a documentation drift, not a functional bug, since the docstring is never executed). Corrected to "12:30 UTC (~8:30 AM ET)" to match the real, unchanged schedule.

**Why this is the right scope**: this does not fix the underlying trigger-delivery problem (which cannot be diagnosed without GitHub Actions/Railway log access this environment doesn't have), and it does not change the schedule, cron, or any investment logic. It gives the **next** occurrence of this exact ambiguity a direct, three-way answer that today's evidence had to reconstruct indirectly: if `last_trigger_received_at` is set and recent but `generated_at` is stale, the failure is *after* the backend accepted the request (inside or around `generate_picks()`, and — combined with `_last_error` — likely diagnosable); if `last_trigger_received_at` itself is stale or `None` at the expected time, the failure is *before* the backend ever saw the request (a GitHub Actions/network/Railway-availability issue, out of this backend's own visibility, but now distinguishable rather than indistinguishable from the first case as it was before this fix.

**This fix has a known limitation, disclosed honestly**: it is in-memory only, like the `_generating`/`_last_error` fields it mirrors, and will not survive a Railway process restart. A durable, persisted (file or Postgres) version is the natural future follow-up if this exact ambiguity recurs and the in-memory version turns out to have been lost to an intervening restart.

## Tests / Validation Performed

- **3 new regression tests** added (`backend/tests/regression/test_daily_picks_trigger_observability.py`): confirm a valid trigger records `last_trigger_received_at` synchronously (independent of whether the mocked background generation ever runs); confirm an invalid secret does **not** record a trigger (preserving the existing auth boundary); confirm `/api/picks/status` correctly exposes the new field.
- **Full backend suite**: 889/889 passing (886 pre-existing + 3 new), confirming zero regression to any existing behavior, including the pre-existing Daily Picks/auth/rate-limiting suites.
- **No frontend code was changed** by this workstream's fix — no frontend test/build/lint run was required.
- **Live production read-only verification** (the core evidence-gathering method of this entire workstream): `GET /health`, `GET /api/picks/daily?market=US`, `GET /api/picks/daily?market=IN`, `GET /api/picks/status?market=US`, `GET /api/picks/status?market=IN` — all read-only GET requests against the public production URL already targeted by the existing GitHub Actions health-check step; no POST, no state-changing request, no secret was used or required for any of these checks.

## Production Validation Checklist

The following must be confirmed by the user/operator going forward — not claimed as already performed by this workstream:

- [ ] After the next scheduled US run (tomorrow, 12:30 UTC), check `GET /api/picks/status?market=US` — confirm `last_trigger_received_at` is now populated with a recent timestamp and `has_today` becomes `true` shortly after.
- [ ] If `has_today` remains `false` again tomorrow but `last_trigger_received_at` IS recent, the failure is now confirmed to be inside/after the backend's own request handling — check Railway logs around that exact timestamp for a stack trace, and check `last_error` for that market.
- [ ] If `last_trigger_received_at` itself remains stale or unset at/after 12:30 UTC tomorrow, the failure is confirmed to be before the backend ever received the request — check the GitHub Actions run history for the `generate-picks-us` job directly (`Actions` tab, filter by workflow "Daily Stock Picks").
- [ ] Confirm `RCI_LIVE_STOCK_ANALYSIS_ENABLED` and both Valuation Intelligence kill switches remain unset/disabled in Railway (unaffected by this workstream).
- [ ] Confirm no Daily Picks ranking, signal, confidence, target, stop-loss, or entry-zone value differs from before this workstream's deploy for a known stock.
- [ ] Confirm the India schedule and its own UI statement ("2 AM IST") remain unchanged — this workstream did not touch the India path at all (its run succeeded throughout this investigation).

## Confirmation of Unchanged Logic

- **No cron expression, workflow trigger, or schedule was changed** — `.github/workflows/daily_picks.yml` is byte-for-byte unmodified by this workstream.
- **No Railway variable was changed.**
- **RCI and Valuation Intelligence remain exactly as left by Epic 005/Product Integrity Workstream #001** — neither flag was touched, and no file under `frontend/src/components/EvidenceSummary.tsx`/`DisclosurePanel.tsx` or the RCI portion of `api.ts` was modified.
- **No Daily Picks ranking, ranking_alpha, ranking logic, ​signal, confidence, target, stop-loss, entry-zone, or risk/reward calculation was modified** — confirmed by the diff being limited to: a new in-memory dict and its synchronous write in the trigger endpoint, a new field in the status response, a docstring correction, and one new test file.
- **No Daily Picks historical record was rewritten** — the stale Friday US record was left exactly as-is; this workstream observed it, did not alter or delete it, and did not force-trigger a new generation run as a workaround.
- **The India schedule, its UI wording, and its own successful Monday run were not touched.**

---

*This workstream found a genuine runtime defect (the US batch did not execute as scheduled) but, per its own explicit evidence-first mandate, did not guess at the underlying GitHub Actions/Railway-level cause without log access it does not have. The fix applied is a minimal, additive observability improvement that will make the next occurrence of this exact ambiguity directly diagnosable, not a fix to the underlying trigger-delivery mechanism itself, which remains an open item pending GitHub Actions/Railway log access.*
