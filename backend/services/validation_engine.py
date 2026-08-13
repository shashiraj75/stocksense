"""
Walk-Forward Validation Engine
================================
Answers the core question: "Does this model actually predict stock returns?"

Runs the FULL technical scoring model historically across Nifty 100 stocks,
computes forward returns at each signal date, and measures:

1. Hit Rate          — % of BUY/SELL calls where direction was correct
2. Average Return    — mean actual return when model says BUY vs SELL vs baseline
3. Signal Precision  — within BUY calls, % that beat a minimum threshold
4. Sharpe Ratio      — risk-adjusted return of the model's portfolio
5. vs Benchmark      — comparison to Nifty 50 buy-and-hold
6. IC by Factor      — which factor (tech/RS/OBV/MFI) actually predicted returns
7. Score Calibration — hit rate by composite score bucket (60-70, 70-80, 80+)

Walk-forward guarantee: at each date t, only data available before t is used.
No look-ahead bias — forward return is measured at t + horizon_days.
"""

import logging
import os
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

from services.safe_errors import safe_error_message
from services.technical_indicators import compute_indicators
from services import sec_edgar_adapter
from services import sec_pit_store

log = logging.getLogger(__name__)

# ── Storage — Postgres (production) or SQLite (local dev) ────────────────────
_USE_POSTGRES = bool(os.getenv("DATABASE_URL") and os.getenv("USE_POSTGRES", "0") == "1")
_DB_PATH      = os.path.join(os.path.dirname(__file__), "../../validation_results.db")
_db_lock      = threading.Lock()

# ── Progress tracking (in-memory, for API polling) ────────────────────────────
# Backward-compatible top-level keys (running/progress/total/started_at/log)
# plus, since the 2026-07 job-identity fix, a "job" dict binding the active
# run immutably to exactly one market, universe and horizon — see
# _new_job_identity() and tests/regression/test_validation_job_identity.py
# for the production defect (a US run rendered under the Nifty 100 tab)
# this contract exists to prevent.
#
# In-memory only, module-level — a process restart between claim_validation_job()
# and run_validation() completing resets this to the clean default below
# rather than leaving a stale "running" state (the safe direction: a lost
# claim is recoverable by simply starting a new run; a permanently stuck
# "running=True" would not be). No durable/distributed job recovery exists
# or is required by this contract.
_run_status: dict = {"running": False, "progress": 0, "total": 0, "started_at": None, "log": []}
_status_lock = threading.Lock()

# Versions locked into every job identity and persisted run summary.
VALIDATION_METHODOLOGY_VERSION = "walk-forward-v1"
VALIDATION_MODEL_VERSION = "composite-technical-v1"
# The universe lists in this module are static code constants (last revised
# June 2026) — not point-in-time membership. The version tag makes any
# future list revision visible in persisted results.
UNIVERSE_VERSION = {"nifty100": "static-2026.06", "midcap": "static-2026.06", "us": "static-2026.06"}


# ── Public diagnostic sanitization ────────────────────────────────────────────
# Every Validation payload reachable from a public API response — GET /status
# (get_run_status), GET /results (get_latest_results, including older
# persisted val_runs.summary rows) — must never carry a raw exception
# (str(e)/repr(e)), a traceback, a Python exception class name, or provider/
# SQL/filesystem detail. The real exception is always logged server-side
# (log.exception) at the point it occurs; only one of these fixed, stable
# values ever crosses into a public field. Mirrors the repository-wide
# safe_error_message() convention (services/safe_errors.py), centralized
# here for Validation's specific diagnostic fields.
VALIDATION_PUBLIC_FAILURE_MESSAGES = {
    "RUN_EXCEPTION": "Validation run failed.",
    "BENCHMARK_FETCH_FAILED": "Benchmark data is temporarily unavailable.",
    "SYMBOL_VALIDATION_FAILED": "Validation failed for this symbol.",
    "CLAIMED_JOB_MISMATCH": "Validation job identity mismatch.",
    "BENCHMARK_EVIDENCE_UNAVAILABLE": "Benchmark evidence is currently unavailable for this run.",
    "BENCHMARK_ALIGNMENT_COVERAGE_INSUFFICIENT": (
        "Benchmark evidence coverage across this run was insufficient to publish a result."
    ),
}

# benchmark_unavailable_reason's two possible stable public values. The
# genuine "not enough bars for this horizon" condition
# (BENCHMARK_UNAVAILABLE_INSUFFICIENT_HISTORY) must never be collapsed into
# the provider-failure code, and vice versa — they are different conditions
# with different operational meaning and must stay distinguishable.
BENCHMARK_UNAVAILABLE_INSUFFICIENT_HISTORY = "insufficient_benchmark_history_for_horizon"
BENCHMARK_UNAVAILABLE_FETCH_FAILED = "benchmark_fetch_failed"


def _sanitize_benchmark_unavailable_reason(reason: str | None) -> str | None:
    """Map a benchmark_unavailable_reason value to a stable public code.

    Pure and deterministic — safe to apply both to a freshly computed
    reason and to an older persisted val_runs.summary row written before
    this sanitization existed (whose reason may still literally be the
    legacy "benchmark_fetch_failed: <raw exception text>" string). Never
    mutates the stored row; only sanitizes the value being returned.
    """
    if reason is None or reason == BENCHMARK_UNAVAILABLE_INSUFFICIENT_HISTORY:
        return reason
    # Any other value — a freshly written stable code, or a legacy raw
    # "benchmark_fetch_failed: <exception>" string — is a provider-fetch
    # failure of some kind. The exact historical exception text is never
    # reconstructable as safe, so it always collapses to the one stable code.
    return BENCHMARK_UNAVAILABLE_FETCH_FAILED


# ── Benchmark evidence integrity ──────────────────────────────────────────────
# Closes a distinct, deeper class of defect than the sanitization above:
# unavailable or invalid benchmark evidence being silently treated as a
# genuine flat 0.0% return or a genuine neutral (0.0) market regime,
# fabricating alpha/correctness/regime input for signals that never had
# real benchmark evidence behind them. See BenchmarkEvidence,
# _validate_benchmark_acquisition (run-level preflight, before any stock
# work is submitted) and _align_benchmark_close (per-signal alignment,
# forward-fill only, bounded staleness, no look-ahead).
BENCHMARK_EVIDENCE_VERSION = "validation_benchmark_evidence_v1"

# Forward-fill is permitted only to bridge an ordinary same-market
# holiday/weekend gap between the benchmark's own trading calendar and the
# stock's — never to manufacture an observation for a date meaningfully
# newer than the benchmark's last real one. 5 calendar days comfortably
# covers a long weekend or single-day holiday in either the NSE or NYSE
# calendar without accepting a genuine multi-session data gap as current.
BENCHMARK_ALIGNMENT_MAX_STALE_DAYS = 5

# Bounded retry for a transient benchmark-fetch failure, matching this
# codebase's existing provider-retry convention (see e.g.
# sec_edgar_adapter.py's _RETRY_COUNT/_RETRY_BACKOFF_SECONDS,
# nse_bhavcopy.py's _MAX_ATTEMPTS) — same ticker only, capped attempts,
# capped total wait, no new provider. After the last attempt fails, the
# run fails closed (see run_validation's benchmark preflight gate) rather
# than silently proceeding or falling back to a stale cached value (this
# module retains no such cache with the provenance a safe fallback would
# require).
BENCHMARK_FETCH_MAX_ATTEMPTS = 2
BENCHMARK_FETCH_RETRY_BACKOFF_SECONDS = 2

# Acquisition-level coverage gate (Finding C, 2026-07-26 hardening): a
# single valid forward-return window amid hundreds of invalid rows is not
# adequate evidence to back an entire universe's walk-forward run. 95% is
# the preferred conservative default — a genuinely healthy same-market
# benchmark history is expected to clear this comfortably; this has not
# been lowered to accommodate any test's prior, now-corrected expectation.
BENCHMARK_MIN_ACQUISITION_WINDOW_COVERAGE_PCT = 95.0

# Post-alignment, whole-run coverage gate (Finding D): the real fraction of
# candidate per-symbol signal windows (across the whole universe) that had
# genuine benchmark+regime evidence. Distinct from the acquisition-level
# gate above — this can only be known after every symbol's backtest has
# actually run, so it is checked after the ThreadPoolExecutor loop but
# strictly before _compute_metrics()/persistence.
BENCHMARK_MIN_SIGNAL_COVERAGE_PCT = 95.0

# Acquisition states worth a bounded retry — plausibly transient (a
# provider hiccup can return an empty/malformed frame without raising).
# Deliberately excludes structural states (unsorted/duplicate/non-positive
# index, invalid index type, insufficient window coverage, an unexpected
# validator exception) where a second identical call to the same ticker
# cannot reasonably be expected to fix a schema-level problem — see
# run_validation's retry loop for where this set is consulted.
_BENCHMARK_RETRIABLE_ACQUISITION_STATUSES = frozenset({
    "empty", "missing_close_column", "non_numeric", "non_finite", "insufficient_history",
})


@dataclass(frozen=True)
class BenchmarkEvidence:
    """Typed, versioned verdict on one fetched benchmark DataFrame's
    fitness to back an entire Validation run. `status == "available"` is
    the only status under which run_validation proceeds to submit any
    stock backtest — every other status is a distinct, stable, non-
    exception reason a caller (public API, log line, or test) can rely on
    without ever seeing raw provider/exception text. Every field is safe
    to expose in a public API response or a failed job's status snapshot —
    none is ever derived from str(exc)/repr(exc)."""
    status: str
    reason: str | None
    ticker: str
    market: str
    horizon: str
    rows_available: int
    rows_required: int
    first_observation_at: str | None
    last_observation_at: str | None
    methodology_version: str
    # Coverage disclosure (Finding C, 2026-07-26 hardening) — real counts,
    # never fabricated denominators. total_rows is the raw fetched row
    # count; numeric_rows is how many parsed as numeric (see
    # _coerce_benchmark_close); finite_positive_rows counts genuinely
    # usable closes; invalid_rows counts values that were present but
    # could not be parsed as numeric.
    total_rows: int = 0
    numeric_rows: int = 0
    finite_positive_rows: int = 0
    invalid_rows: int = 0
    total_forward_windows: int = 0
    valid_forward_windows: int = 0
    forward_window_coverage_pct: float | None = None


def _coerce_benchmark_close(bench_df: pd.DataFrame) -> tuple[pd.Series, int, int]:
    """Deterministically coerce a benchmark DataFrame's Close column to a
    numeric Series, without mutating the original frame. This is THE one
    effective series shared by acquisition validation
    (_validate_benchmark_acquisition), the run-level aggregate benchmark
    return, and per-signal alignment (_align_benchmark_close) — the same
    validated series must never be swapped for the raw, unfiltered column
    partway through a run (Finding B).

    Returns (numeric_close, numeric_rows, invalid_rows). If Close is
    already a numeric dtype, this is a no-op (zero-copy) pass-through — no
    row is ever reported invalid on dtype grounds alone. Otherwise
    `pd.to_numeric(..., errors="coerce")` is used: deterministic, and any
    originally-non-null value that fails to parse becomes NaN — a
    converted/missing value is NEVER later treated as valid evidence
    (every downstream consumer already requires np.isfinite()).
    """
    close = bench_df["Close"]
    if pd.api.types.is_numeric_dtype(close):
        return close, int(close.notna().sum()), 0
    original_non_null = int(close.notna().sum())
    coerced = pd.to_numeric(close, errors="coerce")
    numeric_rows = int(coerced.notna().sum())
    invalid_rows = max(0, original_non_null - numeric_rows)
    return coerced, numeric_rows, invalid_rows


def _validate_benchmark_acquisition(
    bench_df: pd.DataFrame | None, ticker: str, market: str, horizon: str,
) -> BenchmarkEvidence:
    """Pure(-ish — logs on the exceptional path only) validation of a
    freshly fetched benchmark DataFrame. Called once per attempt, before
    any stock backtest future is submitted, so a bad benchmark fails the
    whole run fast rather than letting every stock in the universe
    silently compute alpha/correctness against fabricated zero/neutral
    evidence.

    TOTAL FUNCTION CONTRACT (Finding A, 2026-07-26 hardening): this
    function must never raise for any DataFrame shape or dtype — it
    always returns a BenchmarkEvidence. Any unexpected internal error
    (a pathological object-dtype value even pd.to_numeric chokes on, a
    non-standard index type breaking .date(), etc.) is caught, logged
    server-side with full detail, and reported as the fixed, safe
    "validation_error" status — never raw exception text. This matters
    because run_validation calls this OUTSIDE its own try/except (so a
    raised failure here is never re-caught and relabeled RUN_EXCEPTION —
    see run_validation's own comment) — an exception escaping this
    function would bypass the BENCHMARK_EVIDENCE_UNAVAILABLE transition
    entirely and could strand the active job slot.

    `status == "available"` guarantees: the frame is non-empty, has a
    `Close` column, a DatetimeIndex, a strictly sorted and duplicate-free
    index, a numerically parseable Close column, and forward-return-window
    coverage (both entry AND exit finite and strictly positive — Finding
    B) at or above BENCHMARK_MIN_ACQUISITION_WINDOW_COVERAGE_PCT — never
    just "at least one" valid window (Finding C).
    """
    fwd_days = HORIZON_DAYS[horizon]
    step = HORIZON_STEP[horizon]
    rows_required = fwd_days + 1

    def _evidence(
        status: str, reason: str | None, *, rows_available: int = 0,
        first: str | None = None, last: str | None = None,
        total_rows: int = 0, numeric_rows: int = 0, finite_positive_rows: int = 0,
        invalid_rows: int = 0, total_forward_windows: int = 0, valid_forward_windows: int = 0,
        forward_window_coverage_pct: float | None = None,
    ) -> BenchmarkEvidence:
        return BenchmarkEvidence(
            status=status, reason=reason, ticker=ticker, market=market, horizon=horizon,
            rows_available=rows_available, rows_required=rows_required,
            first_observation_at=first, last_observation_at=last,
            methodology_version=BENCHMARK_EVIDENCE_VERSION,
            total_rows=total_rows, numeric_rows=numeric_rows,
            finite_positive_rows=finite_positive_rows, invalid_rows=invalid_rows,
            total_forward_windows=total_forward_windows, valid_forward_windows=valid_forward_windows,
            forward_window_coverage_pct=forward_window_coverage_pct,
        )

    try:
        if bench_df is None or bench_df.empty:
            return _evidence("empty", "benchmark fetch returned no rows")
        if "Close" not in bench_df.columns:
            return _evidence(
                "missing_close_column", "benchmark data has no Close column",
                rows_available=len(bench_df), total_rows=len(bench_df),
            )
        if not isinstance(bench_df.index, pd.DatetimeIndex):
            return _evidence(
                "invalid_index_type", "benchmark index is not a DatetimeIndex",
                rows_available=len(bench_df), total_rows=len(bench_df),
            )
        if not bench_df.index.is_monotonic_increasing:
            return _evidence(
                "unsorted_index", "benchmark index is not sorted ascending",
                rows_available=len(bench_df), total_rows=len(bench_df),
            )
        if bench_df.index.has_duplicates:
            return _evidence(
                "duplicate_index", "benchmark index has duplicate dates",
                rows_available=len(bench_df), total_rows=len(bench_df),
            )

        total_rows = len(bench_df)
        first_obs = str(bench_df.index[0].date())
        last_obs = str(bench_df.index[-1].date())

        # Deterministic numeric coercion (Finding A) — a string-only Close
        # column is reported as a distinct, safe status rather than
        # raising; a mixed numeric/string column proceeds using only the
        # values that genuinely parsed, with the invalid count disclosed.
        # "non_numeric" is reserved for a genuinely non-numeric dtype with
        # zero parseable values — an already-numeric (float/int) column
        # that happens to be entirely NaN is a distinct, existing
        # "non_finite" condition, not a dtype/parsing problem.
        was_numeric_dtype = pd.api.types.is_numeric_dtype(bench_df["Close"])
        numeric_close, numeric_rows, invalid_rows = _coerce_benchmark_close(bench_df)
        if not was_numeric_dtype and numeric_rows == 0:
            return _evidence(
                "non_numeric", "benchmark Close column contains no parseable numeric values",
                rows_available=total_rows, first=first_obs, last=last_obs,
                total_rows=total_rows, numeric_rows=0, invalid_rows=invalid_rows,
            )

        finite = numeric_close[np.isfinite(numeric_close)]
        if len(finite) == 0:
            return _evidence(
                "non_finite", "benchmark Close has no finite values",
                rows_available=total_rows, first=first_obs, last=last_obs,
                total_rows=total_rows, numeric_rows=numeric_rows, invalid_rows=invalid_rows,
            )
        finite_positive = finite[finite > 0]
        if len(finite_positive) == 0:
            return _evidence(
                "non_positive", "benchmark Close has no positive values",
                rows_available=total_rows, first=first_obs, last=last_obs,
                total_rows=total_rows, numeric_rows=numeric_rows, invalid_rows=invalid_rows,
            )

        # Forward-window coverage (Finding B: BOTH entry and exit must be
        # finite and strictly positive; Finding C: count every candidate
        # window, not just whether one happens to exist).
        total_forward_windows = 0
        valid_forward_windows = 0
        for i in range(0, total_rows - fwd_days, step):
            total_forward_windows += 1
            e = numeric_close.iloc[i]
            x = numeric_close.iloc[i + fwd_days]
            if np.isfinite(e) and np.isfinite(x) and e > 0 and x > 0:
                valid_forward_windows += 1

        common = dict(
            rows_available=total_rows, first=first_obs, last=last_obs,
            total_rows=total_rows, numeric_rows=numeric_rows,
            finite_positive_rows=len(finite_positive), invalid_rows=invalid_rows,
            total_forward_windows=total_forward_windows, valid_forward_windows=valid_forward_windows,
        )

        if total_forward_windows == 0:
            # Too few rows to form even one candidate window — a distinct,
            # row-count-based condition from "plenty of windows, most
            # invalid" (insufficient_window_coverage, below).
            return _evidence(
                "insufficient_history",
                f"fewer than {rows_required} rows — cannot form any forward-return "
                f"window for horizon={horizon!r}",
                **common, forward_window_coverage_pct=None,
            )

        coverage_pct = round(valid_forward_windows / total_forward_windows * 100, 2)
        if coverage_pct < BENCHMARK_MIN_ACQUISITION_WINDOW_COVERAGE_PCT:
            return _evidence(
                "insufficient_window_coverage",
                f"only {coverage_pct}% of {total_forward_windows} candidate forward-return "
                f"windows are valid (need >= {BENCHMARK_MIN_ACQUISITION_WINDOW_COVERAGE_PCT}%)",
                **common, forward_window_coverage_pct=coverage_pct,
            )

        return _evidence("available", None, **common, forward_window_coverage_pct=coverage_pct)

    except Exception:
        # Total-function guarantee: never let an unexpected internal error
        # escape as a raised exception — the caller (run_validation) does
        # not wrap this call in its own try/except, by design (see this
        # function's own docstring), so an escaping exception here would
        # bypass the fail-closed transition entirely. Full detail logged
        # server-side only; the public status is the fixed, safe code.
        log.exception(
            "[validation] unexpected internal error validating benchmark acquisition — "
            "ticker=%s market=%s horizon=%s",
            ticker, market, horizon,
        )
        return _evidence("validation_error", "unexpected error validating benchmark data")


def _align_benchmark_close(
    benchmark_df: pd.DataFrame, stock_index: pd.DatetimeIndex,
) -> tuple[pd.Series, pd.Series]:
    """Forward-fill-only alignment of benchmark closes onto a stock's own
    trading dates, with a bounded staleness tolerance — the point-in-time
    integrity contract for benchmark alignment.

    Never backward-fills: a stock date before the benchmark's first real
    observation gets no value, regardless of what a later observation
    might say. A stock date whose most recent real benchmark observation
    is more than BENCHMARK_ALIGNMENT_MAX_STALE_DAYS calendar days in the
    past is marked unavailable too, even though a value could technically
    be forward-filled — an ordinary same-market holiday gap is bridged;
    a genuine multi-session data gap is not silently presented as current.

    Timezone/index robustness (Finding G, 2026-07-26 hardening): both
    indexes are normalized to tz-naive, midnight-normalized calendar dates
    before comparison — via tz_localize(None) (strips a timezone while
    PRESERVING the displayed wall-clock date/time; never tz_convert, which
    would shift the instant through UTC and could move a date across a
    midnight boundary) followed by .normalize() (drops any intraday
    time-of-day). This makes a tz-aware-vs-naive mismatch, or an intraday
    timestamp, compare safely without ever fabricating a future
    observation or moving a value to an earlier trading date. A
    non-DatetimeIndex on either side fails safe (returns all-unavailable)
    rather than raising.

    Uses the SAME coerced/validated Close series as acquisition validation
    and the run-level aggregate return (_coerce_benchmark_close) — never a
    different, unfiltered series (Finding B).

    Returns (aligned_close, available) — both indexed by `stock_index`.
    `available[i]` is True only when `aligned_close[i]` is a genuine,
    non-stale, forward-filled-at-most benchmark observation; callers must
    never trust `aligned_close[i]` when `available[i]` is False, even
    though pandas may still show a (stale or NaN) numeric value there.
    """
    aligned = pd.Series(np.nan, index=stock_index, dtype=float)
    available = pd.Series(False, index=stock_index, dtype=bool)
    if not isinstance(stock_index, pd.DatetimeIndex) or not isinstance(benchmark_df.index, pd.DatetimeIndex):
        return aligned, available

    numeric_close, _, _ = _coerce_benchmark_close(benchmark_df)
    finite_close = numeric_close[np.isfinite(numeric_close) & (numeric_close > 0)]
    # Dedupe in the ORIGINAL row order first (keep="last" == the last row
    # for a given date as the provider returned it), THEN sort — sorting
    # first and deduping after would make the kept value depend on
    # pandas' sort-stability for tied keys, which is not guaranteed.
    finite_close = finite_close[~finite_close.index.duplicated(keep="last")].sort_index()
    if finite_close.empty:
        return aligned, available

    norm_bench_index = _normalize_trading_dates(finite_close.index)
    norm_stock_index = _normalize_trading_dates(stock_index)
    # Re-dedupe/re-sort AFTER normalizing — stripping tz/intraday
    # components can newly collapse two distinct raw timestamps onto the
    # same calendar date; keep the same deterministic "last wins" rule,
    # applied again in normalized-index order.
    normalized_bench = pd.Series(finite_close.to_numpy(), index=norm_bench_index)
    normalized_bench = normalized_bench[~normalized_bench.index.duplicated(keep="last")].sort_index()

    # searchsorted(..., side="right") - 1 finds, for each (normalized)
    # stock date, the position of the latest (normalized) benchmark date
    # <= it — forward-fill only, by construction; a stock date before
    # normalized_bench's first entry gets -1 (no source position), never
    # a later (future) one.
    positions = normalized_bench.index.searchsorted(norm_stock_index, side="right") - 1
    valid_pos = positions >= 0
    if not valid_pos.any():
        return aligned, available

    src_dates = normalized_bench.index[positions[valid_pos]]
    aligned.iloc[valid_pos] = normalized_bench.iloc[positions[valid_pos]].to_numpy()
    gap_days = (norm_stock_index[valid_pos] - src_dates).days
    available.iloc[valid_pos] = gap_days <= BENCHMARK_ALIGNMENT_MAX_STALE_DAYS
    return aligned, available


def _normalize_trading_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Normalize a DatetimeIndex to tz-naive, midnight-normalized calendar
    dates — a safe common comparison basis for cross-market/cross-tz
    alignment (Finding G). Both a stock's and a benchmark's trading dates
    represent a calendar day, not a precise instant, so:

    - tz_localize(None) strips a timezone while PRESERVING the displayed
      wall-clock date/time exactly as-is — never tz_convert(None), which
      would first convert through UTC and could shift the displayed date
      across a midnight boundary (that would corrupt the calendar-date
      comparison this function exists to make safe).
    - .normalize() drops any intraday time-of-day component (sets it to
      midnight), so an intraday timestamp compares purely by date.

    A tz-naive, midnight-only index is returned unchanged (both are
    already no-ops in that case).
    """
    idx = index
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def _new_job_identity(*, market: str, universe_id: str, horizon: str,
                      benchmark: str, total: int, trigger_type: str,
                      requested_by: str | None = None) -> dict:
    """Build the immutable identity record for one validation job.

    Created exactly once per run (under _status_lock, at claim time) and
    never relabeled afterwards — progress fields (processed/current_symbol/
    status/updated_at/completed_at/failure_*) are the only mutable fields.
    """
    now = datetime.now(timezone.utc).isoformat()
    return {
        "job_id": str(uuid.uuid4()),
        "market": market,
        "universe_id": universe_id,
        "universe_version": UNIVERSE_VERSION.get(universe_id, "unknown"),
        "benchmark": benchmark,
        "horizon": horizon,
        "started_at": now,
        "completed_at": None,
        "status": "running",
        "processed": 0,
        "total": total,
        "current_symbol": None,
        "source_commit": os.getenv("RAILWAY_GIT_COMMIT_SHA"),
        "model_version": VALIDATION_MODEL_VERSION,
        "methodology_version": VALIDATION_METHODOLOGY_VERSION,
        # `data_cutoff` is deliberately None at claim time — `now` here is
        # the claim/run-start timestamp, not an observed market-data
        # cutoff, and reporting it as one would be false: this job pulls
        # each symbol's own history independently over the run's
        # lifetime, so no single instant is "the" data cutoff for every
        # symbol. `data_cutoff_basis` states plainly that no real cutoff
        # was captured, rather than silently fabricating one from a
        # nearby but unrelated timestamp (e.g. the benchmark fetch time).
        # Kept as an explicit (not omitted) key for any existing consumer
        # that already reads it.
        "data_cutoff": None,
        "data_cutoff_basis": "not_captured",
        "requested_by": requested_by,
        "trigger_type": trigger_type,
        "created_at": now,
        "updated_at": now,
        "failure_code": None,
        "failure_message": None,
    }


def claim_validation_job(horizon: str, universe: str, trigger_type: str = "api",
                         requested_by: str | None = None) -> dict | None:
    """Atomically claim the single validation-run slot.

    Returns the new job identity, or None if a run is already active. The
    identity is created under _status_lock so there is no window in which
    a run exists without its market/universe/horizon binding.
    """
    _require_known_universe(universe)
    universe_map = {"nifty100": NIFTY_100, "midcap": NSE_MIDCAP, "us": US_BASKET}
    n_stocks = len(universe_map[universe])
    benchmark = "^GSPC" if universe == "us" else "^NSEI"
    market = UNIVERSE_MARKET[universe]
    with _status_lock:
        if _run_status["running"]:
            return None
        job = _new_job_identity(
            market=market, universe_id=universe, horizon=horizon,
            benchmark=benchmark, total=n_stocks, trigger_type=trigger_type,
            requested_by=requested_by,
        )
        _run_status.update({
            "running": True, "progress": 0, "total": n_stocks,
            "started_at": job["started_at"],
            "log": [f"Starting {horizon}-term validation on {universe} ({n_stocks} stocks)…"],
            "job": job,
        })
        return job


# ── Postgres helpers ──────────────────────────────────────────────────────────

def _pg_conn():
    """Open a single psycopg connection (no pool needed — called under _db_lock)."""
    import psycopg
    # prepare_threshold=None disables server-side prepared statements, preventing
    # the '_pg3_0 already exists' error that occurs when Postgres retains prepared
    # statement names across connections on the same backend process.
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, prepare_threshold=None)


_PG_SCHEMA = """
CREATE TABLE IF NOT EXISTS val_runs (
    id          BIGSERIAL PRIMARY KEY,
    run_at      TIMESTAMPTZ NOT NULL,
    horizon     TEXT NOT NULL,
    n_stocks    INTEGER,
    n_signals   INTEGER,
    summary     JSONB
);

-- Table may already exist from before multi-universe support — add the
-- column idempotently rather than relying on CREATE TABLE IF NOT EXISTS,
-- which is a no-op (and so never adds new columns) on an existing table.
ALTER TABLE val_runs ADD COLUMN IF NOT EXISTS universe TEXT NOT NULL DEFAULT 'nifty100';

