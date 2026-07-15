# Product Integrity Workstream #007 — US Premarket Finalizer Moved to 6:00 AM ET

**Status:** Implemented, tested, and locally committed. Push/deploy is subject to the pre-push restart-safety gate documented separately in this same turn's report — see that report for the gate result.

**Scope note:** this is a narrowly scoped schedule change to the Daily Stock Picks — US Premarket Finalizer, plus the minimal, proven-necessary base-schedule correction it depends on. It does not touch Daily Picks scoring, ranking, or persistence logic; does not touch Phase 1A/1A.3 outcome or backfill code; and does not touch GPI-0 (the validation/performance integrity hold, which remains enabled).

## 1. Reason for the change

The US Premarket Finalizer (`daily_picks_us_premarket.yml` → `premarket_finalizer.py`) previously targeted ~7:35 AM America/New_York, with a 7:30-9:00 AM ET backend acceptance window. This phase moves the target to **6:00 AM ET**, making it the authoritative finalization attempt, while keeping the required sequencing intact: the US Pre-Open base generation (`daily_picks_us.yml`) must still complete and durably persist before the finalizer can do anything — the finalizer never generates a base itself, it only reviews an already-persisted one (`validate_base_for_finalization()`, unchanged by this phase).

## 2. Old vs. new schedule and backend window

| | Old | New |
|---|---|---|
| Finalizer target (local) | ~7:35 AM America/New_York | ~6:00 AM America/New_York |
| Finalizer cron (EDT candidate) | `35 11 * * 1-5` (11:35 UTC) | `0 10 * * 1-5` (10:00 UTC) |
| Finalizer cron (EST candidate) | `35 12 * * 1-5` (12:35 UTC) | `0 11 * * 1-5` (11:00 UTC) |
| Backend acceptance window (`in_premarket_window()`) | 7:30-9:00 AM ET | 6:00-7:30 AM ET |
| US base workflow cron | `0 4 * * 1-5` (04:00 UTC) | `0 6 * * 1-5` (06:00 UTC) — see §5 |

Boundary behavior of the new window, verified by test:

- 5:59:59 AM ET → rejected
- 6:00:00 AM ET → accepted
- 7:30:00 AM ET → accepted
- 7:30:01 AM ET → rejected

## 3. EDT/EST cron mapping

Both fixed-UTC candidates are kept (GitHub Actions cron is UTC-only and does not observe US DST):

- EDT (Mar-Nov): `10:00 UTC` → `6:00 AM America/New_York`
- EST (Nov-Mar): `11:00 UTC` → `6:00 AM America/New_York`

Whichever candidate is NOT the active DST offset on a given day lands one hour later in ET terms (e.g. during EDT, the EST candidate lands at 7:00 AM ET) — still inside the 90-minute window, exactly as the prior (2026-07-13) design already relied on. The backend's exact-base idempotency guard (`_check_existing_finalization`, unchanged) — not window exclusivity — is what prevents the second candidate from being a duplicate finalization; this behavior is unchanged from before, only retargeted to the new window.

## 4. Base-first sequencing

Unchanged and unweakened: `finalize_premarket()` never generates, scans, or scores the US universe. It requires an already-persisted payload with a `source_job_id` that resolves to a `daily_picks_jobs` row with `status = "completed"`, non-null `completed_at`, and non-null `persisted_picks_timestamp` (checks G-K, `_validate_source_job_state`, byte-for-byte unchanged by this phase). If the base workflow hasn't run, is still running, or failed, the finalizer fails closed with an explicit machine-safe reason code and zero provider/persistence calls — this contract predates this phase (Daily Picks Scheduler Remediation Phase 1A) and is not touched here beyond the window retargeting.

## 5. EST base-date compatibility finding — required base-schedule correction

**Decision: base schedule required a minimal DST correction (not compatible as-is).**

The prior base schedule (`04:00 UTC`, Mon-Fri UTC weekday) is incompatible with the finalizer's same-ET-calendar-day provenance check during EST:

- **EDT**: `04:00 UTC` on a UTC-Monday → `00:00 AM ET Monday` — same calendar date. Fine.
- **EST**: `04:00 UTC` on a UTC-Monday → `11:00 PM ET *Sunday*` — a full calendar date **earlier** than the intended trading day.

