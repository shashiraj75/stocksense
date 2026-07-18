# StockSense360 Daily Picks Implementation Register

> Canonical source of truth for Daily Picks strategy defects, product decisions, implementation status, deployment evidence, production verification, and outcome validation.
>
> **Last reconciled:** 2026-07-18  
> **Repository state reviewed:** `40b0a42d53de81f8cfbad1b926996e23f4e5030c` and current Daily Picks implementation files  
> **Production learning:** Contained/disabled unless a later entry explicitly proves otherwise.  
> **Register baseline:** Claude's five-agent read-only audit at HEAD `40b0a42` was reconciled into the permanent `DP-###` inventory.

## Governance rule

No Daily Picks item may be called complete from a chat summary, test result, commit, merge, or deployment alone. The terminal implementation state is **PRODUCTION VERIFIED**. Statistical or effectiveness work may remain **MONITORING** after production verification until sufficient real outcomes accumulate.

Every future Daily Picks implementation must:

1. cite one or more permanent `DP-###` IDs;
2. update this register in the same implementation commit where practical;
3. record exact files, tests, commit SHA, deployment evidence, production evidence, and remaining work;
4. preserve a truthful distinction between production-active, shadow-only, validation-only, and documentation-only work;
5. never enable `LEARNING_ALPHA_PRODUCTION_ENABLED` as an incidental part of another item.

## Controlled status values

| Status | Meaning |
|---|---|
| IDENTIFIED | Reported but not independently verified against current code. |
| VERIFIED | Confirmed against current code. |
| APPROVED | Remediation or product direction approved by the owner. |
| IN IMPLEMENTATION | Code changes are actively being prepared. |
| IMPLEMENTED — LOCAL | Code changed locally but not merged. |
| MERGED | Present on the default branch. |
| DEPLOYED | Deployed, but live behaviour not yet proved. |
| SHADOW ONLY | Runs diagnostically and does not influence published picks. |
| VALIDATION PENDING | Implemented/deployed, but required production or outcome validation is incomplete. |
| PRODUCTION VERIFIED | Live production behaviour and evidence confirmed. |
| MONITORING | Production-verified, but effectiveness needs continuing outcome evidence. |
| DEFERRED | Intentionally postponed. |
| REJECTED | Explicit decision not to implement. |
| SUPERSEDED | Replaced by another controlled item. |

## Current dashboard

| Category | Count |
|---|---:|
| Total controlled findings | 30 |
| VERIFIED — remediation pending | 26 |
| DEPLOYED — VALIDATION PENDING | 3 |
| MONITORING / existing work acknowledged | 1 |
| DECISION REQUIRED before implementation | 6 |
| Documentation-only baseline created | 1 |
| Production learning active | 0 |

**Immediate recommended implementation order:** ~~`DP-009` + `DP-010`~~ (deployed, validation pending — see evidence below) → `DP-020`/`DP-021`/`DP-022`/`DP-024` (blocked on owner decision `DPD-005` — Portfolio cash and maximum-position contract, currently DECISION PENDING; do not implement until decided) → horizon decision `DPD-001` covering `DP-001`–`DP-004`/`DP-016` → `DP-005` (needs a new DPD; none currently exists) → ~~`DP-017`~~ (deployed, validation pending — see evidence below) → validation harness `DP-025`/`DP-026`. `DP-018`/`DP-019` remain open and are not implied by DP-017's completion.

## Register correction — DPD cross-reference reconciliation (2026-07-18)

The `Decision required` column had drifted out of sync with `DAILY-PICKS-DECISION-LOG.md`'s actual `Related findings` lists (likely from an earlier decision-log renumbering that wasn't propagated back into this register). This was a **documentation-only correction** — no code, no decision content or status, and no DP-### finding text changed. Corrections made, verified against the decision log's authoritative `Related findings` fields:

- `DP-011`, `DP-013`: were citing `DPD-004` → corrected to `DPD-006` (Liquidity and event-risk policy, which explicitly lists `DP-011`/`DP-013`/`DP-029`).
- `DP-014`: was citing `DPD-005` → corrected to `DPD-004` (Top-6 ranking versus cap-tier diversification, which explicitly lists `DP-014` only).
- `DP-016`: was citing `DPD-005` → corrected to `DPD-001` (Horizon architecture and naming, which explicitly lists `DP-016` alongside `DP-001`–`DP-004`).
- `DP-020`, `DP-021`, `DP-022`, `DP-024`: were citing `DPD-006` → corrected to `DPD-005` (Portfolio cash and maximum-position contract, which explicitly lists these four).
- `DP-005`, `DP-012`, `DP-015`, `DP-023`: were citing DPD numbers (`DPD-003`, `DPD-004`, `DPD-004`, `DPD-006` respectively) that do **not** list them in the decision log at all. No DPD entry currently covers these four findings — corrected to state that plainly (decision-log gap) rather than point at an unrelated decision. Each still requires a new DPD before implementation per Decision rule 4 (none is an unambiguous, trade-off-free correctness defect on the level of `DP-009`/`DP-010`).
- `DP-009`, `DP-010`, `DP-017`, `DP-018`, `DP-019`, `DP-025`–`DP-030` were already correct and were not changed.

