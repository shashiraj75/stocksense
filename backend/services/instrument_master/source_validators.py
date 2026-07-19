"""
Offline, source-specific validators for the 11 approved NSE sources
(Phase C1-D1).

Every validator in this module accepts only in-memory text supplied by
the caller. None of them opens a network socket, reads an environment
variable, connects to a database, imports the application, fetches a
URL, or writes to any path — see test_source_registry_no_network.py.

Strict ISIN validation (ISO 6166): 2-letter country prefix, 9
alphanumeric NSIN characters, 1 numeric check digit, computed via the
Luhn-based algorithm ISO 6166 specifies (digits taken as-is, letters
converted to their two-digit position A=10..Z=35, then a standard Luhn
check-digit computation over the resulting digit string).
"""
from __future__ import annotations

import csv
import re
import unicodedata
from dataclasses import dataclass, field

from .enums import SourceId, ValidationResultStatus
from .source_registry import SourceUrlValidationError, registry_by_id, validate_exact_approved_url

# ---------------------------------------------------------------------------
# Strict ISIN validation (ISO 6166) — reused by every per-source validator
# below. Never weakens or duplicates the Phase C0 identity model in
# parser.py/schema.py; this is a stricter, standalone structural check that
# Phase C0 itself never needed (Phase C0 trusts an already-well-formed ISIN
# column and only checks non-blank/uniqueness).
# ---------------------------------------------------------------------------

_ISIN_SHAPE_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")


def _isin_luhn_check_digit(isin_without_check_digit: str) -> str:
    """Computes the ISO 6166 check digit for an 11-character ISIN body
    (2-letter country prefix + 9-character NSIN)."""
    digit_string = ""
    for ch in isin_without_check_digit:
        if ch.isdigit():
            digit_string += ch
        else:
            digit_string += str(ord(ch) - ord("A") + 10)

    # Luhn algorithm over digit_string, doubling every second digit from
    # the right, computed as-if a trailing check digit of 0 were present.
    total = 0
    digits = [int(d) for d in digit_string]
    digits.append(0)  # placeholder check digit
    parity_offset = len(digits) % 2
    for i, d in enumerate(digits[:-1]):
        if i % 2 == parity_offset:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    check_digit = (10 - (total % 10)) % 10
    return str(check_digit)


def is_strict_valid_isin(value: str | None) -> bool:
    """Returns True only for a well-formed, non-blank, non-placeholder
    ISIN with a correct ISO 6166 check digit. No silent truncation: the
    full, unmodified string must match the exact 12-character shape.

    Country/prefix policy (Phase C1-D3 documentation amendment): this
    validator verifies general ISO 6166 structure and check-digit
    correctness only — it accepts any valid 2-letter country prefix, not
    just "IN". It does NOT guarantee the ISIN is India-issued/NSE-linked.
    Every real ISIN observed anywhere in this engagement's evidence has
    in fact been IN-prefixed, but no ratified Phase C1-C1/C1-C2 decision
    requires rejecting a structurally-valid non-IN ISIN at this layer.
    If a source-specific or system-wide IN-only restriction is ever
    required, it belongs in a separate source-policy check layered on
    top of this general-purpose structural validator, not inside it."""
    if value is None:
        return False
    candidate = value.strip()
    if not candidate:
        return False
    if candidate != value:
        # Leading/trailing whitespace in the raw field is itself a
        # validation failure here — normalization happens in the caller
        # (parser/normalizer), not silently inside the strict validator.
        return False
    if not _ISIN_SHAPE_RE.match(candidate):
        return False
    body, supplied_check_digit = candidate[:11], candidate[11]
    return _isin_luhn_check_digit(body) == supplied_check_digit


# ---------------------------------------------------------------------------
# Deterministic validation-result types.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationError:
    line_number: int | None
    code: str
    detail: str


@dataclass(frozen=True)
class ValidationResult:
    """Deterministic for identical input bytes + identical validator
    contract version. Contains no runtime timestamp, no wall-clock read,
    and no machine-specific path."""

    source_id: SourceId
    status: ValidationResultStatus
    valid: bool
    record_count: int
    error_count: int
    warning_count: int
    errors: tuple[ValidationError, ...] = field(default_factory=tuple)
    warnings: tuple[ValidationError, ...] = field(default_factory=tuple)
    observed_header: tuple[str, ...] = field(default_factory=tuple)
    observed_min_field_count: int | None = None
    observed_max_field_count: int | None = None
    isin_total_rows: int = 0
    isin_nonblank_rows: int = 0
    isin_valid_rows: int = 0
    isin_duplicate_count: int = 0


