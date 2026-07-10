# Sprint #014 — Daily Picks Large/Mid/Small-Cap Stratification and Confidence Priority

**Status:** Complete (code + tests + docs). Not yet observed in a real production generation run — see Testing Status.

**Prompted by:** a user question about which India market-cap horizons Daily Picks covers, following the Multibagger pledge-NULL investigation (Sprint report: none dedicated, see `Documentation/STOCKSENSE_DOCUMENTATION.md` §27 Session 10's Multibagger entry). Confirmed via direct code reading, then agreed with the user across a multi-turn discussion (recorded in this session's plan file) before implementation began.

---

## Summary

Daily Picks' universe construction only ever surfaced Large/Mid-cap stocks in both markets:

- **India**: `_get_universe_by_mcap` paged Yahoo's `yf.screen()` sorted by market cap descending, hard-capped at 250 symbols. By SEBI's rank convention (Large = rank 1-100, Mid = 101-250, Small = 251+), this was structurally Large+Mid only — Small cap never entered the pipeline regardless of screener health.
- **US**: a hard `$2,000M` floor built into the query itself excluded true US small-caps (<$2B) by definition, not by rank pressure. Degraded fallback was a hardcoded 100-symbol mega-cap list.
- The real deep-scored pool was smaller still: Phase 0's momentum-ranking step (`_bulk_screen`) truncated to `_N_CANDIDATES` (default 50, shared across all 3 horizons) before the `PredictionEngine` ever saw a candidate.

This sprint replaces Yahoo-screener-based universe discovery with the already-existing, nightly-refreshed `stock_fundamentals_cache` (screener.in for India, yfinance-derived for US — both previously unused by Daily Picks, already maintained for Multibagger), stratifies it into Large/Mid/Small tiers, raises the deep-scored pool from 50 to 400, and changes Phase 5 selection per horizon: short-term now prioritizes confidence (>80%) with fill-down; medium/long-term enforces a tier quota.

## Files Changed

- `backend/services/fundamentals_cache.py` — added `get_ranked_universe(market)`: `(symbol, market_cap)` pairs ordered market-cap descending, sourced from the existing `stock_fundamentals_cache` table. No new columns, no schema change.
- `backend/services/daily_picks.py` — the bulk of the change:
  - Removed entirely: `_collect_in_screener_symbols`, `_select_in_universe_with_retry`, `_classify_screener_error`, `_in_screener_backoff_delay`, and their constants (`_IN_TARGET_UNIVERSE`, `_IN_MIN_HEALTHY_UNIVERSE`, `_SCREEN_PAGE_SIZE`, `_SCREEN_MAX_PAGES`, `_IN_SCREENER_*`) — the entire Release 12B/12C Yahoo-screener pagination/retry mechanism, now dead code once both markets' primary universe path moved off `yf.screen()`.
  - Added: `_assign_cap_tiers(market, ranked)` (rank-based for India, value-based for US), `_stratified_sample(ranked, tiers, quotas)`, `_select_with_tier_quota(candidates, quotas)`, a small local `_classify_error` (non-secret error categorization, replacing the removed screener-specific one).
  - New constants: `_TARGET_UNIVERSE_SIZE = 400`, `_TIER_QUOTA = {"large": 160, "mid": 120, "small": 120}`, `_MIN_MCAP_USD_M_FLOOR = 100` (replaces the old exclusionary `_MIN_MCAP_USD_M = 2000`), `_SHORT_TERM_CONFIDENCE_PRIORITY = 80`, `_MEDIUM_LONG_TIER_QUOTA_6 = {"large": 2, "mid": 2, "small": 2}`, `_MIN_HEALTHY_UNIVERSE = 100` (shared IN/US, replaces `_IN_MIN_HEALTHY_UNIVERSE`).
  - `_get_universe_by_mcap` rewritten to source from `fundamentals_cache.get_ranked_universe()`; same 5-tuple return contract, `universe_used` value renamed `"screener"` → `"fundamentals_cache"`, `selection_meta` additionally carries `tier_map`/`tier_counts`.
  - `_N_CANDIDATES` default raised 50 → 400 (`PICKS_CANDIDATES` env var unchanged in name).
  - Phase 1: `cap_tier` attached to each scored result via the threaded `tier_map`.
  - Phase 5: `top_buy` selection now branches by horizon (confidence-priority fill-down for short; tier-quota selection for medium/long) instead of a flat `all_buy_deduped[:6]` for every horizon.
  - `universe_target_count` payload field now `_TARGET_UNIVERSE_SIZE` (400) for both markets, was `_IN_TARGET_UNIVERSE`-or-`None`.
