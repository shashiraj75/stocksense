"""
Price-path evidence acquisition — Trade Postmortem Sprint 3A, Stages 4/5/12.

Split deliberately into a pure bundle-construction function
(`build_price_path_evidence` — no I/O, exhaustively unit-testable with
hand-built raw bar fixtures) and a thin provider-fetch wrapper
(`fetch_raw_daily_bars` — the only function in this module that touches
yfinance). Callers (the generation pipeline) inject `fetch_raw_daily_bars`
as a parameter into `acquire_price_path_evidence` rather than this module
importing yfinance at call time in the pure path, so tests never need a
real network call.

Stage 12's own explicit requirement — "do not fetch market data inside
the trade-close transaction" — is enforced by construction: nothing in
this module accepts or opens a database connection. The caller (generation
service) is responsible for calling this module OUTSIDE any
`with conn.transaction():` block, then persisting the returned bundle in
its own, separate, short transaction.
"""

import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone

from services.postmortem.price_path_evidence import (
    BAR_INTERVAL_DAILY,
    ENTRY_BAR_PARTIAL_UNKNOWN,
    EVIDENCE_BUNDLE_SCHEMA_VERSION,
    EXIT_BAR_PARTIAL_UNKNOWN,
    PricePathBar,
    PricePathEvidenceBundle,
    STATUS_AMBIGUOUS_RESOLUTION,
    STATUS_COMPLETE,
    STATUS_PARTIAL,
    STATUS_UNAVAILABLE,
    UNADJUSTED,
    UNKNOWN_ADJUSTMENT,
)

SOURCE_ID_YFINANCE_DAILY = "yfinance_daily"

# Stage D0 terminology correction: yfinance is an unofficial, unlicensed
# third-party dependency this codebase has never run a production-
# authority or licensing review on. Calling it APPROVED_EXTERNAL_SOURCE
# would falsely imply that review happened. EXTERNAL_UNOFFICIAL_DAILY
# states plainly what it is; SOURCE_SCOPE states plainly what it is
# permitted to be used for here — bounded, replayable evidence
# acquisition only, never as a production-authoritative price feed for
# any other part of this codebase (P&L, execution, portfolio valuation).
SOURCE_TYPE = "EXTERNAL_UNOFFICIAL_DAILY"
SOURCE_SCOPE = "BOUNDED_EVIDENCE_ACQUISITION_ONLY"
# Bumped whenever this module's acquisition/window-construction RULES
# change (not the underlying provider's own data) — independent of
# EVIDENCE_BUNDLE_SCHEMA_VERSION, which tracks the persisted shape.
SOURCE_VERSION = "1.0.0"

# Stage J-F3 — versions for manifest sub-concerns that evolve
# independently of SOURCE_VERSION (which covers the acquisition
# *ruleset* as a whole). Each is a distinct thing that could change on
# its own: the manifest's own field set, the symbol-normalization rule
# (_provider_symbol below), and the entry/exit boundary-inclusion rules
# (_resolve_boundary_policies / services.postmortem.session_boundary).
SOURCE_MANIFEST_SCHEMA_VERSION = "1.0.0"
SYMBOL_NORMALIZATION_VERSION = "1.0.0"
BOUNDARY_POLICY_VERSION = "1.0.0"

_MARKET_SUFFIX = {"US": "", "IN": ".NS"}

# Stage D bounding: a single acquisition call must never request an
# unbounded window or return an unbounded number of bars. These are
# evidence-acquisition bounds only — they do not limit how long a paper
# trade may be held; a longer hold is simply evidence-unavailable beyond
# this window rather than silently fetched in full.
MAX_ACQUISITION_WINDOW_DAYS = 1500  # ~4 calendar years of daily sessions
MAX_ACQUISITION_BARS = 1100         # generous upper bound for ~4 years of trading days
FETCH_TIMEOUT_SECONDS = 10


class AcquisitionWindowTooLargeError(ValueError):
    """Raised BEFORE any provider call is attempted — an oversized window
    is rejected at the boundary, never silently truncated or fetched in
    full and truncated after the fact."""


