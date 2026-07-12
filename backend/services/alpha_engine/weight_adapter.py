"""
Live Weight Adaptation Loop.

Runs after each daily picks generation to, for the given market:
  1. Invalidate IC cache → next call recomputes from latest outcomes
  2. Retrain meta-model (Ridge/XGBoost) for each horizon if enough data
  3. Retrain regime KMeans on accumulated historical snapshots (shared
     across markets — regime detection is a global macro signal, see
     regime_cluster.py)
  4. Print a diagnostics summary

This is the "learning" in Learning Alpha Engine.
Everything improves automatically as the system accumulates real predictions
and their actual forward return outcomes — separately for IN and US, since
each market's IC engine and meta-model are trained independently (see
ic_engine.py / meta_model.py for why).
"""

import logging

from services.alpha_engine import ic_engine, meta_model, regime_cluster
from services.alpha_engine.store import count_training_rows

log = logging.getLogger(__name__)


def run_adaptation(market: str = "IN"):
    """
    Run the full adaptation cycle for one market.
    Safe to call in a background thread after picks generation.
    Non-fatal — any error is caught and logged.

    Learning Alpha Engine remediation, Phase 1: while production learning is
    disabled (the default — see services.alpha_engine.containment), this
    function still invalidates the IC cache (step 1) so shadow/diagnostic IC
    values (ic_engine.get_ic_values, shadow_ic_available) stay fresh against
    the latest resolved outcomes — but it does NOT call meta_model.train().
    No Learning Alpha IC/meta-model production artifact is trained or
    overwritten while containment is active. Regime KMeans (step 3) remains
    independent and unchanged by this containment — it trains on
    regime_log's global macro features, not the legacy predictions/outcomes
    join, and always runs regardless of the containment flag.

    Cache invalidation is safe to leave unconditional because it can only
    affect what a FRESH call to ic_engine.get_ic_weights() (the
    live-adaptation-eligible path) would return next — and
    get_production_ic_weights(), the only entry point PRODUCTION Daily
    Picks ranking calls, never calls get_ic_weights() at all while
    contained (see ic_engine.py). A stale-vs-fresh live IC cache is
    therefore invisible to published ranking either way; it only changes
    what shadow diagnostics observe.
    """
    from services.alpha_engine.containment import is_production_learning_enabled

    production_enabled = is_production_learning_enabled()
    log.info(f"[weight_adapter] Starting adaptation cycle ({market}) …")
    if not production_enabled:
        log.info("Production learning disabled: legacy training data quarantined.")

    # ── 1. Invalidate IC cache — always, regardless of containment. This only
    # refreshes the shadow/diagnostic live-IC value; production ranking never
    # reads it while contained (see docstring above and ic_engine.py).
    try:
        ic_engine.invalidate_cache()
        log.info("[weight_adapter] IC cache invalidated — shadow diagnostics will recompute on next read")
    except Exception as e:
        log.warning(f"[weight_adapter] IC invalidation error: {e}")

    # ── 2. Retrain meta-model per horizon ─────────────────────────────────────
    for horizon in ("short", "medium", "long"):
        try:
            n = count_training_rows(horizon, market=market)
            if not production_enabled:
                log.info(
                    f"[weight_adapter] {market}/{horizon}: containment active — "
                    f"meta-model retraining skipped ({n} legacy rows quarantined, diagnostics only)"
                )
                continue
            if n >= meta_model.MIN_ROWS:
                success = meta_model.train(horizon, market=market)
                if success:
                    log.info(f"[weight_adapter] Meta-model retrained for {market}/{horizon} ({n} rows)")
                else:
                    log.warning(f"[weight_adapter] Meta-model training failed for {market}/{horizon}")
            else:
                log.info(f"[weight_adapter] {market}/{horizon}: {n}/{meta_model.MIN_ROWS} rows — skipping retrain")
        except Exception as e:
            log.warning(f"[weight_adapter] Meta-model error ({market}/{horizon}): {e}")

    # ── 3. Retrain regime KMeans ──────────────────────────────────────────────
    # Shared across markets — regime detection runs on global macro features
    # (VIX, DXY, US10Y) plus both Nifty and S&P trend, not per-market history.
    try:
        regime_cluster.retrain_on_history()
    except Exception as e:
        log.warning(f"[weight_adapter] Regime retrain error: {e}")

    # ── 4. Print diagnostics ──────────────────────────────────────────────────
    try:
        for horizon in ("short", "medium", "long"):
            ic = ic_engine.get_ic_values(horizon, market=market)
            n  = count_training_rows(horizon, market=market)
            ic_str = ", ".join(f"{k}={v:.3f}" for k, v in ic.items())
            model_status = "✓ trained" if meta_model.is_trained(horizon, market=market) else "◻ using IC-alpha"
            log.info(f"[weight_adapter] {market}/{horizon}: {n} rows | IC=[{ic_str}] | model={model_status}")
    except Exception as e:
        log.warning(f"[weight_adapter] Diagnostics error: {e}")

    log.info(f"[weight_adapter] Adaptation cycle complete ({market}).")
