"""
2026-08-24 weight-calibration investigation, part 3 (final check).

Both the tech/rs/obv/mfi blend and the fundamental score showed no
robust, generalizable correlation with realized alpha among BUY signals
(fit_composite_weights.py, isolate_fundamental_ic.py) — re-weighting the
composite's internal composition is a dead end. This tests a different,
coarser hypothesis: that the model behaves as a FILTER, not a fine
ranker — i.e. clearing a score bar matters, but the exact level within
"cleared" does not. If true, mean alpha should step up noticeably around
the existing BUY_THRESHOLD=60 (composite >= 60 vs just below it) but stay
roughly FLAT across score bands within the BUY range (60-65 vs 75+) —
confirming the filter read and telling us whether the *threshold itself*
(60) is well-placed, too low, or too high, independent of any internal
weighting question.

Uses composite_score buckets on the SAME held-out (last 30% by date)
rows as the prior two scripts, across ALL signals regardless of their
predicted label (since predicted is a deterministic function of
composite_score) — not just those already labelled BUY — to see the
alpha trend on both sides of the existing cutoff.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/test_buy_threshold_sensitivity.py
"""
import json
import sys
import time

import numpy as np

sys.path.insert(0, ".")

from services.validation_engine import run_validation, BUY_THRESHOLD  # noqa: E402

SEGMENTS = [
    ("long", "us"),
    ("medium", "us"),
    ("long", "nifty100"),
    ("medium", "nifty100"),
]

SYMBOL, HORIZON, SIGNAL_DATE, COMPOSITE, TECH, RS, OBV, MFI, PREDICTED, \
    FWD_RET, BENCH_FWD_RET, ALPHA, ACTUAL_DIR, CORRECT = range(14)

BUCKETS = [(40, 50), (50, 55), (55, 60), (60, 65), (65, 70), (70, 75), (75, 100)]


def _bucket_stats(rows: list[tuple], lo: float, hi: float) -> dict:
    vals = [r[ALPHA] for r in rows if lo <= r[COMPOSITE] < hi and r[ALPHA] is not None]
    n = len(vals)
    if n < 2:
        return {"n": n, "mean_alpha": None, "t_stat": None}
    mean = float(np.mean(vals))
    sd = float(np.std(vals, ddof=1))
    t = (mean / sd * (n ** 0.5)) if sd else None
    return {"n": n, "mean_alpha": round(mean, 4), "t_stat": round(t, 2) if t is not None else None}


def main():
    results = {}
    for horizon, universe in SEGMENTS:
        key = f"{universe}_{horizon}"
        thr = BUY_THRESHOLD[horizon]
        print(f"=== {key} (BUY_THRESHOLD={thr}) ===", flush=True)
        start = time.monotonic()
        try:
            metrics = run_validation(horizon=horizon, universe=universe, _persist=False, max_workers=6)
        except Exception as e:
            print(f"FAILED {key}: {e}", flush=True)
            results[key] = {"error": str(e)}
            continue
        elapsed = time.monotonic() - start
        rows = (metrics.get("_persist_payload") or {}).get("signal_rows") or []
        dated = sorted(rows, key=lambda r: r[SIGNAL_DATE])
        test = dated[int(len(dated) * 0.7):]

        seg_result = {"elapsed_seconds": round(elapsed, 1), "buy_threshold": thr, "buckets": {}}
        for lo, hi in BUCKETS:
            seg_result["buckets"][f"{lo}-{hi}"] = _bucket_stats(test, lo, hi)
        results[key] = seg_result
        print(json.dumps(seg_result, indent=2), flush=True)

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/test_buy_threshold_sensitivity_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