class PriceProviderAcquisitionError(RuntimeError):
    """Sanitized failure envelope (Stage D item 7) — the underlying
    provider exception's raw message/stack (which may embed HTTP
    internals, provider-specific error text, or transient infrastructure
    detail) is never propagated verbatim to callers. `code` is a stable,
    small vocabulary the generation pipeline can branch on (e.g. to mark
    an outbox row retryable) without string-matching provider text."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _provider_symbol(symbol: str, market: str) -> str:
    return f"{symbol.upper()}{_MARKET_SUFFIX.get(market, '')}"


def _resolve_boundary_policies(
    market: str, entry_timestamp: datetime, exit_timestamp: datetime, limitations: list[str],
) -> tuple[str, str]:
    """Pre-Stage-H Correction 1 — resolves real entry/exit boundary
    policy from services.postmortem.session_boundary's session-aware
    classification instead of the previous hardcoded PARTIAL_UNKNOWN for
    every trade. Appends any limitations produced (including the
    standing EARLY_CLOSE_UNSUPPORTED disclosure) to the caller's shared
    limitations list. Falls back to PARTIAL_UNKNOWN for any market
    session_boundary doesn't recognize, or for the after-close/
    before-open edge cases where the boundary-day bar isn't post-entry
    (or is post-exit) evidence at all — window-shifting to the adjacent
    trading session is not yet wired into requested_window_start/end
    construction, so this stays conservative rather than silently
    mis-widening the window."""
    from services.postmortem import session_boundary

    try:
        entry_result = session_boundary.classify_entry_boundary(market, entry_timestamp)
        exit_result = session_boundary.classify_exit_boundary(market, exit_timestamp)
    except session_boundary.SessionBoundaryError:
        return ENTRY_BAR_PARTIAL_UNKNOWN, EXIT_BAR_PARTIAL_UNKNOWN

    for lim in entry_result["limitations"] + exit_result["limitations"]:
        if lim not in limitations:
            limitations.append(lim)

    entry_policy = entry_result["policy"] or ENTRY_BAR_PARTIAL_UNKNOWN
    exit_policy = exit_result["policy"] or EXIT_BAR_PARTIAL_UNKNOWN
    return entry_policy, exit_policy


def _check_bounded_window(start: date, end: date) -> None:
    if end < start:
        raise AcquisitionWindowTooLargeError(f"end date {end} precedes start date {start}")
    window_days = (end - start).days + 1
    if window_days > MAX_ACQUISITION_WINDOW_DAYS:
        raise AcquisitionWindowTooLargeError(
            f"requested acquisition window is {window_days} days, exceeding the "
            f"{MAX_ACQUISITION_WINDOW_DAYS}-day bound — evidence acquisition is refused, not truncated"
        )


# Pre-Stage-H Correction 2 — pinned, explicit acquisition arguments.
# auto_adjust=False is used deliberately: yfinance's own documented
# contract for auto_adjust=False is that Open/High/Low/Close are the
# RAW, unadjusted exchange-reported values (only a separate "Adj Close"
# column is adjusted). Sprint 3A previously used auto_adjust=True and
# then ASSUMED its composite-adjusted OHLC was "just split-adjusted" —
# that assumption was never actually established by provider
# documentation, and is exactly what this correction removes. Raw OHLC
# under auto_adjust=False needs no adjustment-basis reconciliation claim
# at all to be compared against this codebase's own unadjusted
# paper-trade execution prices — it simply IS unadjusted, by the
# provider's own contract for that argument.
ACQUISITION_AUTO_ADJUST = False
ACQUISITION_BACK_ADJUST = False
ACQUISITION_ACTIONS = True
ACQUISITION_REPAIR = False
ACQUISITION_PREPOST = False  # regular session only — no pre/post-market bars
ACQUISITION_TIMEZONE_BEHAVIOR = "provider_local_exchange_timezone_normalized_to_date_only"

# Stage 1 hardening: yf.Ticker.history() has no threads/progress/ignore_tz
# parameters — those belong to yf.download(), which this module does not
# use (Ticker.history() is a single-symbol call by construction, so the
# MultiIndex-column concern yf.download() can introduce for multi-symbol
# requests does not arise here either). Documented explicitly rather than
# passed as no-op arguments that would silently do nothing.
_NOT_APPLICABLE_TO_TICKER_HISTORY = ("threads", "progress", "ignore_tz")


def _provider_library_version() -> str:
    try:
        import yfinance as yf
        return getattr(yf, "__version__", "unknown")
    except Exception:
        return "unknown"


def fetch_raw_daily_bars(
    provider_symbol: str, start: date, end: date, *, timeout: int = FETCH_TIMEOUT_SECONDS,
) -> list[dict]:
    """The one function in this module that calls yfinance. Returns a
    list of {date, open, high, low, close, volume, adj_close, dividend}
    dicts, raw and unvalidated — build_price_path_evidence does all
    validation. Uses the pinned, explicit acquisition arguments above
    (auto_adjust=False, actions=True) rather than any implicit default,
    per Pre-Stage-H Correction 2. Provider/network errors are caught
    here and re-raised as a sanitized PriceProviderAcquisitionError
    (Stage D item 7/8) — the caller never sees raw provider exception
    internals, and this function never returns a partially-built or
    fabricated bar list on failure."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(provider_symbol)
        # yfinance's own convention: start is inclusive, end is EXCLUSIVE.
        # Widening by one day here is only how the exit session gets INTO
        # the provider response at all — it is never allowed to let a
        # LATER session leak into the persisted evidence. That exact
        # exclusion is enforced downstream in build_price_path_evidence's
        # `if d < entry_date or d > exit_date: continue` filter, which
        # runs against the ORIGINAL (non-widened) exit date — this
        # function's own [start, end] widening is purely a provider-call
        # convenience the caller must never mistake for the persisted
        # window itself.
        df = ticker.history(
            start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
            interval="1d", auto_adjust=ACQUISITION_AUTO_ADJUST, back_adjust=ACQUISITION_BACK_ADJUST,
            actions=ACQUISITION_ACTIONS, repair=ACQUISITION_REPAIR, prepost=ACQUISITION_PREPOST,
            timeout=timeout,
        )
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            # Defensive: yf.Ticker.history() is single-symbol by
            # construction and should never return a MultiIndex, but if a
            # future yfinance version changes that, fail loudly rather
            # than silently misreading columns.
            raise PriceProviderAcquisitionError(
                "PROVIDER_UNEXPECTED_COLUMN_SHAPE",
                f"expected a single-symbol flat column index for {provider_symbol}, got a MultiIndex",
            )
        bars = []
        for idx, row in df.iterrows():
            bar_date = idx.tz_localize(None).date() if hasattr(idx, "tz_localize") and idx.tzinfo is not None else (
                idx.date() if hasattr(idx, "date") else idx
            )
            adj_close = float(row["Adj Close"]) if "Adj Close" in row and row["Adj Close"] == row["Adj Close"] else None
            dividend = float(row["Dividends"]) if "Dividends" in row and row["Dividends"] == row["Dividends"] else 0.0
            bars.append({
                "date": bar_date,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row["Volume"]) if "Volume" in row and row["Volume"] == row["Volume"] else None,
                "adj_close": adj_close,
                "dividend": dividend,
            })
    except PriceProviderAcquisitionError:
        raise
    except Exception as exc:
        raise PriceProviderAcquisitionError(
            "PROVIDER_FETCH_FAILED", f"daily bar acquisition failed for {provider_symbol}"
        ) from exc
    if len(bars) > MAX_ACQUISITION_BARS:
        raise PriceProviderAcquisitionError(
            "PROVIDER_RESPONSE_TOO_LARGE",
            f"provider returned {len(bars)} bars, exceeding the {MAX_ACQUISITION_BARS}-bar bound",
        )
    return bars


