# Product Integrity Workstream #010 — Multibagger Production Hardening and Legacy-Schema Repair

**Status:** Deployed to production (2026-07-16, commit `5ade20c`) — repair confirmed live via direct read-only introspection (market constraint now `IN ('IN','US')`, active-job index now `WHERE status IN ('queued','running')`).

**Scope note:** this corrects defects in #009's own implementation — discovered through a direct, read-only production-database forensic audit performed for this release, not assumed from source code — and finalizes the Multibagger architecture: single-cron US scheduling, atomic job+lease reservation, a resumable staged worker with atomic cache promotion, and a real legacy-schema repair migration. It does not touch Daily Picks scoring, ranking, universe selection, PredictionEngine logic, Premarket Finalizer decision/provenance logic, Phase 1A/1A.3, backfill tooling, outcome remediation, GPI-0, or India/US market separation. Daily Picks and Premarket Finalizer schedules are explicitly frozen and regression-tested (§2).

## 1. The critical finding: #009's migration never actually ran

Direct introspection of `information_schema`, `pg_constraint`, and `pg_indexes` against production (read-only `SELECT` queries only, no writes) found:

- `multibagger_refresh_jobs`'s market constraint was still `CHECK (market = 'US'::text)` — the original #008 definition — **not** the `CHECK (market IN ('IN','US'))` #009's own schema code specified.
- The active-job unique index was still `WHERE (status = 'running'::text)` — the original #008 predicate — **not** `WHERE status IN ('queued','running')`.
- `multibagger_refresh_jobs` and `heavy_workload_leases` were both **completely empty** — zero rows, either market — despite #008 and #009 both having been live in production for a full day.

**Root cause:** `CREATE TABLE IF NOT EXISTS` and `CREATE INDEX IF NOT EXISTS` are pure name-existence checks in PostgreSQL — they never diff or repair an already-existing object's actual definition. Since both objects already existed under those names from #008, #009's schema code silently no-op'd against them. The status `CHECK` constraint *was* correctly repaired in #009, because that specific line used an explicit `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` — the market constraint and the active-index predicate did not get the same treatment, and #009's own migration comment claiming it followed "the same DROP-IF-EXISTS-then-ADD pattern" was true for the status constraint but not applied to these two.

**Practical impact:** every India Multibagger job insert attempt since #009 deployed raised a Postgres `IntegrityError` (caught by the endpoint's `try/except`, surfaced as `503 durable_state_unavailable`) — India Multibagger has been completely non-functional in production since #009 shipped, silently.

## 2. Frozen schedules — verified unchanged

Regression tests (`test_daily_picks_schedule_freeze.py`) read the actual workflow YAML files directly and fail the suite if any of these ever drift:

| Workflow | Cron | Verified unchanged |
|---|---|---|
| `daily_picks_in.yml` | `56 21 * * 0-4` | ✅ |
| `daily_picks_us.yml` | `0 6 * * 1-5` | ✅ |
| `daily_picks_us_premarket.yml` | `0 10 * * 1-5`, `0 11 * * 1-5` | ✅ |

Also verified: each targets its correct market; no Daily Picks/Finalizer workflow references the Multibagger endpoint, and no Multibagger workflow references the Daily Picks/Finalizer endpoints.

## 3. Final schedules

| | Cron | Meaning |
|---|---|---|
| India Multibagger | `30 21 * * 5` | Friday 21:30 UTC = Saturday 3:00 AM IST = Saturday 1:30 AM Dubai |
| US Multibagger | `0 8 * * 0` | Sunday 08:00 UTC = 3:00 AM EST / 4:00 AM EDT = Sunday 12:00 PM Dubai / 1:30 PM IST |

