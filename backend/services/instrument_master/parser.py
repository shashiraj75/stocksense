"""
Offline CSV parser and identity/classification resolver for the
instrument-master foundation (Phase C0).

This module reads only local files passed explicitly by the caller. It
performs no network I/O, no database I/O, and imports nothing from
`services.stock_universe`, `services.daily_picks`, `services.postgres_store`,
or any other production module — see test_instrument_master_safety.py for
an automated guard against that ever regressing.

Classification discipline (per the accepted Phase B Closure Report):
  - EQ/BE/BZ are exchange trading/settlement series, never instrument
    taxonomy. A record is never auto-classified as `ordinary_common_equity`
    just because its series is EQ.
  - Company/display name is never used to derive `instrument_category`.
    The only two ways a record's category becomes anything other than
    `unknown_unclassified` are: (1) an explicit, exact symbol match
    against the fixed, hardcoded auxiliary-tracking-instrument set
    (mirroring `stock_universe.py`'s own `TRACKING_ONLY_SYMBOLS`
    precedent, not name matching), or (2) an explicit local
    secondary-classification fixture keyed by ISIN.
"""
from __future__ import annotations

import csv
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
from .schema import InstrumentMasterRecord, SeriesHistoryEntry, SymbolHistoryEntry

# Exact, hardcoded set — mirrors backend/services/stock_universe.py's own
# TRACKING_ONLY_SYMBOLS. Never derived from a name/keyword substring match
# (see the accepted Phase B Closure Report §3 for why substring matching
# on company name is unsafe: 8/8 false positives in real NSE data).
AUXILIARY_TRACKING_SYMBOLS = frozenset({"GLD", "SLV", "GOLDBEES", "SILVERBEES"})

REQUIRED_HEADER_COLUMNS = ("SYMBOL", "NAME OF COMPANY", "SERIES", "ISIN NUMBER")
OPTIONAL_HEADER_COLUMNS = (
    "DATE OF LISTING",
    "PAID UP VALUE",
    "MARKET LOT",
    "FACE VALUE",
)
_UNPARSED_EXTRA_KEY = "__unparsed_extra__"

_SERIES_MAP = {
    "EQ": ExchangeSeries.EQ,
    "BE": ExchangeSeries.BE,
    "BZ": ExchangeSeries.BZ,
}


class SourceValidationError(Exception):
    """Raised for a whole-file structural failure (e.g. a missing
    mandatory column in the header, or an empty file) — never raised for
    a single bad row, which is instead recorded as a rejection."""


@dataclass
class RawRow:
    line_number: int
    symbol: str
    name_of_company: str
    series_raw: str
    isin_raw: str
    listing_date_raw: Optional[str]


@dataclass
class RejectedRow:
    line_number: int
    reason_code: str
    detail: str


@dataclass
class ParseResult:
    accepted_candidates: list["_Candidate"] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    total_rows: int = 0


@dataclass
class _Candidate:
    line_number: int
    symbol: str
    display_name: str
    isin: Optional[str]
    series: ExchangeSeries
    listing_date: Optional[str]


def _normalize_header_name(name: str) -> str:
    return name.strip().upper()