-- The DEFAULT above blindly labels every pre-existing row 'nifty100',
-- including rows that were actually run for 'midcap'/'us' before this
-- column existed. Re-derive the true value from the JSON already stored
-- in summary at insert time (run_validation() has always written
-- metrics["universe"]). Idempotent — re-running this is a harmless no-op
-- for rows whose default already happens to match their real universe.
UPDATE val_runs SET universe = COALESCE(summary->>'universe', 'nifty100') WHERE universe = 'nifty100';

CREATE TABLE IF NOT EXISTS val_signals (
    id                BIGSERIAL PRIMARY KEY,
    run_id            BIGINT REFERENCES val_runs(id) ON DELETE CASCADE,
    symbol            TEXT NOT NULL,
    horizon           TEXT NOT NULL,
    signal_date       DATE NOT NULL,
    composite_score   REAL,
    tech_score        REAL,
    rs_score          REAL,
    obv_score         REAL,
    mfi_score         REAL,
    predicted         TEXT,
    fwd_return_pct    REAL,
    nifty_fwd_ret_pct REAL,
    alpha_pct         REAL,
    actual_direction  TEXT,
    correct           SMALLINT
);

CREATE INDEX IF NOT EXISTS idx_val_signals_run   ON val_signals(run_id, horizon);
CREATE INDEX IF NOT EXISTS idx_val_signals_sym   ON val_signals(symbol, horizon);
CREATE INDEX IF NOT EXISTS idx_val_signals_score ON val_signals(composite_score);

-- No PII here, but closing the Supabase "RLS disabled" finding for every
-- public table — this connects as `postgres`, which has BYPASSRLS by
-- default, so our own access is unaffected. Idempotent.
ALTER TABLE val_runs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE val_signals ENABLE ROW LEVEL SECURITY;

-- V-SCHED1B — durable validation scheduling ledger foundation (inert; not
-- called by any production code path yet, see run_validation()'s module
-- docstring and _validation_schedule_loop in api/main.py, both unchanged
-- by this phase). Three deliberately separate entities — see the V-SCHED1A2
-- forensic report for why a single-table model was rejected: a canonical
-- scheduled slot cannot itself represent multiple auditable retry attempts,
-- and neither can represent the pre-existing system-wide one-run-at-a-time
-- rule, which spans every horizon/universe/manual trigger, not one slot.

CREATE TABLE IF NOT EXISTS validation_schedule_slots (
    id               BIGSERIAL PRIMARY KEY,
    horizon          TEXT NOT NULL,
    universe         TEXT NOT NULL,
    scheduled_slot   TIMESTAMPTZ NOT NULL,
    schedule_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'due'
                     CHECK (status IN ('due','running','completed','failed','skipped','abandoned')),
    -- The attempt currently entitled to transition this slot. Set the
    -- moment an attempt is created (in the SAME transaction as the slot's
    -- due->running move) and cleared the moment that attempt reaches any
    -- terminal state. A second, older attempt for this slot (e.g. one
    -- whose lease was reclaimed) can never mutate the slot once this no
    -- longer points at it — ownership is checked explicitly, not inferred
    -- from slot status alone, since multiple historical attempts can
    -- exist for one slot across retries.
    active_attempt_id BIGINT,
    created_at       TIMESTAMPTZ NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL,
    UNIQUE (horizon, universe, scheduled_slot, schedule_version)
);

CREATE INDEX IF NOT EXISTS idx_vss_status ON validation_schedule_slots(status);
CREATE INDEX IF NOT EXISTS idx_vss_lookup ON validation_schedule_slots(horizon, universe, scheduled_slot);

CREATE TABLE IF NOT EXISTS validation_schedule_attempts (
    id                  BIGSERIAL PRIMARY KEY,
    slot_id             BIGINT REFERENCES validation_schedule_slots(id),
    -- Durable target identity, stored on the attempt itself (not just
    -- reachable via slot_id) so a manual attempt (slot_id NULL) still has
    -- an auditable, queryable horizon/universe, and so a scheduled/
    -- catchup attempt's target can be verified to match its slot's target
    -- without a join at write time. For scheduled/catchup attempts this
    -- is copied from the slot at creation and never diverges from it —
    -- enforced in the same transaction that creates the attempt.
    horizon             TEXT NOT NULL,
    universe            TEXT NOT NULL,
    attempt_number      INTEGER NOT NULL,
    trigger_type        TEXT NOT NULL CHECK (trigger_type IN ('scheduler','catchup','manual')),
    status              TEXT NOT NULL DEFAULT 'claimed'
                        CHECK (status IN ('claimed','running','completed','failed','abandoned')),
    -- Populated only once this attempt validly reaches 'running' under a
    -- currently-held lease — an audit trail of who actually executed it,
    -- independent of lease_fencing_token's role in gating writes.
    lease_owner         TEXT,
    lease_fencing_token BIGINT,
    started_at          TIMESTAMPTZ,
    heartbeat_at        TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    result_run_id       BIGINT REFERENCES val_runs(id),
    failure_category    TEXT,
    failure_summary     TEXT,
    created_at          TIMESTAMPTZ NOT NULL,
    updated_at          TIMESTAMPTZ NOT NULL,
    UNIQUE (slot_id, attempt_number)
);

CREATE INDEX IF NOT EXISTS idx_vsa_slot   ON validation_schedule_attempts(slot_id);
CREATE INDEX IF NOT EXISTS idx_vsa_horiz  ON validation_schedule_attempts(horizon, universe);
CREATE INDEX IF NOT EXISTS idx_vsa_trig   ON validation_schedule_attempts(trigger_type);
CREATE INDEX IF NOT EXISTS idx_vsa_status ON validation_schedule_attempts(status);
-- One val_runs row may belong to at most one attempt; multiple NULLs
-- (uncompleted attempts) remain allowed under a standard unique index
-- in both dialects. Named so complete_attempt_with_result can map a
-- violation on THIS specific constraint (and no other) to the
-- explicit 'result_already_linked' conflict.
CREATE UNIQUE INDEX IF NOT EXISTS idx_vsa_result_unique ON validation_schedule_attempts(result_run_id);

CREATE TABLE IF NOT EXISTS validation_execution_leases (
    resource_key   TEXT PRIMARY KEY,
    lease_owner    TEXT,
    fencing_token  BIGINT NOT NULL DEFAULT 0,
    acquired_at    TIMESTAMPTZ,
    heartbeat_at   TIMESTAMPTZ,
    expires_at     TIMESTAMPTZ,
    -- The single attempt currently globally admitted under this lease.
    -- NULL means the lease owns no active validation attempt and a new
    -- one may be admitted. Deliberately NOT reset to NULL by lease
    -- reclaim (acquire_validation_execution_lease) — a reclaim after
    -- expiry must surface a stale admitted attempt to the new owner
    -- (recovery_required in its return value) rather than silently
    -- losing that identity; only recover_stale_active_attempt() or a
    -- normal terminal transition may clear it. No FK to
    -- validation_schedule_attempts(id) — application-enforced only,
    -- since this column is set exclusively by create_schedule_attempt/
    -- create_manual_attempt to their own newly-inserted row's id.
    active_attempt_id BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL
);

INSERT INTO validation_execution_leases (resource_key, fencing_token, updated_at)
VALUES ('validation-global', 0, now())
ON CONFLICT (resource_key) DO NOTHING;