**Why one fixed US cron, not two DST candidates:** #009's original two-candidate design (`0 7 * * 0` EDT / `0 8 * * 0` EST) added real complexity — the backend had to treat one candidate as authoritative and the other as an expected no-op, and during EDT *both* candidates actually land inside a narrow local window, relying on weekly-period idempotency rather than window exclusivity to stay safe. A one-hour seasonal difference in when a **weekly, long-term** fundamentals refresh starts is immaterial to the product. One fixed cron is strictly simpler and removes an entire class of DST-window-ambiguity bugs — exactly the class of subtle drift this release's own forensic audit uncovered elsewhere. User-facing copy (frontend and API) truthfully discloses both possible local times: "Sunday 08:00 UTC (3:00 AM EST / 4:00 AM EDT)" — never a single "3:00 AM ET" claim, which would be wrong for half the year.

## 4. Day-only scheduled acceptance (replaces #009's narrow window)

`services/multibagger_schedule.py` was rewritten: `in_scheduled_window()`/`us_scheduled_window()` (narrow 2:30-4:30 AM local arrival windows) became `in_scheduled_day()`/`us_scheduled_day()` (accept any local time on the correct scheduled weekday). GitHub Actions' own scheduled delivery is best-effort and has been observed arriving hours late in this repository (Product Integrity #003) — a narrow window would reject a legitimately delayed same-day dispatch. `trigger_source` is now a **required** query parameter with no default (`Query(...)`, not `Query("scheduled")`) — a caller can never accidentally omit it and have the endpoint silently assume either scheduled or manual.

## 5. Atomic job+lease reservation (closes #009's "known risk #5")

`try_reserve_multibagger_job_with_lease()` and `try_reserve_daily_picks_job_with_lease()` (new, in `postgres_store.py`) perform the job-row INSERT and the lease-row INSERT inside the **same** `with pool.connection()` block — one transaction. If the lease insert conflicts, the function explicitly `conn.rollback()`s before returning, undoing the job reservation too. #009's design called `try_reserve_multibagger_job()` and `try_acquire_heavy_workload_lease()` as two separate calls (each its own implicit transaction) — a lease-acquisition failure left an orphaned `queued` row with no lease behind it, occupying the weekly-period idempotency slot for nothing. `POST /api/multibagger/refresh` and `POST /api/picks/generate` both now use the atomic path exclusively; the old two-call pattern's standalone `try_reserve_multibagger_job()` function was removed (dead code, real footgun if ever called by mistake) along with the #008-era `has_active_daily_picks_job_or_unknown()` conflict check it superseded.

## 6. Resumable worker (closes #009's "known risk #11")

`try_claim_queued_multibagger_job()` uses `SELECT ... FOR UPDATE SKIP LOCKED` + `UPDATE` to atomically claim a queued job before processing begins — a second concurrent claimant (a hypothetical second Railway instance, or a restarted process racing a still-alive old one) finds no lockable row and returns `False` immediately rather than blocking or double-processing. This backend currently runs as a single Railway instance, so this is a forward-looking safety property, not a currently-exercised multi-instance path — but it closes the gap at negligible cost.

`_run_refresh_job()` loads `get_staged_symbols(job_id)` before starting the refresh loop — any symbols already staged by a prior attempt at the *same* `job_id` (e.g. a process that died and was later reconciled to `interrupted`, then genuinely retried with the same identity is not how retries work today — see §12 limitation) are skipped rather than re-fetched, and the refresh loops (`us_fundamentals_refresh.py`, `fundamentals_refresh.py`) accept an `already_staged` set for exactly this purpose.

## 7. Staging and atomic promotion (closes #009's "known risks #9, #10")

New table `multibagger_staging`: one row per `(job_id, symbol)`, `outcome IN ('refreshed','skipped','failed')`, upserted via `stage_multibagger_symbol()` — the refresh loops now call this instead of `cache.upsert()` directly, so **no partial run ever touches the active `stock_fundamentals_cache`**.

`promote_staged_symbols(job_id, market)` is the only function that writes to `stock_fundamentals_cache`, called once after the full universe has been traversed (verified: `count_staged_outcomes()`'s total must equal the run summary's `total`, or `_run_refresh_job` raises before promoting). It builds its column list dynamically from `fundamentals_cache.FIELD_MAP` — the same source of truth `cache.upsert()` itself uses — rather than a hand-duplicated column list, so a future field added to `FIELD_MAP` is picked up automatically instead of silently promoting `NULL`. Symbols marked `skipped`/`failed` are never promoted **and never delete or overwrite** their prior cache row — a temporary provider failure for one symbol must not erase that symbol's last-known-good data.

