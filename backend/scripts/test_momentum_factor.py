"""
2026-08-24 stock-picking methodology rebuild, step 1: test true 12-1
month momentum (Jegadeesh & Titman 1993) — the single most robust,
widely-replicated cross-sectional equity factor in the academic
literature — using the CORRECT Fama-MacBeth date-neutralized IC test
(see fama_macbeth_ic_test.py, which found the current technical
composite has ~zero genuine cross-sectional stock-picking skill).

Momentum, standard definition: cumulative return from 12 months ago to
1 month ago (the most recent month is deliberately excluded — it is
well-documented to show short-term REVERSAL, the opposite sign of the
12-1 momentum effect, and including it would net the two effects
against each other). Approximated here in trading days: ~252 trading
days ago to ~21 trading days ago.

Reuses run_validation's own alpha computation (already validated,
handles point-in-time exit windows and benchmark subtraction correctly)
for the signal_date/alpha_pct pairs — this script computes ONLY the new
momentum factor value at each signal_date, added independently via a
single extra price-history fetch per symbol (not threaded through the
existing backtest loop, to keep this a clean, standalone research
check before any code is touched).

2026-08-24 broader-universe re-test: the original 42-stock US_BASKET
(mega-cap only) showed a directionally-correct but not-yet-significant
momentum IC (t=1.33) — plausibly underpowered by both small n and being
the most efficiently-priced, most-arbitraged segment of the market,
where momentum is historically weakest. This expands the US test
universe to a broader, still-liquid sample: filter
stock_universe.US_STOCKS (12,275 raw entries, mostly ETFs/funds/SPACs)
down to plausible common stocks via a name-keyword blocklist and a
1-5-letter ticker shape, then take a fixed-seed random sample — not a
perfectly curated index constituent list, but an honest, reproducible
expansion given the time available for this pass. run_validation has no
parameter to pass a custom symbol list — it reads the module-level
US_BASKET global directly (a plain name lookup at call time, not a
bound default argument) — so this monkey-patches that global for the
duration of this standalone script's own run only, restored in a
finally block.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/test_momentum_factor.py
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
from services.validation_engine import (  # noqa: E402
    run_validation, NIFTY_100, HORIZON_PERIOD, _resolve_yahoo_symbol,
)

SEGMENTS = [
    ("medium", "us"),
    # nifty100 (130 stocks) is already a broad universe and was already
    # tested at this size (flat, t=0.02) — not re-run here since this
    # pass specifically tests whether the 42-stock US_BASKET's small size
    # explains its own inconclusive result.
]


def _build_broader_us_universe(n: int = 150, seed: int = 42) -> list[str]:
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


UNIVERSE_SYMBOLS = {
    "us": _build_broader_us_universe(),
    "nifty100": NIFTY_100,
}
UNIVERSE_MARKET_LOCAL = {"us": "US", "nifty100": "IN"}

SYMBOL, HORIZON, SIGNAL_DATE, COMPOSITE, TECH, RS, OBV, MFI, PREDICTED, \
    FWD_RET, BENCH_FWD_RET, ALPHA, ACTUAL_DIR, CORRECT = range(14)

MOM_LOOKBACK_DAYS = 252   # ~12 months of trading days
MOM_SKIP_DAYS = 21        # ~1 month excluded (short-term reversal)
MIN_STOCKS_PER_DATE = 5


def _fetch_close_series(symbol: str, market: str, period: str) -> pd.Series | None:
    yf_sym = _resolve_yahoo_symbol(symbol, market)
    try:
        df = yf.Ticker(yf_sym).history(period=period, auto_adjust=True)
    except Exception as e:
        print(f"  fetch failed {yf_sym}: {e}", flush=True)
        return None
    if df is None or df.empty or "Close" not in df.columns:
        return None
    close = df["Close"].copy()
    close.index = pd.to_datetime(close.index).tz_localize(None).normalize()
    return close


def _momentum_at(close: pd.Series, signal_date: str) -> float | None:
    ts = pd.Timestamp(signal_date)
    pos = close.index.searchsorted(ts, side="right") - 1
    if pos < MOM_LOOKBACK_DAYS:
        return None
    p_recent = close.iloc[pos - MOM_SKIP_DAYS]
    p_old = close.iloc[pos - MOM_LOOKBACK_DAYS]
    if p_old <= 0 or not np.isfinite(p_recent) or not np.isfinite(p_old):
        return None
    return (p_recent / p_old - 1.0) * 100.0


def _fm_stat(daily_ics: list[float]) -> dict:
    n = len(daily_ics)
    if n < 2:
        return {"n_dates": n, "mean_ic": None, "t_stat": None}
    arr = np.array(daily_ics)
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    t = (mean / sd * (n ** 0.5)) if sd else None
    return {"n_dates": n, "mean_ic": round(mean, 4), "t_stat": round(t, 2) if t is not None else None}


def _run_segment(horizon: str, universe: str) -> dict:
    start = time.monotonic()
    metrics = run_validation(horizon=horizon, universe=universe, _persist=False, max_workers=6)
    rows = (metrics.get("_persist_payload") or {}).get("signal_rows") or []
    rows = [r for r in rows if r[ALPHA] is not None]
    symbols_needed = sorted({r[SYMBOL] for r in rows})
    print(f"  fetching price history for {len(symbols_needed)} symbols...", flush=True)

    market = UNIVERSE_MARKET_LOCAL[universe]
    period = HORIZON_PERIOD[horizon]
    close_by_symbol = {sym: _fetch_close_series(sym, market, period) for sym in symbols_needed}

    rows_by_date = defaultdict(list)
    n_momentum_computed = 0
    for r in rows:
        close = close_by_symbol.get(r[SYMBOL])
        if close is None:
            continue
        mom = _momentum_at(close, r[SIGNAL_DATE])
        if mom is None:
            continue
        n_momentum_computed += 1
        rows_by_date[r[SIGNAL_DATE]].append((mom, r[ALPHA]))

    daily_ics = []
    for _date, pairs in rows_by_date.items():
        if len(pairs) < MIN_STOCKS_PER_DATE:
            continue
        moms = [p[0] for p in pairs]
        alphas = [p[1] for p in pairs]
        if len(set(moms)) < 2:
            continue
        rho, _p = spearmanr(moms, alphas)
        if np.isfinite(rho):
            daily_ics.append(float(rho))

    return {
        "elapsed_seconds": round(time.monotonic() - start, 1),
        "n_signal_rows_with_alpha": len(rows),
        "n_symbols": len(symbols_needed),
        "n_momentum_computed": n_momentum_computed,
        "momentum_fm": _fm_stat(daily_ics),
    }


def main():
    original_us_basket = ve.US_BASKET
    ve.US_BASKET = UNIVERSE_SYMBOLS["us"]
    print(f"Broader US test universe: {len(ve.US_BASKET)} symbols (vs {len(original_us_basket)} original)", flush=True)

    results = {}
    try:
        for horizon, universe in SEGMENTS:
            key = f"{universe}_{horizon}"
            print(f"=== {key} ===", flush=True)
            try:
                seg_result = _run_segment(horizon, universe)
            except Exception as e:
                print(f"FAILED {key}: {e}", flush=True)
                results[key] = {"error": str(e)}
                continue
            results[key] = seg_result
            print(json.dumps(seg_result, indent=2), flush=True)
    finally:
        ve.US_BASKET = original_us_basket

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/test_momentum_factor_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