ALTER TABLE validation_schedule_slots    ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_schedule_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE validation_execution_leases  ENABLE ROW LEVEL SECURITY;
"""

# Synced with official NSE Nifty 100 index (June 2026) + popular large-caps
# removed from the index that are still worth validating.
NIFTY_100 = [
    # ── Core Nifty 100 (official, June 2026) ─────────────────────────────────
    "RELIANCE", "TCS", "HDFCBANK", "ICICIBANK", "INFY", "HINDUNILVR", "ITC",
    "SBIN", "BHARTIARTL", "BAJFINANCE", "KOTAKBANK", "LT", "AXISBANK",
    "MARUTI", "HCLTECH", "SUNPHARMA", "TITAN", "WIPRO", "ULTRACEMCO",
    "NTPC", "POWERGRID", "ONGC", "M&M", "ASIANPAINT",
    "NESTLEIND", "BAJAJFINSV", "TECHM", "DRREDDY", "CIPLA",
    "JSWSTEEL", "TATASTEEL", "HINDALCO", "DIVISLAB", "BRITANNIA",
    "GODREJCP", "TATACONSUM", "EICHERMOT",
    "BAJAJ-AUTO", "TVSMOTOR", "ADANIENT", "ADANIPORTS",
    "SIEMENS", "HAL", "DLF",
    "HDFCLIFE", "SBILIFE", "CHOLAFIN", "MUTHOOTFIN",
    "TORNTPHARM", "APOLLOHOSP",
    "ETERNAL", "DMART", "INDHOTEL", "TRENT",
    "COALINDIA", "GAIL", "BPCL", "IOC",
    "BANKBARODA", "VEDL", "PIDILITIND", "RECLTD",
    "SHREECEM", "GRASIM", "MOTHERSON",
    # Newly added to Nifty 100
    "ABB", "ADANIENSOL", "ADANIGREEN", "ADANIPOWER", "AMBUJACEM",
    "BAJAJHLDNG", "BEL", "BOSCHLTD", "CANBK", "CGPOWER",
    "CUMMINSIND", "ENRIN", "HDFCAMC", "HINDZINC", "HYUNDAI",
    "INDIGO", "IRFC", "JINDALSTEL", "JIOFIN", "LODHA",
    "LTM", "MAXHEALTH", "MAZDOCK", "PFC", "PNB",
    "SHRIRAMFIN", "SOLARINDS", "TATACAP", "TATAPOWER",
    "TMCV", "TMPV", "UNIONBANK", "UNITDSPR", "VAML",
    "VBL", "VOGL", "ZYDUSLIFE",
    # ── Removed from Nifty 100 but still large-cap & worth validating ────────
    "HEROMOTOCO", "BHEL", "OBEROIRLTY", "ICICIPRULI", "LUPIN",
    "SRF", "PIIND", "IRCTC", "PERSISTENT", "MPHASIS",
    "LTIM", "OFSS", "NAUKRI", "HINDPETRO", "INDUSINDBK",
    "FEDERALBNK", "IDFCFIRSTB", "BANDHANBNK", "NMDC", "SAIL",
    "HAVELLS", "VOLTAS", "SUPREMEIND", "LICHSGFIN", "CONCOR",
    "DELHIVERY", "NYKAA", "PAYTM", "POLICYBZR", "DIXON",
    "DABUR", "MARICO",
]

# ── Mid-cap NSE (top 100 non-Nifty-100 stocks by market cap, June 2026) ──────
NSE_MIDCAP = [
    "KALYANKJIL", "LLOYDSME", "GSPL", "KPITTECH", "COFORGE", "LTTS", "ZENSARTECH",
    "MPHASIS", "PERSISTENT", "DIXON", "TATAELXSI", "CYIENT", "MASTEK", "RATEGAIN",
    "ANGELONE", "CDSL", "BSE", "MOTILALOFS", "IIFL", "MUTHOOTFIN",
    "CANFINHOME", "AAVAS", "HOMEFIRST", "CREDITACC", "UGROCAP",
    "APOLLOTYRE", "MRF", "BALKRISIND", "TIINDIA", "CEATLTD",
    "APLAPOLLO", "RATNAMANI", "JINDALSAW", "SHYAMMETL", "WELSPUNLIV",
    "DEEPAKNTR", "NAVINFLUOR", "AARTIIND", "VINATIORGA", "ROSSARI", "FINEORG",
    "JKCEMENT", "RAMCOCEM", "HEIDELBERG", "DALBHARAT", "ORIENTCEM",
    "ATGL", "GUJGASLTD", "IGL", "MGL", "PETRONET",
    "ASTERDM", "NH", "RAINBOW", "METROPOLIS", "LALPATHLAB", "THYROCARE",
    "HAL", "BEL", "MIDHANI", "MAZDOCK", "COCHINSHIP", "DATAPATTNS", "MTARTECH",
    "GODREJPROP", "PRESTIGE", "SOBHA", "BRIGADE", "PHOENIXLTD",
    "LEMONTREE", "CHALET", "INDHOTEL", "EIHOTEL",
    "JUBLFOOD", "DEVYANI", "SAPPHIRE", "WESTLIFE",
    "PVRINOX", "SAREGAMA", "NAZARA", "TIPSFILMS",
    "PAGEIND", "TRIDENT", "VTL", "KPRMILL", "ARVIND",
    "UPL", "PIIND", "DHANUKA", "BAYERCROP", "RALLIS", "SUMICHEM",
    "DELHIVERY", "BLUEDART", "TCIEXP", "VRLLOG", "ALLCARGO", "CONCOR",
    "ASIANPAINT", "BERGEPAINT", "KANSAINER", "INDIGOPNTS",
    "KNRCON", "ASHOKA", "PNCINFRA", "HGINFRA", "IRB",
    "GRINDWELL", "SCHAEFFLER", "TIMKEN", "ELGIEQUIP", "THERMAX",
]

# ── US validation basket — 50 stocks covering all major S&P 500 sectors ──────
US_BASKET = [
    # Mega-cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
    # Semiconductors
    "TSM", "AVGO", "AMD", "QCOM", "INTC",
    # Cloud / SaaS
    "CRM", "NOW", "SNOW", "DDOG",
    # Fintech / Finance
    "JPM", "BAC", "GS", "V", "MA", "PYPL",
    # Healthcare / Pharma
    "JNJ", "UNH", "PFE", "ABBV", "LLY", "MRK",
    # Consumer
    "AMZN", "TSLA", "HD", "MCD", "NKE", "COST",
    # Energy
    "XOM", "CVX", "COP",
    # Industrials / Defence
    "LMT", "RTX", "BA", "GE",
    # Utilities / Realty
    "NEE", "AMT", "PLD",
    # ETF benchmarks (used for alpha calculation)
]
# deduplicate (AMZN appears in two sectors above)
US_BASKET = list(dict.fromkeys(US_BASKET))

HORIZON_DAYS  = {"short": 5,  "medium": 21, "long": 63}
HORIZON_STEP  = {"short": 5,  "medium": 10, "long": 21}
HORIZON_THRESHOLDS = {"short": 0.02, "medium": 0.04, "long": 0.10}
HORIZON_PERIOD = {"short": "3y", "medium": "5y", "long": "7y"}

# Per-horizon thresholds — long horizon covers 7 years of data including bear markets
# where regime_adj is often 0 or -5, so the composite ceiling is lower. Keeping a
# single 65 threshold produced 0 BUY signals for long horizon.
# Must match prediction_engine.py thresholds exactly — validation measures the live model
BUY_THRESHOLD  = {"short": 60, "medium": 60, "long": 60}
SELL_THRESHOLD = {"short": 45, "medium": 45, "long": 45}

# 2026-07-17: confidence-band calibration audit. Production's displayed
# "confidence" (prediction_engine.py's _composite_signal, the one that
# gates Daily Picks' 25% cutoff) has never been checked against realized
# hit rate — only composite_score has a hit-rate table (see `buckets` in
# _compute_metrics below). confidence is a pure, deterministic function of
# composite_score + predicted signal, so it can be reconstructed here from
# data this walk-forward backtest already computes, without touching the
# live prediction path or any of its consumers. Mirrors
# prediction_engine.py's exact formula — must be kept in sync if that
# formula ever changes.
def _confidence_from_composite(composite_r: float, predicted: str) -> int:
    if predicted == "BUY":
        # 2026-07-17: kept in sync with prediction_engine.py's BUY branch —
        # /20 (not /40), rescaled over the empirically observed [60,80]
        # composite range this validator's own confidence_buckets output
        # was the evidence for. See that file's comment for the full
        # reasoning; this file's job is to stay identical, not re-derive it.
        return round(max(0, min(100, (composite_r - 60) / 20 * 100)))
    if predicted == "SELL":
        # 2026-07-17: kept in sync with prediction_engine.py's SELL branch —
        # /20 (not /45), rescaled over the empirically observed [25,45)
        # composite range (132,354 real SELL signals queried; scores
        # essentially never go below ~25). Unlike BUY, hit rate is flat
        # (~47-52%) across every bucket — this fixes an unambiguous range
        # bug, it does not newly claim SELL confidence predicts accuracy.
        return round(max(0, min(100, (45 - composite_r) / 20 * 100)))
    # HOLD: deliberately NOT rescaled — 600,121 real HOLD signals queried,
    # hit rate flat ~43-46% across the entire range including right at the
    # assumed peak (52). The formula's whole premise (confidence peaks at
    # the midpoint) isn't supported by data, not just mis-scaled; fixing
    # that is a separate, larger investigation, not a range tweak.
    return max(0, min(100, 50 - int(abs(composite_r - 52) * 2)))  # HOLD


# Explicit universe -> market map (Phase GPI-1) and the single source of
# truth for which universe strings run_validation() accepts at all. Passed
# down to _backtest_stock/_resolve_yahoo_symbol so routing never has to be
# re-derived from a symbol's own shape — see _resolve_yahoo_symbol's
# docstring for the defect this replaced. An unrecognized universe string
# is rejected by run_validation() before any work happens (see
# _require_known_universe) — it is never silently remapped to NIFTY_100,
# and market is never silently defaulted to "IN".
UNIVERSE_MARKET = {"nifty100": "IN", "midcap": "IN", "us": "US"}


def _require_known_universe(universe: str) -> None:
    """
    Fail-closed universe gate for run_validation(). Matches the API layer's
    existing contract exactly (api/routers/validation.py's
    Literal["nifty100", "midcap", "us"] query param) — case-sensitive, no
    whitespace trimming, no aliasing. Every current caller (the validation
    router and api/main.py's scheduler, which only ever iterates the literal
    tuple ("nifty100", "midcap", "us")) already only ever passes one of
    these three exact strings, so this can never fire in normal operation —
    it exists purely so a malformed or unknown universe can never silently
    fall back to NIFTY_100 or silently default its market to "IN".
    """
    if universe not in UNIVERSE_MARKET:
        raise ValueError(
            f"run_validation: unsupported universe {universe!r} — must be exactly one "
            f"of {sorted(UNIVERSE_MARKET)} (case-sensitive; no whitespace or alias "
            f"normalization)"
        )


# ── Database ──────────────────────────────────────────────────────────────────

def _get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(_DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _get_ledger_sqlite_conn() -> sqlite3.Connection:
    """V-SCHED1B ledger-only connection helper. SQLite does not enforce
    declared FOREIGN KEY constraints unless `PRAGMA foreign_keys = ON` is
    issued per-connection (it is OFF by default) — enabling it here,
    scoped to the ledger's own connections only, rather than on the
    shared `_get_sqlite_conn()` used by every pre-existing val_runs/
    val_signals code path in this module, deliberately avoids widening
    this correction's blast radius to unrelated, already-established
    behavior outside V-SCHED1B's authorized file allowlist.

    `isolation_level=None` puts the driver in true autocommit mode so
    ledger compound operations can issue an explicit `BEGIN IMMEDIATE`
    (acquiring SQLite's write lock up front, before any read used to
    decide what to write) and an explicit COMMIT/ROLLBACK spanning
    multiple statements — the implicit, deferred-lock transaction the
    sqlite3 module would otherwise open automatically is not strong
    enough for the atomic, lock-then-decide compound transitions this
    module needs."""
    conn = sqlite3.connect(_DB_PATH, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


_db_initialised = False

def _init_db():
    global _db_initialised
    if _db_initialised:
        return
    with _db_lock:
        if _db_initialised:
            return
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                conn.execute(_PG_SCHEMA)
            finally:
                conn.close()
        else:
            with _get_sqlite_conn() as c:
                c.executescript("""
                CREATE TABLE IF NOT EXISTS val_runs (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at      TEXT NOT NULL,
                    horizon     TEXT NOT NULL,
                    n_stocks    INTEGER,
                    n_signals   INTEGER,
                    summary     TEXT,
                    universe    TEXT NOT NULL DEFAULT 'nifty100'
                );
                CREATE TABLE IF NOT EXISTS val_signals (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id            INTEGER REFERENCES val_runs(id),
                    symbol            TEXT NOT NULL,
                    horizon           TEXT NOT NULL,
                    signal_date       TEXT NOT NULL,
                    composite_score   REAL,
                    tech_score        REAL,
                    rs_score          REAL,
                    obv_score         REAL,
                    mfi_score         REAL,
                    predicted         TEXT,
                    fwd_return_pct    REAL,
                    nifty_fwd_ret_pct REAL,
                    alpha_pct         REAL,
                    actual_direction  TEXT,
                    correct           INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_vs_symbol ON val_signals(symbol, horizon);
                CREATE INDEX IF NOT EXISTS idx_vs_run    ON val_signals(run_id, horizon);
                CREATE INDEX IF NOT EXISTS idx_vs_score  ON val_signals(composite_score);
                """)
                # SQLite's CREATE TABLE IF NOT EXISTS is a no-op on a table that
                # already exists from before multi-universe support — add the
                # column explicitly, ignoring "duplicate column" on repeat runs.
                try:
                    c.execute("ALTER TABLE val_runs ADD COLUMN universe TEXT NOT NULL DEFAULT 'nifty100'")
                except sqlite3.OperationalError:
                    pass
                # Re-derive true universe for pre-existing rows from the JSON
                # already stored in summary — see matching Postgres comment above.
                c.execute(
                    "UPDATE val_runs SET universe = "
                    "COALESCE(json_extract(summary, '$.universe'), 'nifty100') "
                    "WHERE universe = 'nifty100'"
                )
                # V-SCHED1B — SQLite mirror of the Postgres ledger schema above.
                c.executescript("""
                CREATE TABLE IF NOT EXISTS validation_schedule_slots (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    horizon          TEXT NOT NULL,
                    universe         TEXT NOT NULL,
                    scheduled_slot   TEXT NOT NULL,
                    schedule_version TEXT NOT NULL,
                    status           TEXT NOT NULL DEFAULT 'due'
                                     CHECK (status IN ('due','running','completed','failed','skipped','abandoned')),
                    active_attempt_id INTEGER,
                    created_at       TEXT NOT NULL,
                    updated_at       TEXT NOT NULL,
                    UNIQUE (horizon, universe, scheduled_slot, schedule_version)
                );

                CREATE INDEX IF NOT EXISTS idx_vss_status ON validation_schedule_slots(status);
                CREATE INDEX IF NOT EXISTS idx_vss_lookup ON validation_schedule_slots(horizon, universe, scheduled_slot);

                CREATE TABLE IF NOT EXISTS validation_schedule_attempts (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    slot_id             INTEGER REFERENCES validation_schedule_slots(id),
                    horizon             TEXT NOT NULL,
                    universe            TEXT NOT NULL,
                    attempt_number      INTEGER NOT NULL,
                    trigger_type        TEXT NOT NULL CHECK (trigger_type IN ('scheduler','catchup','manual')),
                    status              TEXT NOT NULL DEFAULT 'claimed'
                                        CHECK (status IN ('claimed','running','completed','failed','abandoned')),
                    lease_owner         TEXT,
                    lease_fencing_token INTEGER,
                    started_at          TEXT,
                    heartbeat_at        TEXT,
                    completed_at        TEXT,
                    result_run_id       INTEGER REFERENCES val_runs(id),
                    failure_category    TEXT,
                    failure_summary     TEXT,
                    created_at          TEXT NOT NULL,
                    updated_at          TEXT NOT NULL,
                    UNIQUE (slot_id, attempt_number)
                );

                CREATE INDEX IF NOT EXISTS idx_vsa_slot   ON validation_schedule_attempts(slot_id);
                CREATE INDEX IF NOT EXISTS idx_vsa_horiz  ON validation_schedule_attempts(horizon, universe);
                CREATE INDEX IF NOT EXISTS idx_vsa_trig   ON validation_schedule_attempts(trigger_type);
                CREATE INDEX IF NOT EXISTS idx_vsa_status ON validation_schedule_attempts(status);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_vsa_result_unique ON validation_schedule_attempts(result_run_id);

                CREATE TABLE IF NOT EXISTS validation_execution_leases (
                    resource_key   TEXT PRIMARY KEY,
                    lease_owner    TEXT,
                    fencing_token  INTEGER NOT NULL DEFAULT 0,
                    acquired_at    TEXT,
                    heartbeat_at   TEXT,
                    expires_at     TEXT,
                    active_attempt_id INTEGER,
                    updated_at     TEXT NOT NULL
                );
                """)
                c.execute(
                    "INSERT OR IGNORE INTO validation_execution_leases "
                    "(resource_key, fencing_token, updated_at) VALUES (?, 0, ?)",
                    ("validation-global", datetime.now(timezone.utc).isoformat()),
                )
        _db_initialised = True


def init_db():
    """
    Public entry point for app startup. _init_db() used to only run lazily
    on first call to a handful of functions (get_latest_results,
    get_per_stock_results, etc.), never at boot like every other table's
    schema setup in postgres_store.py. That meant a schema change here
    (e.g. adding ENABLE ROW LEVEL SECURITY) would sit deployed but inert
    until something happened to hit one of those lazy call sites — exactly
    what happened with the RLS fix, which only took effect after manually
    curling /api/validation/results. Call this from main.py's lifespan so
    schema changes apply on deploy like everywhere else.
    """
    _init_db()


# ── Scoring (uses ONLY data at index i — no look-ahead) ──────────────────────

def _score_at(df: pd.DataFrame, i: int, benchmark_close: pd.Series | None, fund_score: float, regime_adj: float) -> dict:
    """
    Compute composite score at row i using only df[:i+1].
    Returns dict with composite_score and sub-scores.
    """
    row = df.iloc[i]

    # ── Technical score (mirrors get_signal_summary logic) ────────────────────
    tech = 50.0
    rsi = row.get("rsi_14", np.nan)
    if pd.notna(rsi):
        if rsi < 30:    tech += 15
        elif rsi < 45:  tech += 7
        elif rsi > 70:  tech -= 15
        elif rsi > 60:  tech -= 7

    macd_diff = row.get("macd_diff", np.nan)
    if pd.notna(macd_diff):
        tech += 12 if macd_diff > 0 else -12

    close = row.get("Close", np.nan)
    ema200 = row.get("ema_200", np.nan)
    ema20  = row.get("ema_20",  np.nan)
    ema50  = row.get("ema_50",  np.nan)
    if pd.notna(close) and pd.notna(ema200):
        tech += 10 if close > ema200 else -10
    if pd.notna(ema20) and pd.notna(ema50):
        tech += 8 if ema20 > ema50 else -8

    adx     = row.get("adx", np.nan)
    adx_pos = row.get("adx_pos", np.nan)
    adx_neg = row.get("adx_neg", np.nan)
    if pd.notna(adx) and adx > 25 and pd.notna(adx_pos) and pd.notna(adx_neg):
        tech += 10 if adx_pos > adx_neg else -10

    bb_pct = row.get("bb_pct", np.nan)
    if pd.notna(bb_pct):
        if bb_pct < 0.1:  tech += 8
        elif bb_pct > 0.9: tech -= 8

    stoch_rsi = row.get("stoch_rsi", np.nan)
    if pd.notna(stoch_rsi):
        if stoch_rsi < 0.2:  tech += 7
        elif stoch_rsi > 0.8: tech -= 7

    wr = row.get("williams_r", np.nan)
    if pd.notna(wr):
        if wr < -80:  tech += 6
        elif wr > -20: tech -= 6

    cci = row.get("cci", np.nan)
    if pd.notna(cci):
        if cci < -100: tech += 6
        elif cci > 100: tech -= 6

    tech = max(0.0, min(100.0, tech))

    # ── Relative strength vs benchmark (1M, 3M) ─────────────────────────────
    # Benchmark is Nifty 50 for India universes, S&P 500 for the US universe
    # (see run_validation's UNIVERSE_MARKET / benchmark_ticker) — this
    # function itself is market-agnostic, it just compares against whichever
    # benchmark_close series the caller supplied.
    rs_score = 50.0
    if benchmark_close is not None and pd.notna(close):
        close_series = df["Close"].iloc[:i+1]
        for days in (21, 63):
            if i >= days and len(benchmark_close) > 0:
                try:
                    benchmark_at_i  = benchmark_close.iloc[min(i, len(benchmark_close)-1)]
                    benchmark_at_im = benchmark_close.iloc[max(0, min(i-days, len(benchmark_close)-1))]
                    stock_ret = (close_series.iloc[-1] - close_series.iloc[-days]) / close_series.iloc[-days] * 100 if close_series.iloc[-days] != 0 else 0
                    benchmark_ret = (benchmark_at_i - benchmark_at_im) / benchmark_at_im * 100 if benchmark_at_im != 0 else 0
                    rs = stock_ret - benchmark_ret
                    if rs > 10:    rs_score += 10
                    elif rs > 4:   rs_score += 5
                    elif rs < -10: rs_score -= 10
                    elif rs < -4:  rs_score -= 5
                except Exception:
                    pass
    rs_score = max(0.0, min(100.0, rs_score))

    # ── OBV trend (fixed: raw slope, no sign flip) ────────────────────────────
    obv_score = 50.0
    obv_col = df.get("obv") if hasattr(df, "get") else None
    if "obv" in df.columns and i >= 20:
        obv_series = df["obv"].iloc[:i+1]
        obv_slope = float(obv_series.iloc[-1] - obv_series.iloc[-20])
        close_20d_base = df["Close"].iloc[i-20]
        price_ret = (close - close_20d_base) / close_20d_base if close_20d_base != 0 else 0
        if obv_slope > 0 and price_ret > 0:
            obv_score = 70.0
        elif obv_slope > 0 and price_ret < 0:
            obv_score = 65.0
        elif obv_slope < 0 and price_ret > 0:
            obv_score = 35.0
        elif obv_slope < 0 and price_ret < 0:
            obv_score = 38.0

    # ── MFI (fixed: standard formula) ────────────────────────────────────────
    mfi_score = 50.0
    if all(c in df.columns for c in ("High", "Low", "Close", "Volume")) and i >= 14:
        window = df.iloc[max(0, i-30):i+1]
        tp = (window["High"] + window["Low"] + window["Close"]) / 3
        rmf = tp * window["Volume"]
        pos = pd.Series(0.0, index=window.index)
        neg = pd.Series(0.0, index=window.index)
        for j in range(1, len(window)):
            if tp.iloc[j] > tp.iloc[j-1]:
                pos.iloc[j] = rmf.iloc[j]
            else:
                neg.iloc[j] = rmf.iloc[j]
        pos14 = pos.rolling(14).sum().iloc[-1]
        neg14 = neg.rolling(14).sum().iloc[-1]
        mfi_val = 100 * pos14 / (pos14 + neg14 + 1e-10)
        if mfi_val > 70:   mfi_score = 72.0
        elif mfi_val > 55: mfi_score = 60.0
        elif mfi_val < 30: mfi_score = 30.0
        elif mfi_val < 45: mfi_score = 42.0

    # ── Composite (weights: tech 30%, RS 30%, OBV 20%, MFI 20%) ──────────────
    # Reduced tech weight (IC=-0.037, momentum-chasing after price has moved).
    # Raised RS weight (cross-sectional alpha signal; leading not lagging).
    # Fundamentals blend raised from 30% to 45% (quality / value is more stable).
    composite = (
        tech      * 0.30
        + rs_score  * 0.30
        + obv_score * 0.20
        + mfi_score * 0.20
    )
    # Blend with fundamentals (fixed for the whole stock period)
    composite = composite * 0.55 + fund_score * 0.45
    composite += regime_adj
    composite = max(0.0, min(100.0, composite))

    return {
        "composite": round(composite, 1),
        "tech":      round(tech, 1),
        "rs":        round(rs_score, 1),
        "obv":       round(obv_score, 1),
        "mfi":       round(mfi_score, 1),
    }


def _resolve_yahoo_symbol(symbol: str, market: str) -> str:
    """
    Deterministically resolve a validation-universe symbol to its Yahoo
    Finance ticker for an EXPLICITLY supplied market — never inferred from
    the symbol's own shape (length, presence/absence of a dot, casing).
    `market` must be the caller's already-known universe context
    (run_validation's UNIVERSE_MARKET maps nifty100/midcap -> "IN",
    us -> "US"); this function does not guess it.

    Phase GPI-1: replaces the confirmed defect where a length/punctuation
    heuristic in this function's old body misrouted short NSE symbols
    (INFY, TCS, SBIN, M&M, ...) to bare Yahoo tickers — silently resolving
    some of them to unrelated US-listed instruments (e.g. INFY's own NYSE
    ADR) instead of failing or routing to the correct NSE line.
    """
    if market == "IN":
        return symbol if symbol.endswith(".NS") else f"{symbol}.NS"
    if market == "US":
        # US tickers are used exactly as configured — including any dotted
        # or dashed class-share form (e.g. "BRK.B", "BRK-B") — never
        # suffixed, never rewritten.
        return symbol
    raise ValueError(
        f"_resolve_yahoo_symbol: unsupported market {market!r} for symbol {symbol!r} — "
        f"must be explicitly 'IN' or 'US', never inferred"
    )


# DP-026 remediation (US only — see module docstring note near
# `_backtest_stock`'s fund_score computation for why India cannot receive
# the same treatment). Genuine point-in-time fundamental score built from
# `sec_edgar_adapter.get_fundamentals_as_of()` — SEC EDGAR XBRL facts
# filed on or before the signal's own historical date, never a present-day
# snapshot. Deliberately uses only single-period ratios (ROE, net margin)
# that are computable from ONE as-of snapshot — a genuine revenue-growth
# figure needs the prior fiscal year's revenue as of the SAME cutoff too
# (two independent as-of lookups, correctly ordered), which
# get_fundamentals_as_of()'s current single-snapshot contract does not yet
# provide; DEFERRED, not silently dropped — see DP-031's register entry,
# which now also owns this specific gap; PE was dropped rather than
# approximated because it needs a point-in-time EPS-outstanding-shares
# figure this adapter does not extract at all today.
# DP-033 scoring-policy decision (Phase 6 of the readiness audit): the
# point-in-time US fund_score is NOT a drop-in replacement computing the
# "same" score with better inputs — it uses ROE + net margin only (2
# ratios), while the pre-existing IN/legacy formula uses PE + ROE + revenue
# growth (3 different ratios, none of which is "ROE" in exactly the same
# bucketing). These are NOT semantically equivalent and must not be
# presented as interchangeable historical continuations of one score.
# Decision: Option B — a separately versioned point-in-time scoring
# policy, not a silent replacement of the old one. The old (IN/legacy)
# formula is completely unversioned and untouched; this new formula is
# explicitly named and versioned so every US signal/result can be traced
# to exactly which formula produced it, and so a future formula change
# (e.g. adding revenue growth once a second as-of lookup exists, per
# DP-031) bumps this version rather than silently altering the meaning of
# already-published historical scores.
US_PIT_SCORING_POLICY_VERSION = "us_pit_roe_margin_v1"

def _get_fundamentals_as_of_replay(symbol: str, as_of) -> dict:
    """
    DP-033 architecture correction (2026-07-22, second readiness pass):
    historical replay must NEVER perform acquisition, including on a
    "cache miss" — the prior pass's `_get_fundamentals_as_of_persisted_
    first()` still lazily called `fetch_company_facts()`/`resolve_cik()`
    on first use per process, which is acquisition-during-replay in
    everything but name. This function is genuinely acquisition-free: it
    calls ONLY `sec_pit_store.get_facts_as_of_replay()` (itself backed by
    the persisted `sec_pit_symbol_registry`, never a live CIK resolution)
    — no code path here can reach `services.sec_edgar_adapter`'s live
    functions under any circumstance, including a symbol that was never
    ingested. A never-ingested symbol correctly returns `available:
    False` with a distinct reason, not a lazy live fetch.

    Ingestion is now an entirely separate, explicit, administrative
    operation — see `sec_pit_store.ingest_symbol()`, invoked only by
    dedicated ingestion tooling (scripts/sec_pit_ingest.py), never by
    this module.
    """
    entry = sec_pit_store.get_symbol_registry_entry(symbol)
    if entry is None:
        return {"available": False, "reason": "symbol not ingested — no sec_pit_symbol_registry entry"}

    cik = entry["cik"]
    pruned = sec_pit_store.get_facts_as_of_replay(symbol, as_of)
    fields = sec_edgar_adapter.normalize_fields(pruned)
    has_any = any(v.get("value") is not None for v in fields.values())
    return {
        "available": has_any,
        "fields": fields,
        "reason": None if has_any else "ingested, but no eligible filing as of the signal date",
    }


def _us_fund_score_as_of(symbol: str, as_of) -> tuple[float | None, bool, str | None]:
    """Returns (score, available, reason). `score` is None and `available`
    is False — never a fabricated/neutral value — when the persisted SEC
    EDGAR store has no eligible filing for `symbol` as of `as_of`, or when
    the eligible filing(s) don't contain enough of (net_income,
    shareholders_equity, revenue) to compute either ratio. Caller decides
    the neutral-fallback policy for the unavailable case; this function
    never applies one itself."""
    result = _get_fundamentals_as_of_replay(symbol, as_of)
    if not result.get("available"):
        return None, False, result.get("reason", "unavailable")

    fields = result["fields"]
    net_income = fields.get("net_income", {}).get("value")
    equity     = fields.get("shareholders_equity", {}).get("value")
    revenue    = fields.get("revenue", {}).get("value")

    score = 50.0
    used_any = False
    if net_income is not None and equity:
        roe = net_income / equity
        score += 10 if roe > 0.15 else (-10 if roe < 0 else 0)
        used_any = True
    if net_income is not None and revenue:
        margin = net_income / revenue
        score += 8 if margin > 0.10 else (-8 if margin < 0 else 0)
        used_any = True

    if not used_any:
        return None, False, "eligible filing found but no usable ROE/margin inputs as of this date"
    return max(0.0, min(100.0, score)), True, None


def _backtest_stock(
    symbol: str,
    horizon: str,
    benchmark_df: pd.DataFrame | None,
    market: str,
    *,
    universe: str | None = None,
    _exclusions: list | None = None,
    _window_stats: dict | None = None,
    _diag: dict | None = None,
) -> list[dict]:
    """
    Walk-forward backtest for one stock over HORIZON_PERIOD[horizon].

    Look-ahead bias fix: indicators are recomputed on df.iloc[:i+1] at each
    signal date i — exactly the data that would have been available in real-time.
    No future prices leak into EMA, MACD, OBV, or any other indicator.

    `market` is required and explicit ("IN" or "US") — see
    _resolve_yahoo_symbol's docstring for why this replaced ticker-shape
    inference. `universe` is optional, carried only for failure-diagnostic
    logging (which universe/run this symbol belonged to), not for routing.

    Benchmark evidence integrity (defense-in-depth): run_validation's own
    preflight (_validate_benchmark_acquisition) already fails the whole run
    closed before this function is ever called with unusable benchmark_df —
    but this function does not trust that guarantee for a future/direct
    caller. Every signal date's regime input and benchmark-relative fields
    (nifty_fwd_ret_pct/alpha_pct/actual_direction/correct) require genuine,
    non-stale, non-backward-filled benchmark evidence at that exact date
    (see _align_benchmark_close) — a date without it is skipped entirely
    (never assigned a fabricated flat-0.0/neutral-regime substitute) and
    counted in `_exclusions` (a shared, append-only list the caller may pass
    to aggregate an exclusion count across every backtest call — list.append
    is safe to call concurrently from ThreadPoolExecutor workers under
    CPython's GIL; the caller only needs len() on it afterwards).

    `_window_stats` (Finding D, 2026-07-26 hardening) — an optional dict
    this call populates with `{"considered": int, "benchmark_valid": int}`.
    Unlike `_exclusions`, this is deliberately a PER-CALL, per-worker
    result — run_validation gives each stock its own fresh dict and sums
    them after every future resolves, rather than sharing one mutable
    counter across threads, for a clearer and more portable long-term
    count contract than a single shared append-only list.

    `_diag` (V-FRESH1B) — an optional dict, following the exact same
    per-call/per-worker-owned pattern as `_window_stats` (never a single
    dict shared/mutated across threads — the caller creates one fresh
    dict per symbol before submitting to the pool, and only that
    symbol's own worker thread ever writes to it). Populated with
    exactly one terminal-path classification —
    "fetch_exception" | "empty_data" | "insufficient_data" |
    "calculation_exception" | "processed" — plus, only when the fetched
    dataframe was non-empty, the symbol's own actual first/last input-bar
    dates (`input_first_date`/`input_last_date`, `YYYY-MM-DD` strings).
    This is diagnostic evidence only — it answers "what happened to this
    symbol's data" for future coverage/freshness disclosure, and must
    never be interpreted as a data-through or freshness claim by itself.

    Returns list of signal dicts, empty list on error, insufficient data, or
    an entirely benchmark-unavailable date range (all logged with full
    symbol/market/universe/horizon context so a caller reviewing effective
    sample size can see WHY a requested symbol contributed zero signals —
    see run_validation's n_stocks_with_signals).
    """
    exclusions = _exclusions if _exclusions is not None else []
    window_stats = _window_stats if _window_stats is not None else {}
    window_stats.setdefault("considered", 0)
    window_stats.setdefault("benchmark_valid", 0)
    if _diag is not None:
        _diag.setdefault("terminal_path", None)
        _diag.setdefault("input_first_date", None)
        _diag.setdefault("input_last_date", None)
    yf_sym = _resolve_yahoo_symbol(symbol, market)
    fwd_days  = HORIZON_DAYS[horizon]
    step      = HORIZON_STEP[horizon]
    threshold = HORIZON_THRESHOLDS[horizon]

    # Need ≥200 rows for EMA-200 to be meaningful; start signal loop from row 200
    MIN_WARMUP = 200

    # V-FRESH1B — the fetch call gets its own narrow try/except so a
    # provider-level failure (fetch_exception) is distinguishable from an
    # empty-but-successful response (empty_data) and from a downstream
    # calculation failure (calculation_exception, caught by the existing
    # outer try/except further below). Previously all three collapsed
    # into the same silent `[]` return with no durable distinction (see
    # V-FRESH1A2's forensic finding on run 221/long-US) — this restructure
    # is diagnostic-only and does not change any existing return value or
    # control flow for callers that don't pass `_diag`.
    try:
        df = yf.Ticker(yf_sym).history(period=HORIZON_PERIOD[horizon])
    except Exception as e:
        log.warning(
            "[validation] price fetch failed — symbol=%s yf_symbol=%s market=%s "
            "universe=%s horizon=%s error=%s",
            symbol, yf_sym, market, universe, horizon, e,
        )
        if _diag is not None:
            _diag["terminal_path"] = "fetch_exception"
        return []

    if df.empty:
        if _diag is not None:
            _diag["terminal_path"] = "empty_data"
        return []

    if _diag is not None:
        _diag["input_first_date"] = str(df.index[0])[:10]
        _diag["input_last_date"] = str(df.index[-1])[:10]

    try:
        if len(df) < MIN_WARMUP + fwd_days:
            log.info(
                "[validation] insufficient history — symbol=%s yf_symbol=%s market=%s "
                "universe=%s horizon=%s rows=%d needed=%d",
                symbol, yf_sym, market, universe, horizon, len(df), MIN_WARMUP + fwd_days,
            )
            if _diag is not None:
                _diag["terminal_path"] = "insufficient_data"
            return []
        if _diag is not None:
            _diag["terminal_path"] = "processed"
        # Do NOT call compute_indicators on full df — that causes look-ahead bias.
        # Raw OHLCV df is kept clean; indicators are computed per window inside loop.

        # DP-026 status by market, as of this session's remediation work:
        #
        # US: REMEDIATED. Every US signal below computes its own fund_score
        # via `_us_fund_score_as_of()` — SEC EDGAR XBRL facts filed on or
        # before that SPECIFIC signal's date — inside the per-signal loop,
        # not once out here. No present-day snapshot is used for a US
        # historical signal.
        #
        # IN (India): STILL DISCLOSED, NOT REMEDIATED. No point-in-time
        # fundamentals source is integrated for India (investigated
        # 2026-07-21: neither yfinance/screener.in/BSE nor any internally
        # retained data — `fundamentals_cache.py`'s `stock_fundamentals_
        # cache` is overwrite-only, no history retained — provide a filed/
        # accepted timestamp per fact; NSE's own corporate-financial-
        # results API returned bot-protection challenge output, not
        # structured data, on direct testing this session; this is a
        # repository/market-scoped finding, not a market-wide claim that no
        # provider anywhere could supply this for India). `fund_score`
        # below is computed exactly as before DP-026 — a SINGLE present-day
        # `yf.Ticker(yf_sym).info` snapshot reused across every India
        # signal date. This is unchanged and remains contaminated; the
        # `data_limitations` disclosure in `_compute_metrics()` reflects
        # this per-market split, not a blanket claim.
        fund_score = 50.0
        if market == "IN":
            try:
                info = yf.Ticker(yf_sym).info
            except Exception:
                info = {}
            pe = info.get("trailingPE")
            if pe:
                fund_score += 10 if pe < 20 else (-10 if pe > 40 else 0)
            roe = info.get("returnOnEquity")
            if roe:
                fund_score += 10 if roe > 0.15 else 0
            rev_g = info.get("revenueGrowth")
            if rev_g:
                fund_score += 8 if rev_g > 0.10 else (-8 if rev_g < 0 else 0)
            fund_score = max(0.0, min(100.0, fund_score))

        # Align benchmark index to stock dates — forward-fill only, bounded
        # staleness, never backward-fill (see _align_benchmark_close). A
        # position with `benchmark_available[idx] is False` must never be
        # treated as evidence, even though `benchmark_close` may still hold
        # a (stale-beyond-tolerance or pre-first-observation) NaN/number there.
        benchmark_close = None
        benchmark_available = None
        if benchmark_df is not None and not benchmark_df.empty and "Close" in benchmark_df.columns:
            benchmark_close, benchmark_available = _align_benchmark_close(benchmark_df, df.index)

        # Precompute regime adjustments from benchmark only (no stock data →
        # no bias). regime_available[idx] distinguishes a genuinely
        # calculated neutral regime (0.0, real evidence, no directional
        # signal) from unavailable regime evidence (also stored as 0.0 in
        # regime_adjs for arithmetic convenience, but NEVER used — the
        # per-signal loop below checks regime_available, not the value).
        regime_adjs = []
        regime_available = []
        if benchmark_close is not None:
            ema50_bench = benchmark_close.ewm(span=50).mean()
            for idx in range(len(df)):
                base_idx = max(0, idx - 63)
                if not (bool(benchmark_available.iloc[idx]) and bool(benchmark_available.iloc[base_idx])):
                    regime_adjs.append(0.0)
                    regime_available.append(False)
                    continue
                try:
                    cur  = float(benchmark_close.iloc[idx])
                    e50  = float(ema50_bench.iloc[idx])
                    base = float(benchmark_close.iloc[base_idx])
                    if not (np.isfinite(cur) and np.isfinite(e50) and np.isfinite(base)) or base <= 0:
                        regime_adjs.append(0.0)
                        regime_available.append(False)
                        continue
                    r3m = (cur - base) / base
                    if cur > e50 and r3m > 0.03:    regime_adjs.append(5.0)
                    elif cur < e50 and r3m < -0.03: regime_adjs.append(-5.0)
                    else:                            regime_adjs.append(0.0)
                    regime_available.append(True)
                except Exception:
                    regime_adjs.append(0.0)
                    regime_available.append(False)
        else:
            regime_adjs = [0.0] * len(df)
            regime_available = [False] * len(df)

        signals = []
        for i in range(MIN_WARMUP, len(df) - fwd_days, step):
            try:
                window_stats["considered"] += 1
                entry = float(df["Close"].iloc[i])
                # V-PS2 — a NaN Close (a genuine market-data gap) at the
                # ENTRY date means there was never an observable, tradeable
                # price to enter this hypothetical position at — the whole
                # window cannot produce a signal (this row also feeds
                # _score_at()'s technical sub-scores below, so an invalid
                # entry poisons the prediction itself, not just its
                # outcome). Only entry==0 (division-by-zero) is rejected
                # here too — a genuine 0 EXIT price is a valid total loss,
                # handled below, never rejected.
                if not np.isfinite(entry) or entry == 0:
                    continue

                # V-PS2A — exit-price validity is handled separately from
                # entry: the model's prediction (composite score/predicted
                # label, computed below from data up to and including the
                # ENTRY date only) already exists independent of whether
                # the FUTURE exit price ever became observable. A missing/
                # non-finite exit means the OUTCOME cannot be measured —
                # not that the prediction never happened. `fwd_ret=None`
                # represents this honestly; the signal is still recorded
                # (see get_per_stock_results()'s evaluated/return cohort
                # contract, which already treats a None fwd_return_pct as
                # "not yet evaluated", never as a fabricated loss).
                exit_ = float(df["Close"].iloc[i + fwd_days])
                if np.isfinite(exit_):
                    fwd_ret = (exit_ - entry) / entry * 100
                    # Defense against any other unexpected arithmetic
                    # result (e.g. an extreme-magnitude entry) — a
                    # non-finite fwd_ret must never reach persistence.
                    if not np.isfinite(fwd_ret):
                        fwd_ret = None
                else:
                    fwd_ret = None

                # V-FRESH1B — the actual exit bar's own date, read directly
                # from the dataframe index at the same position already
                # used to compute exit_/fwd_ret above (df.index[i +
                # fwd_days]) — never reconstructed via calendar arithmetic.
                # Only set when the outcome is genuinely evaluated
                # (fwd_ret is not None); an unresolved outcome must not
                # fabricate an exit date.
                exit_date = str(df.index[i + fwd_days])[:10] if fwd_ret is not None else None

                # Benchmark forward return over same window (for alpha
                # calculation) — genuine evidence required at BOTH the
                # entry and exit dates, plus regime evidence at entry
                # (regime_adjs[i] already fed the composite score below, so
                # a signal whose regime input was unavailable must not be
                # published at all, not just have its alpha/correctness
                # fields blanked — see this function's own docstring).
                # Never initialized to 0.0 as a missing-value fallback: if
                # evidence is unavailable, the signal is skipped entirely.
                benchmark_ok = (
                    benchmark_close is not None
                    and bool(regime_available[i])
                    and bool(benchmark_available.iloc[i])
                    and bool(benchmark_available.iloc[i + fwd_days])
                )
                if not benchmark_ok:
                    exclusions.append(1)
                    continue
                b_entry = float(benchmark_close.iloc[i])
                b_exit  = float(benchmark_close.iloc[i + fwd_days])
                # Finding B (2026-07-26 hardening): BOTH sides of the
                # window must be finite and strictly positive — the
                # original condition only checked b_entry, letting a
                # zero/negative b_exit silently through to the forward-
                # return division below.
                if not (np.isfinite(b_entry) and np.isfinite(b_exit)) or b_entry <= 0 or b_exit <= 0:
                    exclusions.append(1)
                    continue
                window_stats["benchmark_valid"] += 1
                benchmark_fwd_ret = (b_exit - b_entry) / b_entry * 100

                # ── Look-ahead-free indicator computation ──────────────────────
                # Slice only rows 0..i (inclusive) — no future data visible
                window = df.iloc[:i + 1].copy()
                window = compute_indicators(window)

                # DP-026 remediation — US only: a genuine point-in-time
                # fund_score for THIS signal's own date, not the per-symbol
                # snapshot computed above. `signal_date` (an SEC-filing-
                # comparable calendar date) is the as-of cutoff — no
                # network call happens here beyond the first per-symbol
                # fetch, which sec_edgar_adapter.fetch_company_facts()
                # already 12h-caches by CIK.
                signal_date_i = df.index[i].date() if hasattr(df.index[i], "date") else df.index[i]
                if market == "US":
                    fund_score_i, fund_pit_available_i, fund_pit_reason_i = _us_fund_score_as_of(symbol, signal_date_i)
                    if not fund_pit_available_i:
                        # Explicitly unavailable, not fabricated — falls
                        # back to the same neutral 50.0 this codebase
                        # already uses elsewhere for "no evidence" (e.g.
                        # daily_picks.py's quality_score convention), but
                        # UNLIKE the pre-remediation code, this is now
                        # recorded per-signal (fund_pit_available=False)
                        # rather than being indistinguishable from a
                        # genuine neutral score — see DP-031's ownership of
                        # this same distinction for the IN-side legacy path.
                        fund_score_i = 50.0
                else:
                    fund_score_i = fund_score
                    fund_pit_available_i = False   # IN: never point-in-time (see comment above the loop)
                    fund_pit_reason_i = "point-in-time fundamentals not available for market=IN (DP-026)"

                # Score uses the last row of the window (= day i)
                sc = _score_at(window, len(window) - 1, benchmark_close, fund_score_i, regime_adjs[i])
                composite = sc["composite"]

                buy_thr  = BUY_THRESHOLD[horizon]
                sell_thr = SELL_THRESHOLD[horizon]
                predicted = "BUY" if composite >= buy_thr else ("SELL" if composite <= sell_thr else "HOLD")

                # "correct" = benchmark-relative:
                #   BUY is correct  if stock outperforms the benchmark by > 0 over fwd window
                #   SELL is correct if stock underperforms the benchmark by > 0 over fwd window
                #   HOLD is correct if stock is within ±threshold% of the benchmark return
                #
                # V-PS2A — an unmeasurable outcome (fwd_ret is None, exit
                # price never became available) must never be classified
                # at all: `alpha = None - benchmark_fwd_ret` would raise,
                # and even a defensive `alpha = NaN` would previously have
                # been silently classified `correct=False` by `NaN > 0`
                # (Python's NaN-comparison semantics) — a fabricated miss.
                # correct/alpha/actual_direction all stay honestly None
                # for this signal; it is still recorded (see below) with
                # its prediction intact, just without a graded outcome.
                if fwd_ret is not None:
                    alpha = fwd_ret - benchmark_fwd_ret
                    if not np.isfinite(alpha):
                        alpha = None
                else:
                    alpha = None

                if alpha is not None:
                    if predicted == "BUY":
                        correct_val = int(alpha > 0)
                    elif predicted == "SELL":
                        correct_val = int(alpha < 0)
                    else:
                        correct_val = int(abs(alpha) <= threshold * 100)
                    # Keep absolute direction for context (used in avg return calcs)
                    actual_dir = "UP" if fwd_ret >= threshold * 100 else ("DOWN" if fwd_ret <= -threshold * 100 else "FLAT")
                else:
                    correct_val = None
                    actual_dir = None

                signals.append({
                    "symbol":          symbol,
                    "horizon":         horizon,
                    "signal_date":     str(df.index[i])[:10],
                    # V-FRESH1B — in-memory only, not persisted to
                    # val_signals' fixed columns (same pattern as
                    # "confidence" below) — aggregated by run_validation()
                    # into evaluated_exit_date_min/max.
                    "exit_date":       exit_date,
                    "composite_score": composite,
                    "tech_score":      sc["tech"],
                    "rs_score":        sc["rs"],
                    "obv_score":       sc["obv"],
                    "mfi_score":       sc["mfi"],
                    "predicted":       predicted,
                    # Additive-only (see _confidence_from_composite docstring) —
                    # not persisted to val_signals' fixed columns, in-memory
                    # only, used solely for the confidence_buckets report below.
                    "confidence":      _confidence_from_composite(composite, predicted),
                    # V-PS2A — None when the exit price never became
                    # observable (see the exit-validity block above); the
                    # existing evaluated/return cohort contract in
                    # _compute_metrics()/get_per_stock_results() already
                    # treats a None fwd_return_pct as "not yet evaluated",
                    # never as a fabricated loss.
                    "fwd_return_pct":  round(fwd_ret, 3) if fwd_ret is not None else None,
                    # Persisted column/JSON key name (val_signals.nifty_fwd_ret_pct) —
                    # kept unchanged to avoid a schema migration; the underlying
                    # value is the run's benchmark forward return regardless of
                    # market (Nifty 50 for IN, S&P 500 for US). Always finite
                    # here — benchmark_ok/finiteness was already gated above.
                    "nifty_fwd_ret_pct": round(benchmark_fwd_ret, 3),
                    "alpha_pct":       round(alpha, 3) if alpha is not None else None,
                    "actual_direction": actual_dir,
                    "correct":         correct_val,
                    # DP-026 remediation — additive-only, same non-persisted
                    # pattern as "confidence" above (not a val_signals fixed
                    # column; used solely for _compute_metrics()'s coverage
                    # reporting). True only for a US signal whose fund_score
                    # came from a genuine as-of SEC EDGAR lookup that found
                    # eligible facts; always False for IN (no point-in-time
                    # source exists) and for a US signal where the as-of
                    # lookup itself came back unavailable.
                    "fund_pit_available": bool(fund_pit_available_i),
                    # DP-033 scoring-policy versioning (Option B) — None
                    # for IN (the old, unversioned formula); the exact
                    # policy string for a US signal, whether or not the
                    # as-of lookup found eligible facts (fund_score_i still
                    # went through this policy's neutral-fallback branch).
                    "fund_score_policy_version": US_PIT_SCORING_POLICY_VERSION if market == "US" else None,
                })
            except Exception:
                continue

        return signals

    except Exception as e:
        log.warning(
            "[validation] backtest failed — symbol=%s yf_symbol=%s market=%s "
            "universe=%s horizon=%s error=%s",
            symbol, yf_sym, market, universe, horizon, e,
        )
        if _diag is not None:
            _diag["terminal_path"] = "calculation_exception"
        return []


def _build_validation_evidence(
    n_stocks: int,
    diag_by_symbol: dict,
    window_stats_by_symbol: dict,
    symbols_with_signals: set,
    all_signals: list,
) -> dict:
    """V-FRESH1B — assembles factual, durable validation-evidence
    diagnostics purely from data already computed during this run.
    Stored additively inside the existing immutable summary JSON (no
    schema change — see run_validation's persistence block and
    get_latest_results()'s read-time freshness derivation). Every
    count/date here is directly derived from per-symbol diagnostics
    (`_backtest_stock`'s `_diag`/`_window_stats` outputs) and persisted
    signal dicts — nothing is invented, nothing is backfilled, nothing
    is calendar-derived. `symbols_with_signals` is factual (how many
    symbols produced ≥1 signal) — deliberately never described as
    "coverage" or "eligibility" anywhere this value is surfaced (see
    V-FRESH1A2's corrected forensic finding).
    """
    terminal_counts = {
        "fetch_exception": 0, "empty_data": 0, "insufficient_data": 0,
        "calculation_exception": 0, "processed": 0,
    }
    input_last_dates: list[str] = []
    symbols_processed = 0  # actually entered the per-window loop (>=1 window considered)
    for sym, diag in diag_by_symbol.items():
        tp = diag.get("terminal_path")
        if tp in terminal_counts:
            terminal_counts[tp] += 1
        if diag.get("input_last_date") is not None:
            input_last_dates.append(diag["input_last_date"])
        stats = window_stats_by_symbol.get(sym)
        if stats and stats.get("considered", 0) > 0:
            symbols_processed += 1

    signal_dates = [s["signal_date"] for s in all_signals if s.get("signal_date")]
    exit_dates = [s["exit_date"] for s in all_signals if s.get("exit_date")]
    evaluated_symbols = {s["symbol"] for s in all_signals if s.get("exit_date")}

    return {
        "symbols_requested": n_stocks,
        "symbols_fetch_attempted": len(diag_by_symbol),
        "symbols_with_input_data": len(input_last_dates),
        "symbols_with_sufficient_data": terminal_counts["processed"],
        "symbols_processed": symbols_processed,
        "symbols_with_signals": len(symbols_with_signals),
        "symbols_with_evaluated_outcomes": len(evaluated_symbols),
        "fetch_exception_count": terminal_counts["fetch_exception"],
        "empty_data_count": terminal_counts["empty_data"],
        "insufficient_data_count": terminal_counts["insufficient_data"],
        "calculation_exception_count": terminal_counts["calculation_exception"],
        "input_latest_bar_date_min": min(input_last_dates) if input_last_dates else None,
        "input_latest_bar_date_max": max(input_last_dates) if input_last_dates else None,
        "signal_date_min": min(signal_dates) if signal_dates else None,
        "signal_date_max": max(signal_dates) if signal_dates else None,
        "evaluated_exit_date_min": min(exit_dates) if exit_dates else None,
        "evaluated_exit_date_max": max(exit_dates) if exit_dates else None,
    }


# ── Aggregate metrics ─────────────────────────────────────────────────────────

def _max_consec_wrong(buys: list[dict]) -> int:
    # V-PS2B — `correct` is tri-state (True/False/None; a retained V-PS2A
    # unevaluated signal is None). Raw truthiness (`if not s["correct"]`)
    # treated None as a wrong result — an unknown outcome is neither a
    # hit nor a miss, so it must reset the streak without incrementing
    # it. Deliberately NOT pre-filtered: filtering None out would bridge
    # two genuine streaks across an evidentiary gap that never actually
    # happened consecutively.
    ordered = sorted(buys, key=lambda s: s.get("signal_date", ""))
    best = cur = 0
    for s in ordered:
        c = s["correct"]
        if c is None:
            cur = 0
        elif not c:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _max_consec_right(buys: list[dict]) -> int:
    # V-PS2B — same tri-state contract as _max_consec_wrong() above.
    ordered = sorted(buys, key=lambda s: s.get("signal_date", ""))
    best = cur = 0
    for s in ordered:
        c = s["correct"]
        if c is None:
            cur = 0
        elif c:
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def _max_drawdown(buys: list[dict]) -> float | None:
    """Peak-to-trough drawdown of the equal-weight SIGNAL-DATE return curve.

    NOT a capital-allocated portfolio backtest — see the label this value
    is displayed under ("Max drawdown of the equal-weight signal-date
    return curve"). It makes no capital-allocation, transaction-cost, or
    execution-timing assumption; signals on the same date can represent
    genuinely concurrent/overlapping positions, which this metric does
    not attempt to model.

    Contract (order-invariant by construction, not just by sorting):
      1. Group BUY signals with a non-null fwd_return_pct by signal_date.
      2. Each date's return is the equal-weight mean of that date's
         signal returns — this is what removes intra-date ordering as a
         variable at all, rather than relying on a stable sort of
         individual signals (whose original order reflects
         ThreadPoolExecutor completion order in run_validation(), not
         anything meaningful).
      3. Sort the resulting (one row per date) series chronologically.
      4. Compound that deterministic date-level series into an equity
         curve and compute peak-to-trough drawdown on it.

    Return-domain contract (fail closed): for a long-equity percentage
    return, -100% (total loss) is the valid floor — a return below -100%,
    or a non-finite/non-numeric value, is impossible for a long equity
    position and means the upstream evidence is malformed, not that a
    real drawdown event occurred. Any such value invalidates the WHOLE
    metric (returns None) rather than being clamped, replaced, or
    silently dropped while the remaining rows compute an apparently-valid
    number — a partial result over a corrupted cohort is indistinguishable
    from a genuine one and would misrepresent the metric's integrity.
    Genuinely missing (None) returns are excluded from the cohort before
    this validation runs, per the existing evaluated-cohort contract —
    that is an intentional exclusion, not a malformed value.
    """
    import math
    from collections import defaultdict

    by_date: dict[str, list[float]] = defaultdict(list)
    for s in buys:
        r = s.get("fwd_return_pct")
        if r is None:
            continue
        if not isinstance(r, (int, float)) or not math.isfinite(r) or r < -100:
            return None
        by_date[s.get("signal_date", "")].append(r)

    if not by_date:
        return None

    date_returns = [
        (date, sum(rets) / len(rets))
        for date, rets in sorted(by_date.items(), key=lambda kv: kv[0])
    ]

    equity = 100.0
    peak = equity
    max_dd = 0.0
    for _date, r in date_returns:
        equity *= (1 + r / 100)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak * 100
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 1)


