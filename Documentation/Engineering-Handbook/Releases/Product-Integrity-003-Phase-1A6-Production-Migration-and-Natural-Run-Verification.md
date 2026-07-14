# Product Integrity Workstream #003 — Phase 1A.6 Production Migration and Natural-Run Verification

**Status:** All actions described below completed and independently verified. No historical row repair, backfill, or relabeling was performed as part of any action in this record.

**Scope note:** this record covers only the *production operational* actions taken after Phase 1A.6's code remediation (commit `36d4b33`) was already written, tested, and committed. For the code-level design/implementation, see [Phase 1A.6 — Market Integrity Hardening and Historical Repair Planning](../Architecture/Phase-1A6-Market-Integrity-Hardening-and-Repair-Planning.md); this record is that document's own §23, expanded to full evidence.

## 1. Deployment verification (2026-07-14)

- Repository: branch `main`, local `HEAD` and `origin/main` both `36d4b338377026ad7aa69014cf3efe4edc45a572`.
- Railway deployment `07ff8c6d-0caa-462e-804b-4aa207d0b50c` — status `SUCCESS`, built from commit `36d4b33` exactly (confirmed via `railway deployment list --json`, not inferred from `/health` alone). Service reports Online.
- `/health` returns HTTP 200. `/api/picks/status?market=IN` and `?market=US` both confirm `production_learning_enabled: false`, `production_alpha_source: "fixed_academic_prior"`.
- Deployment logs directly confirm the new resolver-containment logic is live against real production data (an observed containment-exclusion warning naming a real symbol), and a targeted log search found zero occurrences of `MissingMarketContextError`, `InvalidMarketError`, `MarketSymbolConflictError`, manifest-version errors, repair-planner activity, tracebacks, or crash markers.

## 2. Database-default migration execution (2026-07-14T05:54:02Z)

Executed as a separate, explicitly authorized, controlled production action — not part of the code deployment above.

**Preflight (read-only, immediately before execution):**
- Execution role identity confirmed: owner of both `predictions` and `outcomes` (able to `ALTER` both tables without any privilege change), `transaction_read_only: off` for the deliberate session.
- Immediate pre-execution schema re-check: both `predictions.market` and `outcomes.market` still `DEFAULT 'IN'::text`, both `NOT NULL`, both `text`, both `public` schema.
- Immediate pre-execution data-integrity snapshot: `predictions` 39,819 rows (0 NULL, 0 non-`IN`/`US`); `outcomes` 4,841 rows (0 NULL, 0 non-`IN`/`US`).
- Activity/lock preflight: no long-running or idle-in-transaction sessions, no locks held or waited on either target table — clear to proceed.

**Execution:** both `ALTER TABLE ... ALTER COLUMN market DROP DEFAULT` statements inside one explicit transaction (`lock_timeout = '5s'`, `statement_timeout = '30s'`), in-transaction verification confirmed both defaults absent and both `NOT NULL` constraints intact **before** `COMMIT`. Transaction executed in ≈380ms. `COMMIT` executed; no `ROLLBACK` was needed.

**Post-commit, independently re-verified from a separate read-only session:**
- Both `predictions.market` and `outcomes.market` report `column_default: NULL` via both `information_schema` and `pg_attrdef` (the catalog default entry itself is gone, not merely reporting NULL). Both columns remain `NOT NULL`, both remain `text`.
- Data-integrity counts recaptured: **identical** to the pre-execution snapshot — `predictions` 39,819 (0 NULL, 0 non-`IN`/`US`); `outcomes` 4,841 (0 NULL, 0 non-`IN`/`US`); min/max timestamps unchanged. **Zero existing rows were affected, rewritten, or relabeled.**
- `/health` remained HTTP 200 throughout; India and US `/api/picks/status` remained readable with no new `last_error`; production learning quarantine unaffected.
- Bounded post-migration log review: zero database errors, zero `NOT NULL`/constraint-violation messages, zero market-integrity exception markers, zero crash/restart markers.