## Evidence and status rules

- A finding remains `VERIFIED` until an implementation commit is inspected.
- A commit alone changes status only to `MERGED`.
- Railway/Vercel success changes status only to `DEPLOYED`.
- Live API/database/UI evidence is required for `PRODUCTION VERIFIED`.
- Backtest/statistical claims require methodology evidence and may remain `MONITORING`.
- Product choices marked **Decision required** must reference `DAILY-PICKS-DECISION-LOG.md` before code changes.

## Controlled findings

| ID | Priority | Horizon | Finding | Status | Decision required | Current evidence / reconciliation | Next safe action |
|---|---|---|---|---|---|---|---|
| DP-001 | P0 | Medium | The 2–4 week target blends 70% of a materially longer-horizon analyst consensus target. | VERIFIED | Yes — DPD-001 | Confirmed in `_estimate_target()` medium branch. | Decide horizon architecture, then design horizon-matched target methodology. |
| DP-002 | P0 | Medium | Medium stop/trailing-stop assumptions are described as approximately three-month swings despite a 2–4 week label. | VERIFIED | Yes — DPD-001 | Confirmed in `_trade_levels()` comments and parameters. | Resolve with DP-001 under one horizon contract. |
| DP-003 | P0 | Positional/Long | Current 3–6 month target extrapolates analyst targets or compounds fundamentals over roughly multi-year periods. | VERIFIED | Yes — DPD-001 | Confirmed in `_estimate_target()` long branch. | Decide whether to reformulate 3–6 month targets or introduce a genuine 1–3 year horizon. |
| DP-004 | P0 | All | Outcome-resolution windows do not match the economic horizon implied by medium/long target formulas. | VERIFIED | Yes — DPD-001 | Resolver uses approximately 3/30/90-day windows. | Align target, label, stop, trailing stop and outcome contract as one design. |
| DP-005 | P0 | All | Target and trade levels are computed from initial confidence before later confidence adjustments alter the displayed value. | VERIFIED | Yes — no DPD entry currently exists (decision-log gap; `DPD-003` covers only DP-007/DP-008, not DP-005) | Confirmed ordering in `PredictionEngine.predict()`. | Author a new DPD for the freeze-first vs. recompute-after-adjustments contract before implementing; then implement with behavioural tests. |
| DP-006 | P0 | All | User-facing confidence is largely a linear score rescale, not a calibrated success probability; SELL/HOLD lack a useful observed gradient. | VERIFIED | Yes — DPD-002 | Recent commits added historical-track-record visibility but did not calibrate or rename the active confidence field. | Decide naming/calibration contract; retain track-record disclosure. |
| DP-007 | P0 | All | 1.5:1 R:R is attempted but not guaranteed because volatility floors can prevent it. | VERIFIED | Yes — DPD-003 | Confirmed in `_trade_levels()` and comments. | Decide enforce-vs-disclose policy. |
| DP-008 | P1 | All | Daily Picks only rejects explicit R:R red flags below 1.0; candidates from 1.0 to 1.49 remain publishable. | VERIFIED | Yes — DPD-003 | Confirmed in `_apply_risk_reward_adjustment()` and final quality gate. | Align actual gate and UI wording with approved policy. |
| DP-009 | P0 | All | `qf.get("score") or 50` turns a genuine quality score of zero into neutral 50 for ranking. | VALIDATION PENDING | No | Fixed: `daily_picks.py:872` now reads `qf.get("score") if qf.get("score") is not None else 50`. Commit `2d721bb38bd52ffef183fc6fb66626c30ab36b5f` is on `origin/main`; Railway and Vercel checks both succeeded. See implementation evidence below. | Await naturally scheduled India or US Daily Picks generation and inspect live output, score snapshots, alpha observations or logs for a genuine zero factor score surviving without conversion to 50. |
| DP-010 | P0 | All | `_zscore_and_rank()` uses `raw_val or 50`, converting genuine zero technical/fundamental values to 50 after the `None` check. | VALIDATION PENDING | No | Fixed: `daily_picks.py:1013` now reads `raw = float(raw_val)` (the preceding `is None` branch already handles missing evidence). Commit `2d721bb38bd52ffef183fc6fb66626c30ab36b5f` is on `origin/main`; Railway and Vercel checks both succeeded. See implementation evidence below. | Await naturally scheduled India or US Daily Picks generation and inspect live output, score snapshots, alpha observations or logs for a genuine zero factor score surviving without conversion to 50. |
| DP-011 | P1 | Short/Medium | No hard traded-value, volume, spread, circuit, price or market-impact gate exists before publication. | VERIFIED | Yes — DPD-006 | Existing liquidity-distress gate concerns financial health, not market tradability. | Define market-specific tradability thresholds and data-source availability. |
| DP-012 | P1 | US | US eligibility is heuristic name filtering rather than a verified common-equity master; some non-equities can pass. | VERIFIED | Yes — no DPD entry currently exists (decision-log gap: instrument/security-master provider selection) | Code documents QQQ/GLD-style residual pass-through risk. | Author a new DPD for common-equity master/provider selection before implementing. |
| DP-013 | P1 | Short/Medium | No earnings-date, board-meeting, results or material-event proximity gate exists. | VERIFIED | Yes — DPD-006 | Corporate-actions scoring is not an event-timing exclusion. | Define exclude, warn or event-driven policy and provider. |
| DP-014 | P1 | Medium/Positional | Mandatory 2/2/2 cap-tier selection can replace a stronger global-alpha candidate with a weaker tier candidate. | VERIFIED | Yes — DPD-004 | Current behaviour is intentional design, not an implementation defect. | Decide pure-alpha, soft-diversification or mandatory-quota policy. |
| DP-015 | P1 | US | Issuer deduplication covers only a small static mapping and lacks general dual-class/ADR/underlying resolution. | VERIFIED | Yes — no DPD entry currently exists (decision-log gap: issuer-identity/security-master provider selection) | Current mapping handles only four issuer groups. | Author a new DPD for issuer-identity/security-master selection before implementing. |
| DP-016 | P1 | All | No sector, industry, theme or factor-concentration control exists in final selection/portfolio construction. | VERIFIED | Yes — DPD-001 | Position cap alone does not manage common-factor concentration. | Define selection and portfolio exposure caps. |
| DP-017 | P0 | All | Retrained KMeans cluster IDs can permute while semantic regime labels remain pinned to fixed numeric IDs. | VALIDATION PENDING | No — governed by `DPD-007` (DECIDED — SAFETY CONSTRAINT) | Fixed: `regime_cluster.py` now deterministically anchors every fitted/loaded KMeans model's cluster IDs to canonical semantic anchor centroids before use. Commit `e40dcdc84a87421330bd3d7243d6337dba28f9c3` is on `origin/main`; Railway and Vercel checks both succeeded. See implementation evidence below. | Await a naturally scheduled retrain or model-load cycle and inspect logs/`alpha_observations`/regime snapshots for evidence that anchoring ran and produced semantically consistent IDs. |
| DP-018 | P2 | All | Four of five regime features are not clipped/winsorised, allowing out-of-distribution extreme values. | VERIFIED | No | VIX is capped; market changes, DXY, yields and Nifty feature are not. | Add bounded transformation after DP-017 correctness safeguard. |
| DP-019 | P1 | All | Regime factor multipliers are hand-set and lack direct production-pipeline validation against a no-multiplier baseline. | VERIFIED | No | Existing backtests do not validate this exact ranking path. | Include multiplier A/B in pipeline replay harness. |
| DP-020 | P0 | Portfolio | A two-pick portfolio cannot satisfy full-investment plus 40% caps; normalisation can output 50/50. | VERIFIED | Yes — DPD-005 | Confirmed in optimiser constraints/fallback. | Approve explicit cash allocation and test N=1..6 feasibility. |
| DP-021 | P2 | Portfolio | Optimiser's one-pick branch returns 100%, although current Daily Picks call site overrides singles to 50%. | VERIFIED | Yes — DPD-005 | Latent defect if the call path changes. | Resolve together with cash-aware optimiser contract. |
| DP-022 | P1 | Portfolio | Dimensionless ranking alpha and covariance risk are in incompatible/inconsistently scaled units. | VERIFIED | Yes — DPD-005 | Real covariance and fallback covariance differ materially in scale. | Define expected-return units or calibrate objective scaling. |
| DP-023 | P1 | Portfolio | Covariance lookback is fixed at six months for every horizon; the function's `days` argument is unused. | VERIFIED | Yes — no DPD entry currently exists (decision-log gap: horizon-matched covariance lookback design; `DPD-005` covers DP-020/021/022/024 only) | Newly confirmed by Claude audit. | Author a new DPD for horizon-specific covariance estimation windows before implementing. |
| DP-024 | P1 | Portfolio | Optimiser has no explicit cash, sector, liquidity-capacity, volatility-target or tail-risk control. | VERIFIED | Yes — DPD-005 | Equality constraint forces full investment when N>1. | Design minimum viable cash-aware portfolio contract first. |
| DP-025 | P0 | Validation | Current validation scripts do not replay the complete live composite→confidence→ranking→selection→optimisation pipeline. | VERIFIED | No | Historical-track-record UI commit correctly discloses simplified walk-forward evidence but does not close this gap. | Build additive production-pipeline replay harness before using results for model graduation. |
| DP-026 | P0 | Validation | Historical fundamentals in existing backtests are not point-in-time and can introduce look-ahead bias. | VERIFIED | No | Current-day fundamentals are reused across historical windows. | Source/version point-in-time fundamentals or prominently quarantine affected metrics. |
| DP-027 | P1 | Validation | Static current universes produce survivorship bias and omit delisted/failed constituents. | VERIFIED | No | Confirmed in fixed Nifty/mid-cap/US basket constants. | Add point-in-time membership datasets. |
| DP-028 | P1 | Validation | Overlapping medium/long outcome windows reduce effective independent sample size. | VERIFIED | No | Existing step/window combinations overlap substantially. | Use non-overlapping samples or dependence-aware inference. |
| DP-029 | P2 | Validation | Existing backtests omit transaction costs, spread and slippage. | VERIFIED | No | No cost model found in the validation paths. | Add conservative market-specific cost assumptions after tradability gates. |
| DP-030 | P2 | Learning | Graduation observability exists, but `alpha_observations` is not yet a sufficient outcome-linked production-learning readiness gate. | MONITORING | No | Commit `203250c` is correctly read-only; production learning remains contained. | Continue outcome/provenance work; never infer graduation from coverage metrics alone. |

