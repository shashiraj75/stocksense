"use client";
import { AlertTriangle } from "lucide-react";

/**
 * Product Integrity Workstream — Phase GPI-0 (temporary truthfulness
 * containment, not a fix).
 *
 * The global price-integrity audit confirmed the Daily Picks Backtest panel
 * and Live Performance Tracker are not currently trustworthy:
 *
 *   - validation_engine.py has a confirmed symbol-market routing defect —
 *     its market-routing heuristic ignores the caller-supplied universe and
 *     misroutes a material part of the India universe to the wrong
 *     exchange/currency (e.g. an NSE symbol resolving to a USD ADR line);
 *   - the Backtest panel's own query (`["validation", horizon]`,
 *     `GET /api/validation/results?horizon=...`) omits market/universe
 *     entirely and silently defaults to the India/Nifty universe — so the
 *     US Daily Picks tab could display India validation results while
 *     labelling them "Alpha vs S&P 500";
 *   - the Live Performance Tracker's query
 *     (`["picks-performance", horizon]`) also omits market, can blend India
 *     and US resolved picks together, and treats a missing benchmark return
 *     as 0 rather than "unknown" — so its alpha/"Beat benchmark" figures are
 *     not currently meaningful;
 *   - prediction-time entry price and outcome-resolution price are two
 *     structurally different Yahoo Finance data products, fetched
 *     independently and never reconciled, so a resolved pick's published
 *     return% is not proven to reflect the price a user actually saw.
 *
 * None of this affects Daily Picks signal generation, scoring, the India
 * session-freshness containment, or the cross-market payload guard — this
 * hold is scoped strictly to the historical-accuracy/performance display.
 *
 * `INTEGRITY_HOLD_ACTIVE` gates `BacktestPanel`/`LivePerformanceTracker`
 * (frontend/src/app/picks/page.tsx) out of the render tree entirely, not
 * just visually — neither component's `useQuery` call ever mounts while
 * this flag is `true`, so their requests never fire. Both components stay
 * fully implemented in that file, unmodified, for restoration once the
 * backend work below lands.
 *
 * Remove this hold (set `INTEGRITY_HOLD_ACTIVE = false` below and restore
 * the two render call sites in page.tsx) only once ALL of the following are
 * true:
 *   1. [DONE — Product Integrity #006] validation_engine.py's market-routing
 *      heuristic is fixed to use the caller-supplied universe instead of
 *      re-deriving its own guess.
 *   2. [DONE — 2026-07-17] validation results have been rerun for the
 *      correct market universe on both IN and US (8 runs: short/medium/long
 *      x nifty100/midcap, plus long/us and short/us — all using the #1 fix,
 *      which predates these runs).
 *   3. [OPEN] the outcome entry-price contract is corrected so the price
 *      shown to users at generation time and the price used to resolve an
 *      outcome share one reconciled, auditable basis. This is the ONLY
 *      remaining condition — it's a real redesign of the prediction-
 *      generation/outcome-resolution price contract, not a quick fix, and
 *      deliberately wasn't rushed alongside 2/4/5 below.
 *   4. [DONE — 2026-07-17] benchmark_return_5d/20d/60d are populated by the
 *      live outcome resolver (services/alpha_engine/outcome_logger.py's
 *      resolve_pair), not left NULL and defaulted to 0 client-side.
 *   5. [DONE — 2026-07-17] market filtering is added to both
 *      `/api/validation/results` (already had it) and `/api/picks/
 *      performance` (added), each additive/opt-in via a new `market`
 *      param — the frontend query keys/requests below still don't pass it,
 *      since restoring these panels is condition 3's job, not this pass's.
 *
 * Do not flip INTEGRITY_HOLD_ACTIVE based on 1/2/4/5 alone — condition 3
 * is the actual gate. Fixing it requires reconciling
 * prediction_engine.py's generation-time price source with outcome_logger.py's
 * independent resolution-time refetch, which is a real design decision
 * about the entry-price contract, not a bug with one obvious fix.
 */
export const INTEGRITY_HOLD_ACTIVE = true;

export function ValidationIntegrityHold() {
  return (
    <div className="bg-yellow-500/10 border border-yellow-500/30 rounded-xl p-4 flex items-start gap-3">
      <AlertTriangle size={18} className="text-yellow-400 mt-0.5 shrink-0" />
      <div>
        <p className="text-sm font-semibold text-yellow-300">
          Historical validation and live performance statistics are temporarily withheld while market routing, entry-price basis, and benchmark-return calculations are being verified. Current Daily Picks signals are not recalculated or changed by this notice.
        </p>
        <p className="text-xs text-yellow-400/70 mt-1">
          The underlying signal cards remain available for research. Accuracy, alpha, hit-rate and benchmark comparisons should not be relied upon until verification is complete.
        </p>
      </div>
    </div>
  );
}
