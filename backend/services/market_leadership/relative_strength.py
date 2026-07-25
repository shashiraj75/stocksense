"""
Stock Relative Strength Rank (methodology ml-rs-1.0.0).

Pure calculation functions — no I/O. Callers supply already-fetched,
as-of-sliced price DataFrames (yfinance convention: "Close"/"High"/"Low"/
"Volume" columns, DatetimeIndex). See
Documentation/Engineering-Handbook/Architecture/Market-Leadership-Trend-Context-Layer.md
§4.1 for the methodology write-up and the weight-selection rationale.
"""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from services.market_leadership.configuration import (
    RS_WINDOWS, RS_WINDOW_WEIGHTS, RS_MIN_WINDOWS, RS_REQUIRED_WINDOW,
    RS_MIN_WINDOW_COVERAGE, RS_TREND_DEADBAND_PP, RS_METHODOLOGY_VERSION,
    UNIVERSE_VERSION,
)
from services.market_leadership.contracts import (
    StockRelativeStrength, RsTrend, DataQualityStatus, ReasonCode,
)
from services.market_leadership.sessions import slice_as_of, window_coverage, drop_incomplete_bars


def _total_return(closes: pd.Series, window: int) -> float | None:
    """Total return over the trailing `window` sessions, as a percentage."""
    if closes is None or len(closes) < window + 1:
        return None
    start = float(closes.iloc[-(window + 1)])
    end = float(closes.iloc[-1])
    if start == 0 or not np.isfinite(start) or not np.isfinite(end):
        return None
    return (end - start) / start * 100.0


def compute_relative_return_windows(
    price_df: pd.DataFrame, benchmark_df: pd.DataFrame, as_of: date,
) -> dict:
    """Benchmark-relative return for each configured window, plus coverage
    and a data-quality verdict. Both inputs are as-of sliced here (callers
    must not pre-slice differently) so the leakage guarantee is centralized.
    """
    stock = drop_incomplete_bars(slice_as_of(price_df, as_of)) if price_df is not None else None
    bench = (
        drop_incomplete_bars(slice_as_of(benchmark_df, as_of))
        if benchmark_df is not None and len(benchmark_df) > 0 else None
    )

    if stock is None or len(stock) == 0:
        return {
            "windows": {k: None for k in RS_WINDOWS},
            "coverage": 0.0,
            "quality": DataQualityStatus.FAILED,
            "reason_codes": [ReasonCode.INSUFFICIENT_HISTORY.value],
        }

    stock_close = stock["Close"]
    bench_close = bench["Close"] if bench is not None and len(bench) > 0 else None

    windows: dict[str, float | None] = {}
    coverages: dict[str, float] = {}
    for key, days in RS_WINDOWS.items():
        stock_ret = _total_return(stock_close, days)
        bench_ret = _total_return(bench_close, days) if bench_close is not None else None
        coverages[key] = window_coverage(stock_close, days)
        if stock_ret is None or bench_ret is None or coverages[key] < RS_MIN_WINDOW_COVERAGE:
            windows[key] = None
        else:
            windows[key] = stock_ret - bench_ret

    reason_codes: list[str] = []
    worst_used_coverage = min(
        (coverages[k] for k in windows if windows[k] is not None), default=0.0
    )
    if not coverages.get(RS_REQUIRED_WINDOW, 0.0) >= RS_MIN_WINDOW_COVERAGE:
        reason_codes.append(ReasonCode.INSUFFICIENT_HISTORY.value)
    elif worst_used_coverage < 1.0:
        reason_codes.append(ReasonCode.MISSING_BARS.value)

    n_usable = sum(1 for v in windows.values() if v is not None)
    if windows.get(RS_REQUIRED_WINDOW) is None or n_usable < RS_MIN_WINDOWS:
        quality = DataQualityStatus.PARTIAL_HISTORY if n_usable > 0 else DataQualityStatus.FAILED
    elif worst_used_coverage < 1.0:
        quality = DataQualityStatus.PARTIAL_HISTORY
    else:
        quality = DataQualityStatus.OK

    return {
        "windows": windows,
        "coverage": round(worst_used_coverage, 4),
        "quality": quality,
        "reason_codes": reason_codes,
    }


def composite_relative_return(windows: dict[str, float | None]) -> float | None:
    """Equal-weighted composite across usable windows, renormalized to the
    usable subset — but only when >= RS_MIN_WINDOWS are usable AND the
    required (3m) window is among them. Otherwise INSUFFICIENT_DATA."""
    if windows.get(RS_REQUIRED_WINDOW) is None:
        return None
    usable = {k: v for k, v in windows.items() if v is not None}
    if len(usable) < RS_MIN_WINDOWS:
        return None
    total_weight = sum(RS_WINDOW_WEIGHTS[k] for k in usable)
    return sum(RS_WINDOW_WEIGHTS[k] * v for k, v in usable.items()) / total_weight