### DP-009 / DP-010 implementation evidence

- Approved decision: Not required (Decision rule 4 — unambiguous correctness defect, no product trade-off: preserving a genuine numeric zero).
- Status: **VALIDATION PENDING** (implemented, merged to `origin/main`, and deployed — Railway and Vercel checks both succeeded on the implementation commit — but not yet confirmed against live production output).
- Files changed:
  - `backend/services/daily_picks.py` — `_predict_stock()` line ~872 (DP-009: `qf.get("score") or 50` → `qf.get("score") if qf.get("score") is not None else 50`) and its surrounding comment; `_zscore_and_rank()` line ~1013 (DP-010: `float(raw_val or 50)` → `float(raw_val)`, since the preceding `raw_val is None` branch at line ~1003 already handles missing evidence); one comment-only touch-up near line ~121 (`_build_alpha_observation_row`) to keep an adjacent code comment accurate after the DP-009 fix. No other lines changed. Confidence, target-price, stop-loss, horizon, universe, tier-quota, sector, liquidity/event gates, IC weights, regime multipliers/KMeans, optimizer, backtesting, outcome-resolution windows, frontend, schemas, API contracts, env vars, schedules, and production-learning configuration were not touched.
  - `backend/tests/regression/test_alpha_observations.py` — updated two pre-existing assertions (`TestPredictStockQualityRawScoreSource::test_genuine_zero_quality_score` and `::test_quality_raw_score_and_availability_never_in_published_payload`) that had locked in the pre-fix behaviour (asserting `quality_score == 50` for a genuine 0 input); both now assert `quality_score == 0`. No other assertion in this file required a change — the `None`/missing-value assertions were already correct and remain unchanged.
