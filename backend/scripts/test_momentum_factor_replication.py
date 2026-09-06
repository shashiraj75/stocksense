"""
2026-08-24 momentum factor replication (follow-up to test_momentum_factor.py,
which found a significant, correctly-signed IC on a broader 150-stock US
universe at medium horizon: mean_ic=+0.046, t=2.34).

Replicates across three dimensions before trusting that result:
  1. A different fixed random seed for the 150-stock US sample (rules out
     this specific draw of stocks being a lucky/unlucky one).
  2. Long and short horizons, same US universe/seed as the original find
     (momentum is known in the literature to be horizon-dependent).
  3. A broader India universe (~250 stocks sampled from stock_universe.IN_STOCKS,
     same filtering approach as the US broadening) — Nifty100 (130 stocks)
     showed nothing; this tests whether the same universe-size effect found
     for the US applies to India too.

Same methodology throughout: run_validation(_persist=False) for
signal_date/alpha_pct, one extra price-history fetch per symbol for
momentum, Fama-MacBeth (per-date Spearman IC, then t-test on the daily
IC series) — see test_momentum_factor.py's docstring for full rationale.

Manual, standalone invocation — not wired into any scheduled job:
    python scripts/test_momentum_factor_replication.py
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

SYMBOL, HORIZON, SIGNAL_DATE, COMPOSITE, TECH, RS, OBV, MFI, PREDICTED, \
    FWD_RET, BENCH_FWD_RET, ALPHA, ACTUAL_DIR, CORRECT = range(14)

MOM_LOOKBACK_DAYS = 252
MOM_SKIP_DAYS = 21
MIN_STOCKS_PER_DATE = 5


def _build_broader_universe(stock_list, n, seed):
    import random
    import re
    blocklist = re.compile(
        r"ETF|Fund|Trust|Index|Shares|Notes|\bBond\b|iShares|SPDR|Depositary|"
        r"Preferred|Warrant|Acquisition Corp|Limited",  # "Limited" alone is too broad for IN names, see below
        re.I,
    )
    filtered = [t for t, name in stock_list if re.match(r"^[A-Z0-9]{1,15}$", t)]
    rng = random.Random(seed)
    return sorted(rng.sample(filtered, min(n, len(filtered))))


def _build_broader_us_universe(n=150, seed=42):
    from services.stock_universe import US_STOCKS
    import re
    blocklist = re.compile(
        r"ETF|Fund|Trust|Index|Shares|Notes|\bBond\b|iShares|SPDR|Depositary|"
        r"Preferred|Warrant|Acquisition Corp",
        re.I,
    )
    filtered = [t for t, name in US_STOCKS if not blocklist.search(name) and re.match(r"^[A-Z]{1,5}$", t)]
    import random
    rng = random.Random(seed)
    return sorted(rng.sample(filtered, min(n, len(filtered))))


def _build_broader_in_universe(n=250, seed=42):
    from services.stock_universe import IN_STOCKS
    import re
    import random
    # IN_STOCKS tickers are bare NSE symbols (letters/digits/&), no
    # exchange suffix — _resolve_yahoo_symbol appends ".NS". No ETF-name
    # blocklist applied (India ETF names don't reliably contain "ETF" in
    # this dataset's company-name field the same way US ones do) — this
    # sample may include some non-common-equity instruments; treated as
    # bounded noise for this exploratory replication, not a production
    # universe definition.
    filtered = [t for t, _name in IN_STOCKS if re.match(r"^[A-Z0-9&]{1,15}$", t)]
    rng = random.Random(seed)
    return sorted(rng.sample(filtered, min(n, len(filtered))))


def _fetch_close_series(symbol, market, period):
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


def _momentum_at(close, signal_date):
    ts = pd.Timestamp(signal_date)
    pos = close.index.searchsorted(ts, side="right") - 1
    if pos < MOM_LOOKBACK_DAYS:
        return None
    p_recent = close.iloc[pos - MOM_SKIP_DAYS]
    p_old = close.iloc[pos - MOM_LOOKBACK_DAYS]
    if p_old <= 0 or not np.isfinite(p_recent) or not np.isfinite(p_old):
        return None
    return (p_recent / p_old - 1.0) * 100.0


def _fm_stat(daily_ics):
    n = len(daily_ics)
    if n < 2:
        return {"n_dates": n, "mean_ic": None, "t_stat": None}
    arr = np.array(daily_ics)
    mean = float(np.mean(arr))
    sd = float(np.std(arr, ddof=1))
    t = (mean / sd * (n ** 0.5)) if sd else None
    return {"n_dates": n, "mean_ic": round(mean, 4), "t_stat": round(t, 2) if t is not None else None}


def _run_segment(horizon, universe, market):
    start = time.monotonic()
    metrics = run_validation(horizon=horizon, universe=universe, _persist=False, max_workers=6)
    rows = [r for r in ((metrics.get("_persist_payload") or {}).get("signal_rows") or []) if r[ALPHA] is not None]
    symbols_needed = sorted({r[SYMBOL] for r in rows})
    print(f"  fetching price history for {len(symbols_needed)} symbols...", flush=True)
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
    original_nifty100 = ve.NIFTY_100
    results = {}

    plan = [
        ("us_medium_seed123", "medium", "us", "US", lambda: _build_broader_us_universe(150, seed=123)),
        ("us_long_seed42", "long", "us", "US", lambda: _build_broader_us_universe(150, seed=42)),
        ("us_short_seed42", "short", "us", "US", lambda: _build_broader_us_universe(150, seed=42)),
        ("nifty100_medium_broader250", "medium", "nifty100", "IN", lambda: _build_broader_in_universe(250, seed=42)),
    ]

    try:
        for key, horizon, universe, market, universe_builder in plan:
            print(f"=== {key} ===", flush=True)
            custom_universe = universe_builder()
            if universe == "us":
                ve.US_BASKET = custom_universe
            else:
                ve.NIFTY_100 = custom_universe
            print(f"  test universe size: {len(custom_universe)}", flush=True)
            try:
                seg_result = _run_segment(horizon, universe, market)
            except Exception as e:
                print(f"FAILED {key}: {e}", flush=True)
                results[key] = {"error": str(e)}
                continue
            finally:
                ve.US_BASKET = original_us_basket
                ve.NIFTY_100 = original_nifty100
            results[key] = seg_result
            print(json.dumps(seg_result, indent=2), flush=True)
    finally:
        ve.US_BASKET = original_us_basket
        ve.NIFTY_100 = original_nifty100

    print("=== FINAL RESULTS ===", flush=True)
    print(json.dumps(results, indent=2), flush=True)
    with open("scripts/test_momentum_factor_replication_results.json", "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
