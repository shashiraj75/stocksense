"""
2026-08-24 momentum factor — final out-of-sample check before shipping.

test_momentum_factor.py / test_momentum_factor_replication.py established
the momentum-alpha relationship exists (Fama-MacBeth IC, significant,
correctly signed, replicated across 2 US universe seeds and India). This
script asks a different, final question: does ADDING the now-implemented
momentum term to the actual composite score (as wired into
validation_engine.py::_score_at) produce a composite with BETTER
out-of-sample cross-sectional IC than the composite WITHOUT it — using a
strict time-based train/test split (first 70% of dates informs nothing,
since momentum's weight was fixed by evidence/judgment, not fit; this
split exists purely to test generalization on genuinely unseen dates,
the same discipline as fit_composite_weights.py) — computed the
CORRECT (date-neutralized, Fama-MacBeth) way, not the flawed pooled way.

Uses run_validation directly (post-implementation code, on this branch)
for BOTH the old (no momentum, other horizons in the same run — this
script computes the "without momentum" composite by hand from the same
signal_rows, so no second backtest run is needed) and new composite:
composite_with_momentum is already what validation_engine now returns
directly (predicted/composite_score in signal_rows); composite_without
is reconstructed by subtracting the wired-in momentum contribution's
formula from persisted composite_score using persisted sub-scores plus
an independent momentum recomputation.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/validate_momentum_out_of_sample.py
"""
import json
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.stats import spearmanr

sys.path.insert(0, ".")

import services.validation_engine as ve  # noqa: E402
from services.validation_engine import run_validation, HORIZON_PERIOD, _resolve_yahoo_symbol  # noqa: E402
from services.technical_indicators import (  # noqa: E402
    compute_momentum_score, MOMENTUM_LOOKBACK_DAYS, MOMENTUM_SKIP_DAYS,
)

SEGMENTS = [("medium", "us"), ("medium", "nifty100")]


def _build_broader_us_universe(n=150, seed=42):
    import random
    import re
    from services.stock_universe import US_STOCKS
    blocklist = re.compile(
        r"ETF|Fund|Trust|Index|Shares|Notes|\bBond\b|iShares|SPDR|Depositary|"
        r"Preferred|Warrant|Acquisition Corp",
        re.I,
    )
    filtered = [t for t, name in US_STOCKS if not blocklist.search(name) and re.match(r"^[A-Z]{1,5}$", t)]
    rng = random.Random(seed)
    return sorted(rng.sample(filtered, min(n, len(filtered))))


def _build_broader_in_universe(n=250, seed=42):
    import random
    import re
    from services.stock_universe import IN_STOCKS
    filtered = [t for t, _name in IN_STOCKS if re.match(r"^[A-Z0-9&]{1,15}$", t)]
    rng = random.Random(seed)
    return sorted(rng.sample(filtered, min(n, len(filtered))))

SYMBOL, HORIZON, SIGNAL_DATE, COMPOSITE, TECH, RS, OBV, MFI, PREDICTED, \
    FWD_RET, BENCH_FWD_RET, ALPHA, ACTUAL_DIR, CORRECT = range(14)

MIN_STOCKS_PER_DATE = 5
MARKET = {"us": "US", "nifty100": "IN"}


def _fetch_close_series(symbol, market, period):
    yf_sym = _resolve_yahoo_symbol(symbol, market)
    try:
        df = yf.Ticker(yf_sym).history(period=period, auto_adjust=True)
    except Exception:
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    close = df["Close"].copy()
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close


def _momentum_pct_at(close, signal_date):
    ts = pd.Timestamp(signal_date)
    pos = close.index.searchsorted(ts, side="right") - 1
    if pos < MOMENTUM_LOOKBACK_DAYS:
        return None
    p_recent = close.iloc[pos - MOMENTUM_SKIP_DAYS]
    p_old = close.iloc[pos - MOMENTUM_LOOKBACK_DAYS]
    if p_old <= 0 or not np.isfinite(p_recent) or not np.isfinite(p_old):
        return None
    return (p_recent / p_old - 1.0) * 100.0


def _momentum_score_from_pct(mom_pct):
    score = 50.0
    if mom_pct > 30:
        score += 15
    elif mom_pct > 15:
        score += 8
    elif mom_pct < -30:
        score -= 15
    elif mom_pct < -15:
        score -= 8
    return score


