"""
2026-08-24 SELL-backwards / regime-conditioning fix (PR #83) — post-fix
validation re-run.

Runs run_validation(..., _persist=False) for the segments with the
strongest pre-fix evidence of a wrong-signed SELL signal, using the
CURRENT (fixed) code on this branch, without writing to production
Postgres. `signal_rows` tuples match val_signals' column order minus
(id, run_id): symbol, horizon, signal_date, composite_score, tech_score,
rs_score, obv_score, mfi_score, predicted, fwd_return_pct,
nifty_fwd_ret_pct, alpha_pct, actual_direction, correct.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/rerun_validation_post_regime_gate.py
"""
import json
import statistics
import sys
import time

sys.path.insert(0, ".")

from services.validation_engine import run_validation  # noqa: E402

SEGMENTS = [
    ("long", "us"),
    ("medium", "us"),
    ("long", "nifty100"),
    ("medium", "nifty100"),
]

PREDICTED_IDX = 8
ALPHA_IDX = 11


def _alpha_stats(rows: list[tuple], predicted_label: str) -> dict:
    vals = [r[ALPHA_IDX] for r in rows if r[PREDICTED_IDX] == predicted_label and r[ALPHA_IDX] is not None]
    n = len(vals)
    if n < 2:
        return {"n": n, "mean_alpha": None, "t_stat": None}
    mean = statistics.mean(vals)
    sd = statistics.stdev(vals)
    t = (mean / sd * (n ** 0.5)) if sd else None
    return {"n": n, "mean_alpha": round(mean, 4), "t_stat": round(t, 2) if t is not None else None}


def main():
    results = {}
    for horizon, universe in SEGMENTS:
        key = f"{universe}_{horizon}"
        print(f"=== running horizon={horizon} universe={universe} (_persist=False) ===", flush=True)
        start = time.monotonic()
        try:
            metrics = run_validation(horizon=horizon, universe=universe, _persist=False, max_workers=6)
        except Exception as e:
            print(f"FAILED {key}: {e}", flush=True)
            results[key] = {"error": str(e)}
            continue
        elapsed = time.monotonic() - start
        rows = (metrics.get("_persist_payload") or {}).get("signal_rows") or []
        seg_result = {
            "elapsed_seconds": round(elapsed, 1),
            "n_signal_rows": len(rows),
            "BUY": _alpha_stats(rows, "BUY"),
            "SELL": _alpha_stats(rows, "SELL"),
            "top_level_buy_hit_rate_pct": metrics.get("buy_hit_rate_pct"),
            "top_level_sell_hit_rate_pct": metrics.get("sell_hit_rate_pct"),
        }
        results[key] = seg_result
        print(json.dumps(seg_result, indent=2), flush=True)

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/rerun_validation_post_regime_gate_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