def _normalize_header_cell(cell: str) -> str:
    return unicodedata.normalize("NFC", cell).strip()


def _split_csv_lines(text: str) -> list[str]:
    # Deterministic line splitting: normalize all line-ending styles to
    # '\n' before splitting, so "\r\n"-sourced fixtures/content never
    # produce different results than "\n"-sourced ones.
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n")


def _looks_like_non_csv(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("<!DOCTYPE") or stripped.startswith("<html") or stripped.startswith("<HTML")


# ---------------------------------------------------------------------------
# Narrowly-scoped, evidence-backed footnote-row recognition (Phase C1-D3
# correction of a Phase C1-D2 finding). This is deliberately NOT "no valid
# ISIN found anywhere in the row means it is a footnote" — that rule is too
# broad and would silently discard a malformed security record whose ISIN
# field was itself lost or corrupted. A row is recognized as a footnote
# ONLY when it is a single CSV field (no unescaped commas) whose text
# matches the exact documented footnote marker for that specific source,
# per the reviewed Phase C1-A3 evidence:
#   - REITS_L.csv / INVITS_L.csv: "Note: the Market lot is updated..."
#   - IDR_W9.csv: a "*"-prefixed footnote, matching the header's own
#     trailing "FACE VALUE*" asterisk annotation.
# Any row that does not match — including a genuinely truncated/malformed
# data row with the wrong field count — falls through to
# INVALID_FIELD_COUNT instead of being silently dropped.
# ---------------------------------------------------------------------------

_FOOTNOTE_SOURCE_MARKERS: dict[SourceId, str] = {
    SourceId.NSE_REIT_CURRENT: "NOTE:",
    SourceId.NSE_INVIT_CURRENT: "NOTE:",
    SourceId.NSE_IDR: "*",
}


def _is_known_footnote_row(source_id: SourceId, row: list[str]) -> bool:
    if len(row) != 1:
        # A footnote is a single free-text CSV field with no embedded,
        # unescaped commas. Any other field count is never a footnote —
        # it is either a malformed data row (fail closed, see caller) or
        # genuinely blank (filtered separately, before this is called).
        return False
    text = row[0].strip()
    if not text:
        return False
    marker = _FOOTNOTE_SOURCE_MARKERS.get(source_id)
    if marker is None:
        # No evidence-backed footnote pattern established for this
        # source — fail closed rather than guess.
        return False
    return text.upper().startswith(marker.upper()) if marker != "*" else text.startswith("*")


# ---------------------------------------------------------------------------
# Shared "current-category dedicated-source" validator: EQUITY_L / ETF /
# SME / REIT / InvIT all share the same core shape (header-present,
# fixed field count, ISIN required non-blank/unique) — implemented once
# and parameterized, per source-specific contract, rather than five
# copies of the same loop. The PREF/WARRANT trailing-ISIN rule is
# deliberately NOT folded into this shared helper (see their own
# functions below) — it remains a narrowly-scoped, source-ID-gated rule.
# ---------------------------------------------------------------------------


def _validate_dedicated_current_source(
    text: str,
    *,
    source_id: SourceId,
    expected_header: tuple[str, ...],
    isin_column: str,
    footnote_filter: bool = False,
) -> ValidationResult:
    if _looks_like_non_csv(text):
        return ValidationResult(
            source_id=source_id,
            status=ValidationResultStatus.UNEXPECTED_NON_CSV_CONTENT,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(None, "unexpected_non_csv_content", "response body looks like HTML, not CSV"),),
        )

    lines = _split_csv_lines(text)
    if not lines or not lines[0].strip():
        return ValidationResult(
            source_id=source_id,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", "empty or missing header row"),),
        )

    header = tuple(_normalize_header_cell(c) for c in next(csv.reader([lines[0]])))
    if header != expected_header:
        return ValidationResult(
            source_id=source_id,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", f"observed={header!r} expected={expected_header!r}"),),
            observed_header=header,
        )

    expected_count = len(expected_header)
    isin_idx = expected_header.index(isin_column)

    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []
    seen_isins: dict[str, int] = {}
    record_count = 0
    isin_total_rows = 0
    isin_nonblank_rows = 0
    isin_valid_rows = 0
    min_fc: int | None = None
    max_fc: int | None = None

    # Blank rows (empty or whitespace-only) may be ignored outright — they
    # carry no content of any kind, footnote or otherwise.
    data_lines = [ln for ln in lines[1:] if ln.strip() != ""]
    for line_no, raw_line in enumerate(data_lines, start=2):
        row = next(csv.reader([raw_line]))
        fc = len(row)
        min_fc = fc if min_fc is None else min(min_fc, fc)
        max_fc = fc if max_fc is None else max(max_fc, fc)

        if fc != expected_count:
            if footnote_filter and _is_known_footnote_row(source_id, row):
                # Recognized, evidence-backed footnote pattern for this
                # exact source — never treated as a security record,
                # never counted as an error.
                continue
            # Any other field-count mismatch — including a genuinely
            # malformed/truncated data row that merely resembles a
            # footnote in width — fails closed as a structural error,
            # never silently dropped.
            errors.append(
                ValidationError(
                    line_no,
                    "invalid_field_count",
                    f"observed={fc} expected={expected_count}"
                    + (" (not a recognized footnote pattern)" if footnote_filter else ""),
                )
            )
            continue

        isin_total_rows += 1
        isin_raw = row[isin_idx].strip()
        if not isin_raw:
            errors.append(ValidationError(line_no, "missing_required_field", f"blank {isin_column}"))
            continue
        isin_nonblank_rows += 1
        if not is_strict_valid_isin(isin_raw):
            errors.append(ValidationError(line_no, "invalid_isin", f"{isin_column}={isin_raw!r}"))
            continue
        isin_valid_rows += 1
        seen_isins[isin_raw] = seen_isins.get(isin_raw, 0) + 1
        record_count += 1

    dup_isins = {k: v for k, v in seen_isins.items() if v > 1}
    if dup_isins:
        for isin_val, count in sorted(dup_isins.items()):
            errors.append(ValidationError(None, "duplicate_isin", f"{isin_val} appears {count} times"))
        record_count -= sum(dup_isins.values())  # duplicated rows excluded from the accepted count

    if record_count == 0 and not errors:
        # The "header-only == valid" exception is exclusive to
        # eq_ilseclist.csv (see validate_il_series(), which does not call
        # this shared helper at all). Every other dedicated-current-source
        # contract that reaches this shared helper requires at least one
        # data record — a zero-record body is a validation failure here,
        # never a silently-accepted VALID_EMPTY, per the C1-D1 requirement
        # that this exception must not leak to any other source.
        status = ValidationResultStatus.MISSING_REQUIRED_FIELD
        valid = False
        errors.append(ValidationError(None, "missing_required_field", "zero data records observed; this source does not permit a valid-empty result"))
    elif errors:
        # Phase C1-D5 cleanup (non-blocking finding from Phase C1-D4):
        # the reported status must reflect the actual dominant error
        # code, not default to INVALID_ISIN whenever anything other than
        # a pure duplicate-ISIN error is present — a wrong-width row
        # (footnote-rejection or otherwise) previously reported
        # status=INVALID_ISIN despite its error code being
        # invalid_field_count. Deterministic priority order: field-count
        # errors (including unrecognized footnote-shaped rows) first,
        # then missing-field, then invalid-ISIN, then pure duplicate-ISIN.
        error_codes = {e.code for e in errors}
        if "invalid_field_count" in error_codes:
            status = ValidationResultStatus.INVALID_FIELD_COUNT
        elif "missing_required_field" in error_codes:
            status = ValidationResultStatus.MISSING_REQUIRED_FIELD
        elif "invalid_isin" in error_codes:
            status = ValidationResultStatus.INVALID_ISIN
        else:
            status = ValidationResultStatus.DUPLICATE_ISIN
        valid = False
    else:
        status = ValidationResultStatus.VALID
        valid = True

    return ValidationResult(
        source_id=source_id,
        status=status,
        valid=valid,
        record_count=record_count,
        error_count=len(errors),
        warning_count=len(warnings),
        errors=tuple(errors),
        warnings=tuple(warnings),
        observed_header=header,
        observed_min_field_count=min_fc,
        observed_max_field_count=max_fc,
        isin_total_rows=isin_total_rows,
        isin_nonblank_rows=isin_nonblank_rows,
        isin_valid_rows=isin_valid_rows,
        isin_duplicate_count=sum(dup_isins.values()),
    )