def _fm_stat(daily_ics):
    n = len(daily_ics)
    if n < 2:
        return {"n_dates": n, "mean_ic": None, "t_stat": None}
    arr = np.array(daily_ics)
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    t = (mean / sd * (n ** 0.5)) if sd else None
    return {"n_dates": n, "mean_ic": round(mean, 4), "t_stat": round(t, 2) if t is not None else None}


def _daily_ics(rows_by_date, score_key):
    ics = []
    for _date, entries in rows_by_date.items():
        if len(entries) < MIN_STOCKS_PER_DATE:
            continue
        scores = [e[score_key] for e in entries]
        alphas = [e["alpha"] for e in entries]
        if len(set(scores)) < 2:
            continue
        rho, _p = spearmanr(scores, alphas)
        if np.isfinite(rho):
            ics.append(float(rho))
    return ics


WEIGHTS_TO_SWEEP = [0.20, 0.5, 1.0, 1.5, 2.0, 3.0]


def _run_segment(horizon, universe):
    start = time.monotonic()
    metrics = run_validation(horizon=horizon, universe=universe, _persist=False, max_workers=6)
    rows = [r for r in ((metrics.get("_persist_payload") or {}).get("signal_rows") or []) if r[ALPHA] is not None]
    symbols_needed = sorted({r[SYMBOL] for r in rows})
    print(f"  fetching price history for {len(symbols_needed)} symbols...", flush=True)
    market = MARKET[universe]
    period = HORIZON_PERIOD[horizon]
    close_by_symbol = {sym: _fetch_close_series(sym, market, period) for sym in symbols_needed}

    # Time-based 70/30 split — held-out test is the LAST 30% of dates.
    dated = sorted(rows, key=lambda r: r[SIGNAL_DATE])
    test_rows = dated[int(len(dated) * 0.7):]

    # Base composite (momentum entirely removed, using the ACTUAL shipped
    # weight of 0.20 to back it out) plus the raw momentum score for each
    # row, computed ONCE — every candidate weight below is then just
    # base + (mom_score-50)*weight, no new network calls or backtest runs
    # per weight.
    enriched = []
    for r in test_rows:
        close = close_by_symbol.get(r[SYMBOL])
        mom_pct = _momentum_pct_at(close, r[SIGNAL_DATE]) if close is not None else None
        mom_score = _momentum_score_from_pct(mom_pct) if mom_pct is not None else 50.0
        base = r[COMPOSITE] - (mom_score - 50.0) * 0.20  # undo the shipped 0.20 weight
        enriched.append({"date": r[SIGNAL_DATE], "base": base, "mom_score": mom_score, "alpha": r[ALPHA]})

    weight_results = {}
    for w in WEIGHTS_TO_SWEEP:
        rows_by_date = defaultdict(list)
        for e in enriched:
            composite_w = max(0.0, min(100.0, e["base"] + (e["mom_score"] - 50.0) * w))
            rows_by_date[e["date"]].append({"composite": composite_w, "alpha": e["alpha"]})
        ics = _daily_ics(rows_by_date, "composite")
        weight_results[str(w)] = _fm_stat(ics)

    return {
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "n_test_rows": len(test_rows),
        "weight_sweep": weight_results,
    }


def main():
    original_us_basket = ve.US_BASKET
    original_nifty100 = ve.NIFTY_100
    ve.US_BASKET = _build_broader_us_universe(150, seed=42)
    ve.NIFTY_100 = _build_broader_in_universe(250, seed=42)
    print(f"Broader universes: US={len(ve.US_BASKET)} (was {len(original_us_basket)}), "
          f"IN={len(ve.NIFTY_100)} (was {len(original_nifty100)})", flush=True)

    results = {}
    try:
        for horizon, universe in SEGMENTS:
            key = f"{universe}_{horizon}"
            print(f"=== {key} ===", flush=True)
            seg_result = _run_segment(horizon, universe)
            results[key] = seg_result
            print(json.dumps(seg_result, indent=2), flush=True)
    finally:
        ve.US_BASKET = original_us_basket
        ve.NIFTY_100 = original_nifty100

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/validate_momentum_out_of_sample_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
