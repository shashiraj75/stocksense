# StockSense360 — API Memory Hardening (2026-10-19)

**Status:** Implemented and tested locally on branch `fix/api-memory-hardening` (from `origin/main@1a7a8a44`). Not pushed, not deployed. **Awaiting natural-run verification** after any deployment.

## Why
The Phase 0 audit (2026-10-19) found two issues in the long-lived API container: (1) the startup catch-up path can start Daily Picks generation inside the API without the heavy-workload lease and without honouring `DAILY_PICKS_EXTERNAL_WORKER_ONLY`; (2) `web_memory_maintenance` ran a full-heap `gc.collect()` + `malloc_trim(0)` every 60 s, with no recorded measurement that this cadence is needed and a plausible event-loop latency cost (unmeasured).

## Changes
- `backend/api/main.py` — `startup_catchup_enabled()` returns False whenever `DAILY_PICKS_EXTERNAL_WORKER_ONLY` is truthy (`1/true/yes/on`). Behaviour is unchanged when that flag is off. Catch-up was already default-off, so production behaviour today is unchanged.
- `backend/services/web_memory.py` — wakes every 60 s but reclaims only when due: RSS grew by `WEB_MEMORY_RECLAIM_GROWTH_MIB` (default 256) since the last reclaim, or `WEB_MEMORY_RECLAIM_INTERVAL_S` (default 900) elapsed. `reclaim_web_memory()` now returns post-reclaim RSS. Reclaim logic itself (gc + malloc_trim, no cache clearing) is unchanged.
- Tests: new/updated in `test_web_memory_maintenance.py` and `test_startup_catchup_control.py`; the new catch-up test was confirmed to fail with the guard removed.

## Non-claims
Does not prove the API memory slope improves; the change reduces GC frequency (1440 → at most ~96 passes/day absent growth). Does not touch memory_guard thresholds, worker isolation, leases, publication logic, scoring or candidate pools. Does not set `MALLOC_ARENA_MAX` (a Railway environment change requiring separate approval).

## Rollback
Revert the commit, or set `WEB_MEMORY_RECLAIM_INTERVAL_S=60` to restore the previous every-minute cadence without a deploy of code. Catch-up guard: unset `DAILY_PICKS_EXTERNAL_WORKER_ONLY` (not recommended).

## Natural-run verification required
After deploy, compare `[web_memory] rss_before_kib/rss_after_kib` log cadence and the API RSS slope over 48 h versus the prior 48 h; confirm `/health` latency unchanged.