def validate_equity_current(text: str) -> ValidationResult:
    """EQUITY_L.csv contract: ISIN required. EQ/BE/BZ (and every other
    observed SERIES value) are trading/settlement series only — this
    validator makes no taxonomy claim from series or from membership in
    this file alone; it validates structure only, never classification.
    Paid-up value / face value are likewise never inspected here for any
    taxonomy signal — they carry none in this contract."""
    return _validate_dedicated_current_source(
        text,
        source_id=SourceId.NSE_EQUITY_CURRENT,
        expected_header=(
            "SYMBOL",
            "NAME OF COMPANY",
            "SERIES",
            "DATE OF LISTING",
            "PAID UP VALUE",
            "MARKET LOT",
            "ISIN NUMBER",
            "FACE VALUE",
        ),
        isin_column="ISIN NUMBER",
    )


def validate_etf(text: str) -> ValidationResult:
    """eq_etfseclist.csv: membership in this dedicated file is what
    supports ETF classification (never a name/ticker heuristic) —
    that classification decision itself belongs to a future classifier,
    not this validator, which only confirms the file's structural
    validity and ISIN integrity."""
    return _validate_dedicated_current_source(
        text,
        source_id=SourceId.NSE_ETF_CURRENT,
        expected_header=("Symbol", "Underlying", "SecurityName", "DateofListing", "MarketLot", "ISINNumber", "FaceValue"),
        isin_column="ISINNumber",
    )