Since `validate_base_for_finalization()`'s check E requires `generated_at.astimezone(ET).date() == now_et.date()`, a base generated under the old EST schedule would be tagged with Sunday's ET date while the finalizer runs Monday ET morning — **every EST-season base would be rejected as `skipped_stale_base`, regardless of any finalizer-only schedule change.** This was confirmed by direct computation (not assumed):

```
EST Monday example: UTC=2026-01-12T04:00:00+00:00 (Monday) -> ET=2026-01-11T23:00:00-05:00 (Sunday)
```

**Correction applied:** the base workflow moved from `04:00 UTC` to `06:00 UTC`. Verified in both offsets:

- EDT: `06:00 UTC` → `02:00 AM ET`, same calendar date, ~4 hours before the 6:00 AM ET EDT finalizer candidate.
- EST: `06:00 UTC` → `01:00 AM ET`, same calendar date, ~5 hours before the 6:00 AM ET EST finalizer candidate.

Both offsets land on the correct New York market date with several hours of execution buffer, and India's schedule (`daily_picks_in.yml`) is untouched.

**Incidental related finding, not separately fixed in this phase:** `services.daily_picks.picks_generated_today("US")` compares `generated_at.astimezone(ET).date()` against today's ET date the same way `validate_base_for_finalization()` does. The old `04:00 UTC` schedule would have made this function under-report "already generated today" during EST for the identical reason — the `06:00 UTC` correction above resolves this as a side effect, since it's the same root cause, but this was not separately tested or targeted as its own fix.

## 6. Fail-closed behavior

Unchanged from Daily Picks Scheduler Remediation Phase 1A, retested against the new window boundaries: a missing base, empty picks, wrong market, missing/malformed/naive `generated_at`, a stale (prior-ET-day) base, a missing `source_job_id`, an unreadable durable job-state lookup, a not-found source job, a source job belonging to another market, a queued/running/failed/interrupted/expired source job, a completed job missing `completed_at` or `persisted_picks_timestamp`, and partial/malformed/mismatched finalization-marker pairs all continue to make **zero provider calls and zero persistence calls**, per the existing machine-safe outcome codes (`_OUTCOME_STATUS_MAP`). No precondition failure was weakened or silently converted to success.

## 7. Exact-base idempotency and duplicate-UTC-candidate behavior

Unchanged: idempotency is keyed on `(source_job_id, base_generated_at instant)`, never on calendar date alone. A second call for the exact same base (e.g. the "wrong" DST candidate landing inside the widened window on the same day) returns `already_finalized` with zero further job lookup, provider, or persistence calls. A genuinely new base under a new `source_job_id` is always eligible independently, regardless of what the prior base's markers say. New tests added (`TestReplacementBaseFinalizesIndependently`) explicitly cover a replacement base finalizing independently and a malformed inherited marker pair failing closed.

## 8. GitHub Actions delay risk

Unchanged risk profile: GitHub Actions scheduled triggers remain best-effort and can run late. The ~90-minute window width is preserved (just retargeted to 6:00-7:30 AM ET), absorbing the same class of delay the 2026-07-13 change was designed for. A pathological multi-hour delay (observed once on a different Daily Picks workflow — see Product-Integrity-001C) is still not covered — an accepted, disclosed residual risk, not a guarantee.

## 9. Frontend consistency changes

`frontend/src/app/picks/page.tsx` (the only frontend file with premarket-schedule content — confirmed via repository-wide search, no landing/dashboard/other page references this feature):

- US `genTime` (the Pre-Open **base** generation label) updated to `10:00 AM Dubai / 11:30 AM IST`, matching the corrected `06:00 UTC` base schedule — this label was never changed to "6:00 AM ET" and must not be, since that string is reserved for the separate Premarket Review stage.
- The base-generation timestamp badge is now explicitly labeled "Base generated" for US (was ambiguous "Updated" for both markets) — India's label is unchanged.
- The Premarket Review badge now renders unconditionally for US (previously hidden entirely whenever `premarket_status` was absent), with a truthful `"pending"` fallback showing "Premarket Review Pending · Scheduled for 6:00 AM ET" instead of silence.
- Completed/limited-data outcomes now display the actual `premarket_finalized_at` timestamp (previously read into state but never rendered anywhere).
- The page's descriptive text no longer claims the finalizer "runs before US market open" (vague, and could be misread as running before base generation) — it now states the review is scheduled for 6:00 AM ET and runs after the base picks complete.
- India renders none of this — the badge and copy are gated on `market === "US"` only, verified by test.

