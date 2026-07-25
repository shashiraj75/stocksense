# Market Leadership and Trend Context Layer — Local Implementation and Release Evidence

**Status:** IMPLEMENTED LOCALLY, TESTED LOCALLY, SHADOW-VALIDATION READY. Not pushed, not merged,
not deployed. All five feature flags default OFF; scoring influence
(`MARKET_LEADERSHIP_SCORING_ENABLED`) is reserved and consumed by no production code path — ZERO
PRODUCTION SCORING INFLUENCE. NOT ENABLED in any environment. No production system, database, or
published signal was touched.

**PR split note:** this document was originally written against the full local development branch
(`feat/market-leadership-trend-context`, 9 commits, including a separate, file-disjoint validation
job-identity fix). A pre-publication audit confirmed the two changes share zero files and zero
runtime dependency (the market-leadership code only reads a pre-existing, unmodified function from
`validation_engine.py`), so they ship as two independent draft PRs:

- **This PR** (`feat/shadow-market-leadership-context`) carries only the Market Leadership and Trend
  Context Layer — 8 of the 9 original commits, cherry-picked verbatim, plus one additional commit
  (`fix(leadership): defensive-copy compute_stock_context cache reads/writes`) added during the
  pre-publication audit itself (§4.5 below).
- The validation job-identity fix ships separately as `fix/validation-job-universe-identity`.

Companion document: [Architecture](../Architecture/Market-Leadership-Trend-Context-Layer.md) (full
methodology, contracts, PIT controls, rollback plan).

---

## 1. Scope delivered this session

1. **Validation job-identity defect** (Section 3 of the governing brief) — root-caused and fixed.
   **Ships in the separate `fix/validation-job-universe-identity` PR, not this one.**
2. **Market Leadership and Trend Context Layer** (Sections 4–9) — new `backend/services/market_leadership/`
   package: Stock Relative Strength Rank, Sector/Industry Group Leadership, Trend Lifecycle,
   Market Breadth, "Why Now?" explanation contract.
3. **Point-in-time controls, feature flags, persistence** (Sections 10–12).
4. **API + frontend integration**, shadow-only (Sections 13–14).
5. **Shadow-experiment harness, mutation-testing sanity checks, performance regression guards**
   (Sections 15–17).
6. **Four-hat adversarial review** (Section 19) with fixes applied and re-tested (this document).
7. **Final independent pre-publication audit** — one additional defect found and fixed (§4.5).

## 2. Commits on this PR (chronological, cherry-picked from the original 9-commit branch plus one new commit; none pushed to `main`, only to this feature branch)

| Commit (this branch) | Origin | Summary |
|---|---|---|
| `0d84284` | cherry-picked from `c239a55` | docs: current-state investigation + architecture |
| `165521e` | cherry-picked from `c3fb9f8` | feat(leadership): RS, group leadership, trend lifecycle, breadth, explanation — pure core |
| `2532de2` | cherry-picked from `719510c` | feat(persistence): snapshot storage, telemetry, orchestration; PIT + flag regression tests |
| `f90f259` | cherry-picked from `78432a2` | feat(api): shadow context endpoint; feat(frontend): experimental UI |
| `a3c14c4` | cherry-picked from `cb718bf` | fix(leadership): incomplete last-bar handling + misleading explanation text (live-verified defects) |
| `9622f9a` | cherry-picked from `ff2b353` | test: shadow-experiment harness, mutation checks, performance guards |
| `f46a6de` | cherry-picked from `cb6c3f4` | fix(leadership): TTL cache for `compute_stock_context` (adversarial-review finding) |
| `cc311d7` | cherry-picked from `f4b7024` | docs: add release evidence and rollout gates |
| *(new)* | pre-publication audit | fix(leadership): defensive-copy cache reads/writes (§4.5) |

(The excluded 9th original commit, `8f0d8ed` fix(validation), ships in the separate PR.)

## 3. Baseline and final test evidence (this PR's isolated scope, verified independently)

| Suite | Baseline (`origin/main`) | This PR, isolated |
|---|---|---|
| Backend (`pytest`) | 3553 passed, 0 failed, 88s | **3727 passed, 0 failed** |
| Frontend typecheck (`tsc --noEmit`) | clean | clean |
| Frontend tests (`vitest`) | 410 passed | **423 passed** (410 baseline + 13 new: `marketLeadership.test.ts`, `MarketLeadershipContext.test.tsx`) |
| Frontend production build (`next build`) | clean, all routes | clean, all routes, `/api/leadership/context` in `openapi.json`, no existing route removed |

Verified by checking out this exact branch into its own worktree, installing dependencies fresh, and
running the full suite in isolation — not inferred from the combined development branch's numbers.

No pre-existing test was modified to make it pass; two pre-existing tests were extended (§4).