def validate_sme(text: str, *, source_url: str | None = None) -> ValidationResult:
    """SME_EQUITY_L.csv: membership supports SME classification. `series`
    (SM/ST/SZ observed) is validated as data only, never as identity.
    If `source_url` is supplied, this validator requires it to be an
    EXACT, byte-for-byte match for the registered nse_sme_current URL
    (https://nsearchives.nseindia.com/emerge/corporates/content/SME_EQUITY_L.csv)
    — not a substring/containment check. This rejects: a different host
    that merely contains the correct path; the correct path appearing
    inside a query parameter; the legacy /content/equities path; a
    trailing slash, fragment, or query string; any percent-encoded path
    variation; and any filename suffix/prefix addition. Per the Phase
    C1-D3 correction of the Phase C1-D2 finding that the prior substring
    check let an attacker-controlled host pass merely by containing the
    right path fragment."""
    if source_url is not None:
        expected_url = registry_by_id(SourceId.NSE_SME_CURRENT).exact_url
        try:
            validate_exact_approved_url(source_url)
        except SourceUrlValidationError:
            return ValidationResult(
                source_id=SourceId.NSE_SME_CURRENT,
                status=ValidationResultStatus.INVALID_TRANSPORT_METADATA,
                valid=False,
                record_count=0,
                error_count=1,
                warning_count=0,
                errors=(
                    ValidationError(
                        None,
                        "invalid_transport_metadata",
                        f"source_url is not an approved registry URL: {source_url!r}",
                    ),
                ),
            )
        if source_url != expected_url:
            return ValidationResult(
                source_id=SourceId.NSE_SME_CURRENT,
                status=ValidationResultStatus.INVALID_TRANSPORT_METADATA,
                valid=False,
                record_count=0,
                error_count=1,
                warning_count=0,
                errors=(
                    ValidationError(
                        None,
                        "invalid_transport_metadata",
                        f"source_url does not exactly match the registered SME URL: "
                        f"got={source_url!r} expected={expected_url!r}",
                    ),
                ),
            )
    return _validate_dedicated_current_source(
        text,
        source_id=SourceId.NSE_SME_CURRENT,
        expected_header=("SYMBOL", "NAME_OF_COMPANY", "SERIES", "DATE_OF_LISTING", "PAID_UP_VALUE", "ISIN_NUMBER", "FACE_VALUE"),
        isin_column="ISIN_NUMBER",
    )


def validate_reit(text: str) -> ValidationResult:
    """REITS_L.csv: known trailing footnote row present in the real
    source — filtered by field-count mismatch (never by blank-field
    detection, which was the precedent Phase C1-A3 bug: a footnote row's
    first field is non-empty text)."""
    return _validate_dedicated_current_source(
        text,
        source_id=SourceId.NSE_REIT_CURRENT,
        expected_header=(
            "SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING",
            "PAID UP VALUE", "MARKET LOT", "ISIN NUMBER", "FACE VALUE",
        ),
        isin_column="ISIN NUMBER",
        footnote_filter=True,
    )


def validate_invit(text: str) -> ValidationResult:
    """INVITS_L.csv: same contract shape and footnote handling as REIT."""
    return _validate_dedicated_current_source(
        text,
        source_id=SourceId.NSE_INVIT_CURRENT,
        expected_header=(
            "SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING",
            "PAID UP VALUE", "MARKET LOT", "ISIN NUMBER", "FACE VALUE",
        ),
        isin_column="ISIN NUMBER",
        footnote_filter=True,
    )


# ---------------------------------------------------------------------------
# PREF.csv / WARRANT.csv — the narrowly-scoped trailing-undeclared-ISIN
# contract. Deliberately NOT implemented as a shared "extra column"
# helper reused by any other source — each function below is its own
# explicit, source-ID-specific contract, per the Phase C1-C2 ratification.
# ---------------------------------------------------------------------------


