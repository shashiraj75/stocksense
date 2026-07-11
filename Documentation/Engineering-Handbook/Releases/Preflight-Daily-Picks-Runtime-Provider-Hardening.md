# Preflight — Daily Picks Runtime/Provider Hardening

**Status:** Preflight/planning only. No implementation in this document. No production generation endpoint was called to produce this note; markets were closed when it was written.

**This is not Release 12B validation.** It does not claim any validation passed, does not enable the scheduler, and does not change Daily Picks generation, scoring, RCI, or any prediction/valuation/growth/financial engine. The next proper validation still requires a genuine fresh market-day natural run for both India and US — this note exists to scope what to inspect *before* that run, not to substitute for it.

---

## 1. Current gate (unchanged by this note)

- **Release 12B / Sprint #014 validation remains pending.** See `Documentation/Engineering-Handbook/Operations/Current-Release-Status.md` (Release 12B entry) and `Documentation/Engineering-Handbook/Releases/Sprint-014-Daily-Picks-Cap-Stratification-and-Confidence-Priority.md` (Testing Status: "Not yet observed in a real production generation run").
- **Scheduler enablement remains blocked** until both India and US Release 12B validations pass — this note does not change that.
- **Validation must target the new universe, not the old one.** Sprint #014 replaced the `yf.screen()`-based universe with a `stock_fundamentals_cache`-sourced Large/Mid/Small stratified universe (`_get_universe_by_mcap` → `fundamentals_cache.get_ranked_universe()` → `_assign_cap_tiers()` → `_stratified_sample()`). Any validation still describing "Release 12B's universe behavior" in terms of the old Yahoo-screener logic is validating something that no longer exists. This was already flagged in `Current-Release-Status.md`'s Release 12B entry (2026-07-10 superseded-scope note) — repeated here only so this preflight doesn't get read in isolation from that constraint.

## 2. Hardening areas to inspect later (not inspected in depth yet — this is a checklist, not findings)

1. **Provider timeout handling** — what timeout (if any) wraps each `yfinance`/screener.in/Finnhub call inside Phase 0/Phase 1; whether a hang on one provider can stall the whole run.
2. **Retry/backoff behavior** — whether the now-current `stock_fundamentals_cache` read path has any retry logic at all (the deleted `_IN_SCREENER_*` retry/backoff constants were screener-specific and are gone; the cache read may have none, which is a different risk profile, not a leftover bug).
3. **Per-symbol failure isolation** — whether one symbol's scoring exception can abort the whole batch versus being caught and excluded.
4. **Generation runtime measurement** — Sprint #014 estimated ~60-90 minutes (up from ~10-20) for the new 400-candidate pool but this was never measured against a real run; need actual instrumentation/telemetry to confirm.
5. **Trigger observability** — `test_daily_picks_trigger_observability.py` already exists; check what it covers today versus what the hardened path would need.
6. **Payload freshness metadata** — whether the Daily Picks payload exposes a clear "generated_at"/staleness signal a consumer (frontend or otherwise) can trust.
7. **Cache integrity and stale-cache detection** — `stock_fundamentals_cache` is nightly-refreshed; what happens to a run if that nightly refresh job failed or is stale on a given day (silent degradation vs. visible failure).
8. **Market-specific run windows** — India and US have different natural run/generation windows (see `daily_picks_in.yml`, `daily_picks_us.yml`, `daily_picks_us_premarket.yml`); confirm hardening changes don't blur that separation.
9. **US/India separate validation evidence** — Release 12B's own gate already requires separate evidence per market; hardening work should preserve that separation, not produce one combined "it works" statement.
10. **Candidate universe health after filtering** — after the junk-floor filters (`_MIN_MCAP_CR`, `_MIN_MCAP_USD_M_FLOOR`) and tier assignment, confirm the resulting pool size/tier mix looks sane on a real cache snapshot, not just in unit tests with synthetic data.
11. **Tier diversity metadata** — `selection_meta["tier_map"]`/`tier_counts` were added in Sprint #014; confirm they're actually surfaced somewhere observable (logs, payload, telemetry) rather than computed and discarded.
12. **Malformed/null/NaN score fields** — what happens downstream if a `PredictionEngine.predict()` call returns a partial/NaN/null score for a candidate; whether that candidate silently corrupts a tier-quota or confidence-priority selection step.
13. **Confidence distribution sanity checks** — whether an unusually skewed confidence distribution on a given day (e.g. everything >80%, or nothing) is detectable/loggable rather than only visible after the fact in Phase 5's fill-down behavior.

