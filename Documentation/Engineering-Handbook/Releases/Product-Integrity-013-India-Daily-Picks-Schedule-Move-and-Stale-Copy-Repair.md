# Product Integrity Workstream #013 — India Daily Picks Schedule Move (2:07 AM IST) and Stale-Copy Repair

**Status:** Implemented, tested, and locally committed. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

## 1. Trigger

A user question about whether the frontend's `"generated daily at 2 AM IST"` copy was still accurate, followed by an explicit request to move India Daily Picks generation to **2:07 AM IST** and update every affected statement.

## 2. What was found before making any change

- **The frontend's "2 AM IST" label was already stale, independent of this request.** The live cron in `.github/workflows/daily_picks_in.yml` was `56 21 * * 0-4` UTC (3:26 AM IST) as of commit `e20e686` (2026-07-09) — moved from an earlier ~2 AM IST schedule, but the frontend `genTime` label, `picks.py`'s `next_run` string/docstring, and a startup catch-up threshold in `main.py` were never updated to follow. This had been silently wrong for about a week.
- **"screened from 340 eligible NSE stocks" was not a bug.** That text is not hardcoded anywhere — it's a live template driven by the backend's actual `screened_from` API field (`frontend/src/app/picks/page.tsx:1010`), deliberately built that way (per an existing code comment: "real returned count only, never a hardcoded number"). The backend's *target* universe size is 400 (`_TARGET_UNIVERSE_SIZE`, `backend/services/daily_picks.py`); the actual screened count fluctuates run to run based on eligibility filters, and 340 was simply what one particular run produced. No code change was needed or made here.
- **A pre-existing functional drift, not just a copy issue**: `backend/api/main.py`'s startup catch-up logic (`_catchup_picks("IN", _IST, 2, 60)`) assumes generation should have started shortly after 2 AM IST. Since the real cron had drifted to 3:26 AM IST without this threshold following, a server restart between roughly 2:00 and 3:26 AM IST on a weekday would have triggered an unwanted early catch-up generation, racing the legitimate cron run (the atomic job-reservation path prevents actual corruption, but it's still an unintended duplicate-trigger condition).

## 3. What this release changes

### 3a. Schedule move

`.github/workflows/daily_picks_in.yml`: cron moved from `56 21 * * 0-4` (21:56 UTC / 3:26 AM IST) to **`37 20 * * 0-4`** (20:37 UTC / **2:07 AM IST**). Weekday range (`0-4`, Sun–Thu UTC = Mon–Fri IST market days) is unchanged. Workflow `name:` field updated to match.

### 3b. Copy repair (now correctly reflects the new schedule, not the old stale one)

- `frontend/src/app/picks/page.tsx:86` — `genTime: "2 AM IST"` → `"2:07 AM IST"`. This flows automatically into the two places that render it (`picks/page.tsx:1010` and `:1217`) — no separate edit needed there.
- `backend/api/routers/picks.py:60` — `next_run = "2 AM IST"` → `"2:07 AM IST"`.
- `backend/api/routers/picks.py:215` — docstring corrected from `"IN at 20:30 UTC (2 AM IST)"` to `"IN at 20:37 UTC (2:07 AM IST)"`.

### 3c. Startup catch-up threshold — comment corrected, no functional value change needed

`backend/api/main.py`'s `_catchup_picks("IN", _IST, 2, 60)` call already uses `trigger_hour=2`. Since the new cron (2:07 AM IST) lands only ~7 minutes after this hour-level threshold, the value itself remains appropriate — the ~86-minute drift that existed against the old 3:26 AM IST cron is resolved by the schedule move itself, not by changing this parameter. The stale comment (which had described "2 AM IST (the scheduled run time)" as current when it had actually drifted for a week) was rewritten to state the current reality explicitly, including why the remaining ~7-minute imprecision doesn't warrant adding minute-level granularity to the catch-up function: a restart inside that narrow window is already handled safely by the existing atomic job-reservation path (`try_reserve_daily_picks_job`), which arbitrates a race rather than causing corruption.

### 3d. Frozen-schedule tests updated deliberately

Two tests (`test_daily_picks_schedule_freeze.py::test_india_daily_picks_cron_unchanged`, `test_daily_picks_us_premarket_workflow.py::TestIndiaWorkflowUntouched::test_cron_unchanged`) assert the exact India cron string read from the live YAML file. Both were introduced by Product Integrity #010 specifically to prevent *accidental* drift from Multibagger-scoped work — not to prevent a deliberate, explicit scheduling decision like this one. Both were updated to the new cron value with a comment explaining the distinction, rather than treated as an unconditional freeze.

## 4. What was explicitly NOT touched

- Universe size / `screened_from` copy — confirmed already correct and dynamic; no change needed (see §2).
- The `.github/workflows/multibagger_refresh.yml` comment referencing the old IN start time, and the ~12 `Documentation/*.md`/`README.md` prose references to "2 AM IST" — explicitly out of scope for this pass per user direction (functional code only, not documentation prose).
- India Multibagger's own Saturday 3:00 AM IST schedule — unrelated weekday-vs-Saturday cadence, no collision with this change, not touched.
- Daily Picks scoring, ranking, universe selection, or any Product Integrity #011/#012 freshness-gate logic — this release only changes *when* generation runs, not what it does.

## 5. Tests

- `test_daily_picks_schedule_freeze.py` — 10/10 passed (both India-cron and all other frozen-schedule assertions).
- `test_daily_picks_us_premarket_workflow.py` — full file re-run clean after the second cron-string update.
- Full backend suite: **2157/2157 passed** (one unrelated test — `test_alpha_observations.py::...test_persistence_success_noop_and_failure_produce_deeply_equal_payloads` — failed once under full-suite ordering and passed cleanly both in isolation and on a full-suite re-run; pre-existing order-dependent flakiness, not caused by this change).
- Frontend suite: **271/271 passed**.
- Frontend visual verification: not performed via live browser render this pass — the primary worktree (which `.claude/launch.json`'s dev-server config is scoped to) must remain untouched, and the temp worktree wasn't pre-configured for a dev server. Verified instead via direct diff inspection (single-line label change, confirmed correct) plus the full passing frontend test suite, which includes this page's existing source-assertion test pattern.

## 6. Rollback

Revert the cron line, the three copy strings, the `main.py` comment, and the two test assertions back to their prior values. Purely a scheduling + copy change — no schema, no migration, no data-shape change.

## 7. Natural-run verification plan

The next India Daily Picks run should fire at 20:37 UTC (2:07 AM IST) instead of 21:56 UTC — verify via the GitHub Actions run history for `daily_picks_in.yml` and/or `generated_at` on `GET /api/picks/daily?market=IN` landing ~2:07 AM IST rather than ~3:26 AM IST. Also worth confirming no early/duplicate catch-up-triggered generation appears in Railway logs around the 2:00–2:07 AM IST window on the first day after deploy.