- Tests and exact result:
  - New file `backend/tests/regression/test_daily_picks_zero_score_preservation.py` — 15 tests, all passing. Covers: `_predict_stock()` preserving int `0` and float `0.0` quality scores, missing quality score still using neutral 50, `_zscore_and_rank()` preserving genuine `0` for tech/fund/quality (with sign/magnitude assertions, not just "not 50"), missing sentiment still receiving `z = 0.0`, cross-sectional mean/std correctly computed from the true zero, production-learning containment unaffected (meta-model shadow-computed, `ranking_alpha` stays on the contained path by default, explicit enablement still works when tested — the flag was never set in the environment), and existing non-zero ranking behaviour unchanged.
    - Command: `venv/bin/python3 -m pytest tests/regression/test_daily_picks_zero_score_preservation.py -v` → **15 passed**, 0 failed.
  - Related existing Daily Picks/ranking/containment/alpha-observations suites (11 files: `test_alpha_observations.py`, `test_daily_picks_containment.py`, `test_daily_picks_output_integrity.py`, `test_daily_picks_pipeline_telemetry.py`, `test_daily_picks_job_state.py`, `test_daily_picks_track_record.py`, `test_daily_picks_phase5_tier_and_confidence_selection.py`, `test_growth_intelligence_daily_picks_regression.py`, `test_valuation_intelligence_daily_picks_regression.py`, `test_daily_picks_us_universe_guard.py`, `test_sentiment_freshness.py`) → **227 passed**, 0 failed.
  - Full backend suite: `venv/bin/python3 -m pytest --tb=short -q` → **2276 passed, 1 failed** (`tests/unit/test_telegram_market_notifications.py::TestMarketFormatting::test_wording_and_disclaimer_preserved`). Investigated: this failure is caused by a pre-existing, unrelated uncommitted change already present in the working tree before this session started (`backend/services/telegram_bot.py`, one-line horizon-label wording update from "1–10 days"/"1–3 months"/"6M–3Y" to "1–5 days"/"2–4 weeks"/"3–6 months"), which this task's scope explicitly forbids touching. Confirmed unrelated to DP-009/DP-010 by inspection of `git diff` (only `daily_picks.py` and the two test files above were touched by this implementation) and by the failure's content (a Telegram message wording assertion with no reference to quality/tech/fund scores, z-scoring, or ranking).