## 4. Defects found and fixed during this work (not hypothetical — evidence attached)

### 4.1 Validation job-identity defect (original brief, Section 3)
**Root cause:** `validation_engine._run_status` was a module-global dict with no market/universe/
horizon identity; `/api/validation/status` returned it verbatim; the frontend rendered whatever run
was globally active under whichever tab was selected. **Reproduced** with 12 failing regression
assertions pre-fix (`git stash`-equivalent verified by writing the test before the fix). **Fixed**
via an immutable `claim_validation_job()`-created job identity, never relabeled after creation; the
UI now shows an explicit cross-universe banner instead of relabeling a foreign run.

### 4.2 Provider-incomplete last bar → NaN → broken JSON response (found via live manual verification)
Ran the actual backend locally (`MARKET_LEADERSHIP_ENGINE_ENABLED=1 MARKET_LEADERSHIP_UI_ENABLED=1`,
real `uvicorn` process) and called `GET /api/leadership/context?symbol=AAPL&market=US` against real
yfinance data. Confirmed live: yfinance's technically most-recent row in a `period="2y"` fetch
carried a NaN Close (2026-07-24 session) even though that session had genuinely closed. This NaN
propagated into `trend_lifecycle`'s drawdown/price-vs-MA fields and crashed the endpoint outright
(`ValueError: Out of range float values are not JSON compliant: nan`) — a full request failure, not
a degraded response. **Fixed**: `sessions.drop_incomplete_bars()` strips any NaN-Close row right
after as-of slicing (applied in both `relative_strength.py` and `trend_lifecycle.py`); a `_safe_json`
boundary guard added at the API layer as defense in depth. Re-verified live post-fix: the endpoint
now returns a complete, correct payload for AAPL.

### 4.3 Misleading "insufficient price history" explanation (found via the same live check)
Once 4.2 was fixed, the live response surfaced a second, real correctness bug: the single-symbol
on-demand endpoint has no cross-sectional universe rank by design, so `stock_rs_percentile` is
honestly `None` — but `explanation.py`'s Why Now text unconditionally said *"Relative Strength could
not be computed (insufficient price history)"* whenever percentile was `None`, which was **false**
in this case (`rs_1m`/`rs_3m`/`rs_6m`/`rs_12m` and `benchmark_relative_return` were all present,
`data_quality_status` was `OK`). **Fixed**: the explanation now distinguishes "percentile
unavailable but data valid" from genuine insufficient data, and states the raw benchmark-relative
return honestly instead of a false limitation claim.

### 4.4 Missing cache → unbounded per-request cost (four-hat adversarial review, Principal Architect hat)
`compute_stock_context` recomputed from scratch (fresh yfinance fetch + full calculation) on every
API call — violating the brief's own Section 17 #10 ("prevent expensive recalculation on every page
request") and, unbounded, echoing the exact "unbounded cache until OOM" defect class already found
twice elsewhere in this codebase (`market_data.py`, `sec_edgar_adapter.py` per their own code
comments). **Fixed**: a bounded (300-entry), TTL-cached (4h) in-process cache mirroring the
established `_cache_set` eviction pattern, keyed by `(symbol, market, as_of, methodology_versions)`
so a version bump auto-invalidates; errors get a short 2-minute TTL (mirrors `prediction_engine.py`'s
own convention) so a failing provider isn't hammered but recovers quickly. Two unrelated pieces of
dead weight (an unused local, an unconditional-but-unused DB read) removed in the same pass.

All four fixes are covered by dedicated regression/integration tests (34 new tests across 4.2–4.4)
and the full backend suite was re-run green after each.

### 4.5 Cache-mutation hazard (found via final independent pre-publication audit)

The §4.4 TTL cache fix returned the exact dict object stored in `_STOCK_CONTEXT_CACHE` — on both the
cache-hit path and the fresh-computation path. **Reproduced live**: a caller mutating its own
response (`result["rs"]["stock_rs_score"] = 999999`) silently corrupted every subsequent cache hit
for that `(symbol, market, as_of)` key. This is the exact cache-mutation hazard class this
codebase's own Recommendation Consolidation Sprint #007 previously found and explicitly designed
around for `prediction_engine.py`'s `_pred_cache` (documented in that sprint's own release notes as
the reason a dedicated, always-freshly-assembled response composer was required instead of building
directly inside the cached `predict()` path).

**Fixed**: every return path in `compute_stock_context` now hands the caller `copy.deepcopy(...)` of
the cached/fresh value — never the object stored in the cache itself. Verified live, before and
after: before the fix, a mutated field leaked into the next call's result; after the fix, it did
not. 3 new regression tests lock this in (`test_caller_mutating_a_cache_hit_response_does_not_corrupt_the_cache`,
`test_caller_mutating_the_first_fresh_response_does_not_corrupt_the_cache`,
`test_repeated_calls_return_equal_but_independent_objects`). Full backend suite re-run green after
the fix: 3727/3727.