## 3. Output of this preflight

### Proposed implementation sequence (for the future hardening task, not started here)
1. Instrument generation runtime end-to-end (Phase 0 → Phase 8 timestamps) without changing selection/scoring logic — pure observability first.
2. Add explicit timeout + per-symbol failure isolation around provider calls in Phase 0/Phase 1, with a symbol-level skip-and-log rather than a batch-level abort.
3. Add stale-cache detection (compare `stock_fundamentals_cache`'s last-refresh timestamp against a sane threshold) and surface it in the payload/logs rather than silently proceeding on stale data.
4. Add payload-level freshness/generation metadata if not already present in full.
5. Add defensive null/NaN guards around score fields feeding tier-quota and confidence-priority selection, with a test asserting a malformed candidate is excluded, not silently miscounted.
6. Only after the above: re-attempt the still-pending India/US Release 12B validation on a genuine natural run, evaluating the new universe explicitly (not the retired `yf.screen()` behavior).

### Files likely to inspect later
- `backend/services/daily_picks.py` (Phase 0/Phase 1 provider calls, `_get_universe_by_mcap`, `_assign_cap_tiers`, `_stratified_sample`, Phase 5 selection)
- `backend/services/fundamentals_cache.py` (`get_ranked_universe`, cache freshness/last-refresh fields)
- `backend/api/routers/picks.py` (payload shape, any freshness metadata already exposed)
- `backend/services/prediction_engine.py` (score/confidence field shape, what a partial/failed `predict()` call actually returns)
- `.github/workflows/daily_picks_in.yml`, `daily_picks_us.yml`, `daily_picks_us_premarket.yml` (existing run-window/trigger configuration — read-only reference, not to be changed by this preflight)
- `Documentation/Engineering-Handbook/Operations/Current-Release-Status.md` (Release 12B gate — update only once real validation evidence exists)

### Tests likely required later (naming only — none written yet)
- Existing, to review for adequacy: `backend/tests/regression/test_daily_picks_trigger_observability.py`, `test_daily_picks_pipeline_telemetry.py`, `test_daily_picks_job_state.py`, `test_daily_picks_output_integrity.py`, `test_in_universe_expansion.py`, `test_daily_picks_us_universe_guard.py`, `test_daily_picks_phase5_tier_and_confidence_selection.py`.
- Likely new: per-symbol failure isolation (one bad symbol doesn't abort a batch), stale-cache detection (mocked old last-refresh timestamp triggers a visible flag), malformed/NaN score field exclusion from tier-quota/confidence-priority selection, generation runtime telemetry presence.

### Risks/blockers
- No real production generation run has exercised the Sprint #014 universe change yet at all — hardening work risks being designed against an unvalidated assumption about real-world cache shape/tier mix. The sequence above deliberately front-loads pure observability (step 1-2) so the eventual real validation run also yields the runtime/failure data this hardening needs, rather than treating validation and hardening as fully separate efforts.
- Cache staleness detection thresholds are undefined — will need a judgment call (flagged explicitly when made, not silently hardcoded) similar to Sprint #014's `_MIN_MCAP_USD_M_FLOOR`.
- Any of this work must not be mistaken for satisfying the Release 12B gate — the gate requires a genuine fresh natural run on both markets, which remains outstanding regardless of how much hardening lands first.

### Exact next prompt for the future hardening implementation

> "Implement Daily Picks runtime/provider hardening per `Documentation/Engineering-Handbook/Releases/Preflight-Daily-Picks-Runtime-Provider-Hardening.md`, starting with step 1 (generation runtime instrumentation, Phase 0 → Phase 8 timestamps, no selection/scoring logic changes) and step 2 (per-symbol provider-call timeout + failure isolation, skip-and-log instead of batch abort). Do not enable the scheduler, do not call the production generation endpoint, do not change RCI/scoring/valuation/growth/financial engines, and do not claim Release 12B validation passed — that still requires a separate genuine fresh natural run on both India and US after this hardening lands. Add tests for per-symbol failure isolation and confirm the full backend suite stays green."

## 4. Explicitly out of scope for this document

- No code changed.
- No scheduler enabled.
- No production generation endpoint called.
- No claim that any validation passed.