## 10. User-facing two-stage terminology

Implemented distinctly, per the required contract:

- **US Pre-Open Base Generation** — "Base generated `<actual timestamp>`" (never fabricated, always the real `generated_at`).
- **US Premarket Review** — "Premarket Review Pending · Scheduled for 6:00 AM ET" / "Premarket Review Completed · `<actual premarket_finalized_at>`" / "Premarket Review Completed (Limited Data) · `<actual timestamp>`" / "Premarket Review Skipped" / "Premarket Review Failed".

No permanent single UTC-to-ET conversion is hardcoded in the frontend copy — "Scheduled for 6:00 AM ET" is an America/New_York local-time target, correct year-round without a separate EDT/EST string; the backend (`premarket_finalizer.py`) owns the actual DST-aware UTC mapping via the dual cron candidates.

## 11. Rollback instructions

If this change needs to be reverted:

1. Revert `.github/workflows/daily_picks_us_premarket.yml`'s cron lines to `35 11 * * 1-5` / `35 12 * * 1-5`.
2. Revert `backend/services/premarket_finalizer.py`'s `in_premarket_window()` bounds to `7:30`/`9:00`.
3. Revert `.github/workflows/daily_picks_us.yml`'s cron to `0 4 * * 1-5` **only if** reverting item 1-2 together — reverting the finalizer schedule alone while leaving the base at `06:00 UTC` is safe (the base would just run slightly later in Dubai/IST local terms); reverting the base schedule alone while keeping the finalizer at 6:00 AM ET would reintroduce the EST same-day provenance failure documented in §5.
4. Revert `frontend/src/app/picks/page.tsx`'s `genTime`, badge, and copy changes.
5. Revert the four backend test files and the new frontend test file to their pre-change versions, or re-run this phase's own test suite against the reverted code to confirm it still passes for the old schedule.

No database migration, schema change, or data backfill is involved in this change — a code/config revert alone is sufficient; no data cleanup is required.

## Known limitations — explicitly not claimed as fixed by this phase

- **US Daily Picks OOM root cause** — unresolved, unrelated, out of scope. See the separate orphaned-job remediation and preflight work from earlier in this session.
- **GitHub Actions dispatch reliability** — best-effort scheduling delay is absorbed up to ~90 minutes by design (§8); a multi-hour pathological delay is not solved by this phase.
- **Quote timestamp limitations** — `price_gap_pct` still uses "latest available quote," not verified true premarket-tick data; unchanged by this phase.
- **Absence of true premarket volume, incremental news, sector movement, earnings-event risk, and abnormal volatility/spread risk** — still explicitly reported as missing inputs (`_ALWAYS_MISSING_INPUTS`), never fabricated; unchanged by this phase.
- **Phase 1A / 1A.3 outcome or backfill logic** — untouched by this phase.
- **GPI-0 (validation/performance integrity hold)** — remains enabled; this phase does not lift it, does not touch it, and does not claim any of GPI-0's own removal criteria are satisfied.
- **`backend/api/routers/picks.py`'s `next_base_run_hint`/`next_premarket_run_hint` strings and the `/premarket-finalize` endpoint docstring** — these still describe the OLD schedule (`04:00 UTC`/`8:00 AM Dubai`/`~7:35 AM`/`7:30-9:00 AM`). This file carried a pre-existing, unrelated staged change that this phase was explicitly instructed to preserve exactly — including not editing it at all — so these two informational strings and one docstring could not be updated in this phase. They do not affect the actual acceptance-window logic (which lives in, and was correctly updated in, `premarket_finalizer.py`'s `in_premarket_window()`); they are stale informational text only. Updating them is a small, separate, explicitly-flagged follow-up once that file's unrelated staged change is resolved.