- Production behaviour changed: `quality_score` (ranking input) and the `raw`/`tech_score`/`fund_score`/`quality_score` values consumed inside `_zscore_and_rank()` now preserve a genuine `0`/`0.0` instead of silently substituting `50`. This can change `combined_alpha`/`ranking_alpha` and therefore final Top-6 ordering for any candidate that previously had a genuine zero factor score miscoalesced to 50 (and, more subtly, corrects the z-scores of every *other* candidate in the same cross-section that was being compared against that miscoalesced value).
- Unrelated behaviour intentionally unchanged: the `is None` / missing-evidence path (sentiment `z = 0.0`, quality `quality_available=False` → `quality_score=50`) is untouched; `quality_raw_score` shadow/provenance handling (`_quality_raw`, `_build_alpha_observation_row`) is untouched in logic (only an adjacent comment was corrected for accuracy); population mean/std computation (`stats` loop) was already correct pre-fix and remains so; meta-model shadow computation and production-learning containment gating (`LEARNING_ALPHA_PRODUCTION_ENABLED`) are untouched and were not exercised as enabled at any point in this session; confidence, target price, stop-loss, R:R, horizon labels, universe construction, tier quotas, sector controls, liquidity/event gates, IC weights, regime multipliers/KMeans, optimizer, backtesting, outcome-resolution windows, frontend, DB schemas, API contracts, env vars, and schedules were not touched.
- Commit SHA: `2d721bb38bd52ffef183fc6fb66626c30ab36b5f`. Confirmed present on `origin/main` (`git rev-parse HEAD` and `git rev-parse origin/main` both resolve to this SHA as of the 2026-07-18 reconciliation).
- Deployment status: **DEPLOYED.** GitHub commit status for `2d721bb38bd52ffef183fc6fb66626c30ab36b5f` is overall `success`, with both checks green: Railway (`artistic-spontaneity - StockSense360` — "Success - stocksense-production-7e0d.up.railway.app") and Vercel ("Deployment has completed"), the latter succeeding as expected even though this was a backend-focused change (Vercel builds the frontend, which was unaffected but still deploys on every push to `main`). GitHub Actions check runs `test` and `refresh-fundamentals-in` both completed successfully; `refresh-fundamentals-in` is a pre-existing scheduled/automated workflow unrelated to Daily Picks generation — it was not manually triggered by this session. No manual deployment was initiated at any point.
- Production-verification status: **PENDING.** Per the register's status rules, this cannot become `PRODUCTION VERIFIED` merely because local tests pass, nor merely because the commit merged and deployed successfully. It remains pending until a naturally scheduled (not manually triggered) India or US Daily Picks generation run demonstrates, from live output, score snapshots, `alpha_observations` rows, or logs, that a genuine zero factor score survives to `quality_score`/z-scoring unmodified. No Daily Picks generation, backfill, retraining, or manual trigger was performed in this session or the prior implementation session.
- Outcome-validation evidence: None yet — outcome validation is a separate, longer-horizon concern (see DP-025–DP-028) and is not a precondition for this correctness fix; not claimed here.
- Remaining limitations: This fix only corrects the two `x or 50` truthiness fallbacks identified as DP-009/DP-010. It does not address any other controlled finding (in particular DP-020 and all P1/P2 items remain open and unimplemented, as instructed). Production verification (above) remains the only remaining step specific to DP-009/DP-010.
- Rollback trigger: If a naturally scheduled Daily Picks generation run shows unexpected ranking instability, an unhandled exception in `_predict_stock()` or `_zscore_and_rank()`, or any other regression traceable to this change, revert this commit (a plain `git revert`, not a history rewrite) and restore the prior `or 50` fallback pending re-investigation.
- Next safe action: Await naturally scheduled India or US Daily Picks generation and inspect live output, score snapshots, alpha observations or logs for a genuine zero factor score surviving without conversion to 50. Once confirmed, update this entry's status to `PRODUCTION VERIFIED`. Do not begin DP-020 or any other finding under this entry.
- Last verified date: 2026-07-18 (documentation reconciliation against deployed commit `2d721bb38bd52ffef183fc6fb66626c30ab36b5f`).

