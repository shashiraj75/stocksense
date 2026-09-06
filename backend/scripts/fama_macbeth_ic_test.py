"""
2026-08-24 weight-calibration investigation, part 4 — corrected methodology.

The earlier pooled-IC tests (fit_composite_weights.py, isolate_fundamental_ic.py,
test_buy_threshold_sensitivity.py) correlated raw composite score against
raw alpha across ALL signal-dates pooled together. That conflates two
different things: genuine cross-sectional stock-picking skill (did the
model pick better stocks than worse ones ON THE SAME DATE) and
time-varying market drift (the whole market/period was up or down,
which affects every stock's alpha regardless of stock selection). A
stock scored 70 in a strong month can show higher alpha than one scored
90 in a flat month for reasons that have nothing to do with the model.

This redoes it the standard quant way (Fama-MacBeth): for EACH individual
signal_date, compute the Spearman rank correlation between composite
score and alpha WITHIN that date's cross-section of stocks only (netting
out that date's common market/period return), then average the resulting
per-date ICs across all dates, with a t-stat on the SERIES of daily ICs
(mean / (std / sqrt(n_dates))) — the standard FM significance test.

Also computed on tech_blend alone (the same recovered quantity used in
the prior scripts) so the earlier "components carry no signal" finding
can be re-checked under the corrected methodology too.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/fama_macbeth_ic_test.py
"""
import json
import sys
import time
from collections import defaultdict

import numpy as np
from scipy.stats import spearmanr

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

MIN_STOCKS_PER_DATE = 5


def _fm_stat(daily_ics: list[float]) -> dict:
    n = len(daily_ics)
    if n < 2:
        return {"n_dates": n, "mean_ic": None, "t_stat": None}
    arr = np.array(daily_ics)
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    t = (mean / sd * (n ** 0.5)) if sd else None
    return {"n_dates": n, "mean_ic": round(mean, 4), "t_stat": round(t, 2) if t is not None else None}


def _daily_ics(rows_by_date: dict, score_fn) -> list[float]:
    ics = []
    for date, rows in rows_by_date.items():
        scores, alphas = [], []
        for r in rows:
            if r[ALPHA] is None:
                continue
            scores.append(score_fn(r))
            alphas.append(r[ALPHA])
        if len(scores) < MIN_STOCKS_PER_DATE or len(set(scores)) < 2:
            continue
        rho, _p = spearmanr(scores, alphas)
        if np.isfinite(rho):
            ics.append(float(rho))
    return ics


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

        rows_by_date = defaultdict(list)
        for r in rows:
            rows_by_date[r[SIGNAL_DATE]].append(r)

        composite_ics = _daily_ics(rows_by_date, lambda r: r[COMPOSITE])
        tech_blend_ics = _daily_ics(
            rows_by_date,
            lambda r: r[TECH] * 0.30 + r[RS] * 0.30 + r[OBV] * 0.20 + r[MFI] * 0.20,
        )

        seg_result = {
            "elapsed_seconds": round(elapsed, 1),
            "n_total_dates": len(rows_by_date),
            "n_signal_rows": len(rows),
            "composite_score_fm": _fm_stat(composite_ics),
            "tech_blend_fm": _fm_stat(tech_blend_ics),
        }
        results[key] = seg_result
        print(json.dumps(seg_result, indent=2), flush=True)

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/fama_macbeth_ic_test_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