- `backend/tests/regression/test_in_screener_retry_and_observability.py`, `test_daily_picks_screener_count_limit.py` — **deleted**: both tested the now-removed Yahoo screener retry/pagination mechanism.
- `backend/tests/regression/test_in_universe_expansion.py` — fully rewritten for the cache-sourced stratification (13 tests): tier boundaries (both markets' conventions), junk floors, thin-cache/exception fallback truthfulness, sizing invariants.
- `backend/tests/regression/test_daily_picks_us_universe_guard.py` — 3 tests updated to mock `fundamentals_cache.get_ranked_universe` instead of `yf.screen`; all ETF/SPAC/preferred-share heuristic-filter tests and anchor-content tests left untouched (still valid, unaffected by this change).
- `backend/tests/regression/test_daily_picks_output_integrity.py` — 3 tests updated for the new `screener_raw_count` semantics (now the cache's raw positive-market-cap row count, captured before the US eligibility filter — a real bug caught and fixed mid-implementation, see Errors Found below).
- `backend/tests/regression/test_daily_picks_phase5_tier_and_confidence_selection.py` — new, 9 tests covering `_select_with_tier_quota` (top-N-per-tier, leftover top-up, alpha-ordered final display, unknown-tier handling) and the short-term confidence-priority fill-down logic (priority over higher alpha, fill-down, zero-high-confidence day, empty list, alpha as secondary key).
- `Documentation/STOCKSENSE_DOCUMENTATION.md` §13 — corrected Phase 1 (was still describing "Nifty 100" and Yahoo screener pagination) and Phase 5 ("top 5" → "top 6", documented the horizon-specific selection rules); added a Session 10 changelog entry.
- `Documentation/Engineering-Handbook/Operations/Current-Release-Status.md` — noted Release 12B's original universe-construction scope is superseded; any future validation must be re-scoped against this sprint's logic.

## Architecture Changes

The universe-discovery boundary moved from a live Yahoo Finance screener call to a read from the existing `stock_fundamentals_cache` table — no new provider, no new schema, no new nightly job (both markets' refresh jobs already existed for Multibagger). This is a data-source substitution at one well-defined function boundary (`_get_universe_by_mcap`), not a pipeline redesign — Phases 2 through 8 are structurally unchanged; only what feeds Phase 1 and how Phase 5 slices its final 6 differ.

## Risks

- **Phase 1 runtime increases materially** (~8x candidate count, sequential scoring unchanged) — estimated ~60-90 minutes total generation time versus the prior ~10-20 minutes. Confirmed acceptable given both markets' multi-hour runway before market open, but this is a real operational change worth another engineer noticing, not a free lunch.
- **US small-cap junk floor ($100M) is a judgment call**, explicitly flagged as adjustable during planning — not empirically calibrated against real US small-cap data quality in `stock_fundamentals_cache` (unlike India's ₹100 Cr floor, inherited unchanged from the prior release).
- **`_MEDIUM_LONG_TIER_QUOTA_6` (2/2/2)** is a simplification of the population-level 40/30/30 split, chosen because 6 slots doesn't divide cleanly — reasonable, but a different rounding choice (e.g. 3/2/1) was equally defensible and this one wasn't re-confirmed with the user after the initial 40/30/30 population-level agreement.
- **Short-term can now legitimately show fewer than 6 (or 0) picks** on a low-conviction day — an intentional design choice per explicit user instruction, but a behavior change from today's near-always-6 pattern that could read as "broken" to a user unfamiliar with the new rule until the UI/copy communicates it (not addressed by this sprint — backend only).
- **No live production generation run has exercised this yet** — see Testing Status.

## Migration Notes

- `PICKS_CANDIDATES` env var (if set in Railway) now controls a 400-default instead of 50 — check for an explicit override before assuming the new default takes effect.
- Any external tooling/dashboards reading `universe_used == "screener"` from the Daily Picks payload will need updating to `"fundamentals_cache"` — the value changed, the field did not.
- `IN_SCREENER_MAX_ATTEMPTS`/`IN_SCREENER_RETRY_BACKOFF_SECONDS`/`IN_SCREENER_RETRY_MAX_DELAY_SECONDS`/`IN_SCREENER_RETRY_JITTER_SECONDS` env vars, if set anywhere in deployment config, are now inert (the code reading them was deleted) — safe to leave or remove.

## Testing Status

- **1517/1517 full backend suite passing** locally.
- New/updated coverage: tier-boundary edge cases (both markets' conventions), stratified-sampling honesty when a tier is short (no cross-tier backfill at the Phase-1-pool stage), thin-cache/exception fallback truthfulness, tier-quota top-N-and-top-up-from-leftover behavior, short-term confidence-priority fill-down (including a zero-high-confidence day and an alpha-as-secondary-key check).
- Sanity-checked per this repo's SES-003 §4 discipline: deliberately removed the final alpha re-sort from `_select_with_tier_quota`, confirmed the corresponding test failed with a clear message, restored the file byte-identical, confirmed the suite green again.
- **What is not covered**: no live end-to-end run of `_generate_picks_inner` against real market data — per standing protocol, the production `/api/picks/generate` endpoint was never called directly during this sprint. Verification was via unit tests and direct read-only production checks (confirmed via the live Multibagger endpoint that `stock_fundamentals_cache` is genuinely populated and fresh for both markets — India: 52/167/73 across its three screens; US: 19 for `quality_compounder`, refreshed same-day). The actual Daily Picks generation behavior (real tier diversity in the output, real confidence distribution for short-term, real total runtime) has not yet been observed and should be checked read-only after the next natural scheduled run.

## Errors Found and Fixed During Implementation

- **`cache_raw_count` was captured after the US eligibility filter, not before** — contradicted the function's own docstring ("before local eligibility filtering") and made `screener_raw_count` silently mean something different for US than for India. Caught by a test assertion mismatch (`153 == 154`), not by inspection; fixed by capturing the count before the US-only filter reassigns the `ranked` list.
- Two test-data mistakes in newly-written tests (synthetic `PAD0..PADn` padding symbols that don't exist in the real US eligible-ticker set, so they were correctly stripped by the eligibility intersection, breaking the test's own count assertion) — fixed by sourcing real tickers from `_US_DAILY_PICKS_HEURISTIC_FILTERED_SET` for padding instead of fabricated ones.

## Recommendations for the Next Sprint

- After the next natural scheduled generation run (both markets), do a read-only inspection of the real payload: confirm tier diversity actually appears in medium/long picks, confirm short-term confidence distribution matches expectation, confirm total generation time lands in the estimated ~60-90 minute range rather than timing out or hitting an unexpected resource ceiling on Render's free tier.
- Revisit the US $100M small-cap junk floor once real data-quality evidence exists for that tier in `stock_fundamentals_cache` — it was a reasoned placeholder, not empirically calibrated.
- Consider whether the frontend needs any copy update for short-term's new "can legitimately show fewer than 6" behavior, so a sparse short-term day doesn't read as a bug to a user.