**No writer role was created or used. No repository file was edited, staged, or committed as part of this action.**

## 3. Natural Daily Picks runs, post-deployment (2026-07-14)

- **India:** scheduled run completed on the current deployed code. `job_status: completed`, `has_today: true`, `last_error: null`, `universe_degraded: false`, `production_learning_enabled: false`, `production_alpha_source: "fixed_academic_prior"`.
- **US:** a fresh job (`942231a1-a0c2-4fda-89e7-0450160b69eb`) was reserved and completed by the scheduled trigger. `job_status: completed`, `has_today: true`, `last_error: null`, `universe_used: "fundamentals_cache"`, `universe_degraded: false`, `universe_candidate_count: 400` (matches Sprint #014's `_TARGET_UNIVERSE_SIZE` exactly, confirming its stratified-universe logic completes end-to-end for US, not just structurally in unit tests).
- Both runs are natural (scheduler-fired), not manually triggered — no `POST /api/picks/generate` call was ever issued by any read-only investigation session; every generation observed in this record was fired by GitHub Actions' own cron.

## 4. Historical US job `aa73f80b` — root cause, closure, and non-blocking status

A separate, older US Daily Picks job predates Phase 1A.6's deployment and is unrelated to it or to the migration.

**Persisted state (read-only, audit role):** `job_id aa73f80b-705b-4a31-953a-6b5e26682892`, `status: interrupted`, `phase: phase_1`, `processed: 1185`, `total: 1191`, `started_at: 2026-07-13T06:45:52Z`, `last_progress_at: 2026-07-13T07:26:39Z`, `completed_at: NULL`, `last_error: NULL`, `persisted_picks_timestamp: NULL`. No US Daily Picks cache row exists between 2026-07-10 and 2026-07-14 — this run produced no partial or final output of any kind.

**GitHub Actions evidence:** the `daily_picks_us.yml` scheduled trigger (`event: schedule`) fired at `2026-07-13T06:45:46Z` — 2h46m after its nominal `04:00 UTC` cron time — and reported `success` (the trigger `POST` was accepted; GitHub Actions' own "success" does not certify downstream completion, see §5).

**Railway log timeline:** the job's host process's last healthy log line is at `2026-07-13 07:26:40Z`, one second after the job's own `last_progress_at`. Immediately following, the same deployment (commit `f45ed1fe7f89`) entered a ≈10-repetition crash loop on every restart attempt: `ImportError: cannot import name 'is_known_symbol' from 'services.stock_universe'` (`api/main.py` → `api/routers/predictions.py:19`) — a real, confirmed defect in that exact deployed commit, unrelated to Phase 1A.6. No `OOM`/`SIGKILL` marker is visible in the application log stream; what caused the *initial* process death is inference, not directly confirmed evidence. What **is** directly confirmed is that the deployment could not successfully restart until it was replaced by commit `e3404a3` (containing the fix) at `2026-07-13T07:52:23Z`, 26 minutes later.

**Code-path confirmation (read-only source inspection):**
- `daily_picks_jobs`'s cross-process mutual-exclusion index is `UNIQUE (market) WHERE status IN ('queued', 'running')` — `'interrupted'` is deliberately excluded from that `WHERE` clause. An interrupted row **cannot** block a new reservation, confirmed both by this code and by direct observation: the 2026-07-14 US job (`942231a1`) reserved and completed successfully while `aa73f80b` remained `interrupted` the entire time.
- The `daily_picks_jobs` schema's own comment states: *"'interrupted' is a manual-only operator recovery status; no code path writes it automatically."* This status was a manual diagnostic label applied by an earlier operator investigation, not a system-generated marker, and carries no special operational meaning to the application.
- `generate_picks()` always runs the full pipeline from a fresh `job_id`; there is no checkpoint/resume logic keyed on a prior job's `processed`/`total`. Confirmed both by code and empirically (the 2026-07-14 US job re-ran its own Phase-0 universe selection and produced `total: 1188`, not a continuation of `1191`).

**Conclusion: benign historical interruption. No cleanup of the `aa73f80b` row is required for correctness.** Its root cause (`is_known_symbol` `ImportError`) was fixed same-day by `e3404a3`, well before Phase 1A.6's own deployment — neither Phase 1A.6 nor the database-default migration caused, contributed to, or is implicated by this interruption in any way (the interruption occurred over 22 hours before Phase 1A.6 deployed and 26.5 hours before the migration executed).

## 5. Scheduler-timing reliability — separate, open operational concern

Observed GitHub Actions trigger times for `daily_picks_us.yml` (nominal cron `04:00 UTC`), all `event: schedule` (not manual dispatch):

| Date | Actual fire time | Delay vs. nominal |
|---|---|---|
| 2026-07-14 | 06:04:16 UTC | +2h04m |
| 2026-07-13 | 06:45:46 UTC | +2h46m |
| 2026-07-10 | 07:27:25 UTC | +3h27m |
| 2026-07-09 | 15:33:20 UTC | +11h33m |

Every observed run in this sample fired late, by a materially different margin each time — a recurring, systemic pattern, not a one-off. GitHub Actions' own "success" conclusion on each of these runs certifies only that the workflow's `curl -X POST .../api/picks/generate` call received a 2xx response; it does **not** certify that the downstream, asynchronous `generate_picks()` background task subsequently completed — those are two separate, decoupled facts, and conflating them was the root gap in how earlier records (e.g. Product Integrity #001B/#001C) had to reconstruct completion status indirectly.

This pattern is consistent with, and extends, the delay class already named in `daily_picks_us_premarket.yml`'s own code comments (a real missed run recorded there previously). **This is recorded as open follow-up work — no scheduler was selected, no cron expression was changed, and no replacement mechanism was implemented as part of this record.** A dedicated Daily Picks Scheduler Reliability Audit is required to design end-to-end completion monitoring and investigate the timing delay itself; see `Operations/Current-Release-Status.md` → Release 12B for the standing pointer to this open item.

## 6. What this record does and does not establish

**Establishes:** Phase 1A.6 is deployed and live; the database-default migration executed cleanly with zero data impact; production learning quarantine is intact; both markets' Daily Picks generation completes naturally end-to-end on the current code; the historical US interruption is fully explained, inert, and unrelated to Phase 1A.6.

**Does not establish:** historical contamination repair (separately gated, not started); canonical clean learning-dataset construction (not started); learning re-enablement (not authorized); GitHub Actions scheduler-timing reliability (separately open); Sprint #014's finer-grained output-quality items — real tier diversity, real short-term confidence distribution, actual total runtime (not yet inspected, see `Sprint-014-Daily-Picks-Cap-Stratification-and-Confidence-Priority.md`'s own Recommendations section).

## Related documents

- [Phase 1A.6 — Market Integrity Hardening and Historical Repair Planning](../Architecture/Phase-1A6-Market-Integrity-Hardening-and-Repair-Planning.md) — code-level design/implementation, this record's own §23 pointer.
- [Operations/Current-Release-Status.md](../Operations/Current-Release-Status.md) — Phase 1A.6 and Release 12B live-status entries.
- [Sprint #014 — Daily Picks Large/Mid/Small-Cap Stratification and Confidence Priority](Sprint-014-Daily-Picks-Cap-Stratification-and-Confidence-Priority.md) — the universe logic this record's natural-run evidence validates end-to-end for the first time.
- [Product Integrity Workstream #001C](Product-Integrity-001C-US-Daily-Picks-Trigger-Delivery-and-Recovery.md) and [#002A](Product-Integrity-002A-Daily-Picks-India-Symbol-and-Batch-Isolation-Verification.md) — prior, related US/India Daily Picks operational verifications this record continues the same evidentiary standard from.