def _data_limitations_for_market(signals: list[dict], market: str | None) -> dict:
    """
    DP-026 disclosure, now genuinely market-dependent (2026-07-21
    remediation session). US signals are computed from a real per-signal
    SEC EDGAR as-of lookup (`_us_fund_score_as_of()`); `coverage_pct` below
    is a REAL measured statistic from this run's own `fund_pit_available`
    flags — never a guessed or fixed number. IN remains exactly the
    pre-remediation disclosure: no point-in-time source exists for India,
    so every India signal is still built from a single present-day
    snapshot.
    """
    n = len(signals)
    pit_available_n = sum(1 for s in signals if s.get("fund_pit_available")) if n else 0
    coverage_pct = round(pit_available_n / n * 100, 1) if n else None

    if market == "US":
        return {
            "fundamentals_point_in_time": True,
            "fundamentals_point_in_time_coverage_pct": coverage_pct,
            "fundamentals_availability_vs_neutral_distinguishable": True,
            "dp_id": "DP-026",
            "scoring_policy_version": US_PIT_SCORING_POLICY_VERSION,
            "status": "remediated (US, methodology) — point-in-time via persisted SEC EDGAR facts; NOT YET deployed/backfilled/production-verified",
            "reason": (
                f"Every US signal's fund_score is computed from SEC EDGAR "
                f"XBRL facts persisted in services.sec_pit_store's immutable "
                f"table (services.validation_engine._get_fundamentals_as_of_"
                f"persisted_first()), filtered to those filed — and eligible "
                f"per the next-US-trading-session conservative rule for "
                f"date-only SEC filing timestamps — on or before that "
                f"signal's own historical date. Not a present-day snapshot, "
                f"and not dependent on live network access after the "
                f"one-time per-CIK ingestion. Scoring policy version "
                f"'{US_PIT_SCORING_POLICY_VERSION}' — a SEPARATELY versioned "
                f"formula (ROE + net margin only), not a drop-in replacement "
                f"claiming equivalence with the pre-existing PE+ROE+revenue-"
                f"growth formula IN/legacy results still use; every US "
                f"signal records this exact version. "
                f"{coverage_pct}% of signals in this run ({pit_available_n}"
                f"/{n}) found an eligible SEC filing and usable ROE/margin "
                f"inputs as of their date; the remainder ({n - pit_available_n}"
                f"/{n}) explicitly had no eligible filing or insufficient "
                f"fields as of that date and used a disclosed neutral 50.0 "
                f"fallback (tracked per-signal via fund_pit_available, not "
                f"silently indistinguishable from a genuine neutral score — "
                f"resolving the prior DP-031-owned ambiguity for this "
                f"market). Revenue-growth and PE ratios were deliberately "
                f"NOT reconstructed point-in-time (revenue growth needs a "
                f"second, correctly-ordered as-of lookup; PE needs point-in-"
                f"time shares-outstanding data this adapter does not "
                f"extract) — a documented reduction in factor richness, not "
                f"a silent one. See DP-031 for closing this gap alongside "
                f"its neutral-vs-unavailable instrumentation work."
            ),
        }

    # IN (or an unknown/unset market — treated as the worst case, not
    # silently assumed remediated).
    return {
        "fundamentals_point_in_time": False,
        "fundamentals_affected_signals_pct": 100.0,
        "fundamentals_availability_vs_neutral_distinguishable": False,
        "dp_id": "DP-026",
        "status": "disclosed, not remediated (India — no point-in-time source integrated)",
        "reason": (
            "fund_score is a single current-day fundamentals snapshot "
            "(yfinance .info, fetched once per symbol at backtest run "
            "time), reused unchanged for every historical signal date "
            "produced for that symbol. It is NOT the fundamentals that "
            "were knowable as of each signal's actual date, and "
            "constitutes look-ahead bias on the fundamentals component "
            "of composite_score for 100% of India signals in this run. "
            "Investigated 2026-07-21 (remediation session): no point-in-"
            "time fundamentals source is integrated for India — neither "
            "yfinance/screener.in/BSE nor any internally retained data "
            "(stock_fundamentals_cache is overwrite-only, no history "
            "retained) provide a filed/accepted timestamp per fact; NSE's "
            "own corporate-financial-results API returned bot-protection "
            "challenge output (not structured data) on direct testing "
            "this session. This does not establish that no such data "
            "exists from any provider in the market — a full vendor "
            "survey is out of scope for this finding — only that it is "
            "not obtainable from what this repository currently "
            "integrates or retains without procuring/building a new, "
            "currently unintegrated data source. Separately (see DP-031, not "
            "resolved here): whether a given India signal's fund_score "
            "reflects successfully retrieved current data or an "
            "unavailable-data fallback is not currently distinguishable "
            "-- do not read '100% affected' above as '100% unavailable'; "
            "it means 100% share this same non-point-in-time input, "
            "regardless of whether that input was successfully retrieved. "
            "US validation runs from this same codebase are, as of this "
            "session, genuinely point-in-time — see this field's "
            "US-market counterpart."
        ),
    }


def _compute_metrics(
    signals: list[dict], benchmark_return_pct: float | None, horizon: str = "medium",
    market: str | None = None, signals_excluded_benchmark: int = 0,
) -> dict:
    """Compute all aggregate validation metrics from raw signals.

    `signals_excluded_benchmark` — count of stock-date windows that were
    skipped entirely because genuine benchmark/regime evidence was
    unavailable at that date (see _backtest_stock's `_exclusions`) — is
    purely disclosive here, never used in any calculation below; every
    signal actually reaching this function already has real
    benchmark-relative fields (no fabricated 0.0/neutral survivors)."""
    if not signals:
        return {}

    buys  = [s for s in signals if s["predicted"] == "BUY"]
    sells = [s for s in signals if s["predicted"] == "SELL"]
    holds = [s for s in signals if s["predicted"] == "HOLD"]

    fwd_days = HORIZON_DAYS.get(horizon, 5)

    def _hit_rate(subset):
        evaluated = [s for s in subset if s.get("correct") is not None]
        if not evaluated: return None
        return round(sum(s["correct"] for s in evaluated) / len(evaluated) * 100, 1)

    # V-VAL1 aggregate evaluated-BUY-cohort contract (additive, does not
    # remove/rename any existing field). `correct`/`fwd_return_pct` are
    # never None on an in-memory signal dict as of this writing —
    # run_validation()'s per-window loop only appends a signal once both
    # are fully computed (see _backtest_stock) — but this filters
    # defensively on `is not None` rather than assuming that invariant,
    # so evaluated_buy_count/buy_return_count would genuinely diverge from
    # buy_signal_count if that ever changed, instead of silently
    # miscounting. buy_hits is a real integer count, never reconstructed
    # from a rounded percentage.
    evaluated_buys      = [s for s in buys if s.get("correct") is not None]
    buy_hits_count      = sum(1 for s in evaluated_buys if s["correct"])
    buy_return_signals  = [s for s in buys if s.get("fwd_return_pct") is not None]

    def _avg_ret(subset):
        vals = [s["fwd_return_pct"] for s in subset if s.get("fwd_return_pct") is not None]
        if not vals: return None
        return round(float(np.mean(vals)), 2)

    def _sharpe(rets, rf=0.0):
        arr = np.array(rets)
        if arr.std() == 0: return 0.0
        # Annualise using actual forward window (not a hardcoded 5-day assumption)
        return round(float((arr.mean() - rf) / arr.std() * np.sqrt(252 / fwd_days)), 2)

    # Score bucket analysis — key table for investor confidence
    buckets = []
    for lo, hi in ((60,65),(65,70),(70,75),(75,80),(80,85),(85,91)):
        bucket_buys = [s for s in buys if lo <= s["composite_score"] < hi]
        if bucket_buys:
            buckets.append({
                "score_range": f"{lo}–{hi}",
                "count":       len(bucket_buys),
                "hit_rate_pct": _hit_rate(bucket_buys),
                "avg_return_pct": _avg_ret(bucket_buys),
            })

    # Confidence bucket analysis (2026-07-17 audit, additive-only — does not
    # feed any live decision). Daily Picks filters BUY signals below 25%
    # confidence (services/daily_picks.py); this table is the first place
    # that number is checked against realized hit rate, using the same 25%
    # boundary as one of the bucket edges. Read this before ever changing
    # the confidence formula or the 25% cutoff — a formula change should be
    # justified by what this table shows, not guessed at.
    confidence_buckets = []
    for lo, hi in ((0, 25), (25, 50), (50, 75), (75, 101)):
        bucket_buys = [s for s in buys if lo <= s.get("confidence", -1) < hi]
        if bucket_buys:
            confidence_buckets.append({
                "confidence_range": f"{lo}–{hi if hi <= 100 else 100}",
                "count":            len(bucket_buys),
                "hit_rate_pct":     _hit_rate(bucket_buys),
                "avg_return_pct":   _avg_ret(bucket_buys),
            })

    # SELL confidence bucket analysis (2026-07-17) — same purpose as
    # confidence_buckets above, for the SELL branch. Kept separate rather
    # than merged: SELL's hit rate is flat across buckets (no gradient),
    # unlike BUY's — a combined table would obscure that real difference.
    sell_confidence_buckets = []
    for lo, hi in ((0, 25), (25, 50), (50, 75), (75, 101)):
        bucket_sells = [s for s in sells if lo <= s.get("confidence", -1) < hi]
        if bucket_sells:
            sell_confidence_buckets.append({
                "confidence_range": f"{lo}–{hi if hi <= 100 else 100}",
                "count":            len(bucket_sells),
                "hit_rate_pct":     _hit_rate(bucket_sells),
                "avg_return_pct":   _avg_ret(bucket_sells),
            })

    # Factor IC (Pearson correlation of each sub-score with forward return)
    def _ic(factor_key):
        pairs = [(s[factor_key], s["fwd_return_pct"]) for s in signals
                 if s.get(factor_key) is not None and s.get("fwd_return_pct") is not None]
        if len(pairs) < 30: return None
        vals, rets = zip(*pairs)
        return round(float(np.corrcoef(vals, rets)[0,1]), 4)

    # Portfolio simulation: equal-weight all BUY signals, measure vs benchmark.
    # model_avg must stay None (never a fabricated 0.0 "model return") when
    # there are zero BUY signals — a missing average is not a genuine zero
    # average, and comparing a phantom 0% model return against a real
    # benchmark would misleadingly imply the model produced flat signals
    # rather than no signals at all. outperformance requires BOTH sides to
    # be genuinely available.
    buy_rets   = [s["fwd_return_pct"] for s in buy_return_signals]
    buy_alphas = [s["alpha_pct"] for s in buys if s.get("alpha_pct") is not None]
    model_avg  = _avg_ret(buys)
    outperformance = (
        round(model_avg - benchmark_return_pct, 2)
        if model_avg is not None and benchmark_return_pct is not None
        else None
    )

    def _avg(lst):
        return round(float(np.mean(lst)), 2) if lst else None

    return {
        "total_signals":    len(signals),
        "buy_signals":      len(buys),
        "sell_signals":     len(sells),
        "hold_signals":     len(holds),
        # V-VAL1 additive cohort fields — buy_signals above (legacy name,
        # kept unchanged) and buy_signal_count are the identical value
        # under a clearer name; evaluated_buy_count/buy_hits/
        # buy_return_count are new. NOTE: buy_signals/sell_signals/
        # hold_signals are PREDICTED-class counts (what the model called),
        # not an actual/ground-truth class distribution — see the
        # frontend's Overall Accuracy sub-text for why these must never be
        # read as a majority-class accuracy baseline.
        "buy_signal_count":    len(buys),
        "evaluated_buy_count": len(evaluated_buys),
        "buy_hits":            buy_hits_count,
        "buy_return_count":    len(buy_return_signals),
        # Benchmark-relative hit rate (primary metric — stock must beat Nifty
        # to be "correct"). Now explicitly buy_hits/evaluated_buy_count
        # rather than _hit_rate(buys) — identical value today (evaluated_buy_
        # count == len(buys) under the current invariant) but honestly
        # derived from the evaluated cohort rather than the raw BUY count.
        "buy_hit_rate_pct":           round(buy_hits_count / len(evaluated_buys) * 100, 1) if evaluated_buys else None,
        "sell_hit_rate_pct":          _hit_rate(sells),
        "overall_accuracy_pct":       _hit_rate(signals),
        # Return metrics
        "avg_return_on_buy_pct":      _avg_ret(buys),
        "avg_alpha_on_buy_pct":       _avg(buy_alphas),
        "avg_return_on_sell_pct":     _avg_ret(sells),
        # Genuine zero vs missing: a real benchmark_return_pct of exactly
        # 0.0 must survive as 0.0, never collapse to None via a truthy
        # check (`if benchmark_return_pct` treats 0.0 as falsy — fixed).
        "avg_return_benchmark_pct":   round(benchmark_return_pct, 2) if benchmark_return_pct is not None else None,
        "buy_outperformance_pct":     outperformance,
        "sharpe_on_buys":             _sharpe(buy_rets) if buy_rets else None,
        "sharpe_on_alphas":           _sharpe(buy_alphas) if buy_alphas else None,
        "profitable_buy_pct":         round(sum(1 for r in buy_rets if r > 0) / len(buy_rets) * 100, 1) if buy_rets else None,
        # "beat benchmark meaningfully" = alpha > 1% (not just alpha > 0 which equals buy_hit_rate)
        "beat_benchmark_pct":         round(sum(1 for a in buy_alphas if a > 1.0) / len(buy_alphas) * 100, 1) if buy_alphas else None,
        # Streak / drawdown analysis on BUY signals ordered by signal_date
        "max_consecutive_wrong":  _max_consec_wrong(buys),
        "max_consecutive_right":  _max_consec_right(buys),
        "max_drawdown_pct":       _max_drawdown(buys),
        "score_buckets":          buckets,
        "confidence_buckets":     confidence_buckets,
        "sell_confidence_buckets": sell_confidence_buckets,
        "factor_ic": {
            "tech":      _ic("tech_score"),
            "rs":        _ic("rs_score"),
            "obv":       _ic("obv_score"),
            "mfi":       _ic("mfi_score"),
            "composite": _ic("composite_score"),
        },
        # DP-026 status is now genuinely market-dependent — see
        # _backtest_stock()'s market branch (US: `_us_fund_score_as_of()`
        # per signal date via SEC EDGAR; IN: unchanged single-snapshot
        # yfinance reuse, still contaminated). `market` is required so this
        # dict can report the correct one; `None` (a caller that predates
        # this parameter) is treated as unknown/worst-case (IN-equivalent),
        # never silently reported as remediated.
        "data_limitations": _data_limitations_for_market(signals, market),
        # Benchmark evidence integrity disclosure (never fabricated —
        # signals_excluded_benchmark is the caller-supplied real count of
        # stock-date windows skipped for missing/stale/non-finite benchmark
        # or regime evidence; every signal counted above already has real,
        # non-fabricated benchmark-relative fields).
        "signals_excluded_benchmark": signals_excluded_benchmark,
    }


# ── Main runner ───────────────────────────────────────────────────────────────

