"""
Historical price-path EVIDENCE decision governance — Trade Postmortem
Sprint 3A, Stage J1B.

Second of the two-stage historical decision model. Stage J1A
(price_path_eligibility.evaluate_eligibility) decides ONLY whether a
trade's own persisted context permits attempting acquisition/replay/
calculation at all.

Stage J1B is split into two DELIBERATELY SEPARATE decisions — a Stage
J1B-Fail-Closed-Hardening correction. The prior single
HistoricalEvidenceDecision conflated "how was this evidence obtained"
with "is this evidence usable," which meant a genuine evidence-replay
attempt (zero provider calls) and a freshly-acquired-but-then-found-
UNAVAILABLE attempt (one provider call, which returned nothing usable)
were indistinguishable in the persisted provenance — a real false-
provenance defect (a replayed report could read provider_call_expected
=True even though no provider was ever touched for it).

  1. classify_replay_or_acquisition -> HistoricalEvidenceAcquisitionDecision
     WHERE the evidence came from (compatible replay vs fresh
     acquisition). This is a fact about ACQUISITION, fixed the moment a
     compatible-evidence lookup returns — nothing that happens
     afterward (how good the evidence turns out to be) may ever
     overwrite it.

  2. classify_acquired_evidence -> HistoricalEvidenceQualityDecision
     WHETHER the (replayed or freshly acquired) evidence is usable, and
     for what. Never claims acquisition provenance.

A third function, get_provider_failure_policy -> ProviderFailurePolicy,
is the SOLE authority for a caught PriceProviderAcquisitionError (Stage
J1B-Assurance-Closure correction — an earlier classify_provider_failure
function duplicated this same error-code-to-evidence-status mapping but
was never actually called by the live path; removed rather than left as
a second, unconsulted classification table).

All three are pure — no I/O, no database access, no provider calls.
Callers gather the facts (a compatible-evidence lookup result, an
acquired PricePathEvidenceBundle's own fields, a caught
PriceProviderAcquisitionError's code) and pass them in; this module
only classifies.
"""

from dataclasses import dataclass, field

# --- Acquisition decision (WHERE evidence came from) -----------------
COMPATIBLE_REPLAY = "COMPATIBLE_REPLAY"
ACQUISITION_REQUIRED = "ACQUISITION_REQUIRED"

# --- Evidence-quality decision (WHETHER it is usable) -----------------
SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
SOURCE_INVALID = "SOURCE_INVALID"
PARTIAL_EVIDENCE = "PARTIAL_EVIDENCE"
AMBIGUOUS_RESOLUTION = "AMBIGUOUS_RESOLUTION"
CALCULATION_ELIGIBLE = "CALCULATION_ELIGIBLE"
CALCULATION_UNAVAILABLE = "CALCULATION_UNAVAILABLE"
# UNSUPPORTED_EVIDENCE_COMPLETENESS — the fail-closed destination for
# any data_completeness value this module does not recognize (null,
# empty, malformed, or a genuinely new future value not yet mapped
# here). Never silently treated as COMPLETE.
UNSUPPORTED_EVIDENCE_COMPLETENESS = "UNSUPPORTED_EVIDENCE_COMPLETENESS"

# PRICE_BASIS_INCOMPATIBLE — Stage J1B-Fail-Closed-Hardening, Stage 6:
# removed from the active runtime enum. The live acquisition layer
# (price_path_acquisition.build_price_path_evidence) has no field that
# truthfully distinguishes "bars available but basis unknown/
# incompatible" from its own real AMBIGUOUS_RESOLUTION trigger (a split
# inside the window zeroes bars_observed AND sets data_completeness to
# AMBIGUOUS_RESOLUTION — there is no separate "basis known but
# incompatible with bars present" bundle state to classify against).
# Inventing a boolean to synthesize this distinction was exactly the
# fabrication Stage J1B-Fail-Closed-Hardening was opened to eliminate.
# Retained ONLY as a documented future-scope constant, name preserved
# so a future acquisition-layer signal (e.g. a persisted, provider-
# derived basis-compatibility field) can be wired in without a rename;
# it is not returned by any function in this module today, and no
# caller references it as a live branch target.
PRICE_BASIS_INCOMPATIBLE_FUTURE_SCOPE = "PRICE_BASIS_INCOMPATIBLE"

_INSUFFICIENT_EVIDENCE_FALLBACK = "Insufficient evidence to determine this factor reliably."

# data_completeness (price_path_acquisition's own real, already-
# computed enum) -> this module's evidence_status. Anything NOT a key
# in this dict — None, "", a typo, a genuinely new future value — falls
# through to the explicit fail-closed branch in classify_acquired_
# evidence, never silently to COMPLETE/CALCULATION_ELIGIBLE.
_DATA_COMPLETENESS_TO_EVIDENCE_STATUS = {
    "UNAVAILABLE": SOURCE_UNAVAILABLE,
    "INVALID_SOURCE_DATA": SOURCE_INVALID,
    "AMBIGUOUS_RESOLUTION": AMBIGUOUS_RESOLUTION,
    "PARTIAL": PARTIAL_EVIDENCE,
    "COMPLETE": CALCULATION_ELIGIBLE,
}