def _validate_trailing_isin_source(
    text: str,
    *,
    source_id: SourceId,
    declared_header: tuple[str, ...],
) -> ValidationResult:
    if _looks_like_non_csv(text):
        return ValidationResult(
            source_id=source_id,
            status=ValidationResultStatus.UNEXPECTED_NON_CSV_CONTENT,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(None, "unexpected_non_csv_content", "response body looks like HTML, not CSV"),),
        )

    lines = _split_csv_lines(text)
    if not lines or not lines[0].strip():
        return ValidationResult(
            source_id=source_id,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", "empty or missing header row"),),
        )

    header = tuple(_normalize_header_cell(c) for c in next(csv.reader([lines[0]])))
    if header != declared_header:
        return ValidationResult(
            source_id=source_id,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", f"observed={header!r} expected={declared_header!r}"),),
            observed_header=header,
        )

    declared_count = len(declared_header)
    required_row_width = declared_count + 1  # the one, and only one, tolerated extra field

    errors: list[ValidationError] = []
    seen_isins: dict[str, int] = {}
    record_count = 0
    isin_total_rows = 0
    isin_nonblank_rows = 0
    isin_valid_rows = 0
    min_fc: int | None = None
    max_fc: int | None = None

    data_lines = [ln for ln in lines[1:] if ln != ""]
    for line_no, raw_line in enumerate(data_lines, start=2):
        row = next(csv.reader([raw_line]))
        fc = len(row)
        min_fc = fc if min_fc is None else min(min_fc, fc)
        max_fc = fc if max_fc is None else max(max_fc, fc)

        # Accept only when field count is EXACTLY declared_count + 1 —
        # any other width (declared_count itself, or anything else)
        # fails closed for that row. No generic "extra column" tolerance.
        if fc != required_row_width:
            errors.append(
                ValidationError(
                    line_no,
                    "invalid_field_count",
                    f"observed={fc} required={required_row_width} (declared_header_count + 1)",
                )
            )
            continue

        isin_total_rows += 1
        trailing_isin = row[-1].strip()
        if not trailing_isin:
            errors.append(ValidationError(line_no, "missing_required_field", "blank trailing ISIN field"))
            continue
        isin_nonblank_rows += 1
        if not is_strict_valid_isin(trailing_isin):
            errors.append(ValidationError(line_no, "invalid_isin", f"trailing_field={trailing_isin!r}"))
            continue
        isin_valid_rows += 1
        seen_isins[trailing_isin] = seen_isins.get(trailing_isin, 0) + 1
        record_count += 1

    dup_isins = {k: v for k, v in seen_isins.items() if v > 1}
    if dup_isins:
        for isin_val, count in sorted(dup_isins.items()):
            errors.append(ValidationError(None, "duplicate_isin", f"{isin_val} appears {count} times"))
        record_count -= sum(dup_isins.values())

    if record_count == 0 and not errors:
        status, valid = ValidationResultStatus.VALID_EMPTY, True
    elif errors:
        status, valid = ValidationResultStatus.INVALID_FIELD_COUNT, False
    else:
        status, valid = ValidationResultStatus.VALID, True

    return ValidationResult(
        source_id=source_id,
        status=status,
        valid=valid,
        record_count=record_count,
        error_count=len(errors),
        warning_count=0,
        errors=tuple(errors),
        observed_header=header,
        observed_min_field_count=min_fc,
        observed_max_field_count=max_fc,
        isin_total_rows=isin_total_rows,
        isin_nonblank_rows=isin_nonblank_rows,
        isin_valid_rows=isin_valid_rows,
        isin_duplicate_count=sum(dup_isins.values()),
    )


def validate_preference(text: str) -> ValidationResult:
    """PREF.csv: confirmed real source defect — declared header has one
    fewer field than every valid data row; the trailing undeclared field
    is the ISIN. Accepted only when: source is nse_preference (this
    function is only ever called for that source); declared header
    matches exactly; every accepted row has exactly header_count + 1
    fields; the trailing field passes strict ISIN validation; all
    preceding fields are simply carried through unvalidated beyond
    field-count (per the ratified contract, no further semantic
    validation of the 14 declared columns is required in this phase).
    Grain (possibly per-redemption-tranche) is NOT resolved here — that
    is a classification-engine concern, out of scope for C1-D1."""
    return _validate_trailing_isin_source(
        text,
        source_id=SourceId.NSE_PREFERENCE,
        declared_header=(
            "SYMBOL", "NAME OF COMPANY", "SERIES", "SECURITY NAME", "FACE VALUE",
            "PAID UP VALUE", "MKT LOT", "DIVIDEND %", "DATE OF LISTING",
            "DATE OF ALLOTMENT", "REDEMPTION DATE", "REDEMPTION AMT",
            "CONVERSION DATE", "CONVERSION AMT",
        ),
    )