def fetch_dividend_events(
    provider_symbol: str, start: date, end: date, *, timeout: int = FETCH_TIMEOUT_SECONDS,
) -> list[date]:
    """Returns session dates on which a dividend was paid within
    [start, end]. Recorded in the manifest for completeness (Correction
    2's own requirement) — unlike a split, a dividend does not distort
    RAW (auto_adjust=False) OHLC values, so its presence is disclosed
    but does not by itself force UNKNOWN_ADJUSTMENT the way a split
    does."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(provider_symbol)
        dividends = ticker.dividends
        events = []
        for ts, amount in dividends.items():
            d = ts.date() if hasattr(ts, "date") else ts
            if start <= d <= end and amount and amount != 0.0:
                events.append(d)
    except Exception as exc:
        raise PriceProviderAcquisitionError(
            "PROVIDER_FETCH_FAILED", f"dividend-event acquisition failed for {provider_symbol}"
        ) from exc
    return events


def fetch_split_events(
    provider_symbol: str, start: date, end: date, *, timeout: int = FETCH_TIMEOUT_SECONDS,
) -> list[date]:
    """Returns session dates on which a stock split occurred within
    [start, end], per yfinance's own corporate-action history. Used only
    to decide whether SPLIT_ADJUSTED bars remain safely comparable to
    this codebase's own UNADJUSTED paper-trade execution prices — never
    to adjust prices itself (this module never invents a split ratio
    reconciliation; a split in-window means the safe, honest answer is
    LIMITED/UNAVAILABLE evidence, not a "corrected" number). Errors are
    sanitized identically to fetch_raw_daily_bars."""
    import yfinance as yf

    try:
        ticker = yf.Ticker(provider_symbol)
        splits = ticker.splits
        events = []
        for ts, ratio in splits.items():
            d = ts.date() if hasattr(ts, "date") else ts
            if start <= d <= end and ratio and ratio != 1.0:
                events.append(d)
    except Exception as exc:
        raise PriceProviderAcquisitionError(
            "PROVIDER_FETCH_FAILED", f"split-event acquisition failed for {provider_symbol}"
        ) from exc
    return events


def _count_requested_weekdays(entry_date: date, exit_date: date) -> int:
    """Mon-Fri count within [entry_date, exit_date], inclusive. Deliberately
    does NOT subtract market holidays — this codebase's holiday calendars
    (services.market_hours.NSE_EXTRA_HOLIDAYS / _us_fixed_holidays) are
    internal to that module and scoped to "is trading open right now"
    checks, not a general date-range session-counter, and reusing them
    here would silently couple this manifest's honesty to a calendar that
    itself needs annual manual refresh. Naming this field
    `requested_trading_weekday_count` rather than "expected trading
    sessions" is the explicit, honest boundary: weekends are excluded,
    holidays are not — never claim more precision than that."""
    count = 0
    d = entry_date
    one_day = timedelta(days=1)
    while d <= exit_date:
        if d.weekday() < 5:
            count += 1
        d += one_day
    return count


def _compute_manifest_integrity_hash(manifest: dict) -> str:
    """Separate from _compute_evidence_hash (bars only) — this covers the
    manifest's own provenance/configuration fields, so a manifest could be
    tampered with or corrupted independently of the bar data and still be
    caught. `unresolved_basis_limitations` is excluded because it is the
    same mutable list object as the bundle's own `limitations` field and
    is still being appended to by the caller at the point this function
    runs for the split-in-window/no-bars early-return paths — hashing a
    not-yet-final list would make the hash non-reproducible for the exact
    same acquisition. Every other field is fixed at this point."""
    hashable = {k: v for k, v in manifest.items() if k != "unresolved_basis_limitations"}
    canonical = json.dumps(hashable, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _compute_evidence_hash(bars: list[PricePathBar]) -> str:
    """Deterministic for identical bars — same discipline as Sprint 2's
    report_store.compute_evidence_hash: sorted-key JSON of exactly the
    bar content, SHA-256 hashed. Used for debugging/dedup visibility, not
    the version-triple uniqueness boundary (the DB unique index is)."""
    canonical = json.dumps(
        [
            {
                "session_date": b.session_date.isoformat(),
                "open": b.open, "high": b.high, "low": b.low, "close": b.close,
                "volume": b.volume, "adjustment_basis": b.adjustment_basis,
            }
            for b in bars
        ],
        sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_price_path_evidence(
    *,
    paper_trade_id: int,
    user_id: str,
    symbol: str,
    market: str,
    market_timezone_name: str,
    market_tzinfo,
    entry_timestamp: datetime,
    exit_timestamp: datetime,
    raw_bars: list[dict],
    split_events: list[date],
    dividend_events: list[date] | None = None,
    acquisition_timestamp: datetime | None = None,
) -> PricePathEvidenceBundle:
    """Pure: no I/O. Turns already-fetched raw daily bars into a
    validated, immutable PricePathEvidenceBundle.

    Window construction (Stage 4, refined by Pre-Stage-H Correction 1):
    the requested window is [entry_date, exit_date] in the market's own
    local calendar. Every bar between the two dates (inclusive) is
    retained; the entry-date and exit-date bars are each tagged with a
    REAL session-boundary policy computed by
    services.postmortem.session_boundary — ENTRY_BAR_INCLUDED_FULL /
    EXIT_BAR_INCLUDED_FULL when the trade timestamp lands exactly at
    official session open/close, PARTIAL_UNKNOWN otherwise. See
    _resolve_boundary_policies.

    Adjustment basis (Correction 2): raw_bars are expected to come from
    auto_adjust=False acquisition (this function's own contract, not
    merely a caller convention) — under yfinance's own documented
    contract for that argument, Open/High/Low/Close are UNADJUSTED
    exchange-reported values, directly comparable to this codebase's own
    unadjusted paper-trade prices without needing any adjustment-basis
    reconciliation claim. A split occurring in-window is still the one
    disqualifying fact this function checks for: a split changes what
    "unadjusted" even means across the boundary (pre-split prices are on
    a different share-count basis than post-split prices), so
    excursion values are never computed across a split regardless of
    which acquisition mode produced the bars — status becomes
    AMBIGUOUS_RESOLUTION and adjustment_basis becomes UNKNOWN_ADJUSTMENT,
    with an explicit limitation string, rather than silently computing a
    corrupted MFE/MAE across the split boundary."""
    if acquisition_timestamp is None:
        acquisition_timestamp = datetime.now(timezone.utc)
    if dividend_events is None:
        dividend_events = []

    entry_date = entry_timestamp.astimezone(market_tzinfo).date()
    exit_date = exit_timestamp.astimezone(market_tzinfo).date()

    # The +1-day widening fetch_raw_daily_bars actually sends as its
    # provider `end=` argument (yfinance's start-inclusive/end-exclusive
    # convention) — recomputed here (not passed in) because
    # build_price_path_evidence is pure/no-I/O and must stay reproducible
    # from persisted trade facts alone; this is the same arithmetic
    # fetch_raw_daily_bars itself uses, so the manifest's disclosed value
    # always matches what was actually requested.
    provider_exclusive_request_end = exit_date + timedelta(days=1)

    limitations: list[str] = []
    any_adj_close = any(
        raw.get("adj_close") is not None and raw.get("adj_close") == raw.get("adj_close")  # NaN != NaN
        for raw in raw_bars
    )
    source_manifest = {
        "source_manifest_schema_version": SOURCE_MANIFEST_SCHEMA_VERSION,
        "source_id": SOURCE_ID_YFINANCE_DAILY,
        "source_type": SOURCE_TYPE,
        "source_scope": SOURCE_SCOPE,
        "production_authoritative": False,
        "source_version": SOURCE_VERSION,
        "provider": "yfinance",
        "provider_library_version": _provider_library_version(),
        "interval": BAR_INTERVAL_DAILY,
        "auto_adjust": ACQUISITION_AUTO_ADJUST,
        "back_adjust": ACQUISITION_BACK_ADJUST,
        "actions": ACQUISITION_ACTIONS,
        "repair": ACQUISITION_REPAIR,
        "prepost": ACQUISITION_PREPOST,
        "timezone_behavior": ACQUISITION_TIMEZONE_BEHAVIOR,
        # PROVIDER_RETURNED_UNADJUSTED_OHLC, not "raw exchange-authoritative
        # prices" — this codebase does not claim yfinance IS the exchange
        # of record, only that it was ASKED for and RETURNED data under
        # the auto_adjust=False contract (see SOURCE_TYPE/SOURCE_SCOPE
        # above for the separate, already-explicit non-authoritative
        # disclosure).
        "raw_ohlc_basis_claim": "PROVIDER_RETURNED_UNADJUSTED_OHLC",
        "adjusted_close_available": any_adj_close,
        "split_event_manifest": [d.isoformat() for d in split_events],
        "dividend_event_manifest": [d.isoformat() for d in dividend_events],
        "provider_symbol": _provider_symbol(symbol, market),
        "trade_symbol": symbol.upper(),
        "symbol_normalization_version": SYMBOL_NORMALIZATION_VERSION,
        "market": market,
        "acquisition_mode": "auto_adjust_false",
        "provider_request_start": entry_date.isoformat(),
        "provider_exclusive_request_end": provider_exclusive_request_end.isoformat(),
        "end_widening_reason": (
            "yfinance's Ticker.history() treats `end` as exclusive; widened by one day "
            "purely so the exit session itself is included in the provider response — "
            "requested_window_end on the bundle remains the original, non-widened exit date"
        ),
        "boundary_policy_version": BOUNDARY_POLICY_VERSION,
        "requested_trading_weekday_count": _count_requested_weekdays(entry_date, exit_date),
        # Same list object as `limitations` below — appends made after
        # this point are visible through this key too, so the persisted
        # manifest always reflects the FINAL limitation set without a
        # second assignment at every return site.
        "unresolved_basis_limitations": limitations,
    }
    source_manifest["manifest_integrity_hash"] = _compute_manifest_integrity_hash(source_manifest)

    if dividend_events:
        limitations.append(
            f"a dividend was paid within the holding window ({[d.isoformat() for d in dividend_events]}) — "
            "disclosed for completeness; raw (auto_adjust=False) OHLC values are not distorted by a "
            "dividend the way they would be by a split, so this does not by itself block computation"
        )

    if split_events:
        limitations.append(
            f"a stock split occurred within the holding window ({[d.isoformat() for d in split_events]}) — "
            "a split changes what 'unadjusted' means across the boundary (different share-count basis "
            "before vs after), so excursion values are not computed for this trade"
        )
        return PricePathEvidenceBundle(
            evidence_bundle_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
            paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
            source_id=SOURCE_ID_YFINANCE_DAILY, source_type=SOURCE_TYPE, source_version=SOURCE_VERSION,
            provider_symbol=_provider_symbol(symbol, market), price_adjustment_basis=UNKNOWN_ADJUSTMENT,
            bar_interval=BAR_INTERVAL_DAILY, market_timezone=market_timezone_name,
            entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
            entry_bar_policy=ENTRY_BAR_PARTIAL_UNKNOWN, exit_bar_policy=EXIT_BAR_PARTIAL_UNKNOWN,
            requested_window_start=entry_date, requested_window_end=exit_date,
            observed_window_start=None, observed_window_end=None,
            bars_expected=None, bars_observed=0, missing_bar_count=None,
            data_completeness=STATUS_AMBIGUOUS_RESOLUTION, freshness_basis="acquired_at_generation_time",
            acquisition_timestamp=acquisition_timestamp, source_manifest=source_manifest,
            limitations=limitations, bars=(), evidence_hash=_compute_evidence_hash([]),
        )

    # Deduplicate and sort defensively — a provider returning a
    # duplicate/out-of-order row must never propagate into the immutable
    # bundle (Stage 15 test items #18/#19); the LATER of two rows for the
    # same date wins (arbitrary but deterministic — a provider should
    # never legitimately return two rows for one date in the first
    # place, so this is a defensive tie-break, not a real code path).
    by_date: dict[date, dict] = {}
    for raw in raw_bars:
        by_date[raw["date"]] = raw
    ordered_dates = sorted(by_date.keys())

    bars: list[PricePathBar] = []
    for d in ordered_dates:
        if d < entry_date or d > exit_date:
            continue
        raw = by_date[d]
        vals = (raw["open"], raw["high"], raw["low"], raw["close"])
        if not all(v == v and math.isfinite(v) for v in vals):
            # NaN/inf close (the documented prediction_engine.py
            # placeholder-row bug this codebase has already hit once) —
            # skip the bar rather than let it reach PricePathBar's own
            # construction guard (which would raise and abort the whole
            # bundle for one bad row).
            continue
        bars.append(PricePathBar(
            timestamp=datetime(d.year, d.month, d.day, tzinfo=market_tzinfo),
            interval=BAR_INTERVAL_DAILY,
            open=raw["open"], high=raw["high"], low=raw["low"], close=raw["close"],
            volume=raw.get("volume"), session_date=d,
            source_id=SOURCE_ID_YFINANCE_DAILY, adjustment_basis=UNADJUSTED,
            verification_level="DIRECTLY_OBSERVED",
        ))

    entry_bar_policy, exit_bar_policy = _resolve_boundary_policies(
        market, entry_timestamp, exit_timestamp, limitations,
    )

    if not bars:
        return PricePathEvidenceBundle(
            evidence_bundle_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
            paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
            source_id=SOURCE_ID_YFINANCE_DAILY, source_type=SOURCE_TYPE, source_version=SOURCE_VERSION,
            provider_symbol=_provider_symbol(symbol, market), price_adjustment_basis=UNADJUSTED,
            bar_interval=BAR_INTERVAL_DAILY, market_timezone=market_timezone_name,
            entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
            entry_bar_policy=entry_bar_policy, exit_bar_policy=exit_bar_policy,
            requested_window_start=entry_date, requested_window_end=exit_date,
            observed_window_start=None, observed_window_end=None,
            bars_expected=None, bars_observed=0, missing_bar_count=None,
            data_completeness=STATUS_UNAVAILABLE, freshness_basis="acquired_at_generation_time",
            acquisition_timestamp=acquisition_timestamp, source_manifest=source_manifest,
            limitations=["no valid bars were returned by the provider for the requested window"],
            bars=(), evidence_hash=_compute_evidence_hash([]),
        )

    observed_start = bars[0].session_date
    observed_end = bars[-1].session_date
    expected_calendar_days = (exit_date - entry_date).days + 1
    completeness = STATUS_COMPLETE if (observed_start == entry_date and observed_end == exit_date) else STATUS_PARTIAL
    if completeness == STATUS_PARTIAL:
        limitations.append(
            f"requested window {entry_date}..{exit_date} but provider only returned bars for "
            f"{observed_start}..{observed_end} — some sessions are missing (weekends/holidays are expected "
            "absences, not evidence gaps; this limitation covers unexplained gaps within the observed range)"
        )

    return PricePathEvidenceBundle(
        evidence_bundle_version=EVIDENCE_BUNDLE_SCHEMA_VERSION,
        paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
        source_id=SOURCE_ID_YFINANCE_DAILY, source_type=SOURCE_TYPE, source_version=SOURCE_VERSION,
        provider_symbol=_provider_symbol(symbol, market), price_adjustment_basis=UNADJUSTED,
        bar_interval=BAR_INTERVAL_DAILY, market_timezone=market_timezone_name,
        entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
        entry_bar_policy=entry_bar_policy, exit_bar_policy=exit_bar_policy,
        requested_window_start=entry_date, requested_window_end=exit_date,
        observed_window_start=observed_start, observed_window_end=observed_end,
        bars_expected=expected_calendar_days, bars_observed=len(bars),
        missing_bar_count=None,  # non-trading days are expected absences, not "missing" — never counted here
        data_completeness=completeness, freshness_basis="acquired_at_generation_time",
        acquisition_timestamp=acquisition_timestamp, source_manifest=source_manifest,
        limitations=limitations, bars=tuple(bars), evidence_hash=_compute_evidence_hash(bars),
    )


def acquire_price_path_evidence(
    *,
    paper_trade_id: int, user_id: str, symbol: str, market: str,
    market_timezone_name: str, market_tzinfo,
    entry_timestamp: datetime, exit_timestamp: datetime,
    fetch_bars_fn=fetch_raw_daily_bars, fetch_splits_fn=fetch_split_events,
    fetch_dividends_fn=fetch_dividend_events,
) -> PricePathEvidenceBundle:
    """Thin orchestration: resolves the provider symbol, fetches raw
    bars, split events and dividend events (the only I/O in this
    module), then delegates to the pure build_price_path_evidence.
    Callers MUST invoke this OUTSIDE any open database transaction
    (Stage 12) — this function never accepts a `conn` parameter, by
    design, so it cannot be called from inside one by accident."""
    provider_symbol = _provider_symbol(symbol, market)
    entry_date = entry_timestamp.astimezone(market_tzinfo).date()
    exit_date = exit_timestamp.astimezone(market_tzinfo).date()

    _check_bounded_window(entry_date, exit_date)

    raw_bars = fetch_bars_fn(provider_symbol, entry_date, exit_date)
    split_events = fetch_splits_fn(provider_symbol, entry_date, exit_date)
    dividend_events = fetch_dividends_fn(provider_symbol, entry_date, exit_date)

    return build_price_path_evidence(
        paper_trade_id=paper_trade_id, user_id=user_id, symbol=symbol, market=market,
        market_timezone_name=market_timezone_name, market_tzinfo=market_tzinfo,
        entry_timestamp=entry_timestamp, exit_timestamp=exit_timestamp,
        raw_bars=raw_bars, split_events=split_events, dividend_events=dividend_events,
    )


# --- Stage F: price-basis / corporate-action compatibility classification ---
# A separate, explicit classification layer on top of the bundle's own
# coarser price_adjustment_basis field (UNADJUSTED/UNKNOWN_ADJUSTMENT).
# This does not change bundle construction — it gives callers (report
# rendering, docs, tests) a documented, six-state vocabulary for exactly
# why a given evidence bundle's prices are or are not safely comparable
# to the paper trade's own unadjusted execution prices.
COMPATIBLE_UNADJUSTED = "COMPATIBLE_UNADJUSTED"
COMPATIBLE_SPLIT_ADJUSTED = "COMPATIBLE_SPLIT_ADJUSTED"
BASIS_MISMATCH = "BASIS_MISMATCH"
SPLIT_IN_WINDOW = "SPLIT_IN_WINDOW"
BASIS_UNKNOWN_ADJUSTMENT = "UNKNOWN_ADJUSTMENT"
BASIS_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

# Correction 2's expanded vocabulary — distinct from the four names
# above where the distinction actually matters (a raw auto_adjust=False
# fetch needs no reconciliation "claim" the way an auto_adjust=True
# fetch's COMPATIBLE_UNADJUSTED conclusion still implicitly does).
UNADJUSTED_PROVIDER_OHLC = "UNADJUSTED_PROVIDER_OHLC"
SPLIT_RECONCILED = "SPLIT_RECONCILED"
COMPOSITE_ADJUSTED_UNKNOWN_BASIS = "COMPOSITE_ADJUSTED_UNKNOWN_BASIS"
DIVIDEND_ADJUSTMENT_PRESENT = "DIVIDEND_ADJUSTMENT_PRESENT"


def evaluate_basis_compatibility(
    *, acquisition_mode: str | None, split_events: list[date], bars_observed: int,
    dividend_events: list[date] | None = None,
) -> str:
    """Stage F / Correction 2 — never assumes no split occurred. A split
    anywhere in the requested window is the single most important fact
    and takes priority over every other consideration, since this
    codebase has no reconciled split-ratio adjustment logic (see
    build_price_path_evidence's own docstring — a split in-window
    already forces UNKNOWN_ADJUSTMENT at the bundle level; this function
    names that same fact under this module's own reporting vocabulary).

    acquisition_mode is read from the persisted source_manifest — never
    re-derived — so replay of an already-persisted evidence bundle
    reproduces the identical compatibility result without any new
    provider call (Stage D item 5 / Stage F's own "same persisted
    evidence reproduces the same result" requirement).

    A missing split event is checked (split_events is empty) but that
    absence is NOT by itself treated as proof of a compatible basis for
    any acquisition mode other than auto_adjust_false — a composite
    "auto_adjust_true"-style feed remains COMPATIBLE_UNADJUSTED only
    because this codebase's own numerically-identical-absent-a-split
    reasoning is documented and tested (see the acquisition_mode ==
    'auto_adjust_true' branch below); any OTHER/unrecognized adjusted
    mode is COMPOSITE_ADJUSTED_UNKNOWN_BASIS, not silently assumed
    compatible."""
    if split_events:
        return SPLIT_IN_WINDOW
    if bars_observed <= 0:
        return BASIS_INSUFFICIENT_EVIDENCE
    if acquisition_mode == "auto_adjust_false":
        # This module's own current, pinned acquisition mode (Correction
        # 2): yfinance's documented contract for auto_adjust=False is
        # that OHLC are the raw, unadjusted exchange-reported values — no
        # reconciliation claim is even needed here, unlike the
        # auto_adjust_true branch below.
        return UNADJUSTED_PROVIDER_OHLC
    if acquisition_mode == "auto_adjust_true":
        # Retained for backward-compatible replay of any evidence
        # persisted before Correction 2 switched the pinned mode. Absent
        # any split in-window (checked above), split-adjusted and
        # raw/unadjusted values are numerically identical for this
        # window — safely comparable to this codebase's own unadjusted
        # paper-trade execution prices.
        return COMPATIBLE_UNADJUSTED
    if acquisition_mode == "total_return_adjusted":
        # Never used for trade-path execution analysis (Stage F's own
        # explicit prohibition) — always a mismatch against unadjusted
        # execution prices, split or no split.
        return BASIS_MISMATCH
    if acquisition_mode is None:
        return BASIS_INSUFFICIENT_EVIDENCE
    return COMPOSITE_ADJUSTED_UNKNOWN_BASIS