@dataclass(frozen=True)
class HistoricalEvidenceAcquisitionDecision:
    """WHERE the evidence came from — fixed at the moment a compatible-
    evidence lookup returns; never revised by anything discovered about
    evidence quality afterward."""
    acquisition_status: str  # COMPATIBLE_REPLAY | ACQUISITION_REQUIRED
    provider_call_expected: bool
    compatible_evidence_found: bool
    evidence_id: int | None = None
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HistoricalEvidenceQualityDecision:
    """WHETHER the evidence is usable, and for what. Never claims
    acquisition provenance — provider_call_expected lives exclusively
    on HistoricalEvidenceAcquisitionDecision."""
    evidence_status: str
    calculation_status: str  # CALCULATION_ELIGIBLE | CALCULATION_UNAVAILABLE
    fresh_persistence_permitted: bool
    report_completeness_ceiling: str  # "COMPLETE" | "LIMITED_EVIDENCE"
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = field(default_factory=tuple)


def classify_replay_or_acquisition(
    *, compatible_evidence_found: bool, evidence_id: int | None = None,
) -> HistoricalEvidenceAcquisitionDecision:
    """Called BEFORE any provider access is attempted, immediately after
    a persisted-evidence compatibility lookup (Stage J5). Compatible
    evidence means deterministic replay: no provider call, ever — a
    provider outage must never prevent this path from succeeding."""
    if compatible_evidence_found:
        return HistoricalEvidenceAcquisitionDecision(
            acquisition_status=COMPATIBLE_REPLAY, provider_call_expected=False,
            compatible_evidence_found=True, evidence_id=evidence_id,
        )
    return HistoricalEvidenceAcquisitionDecision(
        acquisition_status=ACQUISITION_REQUIRED, provider_call_expected=True,
        compatible_evidence_found=False,
    )


