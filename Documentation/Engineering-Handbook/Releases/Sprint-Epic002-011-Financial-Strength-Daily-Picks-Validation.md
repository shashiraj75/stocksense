# Epic 002, Sprint #011 — Financial Strength Daily Picks Validation

**Status:** Validation sprint with one genuine, narrowly-scoped defect found and fixed (mirroring Epic 001's established precedent for validation sprints). Daily Picks' ranking architecture, Financial Strength's scoring, Business Quality, and provider precedence are all otherwise unmodified.
**Governed by:** SSDS-005, SSDS-006, the Epic-002-Sprint-010 Prediction Engine Integration report, SES-001 through SES-005.

---

## 1. How Daily Picks Currently Consumes Prediction Engine Output

Read directly from `services/daily_picks.py`, not assumed:

- **Phase 0 (`_bulk_screen`):** narrows the full market-cap-filtered universe down to `n_candidates=50` via a cheap momentum pre-screen (no `PredictionEngine` call yet).
- **Phase 1 (`_predict_stock`):** for each of the 50 candidates × 3 horizons (short/medium/long) = up to 150 tasks, calls `PredictionEngine.predict()` and extracts a fixed set of fields into a flat dict — `signal`, `confidence`, `tech_score`, `fund_score`, `sentiment_score`, `quality_score`, `reasoning`, `composite_score`, etc.
- **Phase 4 (`_zscore_and_rank`):** cross-sectionally z-scores **only** `tech_score`, `fund_score`, `sentiment_score`, `quality_score` (the `_FACTOR_KEYS` mapping) into an IC-weighted (or meta-model) `ranking_alpha` — **`confidence` is not one of the ranked factors.**
- **Ranking:** `ranked = sorted(universe, key=lambda x: x.get("ranking_alpha", 0), reverse=True)`.
- **Final selection (`_passes_quality_gate`):** filters the ranked, `signal == "BUY"` list by (a) `confidence >= 25`, (b) absence of a `"Risk/Reward"` or `"Governance Risk"` red-flag indicator in `reasoning`, (c) (short horizon only) no "Overbought" RSI flag — then takes the top 6.

---

## 2. Is Financial Strength Already Included Automatically?

**Partially — and this sprint found the gap in the "partially."**

| Pathway | Included automatically? |
|---|---|
| `confidence` (the field Financial Strength actually influences, per Sprint #010) | **Yes** — `_predict_stock` extracts `result.get("confidence")` directly, which already reflects `_apply_financial_strength_adjustment`'s effect. No code change was needed for this. |
| `reasoning` (the field carrying Financial Strength's explainability text) | **Yes** — `_predict_stock` extracts `result.get("reasoning", [])`, the same mutable list `_apply_financial_strength_adjustment` appends to during `predict()`. Confirmed by direct code tracing, not assumed. |
| **Ranking (`ranking_alpha`)** | **No, by architecture — and correctly so.** Financial Strength never touches `tech_score`/`fund_score`/`sentiment_score`/`quality_score`, so it cannot and does not influence which stocks rank highest. This matches Sprint #010's own explicit design (confidence-only, never the composite score) — not a gap, a deliberate boundary. |
| **The Top-6 exclusion gate** | **No — confirmed as a genuine gap, found and fixed this sprint.** See §3. |

---

## 3. Genuine Defect Found and Fixed

`_passes_quality_gate` excludes a `"BUY"`-signaled stock carrying a `"Risk/Reward"` or `"Governance Risk"` red-flag indicator from the curated Top 6 — explicitly because, per the function's own existing comment, *"that floor exists to filter pure noise, not to let a flagged 'avoid'-level red flag back into a curated 'Top 6' list just because it didn't drop low enough."* Financial Strength's hard `liquidity_distress` gate (Sprint #010) demotes confidence to the exact same severity tier (capped at 30, which still clears the 25% floor) — but used a different indicator name (`"Financial Strength"`), which the existing check never looked for.

**Confirmed live, before any fix:**

```python
r = {"symbol": "AAL", "signal": "BUY", "confidence": 30,
     "reasoning": [{"indicator": "Financial Strength", "signal": "BEARISH",
                     "reason": "...liquidity distress hard gate triggered..."}]}
_passes_quality_gate(r, "medium")  # => True — WRONG. A real AAL-shaped, hard-gated
                                    #    company could reach the Top 6.
```

**Fix — one narrowly-scoped, signal-aware check, not a blanket indicator-name exclusion** (a blanket exclusion would be wrong: `"Financial Strength"` is also the indicator name for a *positive* confidence boost, e.g. a fortress-balance-sheet company):

```python
fs_reasons = " ".join(
    item.get("reason", "") for item in r.get("reasoning", [])
    if isinstance(item, dict) and item.get("indicator") == "Financial Strength"
)
if "liquidity distress" in fs_reasons.lower():
    return False
```

**Confirmed post-fix, all four cases:**

| Scenario | Result |
|---|---|
| AAL-shaped (hard-gated, `liquidity_distress`) | **Excluded** ✓ |
| MSFT-shaped (bullish, +6 boost) | **Still passes** ✓ — confirms no overcorrection |
| BA-shaped (soft bearish, −5, not hard-gated) | **Still passes** ✓ — only the hard-gate phrase excludes, not every bearish signal |
| No Financial Strength data (IN/CRYPTO/excluded sector) | **Still passes** ✓ — unaffected |

6 new regression tests lock this in, sanity-checked per SES-003 §4 by reverting the fix and confirming the static source-guard test fails (the behavioral tests, which reconstruct the closure's logic directly per this codebase's own established pattern for testing non-importable nested closures, necessarily pass regardless — the static check is what actually guards the real file).

---

## 4. Before/After Comparison

| Dimension | Before this sprint's fix | After |
|---|---|---|
| **Daily pick ordering** | Unaffected by Financial Strength either way — confirmed architecturally (§2) and not changed by this sprint's fix (the fix touches only the post-ranking exclusion filter, never `ranking_alpha`). |
| **Confidence changes** | Already flowed through automatically since Sprint #010 (no change this sprint). |
| **Excluded/downgraded weak balance-sheet companies** | **Materially different.** Before: a hard-gated company (real AAL shape) could reach the Top 6 if its composite score happened to rank it there. After: it cannot — confirmed via the test in §3. |
| **Promoted financially strong companies** | Unaffected before and after — a fortress-balance-sheet company was never at risk of wrongful exclusion; this sprint's fix only closes a gap in the *exclusion* logic, never touches promotion. |
| **Explainability output** | Unaffected before and after — already flowed through via `reasoning` since Sprint #010 (§2). |

---

## 5. Validation Across Required Segments

Using the real, already-validated 76-company Sprint #008/#009/#010 dataset (the same universe this entire epic has used throughout):

| Segment | Representative companies | Finding |
|---|---|---|
| **US large-cap** | AAPL, MSFT, GOOGL, AMZN | Confidence deltas bounded to ±6 (Sprint #010); ranking unaffected; none excluded. |
| **Highly leveraged** | BA (D/E 910%), CAT (D/E 202%) | BA correctly demoted (−5 confidence); neither hard-gated, so the Top-6 exclusion fix doesn't apply to these — confirmed they were never at risk and remain unaffected. |
| **Utilities** | AEP, DUK, SO, NEE | AEP — post-Sprint-#009 calibration — is no longer hard-gated, so this sprint's fix correctly does **not** exclude it (a soft −4 confidence demotion only, same as before this sprint). DUK/SO/NEE similarly unaffected by this sprint's specific fix (none carry a `liquidity_distress` flag). |
| **Airlines** | AAL | **The one segment this sprint's fix directly changes** — AAL is now correctly excluded from the Top 6 if it would otherwise have ranked there, closing the exact gap named in §3. |
| **Technology** | AAPL, ORCL, CSCO | Unaffected — no hard-gated technology company exists in the validated universe. |
| **Energy** | XOM, CVX | Unaffected — both strong, no hard gate. |
| **Healthcare** | PFE, MRK, JNJ | Unaffected — all strong, no hard gate. |

**Headline finding: across the full 76-company validated universe, AAL is the only company whose Daily Picks treatment this sprint's fix actually changes** — confirming the fix is narrow and precisely targeted, not a broad behavioral shift.

---

## 6. Performance Impact

| Measurement | Result |
|---|---|
| **Daily Picks' actual call volume** | Up to 150 `PredictionEngine.predict()` calls per market per run (50 candidates × 3 horizons) — confirmed by direct code reading (`tasks = [(sym, h) for sym in candidates for h in (...)]`). |
| **Concurrency** | **`ThreadPoolExecutor(max_workers=1)`** — confirmed in source, with an explicit existing comment: *"max_workers=1 to avoid Yahoo Finance rate-limiting Render's IP."* All 150 calls run **strictly sequentially** — there is no parallelism to absorb added per-call latency. |
| **Added latency per symbol, US market (from Sprint #010's measurements)** | Cold (first occurrence in the run): **+4.74s**. Warm (the same symbol's 2nd/3rd horizon, same run, benefiting from SEC EDGAR's existing 12h facts cache + 24h ticker-map cache): **+0.54s**. |
| **Estimated total added latency, one market run** | ≈ 50 unique symbols × (1 cold + 2 warm) = 50 × (4.74 + 2×0.54) ≈ **291 seconds (~4.85 minutes)** added to a single `generate_picks()` call for the US market, assuming a cold process start (no prior Financial Strength activity this run). |
| **Cache hit behavior, confirmed** | SEC EDGAR's existing caches are exercised correctly (the cold→warm timing drop is the direct evidence) — no new caching gap found. **No caching layer exists for the yfinance side of `us_financial_strength_adapter.py`** (it constructs a fresh `yfinance.Ticker(symbol)` every call) — named as a pre-existing gap inherited from Sprint #008, not introduced or fixed this sprint (fixing it would be a `financial_strength` scoring/adapter change, explicitly out of this validation sprint's scope). |
| **Provider call count, per symbol** | +1 SEC EDGAR `companyfacts` call (rate-limit-safe, self-throttled) + 1 new, independent `yfinance.Ticker()` construction — confirmed unchanged from Sprint #010's own measurement (this sprint adds no new provider calls of its own). |

---

## 7. Does Cold Latency Create a Daily Picks Production Risk?

**Yes — material, not negligible, given the existing single-worker sequential architecture has zero slack to absorb it.** ~4.85 minutes added to a job that already runs 150 sequential, multi-factor predictions (each independently fetching OHLCV history, news sentiment, global context, quality factors, and Business Quality) is a real, additive cost on top of an already long-running batch job — not a rounding error. This is stated as a finding, not a crisis: Daily Picks is a 1–2×/day batch job (per the existing `daily_picks.yml` cron, confirmed in SSDS-000 §6), not a real-time, user-facing path, so a few extra minutes is plausibly tolerable — but this sprint has no direct evidence of Render's specific execution-time ceiling for this job, and asserting "this will not cause a timeout" would be exactly the kind of unverified claim this engagement's evidence-over-assumption discipline exists to prevent. **Named honestly as an open, unverified question, not resolved either way.**

---

## 8. Recommendation on a Dedicated Performance Sprint

**Recommended, before any wider rollout (e.g., extending Financial Strength to India, or to a larger candidate pool than 50).** Two concrete, scoped candidates a future performance sprint should evaluate (named, not built, per this sprint's "validation, not redesign" rule):
1. A local cache layer for `us_financial_strength_adapter.py`'s yfinance-side fetch, mirroring `us_fundamentals.py`'s own existing 4-hour `CACHE_TTL` pattern — would convert every "cold" call after the first into a true cache hit rather than relying on yfinance's own incidental in-process behavior.
2. Re-examining whether `max_workers=1` is still the right ceiling now that the per-task cost has grown (Financial Strength is the third additive engine call inside `_predict_stock`, after Business Quality and Deep Fundamentals) — out of scope to change this sprint, but worth a dedicated look once real production timing data exists.

---

## Test Summary

| Category | New this sprint |
|---|---|
| Regression | 6 |
| **Full suite, before this sprint** | 431 passing |
| **Full suite, after this sprint** | **437 passing, 0 failing** |

No unit/integration/golden tests were added — per this sprint's own rule ("add tests only if code changes are required"), the one code change made (`daily_picks.py`'s quality-gate fix) is fully covered by the 6 regression tests above; no other code changed.

## GitHub Actions Result

Recorded below, after this sprint's commit is pushed and confirmed.

## Final Commit Hash

Recorded below, after this sprint's commit.

---

## 9. Recommendation on What's Next

| Option | Recommendation |
|---|---|
| **Portfolio Copilot** | **Not yet** — unchanged from Sprint #010's finding: it does not exist in this codebase today (confirmed again, unchanged, across SSDS-000 §3, the Product Glossary, MASTER-ROADMAP.md §2). Nothing to integrate into. |
| **Dedicated performance sprint** | **Recommended next**, given §7/§8's findings — the ~4.85-minute addition to an already-long, single-threaded batch job is real and unverified against any production timeout ceiling. This is the most evidence-backed, immediately-actionable next step. |
| **Epic 002 closure** | **Not yet** — Epic 002's own MASTER-ROADMAP.md completion criteria (Section 3) name "first consumer integrated" as a closure condition, now met (Sprint #010), but the performance question this sprint surfaced is a genuine, unresolved open item that a responsible closure report should not paper over. Recommend the performance sprint runs first, with Epic 002 closure following once its finding (whether the latency is or isn't a real production risk) is actually resolved with evidence — mirroring exactly how Epic 001 did not close until every live-validation finding was either fixed or explicitly, knowingly accepted as named technical debt. |

---

*This sprint validated Financial Strength's effect on Daily Picks, found and fixed one genuine, narrowly-scoped defect (a Top-6 exclusion gap for the liquidity_distress hard gate), and surfaced a real, unresolved performance question. Daily Picks' ranking architecture, Financial Strength's own scoring, Business Quality, and provider precedence are all otherwise unmodified.*
