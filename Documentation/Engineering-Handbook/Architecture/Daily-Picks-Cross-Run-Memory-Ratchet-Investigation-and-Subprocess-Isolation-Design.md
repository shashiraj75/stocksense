# Daily Picks Cross-Run Memory Ratchet — Investigation and Subprocess Isolation Design

**Status:** Design Study only. No production code changed. Trigger for this document was a user request to widen the Daily Picks candidate pool by +50 in both markets; investigating current headroom (per this codebase's own established precedent — Sprint #014's India candidate-pool feasibility check) found the service already has zero safe headroom for that change, for a reason distinct from Sprint #014's own India finding.

## 1. Trigger

User asked to add 50 candidates to each market's Daily Picks pool (US 400→450, India 370→420). Before implementing, this codebase's own precedent ([Sprint #014](../Releases/Sprint-014-Daily-Picks-Cap-Stratification-and-Confidence-Priority.md)) required checking real production memory headroom first — that sprint's own +50 request for India alone was reduced to +30 after finding insufficient headroom. Doing the same check here surfaced a live, current, and worse problem than requested-scope tuning could fix.

## 2. Evidence gathered (read-only; no code, config, or job triggered)

**Railway container metrics (StockSense360 service, 8GB limit), last 7 days:**
- Peak: 7.23 GB (90.3%)
- Average: 4.51 GB (56.4%)
- Last 24h average alone: 6.04 GB (75.5%) — trending up, not a stale historical spike.

**`memory_guard` application-level logs, Sep 7–11 (5 consecutive scheduled runs per market):**

| Day | Market | Used at run start | Peak this run | Cleanup/warning fired? |
|---|---|---|---|---|
| Sep 7 | US | 14.8% | 51% | No |
| Sep 7 | IN | 44.7% | 52% | No |
| Sep 8 | US | 52.5% | 72% | **Yes (cleanup @72.2%)** |
| Sep 8 | IN | 64.5% | 62% | No |
| Sep 9 | US | 67.2% | 73% | **Yes (cleanup @72.6%)** |
| Sep 9 | IN | 74.8% | 75% | **Yes (cleanup @72.1%)** |
| Sep 10 | US | 20.6% ← reset | 52% | No |
| Sep 10 | IN | 45.7% | 52% | No |
| Sep 11 | US | 50.6% | 70% | **Yes (warning @65.1%)** |

The "used at run start" baseline climbs day over day within each multi-day window (US: 14.8%→52.5%→67.2%; India: 44.7%→64.5%→74.8%) until something resets it (Sep 10's US run started at only 20.6%, consistent with a routine container restart) — then the climb resumes. **The system is already hitting its own emergency cleanup/warning threshold in 4 of the 9 runs shown, at today's candidate counts, with zero increase.**

## 3. What this investigation ruled out

- **`weight_adapter`'s un-joined background thread** — the leading suspect named in [Product Integrity #023](../Releases/Product-Integrity-023-Post-Job-Memory-Retention-Fix.md) (the 2026-07-16 fix that added timing logs but never joined the thread). Direct log evidence for every run Sep 8–11: `Adaptation cycle complete` fires **4.9–5.8 seconds** after `Starting adaptation cycle`. Production learning is currently disabled ("legacy training data quarantined" / "containment active — meta-model retraining skipped") — no heavy retraining occurs. This is not today's cause.
- **Unbounded per-symbol provider caches** — checked `market_data.py`, `nse_client.py`, `screener_data.py`, `bse_data.py`, `nse_pledge.py`, `sec_edgar_adapter.py`, `finnhub_client.py` directly: every one already has a size cap + oldest-entry eviction (the pattern [Product Integrity #020](../Releases/Product-Integrity-020-SEC-EDGAR-Facts-Cache-Memory-Cap.md) established). Not the cause.
- **`heatmap_service`/`screener_service` caches** — keyed by market string only (≤2 entries possible). Not the cause.

## 4. What this investigation confirmed is NOT yet fixed

Two remediations for this exact symptom already exist in the code, and **both are still active but insufficient**:

1. [Product Integrity #023](../Releases/Product-Integrity-023-Post-Job-Memory-Retention-Fix.md) (2026-07-16) — released the `raw[horizon]` accumulator within a single run's ranking loop. Confirmed still structurally present in spirit (the accumulator has since been refactored under Sprint #014's chunked candidate-major rewrite; the specific `raw[horizon] = None` line no longer exists verbatim because `raw` itself was restructured, but the underlying per-horizon processing shape is unchanged).
2. A **2026-08-24 "sustained-high-baseline remediation"** (`backend/services/daily_picks.py:1562-1589`) — added `MemoryCircuitBreaker.release_memory("generate_picks_end")` (safe-cache clear + `gc.collect()` + `malloc_trim()`) to the `finally` block on **every** terminal path (success or exception), specifically because cleanup previously only ran at the *next* run's start. Its own code comment already named the baseline this was meant to fix: "~4.4-4.9GB that never falls even when CPU is idle."

**The Sep 7–11 evidence shows the ratchet continuing despite both fixes being active and running twice per day per market (once at run-end, once at the next run's start).** Since `gc.collect()` + `malloc_trim()` run on a schedule that gives them every opportunity to reclaim everything reclaimable, and the floor still climbs, this points to exactly the one possibility [Product Integrity #023 §4 point 5](../Releases/Product-Integrity-023-Post-Job-Memory-Retention-Fix.md) named but explicitly could not rule out at the time: **glibc allocator arena fragmentation from Phase 1's heavy per-candidate pandas/numpy construction — a category of memory `malloc_trim(0)` cannot always reclaim**, because it is not a Python-level leak (nothing for `gc.collect()` to find) but genuinely fragmented C-heap arenas that the allocator itself won't defragment while the process stays alive.

This is consistent with, not contradictory to, both prior fixes: they correctly addressed the Python-object-retention component of the problem (and measurably helped — the 08-24 comment's baseline of "4.4-4.9GB" is close to what several of the Sep 7-11 run-starts show before climbing further) without being able to address the allocator-fragmentation component, which no Python-level fix can.

## 5. Why this is a genuine safety blocker for the original request

Both markets are already frequently touching the 65-72% mitigation thresholds at today's candidate counts (US 400, India 370) — the same counts Sprint #014 arrived at after India's own +50→+30 reduction. Adding another +50 to either market would increase Phase 1's per-run allocation volume (more candidates × 3 horizons × full DataFrame-backed prediction each), directly increasing the fragmentation this run already generates — on top of a baseline that's already climbing across days independent of any candidate-count change. This would very plausibly convert today's "occasional cleanup-threshold warning" into a real OOM crash, repeating [Product Integrity #023](../Releases/Product-Integrity-023-Post-Job-Memory-Retention-Fix.md)'s original incident (98% container memory, 90-minute stall, OOM-kill).

## 6. The proposed permanent fix: subprocess isolation for generation

Process exit is the one mechanism the OS guarantees will reclaim 100% of a process's memory — including fragmented, otherwise-unreclaimable allocator arenas. No amount of `gc.collect()`/`malloc_trim()` inside a long-lived process can match that guarantee, because both are best-effort requests to an allocator that is free to keep arenas it judges still useful. Running each Daily Picks generation in its **own short-lived subprocess** — which starts clean and is guaranteed to hand back everything it touched when it exits — converts the fragmentation ratchet from "must be prevented every single run, forever, by every future code path" into a structural property that requires no per-feature vigilance.

### 6.1 Current architecture (relevant surface, from direct code reading)

`generate_picks(market, job_id)` (`backend/services/daily_picks.py:1451`), called from an in-process background task, today does all of the following **in the same OS process** that also serves live API traffic:
- Starts a heartbeat daemon thread (`_heartbeat_loop`) that periodically updates the job's Postgres row so a stuck/crashed job is externally detectable.
- Calls `_generate_picks_inner()` — the actual heavy Phase 0–7 computation (bulk screen, per-candidate × per-horizon prediction, regime detection, ranking, portfolio optimization, prediction logging). This is the allocation-heavy part responsible for the fragmentation.
- Marks the job row `completed`/`failed` in Postgres directly.
- In its `finally` block: releases memory (existing fix), then fires three **non-critical, best-effort, already-isolated** post-success side effects as separate daemon threads/calls — `weight_adapter.run_adaptation` (confirmed fast, §3), Telegram notification, and (if enabled) the Intelligence Engine shadow slice.

### 6.2 Proposed shape

- Move exactly `_generate_picks_inner()`'s execution into a **spawned** (not forked — `spawn` starts a genuinely clean interpreter, avoiding known fork-in-a-multithreaded-process hazards and guaranteeing the fragmentation-free starting state this fix depends on) child process, via `multiprocessing` or `concurrent.futures.ProcessPoolExecutor` with `mp_context=multiprocessing.get_context("spawn")`.
- **Boundary discipline (mirroring this codebase's own established precedent — Epic 005 Sprint #007's "dedicated read-only composer" decision for RCI, chosen specifically to avoid a shared-mutable-state hazard)**: the child process should be a pure compute boundary — it receives `(market, job_id)`, runs `_generate_picks_inner()`, and returns the resulting `(payload, persisted_at)` tuple back to the parent via the process pool's own IPC (pickling a plain dict/datetime is unremarkable). **All Postgres writes (job-terminal marking, picks persistence) stay in the parent process**, exactly as today — SQLAlchemy sessions/connection pools are not safely shareable across a process fork/spawn boundary, and keeping all DB I/O in one process avoids that whole class of hazard rather than requiring the child to open its own pool.
- The heartbeat thread, job-lease semantics, terminal-status marking, and the three post-success side effects all stay in the parent process, **unchanged** — none of them are part of the fragmentation-heavy path, and moving them would add risk with no benefit.
- `memory_guard`'s threshold-based abort logic should run **inside the child** (it reads `/sys/fs/cgroup`, which reflects the whole container regardless of which process reads it, so this requires no change to `memory_guard.py` itself) — an abort inside the child should propagate back to the parent as a normal exception via the process pool's own result-retrieval path, which already surfaces child exceptions this way.
- Expected one-time cost: a spawned process must re-import the full dependency set (pandas/numpy/sklearn/yfinance/etc.) — a few seconds, at most twice a day per market, against a run that already takes 20-50 minutes. Disclosed as a deliberate, acceptable trade-off, not a hidden regression.

### 6.3 Named risks / open questions for the implementation sprint to resolve (not resolved here)

1. **Job-cancellation semantics**: does any code path currently rely on being able to interrupt `_generate_picks_inner()` mid-run from within the same process (e.g., a request timeout, a manual "cancel job" admin action)? If so, subprocess isolation actually *simplifies* this (killing a child process is a clean, guaranteed-effective cancellation primitive Python-level cooperative cancellation is not) — needs to be confirmed against actual current behavior, not assumed.
2. **Logging**: `_generate_picks_inner()`'s extensive `log.info`/`log.warning` calls (including every `memory_guard` line quoted in §2) must still reach the same log sink from a child process — needs explicit verification against however this repo's logging is configured (handler inheritance under `spawn` is not automatic the way it is under `fork`).
3. **Exactly-once semantics under a crash**: today, if `generate_picks()` crashes, the existing `except Exception` branch marks the job `failed` with a bounded error. If the *child* process is killed by the container OOM-killer (not a Python exception), the parent must detect that (a dead/non-responsive child) and mark the job failed itself, rather than hanging — this is a new failure mode subprocess isolation introduces and must be explicitly handled and tested, not assumed away.
4. **Startup regression scope**: confirm via a controlled test that spawn-based child startup doesn't silently break any module-level singleton/cache initialization pattern the codebase relies on (e.g., any `_cache: dict = {}` module global that some other request path assumes is warm).
5. **Test/CI feasibility**: this repository's test suite runs in-process; the new subprocess boundary needs its own dedicated test strategy (structural tests proving the boundary + a small number of real subprocess-execution tests), following this codebase's own established convention (per PI-023 §5) for functions too large/externally-integrated to mount fully end-to-end.

## 7. What this document does not do

- Does not implement any code change. Zero lines of production code modified.
- Does not increase the Railway memory limit — available as PI-023's own named fallback if an immediate stopgap is wanted before the subprocess sprint lands, but not implemented here (user's explicit choice this session was to scope the permanent fix, not take the stopgap).
- Does not change the Daily Picks candidate-pool size in either market — the original +50/+50 request remains **not implemented**, pending this fix (or a future re-check once headroom is genuinely restored).
- Does not conclusively prove allocator fragmentation is the *only* remaining contributor — named as the most evidence-consistent explanation given everything else was directly ruled out, not asserted as certain. If a future implementation of §6 does not fully flatten the cross-run ratchet, that would be the signal to keep investigating rather than to consider this closed.

## 8. Recommendation

**Ready for a dedicated Subprocess Isolation Implementation Sprint** — scoped exactly to §6.2, with §6.3's five risks each explicitly resolved (not deferred) before merge, full before/after Railway memory-metric comparison across at least 3 natural runs per market as the sprint's own completion evidence (mirroring this codebase's own natural-run-verification standard), and the original +50/+50 candidate-pool request revisited only after that evidence confirms the ratchet is gone.