def validate_warrant(text: str) -> ValidationResult:
    """WARRANT.csv: same trailing-ISIN defect pattern as PREF.csv,
    applied as its own fully independent contract (declared header of 5,
    required row width of 6) — not derived from or shared with
    validate_preference() beyond the common private helper, which itself
    is parameterized purely by declared_header, not by any implicit
    "any CSV with N+1 fields" generic rule."""
    return _validate_trailing_isin_source(
        text,
        source_id=SourceId.NSE_WARRANT,
        declared_header=("SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING", "MARKET LOT"),
    )


def validate_idr(text: str) -> ValidationResult:
    """IDR_W9.csv: same footnote/blank-row-tolerant contract as
    REIT/InvIT — footnote text must never become an instrument record."""
    return _validate_dedicated_current_source(
        text,
        source_id=SourceId.NSE_IDR,
        expected_header=(
            "SYMBOL", "NAME OF COMPANY", "SERIES", "DATE OF LISTING",
            "PAID UP VALUE", "MARKET LOT", "ISIN NUMBER", "FACE VALUE*",
        ),
        isin_column="ISIN NUMBER",
        footnote_filter=True,
    )


# The exact, reviewed valid-empty baseline for eq_ilseclist.csv (Phase
# C1-D3 correction of a Phase C1-D2 finding): 20 raw bytes, CRLF line
# ending, no BOM, no trailing content of any kind beyond the header row
# itself. A VALID_EMPTY result requires the RAW bytes to match this
# constant exactly, checked before any line-ending normalization or
# text decoding artifact could hide a difference.
IL_SERIES_VALID_EMPTY_RAW_BYTES = b"Symbol,Se,Sec Name\r\n"


def validate_il_series(text: str | bytes) -> ValidationResult:
    """eq_ilseclist.csv: header-only is a VALID_EMPTY result only when
    the RAW byte representation is an exact match for
    IL_SERIES_VALID_EMPTY_RAW_BYTES (20 bytes, CRLF, no BOM, no extra
    content) — not merely a "header looks right after normalization"
    heuristic. Any departure from that exact baseline (LF-only line
    ending, no trailing newline, a UTF-8 BOM, an extra blank/whitespace
    row, a partial data row) fails closed rather than being silently
    accepted as empty. This "header-only == valid" rule is exclusive to
    this validator — no other validator in this module treats an empty
    body as anything but a structural failure."""
    expected_header = ("Symbol", "Se", "Sec Name")

    if isinstance(text, bytes):
        raw_bytes = text
        text_for_parsing = text.decode("utf-8", errors="replace")
    else:
        text_for_parsing = text
        # Encode as UTF-8 without touching line endings first, so the
        # raw byte count reflects exactly what was supplied.
        raw_bytes = text.encode("utf-8")

    if _looks_like_non_csv(text_for_parsing):
        return ValidationResult(
            source_id=SourceId.NSE_IL_SERIES,
            status=ValidationResultStatus.UNEXPECTED_NON_CSV_CONTENT,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(None, "unexpected_non_csv_content", "response body looks like HTML, not CSV"),),
        )

    lines = _split_csv_lines(text_for_parsing)
    if not lines or not lines[0].strip():
        return ValidationResult(
            source_id=SourceId.NSE_IL_SERIES,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", "empty or missing header row"),),
        )

    header = tuple(_normalize_header_cell(c) for c in next(csv.reader([lines[0]])))
    if header != expected_header:
        return ValidationResult(
            source_id=SourceId.NSE_IL_SERIES,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", f"observed={header!r} expected={expected_header!r}"),),
            observed_header=header,
        )

    data_lines = [ln for ln in lines[1:] if ln != ""]
    if not data_lines:
        if raw_bytes == IL_SERIES_VALID_EMPTY_RAW_BYTES:
            # Exact reviewed 20-byte CRLF baseline, structurally
            # complete, zero data rows.
            return ValidationResult(
                source_id=SourceId.NSE_IL_SERIES,
                status=ValidationResultStatus.VALID_EMPTY,
                valid=True,
                record_count=0,
                error_count=0,
                warning_count=0,
                observed_header=header,
                observed_min_field_count=None,
                observed_max_field_count=None,
            )
        # Header text parses as correct, but the raw bytes deviate from
        # the exact reviewed baseline (wrong line-ending convention, a
        # UTF-8 BOM, a missing/extra trailing newline, or an otherwise
        # byte-different "empty" body) — fail closed, never silently
        # accepted as VALID_EMPTY.
        return ValidationResult(
            source_id=SourceId.NSE_IL_SERIES,
            status=ValidationResultStatus.INVALID_TRANSPORT_METADATA,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(
                ValidationError(
                    None,
                    "invalid_transport_metadata",
                    f"raw body does not exactly match the reviewed valid-empty baseline "
                    f"({len(raw_bytes)} bytes observed, {len(IL_SERIES_VALID_EMPTY_RAW_BYTES)} bytes expected)",
                ),
            ),
            observed_header=header,
        )

    # Non-empty: validate exactly like any other dedicated current source
    # (no ISIN column exists in this file's contract to date, so no
    # ISIN-specific checks apply — only field-count/header integrity).
    errors: list[ValidationError] = []
    min_fc: int | None = None
    max_fc: int | None = None
    record_count = 0
    for line_no, raw_line in enumerate(data_lines, start=2):
        row = next(csv.reader([raw_line]))
        fc = len(row)
        min_fc = fc if min_fc is None else min(min_fc, fc)
        max_fc = fc if max_fc is None else max(max_fc, fc)
        if fc != len(expected_header):
            errors.append(ValidationError(line_no, "invalid_field_count", f"observed={fc} expected={len(expected_header)}"))
            continue
        record_count += 1

    status = ValidationResultStatus.VALID if not errors else ValidationResultStatus.INVALID_FIELD_COUNT
    return ValidationResult(
        source_id=SourceId.NSE_IL_SERIES,
        status=status,
        valid=not errors,
        record_count=record_count,
        error_count=len(errors),
        warning_count=0,
        errors=tuple(errors),
        observed_header=header,
        observed_min_field_count=min_fc,
        observed_max_field_count=max_fc,
    )