### DP-017 implementation evidence

- Governing decision: `DPD-007` — Regime model retraining policy (Status: DECIDED — SAFETY CONSTRAINT). No separate owner decision required for DP-017 itself; DPD-007 already mandates that "no retrained regime model may influence production unless semantic cluster labels are deterministically anchored" — this implementation fulfils that specific clause. `DP-018` (feature clipping) and `DP-019` (multiplier validation), the other two findings DPD-007 references, remain open and were explicitly not implemented in this task.
- Status: **VALIDATION PENDING** (implemented, merged to `origin/main`, and deployed — Railway and Vercel checks both succeeded on the implementation commit — but not yet confirmed against live production output).
- Root cause fixed: KMeans cluster integer IDs carry no stable meaning across a refit. `REGIME_LABELS = {0: "BULL_CALM", 1: "BULL_VOLATILE", 2: "BEAR_CALM", 3: "BEAR_PANIC"}` is a fixed ID→name table. Before this fix, `retrain_on_history()` fit a fresh `KMeans(n_clusters=4, n_init=10, random_state=42)` on accumulated history and saved it unconditionally — with no verification that raw cluster ID 0 in the new fit still corresponded to BULL_CALM in feature space. A retrain could silently invert every downstream regime label, description, weight multiplier, and any bull/panic dummy variable derived from `regime_id`.
- Implementation files changed:
  - `backend/services/alpha_engine/regime_cluster.py`:
    - Added `SEMANTIC_ANCHOR_CENTROIDS` — the single canonical anchor-centroid constant (the same 4 hand-crafted vectors previously duplicated only inside the bootstrap branch), in `REGIME_LABELS` ID order, now the one source of truth for what each semantic regime ID means in feature space.
    - Added `AnchorResult` (NamedTuple: `mapping`, `permutation`, `is_identity`, `total_distance`) and `anchor_to_semantic_labels(model, anchors=SEMANTIC_ANCHOR_CENTROIDS)` — a brute-force (4! = 24 permutations, no new dependency) minimum-total-distance assignment between a fitted model's `cluster_centers_` and the semantic anchors. Reorders `cluster_centers_` in place so a future `model.predict()` returns semantic IDs directly; remaps `labels_` in place when present; makes **no mutation at all** when the mapping is already identity; raises `ValueError` explicitly (never silently) if `cluster_centers_` is missing, non-2D, has the wrong cluster count, or the wrong feature dimension.
    - `_load_or_init_model()`: the bootstrap branch now fits on `SEMANTIC_ANCHOR_CENTROIDS` (same values, now the shared constant) and is still passed through `anchor_to_semantic_labels()` before saving (refuses to save and returns `None` if that somehow fails). The load-from-disk branch now calls `anchor_to_semantic_labels()` on every loaded artifact **in memory only** — never writes back to disk — before returning it; if anchoring raises (a malformed/corrupt artifact), that model is discarded exactly like a pickle-load failure was already discarded pre-fix (falls through to bootstrap), preserving prior fail-safe behaviour.
    - `retrain_on_history()`: after `km.fit(X)`, calls `anchor_to_semantic_labels(km)` **before** `_save_model(km)`. If anchoring raises `ValueError`, the function logs a clear rejection message and returns immediately — `_save_model()` is never called, the cache is never invalidated, and the previously saved (already-anchored) artifact is left untouched and in use.
  - No other file in `backend/services/` was changed. `REGIME_LABELS`, `REGIME_DESCRIPTIONS`, `REGIME_WEIGHT_MULTIPLIERS`, `extract_features()` (formulas, clipping, feature order), `CACHE_TTL`, Daily Picks ranking formulas, IC weights, confidence, targets, stop-loss/R:R, the optimizer, production-learning containment, schemas, APIs, schedules, environment variables, and frontend files were all left untouched, per scope.
