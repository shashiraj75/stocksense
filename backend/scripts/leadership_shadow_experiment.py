"""
Market Leadership walk-forward shadow-experiment harness (Section 15).

Purpose: measure whether Stock Relative Strength has real forward-return
signal, using ONLY the module's own pure, deterministic functions
(services.market_leadership.relative_strength) against real historical
price data — no network calls beyond the one-time price-history fetch,
fully reproducible offline afterward from a cached fixture.

Design (CONTROL vs EXPERIMENT A degenerate form — the necessary first
building block before layering in group leadership / trend lifecycle /
breadth per Section 15's own experiment ladder):

For each as-of date in a historical walk-forward window:
  1. Slice every universe symbol's price history to that as-of date only
     (point-in-time; no future bars visible — reuses sessions.slice_as_of,
     the same function unit-tested for leakage in
     tests/regression/market_leadership/test_point_in_time_leakage.py).
  2. Compute each symbol's RS composite + universe percentile as of that
     date (relative_strength.compute_relative_return_windows +
     rank_universe — the exact production functions, not a re-derivation).
  3. Look forward `horizon_days` real trading sessions (using the SAME
     already-fetched history — future bars exist in memory but are never
     read by step 1/2, only by this step) to compute realized return.
  4. Record (rs_percentile, forward_return) pairs across all symbols and
     dates.

Metrics reported: Spearman rank correlation (percentile vs forward
return), top-decile vs bottom-decile average forward return, top-decile
hit rate (positive-return share), observation count, and a coverage
report. This is a SINGLE-FACTOR study — it does NOT yet include group
leadership, trend lifecycle, or breadth (those require their own harness
extensions once RS alone is validated, per Section 15's ladder).

IMPORTANT — this script does not itself certify anything as validated.
Section 15 requires separate India/US reporting, 3 horizons, multiple
market regimes, adequate sample size, bootstrap confidence intervals, and
walk-forward train/validation/test splits before ANY result may be
described as evidence for scoring influence. A single small run of this
script is a smoke test that the harness is functional and reproducible —
NOT a validation result. See the --min-observations gate below, which
refuses to print a correlation-based conclusion under a floor sample size
rather than let a small run masquerade as evidence.

Usage:
    python scripts/leadership_shadow_experiment.py \\
        --market US --symbols AAPL MSFT GOOGL ... \\
        --horizon-days 21 --lookback-days 500 --step-days 21

No production code path calls this script. It is an operator/analyst tool
only, run manually, writing nothing to any database.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta

import numpy as np
import pandas as pd
import yfinance as yf

sys.path.insert(0, ".")  # allow `python scripts/...` from backend/

from services.market_leadership.relative_strength import (
    compute_relative_return_windows, composite_relative_return, rank_universe,
)
from services.market_leadership.sessions import slice_as_of

# Below this many (as_of_date, symbol) observations, a correlation number
# is not printed as a headline result — Section 15 explicitly forbids
# "conclusions based on too few observations."
MIN_OBSERVATIONS_FOR_HEADLINE = 300

BENCHMARK_TICKER = {"IN": "^NSEI", "US": "^GSPC"}


def _fetch_history(symbol: str, market: str, period: str) -> pd.DataFrame:
    from services.validation_engine import _resolve_yahoo_symbol
    return yf.Ticker(_resolve_yahoo_symbol(symbol, market)).history(period=period)


def _spearman(x: list[float], y: list[float]) -> float | None:
    if len(x) < 3:
        return None
    rx = pd.Series(x).rank()
    ry = pd.Series(y).rank()
    if rx.std() == 0 or ry.std() == 0:
        return None
    return float(np.corrcoef(rx, ry)[0, 1])


def run_experiment(
    market: str, symbols: list[str], horizon_days: int, lookback_days: int, step_days: int,
) -> dict:
    print(f"[harness] fetching real price history for {len(symbols)} symbols + benchmark "
          f"(market={market}, one-time network cost, then fully offline)...", file=sys.stderr)

    period = "5y" if lookback_days > 750 else "2y"
    price_history: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        try:
            price_history[sym] = _fetch_history(sym, market, period)
        except Exception as e:
            print(f"[harness] WARNING: fetch failed for {sym}: {e}", file=sys.stderr)

    benchmark_df = yf.Ticker(BENCHMARK_TICKER[market]).history(period=period)

    if not price_history:
        return {"status": "error", "reason": "no symbols fetched"}

    any_df = next(iter(price_history.values()))
    all_dates = any_df.index
    if len(all_dates) < lookback_days + horizon_days + 5:
        return {"status": "error", "reason": "insufficient history for the requested lookback/horizon"}

    as_of_candidates = all_dates[-(lookback_days + horizon_days):-horizon_days:step_days]

    observations: list[dict] = []
    for as_of_ts in as_of_candidates:
        as_of = as_of_ts.date()
        composites: dict[str, float] = {}
        for sym, df in price_history.items():
            calc = compute_relative_return_windows(df, benchmark_df, as_of)
            composite = composite_relative_return(calc["windows"])
            if composite is not None:
                composites[sym] = composite
        if len(composites) < 5:
            continue
        ranks = rank_universe(composites)

        for sym in composites:
            df = price_history[sym]
            sliced = slice_as_of(df, as_of)
            if len(sliced) == 0:
                continue
            entry_idx = df.index.get_indexer([sliced.index[-1]])[0]
            fwd_idx = entry_idx + horizon_days
            if fwd_idx >= len(df):
                continue
            entry_price = float(df["Close"].iloc[entry_idx])
            fwd_price = float(df["Close"].iloc[fwd_idx])
            if entry_price == 0 or not np.isfinite(entry_price) or not np.isfinite(fwd_price):
                continue
            fwd_return_pct = (fwd_price - entry_price) / entry_price * 100.0
            observations.append({
                "as_of": as_of.isoformat(), "symbol": sym,
                "rs_percentile": ranks[sym]["percentile"], "fwd_return_pct": fwd_return_pct,
            })

    n = len(observations)
    result = {
        "status": "ok", "market": market, "horizon_days": horizon_days,
        "n_observations": n, "n_symbols": len(symbols), "n_as_of_dates": len(as_of_candidates),
        "min_observations_for_headline": MIN_OBSERVATIONS_FOR_HEADLINE,
        "headline_suppressed": n < MIN_OBSERVATIONS_FOR_HEADLINE,
    }
    if n == 0:
        result["status"] = "no_observations"
        return result

    pctls = [o["rs_percentile"] for o in observations]
    rets = [o["fwd_return_pct"] for o in observations]
    corr = _spearman(pctls, rets)

    df_obs = pd.DataFrame(observations)
    df_obs["decile"] = pd.qcut(df_obs["rs_percentile"], 10, labels=False, duplicates="drop")
    top_decile = df_obs[df_obs["decile"] == df_obs["decile"].max()]
    bottom_decile = df_obs[df_obs["decile"] == df_obs["decile"].min()]

    result.update({
        "spearman_rs_percentile_vs_fwd_return": corr,
        "top_decile_avg_fwd_return_pct": round(float(top_decile["fwd_return_pct"].mean()), 3) if len(top_decile) else None,
        "bottom_decile_avg_fwd_return_pct": round(float(bottom_decile["fwd_return_pct"].mean()), 3) if len(bottom_decile) else None,
        "top_decile_hit_rate_pct": round(float((top_decile["fwd_return_pct"] > 0).mean() * 100), 1) if len(top_decile) else None,
        "top_decile_n": int(len(top_decile)), "bottom_decile_n": int(len(bottom_decile)),
    })
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--market", choices=["IN", "US"], required=True)
    p.add_argument("--symbols", nargs="+", required=True)
    p.add_argument("--horizon-days", type=int, default=21)
    p.add_argument("--lookback-days", type=int, default=500)
    p.add_argument("--step-days", type=int, default=21)
    p.add_argument("--out", default=None, help="Optional path to write the JSON result")
    args = p.parse_args()

    result = run_experiment(
        args.market, args.symbols, args.horizon_days, args.lookback_days, args.step_days,
    )

    if result.get("headline_suppressed"):
        print(
            f"[harness] {result.get('n_observations', 0)} observations — below the "
            f"{MIN_OBSERVATIONS_FOR_HEADLINE}-observation floor. This run is a functional "
            f"smoke test, NOT a validation result. Do not cite spearman/decile numbers below "
            f"as evidence for anything.",
            file=sys.stderr,
        )

    print(json.dumps(result, indent=2))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