## 5. Mutation-testing sanity checks (Section 16.C)

No `mutmut`/`cosmic-ray` dependency exists in this repository. Followed the established house
convention (SES-003 §4, used throughout the sprint history — e.g. Sprint #013's own "deliberately
removed a check, confirmed the test failed, restored byte-identical" discipline) instead of adding a
new dev dependency for a single session:

| Function | Mutation | Test result before fix | Restored |
|---|---|---|---|
| `trend_lifecycle.classify_trend_lifecycle` | Disabled the EXTENDED_ADVANCE-before-CONFIRMED_ADVANCE precedence check | RED — `test_extended_advance_flags_high_extension_risk_even_with_bullish_structure` failed (state fell through to CONFIRMED_ADVANCE) | byte-identical, confirmed via `diff` |
| `breadth.classify_breadth_state` | Disabled the WASHED_OUT precedence check | RED — `test_washed_out_state` failed (fell through to NARROWING) | byte-identical, confirmed via `diff` |
| `group_leadership._capped_weights` | Raised the effective cap from 20% to 100% (cap disabled) | RED — `test_no_member_exceeds_the_cap` and `test_one_mega_cap_cannot_dominate_the_weighted_score` both failed (mega-cap dominated at 20.06 instead of >60) | byte-identical, confirmed via `diff` |

All three mutations were caught by an existing test. No test needed strengthening as a result.

## 6. Performance and memory evidence (Section 17)

Real measured numbers (this repository's dev machine, mocked acquisition isolating pure-calculation
cost from network I/O — network cost is separately amortized/cached per §4.4 above and the existing
codebase-wide precedent of not re-measuring provider latency per feature):

- 300-symbol synthetic universe, full `compute_market_context` (RS + group + breadth): **0.586s
  total (~1.95ms/symbol), 2.64MB peak traced memory, zero errors**.
- Structural guarantees locked in by regression tests (`test_performance_bounds.py`): exactly one
  `fetch_price_history` call per symbol (no N×M), benchmark fetched once per market context call
  (not once per symbol), one symbol's failure never blocks the other 59 in a 60-symbol universe.
- `compute_stock_context` (the API-facing single-symbol path): TTL-cached per §4.4 — a repeat page
  view within 4 hours costs zero provider calls.

## 7. Quantitative shadow validation (Section 15) — status: **VALIDATION PENDING**

`scripts/leadership_shadow_experiment.py` implements the walk-forward CONTROL-vs-RS-alone study —
the necessary first rung of Section 15's experiment ladder (group leadership / trend lifecycle /
breadth layering is a future harness extension). It uses only the module's own production functions
against real historical price data, is fully reproducible offline after the one-time fetch, and
mechanically refuses to present a correlation-based conclusion below a 300-observation floor
(`headline_suppressed`).

**Smoke-tested against real live data** (15 US large-cap symbols, 21-day horizon, 300-day lookback):
225 observations — correctly suppressed as sub-floor. This proves the harness is **functional and
reproducible**; it is explicitly **not** a validation result, and no scoring-influence recommendation
follows from it. A genuine Section 15 evidence base (India + US separately, 3 horizons, multiple
market regimes, walk-forward train/validation/test splits, bootstrap confidence intervals, adequate
sample size) requires a multi-week/-month data-collection and analysis effort outside a single
session's scope — consistent with the brief's own Gate structure, which requires only "reproducible
offline replay, experiment contracts, telemetry, no scoring influence" at Gate 4, not completed
statistical validation.

## 8. Point-in-time and survivorship controls (Sections 10, verified)

- `sessions.slice_as_of` + `drop_incomplete_bars` are the sole leakage-prevention mechanism; both
  are property-tested (`test_point_in_time_leakage.py`) with synthetic fixtures where a future 200%
  spike / 80% crash / high-volume crash would visibly change the answer if leaked — proven not to.
- Universe/classification are explicitly disclosed as **not** point-in-time safe
  (`universe_pit`/`classification_pit_safe: false` throughout) — v1 uses the existing
  `heatmap_service` sector curation and static universe lists, with no historical-membership source
  in this codebase. This is a named, disclosed limitation, not a defect.
