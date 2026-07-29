"""
Point-in-time price-path evidence contract — Trade Postmortem Sprint 3A.

Problem this solves: Sprint 1/2 can say WHAT happened at entry and exit
(claim-governed, immutable) but nothing about what happened BETWEEN
them — how far price moved favorably, how far adversely, whether a
stop/target was touched, and in what order. This module defines the
typed, versioned contract for that evidence: a bundle of daily OHLC bars
covering the trade's holding window, with enough provenance metadata
that every downstream claim can cite exactly which bars support it.

Source decision (Stage 1, documented in full in the Engineering
Handbook): **daily-bar evidence only**. This codebase has no persisted
OHLC bar store, no intraday data source, and its one provider with real
historical depth (yfinance) is an unofficial scraper with no in-repo
licensing review and inconsistent adjustment-basis usage across
call sites. Building intraday price-path evidence today would mean
either fabricating precision the data doesn't support or taking on an
unreviewed paid/scraped intraday dependency — neither is authorized by
this sprint's scope. Every ambiguity rule in price_path_calculator.py is
written assuming daily resolution is all that will ever be available
here; same-day stop/target touches are therefore ALWAYS ambiguous.

No bar may contain NaN or Infinity (validated at construction). No
timestamp may be timezone-naive (validated at construction) — mirrors
the anti-fabrication discipline entry_snapshot.py/exit_snapshot.py
already established for this codebase.
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from services.postmortem.price_path_identity import EVIDENCE_BUNDLE_SCHEMA_VERSION

# The only bar interval this sprint acquires or supports — see module
# docstring's source decision. A future sprint that adds a genuine
# reviewed intraday source would introduce new interval values here,
# never silently repurpose this one.
BAR_INTERVAL_DAILY = "1d"

# Trade Postmortem Sprint 3A, Stage 5 — price adjustment basis. A daily
# bar's OHLC values may be split-adjusted (yfinance's auto_adjust=True
# convention, used by daily_picks.py/screener_service.py in this
# codebase) or unadjusted (this codebase's own paper-trade execution
# prices, which are always "last_traded_unadjusted" — see
# entry_snapshot.py / exit_snapshot.py's own quote_price_basis labels).
# Comparing the two without reconciliation would silently misrepresent
# excursion values around a split event.
UNADJUSTED = "UNADJUSTED"
SPLIT_ADJUSTED = "SPLIT_ADJUSTED"
TOTAL_RETURN_ADJUSTED = "TOTAL_RETURN_ADJUSTED"
UNKNOWN_ADJUSTMENT = "UNKNOWN_ADJUSTMENT"

_VALID_ADJUSTMENT_BASES = frozenset({UNADJUSTED, SPLIT_ADJUSTED, TOTAL_RETURN_ADJUSTED, UNKNOWN_ADJUSTMENT})


class PricePathEvidenceError(ValueError):
    """Raised by this module's own construction-time validation — a bar
    or bundle that would violate the anti-fabrication contract (NaN/
    Infinity values, timezone-naive timestamps, non-finite prices) never
    becomes a PricePathBar/PricePathEvidenceBundle object in the first
    place."""


def _is_finite_number(value) -> bool:
    return (
        value is not None
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _is_finite_positive(value) -> bool:
    return _is_finite_number(value) and value > 0


@dataclass(frozen=True)
class PricePathBar:
    """One immutable OHLC observation. `timestamp` represents the bar's
    session date at market-local midnight-equivalent (daily resolution
    has no intraday time-of-day to represent) but is always stored
    timezone-AWARE (the market's own tz) — never naive, so a bar can
    never be silently misinterpreted in the wrong timezone downstream."""

    timestamp: datetime
    interval: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    session_date: date
    source_id: str
    adjustment_basis: str
    verification_level: str

    def __post_init__(self):
        if self.timestamp.tzinfo is None:
            raise PricePathEvidenceError(
                f"PricePathBar timestamp must be timezone-aware, got naive datetime {self.timestamp!r}"
            )
        for field_name in ("open", "high", "low", "close"):
            value = getattr(self, field_name)
            if not _is_finite_positive(value):
                raise PricePathEvidenceError(
                    f"PricePathBar.{field_name} must be a finite positive number, got {value!r}"
                )
        if self.volume is not None and not _is_finite_number(self.volume):
            raise PricePathEvidenceError(f"PricePathBar.volume must be finite when present, got {self.volume!r}")
        if not (self.low <= self.open <= self.high and self.low <= self.close <= self.high):
            raise PricePathEvidenceError(
                f"PricePathBar OHLC values are internally inconsistent for session {self.session_date}: "
                f"open={self.open} high={self.high} low={self.low} close={self.close}"
            )
        if self.adjustment_basis not in _VALID_ADJUSTMENT_BASES:
            raise PricePathEvidenceError(f"unknown adjustment_basis: {self.adjustment_basis!r}")


# Entry/exit boundary policies — Stage 4. A daily bar covering the entry
# (or exit) session may contain price action from BEFORE the trade
# actually opened (or AFTER it actually closed), since this codebase has
# no tick-level data to isolate the exact intraday moment. Every
# downstream calculation must know which policy applied to a given bar
# so it can decide whether that bar's extremes are safe to use.
ENTRY_BAR_INCLUDED_FULL = "ENTRY_BAR_INCLUDED_FULL"
ENTRY_BAR_PARTIAL_UNKNOWN = "ENTRY_BAR_PARTIAL_UNKNOWN"
EXIT_BAR_INCLUDED_FULL = "EXIT_BAR_INCLUDED_FULL"
EXIT_BAR_PARTIAL_UNKNOWN = "EXIT_BAR_PARTIAL_UNKNOWN"

# Evidence statuses — Stage 3.
STATUS_COMPLETE = "COMPLETE"
STATUS_PARTIAL = "PARTIAL"
STATUS_UNAVAILABLE = "UNAVAILABLE"

# LEGACY-READ COMPATIBILITY (Stage J-F2, decided over VERSIONED REMOVAL):
# build_price_path_evidence has never assigned this value (confirmed by
# direct inspection of price_path_acquisition.py, twice, in two separate
# audit phases) and price_path_evidence_decision._DATA_COMPLETENESS_TO_
# EVIDENCE_STATUS deliberately excludes it as a mapping key — any bundle
# carrying it (fresh or previously persisted) is classified fail-closed
# as UNSUPPORTED_EVIDENCE_COMPLETENESS by the decision layer, never
# treated as an active, presentable state. It remains declared and
# _VALID_STATUSES-accepted here ONLY so that (a) __post_init__ does not
# reject a legacy row read back from storage before it ever existed
# under the current classification rules, and (b) a bundle reconstructed
# for replay/audit purposes can still be constructed at all. No code
# path in this module or its callers ever assigns this value to a fresh
# bundle; see test_legacy_invalid_source_data_status_has_no_producer and
# test_legacy_invalid_source_data_bundle_replays_without_error in
# tests/unit/test_price_path_evidence.py.
STATUS_INVALID_SOURCE_DATA = "INVALID_SOURCE_DATA"

STATUS_AMBIGUOUS_RESOLUTION = "AMBIGUOUS_RESOLUTION"

_VALID_STATUSES = frozenset({
    STATUS_COMPLETE, STATUS_PARTIAL, STATUS_UNAVAILABLE,
    STATUS_INVALID_SOURCE_DATA, STATUS_AMBIGUOUS_RESOLUTION,
})


@dataclass(frozen=True)
class PricePathEvidenceBundle:
    """The full, versioned, persisted price-path evidence for one trade.
    Preserves every bar that supports the eventual MFE/MAE/touch
    calculations — never just the computed summary numbers (Stage 3's
    own explicit requirement: "do not store only computed MFE/MAE while
    discarding the bars that support the calculation")."""

    evidence_bundle_version: str
    paper_trade_id: int
    user_id: str
    symbol: str
    market: str
    source_id: str
    source_type: str
    source_version: str
    provider_symbol: str
    price_adjustment_basis: str
    bar_interval: str
    market_timezone: str
    entry_timestamp: datetime
    exit_timestamp: datetime
    entry_bar_policy: str
    exit_bar_policy: str
    requested_window_start: date
    requested_window_end: date
    observed_window_start: date | None
    observed_window_end: date | None
    bars_expected: int | None
    bars_observed: int
    missing_bar_count: int | None
    data_completeness: str
    freshness_basis: str
    acquisition_timestamp: datetime
    source_manifest: dict
    limitations: list[str] = field(default_factory=list)
    bars: tuple[PricePathBar, ...] = field(default_factory=tuple)
    evidence_hash: str = ""

    def __post_init__(self):
        for ts_name in ("entry_timestamp", "exit_timestamp", "acquisition_timestamp"):
            ts = getattr(self, ts_name)
            if ts.tzinfo is None:
                raise PricePathEvidenceError(f"PricePathEvidenceBundle.{ts_name} must be timezone-aware, got naive")
        if self.exit_timestamp < self.entry_timestamp:
            raise PricePathEvidenceError("exit_timestamp cannot precede entry_timestamp")
        if self.data_completeness not in _VALID_STATUSES:
            raise PricePathEvidenceError(f"unknown data_completeness status: {self.data_completeness!r}")
        if self.price_adjustment_basis not in _VALID_ADJUSTMENT_BASES:
            raise PricePathEvidenceError(f"unknown price_adjustment_basis: {self.price_adjustment_basis!r}")
        if self.bars_observed != len(self.bars):
            raise PricePathEvidenceError(
                f"bars_observed ({self.bars_observed}) does not match len(bars) ({len(self.bars)})"
            )
        # Duplicate/out-of-order protection — Stage 15 test items #18/#19.
        # A bundle is never persisted with two bars for the same session
        # date, and bars are always stored in ascending session-date
        # order so every downstream consumer can rely on that ordering
        # without re-sorting (and re-sorting silently would hide a
        # genuine acquisition bug rather than surfacing it).
        seen_dates = set()
        prev_date = None
        for bar in self.bars:
            if bar.session_date in seen_dates:
                raise PricePathEvidenceError(f"duplicate bar for session_date {bar.session_date}")
            seen_dates.add(bar.session_date)
            if prev_date is not None and bar.session_date < prev_date:
                raise PricePathEvidenceError(
                    f"bars are not in ascending session-date order: {prev_date} followed by {bar.session_date}"
                )
            prev_date = bar.session_date
