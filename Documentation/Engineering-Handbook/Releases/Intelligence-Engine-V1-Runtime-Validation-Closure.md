# Intelligence Engine V1 — Runtime Validation Closure

**Date:** 2026-07-08
**Type:** Runtime verification record — read-only production checks, no code changed.
**Scope:** Closes the pending runtime-validation item recorded in `Operations/Current-Release-Status.md` for Intelligence Engine V1 (Universe Builder shadow slice, Phases 3A/3B).

## 1. What was pending

`Operations/Current-Release-Status.md` (as of 2026-07-07) required, before Intelligence Engine V1 could be described as runtime-validated:

1. IN **and** US shadow telemetry (`GET /api/picks/intelligence-shadow?market=IN|US`) both show `source_commit = bb5d3cf8247ce829ca9d0e06d4048fc7aa7b740e` **or a later commit**.
2. Both show `tradability.available = true`, `liquidity.available = true`, and `data_confidence.available = true` — the three Phase 3B sub-objects specifically, not just the top-level `available`.

At the time that register entry was written, no fresh India generation had been observed with the Phase 3B-B wiring, and no US shadow run had been observed with it at all.

## 2. Evidence — direct, live, read-only production API calls (2026-07-08)

Both criteria are now satisfied. Raw responses captured 2026-07-08 from
`https://stocksense-production-7e0d.up.railway.app/api/picks/intelligence-shadow`.

### India (`?market=IN`)

- `run_at`: `2026-07-07T22:25:52.510700+00:00`
- `source_commit`: `d81363ec084560a4ee364809d951c9ff1140e134` — **verified a descendant of the required `bb5d3cf`** via `git merge-base --is-ancestor bb5d3cf8 d81363ec` against the local repository (commit `d81363e fix(paper-trading): prevent duplicate lazy history rows`), not assumed from recency.
- `tradability`: `{"available": true, "passed": 12, "failed": 0}`
- `liquidity`: `{"available": true, "passed": 12, "failed": 0, "sample_rejections": []}`
- `data_confidence`: `{"available": true, "average": 86.5}`
- Phase 3A slice consistent with the 2026-07-06 first capture: `raw_count: 2384`, `passed_count: 2382`, `excluded_count: 2` (`GOLDBEES`, `SILVERBEES`, both `etf`).

### US (`?market=US`)

- `run_at`: `2026-07-07T06:04:50.751141+00:00`
- `source_commit`: `bb5d3cf8247ce829ca9d0e06d4048fc7aa7b740e` — the exact required commit.
- `tradability`: `{"available": true, "passed": 11, "failed": 0}`
- `liquidity`: `{"available": true, "passed": 11, "failed": 0, "sample_rejections": []}`
- `data_confidence`: `{"available": true, "average": 88.2}`
- Phase 3A slice: `raw_count: 12121`, `passed_count: 6310`, `excluded_count: 5811` across 8 exclusion reasons (`etf: 5009`, `preferred_share: 368`, `spac: 266`, `index_fund: 52`, `etn: 36`, `closed_end_fund: 33`, `unit_trust: 26`, `leveraged_inverse: 21`) — the first observed US shadow run with the Phase 3B-B wiring.

### Sanity notes on the evidence

- The `passed`/`failed` counts of 12 (IN) and 11 (US) are consistent with the disclosed Phase 3B-B limitation: gate coverage is limited to the symbols selected into `payload["picks"]` (~18 per run at most, fewer after per-horizon overlap), **not** the full candidate pool. This is the documented, expected coverage — not a discrepancy.
- Zero tradability/liquidity failures among selected picks is the expected result, since selected picks have already passed Daily Picks' own upstream screening; the gates observing real data and reporting `available = true` is what this validation was gating on, not a non-zero failure count.
- Both runs remain shadow-only: `INTELLIGENCE_ENGINE_SHADOW_ENABLED` telemetry with zero effect on published Daily Picks, Heatmap, or Portfolio, exactly as deployed.

## 3. Files Changed

- `Documentation/Engineering-Handbook/Releases/Intelligence-Engine-V1-Runtime-Validation-Closure.md` — this record (new).
- `Documentation/Engineering-Handbook/Operations/Current-Release-Status.md` — Intelligence Engine V1 entry updated from "runtime validation pending" to runtime-validated, citing this record.
- `Documentation/Engineering-Handbook/INDEX.md` — entry added for this record.

No production code, configuration, flag, schedule, or Railway state was touched. No non-read-only API call was made.

## 4. Architecture Changes

None.

## 5. Risks

- **Coverage remains picks-only.** Runtime validation confirms the Phase 3B gates work end-to-end on real production data, but only for the ~11–12 selected picks per run. Extending gate evaluation to the full Phase-0/Phase-1 candidate pool remains future, unvalidated work — closing this register item must not be read as validating pool-wide gating.
- **Single-run evidence per market.** Each market's evidence is one completed run. The gates' behavior across degraded runs (partial data, provider outages) is unit-tested but not yet production-observed.
- **Zero-failure gates are weak positive evidence.** Because selected picks are pre-screened upstream, this validation never observed the gates rejecting anything at the tradability/liquidity layer in production. A gate that silently passed everything would look identical in this data; confidence that the gates reject correctly rests on their unit tests and the Phase 3A exclusions (which did fire, on both markets).

## 6. Migration Notes

The Epic-number collision noted in the register stands: this work is *not* Epic 007 (allocated to Portfolio and Watchlist Intelligence in `MASTER-ROADMAP.md` §11). Whoever formally assigns an Epic number must reconcile the two.

## 7. Testing Status

No code changed; no tests added or run. Evidence is live production telemetry plus a local `git merge-base --is-ancestor` ancestry check, both reproducible from this record.

## 8. Recommendations for the next step

1. **Formal closure decision** — the register's two named criteria are met; whether Intelligence Engine V1 is formally *closed* (vs. runtime-validated) is an explicit decision for whoever owns the register, per its own approval rules.
2. If the engine is to progress beyond shadow, the next evidence-bearing step is a **candidate-pool-coverage design study** (extending gate evaluation beyond selected picks), not further shadow observation — the current wiring's coverage ceiling is structural, so more shadow runs add no new information.
3. Release 12B controlled validations (IN and US) remain the register's other open item and are unaffected by this record.
