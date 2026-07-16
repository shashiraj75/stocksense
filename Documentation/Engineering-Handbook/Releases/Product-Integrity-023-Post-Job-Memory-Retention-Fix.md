# Product Integrity Workstream #023 — Post-Job Memory Retention Fix

**Status:** Implemented and tested. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

## 1. Trigger

User noticed a "⚠ 2" Out of Memory badge on the Railway dashboard for the StockSense360 service, despite the service showing "Online." Investigated via Railway's own memory metrics (`railway metrics --raw --memory --since 24h --json`) rather than guessing.

## 2. Investigation findings

Two of the three detected memory "cliffs" (sharp drops from several GB to near-zero, a restart signature) in the last 24h correspond exactly to normal deploy-triggered restarts (06:24 UTC and 08:04 UTC, matching the PI-011/PI-012 deploy timestamps) — not concerning.

The third, at **15:48 UTC today, is a genuine OOM.** Memory climbed from ~1GB starting at 13:24 UTC (when the manually-triggered US Daily Picks job began), hit **7.86GB of the 8GB container limit (98%)** by 14:16 UTC — while the job was still running — and then **stayed pinned at 98% for another 90 minutes after the job had already completed successfully** (completed_at 14:21 UTC). At 15:48 UTC it crashed to 0.53GB with no corresponding deploy — the container was OOM-killed and Railway auto-restarted it.

A dispatched investigation (grep/read against `origin/main`, not the stale local checkout) found the most likely contributor, ranked by plausible size:

1. **`generate_picks()`'s `raw` accumulator** (`daily_picks.py`) — Phase 1 predicts every candidate × horizon (for a US run: ~400 symbols × 3 horizons ≈ 1,200 entries) and holds **all of them simultaneously**, including full reasoning text, quality-factor breakdowns, and bull/bear cases, for the entire rest of the function — even though the per-horizon ranking loop that follows only ever processes one horizon at a time, and the one consumer that needs the full set (`_write_score_snapshots`) already finished before that loop starts. Confirmed by direct inspection: `raw` is read exactly once per horizon (`items = raw[horizon]`) and never referenced again.
2. **Ruled out**: `_pred_cache`/`_regime_cache` (prediction_engine.py) are correctly capped at 300 entries each (verified, no regression) — bounded at roughly 10–30MB total, not a growth driver.
3. **Ruled out**: `INTELLIGENCE_ENGINE_SHADOW_ENABLED` is unset in production (confirmed via `railway variables`), so the shadow intelligence engine's background thread never starts.
4. **A real, but unquantified, timing gap**: after the job is marked `completed`, `daily_picks.py`'s `finally` block fires `run_adaptation` (weight_adapter.py) as an **un-joined daemon thread**. Because it's never joined, the job can legitimately show `completed` in Postgres while this thread — which unconditionally retrains a regime-clustering model on historical macro data (step 3, independent of production-learning containment) — is still running. This lines up with the 90-minute post-completion stall window, though its own memory footprint wasn't separately measurable before this release.

## 3. Fixes

- **`daily_picks.py`**: immediately after `items = raw[horizon]` captures the reference each horizon's ranking/selection loop needs, set `raw[horizon] = None`. This drops the dict's own reference to that horizon's ~400 full result entries, so they become garbage-collectible once the loop's local variables (`universe`, `ranked`, `all_buy`, etc.) get rebound at the start of the next horizon — instead of staying alive, referenced by `raw`, for the entire remainder of the function. At most one horizon's full candidate pool is now held at a time instead of all three simultaneously.
- **`weight_adapter.py`**: added start/completion timestamp logging (`[weight_adapter] Starting adaptation cycle (US) …` / `Adaptation cycle complete (US) in {elapsed:.1f}s.`) to `run_adaptation`. This doesn't change behavior (the thread is still un-joined, still daemon, still fired the same way) — it's purely observability, so a future incident can directly confirm or rule out how long this thread actually runs past job completion, instead of relying on circumstantial timing correlation like this investigation had to.

## 4. What this does not do

- Does not join the `run_adaptation` thread or otherwise change when `generate_picks()` returns — joining would make the HTTP request/response cycle wait on a background retrain, a real behavior change with its own risk, and wasn't the ask here. This release only adds visibility.
- Does not prove finding #4 (the background thread) is the sole or even primary cause of the 90-minute plateau — it's a plausible, evidenced contributor whose actual footprint is now measurable via the new logging, not conclusively sized.
- Does not address a fifth, unquantified possibility the investigation flagged: CPython/glibc allocator fragmentation leaving RSS elevated even after all live objects are freed. This is a plausible secondary factor but isn't something a targeted code change can fix — if the primary fix (finding #1) doesn't fully resolve the plateau on the next heavy run, this is the next thing to investigate.
- Does not increase the Railway memory limit — the user explicitly chose "investigate now" over "bump the limit" as the immediate action; a limit increase remains available as a fallback if the code fix doesn't fully resolve the issue on the next natural run.
- Does not touch ranking, scoring, portfolio optimization, or any published Daily Picks output — purely a memory-lifecycle change to an already-consumed intermediate structure, plus additive logging.

## 5. Tests

- New `test_daily_picks_raw_memory_release.py` — 4 structural tests (following this codebase's established convention for functions too large/externally-integrated to mount end-to-end, matching PI-011/#012's own approach): the release happens immediately after capture, not deferred past the point where copies already exist; it applies before the empty-horizon early-return so no horizon is skipped; `raw` is never read again anywhere after the horizon loop starts (comment-text mentions of "raw[horizon]" explicitly excluded from the check, so this doesn't false-positive on its own docstring); `_write_score_snapshots` is confirmed to run before the release loop, which is what makes the release safe.
- New `test_weight_adapter_timing_instrumentation.py` — 3 tests: start and completion are both logged; the completion log's elapsed-duration suffix parses as a valid non-negative float; timing logs fire regardless of production-learning containment state (the memory question this exists to answer is independent of that flag).
- Full backend suite: **2200/2200 passed** (2193 baseline + 7 new).
- No frontend changes this release.

## 6. Natural-run verification plan

The next natural or manually-triggered US Daily Picks run (the highest-memory scenario, ~400 symbols) is the first opportunity to confirm this actually resolves the plateau. Check via `railway metrics --raw --memory --since <window> --json`: memory should still climb during Phase 1 (expected — real work is happening), but should decline as each horizon's ranking completes, rather than staying pinned near the container limit for an extended period after the job's `completed_at` timestamp. Also check the new `[weight_adapter]` start/completion log timestamps against the job's `completed_at` to directly measure how long that background thread runs past job completion, resolving finding #4 with evidence instead of correlation.

## 7. Rollback

Two-file, additive/non-structural change: reverting `daily_picks.py`'s `raw[horizon] = None` line restores the prior (memory-heavier) retention behavior; reverting `weight_adapter.py`'s timing logs removes the new log lines only — neither has any schema, API contract, or ranking/scoring effect.