def run_validation(horizon: str = "medium", universe: str = "nifty100", max_workers: int = 6,
                   trigger_type: str = "internal", _claimed_job: dict | None = None,
                   progress_callback=None, _persist: bool = True, _fence_check=None) -> dict:
    """
    Run a full walk-forward validation.

    `_persist` and `_fence_check` are internal, underscore-prefixed
    parameters used ONLY by the V-SCHED1C1 ledger-backed execution path
    (execute_and_complete_admitted_attempt) — every direct/legacy caller
    keeps the exact prior behavior (_persist=True, no fence checking).
    When `_persist=False`, this function performs every computation step
    identically but does NOT insert into val_runs/val_signals; instead
    metrics["_persist_payload"] carries everything the caller needs to
    hand to the atomic fenced primitive complete_running_attempt_with_
    computed_result(), and metrics["run_id"] is left unset (persistence,
    and therefore the run_id, only exist after that atomic commit
    succeeds). `_fence_check`, if given, is called at the same cooperative
    checkpoint as `progress_callback` (after each stock's backtest
    completes); if it returns a truthy "fenced" signal, the remaining
    futures are cancelled and _FencedOutDuringComputation is raised
    immediately — no persistence of any kind is attempted for a
    computation known to be stale.

    universe options (case-sensitive, exact match — no other value accepted):
      "nifty100"  — Nifty 100 large-cap India (default, ~125 stocks)
      "midcap"    — Mid-cap NSE sample (~100 stocks beyond Nifty 100)
      "us"        — US S&P 500 basket (~48 stocks, all major sectors)

    Raises ValueError for any other universe string, before any database
    initialization, benchmark fetch, or executor submission happens — see
    _require_known_universe. There is no fallback to NIFTY_100 and no
    default market.

    Stores results in Postgres/SQLite and returns summary metrics, including
    the persisted `val_runs.id` as metrics["run_id"].

    `progress_callback`, if given, is called as `progress_callback(done,
    total)` after each stock's backtest completes (main thread only, same
    point _run_status["progress"] is updated) — V-SCHED1C1 uses this for
    ledger-lease heartbeat renewal tied to genuine forward progress, never
    a blind timer. Optional and backward compatible; existing direct
    callers that don't pass it are unaffected.
    """
    _require_known_universe(universe)

    universe_map = {
        "nifty100": NIFTY_100,
        "midcap":   NSE_MIDCAP,
        "us":       US_BASKET,
    }
    stocks   = universe_map[universe]
    n_stocks = len(stocks)
    label    = f"{horizon}-{universe}"

    if _claimed_job is None:
        _claimed_job = claim_validation_job(horizon, universe, trigger_type=trigger_type)
        if _claimed_job is None:
            with _status_lock:
                active = dict(_run_status.get("job") or {})
            return {"error": "A validation run is already in progress", "job": active or None}
    else:
        # Fail-closed identity invariant: a caller-supplied _claimed_job must
        # match this exact call's universe/horizon. The one current caller
        # (api/routers/validation.py's /run) always satisfies this by
        # construction (job comes from claim_validation_job(horizon,
        # universe, ...) using the same closure variables), so this can
        # never fire today — it exists so a future caller or refactor can
        # never silently use a mismatched job's benchmark/market for this
        # universe's stock list, which would corrupt both the computed
        # results and the persisted job identity attached to them.
        if _claimed_job["universe_id"] != universe or _claimed_job["horizon"] != horizon:
            log.warning(
                "[validation] claimed job identity mismatch — claimed universe=%s "
                "horizon=%s, requested universe=%s horizon=%s",
                _claimed_job["universe_id"], _claimed_job["horizon"], universe, horizon,
            )
            with _status_lock:
                if _run_status.get("job") is _claimed_job:
                    _run_status.update({
                        "running": False,
                        "log": _run_status["log"] + [
                            f"❌ Failed: {VALIDATION_PUBLIC_FAILURE_MESSAGES['CLAIMED_JOB_MISMATCH']}"
                        ],
                    })
                    _claimed_job.update({
                        "status": "failed",
                        "failure_code": "CLAIMED_JOB_MISMATCH",
                        # Public field — stable and fixed, never the dynamic
                        # universe/horizon detail above (that detail is safe
                        # in itself, being constrained enum values not
                        # exception text, but the public contract stays
                        # deterministic regardless — see VALIDATION_PUBLIC_
                        # FAILURE_MESSAGES).
                        "failure_message": VALIDATION_PUBLIC_FAILURE_MESSAGES["CLAIMED_JOB_MISMATCH"],
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
            raise ValueError(
                f"run_validation: _claimed_job identity (universe={_claimed_job['universe_id']!r}, "
                f"horizon={_claimed_job['horizon']!r}) does not match the requested "
                f"universe={universe!r}, horizon={horizon!r} — refusing to run with mismatched identity"
            )
    job = _claimed_job

    # Benchmark ticker: Nifty 50 for India universes, S&P 500 for US
    benchmark_ticker = job["benchmark"]
    market = UNIVERSE_MARKET[universe]  # safe — _require_known_universe already validated this

    # Fetch benchmark with a bounded retry (same ticker, capped attempts and
    # total wait — this codebase's existing provider-retry convention, e.g.
    # sec_edgar_adapter.py/nse_bhavcopy.py), then run a single centralized
    # preflight (_validate_benchmark_acquisition) BEFORE any stock backtest
    # is submitted, before _init_db(), and — deliberately, like the
    # CLAIMED_JOB_MISMATCH guard above — OUTSIDE the try/except below, so a
    # raised failure here is never re-caught and relabeled RUN_EXCEPTION by
    # that handler. Benchmark evidence integrity closure: a bad benchmark
    # must fail the whole run closed, never let every stock in the universe
    # silently compute alpha/correctness/regime input against a fabricated
    # flat-zero/neutral substitute (the pre-existing defect this replaces —
    # see BENCHMARK_EVIDENCE_UNAVAILABLE below and _align_benchmark_close's
    # per-signal defense-in-depth for direct/future callers).
    # Finding E (2026-07-26 hardening): a single loop covers BOTH a thrown
    # provider exception AND a non-exception acquisition failure (an
    # empty/malformed frame returned without raising) — after every
    # attempt, `_validate_benchmark_acquisition` (now a total function,
    # Finding A) always yields a real BenchmarkEvidence, and retry
    # continues only while its status is one of the plausibly-transient
    # states in `_BENCHMARK_RETRIABLE_ACQUISITION_STATUSES`. A structural
    # status (e.g. unsorted_index, invalid_index_type,
    # insufficient_window_coverage, validation_error) stops immediately —
    # a second identical call to the same ticker cannot reasonably fix a
    # schema-level problem.
    bench_df = None
    evidence: BenchmarkEvidence | None = None
    for attempt in range(BENCHMARK_FETCH_MAX_ATTEMPTS):
        try:
            bench_df = yf.Ticker(benchmark_ticker).history(period=HORIZON_PERIOD[horizon])
        except Exception:
            bench_df = None
            log.exception(
                "[validation] benchmark fetch attempt %d/%d raised — ticker=%s "
                "horizon=%s universe=%s",
                attempt + 1, BENCHMARK_FETCH_MAX_ATTEMPTS, benchmark_ticker, horizon, universe,
            )

        evidence = _validate_benchmark_acquisition(bench_df, benchmark_ticker, market, horizon)
        if evidence.status == "available":
            break

        is_last_attempt = attempt == BENCHMARK_FETCH_MAX_ATTEMPTS - 1
        is_retriable = evidence.status in _BENCHMARK_RETRIABLE_ACQUISITION_STATUSES
        if is_last_attempt or not is_retriable:
            break
        log.warning(
            "[validation] retriable benchmark evidence status on attempt %d/%d — "
            "status=%s ticker=%s horizon=%s universe=%s",
            attempt + 1, BENCHMARK_FETCH_MAX_ATTEMPTS, evidence.status, benchmark_ticker, horizon, universe,
        )
        time.sleep(BENCHMARK_FETCH_RETRY_BACKOFF_SECONDS * (attempt + 1))

    if evidence.status != "available":
        log.warning(
            "[validation] benchmark evidence unavailable, failing run closed — "
            "status=%s reason=%s ticker=%s market=%s horizon=%s universe=%s "
            "rows_available=%d rows_required=%d coverage_pct=%s",
            evidence.status, evidence.reason, benchmark_ticker, market, horizon, universe,
            evidence.rows_available, evidence.rows_required, evidence.forward_window_coverage_pct,
        )
        with _status_lock:
            _run_status.update({
                "running": False,
                "log": _run_status["log"] + [
                    f"❌ Failed: {VALIDATION_PUBLIC_FAILURE_MESSAGES['BENCHMARK_EVIDENCE_UNAVAILABLE']}"
                ],
            })
            if _run_status.get("job") is not None:
                _run_status["job"].update({
                    "status": "failed",
                    "failure_code": "BENCHMARK_EVIDENCE_UNAVAILABLE",
                    "failure_message": VALIDATION_PUBLIC_FAILURE_MESSAGES["BENCHMARK_EVIDENCE_UNAVAILABLE"],
                    # Finding F (2026-07-26 hardening): the full stable,
                    # non-exception evidence contract is safe to expose on
                    # the failed job/status snapshot — every field is a
                    # constrained enum, a count, a date string, or a fixed
                    # per-status message; never raw provider/exception text.
                    "benchmark_evidence": asdict(evidence),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
        # No DB init, no stock backtest submitted, no _compute_metrics call,
        # no val_runs/val_signals write — the run slot is already released
        # (running: False) and the job marked terminal above.
        raise ValueError(
            f"run_validation: benchmark evidence unavailable for ticker={benchmark_ticker!r} "
            f"market={market!r} horizon={horizon!r} — status={evidence.status!r} "
            f"reason={evidence.reason!r} — refusing to run without valid benchmark evidence"
        )

    # evidence.status == "available" guarantees at least one finite,
    # positive (entry, exit) pair exists at or above the acquisition
    # coverage threshold — this loop can never end up with an empty
    # bench_rets here. Uses the SAME coerced/validated Close series as
    # acquisition validation and per-signal alignment (Finding B) — never
    # a different, unfiltered series — and requires BOTH entry and exit
    # to be finite and strictly positive (the original condition only
    # checked entry, letting a zero/negative exit silently through).
    fwd_days = HORIZON_DAYS[horizon]
    numeric_bench_close, _, _ = _coerce_benchmark_close(bench_df)
    bench_rets = []
    for i in range(0, len(bench_df) - fwd_days, HORIZON_STEP[horizon]):
        e = numeric_bench_close.iloc[i]
        x = numeric_bench_close.iloc[i + fwd_days]
        if np.isfinite(e) and np.isfinite(x) and e > 0 and x > 0:
            bench_rets.append((x - e) / e * 100)
    benchmark_avg_ret = float(np.mean(bench_rets))
    benchmark_data_available = True
    benchmark_unavailable_reason = None

    try:
        _init_db()

        all_signals: list[dict] = []
        symbols_with_signals: set[str] = set()
        excluded_benchmark: list = []
        # Finding D (2026-07-26 hardening): each stock gets its OWN fresh
        # per-worker stats dict (never a single dict shared/mutated across
        # threads) — aggregated by the main thread once every future has
        # resolved. Deliberately not another shared append-only list, for
        # a clearer, more portable long-term count contract than
        # `excluded_benchmark` above.
        window_stats_by_symbol: dict[str, dict] = {}
        # V-FRESH1B — each stock gets its own fresh diagnostic dict, exactly
        # mirroring window_stats_by_symbol's per-worker-owned pattern above
        # (never a single dict shared/mutated across threads). Aggregated
        # by the main thread only after every future has resolved.
        diag_by_symbol: dict[str, dict] = {}
        done = 0

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {}
            for sym in stocks:
                stats = {"considered": 0, "benchmark_valid": 0}
                window_stats_by_symbol[sym] = stats
                diag = {"terminal_path": None, "input_first_date": None, "input_last_date": None}
                diag_by_symbol[sym] = diag
                futures[pool.submit(
                    _backtest_stock, sym, horizon, bench_df, market,
                    universe=universe, _exclusions=excluded_benchmark, _window_stats=stats,
                    _diag=diag,
                )] = sym
            for future in as_completed(futures):
                sym = futures[future]
                try:
                    sigs = future.result()
                    all_signals.extend(sigs)
                    if sigs:
                        symbols_with_signals.add(sym)
                    done += 1
                    with _status_lock:
                        _run_status["progress"] = done
                        _run_status["log"].append(f"[{done}/{n_stocks}] {sym}: {len(sigs)} signals")
                        if _run_status.get("job") is not None:
                            _run_status["job"]["processed"] = done
                            _run_status["job"]["current_symbol"] = sym
                            _run_status["job"]["updated_at"] = datetime.now(timezone.utc).isoformat()
                    if progress_callback is not None:
                        try:
                            progress_callback(done, n_stocks)
                        except Exception:
                            log.warning("[validation] progress_callback raised — ignored, run continues")
                    if _fence_check is not None:
                        fenced = False
                        try:
                            fenced = bool(_fence_check())
                        except Exception:
                            log.warning("[validation] _fence_check raised — treating as fenced-out")
                            fenced = True
                        if fenced:
                            for f in futures:
                                f.cancel()
                            log.warning(
                                "[validation] fencing lost mid-run (done=%d/%d) — aborting before "
                                "any persistence, no result will be computed further",
                                done, n_stocks,
                            )
                            raise _FencedOutDuringComputation()
                except _FencedOutDuringComputation:
                    raise
                except Exception:
                    log.exception(
                        "[validation] symbol backtest raised — symbol=%s market=%s "
                        "universe=%s horizon=%s",
                        sym, market, universe, horizon,
                    )
                    # V-FRESH1B — a truly unexpected exception escaping
                    # _backtest_stock's own outer try/except (which already
                    # catches everything internal) means this symbol's
                    # diagnostic was never set — classify it here so every
                    # requested symbol always has exactly one terminal
                    # path, preserving the reconciliation invariant.
                    if diag_by_symbol.get(sym, {}).get("terminal_path") is None:
                        diag_by_symbol.setdefault(sym, {})["terminal_path"] = "calculation_exception"
                    done += 1
                    with _status_lock:
                        _run_status["progress"] = done
                        _run_status["log"].append(
                            f"[{done}/{n_stocks}] {sym}: ERROR SYMBOL_VALIDATION_FAILED"
                        )
                        if _run_status.get("job") is not None:
                            _run_status["job"]["processed"] = done
                            _run_status["job"]["current_symbol"] = sym
                            _run_status["job"]["updated_at"] = datetime.now(timezone.utc).isoformat()

        signal_windows_considered = sum(s["considered"] for s in window_stats_by_symbol.values())
        benchmark_valid_signal_windows = sum(s["benchmark_valid"] for s in window_stats_by_symbol.values())
        benchmark_signal_coverage_pct = (
            round(benchmark_valid_signal_windows / signal_windows_considered * 100, 2)
            if signal_windows_considered > 0 else None
        )

        # Finding D — post-alignment, whole-run coverage gate. This can
        # only be evaluated after every symbol's backtest has actually
        # run (unlike the acquisition-level gate above, which prevents
        # the stock work from starting at all). Zero benchmark-valid
        # signal windows, or coverage below the documented minimum, must
        # never be persisted as a completed successful run — the failed
        # attempt must not overwrite the latest previously completed
        # valid result (get_latest_results only ever reads committed
        # val_runs rows, so simply writing nothing here already
        # guarantees that).
        insufficient_signal_coverage = (
            benchmark_valid_signal_windows == 0
            or benchmark_signal_coverage_pct is None
            or benchmark_signal_coverage_pct < BENCHMARK_MIN_SIGNAL_COVERAGE_PCT
        )
        if insufficient_signal_coverage:
            log.warning(
                "[validation] benchmark alignment coverage insufficient, failing run closed — "
                "considered=%d benchmark_valid=%d coverage_pct=%s min_required=%s "
                "ticker=%s market=%s horizon=%s universe=%s",
                signal_windows_considered, benchmark_valid_signal_windows,
                benchmark_signal_coverage_pct, BENCHMARK_MIN_SIGNAL_COVERAGE_PCT,
                benchmark_ticker, market, horizon, universe,
            )
            with _status_lock:
                _run_status.update({
                    "running": False,
                    "log": _run_status["log"] + [
                        f"❌ Failed: "
                        f"{VALIDATION_PUBLIC_FAILURE_MESSAGES['BENCHMARK_ALIGNMENT_COVERAGE_INSUFFICIENT']}"
                    ],
                })
                if _run_status.get("job") is not None:
                    _run_status["job"].update({
                        "status": "failed",
                        "failure_code": "BENCHMARK_ALIGNMENT_COVERAGE_INSUFFICIENT",
                        "failure_message":
                            VALIDATION_PUBLIC_FAILURE_MESSAGES["BENCHMARK_ALIGNMENT_COVERAGE_INSUFFICIENT"],
                        "benchmark_evidence": {
                            **asdict(evidence),
                            "signal_windows_considered": signal_windows_considered,
                            "benchmark_valid_signal_windows": benchmark_valid_signal_windows,
                            "benchmark_signal_coverage_pct": benchmark_signal_coverage_pct,
                            "min_benchmark_signal_coverage_pct": BENCHMARK_MIN_SIGNAL_COVERAGE_PCT,
                        },
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
            # No _compute_metrics call, no val_runs/val_signals write — the
            # run slot is already released and the job marked terminal
            # above. Raised INSIDE this try/except deliberately (unlike
            # the acquisition-level gate, this can only be evaluated after
            # the stock work above has run) — the outer except below
            # recognizes failure_code is already set and does not
            # overwrite it with RUN_EXCEPTION.
            raise ValueError(
                f"run_validation: benchmark alignment coverage insufficient — "
                f"considered={signal_windows_considered} valid={benchmark_valid_signal_windows} "
                f"coverage_pct={benchmark_signal_coverage_pct} "
                f"min_required={BENCHMARK_MIN_SIGNAL_COVERAGE_PCT} — "
                f"refusing to persist a result built on inadequate benchmark evidence"
            )

        metrics = _compute_metrics(
            all_signals, benchmark_avg_ret, horizon, market=market,
            signals_excluded_benchmark=len(excluded_benchmark),
        )
        metrics["signal_windows_considered"] = signal_windows_considered
        metrics["benchmark_valid_signal_windows"] = benchmark_valid_signal_windows
        metrics["benchmark_signal_coverage_pct"] = benchmark_signal_coverage_pct
        metrics["horizon"]   = horizon
        metrics["universe"]  = universe
        metrics["n_stocks_tested"] = n_stocks  # kept unchanged for backward compat — see below
        # n_stocks_tested has historically reported the size of the requested
        # universe, not how many symbols actually returned usable signals.
        # These two fields make that distinction explicit without changing
        # n_stocks_tested's existing meaning or touching persisted schema
        # (both live inside the JSON/JSONB summary column).
        metrics["n_stocks_requested"]     = n_stocks
        metrics["n_stocks_with_signals"]  = len(symbols_with_signals)
        metrics["validation_evidence"] = _build_validation_evidence(
            n_stocks, diag_by_symbol, window_stats_by_symbol, symbols_with_signals, all_signals,
        )
        metrics["run_at"] = datetime.now(timezone.utc).isoformat()
        # benchmark_data_available/benchmark_unavailable_reason are always
        # True/None on this path now — an unavailable/invalid benchmark
        # already failed the run closed above, before any of this code
        # runs. Kept as explicit keys for backward-compatible consumers
        # that read them directly; `benchmark_evidence` below is the new,
        # versioned, fuller provenance record for this same fact.
        metrics["benchmark_data_available"] = benchmark_data_available
        metrics["benchmark_unavailable_reason"] = benchmark_unavailable_reason
        metrics["benchmark_avg_fwd_return_pct"] = round(benchmark_avg_ret, 2) if benchmark_data_available else None
        metrics["nifty_avg_fwd_return_pct"]     = round(benchmark_avg_ret, 2) if benchmark_data_available else None  # backward compat
        metrics["benchmark_evidence"] = asdict(evidence)

        # Persist the full job identity in the run summary (additive JSON —
        # no schema change) so a stored result is permanently bound to its
        # exact market/universe/horizon/model/methodology versions.
        job_snapshot = dict(job)
        job_snapshot["status"] = "completed"
        job_snapshot["completed_at"] = metrics["run_at"]
        job_snapshot["processed"] = done
        metrics["job"] = job_snapshot
        metrics["market"] = market

        # Persist — convert numpy scalars to native Python first
        def _jsonify(obj):
            if isinstance(obj, dict):   return {k: _jsonify(v) for k, v in obj.items()}
            if isinstance(obj, list):   return [_jsonify(v) for v in obj]
            if isinstance(obj, np.integer):  return int(obj)
            if isinstance(obj, np.floating): return None if np.isnan(obj) or np.isinf(obj) else float(obj)
            if isinstance(obj, float) and (obj != obj or obj == float("inf") or obj == float("-inf")):
                return None
            return obj

        clean_metrics = _jsonify(metrics)
        signal_rows = [
            (s["symbol"], s["horizon"], s["signal_date"],
             s["composite_score"], s["tech_score"], s["rs_score"],
             s["obv_score"], s["mfi_score"], s["predicted"],
             s["fwd_return_pct"], s.get("nifty_fwd_ret_pct"), s.get("alpha_pct"),
             s["actual_direction"], s["correct"])
            for s in all_signals
        ]

        if not _persist:
            # V-SCHED1C1 ledger-backed path — computation only. No val_runs/
            # val_signals row is written here; the caller must hand this
            # payload to complete_running_attempt_with_computed_result(),
            # which performs the insert atomically with its own fencing
            # re-check. Nothing about this branch touches the database.
            metrics["_persist_payload"] = {
                "run_at": metrics["run_at"],
                "horizon": horizon,
                "n_stocks": n_stocks,
                "n_signals": len(all_signals),
                "summary_json": json.dumps(clean_metrics),
                "universe": universe,
                "signal_rows": signal_rows,
            }
            with _status_lock:
                _run_status.update({"running": False, "log": _run_status["log"] + ["✅ Validation complete"]})
                if _run_status.get("job") is not None:
                    _run_status["job"].update({
                        "status": "completed",
                        "completed_at": job_snapshot["completed_at"],
                        "processed": done,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
            return metrics

        with _db_lock:
            if _USE_POSTGRES:
                conn = _pg_conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
                            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                            (metrics["run_at"], horizon, n_stocks, len(all_signals),
                             json.dumps(clean_metrics), universe)
                        )
                        run_id = cur.fetchone()[0]
                        cur.executemany(
                            """INSERT INTO val_signals
                               (run_id, symbol, horizon, signal_date, composite_score,
                                tech_score, rs_score, obv_score, mfi_score,
                                predicted, fwd_return_pct, nifty_fwd_ret_pct, alpha_pct,
                                actual_direction, correct)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                            [(run_id,) + r for r in signal_rows]
                        )
                finally:
                    conn.close()
            else:
                with _get_sqlite_conn() as conn:
                    cur = conn.execute(
                        "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) VALUES (?,?,?,?,?,?)",
                        (metrics["run_at"], horizon, n_stocks, len(all_signals),
                         json.dumps(clean_metrics), universe)
                    )
                    run_id = cur.lastrowid
                    conn.executemany(
                        """INSERT INTO val_signals
                           (run_id, symbol, horizon, signal_date, composite_score,
                            tech_score, rs_score, obv_score, mfi_score,
                            predicted, fwd_return_pct, nifty_fwd_ret_pct, alpha_pct,
                            actual_direction, correct)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        [(run_id,) + r for r in signal_rows]
                    )

        metrics["run_id"] = run_id

        with _status_lock:
            _run_status.update({"running": False, "log": _run_status["log"] + ["✅ Validation complete"]})
            if _run_status.get("job") is not None:
                _run_status["job"].update({
                    "status": "completed",
                    "completed_at": job_snapshot["completed_at"],
                    "processed": done,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })

        return metrics

    except Exception:
        log.exception(
            "[validation] run_validation failed — horizon=%s universe=%s market=%s",
            horizon, universe, market,
        )
        with _status_lock:
            # Idempotence guard (2026-07-26 hardening): the post-alignment
            # coverage gate above deliberately raises INSIDE this same
            # try/except (it can only be evaluated after the stock work
            # has run) after already marking the job failed with a more
            # specific code (BENCHMARK_ALIGNMENT_COVERAGE_INSUFFICIENT).
            # Without this guard, this generic handler would unconditionally
            # overwrite that specific code with RUN_EXCEPTION on its way
            # back up — exactly the bug already fixed once for the
            # acquisition-level gate (by moving it outside this try/except
            # entirely); this guard covers any future inner code that
            # follows the same "mark specific, then raise" pattern from
            # inside the try.
            already_marked_failed = (
                _run_status.get("job") is not None
                and _run_status["job"].get("failure_code") is not None
            )
            if already_marked_failed:
                _run_status["running"] = False
            else:
                _run_status.update({
                    "running": False,
                    "log": _run_status["log"] + [
                        f"❌ Failed: {VALIDATION_PUBLIC_FAILURE_MESSAGES['RUN_EXCEPTION']}"
                    ],
                })
                if _run_status.get("job") is not None:
                    _run_status["job"].update({
                        "status": "failed",
                        "failure_code": "RUN_EXCEPTION",
                        "failure_message": VALIDATION_PUBLIC_FAILURE_MESSAGES["RUN_EXCEPTION"],
                        "completed_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    })
        raise


def _fetchone(sql_pg: str, sql_sq: str, params=()):
    if _USE_POSTGRES:
        conn = _pg_conn()
        try:
            return conn.execute(sql_pg, params).fetchone()
        finally:
            conn.close()
    with _get_sqlite_conn() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql_sq, params).fetchone()


def _fetchall(sql_pg: str, sql_sq: str, params=()):
    if _USE_POSTGRES:
        conn = _pg_conn()
        try:
            return conn.execute(sql_pg, params).fetchall()
        finally:
            conn.close()
    with _get_sqlite_conn() as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(sql_sq, params).fetchall()


def _compute_freshness(data: dict) -> dict:
    """V-FRESH1B — Option A (durable metadata-and-disclosure foundation,
    NOT a complete freshness classifier). Derived at READ time from
    already-persisted data, never stored — the policy may evolve
    independently of rows already written.

    Deliberately never returns "fresh", "stale", "schedule-consistent",
    "schedule-missed", "within_expected_cadence" or
    "past_expected_cadence" — all of those require a trusted exchange-
    calendar and a completion-SLO/grace-period contract, neither of
    which exists yet (V-FRESH1A/V-FRESH1A2 forensic findings). Every
    branch here resolves to "unknown" with one specific, honest reason:
      - short horizon: no automatic schedule exists at all —
        "schedule_not_defined", regardless of evidence presence.
      - medium/long, no `validation_evidence` (every row persisted
        before this phase): "legacy_run_without_evidence_metadata".
      - medium/long, `validation_evidence` present (future runs only):
        "calendar_or_completion_slo_unavailable" — the factual dates/
        counters exist, but nothing here can yet judge them against a
        trusted calendar or a completion-SLO.

    `available: true` with `freshness.status: "unknown"` is valid and
    expected — availability and freshness are independent dimensions.
    """
    run_horizon = data.get("horizon")
    evidence = data.get("validation_evidence")

    if run_horizon == "short":
        reason = "schedule_not_defined"
    elif evidence is None:
        reason = "legacy_run_without_evidence_metadata"
    else:
        reason = "calendar_or_completion_slo_unavailable"

    return {
        "status": "unknown",
        "reason": reason,
        "validation_completed_at": data.get("run_at"),
        "input_data_recency": "unknown",
        "outcome_evidence_recency": "unknown",
    }


def get_latest_results(horizon: str | None = None, universe: str = "nifty100") -> dict:
    """Return the most recent validation summary (or per-horizon breakdown) for a given universe.

    V-SNAP1B — additionally returns the canonical database `run_id`
    (the selected val_runs.id), authoritative over any value that may
    happen to already exist inside the stored summary JSON (set last,
    below, so it can never be overridden by JSON content). Eligibility
    (`summary IS NOT NULL`) matches resolve_eligible_run_id()'s own
    predicate exactly — see that function's docstring for why this is
    the correct, proven eligibility definition and not an invented one.
    """
    try:
        _init_db()
        if horizon:
            row = _fetchone(
                "SELECT id, summary FROM val_runs WHERE horizon=%s AND universe=%s AND summary IS NOT NULL ORDER BY id DESC LIMIT 1",
                "SELECT id, summary FROM val_runs WHERE horizon=? AND universe=? AND summary IS NOT NULL ORDER BY id DESC LIMIT 1",
                (horizon, universe)
            )
        else:
            row = _fetchone(
                "SELECT id, summary FROM val_runs WHERE universe=%s AND summary IS NOT NULL ORDER BY id DESC LIMIT 1",
                "SELECT id, summary FROM val_runs WHERE universe=? AND summary IS NOT NULL ORDER BY id DESC LIMIT 1",
                (universe,)
            )
        if not row:
            return {"available": False, "message": "No validation run found. Run /api/validation/run first."}
        db_run_id = row[0] if _USE_POSTGRES else row["id"]
        summary = row[1] if _USE_POSTGRES else row["summary"]
        data = summary if isinstance(summary, dict) else json.loads(summary)
        # Defense-in-depth for rows persisted before public diagnostic
        # sanitization existed — never rewrites the stored row (`data` is a
        # freshly deserialized copy for this call only), only sanitizes the
        # value about to be returned. See _sanitize_benchmark_unavailable_reason.
        if "benchmark_unavailable_reason" in data:
            data["benchmark_unavailable_reason"] = _sanitize_benchmark_unavailable_reason(
                data["benchmark_unavailable_reason"]
            )
        # Legacy provenance: a row persisted before the benchmark-evidence
        # contract existed carries no `benchmark_evidence` key at all — it
        # must never be presented as if it had passed today's checks.
        # Truthfully labelled `legacy_unknown` with no version, not
        # retroactively upgraded and not hidden from consumers.
        if "benchmark_evidence" not in data:
            data["benchmark_evidence"] = {
                "status": "legacy_unknown",
                "reason": "persisted before the benchmark evidence contract existed",
                "methodology_version": None,
            }
        # V-SNAP1B — set LAST, after spreading nothing yet: this is the
        # authoritative database identity, always wins over any run_id
        # the stored JSON might already happen to contain.
        data["run_id"] = db_run_id
        # V-FRESH1B — additive, read-time-derived honest disclosure (never
        # stored — policy may evolve independently of persisted rows).
        data["freshness"] = _compute_freshness(data)
        return {"available": True, **data}
    except Exception as e:
        return {"available": False, "error": safe_error_message(
            log, "validation_engine.get_latest_results", e, "Validation data is temporarily unavailable.")}


# 2026-07-17: connects Daily Picks' displayed confidence to real, walk-forward
# validated track record — additive only, does not touch confidence itself,
# the 25% BUY cutoff, or any existing consumer of get_latest_results().
#
# Deliberately NOT sourced from prediction_engine.py's confidence_score /
# historical_factor_reliability: that component's only real-data path reads
# from the same legacy IC-training join the 2026-07-12 forensic audit found
# contaminated (~1.52x duplicate-inflated), and is active for exactly one of
# six market/horizon combinations in production today (IN/short — the other
# five silently default to a neutral 50). This function instead reads
# val_runs, the walk-forward backtest table these numbers were verified
# against directly in this session (six runs, ~30k combined signals).
_MARKET_UNIVERSES = {"IN": ("nifty100", "midcap"), "US": ("us",)}


def get_track_record_summary(market: str, horizon: str) -> list[dict]:
    """Real, validated track record for every universe backtested for this
    market/horizon — one entry per universe with an available run, empty
    list if none exist yet. Each entry: universe, beat_benchmark_pct,
    buy_hit_rate_pct, n_signals, run_at, plus (DP-026 remediation) the
    point-in-time disclosure fields the UI needs to distinguish a
    genuinely-remediated result from a legacy/still-contaminated one:
    fundamentals_point_in_time, fundamentals_point_in_time_coverage_pct
    (US only — real measured value from that run, never a guess),
    dp026_status. Never raises — a lookup failure for one universe is
    simply omitted, matching get_latest_results()'s own fail-soft
    contract. A legacy run persisted before this session (no
    data_limitations at all) reports fundamentals_point_in_time=None —
    distinct from both True and False — so the UI can render an accurate
    third "legacy, pre-remediation result" state rather than guessing."""
    out = []
    for universe in _MARKET_UNIVERSES.get(market, ()):
        try:
            res = get_latest_results(horizon=horizon, universe=universe)
        except Exception:
            continue
        if not res.get("available"):
            continue
        dl = res.get("data_limitations")
        out.append({
            "universe": universe,
            "beat_benchmark_pct": res.get("beat_benchmark_pct"),
            "buy_hit_rate_pct": res.get("buy_hit_rate_pct"),
            "n_signals": res.get("buy_signals"),
            "run_at": res.get("run_at"),
            "fundamentals_point_in_time": dl.get("fundamentals_point_in_time") if dl else None,
            "fundamentals_point_in_time_coverage_pct": dl.get("fundamentals_point_in_time_coverage_pct") if dl else None,
            "dp026_status": dl.get("status") if dl else None,
            "scoring_policy_version": dl.get("scoring_policy_version") if dl else None,
        })
    return out


def resolve_eligible_run_id(run_id: int | None, horizon: str, universe: str) -> int | None:
    """V-SNAP1B — resolve and validate the run to use for a request,
    using the SAME eligibility definition as get_latest_results()'s own
    WHERE clause: `summary IS NOT NULL`.

    This is the proven eligibility rule (not an invented one): every
    val_runs row is inserted exactly once, atomically, by
    run_validation()'s single INSERT — with `summary` already fully
    computed in the SAME statement (see that function's persistence
    block). There is no "running"/"failed"/partial row state
    representable in this schema at all; a row either does not exist
    yet, or already has a non-null summary. `summary IS NOT NULL` is
    therefore always true today, but is enforced explicitly (not
    assumed) for the same defensive reason V-VAL1/V-PS2 filter on
    `correct IS NOT NULL`/finite-return checks elsewhere in this file.

    If `run_id` is given, it must additionally belong to the requested
    horizon+universe and be eligible, or None is returned — the caller
    must fail closed (never silently substitute the latest run). If
    `run_id` is None, resolves the latest eligible run for
    horizon+universe, or None if none exists. All queries are
    parameterized and bounded (`LIMIT 1` / an exact primary-key lookup).
    """
    _init_db()
    if run_id is not None:
        row = _fetchone(
            "SELECT id FROM val_runs WHERE id=%s AND horizon=%s AND universe=%s AND summary IS NOT NULL",
            "SELECT id FROM val_runs WHERE id=? AND horizon=? AND universe=? AND summary IS NOT NULL",
            (run_id, horizon, universe)
        )
        return run_id if row else None
    row = _fetchone(
        "SELECT id FROM val_runs WHERE horizon=%s AND universe=%s AND summary IS NOT NULL ORDER BY id DESC LIMIT 1",
        "SELECT id FROM val_runs WHERE horizon=? AND universe=? AND summary IS NOT NULL ORDER BY id DESC LIMIT 1",
        (horizon, universe)
    )
    if not row:
        return None
    return row[0] if _USE_POSTGRES else row["id"]


def get_per_stock_results(run_id: int | None = None, horizon: str = "medium", universe: str = "nifty100") -> list[dict]:
    """Return per-stock BUY hit rate and average return for the latest (or given) run in a universe.

    Cohort contract (V-VAL1):
      buy_signal_count   — every persisted BUY row for the symbol.
      evaluated_buy_count — BUY rows with a non-null `correct` outcome.
      buy_hits           — evaluated BUY rows classified correct.
      hit_rate_pct        = buy_hits / evaluated_buy_count * 100, or None
                             if evaluated_buy_count is 0 (never a fabricated 0%).
      buy_return_count    — BUY rows with a non-null `fwd_return_pct`.
      buy_avg_return_pct  — mean fwd_return_pct over buy_return_count, or
                             None if buy_return_count is 0.

    As of this writing, run_validation()'s per-signal loop only ever
    appends a signal (and therefore only ever persists a val_signals row)
    once `correct` and `fwd_return_pct` are both fully computed — a window
    whose evidence is unavailable is skipped via `continue` before
    appending, never persisted with a null outcome. So
    evaluated_buy_count == buy_signal_count and buy_return_count ==
    buy_signal_count hold for every row this codebase currently writes.
    That is an application invariant, not a schema guarantee (`correct`
    and `fwd_return_pct` are nullable columns) — the query below filters
    on `IS NOT NULL` explicitly rather than assuming the invariant, so a
    future violation (a manual insert, a new write path) would show up
    as evaluated_buy_count/buy_return_count genuinely differing from
    buy_signal_count, not as a silently wrong percentage.
    """
    try:
        _init_db()
        if run_id is None:
            # V-SNAP1C — reuse resolve_eligible_run_id() rather than a
            # separate ad hoc "latest run" query, so this internal
            # fallback path (used directly by get_single_stock_accuracy,
            # which has no run_id to pin against) applies the SAME proven
            # `summary IS NOT NULL` eligibility definition as
            # get_latest_results()/the /results/stocks router path —
            # closing a dormant inconsistency the V-SNAP1B independent
            # review flagged (unreachable today per that function's own
            # docstring, since every row is written with `summary`
            # already populated, but no longer merely assumed here).
            # Passing run_id=None here resolves latest-eligible, exactly
            # mirroring this branch's prior behavior and error contract
            # (empty list when no eligible run exists) — not a second,
            # ambiguous resolution of an already-given run_id.
            run_id = resolve_eligible_run_id(None, horizon, universe)
            if run_id is None:
                return []

        # V-PS2 — a historical NaN/Infinity fwd_return_pct (root-caused
        # against production runs 227/228, see the V-PS1/V-PS2 phases)
        # poisons AVG() for that whole symbol and crashes JSON
        # serialization for the whole endpoint. The `clean` CTE nulls out
        # any non-finite fwd_return_pct BEFORE it reaches any aggregate —
        # `x = x` excludes NaN (false for NaN in both dialects; SQLite
        # returns NULL rather than 0 for it, which is equally not-true
        # inside a CASE WHEN, proven in
        # test_sqlite_actually_stores_and_compares_nan_as_expected), and
        # the bound +/-Infinity parameters (not string literals, so they
        # bind identically in both dialects) exclude both infinities. A
        # row nulled out here is treated exactly like a genuinely missing
        # fwd_return_pct by every existing downstream `IS NOT NULL`
        # check — no formula below this point changes.
        _POS_INF, _NEG_INF = float("inf"), float("-inf")
        rows = _fetchall(
            """WITH clean AS (
                   SELECT symbol, predicted, correct,
                          CASE WHEN fwd_return_pct = fwd_return_pct
                                AND fwd_return_pct < %s AND fwd_return_pct > %s
                               THEN fwd_return_pct END AS fwd_return_pct
                   FROM val_signals
                   WHERE run_id=%s AND horizon=%s
               )
               SELECT symbol,
                      COUNT(*) AS total,
                      AVG(fwd_return_pct) AS avg_ret,
                      COUNT(CASE WHEN predicted='BUY' THEN 1 END) AS buy_signal_count,
                      COUNT(CASE WHEN predicted='BUY' AND correct IS NOT NULL AND fwd_return_pct IS NOT NULL THEN 1 END) AS evaluated_buy_count,
                      SUM(CASE WHEN predicted='BUY' AND correct IS NOT NULL AND fwd_return_pct IS NOT NULL THEN correct END) AS buy_hits,
                      COUNT(CASE WHEN predicted='BUY' AND fwd_return_pct IS NOT NULL THEN 1 END) AS buy_return_count,
                      AVG(CASE WHEN predicted='BUY' AND fwd_return_pct IS NOT NULL THEN fwd_return_pct END) AS buy_avg_ret
               FROM clean
               GROUP BY symbol
               ORDER BY buy_avg_ret DESC NULLS LAST, symbol ASC""",
            """WITH clean AS (
                   SELECT symbol, predicted, correct,
                          CASE WHEN fwd_return_pct = fwd_return_pct
                                AND fwd_return_pct < ? AND fwd_return_pct > ?
                               THEN fwd_return_pct END AS fwd_return_pct
                   FROM val_signals
                   WHERE run_id=? AND horizon=?
               )
               SELECT symbol,
                      COUNT(*) AS total,
                      AVG(fwd_return_pct) AS avg_ret,
                      COUNT(CASE WHEN predicted='BUY' THEN 1 END) AS buy_signal_count,
                      COUNT(CASE WHEN predicted='BUY' AND correct IS NOT NULL AND fwd_return_pct IS NOT NULL THEN 1 END) AS evaluated_buy_count,
                      SUM(CASE WHEN predicted='BUY' AND correct IS NOT NULL AND fwd_return_pct IS NOT NULL THEN correct END) AS buy_hits,
                      COUNT(CASE WHEN predicted='BUY' AND fwd_return_pct IS NOT NULL THEN 1 END) AS buy_return_count,
                      AVG(CASE WHEN predicted='BUY' AND fwd_return_pct IS NOT NULL THEN fwd_return_pct END) AS buy_avg_ret
               FROM clean
               GROUP BY symbol
               ORDER BY buy_avg_ret DESC NULLS LAST, symbol ASC""",
            (_POS_INF, _NEG_INF, run_id, horizon)
        )

        def _v(r, key, idx):
            return r[idx] if _USE_POSTGRES else r[key]

        results = []
        for r in rows:
            evaluated_buy_count = _v(r, "evaluated_buy_count", 4)
            buy_hits = _v(r, "buy_hits", 5)
            buy_return_count = _v(r, "buy_return_count", 6)
            buy_avg_ret = _v(r, "buy_avg_ret", 7)
            results.append({
                "symbol":             _v(r, "symbol", 0),
                "total_signals":      _v(r, "total", 1),
                "avg_fwd_return_pct": round(_v(r, "avg_ret", 2), 2) if _v(r, "avg_ret", 2) is not None else None,
                "buy_signal_count":   _v(r, "buy_signal_count", 3),
                "evaluated_buy_count": evaluated_buy_count,
                "buy_hits":           buy_hits,
                # Legacy compatibility alias — this endpoint is a public API
                # surface (get_per_stock_results is called directly by
                # /api/validation/results/stocks and /results/stock/{symbol};
                # an external caller may exist beyond this repo's own
                # frontend, which no longer reads this key). Same value as
                # buy_hits under its pre-V-VAL1 name; do not remove.
                "correct":            buy_hits,
                "hit_rate_pct":       round(buy_hits / evaluated_buy_count * 100, 1) if evaluated_buy_count else None,
                "buy_return_count":   buy_return_count,
                "buy_avg_return_pct": round(buy_avg_ret, 2) if buy_avg_ret is not None else None,
            })
        return results
    except Exception:
        return []


def get_run_status() -> dict:
    with _status_lock:
        snapshot = dict(_run_status)
        # Copy mutable members — a status snapshot must be immutable once
        # returned; the live job/log must not mutate under a caller's feet.
        if snapshot.get("job") is not None:
            snapshot["job"] = dict(snapshot["job"])
            # Defense-in-depth at the public API boundary: failure_code is
            # the source of truth for what's safe to expose publicly — a
            # future internal caller that (incorrectly) writes a raw
            # exception into failure_message directly, without going
            # through VALIDATION_PUBLIC_FAILURE_MESSAGES, can never leak it
            # here, since this always re-derives the public message from
            # the code rather than trusting whatever failure_message holds.
            code = snapshot["job"].get("failure_code")
            if code is not None:
                snapshot["job"]["failure_message"] = VALIDATION_PUBLIC_FAILURE_MESSAGES.get(
                    code, VALIDATION_PUBLIC_FAILURE_MESSAGES["RUN_EXCEPTION"]
                )
        if isinstance(snapshot.get("log"), list):
            snapshot["log"] = list(snapshot["log"])
        return snapshot


def get_last_run_time(horizon: str = "medium", universe: str | None = None):
    """Return the datetime of the most recent completed run for this horizon, or None.

    `universe=None` preserves the legacy any-universe behavior (used by the
    startup catch-up check); pass an explicit universe to scope the lookup —
    a US run must never satisfy a "when did nifty100 last run?" query.
    """
    from datetime import datetime, timezone
    try:
        _init_db()
        if universe is not None:
            rows = _fetchall(
                "SELECT run_at FROM val_runs WHERE horizon = %s AND universe = %s ORDER BY id DESC LIMIT 1",
                "SELECT run_at FROM val_runs WHERE horizon = ? AND universe = ? ORDER BY id DESC LIMIT 1",
                (horizon, universe),
            )
        else:
            rows = _fetchall(
                "SELECT run_at FROM val_runs WHERE horizon = %s ORDER BY id DESC LIMIT 1",
                "SELECT run_at FROM val_runs WHERE horizon = ? ORDER BY id DESC LIMIT 1",
                (horizon,),
            )
        if not rows:
            return None
        run_at_raw = rows[0][0] if _USE_POSTGRES else rows[0]["run_at"]
        if isinstance(run_at_raw, datetime):
            return run_at_raw.astimezone(timezone.utc).replace(tzinfo=timezone.utc)
        # Parse ISO string
        s = str(run_at_raw)
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("get_last_run_time failed: %s", e)
        return None


def get_all_run_summaries() -> list[dict]:
    """List of all past validation runs with key metrics."""
    try:
        _init_db()
        rows = _fetchall(
            "SELECT id, run_at, horizon, n_stocks, n_signals, summary, universe FROM val_runs ORDER BY id DESC LIMIT 20",
            "SELECT id, run_at, horizon, n_stocks, n_signals, summary, universe FROM val_runs ORDER BY id DESC LIMIT 20",
        )
        results = []
        for r in rows:
            if _USE_POSTGRES:
                rid, rat, hor, ns, nsig, summ, univ = r
                s = summ if isinstance(summ, dict) else json.loads(summ)
            else:
                rid, rat, hor, ns, nsig, univ = r["id"], r["run_at"], r["horizon"], r["n_stocks"], r["n_signals"], r["universe"]
                s = json.loads(r["summary"])
            results.append({
                "run_id": rid, "run_at": str(rat), "horizon": hor,
                "universe": univ,
                "market": UNIVERSE_MARKET.get(univ),
                "n_stocks": ns, "n_signals": nsig,
                "buy_hit_rate_pct":      s.get("buy_hit_rate_pct"),
                "avg_return_on_buy_pct": s.get("avg_return_on_buy_pct"),
                "avg_alpha_on_buy_pct":  s.get("avg_alpha_on_buy_pct"),
                "beat_benchmark_pct":    s.get("beat_benchmark_pct"),
                "sharpe_on_buys":        s.get("sharpe_on_buys"),
            })
        return results
    except Exception:
        return []




# ── V-SCHED1B — durable validation scheduling ledger (inert foundation) ───────
# Not called by _validation_schedule_loop, _catchup_validation, or
# run_validation() in this phase — see V-SCHED1C for integration.
#
# CORRECTION (post-second-independent-review): the global lease now owns a
# durable `active_attempt_id` binding. NO scheduled or manual attempt may
# be created without FIRST holding the global lease, verified atomically
# inside the SAME transaction that creates the attempt — closing the gap
# where two different slots could each independently reach 'running'
# before either caller ever contended for the global lease. At most one
# claimed/running attempt can exist system-wide at any time: the lease
# row's active_attempt_id is the single source of truth for that
# invariant, set in the same transaction as attempt creation and cleared
# in the same transaction as every terminal attempt transition.
#
# A process that crashes after admission (attempt created, lease bound)
# but before completion leaves the lease's active_attempt_id pointing at
# a now-stale attempt. Lease reclaim (acquire_validation_execution_lease)
# deliberately does NOT clear this on its own — it surfaces it via
# `recovery_required`/`stale_active_attempt_id` in its return value, and
# the new owner must explicitly call recover_stale_active_attempt() before
# any new attempt can be admitted. This makes the recovery an explicit,
# auditable step rather than a silent side effect of reclaiming the lease.
#
# Three deliberately separate entities (see V-SCHED1A2's forensic report
# for why a single-table model was rejected):
#   - validation_schedule_slots    — canonical scheduled obligation.
#   - validation_schedule_attempts — auditable execution attempt, bound
#     (scheduler/catchup) or unbound (manual, slot_id NULL, horizon/
#     universe durably recorded on the attempt itself).
#   - validation_execution_leases  — single global singleton, now with a
#     durable active-attempt binding enforcing true global admission.
#
# Lock ordering (PostgreSQL) — every multi-row ledger transaction acquires
# row locks in this SAME order, with no exception:
#     1. validation_execution_leases (the single 'validation-global' row)
#     2. validation_schedule_attempts, if an existing attempt is involved
#     3. validation_schedule_slots, if the attempt is bound to a slot
#     4. val_runs, only for complete_attempt_with_result, after 1-3
# create_schedule_attempt/create_manual_attempt lock lease then slot (no
# attempt row exists yet to lock). recover_stale_active_attempt and
# _compound_transition both lock lease then attempt then slot. This single
# consistent order is what prevents an AB-BA deadlock between any two
# concurrent ledger operations — see the second independent review's
# finding that an earlier revision had _compound_transition locking
# attempt-then-lease while recover_stale_active_attempt locked
# lease-then-attempt, a reversal capable of a real PostgreSQL deadlock in
# the "stale worker wakes up while a new owner is recovering it" scenario.

GLOBAL_LEASE_RESOURCE_KEY = "validation-global"

VALID_SLOT_STATUSES = {"due", "running", "completed", "failed", "skipped", "abandoned"}
VALID_ATTEMPT_STATUSES = {"claimed", "running", "completed", "failed", "abandoned"}
VALID_TRIGGER_TYPES = {"scheduler", "catchup", "manual"}
VALID_HORIZONS = {"short", "medium", "long"}


def _require_utc(dt: datetime, *, param: str = "now") -> datetime:
    """Fail-closed timestamp contract: every timestamp accepted by this
    module must be timezone-aware and UTC. A naive datetime is ambiguous
    and is never silently assumed to be UTC."""
    if dt.tzinfo is None:
        raise ValueError(f"{param} must be timezone-aware (UTC) — naive datetimes are rejected, never assumed")
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return _require_utc(dt).isoformat()


def _require_valid_horizon(horizon: str) -> None:
    if horizon not in VALID_HORIZONS:
        raise ValueError(f"horizon must be one of {sorted(VALID_HORIZONS)}, got {horizon!r}")


def _ledger_row_to_dict(row) -> dict | None:
    if row is None:
        return None
    return dict(row)


def _pg_dict_fetchone(cur) -> dict | None:
    row = cur.fetchone()
    if row is None:
        return None
    cols = [d.name for d in cur.description]
    return dict(zip(cols, row))


def _pg_dict_fetchall(cur) -> list[dict]:
    rows = cur.fetchall()
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in rows]


def _fetchone_ledger(sql_pg: str, sql_sq: str, params=()) -> dict | None:
    """Read-only helper — never mutates state."""
    if _USE_POSTGRES:
        conn = _pg_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql_pg, params)
                return _pg_dict_fetchone(cur)
        finally:
            conn.close()
    with _get_ledger_sqlite_conn() as conn:
        row = conn.execute(sql_sq, params).fetchone()
        return _ledger_row_to_dict(row)


_SLOT_COLUMNS = "id, horizon, universe, scheduled_slot, schedule_version, status, active_attempt_id, created_at, updated_at"
_ATTEMPT_COLUMNS = (
    "id, slot_id, horizon, universe, attempt_number, trigger_type, status, lease_owner, "
    "lease_fencing_token, started_at, heartbeat_at, completed_at, result_run_id, "
    "failure_category, failure_summary, created_at, updated_at"
)
_LEASE_COLUMNS = "resource_key, lease_owner, fencing_token, acquired_at, heartbeat_at, expires_at, active_attempt_id, updated_at"


def get_schedule_slot(slot_id: int) -> dict | None:
    """Read-only — never mutates state."""
    return _fetchone_ledger(
        f"SELECT {_SLOT_COLUMNS} FROM validation_schedule_slots WHERE id=%s",
        f"SELECT {_SLOT_COLUMNS} FROM validation_schedule_slots WHERE id=?",
        (slot_id,),
    )


def get_schedule_attempt(attempt_id: int) -> dict | None:
    """Read-only — never mutates state."""
    return _fetchone_ledger(
        f"SELECT {_ATTEMPT_COLUMNS} FROM validation_schedule_attempts WHERE id=%s",
        f"SELECT {_ATTEMPT_COLUMNS} FROM validation_schedule_attempts WHERE id=?",
        (attempt_id,),
    )


def has_established_schedule_baseline(horizon: str, universe: str, schedule_version: str = "v1") -> bool:
    """Read-only — never mutates state. True iff at least one schedule
    slot has EVER been created for this (horizon, universe,
    schedule_version) — regardless of which specific scheduled_slot
    instant. Deliberately NOT scoped to "today's" slot: this answers
    "has the ledger ever established a baseline for this identity", not
    "is there a slot for right now". Used by the startup catch-up
    bootstrap guard (V-SCHED1C1-ROLLOUT1) to distinguish a genuinely
    missed run (a baseline exists, but today's slot is due) from the
    very first deployment ever (no baseline exists yet at all) — the
    catch-up path must never treat "never run before" as "missed a run"."""
    row = _fetchone_ledger(
        "SELECT 1 FROM validation_schedule_slots "
        "WHERE horizon=%s AND universe=%s AND schedule_version=%s LIMIT 1",
        "SELECT 1 FROM validation_schedule_slots "
        "WHERE horizon=? AND universe=? AND schedule_version=? LIMIT 1",
        (horizon, universe, schedule_version),
    )
    return row is not None


def get_validation_execution_lease() -> dict | None:
    """Read-only — never mutates state."""
    return _fetchone_ledger(
        f"SELECT {_LEASE_COLUMNS} FROM validation_execution_leases WHERE resource_key=%s",
        f"SELECT {_LEASE_COLUMNS} FROM validation_execution_leases WHERE resource_key=?",
        (GLOBAL_LEASE_RESOURCE_KEY,),
    )


# ── Canonical scheduled slot — idempotent create (no lease involvement:
# creating the durable slot ROW is not "activating" it — see
# create_schedule_attempt for the lease-gated activation step) ──────────────

def get_or_create_schedule_slot(horizon: str, universe: str, scheduled_slot: datetime,
                                 schedule_version: str, now: datetime) -> dict:
    _require_utc(now, param="now")
    _require_valid_horizon(horizon)
    _require_known_universe(universe)
    slot_iso = _iso(scheduled_slot)
    now_iso = _iso(now)
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO validation_schedule_slots "
                        "(horizon, universe, scheduled_slot, schedule_version, status, created_at, updated_at) "
                        "VALUES (%s,%s,%s,%s,'due',%s,%s) "
                        "ON CONFLICT (horizon, universe, scheduled_slot, schedule_version) DO NOTHING "
                        f"RETURNING {_SLOT_COLUMNS}",
                        (horizon, universe, scheduled_slot, schedule_version, now, now),
                    )
                    row = _pg_dict_fetchone(cur)
                    if row is None:
                        cur.execute(
                            f"SELECT {_SLOT_COLUMNS} FROM validation_schedule_slots "
                            "WHERE horizon=%s AND universe=%s AND scheduled_slot=%s AND schedule_version=%s",
                            (horizon, universe, scheduled_slot, schedule_version),
                        )
                        row = _pg_dict_fetchone(cur)
                    return row
            finally:
                conn.close()
        else:
            with _get_ledger_sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO validation_schedule_slots "
                    "(horizon, universe, scheduled_slot, schedule_version, status, created_at, updated_at) "
                    "VALUES (?,?,?,?,'due',?,?)",
                    (horizon, universe, slot_iso, schedule_version, now_iso, now_iso),
                )
                row = conn.execute(
                    f"SELECT {_SLOT_COLUMNS} FROM validation_schedule_slots "
                    "WHERE horizon=? AND universe=? AND scheduled_slot=? AND schedule_version=?",
                    (horizon, universe, slot_iso, schedule_version),
                ).fetchone()
                return _ledger_row_to_dict(row)


# ── Global execution lease — now the single source of truth for whether
# ANY attempt may be admitted system-wide ─────────────────────────────────────

def acquire_validation_execution_lease(owner: str, now: datetime, lease_duration_seconds: int) -> dict:
    """Atomic CAS acquisition of the single global 'validation-global'
    lease row. Succeeds only if currently unheld (lease_owner IS NULL) or
    expires_at <= now (INCLUSIVE boundary). Every successful acquisition
    strictly increments fencing_token in the same statement.

    Deliberately does NOT touch active_attempt_id on reclaim — if the
    lease being reclaimed still has one bound (the previous holder
    crashed/was killed after admitting an attempt but before it reached a
    terminal state), that identity is preserved and surfaced back to the
    caller via `recovery_required`/`stale_active_attempt_id` rather than
    silently discarded. The caller MUST call recover_stale_active_attempt()
    before create_schedule_attempt/create_manual_attempt will admit
    anything new — both refuse to proceed while active_attempt_id is
    still bound."""
    _require_utc(now, param="now")
    now_iso = _iso(now)
    expires_at = now + timedelta(seconds=lease_duration_seconds)
    expires_iso = _iso(expires_at)
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE validation_execution_leases "
                        "SET lease_owner=%s, fencing_token=fencing_token+1, "
                        "acquired_at=%s, heartbeat_at=%s, expires_at=%s, updated_at=%s "
                        "WHERE resource_key=%s AND (lease_owner IS NULL OR expires_at <= %s) "
                        "RETURNING fencing_token, active_attempt_id",
                        (owner, now, now, expires_at, now, GLOBAL_LEASE_RESOURCE_KEY, now),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return {"ok": False, "reason": "already_leased"}
                    token, stale_id = row[0], row[1]
                    return {
                        "ok": True, "fencing_token": token, "owner": owner, "expires_at": expires_iso,
                        "recovery_required": stale_id is not None, "stale_active_attempt_id": stale_id,
                    }
            finally:
                conn.close()
        else:
            with _get_ledger_sqlite_conn() as conn:
                cur = conn.execute(
                    "UPDATE validation_execution_leases "
                    "SET lease_owner=?, fencing_token=fencing_token+1, "
                    "acquired_at=?, heartbeat_at=?, expires_at=?, updated_at=? "
                    "WHERE resource_key=? AND (lease_owner IS NULL OR expires_at <= ?)",
                    (owner, now_iso, now_iso, expires_iso, now_iso, GLOBAL_LEASE_RESOURCE_KEY, now_iso),
                )
                if cur.rowcount == 0:
                    return {"ok": False, "reason": "already_leased"}
                row = conn.execute(
                    "SELECT fencing_token, active_attempt_id FROM validation_execution_leases WHERE resource_key=?",
                    (GLOBAL_LEASE_RESOURCE_KEY,),
                ).fetchone()
                stale_id = row["active_attempt_id"]
                return {
                    "ok": True, "fencing_token": row["fencing_token"], "owner": owner, "expires_at": expires_iso,
                    "recovery_required": stale_id is not None, "stale_active_attempt_id": stale_id,
                }


def heartbeat_validation_execution_lease(owner: str, fencing_token: int, now: datetime,
                                          lease_duration_seconds: int) -> dict:
    """Renews only if owner+fencing_token still exactly match the current
    row. Deterministic PRIMITIVE only — V-SCHED1B runs no production
    heartbeat loop."""
    _require_utc(now, param="now")
    now_iso = _iso(now)
    expires_at = now + timedelta(seconds=lease_duration_seconds)
    expires_iso = _iso(expires_at)
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE validation_execution_leases "
                        "SET heartbeat_at=%s, expires_at=%s, updated_at=%s "
                        "WHERE resource_key=%s AND lease_owner=%s AND fencing_token=%s "
                        "RETURNING fencing_token",
                        (now, expires_at, now, GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token),
                    )
                    row = cur.fetchone()
                    if row is None:
                        return {"ok": False, "reason": "not_owner_or_stale_token"}
                    return {"ok": True, "fencing_token": row[0], "expires_at": expires_iso}
            finally:
                conn.close()
        else:
            with _get_ledger_sqlite_conn() as conn:
                cur = conn.execute(
                    "UPDATE validation_execution_leases "
                    "SET heartbeat_at=?, expires_at=?, updated_at=? "
                    "WHERE resource_key=? AND lease_owner=? AND fencing_token=?",
                    (now_iso, expires_iso, now_iso, GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token),
                )
                if cur.rowcount == 0:
                    return {"ok": False, "reason": "not_owner_or_stale_token"}
                return {"ok": True, "fencing_token": fencing_token, "expires_at": expires_iso}


def release_validation_execution_lease(owner: str, fencing_token: int, now: datetime) -> dict:
    """Requires the CURRENT owner+fencing_token. Rejects — explicitly,
    with reason 'active_attempt_bound' — while active_attempt_id is
    non-null, so a lease can never be released out from under an attempt
    still believed to be admitted (that would silently orphan it; the
    caller must complete/fail/abandon the attempt, or call
    recover_stale_active_attempt, first). fencing_token is never reset by
    release — it stays monotonically increasing for the row's whole
    history, only ever bumped again by the next successful acquisition."""
    _require_utc(now, param="now")
    now_iso = _iso(now)
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT lease_owner, fencing_token, active_attempt_id "
                            "FROM validation_execution_leases WHERE resource_key=%s FOR UPDATE",
                            (GLOBAL_LEASE_RESOURCE_KEY,),
                        )
                        row = _pg_dict_fetchone(cur)
                        if row is None or row["lease_owner"] != owner or row["fencing_token"] != fencing_token:
                            return {"ok": False, "reason": "not_owner_or_stale_token"}
                        if row["active_attempt_id"] is not None:
                            return {"ok": False, "reason": "active_attempt_bound"}
                        cur.execute(
                            "UPDATE validation_execution_leases "
                            "SET lease_owner=NULL, expires_at=NULL, updated_at=%s WHERE resource_key=%s",
                            (now, GLOBAL_LEASE_RESOURCE_KEY),
                        )
                        return {"ok": True}
            finally:
                conn.close()
        else:
            conn = _get_ledger_sqlite_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    "SELECT lease_owner, fencing_token, active_attempt_id "
                    "FROM validation_execution_leases WHERE resource_key=?",
                    (GLOBAL_LEASE_RESOURCE_KEY,),
                ).fetchone()
                if row is None or row["lease_owner"] != owner or row["fencing_token"] != fencing_token:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_stale_token"}
                if row["active_attempt_id"] is not None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "active_attempt_bound"}
                conn.execute(
                    "UPDATE validation_execution_leases SET lease_owner=NULL, expires_at=NULL, updated_at=? "
                    "WHERE resource_key=?",
                    (now_iso, GLOBAL_LEASE_RESOURCE_KEY),
                )
                conn.execute("COMMIT")
                return {"ok": True}
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()