def classify_acquired_evidence(*, data_completeness: str | None) -> HistoricalEvidenceQualityDecision:
    """Called after a provider response was successfully validated into
    a PricePathEvidenceBundle (or after a compatible evidence row was
    replayed) — `data_completeness` is that bundle's own real,
    already-computed field, never re-derived from bar counts here.

    FAIL-CLOSED: any value not in the known set (None, "", a typo, a
    genuinely new future value this mapping hasn't been taught yet)
    returns UNSUPPORTED_EVIDENCE_COMPLETENESS with calculation_status=
    CALCULATION_UNAVAILABLE and fresh_persistence_permitted=False — never
    silently treated as COMPLETE/CALCULATION_ELIGIBLE."""
    status = _DATA_COMPLETENESS_TO_EVIDENCE_STATUS.get(data_completeness)

    if status is None:
        return HistoricalEvidenceQualityDecision(
            evidence_status=UNSUPPORTED_EVIDENCE_COMPLETENESS, calculation_status=CALCULATION_UNAVAILABLE,
            fresh_persistence_permitted=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(UNSUPPORTED_EVIDENCE_COMPLETENESS,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
        )
    if status == SOURCE_UNAVAILABLE:
        # A zero-bar bundle (an honest "the provider ran and returned
        # nothing usable" manifest, never fabricated bars) IS an
        # immutable evidence record worth persisting — Stage 3's own
        # explicit decision point. Distinct from SOURCE_INVALID, which
        # represents data that failed validation and must never be
        # persisted at all.
        return HistoricalEvidenceQualityDecision(
            evidence_status=status, calculation_status=CALCULATION_UNAVAILABLE,
            fresh_persistence_permitted=True, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(status,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
        )
    if status == SOURCE_INVALID:
        return HistoricalEvidenceQualityDecision(
            evidence_status=status, calculation_status=CALCULATION_UNAVAILABLE,
            fresh_persistence_permitted=False, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(status,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
        )
    if status == AMBIGUOUS_RESOLUTION:
        return HistoricalEvidenceQualityDecision(
            evidence_status=AMBIGUOUS_RESOLUTION, calculation_status=CALCULATION_ELIGIBLE,
            fresh_persistence_permitted=True, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(AMBIGUOUS_RESOLUTION,), limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
        )
    if status == PARTIAL_EVIDENCE:
        return HistoricalEvidenceQualityDecision(
            evidence_status=PARTIAL_EVIDENCE, calculation_status=CALCULATION_ELIGIBLE,
            fresh_persistence_permitted=True, report_completeness_ceiling="LIMITED_EVIDENCE",
            reason_codes=(PARTIAL_EVIDENCE,),
        )
    # status == CALCULATION_ELIGIBLE (data_completeness == "COMPLETE")
    return HistoricalEvidenceQualityDecision(
        evidence_status=CALCULATION_ELIGIBLE, calculation_status=CALCULATION_ELIGIBLE,
        fresh_persistence_permitted=True, report_completeness_ceiling="COMPLETE",
    )


@dataclass(frozen=True)
class ProviderFailurePolicy:
    """Stage J1B-Fail-Closed-Hardening, Stage 5 — the SINGLE typed
    mapping from a provider error code to every lifecycle consequence.
    Callers consult this policy and act on its fields; they do not
    separately hard-code the same outcome next to a call to
    classify_provider_failure."""
    evidence_status: str
    retry_permitted: bool
    terminal_permitted: bool
    evidence_fresh_persistence_permitted: bool
    report_permitted: bool
    sanitized_error_code: str
    limitations: tuple[str, ...] = field(default_factory=tuple)


# Every PriceProviderAcquisitionError.code this codebase currently
# raises (price_path_acquisition.py), inspected explicitly rather than
# assumed identical. All three are, today, retryable-not-terminal: none
# represents a condition retrying the SAME request is guaranteed to
# reproduce forever in a way that couldn't ALSO resolve (a transient
# network blip, a momentarily-too-large response, or — for the shape
# error — a genuinely unexpected provider library behavior that a
# future library version could stop producing). The outbox's own
# MAX_ATTEMPTS_BEFORE_TERMINAL already bounds a truly permanent failure
# to a finite number of attempts before durably settling FAILED_TERMINAL
# — terminal_permitted=True here means "the attempt-limit mechanism is
# allowed to eventually terminate this," not "this call itself settles
# terminal."
_PROVIDER_FAILURE_POLICIES = {
    "PROVIDER_FETCH_FAILED": ProviderFailurePolicy(
        evidence_status=SOURCE_UNAVAILABLE, retry_permitted=True, terminal_permitted=True,
        evidence_fresh_persistence_permitted=False, report_permitted=False,
        sanitized_error_code="PROVIDER_FETCH_FAILED", limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
    ),
    "PROVIDER_RESPONSE_TOO_LARGE": ProviderFailurePolicy(
        evidence_status=SOURCE_UNAVAILABLE, retry_permitted=True, terminal_permitted=True,
        evidence_fresh_persistence_permitted=False, report_permitted=False,
        sanitized_error_code="PROVIDER_RESPONSE_TOO_LARGE", limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
    ),
    "PROVIDER_UNEXPECTED_COLUMN_SHAPE": ProviderFailurePolicy(
        evidence_status=SOURCE_INVALID, retry_permitted=True, terminal_permitted=True,
        evidence_fresh_persistence_permitted=False, report_permitted=False,
        sanitized_error_code="PROVIDER_UNEXPECTED_COLUMN_SHAPE", limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
    ),
}
_DEFAULT_PROVIDER_FAILURE_POLICY = ProviderFailurePolicy(
    evidence_status=SOURCE_UNAVAILABLE, retry_permitted=True, terminal_permitted=True,
    evidence_fresh_persistence_permitted=False, report_permitted=False,
    sanitized_error_code="PROVIDER_ACQUISITION_ERROR", limitations=(_INSUFFICIENT_EVIDENCE_FALLBACK,),
)


def get_provider_failure_policy(*, error_code: str) -> ProviderFailurePolicy:
    """A code this module hasn't seen before still fails closed (never
    evidence persistence, never a report) via _DEFAULT_PROVIDER_FAILURE_
    POLICY, rather than raising or silently permitting persistence."""
    return _PROVIDER_FAILURE_POLICIES.get(error_code, _DEFAULT_PROVIDER_FAILURE_POLICY)


_KNOWN_CEILINGS = frozenset({"COMPLETE", "LIMITED_EVIDENCE"})


def compute_report_completeness_ceiling(*ceilings: str) -> str:
    """Stage J3 — the final report status must never exceed the WEAKEST
    applicable ceiling. Every caller (trade-context ceiling from J1A,
    evidence-quality ceiling from J1B, any downstream boundary/level-
    history ceiling) passes its own ceiling in; this is the single
    place \"minimum of all applicable ceilings\" is computed, so no
    caller can accidentally let a locally-COMPLETE value override an
    upstream LIMITED_EVIDENCE one.

    Stage J1B-Assurance-Closure correction: the prior implementation
    (`"LIMITED_EVIDENCE" if "LIMITED_EVIDENCE" in ceilings else
    "COMPLETE"`) FAILED OPEN — any unknown, null, empty, or malformed
    ceiling value that was not literally the string "LIMITED_EVIDENCE"
    fell through to "COMPLETE", including a value nobody actually
    intended to be treated as complete. Now every input is validated
    against the known two-value set; any unrecognized value fails
    closed to LIMITED_EVIDENCE, exactly like an explicit
    LIMITED_EVIDENCE input would."""
    if any(c not in _KNOWN_CEILINGS for c in ceilings):
        return "LIMITED_EVIDENCE"
    return "LIMITED_EVIDENCE" if "LIMITED_EVIDENCE" in ceilings else "COMPLETE"
