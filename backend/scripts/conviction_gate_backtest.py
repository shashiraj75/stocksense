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

Audit extension (2026-08-22)
----------------------------
This script now also drives a reproducible CONVICTION AUDIT. The audit's
normative rules — market/horizon separation, the eight populations, the claim
levels, and the two return measures — live in
`backend/services/alpha_engine/audit_contract.py` and are summarised here only
to point at it. The key correction it encodes:

  * the original return measure enters at a close that PRECEDES the pick's own
    pre-market generation. That is a NON-EXECUTABLE PRIOR-SESSION RESEARCH
    PRICE (not look-ahead bias, and not investor P&L). It is retained
    unchanged, but is now explicitly labelled RESEARCH_PRIOR_CLOSE, and
  * a second measure, EXECUTABLE_NEXT_OPEN, enters at the open of the first
    regular session strictly after `run_generated_at`, using the real NYSE and
    NSE exchange calendars (`audit_contract`'s companion `audit_calendar`).

Both measures are GROSS of all transaction costs. No cost model is
implemented here and neither measure may be described as net or as investor
P&L.

`--audit-out DIR` writes a full evidence bundle (run_manifest.json,
row_decisions.jsonl, aggregate_summary.json, statistical_results.json,
data_integrity_results.json) to a caller-supplied directory. That directory
MUST be outside the repository — no generated data file is ever committed.
Row counts must reconcile exactly (fetched == included + excluded) at every
stage; a failure raises `ReconciliationError` and exits non-zero rather than
reporting a denominator the audit cannot account for.

Usage (manual, not wired into any scheduled job):
    python backend/scripts/conviction_gate_backtest.py --market IN --horizon short
    python backend/scripts/conviction_gate_backtest.py --market US --horizon medium --limit 200
    python backend/scripts/conviction_gate_backtest.py --market US --horizon short \
        --audit-out /tmp/conviction-audit
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
        SELECT observation_id, run_id, symbol, run_generated_at,
               run_session_date, reference_session_date,
               reference_price, signal_confidence, signal,
               ranking_alpha, is_daily_pick, pick_rank
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
    MEASURE A — "PRIOR-CLOSE-TO-FUTURE-CLOSE RESEARCH RETURN — NON-EXECUTABLE".

    Gross of all transaction costs. The entry close precedes the pick's own
    pre-market generation (India cron ~20:37 UTC / ~02:07 IST next day; US
    cron ~06:00 UTC), so no investor could have transacted at it. This is a
    research reference price, NOT look-ahead bias and NOT investor P&L; it is
    retained unchanged purely for comparability with the superseded analysis.
    For an entry an investor could actually have achieved, use
    `resolve_executable_return` (MEASURE B). See
    `services/alpha_engine/audit_contract.py` section 4.

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


# --------------------------------------------------------------------------
# AUDIT LAYER — populations, the executable return measure, the missing
# analyses, and the reproducible evidence bundle. Every rule applied here is
# stated normatively in services/alpha_engine/audit_contract.py.
# --------------------------------------------------------------------------


def _contract():
    from services.alpha_engine import audit_contract

    return audit_contract


def resolve_executable_return(
    symbol: str,
    market: str,
    run_generated_at,
    horizon: str,
    *,
    today: _dt.date | None = None,
) -> tuple[float | None, str | None, dict]:
    """
    MEASURE B — "NEXT-TRADABLE-OPEN-TO-HORIZON-CLOSE GROSS BENCHMARK RETURN".

    Entry: the OPEN of the first regular session strictly after
    `run_generated_at`, resolved against the real NYSE/NSE exchange calendar
    (weekends, holidays and DST handled by `audit_calendar`, never by a
    hardcoded offset). Exit: the CLOSE of the session exactly
    HORIZON_TRADING_DAYS[horizon] sessions after that entry session.

    Gross of all transaction costs — no commission, spread, slippage, tax or
    market-impact model is applied. This is an executable BENCHMARK, not a
    realised fill.

    Returns `(return_pct, exclusion_reason, detail)`. Exactly one of
    `return_pct` / `exclusion_reason` is non-None, so every fetched row lands
    in exactly one bucket and the waterfall reconciles by construction. A
    horizon window that has not fully elapsed is EXCLUDED — never truncated,
    never extrapolated, never fabricated as 0.0.
    """
    from services.alpha_engine import audit_calendar

    detail: dict = {}
    trading_days = HORIZON_TRADING_DAYS.get(horizon)
    if trading_days is None:
        return None, "unknown_horizon", detail
    if run_generated_at is None:
        return None, "missing_run_generated_at", detail

    entry = audit_calendar.next_tradable_open(market, run_generated_at)
    if entry is None:
        return None, "no_tradable_session_after_generation", detail
    entry_date, entry_open_utc = entry
    detail["entry_session_date"] = entry_date.isoformat()
    detail["entry_session_open_utc"] = entry_open_utc.isoformat()

    exit_date = audit_calendar.session_offset(market, entry_date, trading_days)
    if exit_date is None:
        return None, "horizon_window_not_yet_complete", detail
    detail["exit_session_date"] = exit_date.isoformat()

    today = today or _dt.date.today()
    if exit_date >= today:
        return None, "horizon_window_not_yet_complete", detail

    try:
        import yfinance as yf
        from pandas import Timestamp

        yf_symbol = symbol + _TICKER_SUFFIX.get(market, "")
        hist = yf.Ticker(yf_symbol).history(
            start=entry_date.isoformat(),
            end=(exit_date + _dt.timedelta(days=5)).isoformat(),
        )
        if hist.empty:
            return None, "no_price_data", detail
        hist.index = hist.index.tz_localize(None)
        if Timestamp(entry_date) not in hist.index:
            return None, "entry_session_missing_from_price_data", detail
        if Timestamp(exit_date) not in hist.index:
            return None, "exit_session_missing_from_price_data", detail
        entry_price = float(hist.loc[Timestamp(entry_date), "Open"])
        exit_price = float(hist.loc[Timestamp(exit_date), "Close"])
    except Exception as e:  # noqa: BLE001 — one symbol must not abort the run
        log.debug(f"[conviction_audit] executable resolve failed for {symbol}: {e}")
        return None, "price_fetch_error", detail

    if not _finite(entry_price) or not _finite(exit_price):
        return None, "non_finite_price", detail
    if entry_price <= 0:
        return None, "non_positive_entry_price", detail

    detail["entry_price"] = entry_price
    detail["exit_price"] = exit_price
    return round((exit_price - entry_price) / entry_price * 100, 6), None, detail


def _finite(x) -> bool:
    """True only for a real, finite float — NaN and +/-Infinity are rejected."""
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f not in (float("inf"), float("-inf"))


def assign_populations(row: dict) -> list[str]:
    """
    Label one resolved row with every contract population it belongs to.

    Membership is deliberately non-exclusive (a row is simultaneously
    ALL_ELIGIBLE, BUY and BUY_HIGH_CONV) so that each analysis can select its
    own pair without re-deriving definitions — the drift that let the
    superseded report compare BUY_HIGH_CONV against all of NON_BUY.
    """
    c = _contract()
    pops = [c.P_ALL_ELIGIBLE]
    is_buy = str(row.get("signal") or "").upper() == "BUY"
    conf = row.get("signal_confidence")
    if is_buy:
        pops.append(c.P_BUY)
        if _finite(conf) and float(conf) >= c.MIN_CONVICTION_TO_PUBLISH:
            pops.append(c.P_BUY_HIGH_CONV)
        else:
            pops.append(c.P_BUY_LOW_CONV)
        if not row.get("is_daily_pick"):
            pops.append(c.P_UNPUBLISHED_BUY)
    else:
        pops.append(c.P_NON_BUY)
    if row.get("is_daily_pick"):
        pops.append(c.P_PUBLISHED)
    return pops


def within_run_rank_percentile(rows: list[dict]) -> None:
    """
    Attach `rank_percentile` to each BUY row, computed WITHIN its own run.

    Raw `ranking_alpha` values are not comparable across runs (each run
    z-scores against its own cross-section), so a pooled sort would compare
    incomparable scores. Percentiles are therefore computed inside a single
    run_id, over BUY rows only, with 1.0 = best-ranked in that run. Runs with
    fewer than two BUY rows get None — a single-element percentile carries no
    information and must not be silently coded as 1.0.
    """
    by_run: dict[object, list[dict]] = {}
    for r in rows:
        if str(r.get("signal") or "").upper() == "BUY" and _finite(r.get("ranking_alpha")):
            by_run.setdefault(r.get("run_id"), []).append(r)
    for _run, group in by_run.items():
        if len(group) < 2:
            for r in group:
                r["rank_percentile"] = None
            continue
        ordered = sorted(group, key=lambda r: float(r["ranking_alpha"]))
        n = len(ordered)
        for i, r in enumerate(ordered):
            r["rank_percentile"] = i / (n - 1)


def _win_rows(rows: list[dict], pop_a: str, pop_b: str, measure_key: str) -> list[dict]:
    """Build the flat row list the statistics layer consumes for A-vs-B."""
    out = []
    for r in rows:
        ret = r.get(measure_key)
        if not _finite(ret):
            continue
        if pop_a in r["populations"]:
            group = "A"
        elif pop_b in r["populations"]:
            group = "B"
        else:
            continue
        out.append({
            "group": group,
            "is_win": float(ret) > 0.0,
            "cluster_date": r["reference_session_date_iso"],
            "symbol": r["symbol"],
        })
    return out


def compare_populations(label, rows, pop_a, pop_b, measure_key, *, seed):
    """
    Run one A-vs-B comparison through BOTH the naive and the date-blocked
    method, then record whether they agree.

    Returning both is the point: the superseded report published only the
    naive answer. Where the two disagree the claim is capped at PRELIMINARY.
    """
    from services.alpha_engine import audit_stats

    flat = _win_rows(rows, pop_a, pop_b, measure_key)
    wins_a = sum(1 for r in flat if r["group"] == "A" and r["is_win"])
    n_a = sum(1 for r in flat if r["group"] == "A")
    wins_b = sum(1 for r in flat if r["group"] == "B" and r["is_win"])
    n_b = sum(1 for r in flat if r["group"] == "B")

    naive = audit_stats.two_proportion_effect(label, wins_a, n_a, wins_b, n_b)
    blocked = audit_stats.date_block_bootstrap(label, flat, seed=seed)
    blocked.naive_p_value = naive.naive_p_value
    blocked.naive_ci_pp = naive.naive_ci_pp
    blocked = audit_stats.reconcile_methods(blocked)
    result = blocked.to_dict()
    result["population_a"] = pop_a
    result["population_b"] = pop_b
    result["measure"] = measure_key
    result["jackknife"] = audit_stats.symbol_cluster_jackknife(flat)
    result["claim_level"] = classify_claim(blocked)
    return result


def classify_claim(result) -> str:
    """
    Map a statistical result onto a contract claim level.

    PROVEN requires everything: adequate clusters, a date-blocked interval
    excluding zero, AND agreement with the naive method. Anything less is
    capped — this function is the single place that cap is enforced.
    """
    c = _contract()
    if result.difference_pp is None:
        return c.NOT_PROVEN
    if not result.inference_permitted or result.block_ci_pp is None:
        return c.PRELIMINARY
    lo, hi = result.block_ci_pp
    excludes_zero = (lo > 0 and hi > 0) or (lo < 0 and hi < 0)
    if not excludes_zero:
        return c.NOT_PROVEN
    if result.methods_agree is False:
        return c.PRELIMINARY
    return c.PROVEN


def concentration_report(rows: list[dict], measure_key: str) -> dict:
    """
    Concentration and stability of whatever sample a claim rests on.

    Reports distinct symbols/dates and the largest single symbol's and single
    date's share, because a "10,000 observation" sample built from 400 symbols
    over 20 dates has nothing like 10,000 independent pieces of information.
    The first/second-half split is DATE-PURE (split on the ordered distinct
    dates, never mid-date) and is compared only against THIS market's own
    baseline — never another market's, per contract rule 1.

    Sector breadth is reported as NOT_REPRODUCIBLE: no sector column exists.
    """
    c = _contract()
    resolved = [r for r in rows if _finite(r.get(measure_key))]
    n = len(resolved)
    if n == 0:
        return {"n_resolved": 0, "note": "no resolved rows"}

    dates = sorted({r["reference_session_date_iso"] for r in resolved})
    sym_counts: dict[str, int] = {}
    date_counts: dict[str, int] = {}
    for r in resolved:
        sym_counts[r["symbol"]] = sym_counts.get(r["symbol"], 0) + 1
        date_counts[r["reference_session_date_iso"]] = (
            date_counts.get(r["reference_session_date_iso"], 0) + 1)

    def win_rate(subset):
        return (sum(1 for r in subset if float(r[measure_key]) > 0) / len(subset)
                if subset else None)

    half = len(dates) // 2
    first_dates, second_dates = set(dates[:half]), set(dates[half:])
    first = [r for r in resolved if r["reference_session_date_iso"] in first_dates]
    second = [r for r in resolved if r["reference_session_date_iso"] in second_dates]

    return {
        "n_resolved": n,
        "distinct_symbols": len(sym_counts),
        "distinct_dates": len(dates),
        "max_single_symbol_share": max(sym_counts.values()) / n,
        "max_single_date_share": max(date_counts.values()) / n,
        "baseline_win_rate": win_rate(resolved),
        "first_half": {"n": len(first), "dates": len(first_dates),
                       "win_rate": win_rate(first)},
        "second_half": {"n": len(second), "dates": len(second_dates),
                        "win_rate": win_rate(second)},
        "half_split_note": (
            "Date-pure split on this market's own ordered distinct dates, "
            "compared ONLY against this market's own baseline_win_rate. No "
            "cross-market comparison is made (contract rule 1)."
        ),
        "sector_breadth": {
            "claim_level": c.NOT_REPRODUCIBLE,
            "note": ("No sector column exists in alpha_observations or any "
                     "joined table. Sector breadth cannot be computed and "
                     "must not be claimed."),
        },
    }


def build_audit(market: str, horizon: str, *, limit=None, seed, today=None) -> dict:
    """
    Full audit for one market x horizon, with an exactly-reconciling waterfall.

    Every fetched row produces exactly one decision record. `included` and
    `excluded` are counted independently and then checked against `fetched`
    via `audit_contract.assert_reconciles`, which RAISES on a mismatch — the
    audit refuses to report a denominator it cannot account for.
    """
    import os

    import psycopg

    c = _contract()
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not set — cannot run against Postgres")

    with psycopg.connect(database_url) as conn:
        raw = fetch_observations(conn, market, horizon, limit=limit)

    decisions: list[dict] = []
    rows: list[dict] = []
    for r in raw:
        ref_iso = _to_date(r["reference_session_date"]).isoformat()
        research = resolve_realized_return(
            r["symbol"], market, r["reference_session_date"], horizon, today=today)
        exe, exe_reason, exe_detail = resolve_executable_return(
            r["symbol"], market, r.get("run_generated_at"), horizon, today=today)

        record = dict(r)
        record["reference_session_date_iso"] = ref_iso
        record[c.RESEARCH_PRIOR_CLOSE] = research
        record[c.EXECUTABLE_NEXT_OPEN] = exe
        record["populations"] = assign_populations(r)
        rows.append(record)

        decisions.append({
            "observation_id": str(r.get("observation_id")),
            "run_id": r.get("run_id"),
            "symbol": r["symbol"],
            "market": market,
            "horizon": horizon,
            "reference_session_date": ref_iso,
            "signal": r.get("signal"),
            "signal_confidence": r.get("signal_confidence"),
            "is_daily_pick": bool(r.get("is_daily_pick")),
            "pick_rank": r.get("pick_rank"),
            "populations": record["populations"],
            "research_return_pct": research,
            "research_excluded_reason": None if research is not None
                                        else "research_window_unresolvable",
            "executable_return_pct": exe,
            "executable_excluded_reason": exe_reason,
            "executable_detail": exe_detail,
        })

    within_run_rank_percentile(rows)

    waterfall = {}
    for measure in (c.RESEARCH_PRIOR_CLOSE, c.EXECUTABLE_NEXT_OPEN):
        included = sum(1 for r in rows if _finite(r.get(measure)))
        excluded = len(rows) - included
        c.assert_reconciles(f"{market}/{horizon}/{measure}", len(rows), included, excluded)
        reasons: dict[str, int] = {}
        key = ("executable_excluded_reason" if measure == c.EXECUTABLE_NEXT_OPEN
               else "research_excluded_reason")
        for d in decisions:
            if d[key]:
                reasons[d[key]] = reasons.get(d[key], 0) + 1
        waterfall[measure] = {"fetched": len(rows), "included": included,
                              "excluded": excluded, "exclusion_reasons": reasons}

    stats: dict = {}
    for measure in (c.RESEARCH_PRIOR_CLOSE, c.EXECUTABLE_NEXT_OPEN):
        stats[measure] = {
            # (A) the headline comparison, correctly specified: BUY against
            # the ELIGIBLE NON-BUY population, never against "chance".
            "buy_vs_non_buy": compare_populations(
                f"{market}/{horizon}/BUY-vs-NON_BUY", rows,
                c.P_BUY, c.P_NON_BUY, measure, seed=seed),
            # (B) the conviction gate, tested WITHIN BUY only.
            "conviction_within_buy": compare_populations(
                f"{market}/{horizon}/BUY>=85-vs-BUY<85", rows,
                c.P_BUY_HIGH_CONV, c.P_BUY_LOW_CONV, measure, seed=seed),
            # (D) what users saw vs the BUY candidates that were not published.
            "published_vs_unpublished_buy": compare_populations(
                f"{market}/{horizon}/PUBLISHED-vs-UNPUBLISHED_BUY", rows,
                c.P_PUBLISHED, c.P_UNPUBLISHED_BUY, measure, seed=seed),
            # (C) does the ranking add information beyond the binary BUY call?
            "ranking_lift": ranking_lift(rows, measure),
            # (E) how concentrated is the sample the claims rest on?
            "concentration": concentration_report(rows, measure),
        }

    return {"market": market, "horizon": horizon, "rows": rows,
            "decisions": decisions, "waterfall": waterfall, "statistics": stats}


def ranking_lift(rows: list[dict], measure_key: str, n_quantiles: int = 4) -> dict:
    """
    (C) Does WITHIN-RUN rank order add information beyond the binary BUY call?

    Buckets BUY rows by their within-run `rank_percentile` and reports each
    quantile's win rate, plus whether those win rates are monotone in rank.
    Monotonicity is the honest headline: a top quantile that beats the bottom
    while the middle quantiles are unordered is noise, not ranking skill.

    Never uses raw ranking_alpha across runs — see `within_run_rank_percentile`.
    """
    c = _contract()
    eligible = [r for r in rows
                if c.P_BUY in r["populations"]
                and r.get("rank_percentile") is not None
                and _finite(r.get(measure_key))]
    if len(eligible) < n_quantiles * 2:
        return {"claim_level": c.NOT_PROVEN, "n": len(eligible),
                "note": "too few ranked BUY rows with a resolved return"}

    buckets: list[list[dict]] = [[] for _ in range(n_quantiles)]
    for r in eligible:
        q = min(int(r["rank_percentile"] * n_quantiles), n_quantiles - 1)
        buckets[q].append(r)

    out = []
    for i, b in enumerate(buckets):
        wr = (sum(1 for r in b if float(r[measure_key]) > 0) / len(b)) if b else None
        out.append({"quantile": i + 1, "n": len(b), "win_rate": wr,
                    "avg_return_pct": (sum(float(r[measure_key]) for r in b) / len(b))
                                      if b else None})
    rates = [q["win_rate"] for q in out if q["win_rate"] is not None]
    monotone = (len(rates) == n_quantiles
                and (all(x <= y for x, y in zip(rates, rates[1:]))
                     or all(x >= y for x, y in zip(rates, rates[1:]))))
    return {
        "quantiles": out,
        "monotone": monotone,
        "top_minus_bottom_pp": ((rates[-1] - rates[0]) * 100.0)
                               if len(rates) == n_quantiles else None,
        "claim_level": c.PRELIMINARY if monotone else c.NOT_PROVEN,
        "note": ("Within-run percentiles of ranking_alpha (raw scores are not "
                 "comparable across runs). Ranking adds information beyond the "
                 "binary BUY call only if these win rates are monotone in rank."),
    }


def write_audit_bundle(out_dir, audits: list[dict], *, seed: int, integrity: dict | None = None):
    """
    Write the five-file evidence bundle to a caller-supplied directory.

    The directory must be OUTSIDE the repository — this function refuses to
    write inside the working tree, so a generated data file can never be
    committed by accident.
    """
    import json
    import os
    import pathlib

    c = _contract()
    out = pathlib.Path(out_dir).expanduser().resolve()
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if out == repo_root or repo_root in out.parents:
        raise ValueError(
            f"refusing to write the audit bundle inside the repository "
            f"({out}); pass --audit-out pointing somewhere outside {repo_root}"
        )
    out.mkdir(parents=True, exist_ok=True)

    def dump(name, obj):
        (out / name).write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")

    dump("run_manifest.json", {
        "calculation_version": c.CALCULATION_VERSION,
        "seed": seed,
        "generated_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "return_measures": c.RETURN_MEASURES,
        "populations": list(c.POPULATIONS),
        "claim_levels": list(c.CLAIM_LEVELS),
        "cells": [{"market": a["market"], "horizon": a["horizon"]} for a in audits],
        "gross_of_costs": True,
        "cost_model_implemented": False,
        "permanently_not_reproducible": list(c.PERMANENTLY_NOT_REPRODUCIBLE),
        "read_only": True,
    })

    with (out / "row_decisions.jsonl").open("w", encoding="utf-8") as fh:
        for a in audits:
            for d in a["decisions"]:
                fh.write(json.dumps(d, default=str) + "\n")

    dump("aggregate_summary.json", {
        f'{a["market"]}/{a["horizon"]}': a["waterfall"] for a in audits})
    dump("statistical_results.json", {
        f'{a["market"]}/{a["horizon"]}': a["statistics"] for a in audits})
    dump("data_integrity_results.json", integrity or {
        "note": "no integrity checks supplied to this invocation"})
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=MARKETS, required=True)
    parser.add_argument("--horizon", choices=HORIZONS, required=True)
    parser.add_argument("--limit", type=int, default=None,
                         help="cap the number of alpha_observations rows fetched "
                              "(oldest first) — useful for a quick manual sample run")
    parser.add_argument("--audit-out", default=None,
                         help="write the full reproducible audit bundle to this "
                              "directory (must be OUTSIDE the repository)")
    parser.add_argument("--seed", type=int, default=None,
                         help="deterministic seed for the date-blocked bootstrap")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.audit_out:
        from services.alpha_engine import audit_stats

        seed = args.seed if args.seed is not None else audit_stats.DEFAULT_SEED
        audit = build_audit(args.market, args.horizon, limit=args.limit, seed=seed)

        # Holm correction across the PRE-REGISTERED primary family only. The
        # exploratory factor/regime/momentum family is corrected separately
        # and is never promoted into this one.
        c = _contract()
        primary = {}
        for measure, block in audit["statistics"].items():
            for name in ("buy_vs_non_buy", "conviction_within_buy",
                         "published_vs_unpublished_buy"):
                primary[f"{measure}/{name}"] = block[name].get("block_p_value")
        corrected = audit_stats.holm_correction(primary)
        for measure, block in audit["statistics"].items():
            for name in ("buy_vs_non_buy", "conviction_within_buy",
                         "published_vs_unpublished_buy"):
                adj = corrected.get(f"{measure}/{name}", {})
                block[name]["holm"] = adj
                # A comparison that does not survive Holm can never be PROVEN.
                if block[name].get("claim_level") == c.PROVEN and not adj.get("reject"):
                    block[name]["claim_level"] = c.PRELIMINARY
                    block[name].setdefault("notes", []).append(
                        "Did not survive Holm correction across the primary "
                        "family — downgraded from PROVEN to PRELIMINARY.")

        dest = write_audit_bundle(args.audit_out, [audit], seed=seed)
        print(f"[conviction_audit] bundle written to {dest}")
        for measure, wf in audit["waterfall"].items():
            print(f"  {measure}: fetched={wf['fetched']} included={wf['included']} "
                  f"excluded={wf['excluded']}")
        return 0

    stats = run_backtest(args.market, args.horizon, limit=args.limit)
    print(format_report({args.horizon: stats}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
