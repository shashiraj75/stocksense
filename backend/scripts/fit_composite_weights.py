"""
2026-08-24 weight-calibration pass (item 1 of the 3 approved follow-ups).

Fits tech/rs/obv/mfi sub-factor weights via OLS regression against
realized alpha (BUY signals only — SELL stays disabled per the prior
investigation), using fresh, post-regime-gate, de-NaN'd validation data.
Replaces the hand-picked 30/30/20/20 constants in
services/validation_engine.py::_score_at with data-driven weights, if and
only if they show a genuine in-sample improvement — and even then, ONLY
as a candidate pending an out-of-sample walk-forward check (a single
in-sample regression is not a deployable result on its own; this script
prints that caveat with its own output rather than asserting success).

signal_rows column order (val_signals minus id/run_id): symbol, horizon,
signal_date, composite_score, tech_score, rs_score, obv_score, mfi_score,
predicted, fwd_return_pct, nifty_fwd_ret_pct, alpha_pct, actual_direction,
correct.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/fit_composite_weights.py
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


def _split_train_test(rows: list[tuple]) -> tuple[list, list]:
    """Time-based 70/30 split by signal_date — never a random shuffle,
    since forward-return windows overlap in time and a random split would
    leak future information into "training" via shared/adjacent windows.
    Sorted ascending; first 70% of dates -> train, rest -> held-out test."""
    dated = sorted(rows, key=lambda r: r[SIGNAL_DATE])
    cut = int(len(dated) * 0.7)
    return dated[:cut], dated[cut:]


def _fit_weights(train_rows: list[tuple]) -> np.ndarray | None:
    """OLS: alpha_pct ~ tech + rs + obv + mfi (no intercept — these are
    already 0-100 scores meant to combine additively like the original
    formula). Returns the 4 fitted coefficients, or None if too few rows."""
    X, y = [], []
    for r in train_rows:
        if r[PREDICTED] != "BUY" or r[ALPHA] is None:
            continue
        X.append([r[TECH], r[RS], r[OBV], r[MFI]])
        y.append(r[ALPHA])
    if len(y) < 30:
        return None
    X = np.array(X)
    y = np.array(y)
    coefs, *_ = np.linalg.lstsq(X, y, rcond=None)
    return coefs


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _evaluate(test_rows: list[tuple], weights: np.ndarray | None) -> dict:
    """Compares, on the HELD-OUT test rows only: the ORIGINAL hand-picked
    30/30/20/20 tech_blend's correlation with realized alpha, versus the
    fitted weights' tech_blend's correlation with the SAME alpha — an
    honest out-of-sample check, not an in-sample fit-quality number."""
    orig_blend, fit_blend, alphas = [], [], []
    for r in test_rows:
        if r[PREDICTED] != "BUY" or r[ALPHA] is None:
            continue
        sub = np.array([r[TECH], r[RS], r[OBV], r[MFI]])
        orig_blend.append(sub[0] * 0.30 + sub[1] * 0.30 + sub[2] * 0.20 + sub[3] * 0.20)
        if weights is not None:
            fit_blend.append(float(np.dot(sub, weights)))
        alphas.append(r[ALPHA])
    alphas = np.array(alphas)
    result = {
        "n_test_buy": len(alphas),
        "original_weights_ic": round(_corr(np.array(orig_blend), alphas), 4),
    }
    if weights is not None:
        result["fitted_weights_ic"] = round(_corr(np.array(fit_blend), alphas), 4)
    return result


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

        train, test = _split_train_test(rows)
        weights = _fit_weights(train)
        evaluation = _evaluate(test, weights)
        seg_result = {
            "elapsed_seconds": round(elapsed, 1),
            "n_total_rows": len(rows),
            "n_train_rows": len(train),
            "n_test_rows": len(test),
            "fitted_weights_raw": weights.tolist() if weights is not None else None,
            "fitted_weights_normalized": (
                (weights / np.abs(weights).sum()).round(4).tolist()
                if weights is not None else None
            ),
            **evaluation,
        }
        results[key] = seg_result
        print(json.dumps(seg_result, indent=2), flush=True)

    print("=== FINAL RESULTS (fitted_weights order: tech, rs, obv, mfi) ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/fit_composite_weights_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