_DATE_RE = re.compile(r"^\d{2}-[A-Z]{3}-\d{4}$")


def validate_symbol_history(text: str) -> ValidationResult:
    """symbolchange.csv: headerless, exactly 4 fields per record
    (company_name, old_symbol, new_symbol, date), no ISIN. Lower-
    confidence history evidence only — this validator never produces or
    implies an automatic identity linkage; that remains a classification/
    consumer-layer decision explicitly out of scope here."""
    if _looks_like_non_csv(text):
        return ValidationResult(
            source_id=SourceId.NSE_SYMBOL_HISTORY,
            status=ValidationResultStatus.UNEXPECTED_NON_CSV_CONTENT,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(None, "unexpected_non_csv_content", "response body looks like HTML, not CSV"),),
        )

    lines = [ln for ln in _split_csv_lines(text) if ln != ""]
    errors: list[ValidationError] = []
    min_fc: int | None = None
    max_fc: int | None = None
    record_count = 0

    for line_no, raw_line in enumerate(lines, start=1):
        row = next(csv.reader([raw_line]))
        fc = len(row)
        min_fc = fc if min_fc is None else min(min_fc, fc)
        max_fc = fc if max_fc is None else max(max_fc, fc)
        if fc != 4:
            errors.append(ValidationError(line_no, "invalid_field_count", f"observed={fc} expected=4"))
            continue
        date_field = row[3].strip().upper()
        if not _DATE_RE.match(date_field):
            errors.append(ValidationError(line_no, "malformed_date", f"date={row[3]!r}"))
            continue
        record_count += 1

    status = ValidationResultStatus.VALID if not errors else ValidationResultStatus.MALFORMED_DATE
    return ValidationResult(
        source_id=SourceId.NSE_SYMBOL_HISTORY,
        status=status if lines else ValidationResultStatus.INVALID_HEADER,
        valid=not errors and bool(lines),
        record_count=record_count,
        error_count=len(errors) + (0 if lines else 1),
        warning_count=0,
        errors=tuple(errors) if lines else (ValidationError(None, "invalid_header", "empty file: headerless source with zero rows"),),
        observed_min_field_count=min_fc,
        observed_max_field_count=max_fc,
    )


