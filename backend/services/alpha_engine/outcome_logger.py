"""
Outcome Logger — resolves pending predictions against actual price returns.

Called periodically (every 6 hours via main.py).
For each unresolved prediction, fetches current price from yfinance and
computes 1D / 5D / 20D / 60D actual returns.

Only logs outcomes when the full forward window has elapsed — partial returns
were previously logged silently and corrupted IC training data.

Runs once per market (IN, US) — this used to hardcode the NSE ".NS" ticker
suffix unconditionally, so US predictions (added later) could never resolve:
"AAPL.NS" doesn't exist on Yahoo Finance, every fetch silently returned None,
and US outcomes never accumulated at all despite US predictions being logged
fine. The IC engine and meta-model were effectively IN-only despite ranking
both markets' Daily Picks with the same learned weights.
"""

import datetime as _dt
import logging
import time
from datetime import datetime, timezone

import yfinance as yf

log = logging.getLogger(__name__)

_TICKER_SUFFIX = {"IN": ".NS", "US": ""}

# (horizon, min_calendar_days_old, compute_1d, compute_5d, compute_20d, compute_60d)
# min_days is calendar days to wait before attempting resolution.
# _fetch_return enforces the trading-day window independently.
HORIZON_CONFIG = [
    ("short",   3,  True,  True,  False, False),  # 1D+5D; wait 3 cal days
    ("medium", 30,  False, True,  True,  False),  # 5D+20D; wait 30 cal days
    ("long",   90,  False, False, False, True),   # 60D only; wait 90 cal days
]

MARKETS = ("IN", "US")