def rank_universe(composites: dict[str, float]) -> dict[str, dict]:
    """Percentile rank (0-100, average-rank tie handling) and integer rank
    (1 = strongest) for every symbol with a non-None composite. Symbols not
    present in `composites` are the caller's INSUFFICIENT_DATA cases and are
    never assigned a synthetic neutral score here."""
    if not composites:
        return {}
    symbols = list(composites)
    values = np.array([composites[s] for s in symbols], dtype=float)
    n = len(values)
    # average-rank percentile: pct = (rank_below + 0.5*ties) / n * 100
    order = np.argsort(values)
    ranks = np.empty(n)
    ranks[order] = np.arange(1, n + 1)
    # resolve ties to the average rank of the tied group
    sorted_vals = values[order]
    i = 0
    while i < n:
        j = i
        while j + 1 < n and sorted_vals[j + 1] == sorted_vals[i]:
            j += 1
        if j > i:
            avg_rank = (ranks[order[i:j + 1]]).mean()
            ranks[order[i:j + 1]] = avg_rank
        i = j + 1
    percentiles = (ranks - 0.5) / n * 100.0
    # integer rank 1 = strongest (descending value)
    desc_order = np.argsort(-values)
    integer_rank = np.empty(n, dtype=int)
    integer_rank[desc_order] = np.arange(1, n + 1)
    return {
        symbols[i]: {
            "percentile": round(float(percentiles[i]), 2),
            "rank": int(integer_rank[i]),
            "universe_size": n,
        }
        for i in range(n)
    }


def classify_rs_trend(recent_21d_rel_rets: list[float], prior_21d_rel_rets: list[float]) -> RsTrend:
    """IMPROVING/FLAT/WEAKENING from the shift in mean 21d relative return
    between the last 10 sessions and the prior 10 sessions, with a dead-band
    to avoid noise-driven flips."""
    if not recent_21d_rel_rets or not prior_21d_rel_rets:
        return RsTrend.INSUFFICIENT_DATA
    shift = float(np.mean(recent_21d_rel_rets)) - float(np.mean(prior_21d_rel_rets))
    if shift > RS_TREND_DEADBAND_PP:
        return RsTrend.IMPROVING
    if shift < -RS_TREND_DEADBAND_PP:
        return RsTrend.WEAKENING
    return RsTrend.FLAT


def compute_rs_acceleration(rs_1m: float | None, rs_6m: float | None) -> float | None:
    """Annualized 1m relative return minus annualized 6m relative return —
    is near-term relative performance running ahead of the medium-term
    trend? Positive = accelerating."""
    if rs_1m is None or rs_6m is None:
        return None
    ann_1m = rs_1m * (252.0 / RS_WINDOWS["rs_1m"])
    ann_6m = rs_6m * (252.0 / RS_WINDOWS["rs_6m"])
    return round(ann_1m - ann_6m, 2)


def build_stock_relative_strength(
    symbol: str, market: str, price_df: pd.DataFrame, benchmark_df: pd.DataFrame,
    as_of: date, universe_id: str, universe_rank_entry: dict | None,
    rs_trend: RsTrend = RsTrend.INSUFFICIENT_DATA,
) -> StockRelativeStrength:
    """Assemble the full contract for one symbol. `universe_rank_entry` is
    this symbol's entry from rank_universe() — None when the composite was
    None (INSUFFICIENT_DATA), never fabricated."""
    calc = compute_relative_return_windows(price_df, benchmark_df, as_of)
    windows = calc["windows"]
    composite = composite_relative_return(windows)
    reason_codes = list(calc["reason_codes"])

    score = percentile = rank = universe_size = None
    if composite is not None and universe_rank_entry is not None:
        score = int(round(universe_rank_entry["percentile"]))
        percentile = universe_rank_entry["percentile"]
        rank = universe_rank_entry["rank"]
        universe_size = universe_rank_entry["universe_size"]
    elif composite is None:
        reason_codes.append(ReasonCode.INSUFFICIENT_HISTORY.value)

    return StockRelativeStrength(
        symbol=symbol,
        market=market,
        stock_rs_score=score,
        stock_rs_percentile=percentile,
        benchmark_relative_return=None if composite is None else round(composite, 2),
        universe_relative_rank=rank,
        universe_size=universe_size,
        rs_1m=windows.get("rs_1m"),
        rs_3m=windows.get("rs_3m"),
        rs_6m=windows.get("rs_6m"),
        rs_12m=windows.get("rs_12m"),
        rs_trend=rs_trend if composite is not None else RsTrend.INSUFFICIENT_DATA,
        rs_acceleration=compute_rs_acceleration(windows.get("rs_1m"), windows.get("rs_6m")),
        data_coverage=calc["coverage"],
        data_quality_status=calc["quality"],
        calculation_as_of=as_of.isoformat(),
        universe_id=universe_id,
        universe_version=UNIVERSE_VERSION,
        methodology_version=RS_METHODOLOGY_VERSION,
        reason_codes=reason_codes,
    )
