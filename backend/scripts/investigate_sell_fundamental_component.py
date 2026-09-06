"""
2026-08-24 SELL-backwards investigation, part 2.

The regime-conditioning fix (PR #83) reduced but did not eliminate SELL's
wrong-signed alpha (see rerun_validation_post_regime_gate.py's results —
SELL alpha stayed positive in all 4 re-tested segments). This script
isolates whether the FUNDAMENTAL score component (45% of the composite,
services/validation_engine.py::_score_at line ~1369) is the remaining
driver, using data already available per signal without any new capture:

  composite_score = tech_blend * 0.55 + fund_score * 0.45 + regime_adj
  tech_blend       = tech*0.30 + rs*0.30 + obv*0.20 + mfi*0.20

tech_blend is directly recoverable from the persisted tech/rs/obv/mfi
sub-scores. Comparing tech_blend against the SELL threshold (45) in
isolation tells us: would a SELL call have fired on technicals ALONE,
without any fundamental input? If SELL signals where tech_blend alone was
NOT bearish (>=45, i.e. the fundamental blend is what dragged composite
below 45) show WORSE (more positive/more wrong) alpha than SELL signals
where tech_blend alone was already bearish, that implicates the
fundamental component specifically.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/investigate_sell_fundamental_component.py
"""
import json
import statistics
import sys
import time

sys.path.insert(0, ".")

from services.validation_engine import run_validation, SELL_THRESHOLD  # noqa: E402

SEGMENTS = [
    ("long", "us"),
    ("medium", "us"),
    ("long", "nifty100"),
    ("medium", "nifty100"),
]

# signal_rows column order (val_signals minus id/run_id):
SYMBOL, HORIZON, SIGNAL_DATE, COMPOSITE, TECH, RS, OBV, MFI, PREDICTED, \
    FWD_RET, BENCH_FWD_RET, ALPHA, ACTUAL_DIR, CORRECT = range(14)


def _alpha_stats(vals: list[float]) -> dict:
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
        sell_thr = SELL_THRESHOLD[horizon]
        print(f"=== {key} (SELL_THRESHOLD={sell_thr}) ===", flush=True)
        start = time.monotonic()
        try:
            metrics = run_validation(horizon=horizon, universe=universe, _persist=False, max_workers=6)
        except Exception as e:
            print(f"FAILED {key}: {e}", flush=True)
            results[key] = {"error": str(e)}
            continue
        elapsed = time.monotonic() - start
        rows = (metrics.get("_persist_payload") or {}).get("signal_rows") or []

        fund_driven_alphas = []   # tech_blend alone would NOT have been SELL
        tech_driven_alphas = []   # tech_blend alone WOULD already have been SELL
        for r in rows:
            if r[PREDICTED] != "SELL" or r[ALPHA] is None:
                continue
            tech_blend = r[TECH] * 0.30 + r[RS] * 0.30 + r[OBV] * 0.20 + r[MFI] * 0.20
            if tech_blend >= sell_thr:
                fund_driven_alphas.append(r[ALPHA])
            else:
                tech_driven_alphas.append(r[ALPHA])

        seg_result = {
            "elapsed_seconds": round(elapsed, 1),
            "n_sell_total": len(fund_driven_alphas) + len(tech_driven_alphas),
            "fund_driven_sell": _alpha_stats(fund_driven_alphas),
            "tech_driven_sell": _alpha_stats(tech_driven_alphas),
        }
        results[key] = seg_result
        print(json.dumps(seg_result, indent=2), flush=True)

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/investigate_sell_fundamental_component_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
