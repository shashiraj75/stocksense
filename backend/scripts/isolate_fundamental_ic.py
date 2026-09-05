"""
2026-08-24 weight-calibration investigation, part 2.

The tech/rs/obv/mfi blend showed near-zero-to-negative, non-generalizing
correlation with realized alpha (fit_composite_weights.py) — re-weighting
those four sub-factors is a dead end. This isolates whether the
FUNDAMENTAL score (45% of the composite, not persisted as its own
column) is where the real BUY edge actually lives.

fund_score is recoverable algebraically:
    composite = tech_blend * 0.55 + fund_score * 0.45 + regime_adj
    => fund_score ~= (composite - tech_blend * 0.55) / 0.45

regime_adj is a small, date-level (not stock-level) adjustment in
{-5, 0, +5} (services/validation_engine.py ~line 1720) — ignored here as
bounded noise (contributes at most ~11 points after dividing by 0.45),
which attenuates but does not bias the sign of a correlation computed
across thousands of signals.

Reports, per segment, on the SAME held-out (last 30% by date) rows used
in fit_composite_weights.py: tech_blend's IC vs implied fund_score's IC
against realized alpha, for BUY signals only.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/isolate_fundamental_ic.py
"""
import json
import sys
import time

import numpy as np

sys.path.insert(0, ".")

from services.validation_engine import run_validation  # noqa: E402

SEGMENTS = [
    ("long", "us"),
    ("medium", "us"),
    ("long", "nifty100"),
    ("medium", "nifty100"),
]

SYMBOL, HORIZON, SIGNAL_DATE, COMPOSITE, TECH, RS, OBV, MFI, PREDICTED, \
    FWD_RET, BENCH_FWD_RET, ALPHA, ACTUAL_DIR, CORRECT = range(14)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def main():
    results = {}
    for horizon, universe in SEGMENTS:
        key = f"{universe}_{horizon}"
        print(f"=== {key} ===", flush=True)
        start = time.monotonic()
        try:
            metrics = run_validation(horizon=horizon, universe=universe, _persist=False, max_workers=6)
        except Exception as e:
            print(f"FAILED {key}: {e}", flush=True)
            results[key] = {"error": str(e)}
            continue
        elapsed = time.monotonic() - start
        rows = (metrics.get("_persist_payload") or {}).get("signal_rows") or []
        # Same time-based 70/30 split convention as fit_composite_weights.py
        dated = sorted(rows, key=lambda r: r[SIGNAL_DATE])
        test = dated[int(len(dated) * 0.7):]

        tech_blends, implied_funds, composites, alphas = [], [], [], []
        for r in test:
            if r[PREDICTED] != "BUY" or r[ALPHA] is None:
                continue
            tb = r[TECH] * 0.30 + r[RS] * 0.30 + r[OBV] * 0.20 + r[MFI] * 0.20
            implied_fund = (r[COMPOSITE] - tb * 0.55) / 0.45
            tech_blends.append(tb)
            implied_funds.append(implied_fund)
            composites.append(r[COMPOSITE])
            alphas.append(r[ALPHA])

        alphas_arr = np.array(alphas)
        seg_result = {
            "elapsed_seconds": round(elapsed, 1),
            "n_test_buy": len(alphas),
            "tech_blend_ic": round(_corr(np.array(tech_blends), alphas_arr), 4),
            "implied_fund_score_ic": round(_corr(np.array(implied_funds), alphas_arr), 4),
            "full_composite_ic": round(_corr(np.array(composites), alphas_arr), 4),
            "implied_fund_score_range": [round(min(implied_funds), 1), round(max(implied_funds), 1)] if implied_funds else None,
        }
        results[key] = seg_result
        print(json.dumps(seg_result, indent=2), flush=True)

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/isolate_fundamental_ic_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