def recover_stale_active_attempt(owner: str, fencing_token: int, now: datetime, *,
                                  recovery_category: str = "STALE_LEASE_RECOVERY",
                                  recovery_summary: str | None = None) -> dict:
    """Explicit, fenced recovery for an attempt left admitted by a worker
    that crashed/died before reaching a terminal state. The caller MUST
    be the CURRENT lease holder (post-reclaim via acquire_validation_
    execution_lease, whose `recovery_required`/`stale_active_attempt_id`
    fields point here). ONE transaction:
      1. lock the lease row, verify owner+fencing_token match current and
         the lease is unexpired at `now`;
      2. verify active_attempt_id is actually set (else conflict —
         nothing to recover);
      3. lock the stale attempt row, verify it is still claimed/running
         (a benign race: something else may have already resolved it —
         treated as a conflict, never silently re-resolved);
      4. mark it 'abandoned' with the given sanitized category/summary —
         its historical lease_owner/lease_fencing_token (the ORIGINAL
         claimer) are preserved, never overwritten with the new owner's
         identity, so the audit trail stays accurate;
      5. if it was bound to a slot, return that slot to 'due' and clear
         its active_attempt_id (only if still pointing at this attempt);
      6. clear the lease's active_attempt_id so new admission can proceed;
      7. commit."""
    _require_utc(now, param="now")
    now_iso = _iso(now)
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT lease_owner, fencing_token, active_attempt_id "
                            "FROM validation_execution_leases WHERE resource_key=%s FOR UPDATE",
                            (GLOBAL_LEASE_RESOURCE_KEY,),
                        )
                        lease = _pg_dict_fetchone(cur)
                        if lease is None or lease["lease_owner"] != owner or lease["fencing_token"] != fencing_token:
                            return {"ok": False, "reason": "not_owner_or_stale_token"}
                        cur.execute(
                            "SELECT expires_at FROM validation_execution_leases WHERE resource_key=%s",
                            (GLOBAL_LEASE_RESOURCE_KEY,),
                        )
                        expires_row = cur.fetchone()
                        if expires_row is None or expires_row[0] is None or expires_row[0] <= now:
                            return {"ok": False, "reason": "not_owner_or_expired_lease"}
                        stale_attempt_id = lease["active_attempt_id"]
                        if stale_attempt_id is None:
                            return {"ok": False, "reason": "no_stale_attempt"}

                        cur.execute(
                            "SELECT id, slot_id, status FROM validation_schedule_attempts WHERE id=%s FOR UPDATE",
                            (stale_attempt_id,),
                        )
                        attempt = _pg_dict_fetchone(cur)
                        if attempt is None or attempt["status"] not in ("claimed", "running"):
                            return {"ok": False, "reason": "attempt_not_recoverable"}

                        cur.execute(
                            "UPDATE validation_schedule_attempts "
                            "SET status='abandoned', completed_at=%s, failure_category=%s, "
                            "failure_summary=%s, updated_at=%s WHERE id=%s",
                            (now, recovery_category, recovery_summary, now, stale_attempt_id),
                        )
                        slot_id = attempt["slot_id"]
                        if slot_id is not None:
                            cur.execute(
                                "UPDATE validation_schedule_slots "
                                "SET status='due', active_attempt_id=NULL, updated_at=%s "
                                "WHERE id=%s AND active_attempt_id=%s",
                                (now, slot_id, stale_attempt_id),
                            )
                        cur.execute(
                            "UPDATE validation_execution_leases "
                            "SET active_attempt_id=NULL, updated_at=%s "
                            "WHERE resource_key=%s AND lease_owner=%s AND fencing_token=%s",
                            (now, GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token),
                        )
                        return {"ok": True, "recovered_attempt_id": stale_attempt_id, "slot_id": slot_id}
            finally:
                conn.close()
        else:
            conn = _get_ledger_sqlite_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease = conn.execute(
                    "SELECT lease_owner, fencing_token, active_attempt_id, expires_at "
                    "FROM validation_execution_leases WHERE resource_key=?",
                    (GLOBAL_LEASE_RESOURCE_KEY,),
                ).fetchone()
                if lease is None or lease["lease_owner"] != owner or lease["fencing_token"] != fencing_token:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_stale_token"}
                if lease["expires_at"] is None or lease["expires_at"] <= now_iso:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_expired_lease"}
                stale_attempt_id = lease["active_attempt_id"]
                if stale_attempt_id is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "no_stale_attempt"}

                attempt = conn.execute(
                    "SELECT id, slot_id, status FROM validation_schedule_attempts WHERE id=?",
                    (stale_attempt_id,),
                ).fetchone()
                if attempt is None or attempt["status"] not in ("claimed", "running"):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "attempt_not_recoverable"}

                conn.execute(
                    "UPDATE validation_schedule_attempts "
                    "SET status='abandoned', completed_at=?, failure_category=?, failure_summary=?, updated_at=? "
                    "WHERE id=?",
                    (now_iso, recovery_category, recovery_summary, now_iso, stale_attempt_id),
                )
                slot_id = attempt["slot_id"]
                if slot_id is not None:
                    conn.execute(
                        "UPDATE validation_schedule_slots "
                        "SET status='due', active_attempt_id=NULL, updated_at=? "
                        "WHERE id=? AND active_attempt_id=?",
                        (now_iso, slot_id, stale_attempt_id),
                    )
                conn.execute(
                    "UPDATE validation_execution_leases "
                    "SET active_attempt_id=NULL, updated_at=? "
                    "WHERE resource_key=? AND lease_owner=? AND fencing_token=?",
                    (now_iso, GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token),
                )
                conn.execute("COMMIT")
                return {"ok": True, "recovered_attempt_id": stale_attempt_id, "slot_id": slot_id}
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()


