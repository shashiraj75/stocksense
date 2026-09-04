# Validation Memory Architecture Review (2026-09)

## Purpose

Stage 3 of the Railway memory remediation / weekly-validation-schedule work: determine whether Validation's automatic execution inside the always-on FastAPI/Railway process is a genuine memory-retention risk requiring a dedicated one-shot worker service, or whether the evidence points elsewhere. Per the governing instructions: *"Do not assume reducing frequency fixes memory... If evidence shows a different cause, implement only the evidence-supported correction instead. Do not add speculative cleanup calls."*

## 1. Does Validation execute inside the always-on process?

Partially, and the distinction matters. **Orchestration** (the scheduler loop, catch-up, admission/fencing, and persistence-after-completion) runs inside the always-on `api/main.py` process, via `asyncio` tasks and `loop.run_in_executor()`. **Computation** (the actual pandas/NumPy/yfinance walk-forward backtest work — the part that could plausibly consume multiple GB) does **not** run in that process: `services/validation_engine.py`'s `_run_validation_in_subprocess()` (line ~5745) spawns a genuinely separate OS process via `multiprocessing.get_context("spawn")` for every single validation attempt, already.

This is the structurally important fact: unlike Daily Picks (which, before PR #78, ran `generate_picks()` as a `threading.Thread` *inside* the parent process — sharing its address space, so anything it allocated was directly the parent's own RSS), a spawned OS process is memory-isolated by the kernel. When that child process exits — normally, or via the existing `terminate()`→`join()`→`kill()`→`join()` escalation (`_terminate_child_process`/`_reap_child_process`, lines ~5530-5574) — **the OS unconditionally reclaims every byte of its memory**, with no dependency on Python-level `gc.collect()`/`malloc_trim()` cleanliness. This is exactly the memory-isolation property a dedicated one-shot Railway service would provide; validation already has it, just orchestrated by an in-process scheduler rather than an external one.

## 2. Stage 4 checklist — verified against current code

| Requirement | Status | Evidence |
|---|---|---|
| Child processes always joined and reaped | ✅ | `_terminate_child_process`/`_reap_child_process` (validation_engine.py:5530-5574): `terminate()` → `join(timeout)` → `kill()` → `join(timeout)` on every failure branch (fencing loss, inactivity stall, hard deadline, silent exit). No `process.close()` call found — minor, non-blocking observation (see §5). |
| Multiprocessing queue closed/joined | ✅ | `result_queue.close()` + `result_queue.join_thread()` in a `finally:` block (line ~5920-5924) covering the entire `_run_validation_in_subprocess` body — fires on every exit path, exception-swallowed so cleanup failure can never mask the real error. |
| Executors terminate on every path | ✅ | `pool.shutdown(wait=True, cancel_futures=True)` in a `finally:` (line ~2925), inside `run_validation()` (which itself only ever runs inside the spawned child, per §1 — so this executor's memory is reclaimed with the whole child regardless). |
| Completed futures / exception tracebacks not retained | ✅ | `pool`, and any futures collection, are local variables inside `run_validation()`'s stack frame — never assigned to a module-level or otherwise long-lived structure. Verified by inspection; no counter-evidence found. |
| Parent references to metrics/signals/payloads released after persistence | ✅ | The `payload`/`metrics`/`result` values are local variables in `_run_validation_in_subprocess` and `execute_admitted_validation`'s call chain — nothing stores them in `_run_status` or any other module-level structure. `_run_status["log"]` (the one module-level, cross-call structure that accumulates per-symbol lines during a run) is explicitly `.clear()`-ed and reseeded at the start of every run (`_seed_run_status`, line ~5608-5629) — bounded to at most one run's worth of lines, never a cross-run accumulation. |
| Worker concurrency has a validated safe maximum | ✅ | `max_workers` defaults to `6` at every call site (`run_validation`, `_run_validation_in_subprocess`, `execute_admitted_validation`, `execute_and_complete_admitted_attempt`) — a fixed, non-externally-parameterized value; the manual `/run` API route does not accept a caller-supplied `max_workers` override. |
| DB connections closed on every path | ✅ (pre-existing, re-confirmed) | Every `_pg_conn()`/SQLite connection acquisition in validation_engine.py is paired with `conn.close()` in a `finally:` block — the same pattern repeated 20+ times throughout the file. |
| Peak memory retains ≥30% headroom below 8GB | ⚠️ Not attributable to Validation specifically | Railway's 8GB limit is a single cgroup shared by the parent process AND any live child process combined. The two >70%-used memory readings found in the 7-day metrics window (§3) both correlate with Daily Picks `generate_picks_end` timestamps, not with validation schedule windows. No log evidence (validation_engine.py has no `memory_guard`-equivalent instrumentation) ties a validation run to a headroom breach. |

## 3. Correlation evidence (7-day Railway metrics + logs, 2026-08-28 through 2026-09-04)

- 11+ Daily Picks `generate_picks_end` release-memory log lines observed, each with a `before`/`after_trim` byte pair showing real, material memory recovery (e.g. 08-31 US: 5.11GB → 3.53GB; 09-01 IN: 4.96GB → 3.59GB) — direct confirmation PR #78's fix is active and effective in production.
- The two highest observed peaks in this window (08-28 US ~71.6% used, 09-03 US ~72.7% used) are both timestamped at Daily Picks `generate_picks_end` lines, not validation scheduler windows (`00:30 UTC` under the pre-PR-79 daily schedule).
- No restart, redeploy, or OOM/exit-137 event occurred in this 11-day window (deployment `95bfd3e6` has been continuously live since 2026-08-24).
- **No point-in-time metrics series correlating specific validation-window timestamps to a measured memory delta was obtainable** — the Railway MCP's `get-service-metrics` tool returns only window-aggregate statistics (average/current/max/min), not a queryable per-timestamp series. This is an explicit evidence gap (see §6), not a claim of certainty.

## 4. Conclusion

**The evidence does not support Validation as the dominant Railway memory-retention driver**, and does not support that a dedicated one-shot Railway service is currently required to fix a measured problem. The dominant driver, per direct log evidence, was Daily Picks' in-process threading model — already fixed in PR #78, with real post-fix production evidence in §3. Validation's architecture already achieves subprocess-level memory isolation, and every Stage 4 checklist item was independently verified against current code with no gap requiring a code change.

**Residual, evidence-thin risk**: because Railway's memory limit is a single cgroup shared across the parent and any live child, an elevated Daily-Picks-driven parent baseline occurring at the *exact same moment* as a validation child's own peak could, in principle, add up closer to the ceiling than either alone. PR #79 (the weekly-Saturday schedule) already reduces this overlap window's frequency by 7x (daily → weekly) as a direct side effect, independent of memory considerations. No further code change is implemented here, per the explicit instruction against speculative cleanup unsupported by evidence.

## 5. Minor, non-blocking observation

`multiprocessing.Process.close()` is never called on the validation child's process handle after `join()`/`kill()`. This is cosmetic: `close()` only releases the small amount of internal handle bookkeeping and is not required for the OS to reclaim the child's actual memory (already guaranteed by process exit). Not implemented as a fix here — no evidence ties it to any measured memory issue, and `close()` raises if called on a still-running process, so adding it would need care around the exact point every exit path has already confirmed `is_alive() is False`. Flagged for a future, separately-scoped cleanup pass if desired.

## 6. Prepared architecture: one-shot validation worker (NOT deployed)

Per the instruction to prepare this even where current evidence doesn't mandate it: `backend/validation_worker.py` (this PR) is a complete, tested, standalone one-shot entrypoint. It is **not referenced by any Railway config, not wired into any startup/lifespan code, and has zero effect on the running application** — it exists so the exact code such a migration would run is fully specified and reviewable now.

### What it does
- Runs exactly one `(horizon, universe)` validation admission+execution+persistence cycle by calling the existing `services.validation_engine.execute_admitted_validation()` — the identical function the in-process scheduler already calls. No new validation logic, no new persistence path, no new scoring/methodology.
- `--horizon` restricted to `(medium, long)`; `--universe` restricted to `(nifty100, midcap, us)` — via `argparse choices=`, so it structurally cannot request a currently-disabled market/universe.
- Exits `0` only on `{"ok": True}` (or on a rejected admission with `--allow-rejected`, for planned-overlap migration windows); exits non-zero on any failure, rejection (by default), or unhandled exception.
- Inherits every existing safety property from `execute_admitted_validation()` unchanged: the same durable ledger/lease admission (so this worker cannot overlap with a scheduler/catch-up/manual run, or with another instance of itself), the same subprocess-isolated computation, the same DB-connection/queue/executor cleanup described in §2.

### If/when this is approved for production (NOT done here)

1. **Railway service creation** (a production change requiring separate explicit approval):
   ```
   railway service create validation-worker --project a35f6bff-2139-4aa4-9248-0090d82d95a7
   ```
2. **Configuration**: root directory `/backend` (matching the existing `StockSense360` service), no HTTP port/healthcheck (this is a run-to-completion job, not a web service), a Railway Cron Job trigger (Railway's own cron scheduling, distinct from the in-process `asyncio` loops this review found to be the current sole automatic trigger — see PR #79's Stage 1 findings) set to run once per horizon/universe on the weekly Saturday 12:00 UTC cadence PR #79 establishes, invoking e.g.:
   ```
   python validation_worker.py --horizon medium --universe nifty100
   ```
   (one job/cron entry per horizon/universe pair, or a small wrapper script looping over the same 6 pairs the current scheduler iterates).
3. **Cutover**: disable `_validation_schedule_loop()`'s live invocation in `api/main.py` (comment out or remove its `asyncio.create_task(...)` call site) only after the Railway cron jobs are confirmed running successfully for at least one full cycle — never both triggers active simultaneously for the same horizon/universe (the shared ledger/lease would reject the second, but running both is wasted duplicate compute).
4. **Rollback**: delete the Railway cron service/jobs; restore `_validation_schedule_loop()`'s task-creation call in `api/main.py` (a one-line revert). No data migration, no schema change — the ledger/lease/persistence contract is identical either way.

### Recommendation

Given §4's conclusion, **do not proceed with step 1-3 above at this time** — the evidence does not show a problem this migration would fix, and it would add a second deployable artifact (build/deploy/monitoring surface) for a risk that is currently theoretical. Revisit if: (a) a future forensic pass finds a genuine validation-attributable memory event (a `get-service-metrics` point-in-time series, if that capability becomes available, would resolve the §3 evidence gap directly), or (b) Daily Picks' own memory footprint grows again to a point where the residual overlap risk in §4 becomes material.
