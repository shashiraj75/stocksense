"""
Record schema for the offline instrument-master foundation (Phase C0).

Identity model (per the accepted Phase B Closure Report §6):
  - ISIN is primary security identity where present. A record with no
    ISIN can still be retained (never silently dropped) but can never be
    treated as a stable, mergeable identity across snapshots.
  - `symbol` is a mutable display/trading attribute, never identity.
  - `series` is a mutable exchange settlement/surveillance-status
    attribute, never identity and never instrument taxonomy.
  - Company/display name is never used as an identity or classification
    signal (see parser.py's explicit rejection of name-substring
    classification, and the false-positive evidence in the accepted
    Phase B Closure Report §3).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .enums import (
    ActiveStatus,
    ClassificationStatus,
    EligibilityStatus,
    ExchangeSeries,
    FundamentalsCoverageStatus,
    InstrumentCategory,
    MarketDataStatus,
    MinimumHistoryBand,
)


@dataclass(frozen=True)
class SymbolHistoryEntry:
    symbol: str
    effective_from: Optional[str]  # explicit source-supplied date/run label, never wall-clock
    effective_to: Optional[str]


@dataclass(frozen=True)
class SeriesHistoryEntry:
    series: str
    effective_from: Optional[str]
    effective_to: Optional[str]


@dataclass(frozen=True)
class InstrumentMasterRecord:
    """One instrument-master record. Immutable — a re-derivation always
    produces a new record rather than mutating an existing one, so that
    canonical serialization is a pure function of the record's fields."""

    schema_version: int
    exchange: str
    symbol: str
    display_name: str
    isin: Optional[str]
    series: ExchangeSeries
    instrument_category: InstrumentCategory
    classification_status: ClassificationStatus
    listing_date: Optional[str]
    active_status: ActiveStatus
    source_identifier: str
    source_publication_date: Optional[str]
    record_version: int
    eligibility_status: EligibilityStatus
    eligibility_reason_codes: tuple[str, ...] = field(default_factory=tuple)
    previous_symbol: Optional[str] = None
    symbol_history: tuple[SymbolHistoryEntry, ...] = field(default_factory=tuple)
    series_history: tuple[SeriesHistoryEntry, ...] = field(default_factory=tuple)
    minimum_history_band: MinimumHistoryBand = MinimumHistoryBand.UNKNOWN
    fundamentals_coverage_status: FundamentalsCoverageStatus = (
        FundamentalsCoverageStatus.UNKNOWN
    )
    market_data_status: MarketDataStatus = MarketDataStatus.UNKNOWN
