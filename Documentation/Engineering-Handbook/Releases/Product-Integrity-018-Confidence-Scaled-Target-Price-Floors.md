# Product Integrity Workstream #018 — Confidence-Scaled Target Price Floors

**Status:** Implemented, tested, and locally committed. Push/deploy is subject to the pre-push production safety gate documented in this same turn's final report.

**Follows directly from user feedback on [Product Integrity #017](Product-Integrity-017-AI-Prediction-Confidence-Display-Fixes.md)** — after PI-017 added a low-confidence warning banner to the Trade Levels card, the user asked the sharper question underneath it: *"showing only 12% confidence but upside is 15.8% for long term, how's that possible?"* PI-017 treated the symptom (disclose that the numbers might not be trustworthy); this release treats the actual cause.

## 1. Root cause

`PredictionEngine._estimate_target()` computes each pick's target price from real inputs (analyst targets, PE/EPS extrapolation, ATR-based technical moves) — but every BUY/SELL signal's target was then clamped to a **flat, confidence-independent minimum floor**: a long-term BUY always showed at least +15% upside, a medium-term BUY at least +5%, regardless of whether confidence was 90% or 12%. The one place confidence *did* factor in (`conf_factor = max(0.5, confidence / 100)`, used for the short-horizon move and medium-horizon organic projection) still floored at 0.5 — so even at near-zero confidence, the computed magnitude could never drop below half of the full-confidence value.

**Net effect: confidence and target magnitude were almost entirely decoupled for weak signals.** A 12%-confidence BUY and a 90%-confidence BUY displayed the same minimum-guaranteed upside — the exact pattern the user's screenshot showed (12% confidence, +15.8% target, barely above the 15% floor).

## 2. Fix

`conf_factor`'s floor lowered from 0.5 to 0.2, and applied consistently to every BUY/SELL floor clamp across all three horizons (short already used it; medium and long previously used flat constants):

| Horizon | Signal | Old floor | New floor |
|---|---|---|---|
| Short | BUY/SELL | `atr * 2.5 * conf_factor` (conf_factor ≥ 0.5) | same formula, conf_factor ≥ 0.2 |
| Medium | BUY (analyst path) | flat `price * 1.05` | `price * (1 + 0.05 * conf_factor)` |
| Medium | SELL (analyst path) | flat `price * 0.95` | `price * (1 - 0.05 * conf_factor)` |
| Medium | BUY (projection path) | flat `price * 1.05` | `price * (1 + 0.05 * conf_factor)` |
| Medium | SELL (projection path) | flat `price * 0.92` | `price * (1 - 0.08 * conf_factor)` |
| Long | BUY | flat `price * 1.15` | `price * (1 + 0.15 * conf_factor)` |
| Long | SELL | flat `price * 0.80` | `price * (1 - 0.20 * conf_factor)` |

At full confidence (100%), `conf_factor` caps at 1.0, so the floor matches the original flat constant exactly — no change in behavior for high-confidence picks. At the new 0.2 floor, a near-zero-confidence BUY now shows roughly a fifth of the full-confidence minimum upside (e.g. long-term: ~3% instead of 15%) — still guaranteed positive (a BUY must show *some* edge, or the signal label itself would be self-contradictory), but no longer misleadingly large.

**HOLD is intentionally untouched.** HOLD's existing ±8%/±10% bands aren't "floors" in the same sense — they're a symmetric cap expressing "nothing dramatic either way," which doesn't carry a directional-conviction claim for a floor to be proportional to.

## 3. Downstream propagation (verified, not just assumed)

`target_price` from `_estimate_target()` flows directly into `_trade_levels(price, signal, target, atr, horizon)` as `take_profit = round(target, 2)` — so this fix automatically corrects both the "Target Price" figure in the AI Prediction card header and the "Take Profit" box in the Trade Levels card; they were always the same underlying number and remain so. As a side effect, `_trade_levels`' own stop-loss-tightening logic (which targets a 1.5 risk/reward ratio, tightening the stop toward the target rather than stretching the target) will now honestly report a sub-1.5 risk/reward for weak signals with a smaller confidence-scaled target, rather than always hitting 1.5 artificially — no code change needed there, it already has the "surface the honest R:R rather than faking the take-profit" fallback built in.

This is core prediction logic — it affects every consumer of `predict()`'s `target_price`/`trade_levels` fields, not just the Stock Detail page: Daily Picks generation, Multibagger scoring context, and backtest target comparisons.

## 4. What this release does not do

- Does not change the *organic* target computations themselves (analyst-target blending, PE/EPS extrapolation, ATR-based short-term moves) — only the minimum floor applied on top of them.
- Does not change `confidence` itself, `_confidence_engine()`, or any of its component weights.
- Does not change entry-zone or stop-loss computation logic directly (only inherits the effect via a now-smaller `target`/`profit_distance`).
- Does not touch HOLD's target band.

## 5. Tests

- `test_estimate_target_confidence_scaling.py` — 8 new behavioral tests (not source-assertions — these directly call `_estimate_target()` with constructed `df`/`info` inputs and check real numeric output), covering: low-vs-high confidence floor comparison for BUY and SELL at both medium and long horizons, confirmation the floor never goes to zero even at 1% confidence, confirmation HOLD is unaffected, and confirmation the short-horizon floor is now meaningfully lower than the old 0.5-floor behavior.
- One test-writing correction worth noting: an initial fixture assumption (that `info={}` alone would produce a small enough organic long-term target to isolate the floor) was wrong — the organic computation has its own internal `max(eps_growth, 0.05)` floor that compounds to ~15.8% over 3 years regardless of input, which was *itself* already above the BUY floor in both the low- and high-confidence cases, making the floor a no-op in that test. Corrected by constructing inputs (below-price analyst target + strongly negative earnings growth) that reliably push the organic target below both floors, so the floor is what's actually being measured.
- Full backend suite: **2177/2177 passed** (2169 baseline + 8 new).
- No frontend changes this release (PI-017's warning banner text remains accurate and unchanged — it doesn't assert specific percentages).

## 6. Rollback

Single self-contained change to `_estimate_target()` — reverting restores the exact prior flat-floor behavior. No schema, no API contract change, no other file touched.