def validate_name_history(text: str) -> ValidationResult:
    """namechange.csv: exact declared header, deterministic exact-row
    deduplication (order-independent), no ISIN. Symbol-only linkage is
    enrichment evidence only. Conflicting duplicate rows (rows sharing a
    symbol/date but differing content) remain visible as warnings, never
    silently collapsed alongside true exact duplicates."""
    expected_header = ("NCH_SYMBOL", "NCH_PREV_NAME", "NCH_NEW_NAME", "NCH_DT")

    if _looks_like_non_csv(text):
        return ValidationResult(
            source_id=SourceId.NSE_NAME_HISTORY,
            status=ValidationResultStatus.UNEXPECTED_NON_CSV_CONTENT,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(None, "unexpected_non_csv_content", "response body looks like HTML, not CSV"),),
        )

    lines = _split_csv_lines(text)
    if not lines or not lines[0].strip():
        return ValidationResult(
            source_id=SourceId.NSE_NAME_HISTORY,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", "empty or missing header row"),),
        )

    header = tuple(_normalize_header_cell(c) for c in next(csv.reader([lines[0]])))
    if header != expected_header:
        return ValidationResult(
            source_id=SourceId.NSE_NAME_HISTORY,
            status=ValidationResultStatus.INVALID_HEADER,
            valid=False,
            record_count=0,
            error_count=1,
            warning_count=0,
            errors=(ValidationError(1, "invalid_header", f"observed={header!r} expected={expected_header!r}"),),
            observed_header=header,
        )

    data_lines = [ln for ln in lines[1:] if ln != ""]
    errors: list[ValidationError] = []
    warnings: list[ValidationError] = []
    min_fc: int | None = None
    max_fc: int | None = None

    # Deterministic exact-row dedup: normalize each row to a tuple key,
    # count occurrences. Order-independent — same input, any row order,
    # produces the same deduplicated set and the same duplicate count.
    normalized_rows: list[tuple[str, ...]] = []
    for line_no, raw_line in enumerate(data_lines, start=2):
        row = next(csv.reader([raw_line]))
        fc = len(row)
        min_fc = fc if min_fc is None else min(min_fc, fc)
        max_fc = fc if max_fc is None else max(max_fc, fc)
        if fc != len(expected_header):
            errors.append(ValidationError(line_no, "invalid_field_count", f"observed={fc} expected={len(expected_header)}"))
            continue
        normalized_rows.append(tuple(unicodedata.normalize("NFC", c.strip()) for c in row))

    row_counts: dict[tuple[str, ...], int] = {}
    for r in normalized_rows:
        row_counts[r] = row_counts.get(r, 0) + 1

    # Exact duplicates: byte-for-byte identical rows (same NCH_SYMBOL,
    # NCH_PREV_NAME, NCH_NEW_NAME, NCH_DT). Deterministically collapsed,
    # warned with a code distinct from ConflictState.DUPLICATE_SOURCE_RECORD
    # (a different, unrelated enum) to avoid the naming collision flagged
    # in the Phase C1-D2 audit.
    exact_dup_count = sum(c - 1 for c in row_counts.values() if c > 1)
    if exact_dup_count:
        warnings.append(
            ValidationError(None, "exact_duplicate_history_row", f"{exact_dup_count} exact-duplicate rows collapsed deterministically")
        )

    # Conflicting rows: same logical key (NCH_SYMBOL + NCH_DT) but
    # different NCH_PREV_NAME and/or NCH_NEW_NAME. These are never
    # merged or silently collapsed — both/all variants are retained in
    # the deduplicated set, and a distinct, deterministic warning is
    # emitted per conflicting key so the conflict remains visible.
    rows_by_key: dict[tuple[str, str], set[tuple[str, ...]]] = {}
    for r in row_counts:
        key = (r[0], r[3])  # (NCH_SYMBOL, NCH_DT)
        rows_by_key.setdefault(key, set()).add(r)
    conflicting_keys = sorted(k for k, variants in rows_by_key.items() if len(variants) > 1)
    for symbol, date in conflicting_keys:
        variant_count = len(rows_by_key[(symbol, date)])
        warnings.append(
            ValidationError(
                None,
                "conflicting_history_rows",
                f"NCH_SYMBOL={symbol!r} NCH_DT={date!r} has {variant_count} conflicting variants "
                "(differing NCH_PREV_NAME/NCH_NEW_NAME); all retained, none merged",
            )
        )

    deduplicated_record_count = len(row_counts)
    status = ValidationResultStatus.VALID if not errors else ValidationResultStatus.INVALID_FIELD_COUNT
    return ValidationResult(
        source_id=SourceId.NSE_NAME_HISTORY,
        status=status,
        valid=not errors,
        record_count=deduplicated_record_count,
        error_count=len(errors),
        warning_count=len(warnings),
        errors=tuple(errors),
        warnings=tuple(warnings),
        observed_header=header,
        observed_min_field_count=min_fc,
        observed_max_field_count=max_fc,
    )
