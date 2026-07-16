# Product Integrity Workstream #020 — SEC EDGAR Facts Cache Memory Cap

**Status:** Deployed to production (2026-07-16, commit `0af3dbd`) — confirmed working via a full ~1-hour natural US Daily Picks production run with no OOM/stall, well past the point (367/1188 symbols processed) where the prior incident job died.

## 1. Trigger

User noticed (screenshot of `stocksense360.com/picks`, US market): "Base generated Jul 14, 2026, 02:45 AM ET · 54h ago" — the US Daily Picks had not refreshed for over two days. User asked what the issue was.

## 2. Investigation findings

Direct read-only query against production Postgres found `daily_picks_jobs` row `a16c189d-5f64-4a5a-8a5f-2684e21111ed` (market='US') stuck in `running` since 2026-07-16 07:51:23 UTC, last heartbeat 08:08:02 UTC (~4h51m stale at the time of investigation), stopped mid-`phase_1` (367/1188 symbols processed). A prior US job (`e25df775`) had failed the day before (2026-07-15) with `last_error`: *"Operator-finalized orphan: generation worker was terminated by confirmed Railway OOM at approximately 2026-07-15T06:45:30Z; no worker or heartbeat survived the process restart."* A stuck `running` job blocks all new reservation attempts, including automatic startup catch-up, until manually finalized — this was fixed separately as a direct production data correction (see the immediately preceding turn's report), not part of this code release.

This release addresses the underlying recurring cause. Code review found `sec_edgar_adapter.py`'s `_facts_cache: dict[int, tuple[float, Optional[dict]]] = {}` had no size cap or eviction — unlike every other cross-run cache in the prediction pipeline. `prediction_engine.py`'s `_pred_cache`/`_regime_cache` are explicitly capped at 300 entries via a shared `_cache_set` helper, with a comment stating the cap exists specifically "to prevent OOM on free-tier 512MB Render." `_facts_cache` was the one cache in this codebase's SEC EDGAR path that never received the same treatment.

Each entry in `_facts_cache` can hold a full SEC EDGAR companyfacts payload — up to 17 years of XBRL history per issuer. With the US universe raised to 400 symbols (Sprint #014), a single Daily Picks run can populate up to ~400 distinct CIK entries with no eviction, growing the cache unboundedly for the lifetime of the process. This is a well-evidenced, plausible root cause for the observed OOM pattern — consistent with both the 2026-07-15 confirmed OOM and the 2026-07-16 stall — though not provable with certainty from the available data alone, since it coincides with several backend redeploys pushed after the stuck job started.

## 3. Fix

Mirrors the exact pattern already proven safe in `prediction_engine.py`:

- Added `_FACTS_CACHE_MAX = 300` (same cap value as `prediction_engine._CACHE_MAX` — not a new, untested number) and a `_facts_cache_set(cik, value)` helper to `sec_edgar_adapter.py`. On insert, if the cache is at capacity and the key is new, it evicts the single oldest entry (by stored timestamp) before writing.
- `fetch_company_facts(cik)`'s cache write now goes through `_facts_cache_set` instead of a direct dict assignment, under the existing `_facts_lock`.

## 4. What this does not do

- Does not touch any other cache in the codebase — `_pred_cache`, `_regime_cache`, and all others were already capped and are unaffected.
- Does not add new memory profiling, monitoring, or alerting for future OOM causes — this is a targeted fix for the one specific unbounded cache found, not a general memory audit.
- Does not conclusively prove this was the sole cause of the 2026-07-15/16 incidents — it is the most well-evidenced candidate found, consistent with the codebase's own precedent for exactly this failure mode, but redeploy-driven restarts remain a contributing/alternative factor that this fix does not address.
- Does not change `fetch_company_facts`'s retry, TTL, or error-handling behavior — only the cache write path.

## 5. Tests

- New `test_sec_edgar_adapter_facts_cache_cap.py` — 6 tests: cap value matches `prediction_engine._CACHE_MAX` (300); inserting up to the cap retains every entry; exceeding the cap evicts strictly the oldest entry, not an arbitrary one; cache size never exceeds the cap across a simulated 400-symbol run (the exact OOM-triggering scenario); re-inserting an existing key updates in place without evicting; `fetch_company_facts()` itself writes through the capped setter (regression guard against a future edit reverting to a direct dict assignment).
- All 5 pre-existing SEC EDGAR test files re-run unmodified: `test_sec_edgar_adapter_normalization.py`, `test_sec_edgar_adapter_cik_resolution.py`, `test_sec_edgar_adapter_retry_behavior.py`, `test_sec_edgar_adapter_field_extraction.py`, `test_sec_edgar_adapter_stale_tag_defect.py` — 39/39 passed, no regressions.
- Full backend suite: **2193/2193 passed** (2187 baseline + 6 new).
- No frontend changes this release.

## 6. Natural-run verification plan

The next scheduled US Daily Picks cron run (or the next manually-triggered run, once approved) is the first real-world test of this fix together with the prior turn's stuck-job finalization: a full 400-symbol US run completing without a repeat OOM/stall would confirm the fix in production conditions. Will monitor `daily_picks_jobs` status and Railway memory behavior on that next run.

**Confirmed 2026-07-16, same day.** A manually-triggered US Daily Picks run (job `6b880529`, market-open hours) processed the full ~400-symbol universe end-to-end in ~1 hour (13:24–14:21 UTC) with `status: completed`, `last_error: null`, no OOM, no stall — well past the 367/1188-symbol point where the prior incident job (`a16c189d`) died. Production picks confirmed live and dated the same day via `GET /api/picks/daily?market=US`. This is the first real-world confirmation the fix holds under the actual OOM-triggering load pattern; the next natural pre-market cron run (06:00 UTC) is a secondary confirmation opportunity but is no longer the sole evidence.

## 7. Rollback

Single-file, additive change (`sec_edgar_adapter.py`) — reverting the two edits (the `_FACTS_CACHE_MAX`/`_facts_cache_set` addition and the `fetch_company_facts` call-site change) restores the prior unbounded-cache behavior exactly. No schema, no API contract change.