# ── Attempt creation — now requires and verifies global-lease admission
# BEFORE any attempt row exists, atomically with slot activation ──────────────

def create_schedule_attempt(slot_id: int, trigger_type: str, owner: str, fencing_token: int, now: datetime) -> dict:
    """Requires the caller to already hold the global lease. ONE
    transaction on ONE connection:
      1. lock the lease row, verify owner+fencing_token match the CURRENT
         lease and it is unexpired at `now`;
      2. verify the lease's active_attempt_id IS NULL — a second attempt
         (scheduled OR manual) can never be admitted while one is already
         bound, closing the gap where two different slots could each
         reach 'running' before either caller held the lease;
      3. lock the slot row, verify it is 'due' with no active attempt;
      4. allocate the next attempt_number (slot lock makes this race-free
         for this slot; the lease check above makes it globally
         impossible for a second slot to reach this point concurrently
         while admitted);
      5. insert the attempt as 'claimed', horizon/universe copied from
         the slot, lease_owner/lease_fencing_token recorded on it;
      6. move the slot to 'running', set its active_attempt_id;
      7. set the lease's active_attempt_id to the new attempt;
      8. commit — or roll back everything on any failure."""
    if trigger_type not in ("scheduler", "catchup"):
        raise ValueError(f"create_schedule_attempt: trigger_type must be 'scheduler' or 'catchup', got {trigger_type!r}")
    _require_utc(now, param="now")
    now_iso = _iso(now)

    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT lease_owner, fencing_token, expires_at, active_attempt_id "
                            "FROM validation_execution_leases WHERE resource_key=%s FOR UPDATE",
                            (GLOBAL_LEASE_RESOURCE_KEY,),
                        )
                        lease = _pg_dict_fetchone(cur)
                        if (lease is None or lease["lease_owner"] != owner or lease["fencing_token"] != fencing_token
                                or lease["expires_at"] is None or lease["expires_at"] <= now):
                            return {"ok": False, "reason": "not_owner_or_expired_lease"}
                        if lease["active_attempt_id"] is not None:
                            return {"ok": False, "reason": "active_attempt_already_bound"}

                        cur.execute(
                            "SELECT id, horizon, universe, status, active_attempt_id "
                            "FROM validation_schedule_slots WHERE id=%s FOR UPDATE",
                            (slot_id,),
                        )
                        slot = _pg_dict_fetchone(cur)
                        if slot is None:
                            return {"ok": False, "reason": "slot_not_found"}
                        if slot["status"] != "due" or slot["active_attempt_id"] is not None:
                            return {"ok": False, "reason": "slot_not_claimable"}

                        cur.execute(
                            "SELECT COALESCE(MAX(attempt_number), 0) + 1 "
                            "FROM validation_schedule_attempts WHERE slot_id=%s",
                            (slot_id,),
                        )
                        next_number = cur.fetchone()[0]

                        cur.execute(
                            "INSERT INTO validation_schedule_attempts "
                            "(slot_id, horizon, universe, attempt_number, trigger_type, status, "
                            "lease_owner, lease_fencing_token, created_at, updated_at) "
                            "VALUES (%s,%s,%s,%s,%s,'claimed',%s,%s,%s,%s) "
                            f"RETURNING {_ATTEMPT_COLUMNS}",
                            (slot_id, slot["horizon"], slot["universe"], next_number, trigger_type,
                             owner, fencing_token, now, now),
                        )
                        attempt = _pg_dict_fetchone(cur)

                        cur.execute(
                            "UPDATE validation_schedule_slots "
                            "SET status='running', active_attempt_id=%s, updated_at=%s WHERE id=%s",
                            (attempt["id"], now, slot_id),
                        )
                        cur.execute(
                            "UPDATE validation_execution_leases "
                            "SET active_attempt_id=%s, updated_at=%s WHERE resource_key=%s",
                            (attempt["id"], now, GLOBAL_LEASE_RESOURCE_KEY),
                        )
                        attempt["ok"] = True
                        return attempt
            finally:
                conn.close()
        else:
            conn = _get_ledger_sqlite_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease = conn.execute(
                    "SELECT lease_owner, fencing_token, expires_at, active_attempt_id "
                    "FROM validation_execution_leases WHERE resource_key=?",
                    (GLOBAL_LEASE_RESOURCE_KEY,),
                ).fetchone()
                if (lease is None or lease["lease_owner"] != owner or lease["fencing_token"] != fencing_token
                        or lease["expires_at"] is None or lease["expires_at"] <= now_iso):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_expired_lease"}
                if lease["active_attempt_id"] is not None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "active_attempt_already_bound"}

                slot = conn.execute(
                    "SELECT id, horizon, universe, status, active_attempt_id "
                    "FROM validation_schedule_slots WHERE id=?",
                    (slot_id,),
                ).fetchone()
                if slot is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "slot_not_found"}
                if slot["status"] != "due" or slot["active_attempt_id"] is not None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "slot_not_claimable"}

                next_number = conn.execute(
                    "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM validation_schedule_attempts WHERE slot_id=?",
                    (slot_id,),
                ).fetchone()[0]

                cur = conn.execute(
                    "INSERT INTO validation_schedule_attempts "
                    "(slot_id, horizon, universe, attempt_number, trigger_type, status, "
                    "lease_owner, lease_fencing_token, created_at, updated_at) "
                    "VALUES (?,?,?,?,?,'claimed',?,?,?,?)",
                    (slot_id, slot["horizon"], slot["universe"], next_number, trigger_type,
                     owner, fencing_token, now_iso, now_iso),
                )
                attempt_id = cur.lastrowid

                conn.execute(
                    "UPDATE validation_schedule_slots "
                    "SET status='running', active_attempt_id=?, updated_at=? WHERE id=?",
                    (attempt_id, now_iso, slot_id),
                )
                conn.execute(
                    "UPDATE validation_execution_leases SET active_attempt_id=?, updated_at=? WHERE resource_key=?",
                    (attempt_id, now_iso, GLOBAL_LEASE_RESOURCE_KEY),
                )
                row = conn.execute(
                    f"SELECT {_ATTEMPT_COLUMNS} FROM validation_schedule_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                conn.execute("COMMIT")
                result = _ledger_row_to_dict(row)
                result["ok"] = True
                return result
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()


def create_manual_attempt(horizon: str, universe: str, owner: str, fencing_token: int, now: datetime) -> dict:
    """Requires the caller to already hold the global lease — identical
    admission gate to create_schedule_attempt, but never touches any
    slot (slot_id is always NULL). No caller-supplied idempotency key is
    accepted: a future integration's key must be server-derived from the
    X-Secret-authenticated caller, never accepted verbatim from a
    client/browser."""
    _require_utc(now, param="now")
    _require_valid_horizon(horizon)
    _require_known_universe(universe)
    now_iso = _iso(now)
    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT lease_owner, fencing_token, expires_at, active_attempt_id "
                            "FROM validation_execution_leases WHERE resource_key=%s FOR UPDATE",
                            (GLOBAL_LEASE_RESOURCE_KEY,),
                        )
                        lease = _pg_dict_fetchone(cur)
                        if (lease is None or lease["lease_owner"] != owner or lease["fencing_token"] != fencing_token
                                or lease["expires_at"] is None or lease["expires_at"] <= now):
                            return {"ok": False, "reason": "not_owner_or_expired_lease"}
                        if lease["active_attempt_id"] is not None:
                            return {"ok": False, "reason": "active_attempt_already_bound"}

                        cur.execute(
                            "INSERT INTO validation_schedule_attempts "
                            "(slot_id, horizon, universe, attempt_number, trigger_type, status, "
                            "lease_owner, lease_fencing_token, created_at, updated_at) "
                            "VALUES (NULL,%s,%s,1,'manual','claimed',%s,%s,%s,%s) "
                            f"RETURNING {_ATTEMPT_COLUMNS}",
                            (horizon, universe, owner, fencing_token, now, now),
                        )
                        attempt = _pg_dict_fetchone(cur)
                        cur.execute(
                            "UPDATE validation_execution_leases "
                            "SET active_attempt_id=%s, updated_at=%s WHERE resource_key=%s",
                            (attempt["id"], now, GLOBAL_LEASE_RESOURCE_KEY),
                        )
                        attempt["ok"] = True
                        return attempt
            finally:
                conn.close()
        else:
            conn = _get_ledger_sqlite_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease = conn.execute(
                    "SELECT lease_owner, fencing_token, expires_at, active_attempt_id "
                    "FROM validation_execution_leases WHERE resource_key=?",
                    (GLOBAL_LEASE_RESOURCE_KEY,),
                ).fetchone()
                if (lease is None or lease["lease_owner"] != owner or lease["fencing_token"] != fencing_token
                        or lease["expires_at"] is None or lease["expires_at"] <= now_iso):
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_expired_lease"}
                if lease["active_attempt_id"] is not None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "active_attempt_already_bound"}

                cur = conn.execute(
                    "INSERT INTO validation_schedule_attempts "
                    "(slot_id, horizon, universe, attempt_number, trigger_type, status, "
                    "lease_owner, lease_fencing_token, created_at, updated_at) "
                    "VALUES (NULL,?,?,1,'manual','claimed',?,?,?,?)",
                    (horizon, universe, owner, fencing_token, now_iso, now_iso),
                )
                attempt_id = cur.lastrowid
                conn.execute(
                    "UPDATE validation_execution_leases SET active_attempt_id=?, updated_at=? WHERE resource_key=?",
                    (attempt_id, now_iso, GLOBAL_LEASE_RESOURCE_KEY),
                )
                row = conn.execute(
                    f"SELECT {_ATTEMPT_COLUMNS} FROM validation_schedule_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                conn.execute("COMMIT")
                result = _ledger_row_to_dict(row)
                result["ok"] = True
                return result
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()


# ── Compound attempt+slot(+lease) transitions — ONE transaction per operation ─

def _compound_transition(attempt_id: int, owner: str, fencing_token: int, now: datetime, *,
                          from_statuses: tuple[str, ...], to_status: str,
                          new_slot_status_if_bound: str | None, clear_active_attempt: bool,
                          clear_lease_binding: bool,
                          attempt_extra_set_pg: str = "", attempt_extra_set_sq: str = "",
                          attempt_extra_params_pg: tuple = (), attempt_extra_params_sq: tuple = ()) -> dict:
    """Shared core for every attempt(+slot)(+lease) mutation. ONE
    transaction on ONE connection, acquiring locks in the SAME global
    order every ledger operation uses (see the module-level "Lock
    ordering" note above `GLOBAL_LEASE_RESOURCE_KEY`):
    lease -> attempt -> slot -> result (as applicable). Locking the lease
    FIRST here — not the attempt, as an earlier revision did — is what
    closes the AB-BA deadlock the second independent review found between
    this function and recover_stale_active_attempt (which always locked
    lease-then-attempt); both now agree on the same order, so no two
    ledger transactions can ever hold locks in opposite sequence.
      1. lock the lease row CURRENTLY held by (owner, fencing_token),
         unexpired at `now`, and verify its active_attempt_id equals THIS
         attempt;
      2. lock and read the attempt row, verify its status is one of
         `from_statuses`;
      3. if bound to a slot, lock that slot and verify
         slot.active_attempt_id == attempt_id;
      4. update the attempt;
      5. if bound and `new_slot_status_if_bound` given, update the slot
         in the SAME transaction;
      6. if `clear_lease_binding`, clear the lease's active_attempt_id
         (guarded — only if it still equals this attempt_id) in the SAME
         transaction, freeing the lease for the next admission;
      7. commit — or roll back the WHOLE operation on any failure."""
    _require_utc(now, param="now")
    now_iso = _iso(now)
    placeholders_sq = ",".join(["?"] * len(from_statuses))
    placeholders_pg = ",".join(["%s"] * len(from_statuses))

    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT active_attempt_id FROM validation_execution_leases "
                            "WHERE resource_key=%s AND lease_owner=%s AND fencing_token=%s AND expires_at > %s "
                            "FOR UPDATE",
                            (GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token, now),
                        )
                        lease = _pg_dict_fetchone(cur)
                        if lease is None or lease["active_attempt_id"] != attempt_id:
                            return {"ok": False, "reason": "not_owner_or_expired_lease"}

                        cur.execute(
                            "SELECT id, slot_id, status FROM validation_schedule_attempts WHERE id=%s FOR UPDATE",
                            (attempt_id,),
                        )
                        attempt = _pg_dict_fetchone(cur)
                        if attempt is None:
                            return {"ok": False, "reason": "attempt_not_found"}
                        if attempt["status"] not in from_statuses:
                            return {"ok": False, "reason": "illegal_transition"}

                        slot_id = attempt["slot_id"]
                        if slot_id is not None:
                            cur.execute(
                                "SELECT id, active_attempt_id FROM validation_schedule_slots WHERE id=%s FOR UPDATE",
                                (slot_id,),
                            )
                            slot = _pg_dict_fetchone(cur)
                            if slot is None or slot["active_attempt_id"] != attempt_id:
                                return {"ok": False, "reason": "not_active_attempt_for_slot"}

                        cur.execute(
                            f"UPDATE validation_schedule_attempts "
                            f"SET status=%s, updated_at=%s{attempt_extra_set_pg} WHERE id=%s",
                            (to_status, now, *attempt_extra_params_pg, attempt_id),
                        )

                        if slot_id is not None and new_slot_status_if_bound is not None:
                            new_active = None if clear_active_attempt else attempt_id
                            cur.execute(
                                "UPDATE validation_schedule_slots "
                                "SET status=%s, active_attempt_id=%s, updated_at=%s WHERE id=%s",
                                (new_slot_status_if_bound, new_active, now, slot_id),
                            )
                        elif slot_id is not None and clear_active_attempt:
                            cur.execute(
                                "UPDATE validation_schedule_slots SET active_attempt_id=NULL, updated_at=%s WHERE id=%s",
                                (now, slot_id),
                            )

                        if clear_lease_binding:
                            cur.execute(
                                "UPDATE validation_execution_leases "
                                "SET active_attempt_id=NULL, updated_at=%s "
                                "WHERE resource_key=%s AND active_attempt_id=%s",
                                (now, GLOBAL_LEASE_RESOURCE_KEY, attempt_id),
                            )

                        return {"ok": True, "id": attempt_id, "slot_id": slot_id}
            finally:
                conn.close()
        else:
            conn = _get_ledger_sqlite_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                # Same lease -> attempt -> slot order as the PostgreSQL branch
                # above, for consistency — SQLite's BEGIN IMMEDIATE already
                # takes a whole-database write lock, so statement order here
                # cannot itself deadlock, but keeping one documented order
                # everywhere avoids this file ever becoming a second source
                # of truth that silently drifts from the PostgreSQL path.
                lease = conn.execute(
                    "SELECT active_attempt_id FROM validation_execution_leases "
                    "WHERE resource_key=? AND lease_owner=? AND fencing_token=? AND expires_at > ?",
                    (GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token, now_iso),
                ).fetchone()
                if lease is None or lease["active_attempt_id"] != attempt_id:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_expired_lease"}

                attempt = conn.execute(
                    "SELECT id, slot_id, status FROM validation_schedule_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                if attempt is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "attempt_not_found"}
                if attempt["status"] not in from_statuses:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "illegal_transition"}

                slot_id = attempt["slot_id"]
                if slot_id is not None:
                    slot = conn.execute(
                        "SELECT id, active_attempt_id FROM validation_schedule_slots WHERE id=?", (slot_id,)
                    ).fetchone()
                    if slot is None or slot["active_attempt_id"] != attempt_id:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "reason": "not_active_attempt_for_slot"}

                conn.execute(
                    f"UPDATE validation_schedule_attempts SET status=?, updated_at=?{attempt_extra_set_sq} WHERE id=?",
                    (to_status, now_iso, *attempt_extra_params_sq, attempt_id),
                )

                if slot_id is not None and new_slot_status_if_bound is not None:
                    new_active = None if clear_active_attempt else attempt_id
                    conn.execute(
                        "UPDATE validation_schedule_slots SET status=?, active_attempt_id=?, updated_at=? WHERE id=?",
                        (new_slot_status_if_bound, new_active, now_iso, slot_id),
                    )
                elif slot_id is not None and clear_active_attempt:
                    conn.execute(
                        "UPDATE validation_schedule_slots SET active_attempt_id=NULL, updated_at=? WHERE id=?",
                        (now_iso, slot_id),
                    )

                if clear_lease_binding:
                    conn.execute(
                        "UPDATE validation_execution_leases SET active_attempt_id=NULL, updated_at=? "
                        "WHERE resource_key=? AND active_attempt_id=?",
                        (now_iso, GLOBAL_LEASE_RESOURCE_KEY, attempt_id),
                    )

                conn.execute("COMMIT")
                return {"ok": True, "id": attempt_id, "slot_id": slot_id}
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()


def mark_attempt_running(attempt_id: int, owner: str, fencing_token: int, now: datetime) -> dict:
    """claimed -> running. Lease stays bound to this attempt (not
    cleared) — it is still the one globally admitted attempt."""
    return _compound_transition(
        attempt_id, owner, fencing_token, now,
        from_statuses=("claimed",), to_status="running",
        new_slot_status_if_bound=None, clear_active_attempt=False, clear_lease_binding=False,
        attempt_extra_set_pg=", started_at=COALESCE(started_at,%s), heartbeat_at=%s",
        attempt_extra_set_sq=", started_at=COALESCE(started_at,?), heartbeat_at=?",
        attempt_extra_params_pg=(now, now), attempt_extra_params_sq=(_iso(now), _iso(now)),
    )


def mark_attempt_failed_retryable(attempt_id: int, owner: str, fencing_token: int, now: datetime, *,
                                   failure_category: str | None = None, failure_summary: str | None = None) -> dict:
    """claimed/running -> failed; bound slot returns to 'due'; lease
    binding is cleared, freeing global admission for the next attempt."""
    return _compound_transition(
        attempt_id, owner, fencing_token, now,
        from_statuses=("claimed", "running"), to_status="failed",
        new_slot_status_if_bound="due", clear_active_attempt=True, clear_lease_binding=True,
        attempt_extra_set_pg=", completed_at=%s, failure_category=%s, failure_summary=%s",
        attempt_extra_set_sq=", completed_at=?, failure_category=?, failure_summary=?",
        attempt_extra_params_pg=(now, failure_category, failure_summary),
        attempt_extra_params_sq=(_iso(now), failure_category, failure_summary),
    )


def mark_attempt_failed_terminal(attempt_id: int, owner: str, fencing_token: int, now: datetime, *,
                                  failure_category: str | None = None, failure_summary: str | None = None) -> dict:
    """claimed/running -> failed; bound slot moves to 'failed' (terminal,
    non-retryable); lease binding cleared."""
    return _compound_transition(
        attempt_id, owner, fencing_token, now,
        from_statuses=("claimed", "running"), to_status="failed",
        new_slot_status_if_bound="failed", clear_active_attempt=True, clear_lease_binding=True,
        attempt_extra_set_pg=", completed_at=%s, failure_category=%s, failure_summary=%s",
        attempt_extra_set_sq=", completed_at=?, failure_category=?, failure_summary=?",
        attempt_extra_params_pg=(now, failure_category, failure_summary),
        attempt_extra_params_sq=(_iso(now), failure_category, failure_summary),
    )


def mark_attempt_abandoned_retry(attempt_id: int, owner: str, fencing_token: int, now: datetime, *,
                                  failure_category: str | None = None, failure_summary: str | None = None) -> dict:
    """claimed/running -> abandoned; bound slot returns to 'due'; lease
    binding cleared."""
    return _compound_transition(
        attempt_id, owner, fencing_token, now,
        from_statuses=("claimed", "running"), to_status="abandoned",
        new_slot_status_if_bound="due", clear_active_attempt=True, clear_lease_binding=True,
        attempt_extra_set_pg=", completed_at=%s, failure_category=%s, failure_summary=%s",
        attempt_extra_set_sq=", completed_at=?, failure_category=?, failure_summary=?",
        attempt_extra_params_pg=(now, failure_category, failure_summary),
        attempt_extra_params_sq=(_iso(now), failure_category, failure_summary),
    )


def mark_attempt_abandoned_terminal(attempt_id: int, owner: str, fencing_token: int, now: datetime, *,
                                     failure_category: str | None = None, failure_summary: str | None = None) -> dict:
    """claimed/running -> abandoned; bound slot moves to 'abandoned'
    (terminal); lease binding cleared."""
    return _compound_transition(
        attempt_id, owner, fencing_token, now,
        from_statuses=("claimed", "running"), to_status="abandoned",
        new_slot_status_if_bound="abandoned", clear_active_attempt=True, clear_lease_binding=True,
        attempt_extra_set_pg=", completed_at=%s, failure_category=%s, failure_summary=%s",
        attempt_extra_set_sq=", completed_at=?, failure_category=?, failure_summary=?",
        attempt_extra_params_pg=(now, failure_category, failure_summary),
        attempt_extra_params_sq=(_iso(now), failure_category, failure_summary),
    )


