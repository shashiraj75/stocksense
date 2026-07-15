# Product Integrity Workstream #008 — US Scheduler Workload Overlap Compatibility

**Status:** Implemented, tested, and locally committed. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report — see that report for the gate result.

**Scope note:** this closes a dependency Product Integrity #007 (moving US Daily Picks base generation to 06:00 UTC and the Premarket Finalizer to a 6:00 AM ET target) left unaddressed: everything else in the scheduling system that assumed the *old* schedule. It does not touch Daily Picks scoring, ranking, universe selection, persistence, or finalizer decision logic; does not touch Phase 1A/1A.3 or backfill code; and does not touch GPI-0 (which remains enabled).

## 1. Reason for the change

#007 moved the US Daily Picks base run from 04:00 UTC to 06:00 UTC and the Premarket Finalizer's acceptance window to 6:00-7:30 AM ET, but did not audit what else in the scheduler assumed the old timing. Three concrete, previously undetected incompatibilities were found:

1. **US Multibagger fundamentals refresh** (`multibagger_refresh_us.yml`) still ran at 02:00 UTC with a documented 5-6 hour runtime — a schedule chosen when the base ran at 12:30 UTC. Against the new 06:00 UTC base, that runtime (finishing ~07:00-08:00 UTC) now lands directly inside the base generation run and the Premarket Finalizer's EDT/EST candidate window (10:00/11:00 UTC).
2. **US startup catch-up threshold** (`backend/api/main.py`, `_catchup_picks`) was still 9:00 AM ET — later than the finalizer's 7:30 AM ET cutoff, meaning a server restart between 7:30 and 9:00 AM ET could regenerate a base and never see it finalized that day.
3. **Stale schedule comments and current documentation** in `backend/api/main.py`, `multibagger_refresh.yml`, `README.md`, and `Documentation/STOCKSENSE_DOCUMENTATION.md` still described the pre-#007 schedule (04:00/12:30 UTC, 8:30 AM ET, 9 AM ET, 6 PM IST) as current.

## 2. Scheduler matrix