def _strip_or_none(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _load_rows(csv_text: str) -> tuple[list[str], list[dict]]:
    """Parses raw CSV text into (header_names, raw_dict_rows). Header
    names are normalized (stripped, upper-cased) so reordered and
    whitespace-padded headers are handled uniformly. Uses csv.DictReader
    with a distinct restkey so rows carrying more fields than the header
    (a symptom of an unquoted embedded comma — a malformed row) are
    detectable rather than silently truncated."""
    reader = csv.reader(csv_text.splitlines())
    try:
        raw_header = next(reader)
    except StopIteration:
        raise SourceValidationError("empty_file: no header row present")

    header = [_normalize_header_name(h) for h in raw_header]
    missing = [c for c in REQUIRED_HEADER_COLUMNS if c not in header]
    if missing:
        raise SourceValidationError(
            f"missing_mandatory_column: {', '.join(missing)}"
        )

    dict_reader = csv.DictReader(
        csv_text.splitlines(),
        fieldnames=header,
        restkey=_UNPARSED_EXTRA_KEY,
    )
    next(dict_reader)  # skip header row (already consumed above)
    return header, list(dict_reader)


def parse_source_csv(csv_text: str, *, source_identifier: str) -> ParseResult:
    """Parses a local NSE-equity-list-shaped CSV (already read into
    memory by the caller) into accepted candidates and structural
    rejections. Raises SourceValidationError for a whole-file failure
    (empty file, missing mandatory column) rather than a per-row one."""
    header, raw_rows = _load_rows(csv_text)
    result = ParseResult(total_rows=len(raw_rows))

    for idx, row in enumerate(raw_rows, start=2):  # header is line 1
        if _UNPARSED_EXTRA_KEY in row:
            result.rejected.append(
                RejectedRow(
                    line_number=idx,
                    reason_code="malformed_row",
                    detail="row has more fields than the header (likely an unquoted embedded comma)",
                )
            )
            continue

        symbol = _strip_or_none(row.get("SYMBOL"))
        name = _strip_or_none(row.get("NAME OF COMPANY"))
        series_raw = _strip_or_none(row.get("SERIES"))
        isin_raw = _strip_or_none(row.get("ISIN NUMBER"))
        listing_date_raw = _strip_or_none(row.get("DATE OF LISTING"))

        if symbol is None or name is None or series_raw is None:
            result.rejected.append(
                RejectedRow(
                    line_number=idx,
                    reason_code="malformed_row",
                    detail="missing SYMBOL, NAME OF COMPANY, or SERIES value",
                )
            )
            continue

        series = _SERIES_MAP.get(series_raw.upper())
        if series is None:
            series = ExchangeSeries.OTHER if series_raw else ExchangeSeries.UNKNOWN

        candidate = _Candidate(
            line_number=idx,
            symbol=symbol.upper(),
            display_name=name,
            isin=isin_raw.upper() if isin_raw else None,
            series=series,
            listing_date=listing_date_raw,
        )
        result.accepted_candidates.append(candidate)

    return result


def load_secondary_classifications(csv_text: str) -> dict[str, tuple[InstrumentCategory, ClassificationStatus]]:
    """Loads an optional, purely local, offline secondary-classification
    fixture keyed by ISIN: columns ISIN,INSTRUMENT_CATEGORY,CLASSIFICATION_STATUS.
    This models what a real verified secondary source (e.g. the ETF list
    identified in the accepted Phase B Closure Report §5) would supply,
    without ever fetching one — the fixture is authored by hand and
    committed as a deterministic test input, never retrieved live."""
    reader = csv.DictReader(csv_text.splitlines())
    out: dict[str, tuple[InstrumentCategory, ClassificationStatus]] = {}
    for row in reader:
        isin = _strip_or_none(row.get("ISIN"))
        category_raw = _strip_or_none(row.get("INSTRUMENT_CATEGORY"))
        status_raw = _strip_or_none(row.get("CLASSIFICATION_STATUS"))
        if not isin or not category_raw or not status_raw:
            continue
        try:
            category = InstrumentCategory(category_raw)
            status = ClassificationStatus(status_raw)
        except ValueError:
            continue
        out[isin.upper()] = (category, status)
    return out


def _detect_duplicates(candidates: list[_Candidate]) -> tuple[set[str], set[str]]:
    isin_counts: dict[str, int] = {}
    symbol_counts: dict[str, int] = {}
    for c in candidates:
        if c.isin:
            isin_counts[c.isin] = isin_counts.get(c.isin, 0) + 1
        symbol_counts[c.symbol] = symbol_counts.get(c.symbol, 0) + 1
    dup_isins = {isin for isin, n in isin_counts.items() if n > 1}
    dup_symbols = {sym for sym, n in symbol_counts.items() if n > 1}
    return dup_isins, dup_symbols


def _classify(
    candidate: _Candidate,
    secondary: dict[str, tuple[InstrumentCategory, ClassificationStatus]],
) -> tuple[InstrumentCategory, ClassificationStatus]:
    if candidate.symbol in AUXILIARY_TRACKING_SYMBOLS:
        return InstrumentCategory.AUXILIARY_TRACKING_INSTRUMENT, ClassificationStatus.VERIFIED
    if candidate.isin and candidate.isin in secondary:
        return secondary[candidate.isin]
    return InstrumentCategory.UNKNOWN_UNCLASSIFIED, ClassificationStatus.UNAVAILABLE


def _derive_eligibility(
    *,
    candidate: _Candidate,
    category: InstrumentCategory,
    classification_status: ClassificationStatus,
    is_duplicate_isin: bool,
    is_duplicate_symbol: bool,
) -> tuple[EligibilityStatus, tuple[str, ...]]:
    if candidate.isin is None:
        return EligibilityStatus.UNSUPPORTED_INSTRUMENT, ("missing_isin",)

    reasons = []
    if is_duplicate_isin:
        reasons.append("duplicate_isin")
    if is_duplicate_symbol:
        reasons.append("duplicate_symbol")
    if reasons:
        return EligibilityStatus.AMBIGUOUS_CLASSIFICATION, tuple(reasons)

    if category == InstrumentCategory.AUXILIARY_TRACKING_INSTRUMENT:
        return EligibilityStatus.SHADOW_ONLY, ("auxiliary_tracking_instrument_excluded",)

    if candidate.series == ExchangeSeries.BE:
        return EligibilityStatus.SURVEILLANCE_SERIES, ("t2t_series_be",)
    if candidate.series == ExchangeSeries.BZ:
        return EligibilityStatus.SURVEILLANCE_SERIES, ("t2t_series_bz",)
    if candidate.series in (ExchangeSeries.OTHER, ExchangeSeries.UNKNOWN):
        return EligibilityStatus.AMBIGUOUS_CLASSIFICATION, ("unclassified_instrument_category",)

    if classification_status in (
        ClassificationStatus.PROVISIONAL,
        ClassificationStatus.AMBIGUOUS,
        ClassificationStatus.UNAVAILABLE,
    ):
        return EligibilityStatus.AMBIGUOUS_CLASSIFICATION, ("unclassified_instrument_category",)

    # classification_status == VERIFIED beyond this point
    if category == InstrumentCategory.ORDINARY_COMMON_EQUITY:
        return EligibilityStatus.ELIGIBLE, ()
    return EligibilityStatus.UNSUPPORTED_INSTRUMENT, ("confirmed_non_equity_instrument",)


def build_records(
    parse_result: ParseResult,
    *,
    schema_version: int,
    source_identifier: str,
    source_publication_date: Optional[str],
    secondary_classifications: Optional[dict[str, tuple[InstrumentCategory, ClassificationStatus]]] = None,
    prior_candidates_by_isin: Optional[dict[str, _Candidate]] = None,
    prior_generated_at: Optional[str] = None,
    current_generated_at: Optional[str] = None,
) -> tuple[list[InstrumentMasterRecord], dict]:
    """Resolves parsed candidates into final InstrumentMasterRecord
    objects plus a stats dict (accepted/ambiguous/rejected/duplicate/
    reason-code counts) suitable for the CLI manifest.

    `prior_candidates_by_isin`, if supplied, models a previously-parsed
    snapshot (also purely local/offline) — used only to populate
    symbol_history/series_history when the same ISIN's symbol or series
    differs between the two. This never triggers a network or database
    call; both snapshots are files read by the caller.
    """
    secondary = secondary_classifications or {}
    prior_map = prior_candidates_by_isin or {}
    dup_isins, dup_symbols = _detect_duplicates(parse_result.accepted_candidates)

    records: list[InstrumentMasterRecord] = []
    reason_code_counts: dict[str, int] = {}
    accepted_count = 0
    ambiguous_count = 0
    duplicate_isin_count = 0
    duplicate_symbol_count = 0

    for candidate in parse_result.accepted_candidates:
        category, classification_status = _classify(candidate, secondary)
        is_dup_isin = bool(candidate.isin and candidate.isin in dup_isins)
        is_dup_symbol = candidate.symbol in dup_symbols
        if is_dup_isin:
            duplicate_isin_count += 1
        if is_dup_symbol:
            duplicate_symbol_count += 1

        eligibility_status, reason_codes = _derive_eligibility(
            candidate=candidate,
            category=category,
            classification_status=classification_status,
            is_duplicate_isin=is_dup_isin,
            is_duplicate_symbol=is_dup_symbol,
        )
        for rc in reason_codes:
            reason_code_counts[rc] = reason_code_counts.get(rc, 0) + 1

        if eligibility_status in (
            EligibilityStatus.AMBIGUOUS_CLASSIFICATION,
        ) or candidate.isin is None:
            ambiguous_count += 1
        else:
            accepted_count += 1

        previous_symbol = None
        symbol_history: tuple[SymbolHistoryEntry, ...] = ()
        series_history: tuple[SeriesHistoryEntry, ...] = ()
        if candidate.isin and candidate.isin in prior_map:
            prior = prior_map[candidate.isin]
            if prior.symbol != candidate.symbol:
                previous_symbol = prior.symbol
                symbol_history = (
                    SymbolHistoryEntry(
                        symbol=prior.symbol,
                        effective_from=prior_generated_at,
                        effective_to=current_generated_at,
                    ),
                )
            if prior.series != candidate.series:
                series_history = (
                    SeriesHistoryEntry(
                        series=prior.series.value,
                        effective_from=prior_generated_at,
                        effective_to=current_generated_at,
                    ),
                )

        record = InstrumentMasterRecord(
            schema_version=schema_version,
            exchange="NSE",
            symbol=candidate.symbol,
            display_name=candidate.display_name,
            isin=candidate.isin,
            series=candidate.series,
            instrument_category=category,
            classification_status=classification_status,
            listing_date=candidate.listing_date,
            active_status=ActiveStatus.UNKNOWN,
            source_identifier=source_identifier,
            source_publication_date=source_publication_date,
            record_version=1,
            eligibility_status=eligibility_status,
            eligibility_reason_codes=reason_codes,
            previous_symbol=previous_symbol,
            symbol_history=symbol_history,
            series_history=series_history,
            minimum_history_band=MinimumHistoryBand.UNKNOWN,
            fundamentals_coverage_status=FundamentalsCoverageStatus.UNKNOWN,
            market_data_status=MarketDataStatus.UNKNOWN,
        )
        records.append(record)

    for rejected in parse_result.rejected:
        reason_code_counts[rejected.reason_code] = (
            reason_code_counts.get(rejected.reason_code, 0) + 1
        )

    stats = {
        "total_rows": parse_result.total_rows,
        "accepted_records": accepted_count,
        "rejected_records": len(parse_result.rejected),
        "ambiguous_records": ambiguous_count,
        "duplicate_isin_detections": duplicate_isin_count,
        "duplicate_symbol_detections": duplicate_symbol_count,
        "reason_code_counts": reason_code_counts,
        "rejected_detail": [
            {"line_number": r.line_number, "reason_code": r.reason_code, "detail": r.detail}
            for r in parse_result.rejected
        ],
    }
    return records, stats


def candidates_by_isin(parse_result: ParseResult) -> dict[str, _Candidate]:
    """Helper for building a prior-snapshot lookup map for history
    detection. Only includes candidates with a non-null ISIN, since ISIN
    is the only stable join key (per the accepted identity model)."""
    return {c.isin: c for c in parse_result.accepted_candidates if c.isin}
