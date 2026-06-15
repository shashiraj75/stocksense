"""
Live Weight Adaptation Loop.

Runs after each daily picks generation to:
  1. Invalidate IC cache → next call recomputes from latest outcomes
  2. Retrain meta-model (Ridge/XGBoost) for each horizon if enough data
  3. Retrain regime KMeans on accumulated historical snapshots
  4. Print a diagnostics summary

This is the "learning" in Learning Alpha Engine.
Everything improves automatically as the system accumulates real predictions
and their actual forward return outcomes.
"""

from services.alpha_engine import ic_engine, meta_model, regime_cluster
from services.alpha_engine.store import count_training_rows


def run_adaptation():
    """
    Run the full adaptation cycle.
    Safe to call in a background thread after picks generation.
    Non-fatal — any error is caught and logged.
    """
    print("[weight_adapter] Starting adaptation cycle …")

    # ── 1. Invalidate IC cache ────────────────────────────────────────────────
    try:
        ic_engine.invalidate_cache()
        print("[weight_adapter] IC cache invalidated — will recompute on next picks run")
    except Exception as e:
        print(f"[weight_adapter] IC invalidation error: {e}")

    # ── 2. Retrain meta-model per horizon ─────────────────────────────────────
    for horizon in ("short", "medium", "long"):
        try:
            n = count_training_rows(horizon)
            if n >= meta_model.MIN_ROWS:
                success = meta_model.train(horizon)
                if success:
                    print(f"[weight_adapter] Meta-model retrained for {horizon} ({n} rows)")
                else:
                    print(f"[weight_adapter] Meta-model training failed for {horizon}")
            else:
                print(f"[weight_adapter] {horizon}: {n}/{meta_model.MIN_ROWS} rows — skipping retrain")
        except Exception as e:
            print(f"[weight_adapter] Meta-model error ({horizon}): {e}")

    # ── 3. Retrain regime KMeans ──────────────────────────────────────────────
    try:
        regime_cluster.retrain_on_history()
    except Exception as e:
        print(f"[weight_adapter] Regime retrain error: {e}")

    # ── 4. Print diagnostics ──────────────────────────────────────────────────
    try:
        for horizon in ("short", "medium", "long"):
            ic = ic_engine.get_ic_values(horizon)
            n  = count_training_rows(horizon)
            ic_str = ", ".join(f"{k}={v:.3f}" for k, v in ic.items())
            model_status = "✓ trained" if meta_model.is_trained(horizon) else "◻ using IC-alpha"
            print(f"[weight_adapter] {horizon}: {n} rows | IC=[{ic_str}] | model={model_status}")
    except Exception as e:
        print(f"[weight_adapter] Diagnostics error: {e}")

    print("[weight_adapter] Adaptation cycle complete.")