| Process | Cron | UTC | EDT | EST | Dubai | IST | Runtime | Provider | Concurrency guard | Priority if conflict |
|---|---|---|---|---|---|---|---|---|---|---|
| IN Multibagger refresh | `0 17 * * 0-4` | 17:00 | — | — | 21:00 | 22:30 | ~1-2h | screener.in | none (never observed to conflict) | n/a |
| IN Daily Picks | `56 21 * * 0-4` | 21:56 | — | — | 01:56+1 | 03:26+1 | ~4h (finishes ~01:56 UTC) | screener.in + yfinance (live pricing) | none needed — no other IN-market heavy job overlaps it | n/a |
| **US Multibagger refresh (new)** | `0 8 * * 1-5` | 08:00 | 4:00 AM | 3:00 AM | 12:00 | 13:30 | ~5-6h (finishes ~13:00-14:00 UTC) | yfinance | **durable, fail-closed reservation + Daily Picks priority check** (new, this release) | defers to US Daily Picks base |
| US Daily Picks base | `0 6 * * 1-5` | 06:00 | 2:00 AM | 1:00 AM | 10:00 | 11:30 | ~10-20 min | yfinance + fundamentals cache | existing durable `daily_picks_jobs` reservation | **highest — never yields** |
| US Premarket Finalizer | `0 10 * * 1-5`, `0 11 * * 1-5` | 10:00 / 11:00 | 6:00 AM / — | — / 6:00 AM | 14:00 / 15:00 | 15:30 / 16:30 | seconds (lightweight review, no recompute) | minimal — reviews an already-persisted base | same-day idempotency guard (existing, #007); now also raises the cooperative stop signal | high, but genuinely lightweight |
| India/US scheduled validation | in-process loop, not cron | n/a | | | | | variable | internal | independent of Daily Picks/Multibagger state | n/a |
| `keep_alive.yml` | `*/10 * * * *` | every 10 min | | | | | seconds | none (health ping only) | n/a — negligible load | n/a |

**Overlap findings:**
- The old US Multibagger schedule (02:00 UTC) would have overlapped the new 06:00 UTC base run and the finalizer window — the reason for this release.
- The user-suggested 22:00 UTC Sun-Thu candidate was evaluated and **rejected**: India Daily Picks' actual cron (`56 21 * * 0-4` = 21:56 UTC) plus its own ~4-hour runtime occupies 21:56-~01:56 UTC — a 22:00 UTC start would land inside that window instead of avoiding it. 08:00 UTC Mon-Fri was chosen instead — see §5.
- The new 08:00 UTC US Multibagger schedule still partially overlaps the Premarket Finalizer's window (10:00/11:00 UTC candidates); this is accepted deliberately since the finalizer is explicitly lightweight (never recomputes the universe, never re-runs PredictionEngine) and is now also covered by the cooperative stop signal (§3).
- No overlap was found, or is newly introduced, between anything in this matrix and IN Multibagger, IN Daily Picks, or scheduled validation.

## 3. US Multibagger schedule correction

`.github/workflows/multibagger_refresh_us.yml` cron moved from `0 2 * * 1-5` (02:00 UTC) to `0 8 * * 1-5` (08:00 UTC). Reasoning, buffer analysis, and the rejected 22:00 UTC alternative are documented in the workflow file's own comment and §2 above. Runtime buffer: ~2 hours after the base run's typical ~10-20 minute completion before the heavier job starts; finishes ~3-4 hours before IN Multibagger (17:00 UTC) and ~8+ hours before IN Daily Picks (21:56 UTC).

## 4. Deterministic concurrency protection

Cron separation alone is insufficient — GitHub Actions can dispatch late (documented precedent: US base-run cron has fired 2-11+ hours late on sampled occasions, see `Current-Release-Status.md`). Added:

- **New durable table** `multibagger_refresh_jobs` (`backend/services/postgres_store.py`), mirroring `daily_picks_jobs`'s partial-unique-index pattern: at most one `running` row per market via `CREATE UNIQUE INDEX ... WHERE status = 'running'`.
- **`POST /api/multibagger/refresh?market=US`** now requires durable state (`USE_POSTGRES=1`, matching Daily Picks' own existing contract) and, before starting, checks `has_active_daily_picks_job_or_unknown("US")` — a dedicated check that returns `True` (refuse to start) both when an active Daily Picks job exists **and** when the check itself fails, so a DB error is never read as "no conflict." India (`market=IN`) is completely unaffected — no durable requirement, no conflict check, unchanged in-memory-only contract.
- **`POST /api/picks/generate?market=US`** and **`POST /api/picks/premarket-finalize?market=US`**, on seeing an active US Multibagger refresh, call `request_us_stop()` — a plain, non-blocking in-memory flag — and then proceed immediately with their own reservation. Daily Picks never waits for the refresh; it only asks it to yield.
- **`run_full_refresh(should_stop=...)`** (`backend/services/us_fundamentals_refresh.py`) checks this flag once per symbol, between fetches, and exits cleanly at that boundary — never mid-fetch, so no torn writes. The summary records `stopped_early`.
- **Idempotency:** duplicate `POST /refresh` delivery for US returns `409 already_running` (in-memory fast path) or the durable reservation's own conflict outcome — never a second concurrent run.
- **India is not blocked by US-only state:** verified directly — `test_in_refresh_is_completely_unaffected_by_us_guard`, `test_in_refresh_does_not_call_daily_picks_conflict_check`, `test_in_generate_never_touches_multibagger_state`.
- **No new deadlock risk:** `request_us_stop()` is a single dict-key assignment — no lock acquisition, no wait (verified — `test_stop_request_is_non_blocking`).
- **Restart/orphan behavior — a disclosed, not fully solved, limitation.** `multibagger_refresh_jobs` has no automatic timeout/orphan-recovery: a hard-killed process leaves its row `running` until an operator manually clears it, blocking a subsequent US Multibagger start via the unique index. This is not a new risk introduced by this release — it is the exact same, already-accepted limitation `daily_picks_jobs` has always had (`'interrupted' is a manual-only operator recovery status; no code path writes it automatically`, see `postgres_store.py`'s own schema comment). Multibagger jobs are bounded (one nightly run, ~5-6h max), which narrows the exposure window relative to an indefinitely-running process, but does not eliminate it. Building an automatic staleness/timeout mechanism was judged out of scope for "smallest safe protection" and is not implemented here.
- **No production job is cancelled or mutated by this deployment** — the guard is new code, not retroactively applied to any job already in flight; deploying this code does not touch any currently-running row.

## 5. US startup catch-up threshold correction

`backend/api/main.py`'s `_catchup_picks("US", _ET, 9, 90)` call moved to `_catchup_picks("US", _ET, 3, 90)` — 3:00 AM America/New_York, DST-safe via the existing `zoneinfo.ZoneInfo("America/New_York")` object. 3 AM ET is strictly after both DST local times of the 06:00 UTC base run (2 AM EDT / 1 AM EST) and leaves real runway before the 6 AM ET finalizer target and its 7:30 AM ET cutoff. The India threshold (2 AM IST) is unchanged. Stale comments referencing the old 9 AM ET / 12:30 UTC framing were corrected. Tests added: before/at/after-threshold boundaries, both DST offsets, weekend skip, missing-durable-state fail-closed, and confirmation catch-up never itself triggers the finalizer.

## 6. Current-documentation corrections

- `README.md` — removed the false "Automated triggering is currently disabled" claim (automation has been live) and the stale single-stage 8:30 AM ET / 6 PM IST schedule description; now points to `Current-Release-Status.md` as the live source of truth rather than duplicating a schedule snapshot that will drift again.
- `Documentation/STOCKSENSE_DOCUMENTATION.md` — "What StockSense360 Does" and the "Automation Workflows" reference section (§23) rewritten to describe the current 3-phase US schedule and both Multibagger workflows (the old version of this section did not mention `daily_picks_us_premarket.yml`, `multibagger_refresh.yml`, or `multibagger_refresh_us.yml` at all).
- `Current-Release-Status.md` — added a clarifying note that the previously-recorded scheduler-delay observations (06:04 UTC, 06:45 UTC, etc.) were measured against the then-nominal 04:00 UTC cron, not the current 06:00 UTC one, plus a new entry summarizing this release.
- `.github/workflows/multibagger_refresh.yml` (India) — corrected its own comment, which referenced both the wrong IN Daily Picks time (20:30 UTC instead of the actual 21:56 UTC) and the pre-#007 US schedule (12:30 UTC).
- `backend/api/main.py` — corrected the stale "cron fires ~12:30 UTC" comment above the catch-up scheduling block.
- **Deliberately left unchanged as historical evidence**, per SES-006 §11: every Product Integrity #001-#003 report, `CHANGELOG.md`, `MASTER-ROADMAP.md`, and `INDEX.md` entry describing the old schedule — all are dated, past-tense narrative of what was true at the time they were written, not active claims about current state.

## 7. Frontend

No frontend code changes were needed for this workstream — #007 already correctly separates Base Generation from Premarket Review, shows the truthful per-state wording (including the skipped/failed fix from the prior corrective release), and excludes India from the Premarket Review stage. This release re-verified that contract is unaffected and adds the real production browser verification #007 had not yet completed (see the Final Report's frontend evidence section).

## 8. Rollback plan

1. Revert `.github/workflows/multibagger_refresh_us.yml`'s cron to `0 2 * * 1-5`.
2. Revert `backend/api/main.py`'s `_catchup_picks("US", _ET, 3, 90)` to `_catchup_picks("US", _ET, 9, 90)`.
3. Revert `backend/api/routers/multibagger.py`, `backend/services/us_fundamentals_refresh.py`'s `should_stop` parameter, and `backend/api/routers/picks.py`'s Step 4a / finalizer stop-request additions.
4. The new `multibagger_refresh_jobs` table is additive (`CREATE TABLE IF NOT EXISTS`) and safe to leave in place even after a code revert — it simply stops being read/written. No migration or data cleanup is required either direction.
5. Revert the documentation changes in §6, or leave them (they are strictly more accurate than what they replaced regardless of which code version is deployed).

## 9. Known limitations — explicitly not claimed as fixed by this phase

- **Multibagger job orphan/restart recovery** — not automatic, see §4. A hard-killed process requires manual row cleanup.
- **GitHub Actions dispatch reliability** — unchanged; a multi-hour pathological delay (as previously observed) could still, in principle, push US Multibagger's actual start late enough to re-approach the base/finalizer window despite the new schedule. The durable conflict check and cooperative stop are the backstop for exactly this case, but they were not exercised against a real multi-hour-delayed natural run in this session.
- **US Daily Picks OOM root cause** — unresolved, unrelated, out of scope, unaffected by this release.
- **GPI-0 (validation/performance integrity hold)** — remains enabled; untouched by this release.
- **Phase 1A / 1A.3 outcome or backfill logic** — untouched by this release.
- **Live production frontend browser verification** — see the Final Report; performed after deployment, not simulated.

## 10. Natural-run verification plan

Two independent natural-run sequences must both be observed before this workstream can be considered fully closed, neither of which this release triggers or substitutes for:

1. **US Multibagger refresh** fires naturally at 08:00 UTC on a real Mon-Fri, completes (or is cleanly stopped by the cooperative signal if it overlaps a live Daily Picks run) without manual intervention, and does not block the following day's run.
2. **US Daily Picks base + Premarket Finalizer sequence** (already pending from #007) — base completes and persists at ~06:00 UTC, finalizer runs naturally inside 6:00-7:30 AM ET with provenance matching the exact base job, and the duplicate DST candidate safely no-ops.

Both remain **AWAITING NATURAL-RUN VERIFICATION** as of this release.