- India fundamentals remain excluded entirely (DP-026's own standing finding) — this layer is
  price/volume only in v1.

## 9. Feature-flag and backward-compatibility evidence (Section 11)

- All five flags (`MARKET_LEADERSHIP_ENGINE_ENABLED`, `_SHADOW_ENABLED`, `_UI_ENABLED`,
  `_VALIDATION_ENABLED`, `_SCORING_ENABLED`) default OFF — direct unit tests, including a
  malformed-value-fails-closed case and a dependent-flag-without-master-gate case.
- `MARKET_LEADERSHIP_SCORING_ENABLED` is statically proven (via a `grep`-based regression test) to
  be referenced nowhere outside `configuration.py` — reserved, unconsumed, per Section 11's
  non-negotiable rule.
- With flags off: `orchestration.compute_market_context`/`compute_stock_context` return
  `{"status": "disabled"}` with zero acquisition/calculation/persistence (asserted via an
  `AssertionError`-raising monkeypatch on the acquisition adapter); the API answers the same fixed
  shape; the frontend component renders nothing.
- Daily Picks, Multibagger, Portfolio, Paper Trading, Alerts, Validation, Heatmap, and Screener were
  not modified by this work (confirmed by `git diff` scope — the only pre-existing files touched are
  `validation_engine.py`/`api/routers/validation.py`/`api/main.py`, all part of the explicitly
  in-scope validation-job-identity fix) and the full pre-existing test suite (3553 tests) still
  passes unchanged.

## 10. Security and compliance review (Section 18)

- No new secret handling, no new auth surface — the leadership endpoint is unauthenticated read-only
  research context, matching every other public read endpoint in this API (`/api/stocks`,
  `/api/screener`, etc.); no private user data (holdings, watchlists) is touched or exposed.
- Input validation: `symbol` is length-bounded (1–32 chars); `market` is a `Literal["IN","US"]` —
  an invalid value is a 422, never silently defaulted or inferred.
- No internal stack traces, environment variables, or DB connection details are ever returned —
  `safe_error_message` (existing codebase utility) is reused for the error path.
- No HTML/script injection surface: all Why Now text is template-rendered from enum/numeric fields,
  never includes raw user input or provider free text.
- No LLM is used anywhere in this layer (Section 9's requirement) — the explanation is a fixed
  Python template registry.
- Rate-limit/DoS: the new endpoint reads from the same bounded TTL cache as every other read path;
  it does not create a new unbounded-cost surface (see §4.4/§6).
- Compliance positioning: every output is disclosed as informational/experimental, explicitly
  separated from BUY/HOLD/SELL, never implies guaranteed returns (frontend copy verified by a
  dedicated test asserting no bare "BUY"/"SELL" badge and an explicit
  "separate from the signal above" disclosure). This layer introduces no new advice claim and does
  not weaken any existing disclaimer. Specialist securities-law review remains a standing,
  pre-existing open item for the platform's BUY/HOLD/SELL surface generally (unchanged by this work,
  not newly created by it).

## 11. Known limitations (disclosed, not hidden)

1. v1 universe/classification is `heatmap_service`'s existing curated sector map, not the full
   NSE/NASDAQ universe and not point-in-time safe historically.
2. Sector and industry are the same single classification level in v1 (`sector_name == industry_name`)
   — a finer industry taxonomy is future work.
3. Group cap-weighting uses `stock_fundamentals_cache` market caps where available; falls back to
   equal weight otherwise (disclosed via `data_coverage`/reason codes, never silently substituted).
4. `compute_stock_context`'s single-symbol endpoint has no cross-sectional percentile — only
   `compute_market_context` (full-universe batch) produces a ranked percentile.
5. Section 15 quantitative validation is a functional harness only — no statistically adequate
   evidence base exists yet (§7).
6. US breadth's completed-session rule is a conservative, un-calendar-aware approximation (no US
   market-holiday table) — a holiday yields a one-session-stale expectation, surfaced as staleness
   rather than a wrong "complete" claim (safe direction, disclosed in `sessions.py`).

## 12. Rollback plan

All changes are additive and flag-gated. Revert the eight commits above (or `git reset` the feature
branch) to fully undo; no production data migration exists to unwind (new tables are created only
lazily, only if a flag is ever turned on in an environment with `DATABASE_URL`/`USE_POSTGRES=1` set —
never attempted in this session). The validation-job-identity fix is independent and revertible
separately; its only persisted-schema change is additive JSON-summary metadata.

## 13. Recommendation

**Gate 4 (Shadow-Validation Readiness) criteria met**: point-in-time tests pass, offline replay is
reproducible, experiment contracts/telemetry exist, no scoring influence anywhere in the code path.
A final independent pre-publication audit found and fixed one additional defect (§4.5) and confirmed
this PR's scope is file-disjoint from the separately-shipped validation fix, with all five feature
flags verified default OFF both statically and behaviorally.

This branch is published as a **draft pull request only** — draft status and a branch push are not
approval for anything beyond code review. **Not** proceeding past Gate 4 without separate, explicit
user approval for: merge, production deployment, production shadow enablement, user-visible UI
enablement, or any recommendation-scoring influence (Gates 5–7, tracked as explicit unchecked
approval-gate checkboxes in the PR description itself).