def complete_attempt_with_result(attempt_id: int, owner: str, fencing_token: int,
                                  result_run_id: int, now: datetime) -> dict:
    """running -> completed, attaching result_run_id; bound slot moves to
    'completed'; lease binding cleared. Deliberately NOT built on the
    shared _compound_transition core — completion has one more resource
    to lock (val_runs) and one more invariant to enforce (one-to-one
    result linkage) than any other transition, so it gets its own
    self-contained transaction following the SAME documented lock order:
    lease -> attempt -> slot -> result.
      1. lock+verify the lease (current owner/token, unexpired,
         active_attempt_id == this attempt);
      2. lock+verify the attempt (status == 'running');
      3. if bound to a slot, lock+verify it (active_attempt_id ==
         this attempt);
      4. lock the val_runs row and verify it exists and its
         horizon/universe match this attempt's own — applies identically
         to manual and scheduled attempts;
      5. verify no OTHER attempt already has this result_run_id — safe to
         check with a plain read here (not a second FOR UPDATE) because
         step 4's lock on the val_runs row already serializes every other
         completion attempting to claim that same result: a concurrent
         completion for the same result_run_id would itself be blocked at
         step 4 until this transaction commits or rolls back;
      6. perform the atomic completion (attempt+slot+lease) in the same
         transaction;
      7. commit.
    The named UNIQUE INDEX (idx_vsa_result_unique) remains as
    database-level defense in depth — if it fires anyway (e.g. a future
    code path bypasses this function), the violation is caught, the
    transaction is confirmed rolled back, and it maps ONLY to
    'result_already_linked'; any other integrity error propagates
    unchanged as a distinct internal failure, never mislabeled."""
    _require_utc(now, param="now")
    now_iso = _iso(now)

    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                import psycopg.errors

                # `with conn.transaction():` only ROLLBACKs when an exception
                # escapes the block — catching the UniqueViolation and simply
                # `return`ing would exit the block normally after the
                # transaction was already poisoned by the failed UPDATE at
                # the PostgreSQL protocol level, relying on ambiguous
                # commit-on-aborted-transaction semantics. Instead, this
                # sentinel is raised to force the block to exit via its
                # exception path (a guaranteed, explicit ROLLBACK) and is
                # caught OUTSIDE the transaction block, after rollback has
                # already completed.
                class _ResultAlreadyLinked(Exception):
                    pass

                try:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT active_attempt_id FROM validation_execution_leases "
                                "WHERE resource_key=%s AND lease_owner=%s AND fencing_token=%s AND expires_at > %s "
                                "FOR UPDATE",
                                (GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token, now),
                            )
                            lease = _pg_dict_fetchone(cur)
                            if lease is None or lease["active_attempt_id"] != attempt_id:
                                return {"ok": False, "reason": "not_owner_or_expired_lease"}

                            cur.execute(
                                "SELECT id, slot_id, horizon, universe, status "
                                "FROM validation_schedule_attempts WHERE id=%s FOR UPDATE",
                                (attempt_id,),
                            )
                            attempt = _pg_dict_fetchone(cur)
                            if attempt is None:
                                return {"ok": False, "reason": "attempt_not_found"}
                            if attempt["status"] != "running":
                                return {"ok": False, "reason": "illegal_transition"}

                            slot_id = attempt["slot_id"]
                            if slot_id is not None:
                                cur.execute(
                                    "SELECT id, active_attempt_id FROM validation_schedule_slots WHERE id=%s FOR UPDATE",
                                    (slot_id,),
                                )
                                slot = _pg_dict_fetchone(cur)
                                if slot is None or slot["active_attempt_id"] != attempt_id:
                                    return {"ok": False, "reason": "not_active_attempt_for_slot"}

                            cur.execute(
                                "SELECT horizon, universe FROM val_runs WHERE id=%s FOR UPDATE",
                                (result_run_id,),
                            )
                            run_row = _pg_dict_fetchone(cur)
                            if run_row is None:
                                return {"ok": False, "reason": "result_run_id_not_found"}
                            if run_row["horizon"] != attempt["horizon"] or run_row["universe"] != attempt["universe"]:
                                return {"ok": False, "reason": "result_identity_mismatch"}

                            cur.execute(
                                "SELECT id FROM validation_schedule_attempts WHERE result_run_id=%s",
                                (result_run_id,),
                            )
                            existing = cur.fetchone()
                            if existing is not None and existing[0] != attempt_id:
                                return {"ok": False, "reason": "result_already_linked"}

                            try:
                                cur.execute(
                                    "UPDATE validation_schedule_attempts "
                                    "SET status='completed', completed_at=%s, result_run_id=%s, updated_at=%s "
                                    "WHERE id=%s",
                                    (now, result_run_id, now, attempt_id),
                                )
                            except psycopg.errors.UniqueViolation as e:
                                if "idx_vsa_result_unique" in str(e):
                                    raise _ResultAlreadyLinked() from e
                                raise

                            if slot_id is not None:
                                cur.execute(
                                    "UPDATE validation_schedule_slots "
                                    "SET status='completed', active_attempt_id=NULL, updated_at=%s WHERE id=%s",
                                    (now, slot_id),
                                )
                            cur.execute(
                                "UPDATE validation_execution_leases "
                                "SET active_attempt_id=NULL, updated_at=%s "
                                "WHERE resource_key=%s AND active_attempt_id=%s",
                                (now, GLOBAL_LEASE_RESOURCE_KEY, attempt_id),
                            )
                            return {"ok": True, "id": attempt_id, "slot_id": slot_id}
                except _ResultAlreadyLinked:
                    return {"ok": False, "reason": "result_already_linked"}
            finally:
                conn.close()
        else:
            conn = _get_ledger_sqlite_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease = conn.execute(
                    "SELECT active_attempt_id FROM validation_execution_leases "
                    "WHERE resource_key=? AND lease_owner=? AND fencing_token=? AND expires_at > ?",
                    (GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token, now_iso),
                ).fetchone()
                if lease is None or lease["active_attempt_id"] != attempt_id:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_expired_lease"}

                attempt = conn.execute(
                    "SELECT id, slot_id, horizon, universe, status "
                    "FROM validation_schedule_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                if attempt is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "attempt_not_found"}
                if attempt["status"] != "running":
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "illegal_transition"}

                slot_id = attempt["slot_id"]
                if slot_id is not None:
                    slot = conn.execute(
                        "SELECT id, active_attempt_id FROM validation_schedule_slots WHERE id=?", (slot_id,)
                    ).fetchone()
                    if slot is None or slot["active_attempt_id"] != attempt_id:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "reason": "not_active_attempt_for_slot"}

                run_row = conn.execute(
                    "SELECT horizon, universe FROM val_runs WHERE id=?", (result_run_id,)
                ).fetchone()
                if run_row is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "result_run_id_not_found"}
                if run_row["horizon"] != attempt["horizon"] or run_row["universe"] != attempt["universe"]:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "result_identity_mismatch"}

                existing = conn.execute(
                    "SELECT id FROM validation_schedule_attempts WHERE result_run_id=?", (result_run_id,)
                ).fetchone()
                if existing is not None and existing["id"] != attempt_id:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "result_already_linked"}

                try:
                    conn.execute(
                        "UPDATE validation_schedule_attempts "
                        "SET status='completed', completed_at=?, result_run_id=?, updated_at=? WHERE id=?",
                        (now_iso, result_run_id, now_iso, attempt_id),
                    )
                except sqlite3.IntegrityError as e:
                    if "idx_vsa_result_unique" in str(e) or "UNIQUE constraint failed: validation_schedule_attempts.result_run_id" in str(e):
                        conn.execute("ROLLBACK")
                        return {"ok": False, "reason": "result_already_linked"}
                    raise  # unexpected integrity error — let the outer handler roll back once and propagate it distinctly

                if slot_id is not None:
                    conn.execute(
                        "UPDATE validation_schedule_slots "
                        "SET status='completed', active_attempt_id=NULL, updated_at=? WHERE id=?",
                        (now_iso, slot_id),
                    )
                conn.execute(
                    "UPDATE validation_execution_leases "
                    "SET active_attempt_id=NULL, updated_at=? WHERE resource_key=? AND active_attempt_id=?",
                    (now_iso, GLOBAL_LEASE_RESOURCE_KEY, attempt_id),
                )
                conn.execute("COMMIT")
                return {"ok": True, "id": attempt_id, "slot_id": slot_id}
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()


class _FencedOutDuringComputation(Exception):
    """Raised internally by run_validation() when its optional _fence_check
    callback reports the caller's lease/fencing token has been superseded
    mid-run. Stops the stock-backtest loop as soon as safely possible and
    guarantees no val_runs/val_signals persistence is ever attempted for
    this computation — persistence only happens via the atomic fenced
    primitive below, which independently re-verifies fencing anyway, but
    a computation known to be stale should not even reach that call."""


def complete_running_attempt_with_computed_result(
    attempt_id: int, owner: str, fencing_token: int, *,
    horizon: str, universe: str, run_at: str, n_stocks: int, n_signals: int,
    summary_json: str, signal_rows: list, now: datetime,
) -> dict:
    """V-SCHED1C1 correction — the ONE atomic fenced primitive that turns a
    computed-but-not-yet-persisted validation result into a durable,
    linked, completed attempt. Unlike the legacy run_validation() persist
    path (kept only for direct/legacy callers — see _persist=False below),
    this function performs the val_runs/val_signals INSERT itself, inside
    the SAME transaction as the fencing check and the attempt/slot/lease
    transition, following the documented lock order lease -> attempt ->
    slot -> result:
      1-5. lock+verify the lease (owner, token, unexpired, active_attempt_id
           == this attempt);
      6-8. lock+verify the attempt (status == 'running') and, if bound,
           its slot (active_attempt_id == this attempt);
      9-10. insert the val_runs row and its val_signals rows;
      11-15. link result_run_id, complete the attempt, complete the slot,
             clear slot/lease active-attempt bindings;
      16. commit once.
    Any failure at any step rolls back the WHOLE transaction — the
    val_runs/val_signals rows never survive a fencing/ownership failure,
    so a stale/fenced-out worker can never create an orphan result row or
    become the publicly-selected latest result."""
    _require_utc(now, param="now")
    now_iso = _iso(now)

    with _db_lock:
        if _USE_POSTGRES:
            conn = _pg_conn()
            try:
                with conn.transaction():
                    with conn.cursor() as cur:
                        cur.execute(
                            "SELECT active_attempt_id FROM validation_execution_leases "
                            "WHERE resource_key=%s AND lease_owner=%s AND fencing_token=%s AND expires_at > %s "
                            "FOR UPDATE",
                            (GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token, now),
                        )
                        lease = _pg_dict_fetchone(cur)
                        if lease is None or lease["active_attempt_id"] != attempt_id:
                            return {"ok": False, "reason": "not_owner_or_expired_lease"}

                        cur.execute(
                            "SELECT id, slot_id, horizon, universe, status "
                            "FROM validation_schedule_attempts WHERE id=%s FOR UPDATE",
                            (attempt_id,),
                        )
                        attempt = _pg_dict_fetchone(cur)
                        if attempt is None:
                            return {"ok": False, "reason": "attempt_not_found"}
                        if attempt["status"] != "running":
                            return {"ok": False, "reason": "illegal_transition"}
                        if attempt["horizon"] != horizon or attempt["universe"] != universe:
                            return {"ok": False, "reason": "result_identity_mismatch"}

                        slot_id = attempt["slot_id"]
                        if slot_id is not None:
                            cur.execute(
                                "SELECT id, active_attempt_id FROM validation_schedule_slots WHERE id=%s FOR UPDATE",
                                (slot_id,),
                            )
                            slot = _pg_dict_fetchone(cur)
                            if slot is None or slot["active_attempt_id"] != attempt_id:
                                return {"ok": False, "reason": "not_active_attempt_for_slot"}

                        cur.execute(
                            "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) "
                            "VALUES (%s,%s,%s,%s,%s,%s) RETURNING id",
                            (run_at, horizon, n_stocks, n_signals, summary_json, universe),
                        )
                        result_run_id = cur.fetchone()[0]
                        if signal_rows:
                            cur.executemany(
                                """INSERT INTO val_signals
                                   (run_id, symbol, horizon, signal_date, composite_score,
                                    tech_score, rs_score, obv_score, mfi_score,
                                    predicted, fwd_return_pct, nifty_fwd_ret_pct, alpha_pct,
                                    actual_direction, correct)
                                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                                [(result_run_id,) + r for r in signal_rows]
                            )

                        cur.execute(
                            "UPDATE validation_schedule_attempts "
                            "SET status='completed', completed_at=%s, result_run_id=%s, updated_at=%s "
                            "WHERE id=%s",
                            (now, result_run_id, now, attempt_id),
                        )
                        if slot_id is not None:
                            cur.execute(
                                "UPDATE validation_schedule_slots "
                                "SET status='completed', active_attempt_id=NULL, updated_at=%s WHERE id=%s",
                                (now, slot_id),
                            )
                        cur.execute(
                            "UPDATE validation_execution_leases "
                            "SET active_attempt_id=NULL, updated_at=%s "
                            "WHERE resource_key=%s AND active_attempt_id=%s",
                            (now, GLOBAL_LEASE_RESOURCE_KEY, attempt_id),
                        )
                        return {"ok": True, "id": attempt_id, "slot_id": slot_id, "run_id": result_run_id}
            finally:
                conn.close()
        else:
            conn = _get_ledger_sqlite_conn()
            try:
                conn.execute("BEGIN IMMEDIATE")
                lease = conn.execute(
                    "SELECT active_attempt_id FROM validation_execution_leases "
                    "WHERE resource_key=? AND lease_owner=? AND fencing_token=? AND expires_at > ?",
                    (GLOBAL_LEASE_RESOURCE_KEY, owner, fencing_token, now_iso),
                ).fetchone()
                if lease is None or lease["active_attempt_id"] != attempt_id:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "not_owner_or_expired_lease"}

                attempt = conn.execute(
                    "SELECT id, slot_id, horizon, universe, status "
                    "FROM validation_schedule_attempts WHERE id=?", (attempt_id,)
                ).fetchone()
                if attempt is None:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "attempt_not_found"}
                if attempt["status"] != "running":
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "illegal_transition"}
                if attempt["horizon"] != horizon or attempt["universe"] != universe:
                    conn.execute("ROLLBACK")
                    return {"ok": False, "reason": "result_identity_mismatch"}

                slot_id = attempt["slot_id"]
                if slot_id is not None:
                    slot = conn.execute(
                        "SELECT id, active_attempt_id FROM validation_schedule_slots WHERE id=?", (slot_id,)
                    ).fetchone()
                    if slot is None or slot["active_attempt_id"] != attempt_id:
                        conn.execute("ROLLBACK")
                        return {"ok": False, "reason": "not_active_attempt_for_slot"}

                cur = conn.execute(
                    "INSERT INTO val_runs (run_at, horizon, n_stocks, n_signals, summary, universe) VALUES (?,?,?,?,?,?)",
                    (run_at, horizon, n_stocks, n_signals, summary_json, universe),
                )
                result_run_id = cur.lastrowid
                if signal_rows:
                    conn.executemany(
                        """INSERT INTO val_signals
                           (run_id, symbol, horizon, signal_date, composite_score,
                            tech_score, rs_score, obv_score, mfi_score,
                            predicted, fwd_return_pct, nifty_fwd_ret_pct, alpha_pct,
                            actual_direction, correct)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        [(result_run_id,) + r for r in signal_rows]
                    )

                conn.execute(
                    "UPDATE validation_schedule_attempts "
                    "SET status='completed', completed_at=?, result_run_id=?, updated_at=? WHERE id=?",
                    (now_iso, result_run_id, now_iso, attempt_id),
                )
                if slot_id is not None:
                    conn.execute(
                        "UPDATE validation_schedule_slots "
                        "SET status='completed', active_attempt_id=NULL, updated_at=? WHERE id=?",
                        (now_iso, slot_id),
                    )
                conn.execute(
                    "UPDATE validation_execution_leases "
                    "SET active_attempt_id=NULL, updated_at=? WHERE resource_key=? AND active_attempt_id=?",
                    (now_iso, GLOBAL_LEASE_RESOURCE_KEY, attempt_id),
                )
                conn.execute("COMMIT")
                return {"ok": True, "id": attempt_id, "slot_id": slot_id, "run_id": result_run_id}
            except Exception:
                conn.execute("ROLLBACK")
                raise
            finally:
                conn.close()


# ── V-SCHED1C1 — shared execution-admission orchestration ─────────────────────
# The single internal path used by the scheduler, catch-up, and the
# authenticated manual trigger to run validation through the V-SCHED1B
# durable ledger. No caller maintains separate admission logic — main.py's
# scheduler/catch-up and api/routers/validation.py's /run route both call
# these functions rather than run_validation() directly.
#
# Split into two phases so the manual HTTP route can synchronously attempt
# admission (fast: lease + attempt creation + running transition) before
# ever reporting acceptance to the caller, then hand off only the
# potentially long-running actual validation execution to a background
# task — never the reverse.

def admit_validation_attempt(horizon: str, universe: str, trigger_type: str, owner: str, *,
                              slot_id: int | None = None, scheduled_slot: datetime | None = None,
                              schedule_version: str = "v1", now: datetime | None = None,
                              lease_duration_seconds: int = 600) -> dict:
    """Phase 1 — fast and synchronous. Never executes the actual validation
    run. In order:
      1. acquire the global execution lease for `owner`;
      2. if the lease was reclaimed from a stale prior holder
         (recovery_required), recover it via the fenced V-SCHED1B primitive
         before proceeding — fail closed (release and reject) if recovery
         itself fails;
      3. for trigger_type in (scheduler, catchup): resolve/create the
         canonical scheduled slot (unless slot_id already given) and create
         a scheduled attempt bound to it; for trigger_type manual: create an
         unbound (slot_id=NULL) manual attempt;
      4. mark the attempt running.
    Returns {"ok": True, "attempt_id", "fencing_token", "owner"} or
    {"ok": False, "reason": <code>} — a caller must never report acceptance
    on a False result."""
    if trigger_type not in ("scheduler", "catchup", "manual"):
        raise ValueError(f"admit_validation_attempt: trigger_type must be scheduler/catchup/manual, got {trigger_type!r}")
    if now is None:
        now = datetime.now(timezone.utc)
    now = _require_utc(now, param="now")

    lease = acquire_validation_execution_lease(owner=owner, now=now, lease_duration_seconds=lease_duration_seconds)
    if not lease.get("ok"):
        return {"ok": False, "reason": lease.get("reason", "already_leased")}

    fencing_token = lease["fencing_token"]

    if lease.get("recovery_required"):
        recovery = recover_stale_active_attempt(owner=owner, fencing_token=fencing_token, now=now)
        if not recovery.get("ok"):
            # Fail closed — do not admit a new attempt while a stale
            # binding could not be cleanly resolved. Release what we hold
            # (nothing is bound to this owner yet) so a future caller can retry.
            release_validation_execution_lease(owner=owner, fencing_token=fencing_token, now=now)
            return {"ok": False, "reason": f"recovery_failed:{recovery.get('reason', 'unknown')}"}

    if trigger_type in ("scheduler", "catchup"):
        if slot_id is None:
            if scheduled_slot is None:
                release_validation_execution_lease(owner=owner, fencing_token=fencing_token, now=now)
                return {"ok": False, "reason": "missing_scheduled_slot"}
            slot = get_or_create_schedule_slot(
                horizon=horizon, universe=universe, scheduled_slot=scheduled_slot,
                schedule_version=schedule_version, now=now,
            )
            slot_id = slot["id"]
        attempt = create_schedule_attempt(
            slot_id=slot_id, trigger_type=trigger_type, owner=owner,
            fencing_token=fencing_token, now=now,
        )
    else:
        attempt = create_manual_attempt(horizon=horizon, universe=universe, owner=owner,
                                         fencing_token=fencing_token, now=now)

    if not attempt.get("ok"):
        release_validation_execution_lease(owner=owner, fencing_token=fencing_token, now=now)
        return {"ok": False, "reason": attempt.get("reason", "attempt_creation_failed")}

    attempt_id = attempt["id"]
    running = mark_attempt_running(attempt_id, owner=owner, fencing_token=fencing_token, now=now)
    if not running.get("ok"):
        # Should be unreachable (we just created this attempt under this
        # exact lease), but fail closed rather than silently proceeding —
        # abandon the attempt so it can't be left stuck claimed.
        mark_attempt_abandoned_terminal(attempt_id, owner=owner, fencing_token=fencing_token, now=now,
                                         failure_category="ADMISSION_RACE",
                                         failure_summary="mark_attempt_running failed immediately after creation")
        return {"ok": False, "reason": "mark_running_failed"}

    return {"ok": True, "attempt_id": attempt_id, "fencing_token": fencing_token, "owner": owner}


def execute_and_complete_admitted_attempt(attempt_id: int, owner: str, fencing_token: int,
                                           horizon: str, universe: str, trigger_type: str, *,
                                           lease_duration_seconds: int = 600,
                                           heartbeat_every_n_stocks: int = 10,
                                           max_workers: int = 6) -> dict:
    """Phase 2 — runs the actual validation for an attempt already admitted
    by admit_validation_attempt(), then atomically completes/fails it.

    V-SCHED1C1-C1 correction: run_validation() is called with _persist=False
    — it computes but never writes val_runs/val_signals. Heartbeat renewal
    is tied to observable forward progress (every `heartbeat_every_n_stocks`
    completed stocks, via run_validation's progress_callback/_fence_check
    checkpoint) — never an unconditionally-renewing blind timer. The moment
    a heartbeat is rejected (fencing_token superseded), the NEXT checkpoint
    inside run_validation() cancels remaining work and raises
    _FencedOutDuringComputation — execution does not run to completion under
    a known-stale token. Final persistence — the val_runs/val_signals insert
    itself — happens only inside complete_running_attempt_with_computed_
    result(), one atomic transaction that independently re-verifies owner/
    token/expiry/active_attempt_id before writing anything. So even a
    fencing loss that occurs after the last heartbeat (a window this
    cooperative checkpoint cannot close) is still caught: the atomic
    primitive's own fencing check runs immediately before its own insert,
    in the same transaction, and rolls back the insert if it fails. No
    val_runs/val_signals row can ever survive under a stale/superseded
    identity."""
    fenced_out = {"value": False}

    def _on_progress_fence_check():
        hb = heartbeat_validation_execution_lease(
            owner=owner, fencing_token=fencing_token, now=datetime.now(timezone.utc),
            lease_duration_seconds=lease_duration_seconds,
        )
        if not hb.get("ok"):
            fenced_out["value"] = True
            return True
        return False

    def _on_progress(done, total):
        if done % heartbeat_every_n_stocks != 0 and done != total:
            return
        _on_progress_fence_check()

    def _release_if_still_ours(now: datetime) -> None:
        # Best-effort: after a terminal attempt transition, the lease's
        # active_attempt_id is already cleared, so this owner's lease now
        # holds nothing — release it so the NEXT admission (by this same
        # owner reused across universes, or any other) is never blocked
        # behind a lease no attempt actually needs anymore. A failure here
        # (already reclaimed by someone else) is expected and harmless —
        # there is nothing left for this owner to release in that case.
        release_validation_execution_lease(owner=owner, fencing_token=fencing_token, now=now)

    def _fence_checkpoint():
        # Same predicate the heartbeat itself uses (bool(fenced_out) after
        # _on_progress runs) — run_validation() calls this right after
        # progress_callback at every cooperative checkpoint, so a fencing
        # loss detected on heartbeat N is acted on at checkpoint N, not
        # silently carried to the end of the run.
        return fenced_out["value"]

    try:
        metrics = run_validation(horizon=horizon, universe=universe, max_workers=max_workers,
                                  trigger_type=trigger_type, progress_callback=_on_progress,
                                  _persist=False, _fence_check=_fence_checkpoint)
    except _FencedOutDuringComputation:
        log.warning(
            "[validation] attempt %s lost its lease during execution (fenced out) — "
            "computation aborted before any persistence was attempted", attempt_id,
        )
        # Do NOT mark the attempt failed, release the lease, or clear any
        # binding here — this owner's token is already superseded; the new
        # owner holds the lease and must not have its state touched by the
        # stale worker in any way.
        return {"ok": False, "reason": "fenced_out_during_execution"}
    except Exception:
        log.exception("[validation] execute_and_complete_admitted_attempt: run_validation raised")
        fail_now = datetime.now(timezone.utc)
        mark_attempt_failed_retryable(attempt_id, owner=owner, fencing_token=fencing_token, now=fail_now,
                                       failure_category="RUN_EXCEPTION")
        _release_if_still_ours(fail_now)
        return {"ok": False, "reason": "run_exception"}

    complete_now = datetime.now(timezone.utc)

    if fenced_out["value"]:
        # Computation finished naturally between the last checkpoint and
        # the fenced flag being observed here (a narrow window — the
        # checkpoint runs after every heartbeat_every_n_stocks stocks, so
        # this covers the tail after the final checkpoint). No persistence
        # has happened yet (_persist=False), so nothing to roll back — just
        # decline to call the atomic primitive at all.
        log.warning(
            "[validation] attempt %s lost its lease during execution (fenced out, "
            "detected after final checkpoint) — no result will be persisted", attempt_id,
        )
        return {"ok": False, "reason": "fenced_out_during_execution"}

    payload = metrics.get("_persist_payload")
    if payload is None:
        # run_validation's own internal in-memory claim rejected it
        # (metrics.get("error") set) or some other path produced no
        # computed payload — nothing to persist; fail the attempt so its
        # slot (if any) returns to due for a future retry.
        mark_attempt_failed_retryable(attempt_id, owner=owner, fencing_token=fencing_token, now=complete_now,
                                       failure_category="NO_RESULT_RUN_ID",
                                       failure_summary=str(metrics.get("error"))[:200] if metrics.get("error") else None)
        _release_if_still_ours(complete_now)
        return {"ok": False, "reason": "no_result_run_id"}

    completion = complete_running_attempt_with_computed_result(
        attempt_id, owner=owner, fencing_token=fencing_token,
        horizon=payload["horizon"], universe=payload["universe"], run_at=payload["run_at"],
        n_stocks=payload["n_stocks"], n_signals=payload["n_signals"],
        summary_json=payload["summary_json"], signal_rows=payload["signal_rows"],
        now=complete_now,
    )
    if not completion.get("ok"):
        # The atomic primitive rolled back the ENTIRE transaction on any
        # ownership/fencing/attempt/slot failure — no val_runs/val_signals
        # row was left behind (unlike the pre-correction path, there is no
        # "orphaned_run_id" possible here: either everything committed
        # together, or nothing did).
        return {"ok": False, "reason": completion.get("reason", "completion_failed")}

    result_run_id = completion["run_id"]
    metrics["run_id"] = result_run_id
    _release_if_still_ours(complete_now)
    return {"ok": True, "attempt_id": attempt_id, "run_id": result_run_id, "metrics": metrics}


def execute_admitted_validation(horizon: str, universe: str, trigger_type: str, owner: str, *,
                                 slot_id: int | None = None, scheduled_slot: datetime | None = None,
                                 schedule_version: str = "v1", now: datetime | None = None,
                                 lease_duration_seconds: int = 600, heartbeat_every_n_stocks: int = 10,
                                 max_workers: int = 6) -> dict:
    """Convenience wrapper combining both phases for callers that don't
    need the synchronous-admission/background-execution split (the
    scheduler and catch-up — both already run entirely inside a background
    executor thread from the moment they're invoked)."""
    admitted = admit_validation_attempt(
        horizon=horizon, universe=universe, trigger_type=trigger_type, owner=owner,
        slot_id=slot_id, scheduled_slot=scheduled_slot, schedule_version=schedule_version,
        now=now, lease_duration_seconds=lease_duration_seconds,
    )
    if not admitted.get("ok"):
        return admitted
    return execute_and_complete_admitted_attempt(
        admitted["attempt_id"], admitted["owner"], admitted["fencing_token"],
        horizon, universe, trigger_type,
        lease_duration_seconds=lease_duration_seconds,
        heartbeat_every_n_stocks=heartbeat_every_n_stocks, max_workers=max_workers,
    )