- Test files changed: new file `backend/tests/regression/test_regime_cluster_anchoring.py` (47 tests, all passing) — covers identity mapping, all 24 raw-centroid permutations, `labels_` remapping (including the no-`labels_`-attribute case), malformed-input rejection (wrong cluster count, wrong feature dimension, missing/non-2D `cluster_centers_`), `_save_model()`-called-only-after-successful-anchoring ordering, anchoring-failure preserving the prior artifact on disk byte-for-byte and leaving the cache untouched, a permuted on-disk artifact being corrected in memory by `_load_or_init_model()` without any disk write, an identity on-disk artifact producing zero mutation, bootstrap producing an identity-mapped, disk-persisted model, downstream `label`/`description`/`weight_multipliers` consistency across 4 different raw-centroid permutations via `detect_regime()`, and confirmation that no production-learning containment flag is read, set, or affected. All model-file tests use a `tmp_path`-monkeypatched `MODEL_PATH`; the repository's real `regime_kmeans.pkl` was never read or written by this test suite.
- Tests and exact result:
  - `venv/bin/python3 -m pytest tests/regression/test_regime_cluster_anchoring.py -v` → **47 passed**, 0 failed.
  - Related existing regime/alpha-engine/containment suites (7 files: `test_regime_cluster_anchoring.py`, `test_alpha_engine_market_logging.py`, `test_daily_picks_pipeline_telemetry.py`, `test_weight_adapter_containment.py`, `test_weight_adapter_timing_instrumentation.py`, `test_alpha_observations.py`, `test_daily_picks_containment.py`) → **121 passed**, 0 failed.
  - Full backend suite: `venv/bin/python3 -m pytest --tb=short -q` → **2323 passed, 1 failed** (exactly 47 more passes than the DP-009/DP-010 baseline of 2276, confirming the new file added 47 passing tests with no other change in the pass count). The 1 failure is the same pre-existing, unrelated `tests/unit/test_telegram_market_notifications.py::TestMarketFormatting::test_wording_and_disclaimer_preserved` (caused by an uncommitted, out-of-scope `telegram_bot.py` wording change already present before this task began — not touched). No new failure was introduced by DP-017.
  - Note: `scikit-learn` (already declared in `backend/requirements.txt` as `scikit-learn>=1.4.0`) was missing from the local dev venv — a pre-existing environment gap (no prior test exercised real sklearn code; all prior regime tests mock `detect_regime`/`retrain_on_history` directly). It was installed into the local venv so this safety-critical helper could be tested against real `KMeans` behaviour rather than mocks alone. This is a local dev-environment action only — no production dependency file, environment variable, or deployment configuration was changed.
