"""
Conviction-gate walk-forward backtest.

Validates the ACTUAL Daily Picks publication gate field — `confidence`
(persisted per-candidate on `alpha_observations.signal_confidence`,
`item.get("confidence")` in `services/daily_picks.py`) — against realized
forward returns. This is deliberately NOT `val_signals.composite_score`
(validation_engine.py's `_score_at` proxy, which structurally tops out
around 82 and can never reach the real 85.0 gate threshold) and NOT the
separate `confidence_model`/`_confidence_engine` 5-factor heuristic. See
Documentation/Engineering-Handbook/Daily-Picks/DAILY-PICKS-IMPLEMENTATION-REGISTER.md
DP-034/DP-035 for the gate's own semantics evidence.

Two layers, deliberately separated so the statistics are unit-testable
without a database or network connection:

1. Pure functions (`bucket_for_confidence`, `compute_bucket_stats`) — take a
   plain list of {"confidence": float, "realized_return_pct": float} dicts
   and produce win-rate / average-return / sample-size-adequacy per bucket.
   Covered by synthetic-data tests in
   backend/tests/unit/test_conviction_gate_backtest.py.

2. I/O functions (`fetch_observations`, `resolve_realized_return`,
   `run_backtest`) — read `alpha_observations` (Postgres) and compute a
   forward return via yfinance, mirroring the trading-day-window logic
   already used by `services/alpha_engine/outcome_logger.py` for the
   unrelated legacy `predictions`/`outcomes` tables. `alpha_observations`
   itself stores no realized outcome for any row — nothing resolves one —
   so this is computed live, read-only, on every invocation. No write path
   exists in this module; it never persists anything back to
   `alpha_observations` or any other table.

Usage (manual, not wired into any scheduled job):
    python backend/scripts/conviction_gate_backtest.py --market IN --horizon short
    python backend/scripts/conviction_gate_backtest.py --market US --horizon medium --limit 200
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import sys
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Mirrors services/thresholds.py DailyPicksPublicationThresholds without
# importing it, so this script has no import-time dependency on the
# production package layout (kept import-light for standalone invocation
# and for unit tests that never touch backend/services at all).
MIN_CONVICTION_TO_PUBLISH = 85.0

# Bucket edges chosen around the real gate: below the existing
# `_passes_quality_gate` hard floor (25), the pre-existing short-term
# "priority" cutoff (80), the publication gate itself (85), and a
# saturation bucket (95+) since confidence is observed to pile up at 100.
BUCKET_EDGES: list[tuple[float, float, str]] = [
    (0.0, 25.0, "0-24 (below quality gate)"),
    (25.0, 50.0, "25-49"),
    (50.0, 70.0, "50-69"),
    (70.0, 80.0, "70-79"),
    (80.0, 85.0, "80-84 (below publication gate)"),
    (85.0, 95.0, "85-94 (publication gate)"),
    (95.0, 100.0 + 1e-9, "95-100 (publication gate)"),
]

# A bucket's win-rate/avg-return is reported but flagged inadequate below
# this sample size — never presented as a meaningful result on its own.
MIN_SAMPLE_SIZE = 30

# Mirrors outcome_logger.HORIZON_CONFIG's trading-day windows and minimum
# calendar-day wait, applied here to alpha_observations' reference_price
# instead of the legacy predictions table.
HORIZON_TRADING_DAYS = {"short": 5, "medium": 20, "long": 60}
HORIZON_MIN_CALENDAR_DAYS = {"short": 3, "medium": 30, "long": 90}

MARKETS = ("IN", "US")
HORIZONS = ("short", "medium", "long")
_TICKER_SUFFIX = {"IN": ".NS", "US": ""}


# --------------------------------------------------------------------------
# Pure statistics layer — no I/O, fully unit-testable on synthetic data.
# --------------------------------------------------------------------------


def bucket_for_confidence(confidence: float) -> str | None:
    """Return the bucket label for a confidence value, or None if it's not
    a finite value in [0, 100] (fails closed — never silently mis-buckets
    an invalid value into a real bucket)."""
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return None
    if c != c or c in (float("inf"), float("-inf")):  # NaN/±Infinity
        return None
    if c < 0.0 or c > 100.0:
        return None
    for lo, hi, label in BUCKET_EDGES:
        if lo <= c < hi:
            return label
    return None


@dataclass
class BucketStats:
    label: str
    n: int
    n_wins: int
    win_rate: float | None
    avg_return_pct: float | None
    adequate_sample: bool


def compute_bucket_stats(
    observations: list[dict],
    min_sample_size: int = MIN_SAMPLE_SIZE,
) -> dict[str, BucketStats]:
    """
    Bucket `observations` (each a dict with numeric "confidence" and
    "realized_return_pct" keys) by confidence and compute win rate (share
    of rows with realized_return_pct > 0) and average realized return per
    bucket.

    A "win" is a strictly positive realized return — a flat 0.0% return is
    not counted as a win (matches the surrounding codebase's convention of
    never treating a missing/neutral outcome as a fabricated success, see
    the Validation Benchmark Evidence Integrity work in Current-Release-
    Status.md).

    Rows with a non-bucketable confidence (see `bucket_for_confidence`) or
    a non-finite realized_return_pct are silently excluded from every
    bucket's statistics — never counted as either a win or a loss.

    A bucket with fewer than `min_sample_size` observations still reports
    its (possibly noisy) win_rate/avg_return_pct, but `adequate_sample` is
    False — callers must not present an inadequate bucket's numbers as a
    meaningful finding.
    """
    grouped: dict[str, list[float]] = {label: [] for _, _, label in BUCKET_EDGES}
    for obs in observations:
        label = bucket_for_confidence(obs.get("confidence"))
        if label is None:
            continue
        ret = obs.get("realized_return_pct")
        try:
            ret_f = float(ret)
        except (TypeError, ValueError):
            continue
        if ret_f != ret_f or ret_f in (float("inf"), float("-inf")):
            continue
        grouped[label].append(ret_f)

    results: dict[str, BucketStats] = {}
    for _, _, label in BUCKET_EDGES:
        returns = grouped[label]
        n = len(returns)
        if n == 0:
            results[label] = BucketStats(label, 0, 0, None, None, False)
            continue
        n_wins = sum(1 for r in returns if r > 0.0)
        results[label] = BucketStats(
            label=label,
            n=n,
            n_wins=n_wins,
            win_rate=n_wins / n,
            avg_return_pct=sum(returns) / n,
            adequate_sample=n >= min_sample_size,
        )
    return results


def format_report(stats_by_horizon: dict[str, dict[str, BucketStats]]) -> str:
    """Render a plain-text summary table, honestly labeling inadequate
    sample sizes rather than omitting or silently including them."""
    lines: list[str] = []
    for horizon, stats in stats_by_horizon.items():
        lines.append(f"\n=== {horizon.upper()} horizon ===")
        lines.append(f"{'bucket':<32}{'n':>6}{'win_rate':>10}{'avg_ret%':>10}  adequate")
        for _, _, label in BUCKET_EDGES:
            s = stats.get(label)
            if s is None or s.n == 0:
                lines.append(f"{label:<32}{0:>6}{'n/a':>10}{'n/a':>10}  NO (n=0)")
                continue
            wr = f"{s.win_rate * 100:.1f}%"
            ar = f"{s.avg_return_pct:.2f}"
            flag = "yes" if s.adequate_sample else f"NO (n<{MIN_SAMPLE_SIZE})"
            lines.append(f"{label:<32}{s.n:>6}{wr:>10}{ar:>10}  {flag}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# I/O layer — Postgres read + yfinance forward-return lookup. Deliberately
# thin; every decision that needs to be unit-testable lives above this line.
# --------------------------------------------------------------------------


def fetch_observations(conn, market: str, horizon: str, limit: int | None = None) -> list[dict]:
    """Read-only fetch of every (market, horizon) alpha_observations row
    with a numeric signal_confidence, oldest first (so a --limit cap during
    manual/testing invocation samples the earliest, most likely to be
    resolvable rows first, not an arbitrary DB-order slice)."""
    sql = """
        SELECT symbol, run_session_date, reference_session_date,
               reference_price, signal_confidence
        FROM alpha_observations
        WHERE market = %s AND horizon = %s AND signal_confidence IS NOT NULL
        ORDER BY reference_session_date ASC
    """
    if limit:
        sql += " LIMIT %s"
    with conn.cursor() as cur:
        cur.execute(sql, (market, horizon, limit) if limit else (market, horizon))
        cols = [c.name for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def _to_date(value) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))


def resolve_realized_return(
    symbol: str,
    market: str,
    reference_session_date,
    horizon: str,
    *,
    today: _dt.date | None = None,
) -> float | None:
    """
    Compute the realized forward return for one alpha_observations row,
    entering at the first available close on/after reference_session_date
    and exiting `HORIZON_TRADING_DAYS[horizon]` trading days later —
    mirroring outcome_logger._fetch_return's methodology exactly (same
    trading-day window, same "never log a partial window" rule) but applied
    to alpha_observations rows rather than the legacy predictions table,
    since alpha_observations resolves nothing on its own.

    Returns None (never a fabricated 0.0) if:
      - fewer than HORIZON_MIN_CALENDAR_DAYS[horizon] calendar days have
        elapsed since reference_session_date (window can't possibly have
        completed yet — this is the common case for `long` given the
        gate's own data only goes back to 2026-07-17), or
      - yfinance has no data at or after reference_session_date, or the
        full trading-day window hasn't elapsed in the fetched history, or
      - the fetch itself raises (delisted symbol, provider error, etc).
    """
    ref_date = _to_date(reference_session_date)
    today = today or _dt.date.today()
    min_days = HORIZON_MIN_CALENDAR_DAYS.get(horizon)
    trading_days = HORIZON_TRADING_DAYS.get(horizon)
    if min_days is None or trading_days is None:
        return None
    if (today - ref_date).days < min_days:
        return None

    try:
        import yfinance as yf
        from pandas import Timestamp

        yf_symbol = symbol + _TICKER_SUFFIX.get(market, "")
        ticker = yf.Ticker(yf_symbol)
        hist = ticker.history(start=ref_date.isoformat(), period="6mo")
        if hist.empty or len(hist) < 2:
            return None
        hist.index = hist.index.tz_localize(None)
        ref_ts = Timestamp(ref_date)
        avail = hist.index[hist.index >= ref_ts]
        if len(avail) == 0:
            return None
        entry_price = float(hist.loc[avail[0], "Close"])
        future_rows = hist.index[hist.index >= avail[0]]
        if len(future_rows) <= trading_days:
            return None
        exit_price = float(hist.loc[future_rows[trading_days], "Close"])
        if entry_price == 0:
            return None
        return round((exit_price - entry_price) / entry_price * 100, 4)
    except Exception as e:  # noqa: BLE001 — a single symbol's fetch failure
        # must not abort the whole backtest run.
        log.debug(f"[conviction_gate_backtest] resolve failed for {symbol}: {e}")
        return None


def run_backtest(market: str, horizon: str, *, limit: int | None = None) -> dict[str, BucketStats]:
    """End-to-end: fetch rows from alpha_observations, resolve each row's
    realized return via yfinance, bucket and compute statistics. Read-only
    throughout — no write path exists anywhere in this module."""
    import os

    import psycopg

    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set — cannot run against Postgres")

    with psycopg.connect(database_url) as conn:
        rows = fetch_observations(conn, market, horizon, limit=limit)

    observations = []
    skipped_unresolved = 0
    for row in rows:
        ret = resolve_realized_return(
            row["symbol"], market, row["reference_session_date"], horizon
        )
        if ret is None:
            skipped_unresolved += 1
            continue
        observations.append({
            "confidence": row["signal_confidence"],
            "realized_return_pct": ret,
        })

    log.info(
        f"[conviction_gate_backtest] {market}/{horizon}: fetched={len(rows)} "
        f"resolved={len(observations)} skipped_unresolved={skipped_unresolved}"
    )
    return compute_bucket_stats(observations)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=MARKETS, required=True)
    parser.add_argument("--horizon", choices=HORIZONS, required=True)
    parser.add_argument("--limit", type=int, default=None,
                         help="cap the number of alpha_observations rows fetched "
                              "(oldest first) — useful for a quick manual sample run")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    stats = run_backtest(args.market, args.horizon, limit=args.limit)
    print(format_report({args.horizon: stats}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