def _to_date(value) -> _dt.date | None:
    """Normalize a pred_date value (str 'YYYY-MM-DD' from SQLite, or a
    date/datetime from psycopg) to a plain date for range comparison."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))


def _in_date_range(pred_date, start_date: str | None, end_date: str | None) -> bool:
    """Inclusive pred_date range check; None bounds are open-ended."""
    d = _to_date(pred_date)
    if start_date and d < _dt.date.fromisoformat(start_date):
        return False
    if end_date and d > _dt.date.fromisoformat(end_date):
        return False
    return True


def _fetch_return(symbol: str, pred_date_str: str, days: int, market: str = "IN",
                   apply_suffix: bool = True) -> float | None:
    """
    Compute the actual return from pred_date to pred_date + `days` trading days.
    Returns None if fewer than `days` trading days have elapsed since pred_date
    (avoids logging partial returns that corrupt IC calculations).

    apply_suffix: False for benchmark index tickers (e.g. "^NSEI", "^GSPC")
    — these are already complete Yahoo tickers; appending market's own
    equity suffix (".NS" for IN) would silently 404 the whole fetch.
    """
    try:
        from pandas import Timestamp
        pred_date = Timestamp(pred_date_str)
        yf_symbol = symbol + (_TICKER_SUFFIX.get(market, "") if apply_suffix else "")
        ticker = yf.Ticker(yf_symbol)
        # Fetch enough history: 60 trading days ≈ 90 calendar days
        hist = ticker.history(start=pred_date_str, period="4mo")
        if hist.empty or len(hist) < 2:
            return None
        hist.index = hist.index.tz_localize(None)
        avail = hist.index[hist.index >= pred_date]
        if len(avail) == 0:
            return None
        entry_price = float(hist.loc[avail[0], "Close"])
        future_rows = hist.index[hist.index >= avail[0]]
        # Require the full window to have elapsed — never use partial returns
        if len(future_rows) <= days:
            return None
        exit_price = float(hist.loc[future_rows[days], "Close"])
        return round((exit_price - entry_price) / entry_price * 100, 4)
    except Exception:
        return None


# 2026-07-17 (GPI-0 condition D3, Product Integrity #005's global audit):
# benchmark_return_5d/20d/60d were only ever set by a one-off migration
# script — the live resolver below never computed or passed them, so the
# frontend's "Alpha vs. benchmark" figure for every live-resolved outcome
# was silently comparing against an implicit 0%, not a real benchmark
# return. Same index tickers validation_engine.py already uses for its
# own benchmark comparisons, so a resolved outcome's benchmark basis
# matches the walk-forward backtest's.
_BENCHMARK_TICKER = {"IN": "^NSEI", "US": "^GSPC"}


def _fetch_benchmark_return(pred_date_str: str, days: int, market: str,
                             cache: dict[tuple, float | None]) -> float | None:
    """Same as _fetch_return, but for the market's benchmark index — and
    cached per (market, pred_date, days) for the lifetime of one
    resolve_pair() call, since every stock resolved for the same market on
    the same pred_date shares the identical benchmark return; without this,
    a batch of N pending predictions on the same day would trigger N
    redundant Yahoo fetches for the exact same index/window."""
    key = (market, pred_date_str, days)
    if key not in cache:
        ticker = _BENCHMARK_TICKER.get(market)
        cache[key] = (
            _fetch_return(ticker, pred_date_str, days, market, apply_suffix=False)
            if ticker else None
        )
    return cache[key]


def resolve_pair(market: str, horizon: str, min_days: int,
                  d1: bool, d5: bool, d20: bool, d60: bool, *,
                  start_date: str | None = None, end_date: str | None = None,
                  dry_run: bool = False, batch_limit: int | None = None) -> dict:
    """
    Resolve one (market, horizon) sweep. Shared by the periodic resolver
    (resolve_pending_outcomes, called with no extra kwargs — full backlog,
    writes enabled) and the operator backfill tool
    (scripts/backfill_outcomes.py) — the same eligibility check and
    COALESCE-preserving log_outcome back both, so a manual backfill run can
    never diverge in behavior from what the live resolver would eventually do
    on its own schedule.

    start_date/end_date: inclusive 'YYYY-MM-DD' pred_date bounds; None means
    open-ended. Only ever passed by the backfill tool.

    dry_run: when True, still fetches real prices (so the preview reflects
    genuine eligibility) but never calls log_outcome — zero writes.

    batch_limit: caps how many pending predictions this call processes.

    Returns a stats dict: examined, resolved (or would-resolve under
    dry_run), skipped (no return could be computed — see _fetch_return; this
    phase does not further distinguish "not enough trading days yet" from
    "missing/suspended/delisted price data", both surface as a skip), and
    pending (count of predictions offered this call, after start_date/
    end_date filtering, market-conflict exclusion, and batch_limit
    truncation).

    Phase 1A.6: predictions with a definitive market/symbol conflict (e.g. a
    US-only symbol logged under market=IN — the exact shape of the 13,605
    legacy rows found in the forensic investigation) are excluded here,
    BEFORE batch_limit truncation — so they no longer permanently occupy
    "oldest eligible" slots that would otherwise go to genuinely resolvable
    predictions, and the resolver stops making doomed yfinance requests for
    a symbol/suffix combination that can never succeed. Excluded rows are
    reported separately (market_conflict_excluded), never silently dropped.
    """
    from services.alpha_engine.store import get_unresolved_predictions, log_outcome
    from services.market_integrity import (
        EVENT_RESOLVER_CONFLICT_SKIPPED, REASON_DEFINITIVE_CONFLICT, SymbolMarketClass,
        classify_symbol, emit_market_event, is_definitive_conflict,
    )

    all_pending = get_unresolved_predictions(horizon, min_days_old=min_days, market=market)
    if start_date or end_date:
        all_pending = [p for p in all_pending if _in_date_range(p["pred_date"], start_date, end_date)]

    pending = []
    market_conflict_excluded = []
    unknown_symbol_count = 0
    for p in all_pending:
        if is_definitive_conflict(p["symbol"], market):
            market_conflict_excluded.append(p)
            continue
        if classify_symbol(p["symbol"]) == SymbolMarketClass.UNKNOWN:
            unknown_symbol_count += 1
        pending.append(p)

    eligible_scanned = len(all_pending)
    if batch_limit is not None:
        pending = pending[:batch_limit]

    if market_conflict_excluded:
        emit_market_event(
            EVENT_RESOLVER_CONFLICT_SKIPPED,
            f"[outcome_logger] [{market}/{horizon}] excluded {len(market_conflict_excluded)} "
            f"definitive market/symbol conflict(s) from candidate selection "
            f"(e.g. {market_conflict_excluded[0]['symbol']!r})",
            level=logging.WARNING, reason_code=REASON_DEFINITIVE_CONFLICT, market=market,
            excluded_count=len(market_conflict_excluded), eligible_count=eligible_scanned,
        )

    log.info(
        f"[outcome_logger] [{market}/{horizon}] {len(pending)} unresolved "
        f"prediction(s) found"
    )

    stats = {"market": market, "horizon": horizon, "examined": 0,
             "resolved": 0, "skipped": 0, "pending": len(pending), "dry_run": dry_run,
             "eligible_scanned": eligible_scanned,
             "market_conflict_excluded": len(market_conflict_excluded),
             "unknown_symbol_count": unknown_symbol_count}

    # Shared across every row this call resolves — see
    # _fetch_benchmark_return's docstring for why this must be per-call,
    # not per-row (every stock on the same pred_date shares one benchmark
    # return; without this a batch of N pending predictions would trigger
    # N redundant Yahoo fetches for the identical index/window).
    _benchmark_cache: dict[tuple, float | None] = {}

    for row in pending:
        symbol    = row["symbol"]
        pred_date = row["pred_date"]
        stats["examined"] += 1

        r1  = _fetch_return(symbol, pred_date, 1,  market) if d1  else None
        r5  = _fetch_return(symbol, pred_date, 5,  market) if d5  else None
        r20 = _fetch_return(symbol, pred_date, 20, market) if d20 else None
        r60 = _fetch_return(symbol, pred_date, 60, market) if d60 else None

        if any(x is not None for x in (r1, r5, r20, r60)):
            if not dry_run:
                # GPI-0 condition D3 — only computed for the horizons this
                # row's config actually resolves (mirrors r5/r20/r60 above);
                # log_outcome has no benchmark_return_1d column, matching
                # the existing schema (5d/20d/60d only).
                br5  = _fetch_benchmark_return(pred_date, 5,  market, _benchmark_cache) if d5  else None
                br20 = _fetch_benchmark_return(pred_date, 20, market, _benchmark_cache) if d20 else None
                br60 = _fetch_benchmark_return(pred_date, 60, market, _benchmark_cache) if d60 else None
                log_outcome(symbol, horizon, pred_date, r1, r5, r20,
                            return_60d=r60, market=market,
                            benchmark_return_5d=br5, benchmark_return_20d=br20,
                            benchmark_return_60d=br60)
            stats["resolved"] += 1
        else:
            stats["skipped"] += 1

    return stats


def resolve_pending_outcomes():
    """
    Main entry point — called every 6 hours via outcome_resolver_loop in main.py.

    For each market and horizon, finds predictions whose horizon-relevant
    outcome columns are still incomplete, fetches actual returns, and logs
    them. Never logs a column as resolved before its forward window has
    elapsed — a None from _fetch_return means "not ready yet" for that
    column specifically; other already-resolved columns on the same
    prediction are left untouched (see postgres_store.log_outcome).
    """
    sweep_start = time.time()
    total_examined = 0
    total_resolved = 0
    total_skipped = 0
    try:
        for market in MARKETS:
            for horizon, min_days, d1, d5, d20, d60 in HORIZON_CONFIG:
                stats = resolve_pair(market, horizon, min_days, d1, d5, d20, d60)
                total_examined += stats["examined"]
                total_resolved += stats["resolved"]
                total_skipped  += stats["skipped"]

        elapsed = round(time.time() - sweep_start, 1)
        log.info(
            f"[outcome_logger] sweep complete: examined={total_examined} "
            f"resolved={total_resolved} skipped={total_skipped} elapsed={elapsed}s"
        )

    except Exception as e:
        elapsed = round(time.time() - sweep_start, 1)
        log.warning(
            f"[outcome_logger] sweep error after {elapsed}s "
            f"(examined={total_examined} resolved={total_resolved}): {e}"
        )