- Production behaviour changed: every code path that hands a KMeans model to `detect_regime()` (bootstrap, disk-load, post-retrain) now guarantees that `model.predict(...)` returns a semantic ID consistent with `REGIME_LABELS`, regardless of what raw integer ID the solver originally assigned. A `retrain_on_history()` call that would previously have silently saved and activated a mislabelled model now either saves a correctly-anchored one or (only on a genuine anchoring failure) refuses to save anything and leaves the prior artifact in force.
- Unrelated behaviour intentionally unchanged: regime count (4), regime names, `REGIME_DESCRIPTIONS`, `REGIME_WEIGHT_MULTIPLIERS` values, feature formulas/clipping/order, `CACHE_TTL`, Daily Picks ranking formulas, IC weights, confidence, targets, stop-loss/R:R, the optimizer, production-learning containment (`LEARNING_ALPHA_PRODUCTION_ENABLED` was never read, set, or exercised as enabled), schemas, APIs, schedules, and frontend files. DP-018 (feature clipping) and DP-019 (multiplier validation) remain open and unimplemented.
- Commit SHA: `e40dcdc84a87421330bd3d7243d6337dba28f9c3`. Confirmed present on `origin/main` (`git rev-parse HEAD` and `git rev-parse origin/main` both resolve to this SHA as of the 2026-07-18 reconciliation).
- Deployment status: **DEPLOYED.** GitHub commit status for `e40dcdc84a87421330bd3d7243d6337dba28f9c3` is overall `success`, with both checks green: Vercel ("Deployment has completed") and Railway (`artistic-spontaneity - StockSense360` — "Success - stocksense-production-7e0d.up.railway.app"). Both automatic deployment checks succeeded as a normal consequence of the push to `origin/main`; no manual deployment was initiated at any point.
- Production-verification status: **PENDING.** This cannot become `PRODUCTION VERIFIED` merely because local tests pass, nor merely because the commit merged and deployed successfully. It requires naturally occurring live evidence — from a naturally scheduled retrain or model load, not one triggered solely for this reconciliation — that the deployed code returns semantically anchored, consistent regime IDs. No retraining against real history, Daily Picks generation, backfill, or manual production request was performed in this session or the prior implementation session.
- Confirmation — no retraining triggered: `retrain_on_history()` was never called against real production history in the implementation session or this deployment-status reconciliation; all test invocations used synthetic, monkeypatched history and a `tmp_path`-redirected `MODEL_PATH`.
- Confirmation — no model artifact manually rewritten: the repository's real `backend/services/alpha_engine/regime_kmeans.pkl` (untracked, confirmed via `git ls-files`) was never opened, read, or written by any test, by the implementation session, or by this reconciliation session; every test that exercises save/load uses a temporary path.
- Remaining limitations: This fix only addresses DP-017 (cluster-ID anchoring). `DP-018` (four of five regime features are unclipped/unwinsorised) and `DP-019` (regime multipliers lack production-pipeline validation) remain open, unimplemented, and explicitly out of scope for this task.
- Rollback trigger: If a naturally scheduled retrain or model load shows unexpected regime-label instability, an unhandled exception inside `_load_or_init_model()`/`retrain_on_history()`/`detect_regime()`, or any other regression traceable to this change, revert this commit (a plain `git revert`, not a history rewrite) and restore the prior unanchored behaviour pending re-investigation. The bootstrap/bundled logic falling back to `regime_id = 0` (BULL_CALM) on any exception, unchanged from before this fix, remains the ultimate fail-safe.
- Next safe action: Await a naturally scheduled regime retrain or model load in production (do not trigger manually) and inspect logs/`alpha_observations`/regime snapshots for `[regime] KMeans retrained on ... (anchored: identity=..., total_distance=...)` or equivalent evidence that anchoring ran and produced semantically consistent IDs. Once confirmed, update this entry's status to `PRODUCTION VERIFIED`. Do not begin DP-018, DP-019, DP-020, or DP-025 under this entry.
- Last verified date: 2026-07-18 (documentation reconciliation against deployed commit `e40dcdc84a87421330bd3d7243d6337dba28f9c3`).

## Existing work reconciled — not to be reimplemented

| Work | Commit | Register interpretation |
|---|---|---|
| BUY confidence range rescaling | `f068156b2393bcd7ce927b38888bb9ea2284d790` | Implemented before this register; does **not** close DP-006 because the value remains a score rescale rather than a calibrated probability. |
| SELL rescaling and HOLD investigation | `8dc61340f5067bbc26e548488a1ed4a3f06a4912` | Investigation/rescaling acknowledged; does not close DP-006. |
| Target floors scaled by confidence | `970e0d64c524d069261b5e3edccdb02e0f9be68c` | Closes the old flat-floor issue only; does not close DP-001/003/005. |
| Historical track record added to API | `4922553fb21512b767c8feaff0853b01563c67ec` | Adds truthful evidence disclosure; does not convert confidence into calibrated probability. |
| Historical track record displayed in UI | `40b0a42d53de81f8cfbad1b926996e23f4e5030c` | Production UI evidence exists; DP-006 remains open. |
| Learning graduation observability | `203250ca7237420e01da2a37ba6115f81bd49989` | Read-only observability; DP-030 remains monitoring and learning remains contained. |
| Financial Strength liquidity-distress Top-6 exclusion | `d082d526da5b3019cdba065d95c543c0cc25ada0` | Existing investor-safety gate acknowledged; not a tradability/liquidity gate and therefore does not close DP-011. |

## Implementation evidence template

Use this section under the relevant item after each approved implementation:

```markdown
### DP-### implementation evidence
- Approved decision: DPD-### or Not required
- Status:
- Files changed:
- Tests and exact result:
- Commit SHA:
- Deployment platform/status:
- Production verification evidence:
- Outcome-validation evidence:
- Remaining limitations:
- Rollback trigger:
- Next safe action:
- Last verified date:
```

## Session protocol

At the beginning of every Daily Picks engineering session:

1. Read this register and the decision log.
2. Reconcile the requested task with current `main` and recent commits.
3. State the exact `DP-###` item and current status.
4. Do not combine unrelated P0/P1 findings in one implementation commit.
5. End with the register update and exact next safe action.