## 8. RLS

`multibagger_staging` (new) has `ENABLE ROW LEVEL SECURITY` in its `CREATE TABLE` block. `multibagger_refresh_jobs` and `heavy_workload_leases` (already RLS-enabled since #009) are unaffected. No policies were added to any table — this backend connects via the `postgres` role (`BYPASSRLS` by default), so enabling RLS with no policies closes the public PostgREST API's access without affecting this backend's own reads/writes. Verified directly against production: RLS enabled on all four tables, zero policies on any of them.

## 9. Status/freshness contract corrections

`is_stale` now uses a precise `timedelta` comparison (`(now - last_successful_refresh_at) > timedelta(days=9)`) instead of integer `.days` truncation, which would have treated "9 days and 23 hours" as `.days == 9` (not yet stale) — a near-day-long false "fresh" window right at the boundary. `next_scheduled_refresh_hint` for US now reads `"Sunday 08:00 UTC (3:00 AM EST / 4:00 AM EDT)"`.

## 10. Adversarial review findings

| # | Finding | Severity | Evidence | Correction | Test added |
|---|---|---|---|---|---|
| 1 | `market` CHECK constraint still `= 'US'` in production | **HIGH** | Direct `pg_constraint` read | Explicit `DROP CONSTRAINT IF EXISTS` + `ADD CONSTRAINT` | `test_schema_sql_explicitly_repairs_the_market_check_constraint` |
| 2 | Active-job index predicate still `status = 'running'` only | **HIGH** | Direct `pg_indexes` read | Explicit `DROP INDEX IF EXISTS` + `CREATE UNIQUE INDEX` | `test_schema_sql_explicitly_repairs_the_active_job_index_predicate` |
| 3 | Job reservation and lease acquisition in separate transactions — a lease failure orphaned a queued row | **MEDIUM** | Code inspection of #009's `trigger_refresh()` | Atomic `try_reserve_*_job_with_lease()` | `test_atomic_reserve_lease_conflict_rolls_back_job_insert_too` |
| 4 | No resumable-worker claim semantics; no per-symbol checkpoint | **MEDIUM** | STEP 11/12 requirements vs. #009 implementation | `try_claim_queued_multibagger_job` (FOR UPDATE SKIP LOCKED) + staging table + `already_staged` resume | `test_claim_queued_job_uses_for_update_skip_locked`, `test_run_refresh_job_resumes_with_already_staged_symbols` |
| 5 | Refresh loops wrote directly to the active cache — a partial run could leave half-updated data visible | **MEDIUM** | Code inspection of #009's `run_full_refresh()` | Staging + atomic `promote_staged_symbols()`, gated on full-universe traversal | `test_run_refresh_job_raises_on_incomplete_universe_traversal` |
| 6 | `is_stale` used integer `.days`, understating staleness near the boundary | **MEDIUM** | Code inspection | Precise `timedelta` comparison | `test_status_stale_boundary_uses_precise_timedelta_not_integer_days` |
| 7 | Dual-DST-candidate US cron design adds real complexity/ambiguity surface | **MEDIUM** | Design review, consistent with the user's own final operating contract | Single fixed `0 8 * * 0` cron; explicit EST/EDT disclosure in user-facing copy | `test_us_multibagger_is_single_cron_not_dual_dst_candidates` |
| 8 | Old non-atomic `try_reserve_multibagger_job()` and `has_active_daily_picks_job_or_unknown()` left as untested dead code — a footgun if ever called instead of the atomic path | LOW | `grep` found zero callers, zero tests | Deleted | n/a (deletion, not a new behavior to test) |
| 9 | `heavy_workload_leases` lacks its own `heartbeat_at`/`release_reason` columns named in the task's STEP 14 list | LOW, accepted | Design review | **Not implemented** — a lease's lifetime is always 1:1 with its owning job row, whose own heartbeat already drives orphan reconciliation (`reconcile_stale_multibagger_jobs()` releases the lease transactionally when it reconciles the job); a second, always-redundant heartbeat column on the lease itself was judged unnecessary duplication rather than a real gap. Documented here rather than silently omitted. | n/a |
| 10 | `multibagger_staging` has no row-retention/cleanup policy — grows indefinitely (~7,600 rows/week combined) | LOW, accepted | Design review | **Not implemented this release** — growth is slow (~400K rows/year) and non-corrupting; explicitly flagged as follow-up work rather than solved here | n/a |

All HIGH and MEDIUM findings were fixed before commit; both LOW findings are explicitly accepted with reasoning, not silently dropped.

## 11. Failure-mode coverage

Directly tested (mocked DB, no live network): fresh-schema behavior (all new schema is `CREATE ... IF NOT EXISTS`, safe on a fresh DB); explicit legacy-schema repair (§1); repeated/idempotent migration (every `DROP ... IF EXISTS` + `ADD`/`CREATE` pair is safe to run every startup); India Saturday / US Sunday EST / US Sunday EDT acceptance; wrong-weekday rejection; delayed same-day acceptance; duplicate scheduled delivery (safe no-op); manual-run-before-scheduled and manual/scheduled independence; simultaneous reservation requests (atomic INSERT semantics — exactly one wins); DB-unavailable-during-reservation (fails closed, raises); claim-race behavior (SKIP LOCKED); incomplete-universe-traversal (raises, does not promote/complete); terminal completion-write failure and failure-write failure (both return `False`, job retained in prior state); stale-job reconciliation with lease release; RLS/policy state (verified directly against production). **Not directly exercised in this session** (would require a live 5-6 hour run or live OOM): a genuine mid-run process kill on Railway, and a live multi-instance race — both are structurally guarded (staging survives a kill; SKIP LOCKED guards multi-instance) but not observed happening in production.

## 12. Explicit non-claims

This release does **not**:
- Fix the unresolved US Daily Picks OOM root cause.
- Change Multibagger scoring thresholds, verdict labels, or screen logic.
- Change Daily Picks scoring, ranking, confidence, entry/target/stop-loss, or universe selection.
- Change any Daily Picks or Premarket Finalizer cron, endpoint, or market parameter (frozen and regression-tested, §2).
- Lift or touch GPI-0.
- Perform any backfill or outcome repair.
- Add continuous (non-startup-only) orphan reconciliation.
- Implement true multi-attempt resume across *different* `job_id`s — resume only applies within a single job_id's own staging rows (e.g. after `try_claim_queued_multibagger_job` is called again for the same still-queued row); a job already reconciled to `interrupted` starts a genuinely fresh job_id on its next scheduled/manual trigger, with no staging carried over.
- Add lease-row heartbeat/retention columns or staging-table cleanup (§10, findings 9-10 — explicitly accepted, not silently dropped).

## 13. Rollback

1. Revert `.github/workflows/multibagger_refresh_us.yml`'s cron to the #009 dual-candidate form if genuinely needed (not recommended — #010's single-cron design is the intended final state).
2. Revert `backend/services/multibagger_schedule.py`, `backend/services/postgres_store.py`'s new/changed functions, `backend/api/routers/multibagger.py`, and the `picks.py` lease-reservation call site to their #009 versions.
3. The market-constraint and active-index repairs are one-directional widenings (US-only → IN/US; running-only → queued+running) — reverting the code does not need to revert the schema; a wider constraint/predicate is harmless even if unused.
4. `multibagger_staging` is additive and net-new — safe to leave in place after a code revert.
5. Revert the frontend/documentation copy changes, or leave them (strictly more accurate regardless of deployed code version).

## 14. Natural-run verification plan

See the Final Report — all four required natural-run observations (India Daily Picks, US Daily Picks/Finalizer, India Saturday Multibagger, US Sunday Multibagger) remain pending as of this release.
