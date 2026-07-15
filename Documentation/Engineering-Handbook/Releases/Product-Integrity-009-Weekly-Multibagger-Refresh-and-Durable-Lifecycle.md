# Product Integrity Workstream #009 — Weekly Multibagger Refresh and Durable Lifecycle

**Status:** Implemented, tested, and locally committed. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

**Corrected by [Product Integrity #010](Product-Integrity-010-Multibagger-Production-Hardening-and-Legacy-Schema-Repair.md) (2026-07-16):** a direct production-database forensic audit found that this release's own schema migration never actually took effect for two objects — `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` are pure name-existence checks in Postgres, so the #008-era `CHECK (market = 'US')` constraint and the `WHERE status = 'running'`-only active-index predicate were both still live in production after this release deployed, meaning every India Multibagger job insert had been silently failing since this commit shipped. #010 also replaced the two-DST-candidate US cron design below with a single fixed cron, made job+lease reservation atomic, and added per-symbol staging with atomic cache promotion. This document's own content below is preserved unedited as the historical record of what #009 actually shipped and intended; it does not describe current production behavior.

**Scope note:** this is a complete conversion of the Multibagger full-universe fundamentals refresh from nightly (India) / daily-weekday (US) to weekly for both markets, together with correcting several defects #008 introduced or left unresolved (a fragile process-local "cooperative stop" coupling, no durable state for India, no transactional cross-workload exclusion, no orphan recovery). It does not touch Daily Picks scoring, ranking, universe selection, PredictionEngine logic, Premarket Finalizer decision/provenance logic, Phase 1A/1A.3, backfill tooling, outcome remediation, GPI-0, or India/US market separation.

## 1. Product decision and rationale

The full-universe fundamentals refresh (~2,300 NSE stocks via screener.in, ~5,300 US common stocks via yfinance) processes financial-statement data that changes quarterly, not daily. A nightly/daily full-universe scrape was unnecessary provider load, Railway resource pressure, rate-limit risk, and daily scheduler-collision surface for data that barely moves day to day. Moving to weekly reduces all of that while keeping the Multibagger Screen's freshness well inside a reasonable staleness bound for a long-term research screen (see §7).

No daily lightweight valuation/price-only refresh is implemented in this release — recorded here as a possible future enhancement only, not built.

## 2. Old vs. new schedule

| | Old | New |
|---|---|---|
| India cron | `0 17 * * 0-4` (17:00 UTC Sun-Thu, nightly) | `30 21 * * 5` (Friday 21:30 UTC = Saturday 3:00 AM IST, weekly) |
| US cron | `0 8 * * 1-5` (08:00 UTC Mon-Fri, daily, #008) | `0 7 * * 0` + `0 8 * * 0` (Sunday 07:00/08:00 UTC, DST candidates, weekly) |
| India durable state | None (process-local only) | `multibagger_refresh_jobs`, full lifecycle |
| US durable state | Running/completed/failed only (#008) | Full lifecycle: queued/running/completed/failed/interrupted/expired |
| Cross-workload exclusion | Ad-hoc `has_active_daily_picks_job_or_unknown()` check + "cooperative stop" | Transactional heavy-workload lease, symmetric for both directions |
| Weekly-period idempotency | N/A | `scheduled_period_key`, partial unique index |

## 3. India/US timezone mapping

**India** — no DST. Friday 21:30 UTC = Saturday 3:00 AM IST = Saturday 1:30 AM Dubai. The backend-enforced scheduled window is Saturday 2:30-4:30 AM IST (`services/multibagger_schedule.py::in_scheduled_window`).

**US** — DST-aware. GitHub Actions cron is UTC-only, so two fixed-UTC candidates fire every Sunday:
- EDT: 07:00 UTC = 3:00 AM ET
- EST: 08:00 UTC = 3:00 AM ET

The backend-enforced scheduled window is Sunday 2:30-4:30 AM America/New_York (`us_scheduled_window`), using `zoneinfo.ZoneInfo("America/New_York")` as the DST authority — the same pattern as the Premarket Finalizer (#007) and the US startup catch-up threshold (#008). During EDT, **both** UTC candidates land inside the window (3:00 AM and 4:00 AM ET are both within [2:30, 4:30)); during EST, only the 08:00 UTC candidate does (07:00 UTC = 2:00 AM ET, before the window opens). This asymmetry is expected and handled by weekly-period idempotency (§4), not by narrowing the window — see `test_multibagger_schedule.py`'s DST tests for the exact boundary proofs.

## 4. Weekly-period idempotency

`multibagger_refresh_jobs` gained `trigger_source` (`scheduled`/`manual`) and `scheduled_period_key` (the intended Saturday/Sunday's local date, `YYYY-MM-DD`). A partial unique index — `(market, scheduled_period_key) WHERE trigger_source = 'scheduled' AND status IN ('queued','running','completed')` — means:
- The first scheduled call for a period reserves it and starts a job.
- A duplicate scheduled call for the same period (the second DST candidate, or a GitHub Actions retry) gets `200 already_completed_for_period` — a safe no-op, no second job.
- A **failed** attempt does not hold the slot — a retry within the same week is allowed and will start a new job.
- **Manual** calls (`trigger_source=manual`) never set `scheduled_period_key`'s uniqueness gate at all — a manual run can never occupy or impersonate a scheduled period's success.

Period identity uses real local-date arithmetic (`datetime.astimezone(ZoneInfo(...)).date()`), not a UTC-date shortcut — verified directly against both DST offsets in `test_multibagger_schedule.py`.

## 5. Removal of the #008 "cooperative stop" coupling

`request_us_stop()`, `_stop_requested`, and `run_full_refresh(should_stop=...)` are fully removed — not present anywhere in the backend (verified by `test_no_stop_coupling_symbols_remain_anywhere_in_the_backend`). The Premarket Finalizer no longer references the `multibagger` module at all. US Daily Picks generation no longer requests or depends on Multibagger stopping. Weekly scheduling makes a real overlap between Daily Picks and Multibagger rare by design (very different local run times); the residual exceptional case (manual dispatch overlap, or a delayed scheduled run) is handled by durable mutual exclusion (§6), not by one job partially cancelling another — a partially completed refresh is never represented as a successful full refresh, and no job is ever force-killed mid-flight.

## 6. Heavy-workload arbitration

New `heavy_workload_leases` table: one row per currently-held lease, gated by a partial unique index on `(resource) WHERE released_at IS NULL` — the same proven atomic pattern as `daily_picks_jobs`/`multibagger_refresh_jobs`'s own active-row exclusion, applied here across two different job tables via a single shared lease table. `try_acquire_heavy_workload_lease()` is a single `INSERT ... ON CONFLICT DO NOTHING` — there is no separate check-then-start step for a caller to race against.

Resources: `US_YFINANCE_HEAVY`, `IN_SCREENER_HEAVY` — one per market, covering both directions:
- `POST /api/multibagger/refresh` acquires the lease before starting; on failure, returns `409 resource_busy` (retryable) and starts nothing.
- `POST /api/picks/generate` acquires the same lease (owner_type=`daily_picks`) after its own durable reservation succeeds; on failure, returns `409 resource_busy` and starts nothing.

Exactly one workload can hold a market's lease at a time; concurrent requests cannot both win (verified by the atomic INSERT semantics, not by application-level locking); different markets' resources are fully independent (`IN_SCREENER_HEAVY` and `US_YFINANCE_HEAVY` never block each other); a DB failure during acquisition fails closed (raises, treated as `503`, never silently proceeds as "no conflict"); the lease is released only on a confirmed terminal transition (`finally` block in both `_run_refresh_job` and Daily Picks' background task), and the release call itself is idempotent.

## 7. Durable lifecycle and orphan recovery

Lifecycle: `queued → running → (completed | failed | interrupted | expired)`. Heartbeat (`last_runner_heartbeat_at`) and progress (`processed`/`total`, `last_progress_at`) are recorded via an `on_progress` callback threaded through both refresh loops, fired every 100 symbols — the same cadence the existing print-based progress logging already used.

**Terminal-write-failure handling (§10 of the prompt):** `mark_multibagger_job_completed`/`mark_multibagger_job_failed` return `True` only if the durable write itself succeeded. `_run_refresh_job` logs a structured error and does **not** treat the job as resolved if a terminal write fails — the row is left in its prior state for the next reconciliation pass rather than silently reporting success.

**Orphan recovery:** `reconcile_stale_multibagger_jobs()` runs once at backend startup (never from a GET/request path — status endpoints never mutate lifecycle state). A `queued`/`running` row whose heartbeat has gone silent for more than 7 hours (longer than the ~5-6h full US refresh, the longest of the two) is reclassified `interrupted`, and any heavy-workload lease it still held is released in the same pass. A genuinely active job's heartbeat is always recent and is never touched. No row is ever deleted — historical rows, including any pre-#009 rows with `stopped_early` set, remain exactly as they are.

**Known, disclosed limitation:** reconciliation is startup-only, not continuous — a job orphaned mid-week by a crash without a subsequent restart stays `running` (and its lease held) until the next deploy/restart. Given Multibagger jobs are weekly and bounded (~5-6h max), this is a narrow window, but it is not eliminated. Building a continuous background sweep was judged out of scope for this release's "smallest safe protection" principle.

## 8. Row-Level Security

Both new tables (`heavy_workload_leases`) and the widened `multibagger_refresh_jobs` have `ENABLE ROW LEVEL SECURITY` in the schema — idempotent, safe to run on every startup, same pattern as every other table in `postgres_store.py`. No policies are added (none are needed — this backend connects via the `postgres` role, which has `BYPASSRLS` by default), so the only effect is closing the public PostgREST API's access to these tables while leaving this backend's own direct access unaffected. No existing RLS table was weakened, no destructive migration, no historical row was rewritten.

## 9. Status API contract

`GET /api/multibagger/status?market=IN|US` — all previously-existing fields (`market`, `running`, `last_summary`, `last_refreshed`) are preserved. New, additive, durable-first fields: `schedule_frequency` (`"weekly"`), `next_scheduled_refresh_hint`, `stale_after_days`, `durable_state_available`, `job_id`, `job_status`, `trigger_source`, `scheduled_period_key`, `processed`, `total`, `last_error`, `last_runner_heartbeat_at`, `last_progress_at`, `last_successful_refresh_at`, `is_stale`. When Postgres is enabled, `running`/`job_status` are sourced from the durable row, not the in-memory dict — a backend restart resets the in-memory dict to `running=False` regardless of the real state, but the durable row is unaffected, so the API never fabricates a false idle state after a restart. If the durable read itself fails, `durable_state_available=false` is reported rather than silently falling back to a fabricated healthy state.

## 10. Staleness contract

`stale_after_days = 9` (a 7-day weekly cadence plus a 2-day buffer to absorb one missed/delayed run without immediately alarming). Computed only from the most recent **completed** row (`get_last_successful_multibagger_refresh`) — a failed, interrupted, or partial run never advances the freshness clock. `is_stale=true` when no successful refresh has ever completed, or the most recent one is older than the threshold.

## 11. Frontend

`frontend/src/app/multibagger/page.tsx`: header schedule label changed from stale "10:30 PM IST"/"7:30 AM IST" nightly-era text to "Saturday 3:00 AM IST" / "Sunday 3:00 AM ET"; footer "Refreshed nightly" → "Refreshed weekly"; a new yellow stale-data banner renders when `status.is_stale`, and the existing "Last refreshed" chip turns yellow and appends "· data is stale" in that state; a distinct red "Last weekly refresh failed" chip renders when `job_status === "failed"`. `last_refreshed`/timestamps shown are always the real value from the API, never a fabricated "completed exactly on schedule" claim. No public force-refresh control exists on the page (verified by `multibagger-weekly-schedule.test.ts`).

## 12. Manual refresh contract

`workflow_dispatch` remains available on both workflows for exceptional, explicitly authorized runs. `trigger_source` is derived from `github.event_name` (`schedule` → `scheduled`, anything else → `manual`) and passed as a query param. Manual runs may fire outside the scheduled local window, still respect the heavy-workload lease and active-job uniqueness, still require the `X-Secret` header, and never set/impersonate `scheduled_period_key`'s success marker.

## 13. Rollback plan

1. Revert `.github/workflows/multibagger_refresh.yml`'s cron to `0 17 * * 0-4` and `multibagger_refresh_us.yml`'s to `0 8 * * 1-5`.
2. Revert `backend/api/routers/multibagger.py`, `backend/api/routers/picks.py`'s lease-acquisition additions, `backend/services/us_fundamentals_refresh.py`/`fundamentals_refresh.py`'s `on_progress` parameter, and `backend/services/multibagger_schedule.py` (delete).
3. Revert the `backend/api/main.py` startup reconciliation call.
4. The new `multibagger_refresh_jobs` columns and `heavy_workload_leases` table are additive (`ADD COLUMN IF NOT EXISTS` / `CREATE TABLE IF NOT EXISTS`) — safe to leave in place after a code revert; they simply stop being read/written. No migration or data cleanup required either direction.
5. Revert the frontend copy in `multibagger/page.tsx` and the type additions in `utils/api.ts`.
6. Revert the documentation changes in §14 below, or leave them (they are strictly more accurate than what they replaced regardless of deployed code version).

## 14. Documentation corrections

- `Documentation/STOCKSENSE_DOCUMENTATION.md` — "What it is" (Multibagger section) and the "Automation Workflows" reference section both corrected from nightly/daily to weekly, with the new cron values and lease/idempotency design.
- `Documentation/Engineering-Handbook/SSDS/SSDS-006-...md` and `SSDS-003-...md` — one-line corrections where each referenced "nightly" fundamentals refresh as an existing-pattern assumption.
- `backend/services/fundamentals_cache.py` — module docstring corrected.
- `Current-Release-Status.md` — new #009 entry; the #007/#008 entry annotated to note its Multibagger schedule and stop-coupling content is superseded.
- `Product-Integrity-008-...md` — a superseding note added at the top; the historical body is preserved unedited, per SES-006 §11.
- **Deliberately left unchanged as historical evidence:** `Sprint-001-Selection-Engine-Audit.md` (a dated point-in-time audit record) and all Product Integrity #001-#008 reports' own past-tense narrative.

## 15. Explicit non-claims

This release does **not**:
- Fix the unresolved US Daily Picks OOM root cause.
- Add a daily lightweight valuation/price-only refresh (recorded as a possible future enhancement only, §1).
- Change Multibagger scoring thresholds, verdict labels, or screen logic.
- Change Daily Picks scoring, ranking, confidence, entry/target/stop-loss, or universe selection.
- Lift or touch GPI-0.
- Perform any backfill or outcome repair.
- Provide continuous (non-startup-only) orphan reconciliation (§7's disclosed limitation).

## 16. Natural-run verification plan

See the Final Report's STEP 25 section — both India's first natural Saturday run and US's first natural Sunday run remain pending as of this release.
