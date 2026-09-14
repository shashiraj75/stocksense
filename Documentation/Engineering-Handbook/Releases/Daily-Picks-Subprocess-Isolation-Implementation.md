# Daily Picks Subprocess Isolation — Implementation

**Status:** Implemented, kill-switch disabled by default. Not yet deployed/activated. Implements [Daily Picks Cross-Run Memory Ratchet — Investigation and Subprocess Isolation Design](../Architecture/Daily-Picks-Cross-Run-Memory-Ratchet-Investigation-and-Subprocess-Isolation-Design.md) exactly, per that document's §6.2 boundary and §6.3 named risks.

## 1. What changed

`backend/services/daily_picks.py`:

- `_subprocess_isolation_enabled(market)` — the kill switch, **per-market** (`DAILY_PICKS_SUBPROCESS_ISOLATION_ENABLED_IN` / `_US`, mirroring Growth/Valuation Intelligence's own `_IN`/`_US` confidence flags — the first cut of this function shipped as a single combined flag, corrected before activation once it was clear that couldn't express the intended staged rollout, India first). Reads fresh on every call (never cached at import time), rejecting anything but the literal `"1"`; a market that resolves to neither `"IN"` nor `"US"` defaults OFF rather than being silently treated as either market's setting. Defaults OFF for both markets.
- `_daily_picks_subprocess_target(market, job_id, result_queue)` — the child process entry point. A plain top-level function (required for `multiprocessing`'s `spawn` start method, which looks it up by qualified name in the freshly-imported child). Configures its own `logging.basicConfig()` if nothing is already configured — a spawned child starts with zero logging handlers, so without this every `log.info`/`log.warning` call inside `_generate_picks_inner` (including every `memory_guard` line) would silently vanish. Calls `_generate_picks_inner(market, job_id=job_id)` unchanged and puts `("ok", payload, persisted_at)` on the queue; any exception is caught and put as `("error", str(e), traceback.format_exc())` — only strings cross the process boundary, never the exception object itself (not everything raised deep in this pipeline is guaranteed picklable).
- `_generate_picks_isolated(market, job_id)` — spawns the child via `multiprocessing.get_context("spawn")` (never `"fork"` — a fresh interpreter with no inherited threads/DB connections/fragmented arenas is exactly the clean-slate guarantee this fix depends on), polls for a result with a 1-second interval bounded by `DAILY_PICKS_SUBPROCESS_TIMEOUT_S` (default 3600s), and returns `(payload, persisted_at)` on success. Any child-side exception is re-raised as a `RuntimeError` so `generate_picks()`'s existing `except Exception` handling needs no change. A child that dies without ever reporting a result (an OS-level kill, e.g. an OOM-kill, bypasses the child's own exception handler entirely) is detected via the liveness poll — not by waiting out the full timeout — and raises a distinctly-labeled `RuntimeError` naming the exit code.
- `generate_picks()`'s call site now branches on `_subprocess_isolation_enabled()`: isolated path when on, the original direct call when off. With the flag off (the shipped default), behavior is byte-for-byte identical to before this change.

## 2. Boundary discipline (per the design study's §6.2)

`_generate_picks_inner()` already performs its own Postgres persistence internally (returning `persisted_at` as evidence of that) — this was confirmed by direct code reading before implementation, correcting the design study's initial assumption that persistence needed to move to the parent. Because `"spawn"` (not `"fork"`) is used, the child never inherits an open DB connection/session from the parent — it re-imports and opens its own from scratch, exactly as this same process already does at every normal cold start. No change to where persistence happens was needed; the child simply does what `_generate_picks_inner` already did, under its own freshly-opened connection.

The heartbeat thread, job-lease/reservation, terminal-status marking, and the three post-success side effects (`weight_adapter.run_adaptation`, Telegram, the Intelligence Engine shadow slice) all stay in the parent process, entirely unchanged — none of them are part of the fragmentation-heavy path Phase 1 causes, and moving them would add risk with no benefit.

## 3. Named risks from the design study — resolved

1. **Job-cancellation semantics** — confirmed via direct search: no cancellation mechanism exists anywhere in `api/routers/picks.py`, `daily_picks.py`, or `postgres_store.py` today. Nothing relies on same-process interruption; subprocess isolation introduces no regression here (and would make a future cancel feature easier — killing a child process is a clean, guaranteed-effective primitive cooperative Python-level cancellation is not).
2. **Logging** — resolved via `_daily_picks_subprocess_target`'s explicit `logging.basicConfig()` fallback.
3. **Child-OOM detection** — resolved via `_generate_picks_isolated`'s liveness-poll loop, distinct from the timeout branch.
4. **Startup-singleton regressions** — not applicable: `_generate_picks_inner` runs entirely inside the child (not split across the boundary), which does a fresh full re-import exactly like any normal process cold start — structurally identical to what this app already does on every Railway boot, not a new code path.
5. **Test strategy** — see below.

## 4. Tests

New `tests/regression/test_daily_picks_subprocess_isolation.py`, 11 tests, following this codebase's own established convention (Product Integrity #023 §5) for functions too large/externally-integrated to mount fully end-to-end — split into what each layer can actually test, since a genuinely `spawn`ed child re-imports the module fresh and cannot be reached by a parent-process monkeypatch:

- **Kill-switch dispatch** (3 tests): default-off uses the direct path; explicitly-on uses the isolated path; only the literal `"1"` enables it (fail-safe against a truthy-string trap like `"true"`/`"yes"`).
- **`_daily_picks_subprocess_target`'s queue-put contract** (3 tests): success puts an `"ok"` tuple; an exception puts an `"error"` tuple with message + traceback text; an exception whose `__reduce__` deliberately raises `TypeError` (simulating an unpicklable exception) still produces a plain string/traceback pair, never the raw object.
- **`_generate_picks_isolated`'s outcome-handling logic** (4 tests), via a fully-controlled fake `multiprocessing` context (a real `spawn`ed subprocess can't be monkeypatched mid-run, so this exercises the real polling/branching *logic* without needing an OS process for every case): success returns the payload; a child-reported error raises `RuntimeError` with the child's message; a child that dies without ever reporting a result raises a distinctly-labeled `RuntimeError`; a process that never dies and never reports is terminated and raises a "timed out" `RuntimeError`.
- **Real environment-capability smoke test** (1 test): a genuine `multiprocessing.get_context("spawn")` process (not mocked) running a trivial, unrelated target function, confirming this sandbox actually permits process spawning — a real, disclosed check, not an assumption. **Passed** in this session's environment.

Sanity-checked per SES-003 §4: temporarily hardcoded `_subprocess_isolation_enabled()` to always return `True`, confirmed the default-path dispatch test failed with a clear assertion (proving the test actually exercises the real dispatch logic, not a tautology), then restored the file byte-identical and reconfirmed all 11 tests green.

One genuine, unrelated pre-existing defect found and fixed during this sprint's own test-writing (not this sprint's fix): a prose reference to `raw[horizon]` inside this file's new docstring text collided with `test_daily_picks_raw_memory_release.py`'s "no live `raw[` reference anywhere" regex check (that check only excludes `#`-prefixed comment lines, not docstring content) — reworded to "per-horizon accumulator release," a wording-only change with no behavior implication, confirmed the pre-existing test passes again afterward.

Full targeted verification, run individually per file to isolate any slow/network-bound pre-existing tests from this change's own signal: every `test_daily_picks_*.py` and `test_memory_guard*.py` regression file (28 of 30, excluding `test_daily_picks_stress_rss.py` and `test_daily_picks_memory_bound_synthetic_workload.py`, both pre-existing and genuinely slow by design — real synthetic heavy-workload simulations, not hung — deliberately not re-run in full this session given no code path they exercise was touched) — **306/306 passing.** One pre-existing, disclosed, unrelated failure found: `test_daily_picks_governed_recovery.py`'s 4 `@pytest.mark.asyncio`-marked tests fail in this local sandbox because `pytest-asyncio` is not installed here (confirmed: it is the only regression file among all 30 using that marker) — an environment/dependency gap, not a code defect, and not touched by this change's purely additive diff (152 insertions, 1 wording-only edit, confirmed via `git diff --stat`).

## 5. What this does not do

- Does not enable the kill switch anywhere — shipped disabled by default, same rollout posture as every other kill switch in this codebase (Growth/Valuation/RCI). Activation is a separate, later decision requiring natural-run memory-metric evidence per the design study's own recommendation (§8).
- Does not change the Daily Picks candidate-pool size in either market — the original +50/+50 request that triggered this whole investigation remains **not implemented**, pending activation evidence that this fix actually flattens the cross-run ratchet.
- Does not modify `memory_guard.py` — its threshold-based abort logic runs unchanged inside the child (it reads `/sys/fs/cgroup`, container-wide regardless of which process reads it).
- Does not change job-lease semantics, the heartbeat mechanism, or any of the three post-success side effects — all confirmed to stay in the parent process, untouched.
- Does not prove the fix works in production yet — that requires natural-run verification (§6 below), not implemented/observed this session.

## 6. Rollout plan (not executed this session)

1. Enable `DAILY_PICKS_SUBPROCESS_ISOLATION_ENABLED_IN=1` first (India — the market whose baseline climbed fastest in the design study's Sep 7-11 evidence, so the fix's effect is most visible soonest). `_US` stays unset.
2. Observe at least 3 natural (scheduler-fired, never manually-triggered) runs via Railway's real memory metrics — confirm the post-run floor no longer climbs day-over-day the way the design study's evidence showed, and confirm generation still completes successfully with `payload`/`persisted_at` behaving identically to the pre-fix path (job status, picks content, Telegram notification, weight_adapter firing).
3. Only after that evidence is in hand, enable for US, then reconsider the original candidate-pool increase request with real, current headroom data.

## 7. Rollback

Two independent kill-switch env vars — unsetting `DAILY_PICKS_SUBPROCESS_ISOLATION_ENABLED_IN`/`_US` (or setting either to anything other than `"1"`) restores the exact prior direct-call behavior for that market, with no further code change, deploy, or migration required. Each market can be rolled back independently of the other.
