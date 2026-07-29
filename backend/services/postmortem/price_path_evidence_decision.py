"""
Historical price-path EVIDENCE decision governance — Trade Postmortem
Sprint 3A, Stage J1B.

This is the second of the two-stage historical decision model. Stage
J1A (price_path_eligibility.evaluate_eligibility) decides ONLY whether
a trade's own persisted context permits attempting acquisition/replay/
calculation at all. This module decides what actually happened once
that attempt was made — after a persisted-evidence compatibility
lookup, or after a provider response has been validated. J1A never
claims source availability, evidence compatibility, adjustment-basis
compatibility, or bar completeness; those facts belong here, and only
here.

Both stages are pure — no I/O, no database access, no provider calls.
Callers gather the facts (a compatible-evidence lookup result, an
acquired PricePathEvidenceBundle's own fields, a caught
PriceProviderAcquisitionError's code) and pass them in; this module
only classifies.
"""

from dataclasses import dataclass, field

COMPATIBLE_REPLAY = "COMPATIBLE_REPLAY"
ACQUISITION_REQUIRED = "ACQUISITION_REQUIRED"
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
SOURCE_INVALID = "SOURCE_INVALID"
PRICE_BASIS_INCOMPATIBLE = "PRICE_BASIS_INCOMPATIBLE"
PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"
AMBIGUOUS_RESOLUTION = "AMBIGUOUS_RESOLUTION"
CALCULATION_ELIGIBLE = "CALCULATION_ELIGIBLE"
CALCULATION_UNAVAILABLE = "CALCULATION_UNAVAILABLE"

_INSUFFICIENT_EVIDENCE_FALLBACK = "Insufficient evidence to determine this factor reliably."

# Provider error codes (price_path_acquisition.PriceProviderAcquisitionError.code)
# this module already knows how to classify as SOURCE_INVALID (malformed/
# unexpected provider response shape) rather than the generic
# SOURCE_UNAVAILABLE (no data at all, or a transient/network failure).
# PROVIDER_FETCH_FAILED and PROVIDER_RESPONSE_TOO_LARGE are transient/
# volume conditions, not evidence the provider's data was wrong, so they
# classify as SOURCE_UNAVAILABLE.
_SOURCE_INVALID_PROVIDER_CODES = frozenset({"PROVIDER_UNEXPECTED_COLUMN_SHAPE"})


@dataclass(frozen=True)
class HistoricalEvidenceDecision:
    evidence_status: str
    calculation_status: str  # CALCULATION_ELIGIBLE | CALCULATION_UNAVAILABLE
    provider_call_expected: bool
    report_completeness_ceiling: str  # "COMPLETE" | "LIMITED_EVIDENCE"
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)


def classify_replay_or_acquisition(*, compatible_evidence_found: bool) -> HistoricalEvidenceDecision:
    """Called BEFORE any provider access is attempted, immediately after
    a persisted-evidence compatibility lookup (Stage J5). Compatible
    evidence means deterministic replay: no provider call, ever — a
    provider outage must never prevent this path from succeeding."""
    if compatible_evidence_found:
        return HistoricalEvidenceDecision(
            evidence_status=COMPATIBLE_REPLAY, calculation_status=CALCULATION_ELIGIBLE,
            provider_call_expected=False, report_completeness_ceiling="COMPLETE",
        )
    return HistoricalEvidenceDecision(
        evidence_status=ACQUISITION_REQUIRED, calculation_status=CALCULATION_UNAVAILABLE,
        provider_call_expected=True, report_completeness_ceiling="COMPLETE",
    )


def classify_provider_failure(*, error_code: str) -> HistoricalEvidenceDecision:
    """Called from the caught PriceProviderAcquisitionError branch. A
    transient/no-data provider outcome is NEVER treated as proof the
    trade or symbol was invalid — it is a distinct, named, non-terminal
    evidence state; the caller's own outbox lifecycle (Stage J8) is what
    decides retryable-vs-terminal, this function only names WHAT
    happened to the evidence."""
    status = SOURCE_INVALID if error_code in _SOURCE_INVALID_PROVIDER_CODES else SOURCE_UNAVAILABLE
    return HistoricalEvidenceDecision(
        evidence_status=status, calculation_status=CALCULATION_UNAVAILABLE,
        provider_call_expected=True, report_completeness_ceiling="LIMITED_EVIDENCE",
        reason_codes=(status,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
    )


def classify_acquired_evidence(
    *, bars_observed: int, data_completeness: str, adjustment_basis_known: bool, ambiguous_resolution: bool,
) -> HistoricalEvidenceDecision:
    """Called after a provider response was successfully validated into
    a PricePathEvidenceBundle (bars_observed=0 is a valid, honest
    outcome here — build_price_path_evidence already returns a
    STATUS_UNAVAILABLE bundle rather than raising for an empty
    response, so 'the provider ran but found nothing' is represented as
    a bundle, not an exception)."""
    if bars_observed == 0:
        return HistoricalEvidenceDecision(
            evidence_status=SOURCE_UNAVAILABLE, calculation_status=CALCULATION_UNAVAILABLE,
            provider_call_expected=True, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(SOURCE_UNAVAILABLE,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
        )
    if not adjustment_basis_known:
        return HistoricalEvidenceDecision(
            evidence_status=PRICE_BASIS_INCOMPATIBLE, calculation_status=CALCULATION_UNAVAILABLE,
            provider_call_expected=True, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(PRICE_BASIS_INCOMPATIBLE,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
        )
    if ambiguous_resolution:
        return HistoricalEvidenceDecision(
            evidence_status=AMBIGUOUS_RESOLUTION, calculation_status=CALCULATION_ELIGIBLE,
            provider_call_expected=True, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(AMBIGUOUS_RESOLUTION,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
        )
    if data_completeness == "PARTIAL":
        return HistoricalEvidenceDecision(
            evidence_status=PARTIAL_EVIDENCE, calculation_status=CALCULATION_ELIGIBLE,
            provider_call_expected=True, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(PARTIAL_EVIDENCE,),
        )
    return HistoricalEvidenceDecision(
        evidence_status=CALCULATION_ELIGIBLE, calculation_status=CALCULATION_ELIGIBLE,
        provider_call_expected=True, report_completeness_ceiling="COMPLETE",
    )


def compute_report_completeness_ceiling(*ceilings: str) -> str:
    """Stage J3 — the final report status must never exceed the WEAKEST
    applicable ceiling. Every caller (trade-context ceiling from J1A,
    evidence ceiling from J1B, any downstream basis/ambiguity/level-
    history ceiling) passes its own ceiling in; this is the single
    place \"minimum of all applicable ceilings\" is computed, so no
    caller can accidentally let a locally-COMPLETE value override an
    upstream LIMITED_EVIDENCE one."""
    return "LIMITED_EVIDENCE" if "LIMITED_EVIDENCE" in ceilings else "COMPLETE"
