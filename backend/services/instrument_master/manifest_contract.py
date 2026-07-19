"""
Offline manifest/completeness contract and SME freshness/fallback types
(Phase C1-D1).

This module defines types and deterministic validation only. It never
implements a fetcher, a scheduler, an operator UI, file upload, storage,
or any downstream consumer behavior — those remain out of scope for
C1-D1 and require separate, future authorization.

Per the ratified Phase C1-C2 decision package (item 6, "APPROVED WITH
REQUIRED MODIFICATION"): `approved_for_daily_picks_non_sme` is a
distinct flag from `approved_for_daily_picks` — non-SME Daily Picks
eligibility must never be inferred from the coarser flag alone. See
build_completeness_contract() for the enforced invariant.

Cross-reference note (Phase C1-C2 §4 required amendment): the SME
fallback design lives in this module and in source_registry.py's
`nse_sme_current` entry — any prose description of "the SME fallback
design" in this codebase should reference this module, not a
document-section number, precisely to avoid repeating the three
§6-vs-§7 citation errors identified in the Phase C1-C2 audit.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .canonicalize import canonical_json_bytes, sha256_hex
from .enums import RetrievalMethod, SourceCriticality, SourceId, StaleState, ValidationResultStatus
from .source_registry import all_entries

# ---------------------------------------------------------------------------
# SME (and, generically, any source's) freshness thresholds — ratified
# Phase C1-C2 §7 decision D-03: calendar days, not trading days.
# ---------------------------------------------------------------------------

FRESHNESS_WARNING_DAYS_DEFAULT = 3
HARD_STALE_DAYS_DEFAULT = 7


def compute_stale_state(age_days: int, *, warning_days: int = FRESHNESS_WARNING_DAYS_DEFAULT, hard_stale_days: int = HARD_STALE_DAYS_DEFAULT) -> StaleState:
    """Pure function: age_days -> StaleState. No wall-clock read — the
    caller supplies age_days explicitly (computed elsewhere, outside
    this module, from an already-known retrieved_at and an already-known
    "now" — this module never calls a clock)."""
    if age_days < 0:
        raise ValueError("age_days must be non-negative")
    if age_days <= warning_days:
        return StaleState.FRESH
    if age_days <= hard_stale_days:
        return StaleState.STALE_WARNING
    return StaleState.HARD_STALE


@dataclass(frozen=True)
class SmeFreshnessRecord:
    """Preserves exactly the fields the ratified Phase C1-C2 §6/§12
    contract requires for a source's last-known-good/operator-assisted
    provenance record. `retrieved_at` is an explicit caller-supplied
    string (ISO-8601 UTC), never computed by this module."""

    source_id: SourceId
    source_sha256: str
    source_byte_size: int
    retrieved_at: str
    source_url: str
    retrieval_method: RetrievalMethod
    validation_status: ValidationResultStatus
    reviewed: bool
    reviewed_by: str | None
    reviewed_at: str | None
    stale_state: StaleState

    def __post_init__(self) -> None:
        if self.retrieval_method == RetrievalMethod.OPERATOR_ASSISTED and not self.reviewed:
            raise ValueError(
                "operator_assisted retrieval requires reviewed=True "
                "(no single-person silent submission, per ratified Phase C1-C2 §6)"
            )
        if self.reviewed and (self.reviewed_by is None or self.reviewed_at is None):
            raise ValueError("reviewed=True requires both reviewed_by and reviewed_at to be set")
        if not self.reviewed and (self.reviewed_by is not None or self.reviewed_at is not None):
            raise ValueError("reviewed_by/reviewed_at must be unset when reviewed=False")


# ---------------------------------------------------------------------------
# Structured per-source readiness (Phase C1-D5 correction of the Phase
# C1-D4 BLOCKING finding).
#
# Prior history: Phase C1-D2 found that a single trusted aggregate
# Boolean for "are the non-SME required sources ok" let a caller assert
# readiness while the structured per-source data said otherwise. Phase
# C1-D3 fixed this for nse_etf_current by folding it into a structured
# dict, but left `critical_required_sources_ok` (nse_equity_current) as
# an unchecked, plain Boolean — the exact same vulnerability class,
# merely narrowed in scope. Phase C1-D4 caught this residual gap.
#
# This module now has exactly ONE authoritative structured
# representation — `source_statuses: dict[SourceId, SourceReadinessStatus]`
# — covering all 11 registered sources. There is no overlapping Boolean
# map, and no aggregate Boolean of any kind. Every derived safety
# property (taxonomy_complete, source_set_complete,
# approved_for_daily_picks_non_sme, etc.) is computed from this single
# structured mapping plus the ratified criticality data already declared
# in source_registry.py — never from a caller-supplied summary.
# ---------------------------------------------------------------------------


# The closed set of ValidationResultStatus members that mean "validation
# succeeded" (Phase C1-D7 correction of a Phase C1-D6 finding). Every
# other ValidationResultStatus member means validation failed. Kept as
# an explicit, hand-maintained set (not "everything except a failure
# list") so that adding a new ValidationResultStatus member in the
# future requires a deliberate decision about which side of this line
# it falls on — see the parametrized coverage test in
# test_manifest_contract.py, which fails if any current-or-future member
# is left unclassified.
VALIDATION_SUCCESS_STATUSES = frozenset(
    {
        ValidationResultStatus.VALID,
        ValidationResultStatus.VALID_EMPTY,
    }
)


@dataclass(frozen=True)
class SourceReadinessStatus:
    """One source's structured readiness state. `source_id` must match
    the dict key it is stored under in `source_statuses` (enforced by
    build_completeness_contract(), not by this dataclass alone, since a
    dataclass cannot see its own container key).

    Ready == available AND validation_valid AND fresh — all three,
    computed by is_source_ready(), never inferred from any single field
    alone."""

    source_id: SourceId
    available: bool
    validation_valid: bool
    fresh: bool
    validation_status: ValidationResultStatus

    def __post_init__(self) -> None:
        if not self.available and self.validation_valid:
            raise ValueError(
                f"{self.source_id}: available=False with validation_valid=True is "
                "an impossible combination — an unavailable source was never "
                "retrieved, so it cannot have passed validation"
            )
        if not self.available and self.fresh:
            raise ValueError(
                f"{self.source_id}: available=False with fresh=True is an "
                "impossible combination — an unavailable source has no retrieval "
                "to measure freshness against"
            )
        # Phase C1-D7 correction: validation_valid and validation_status
        # must agree exactly — validation_valid=True if and only if
        # validation_status is one of VALIDATION_SUCCESS_STATUSES. This
        # closes the Phase C1-D6 finding that a caller could previously
        # construct a self-contradictory pairing (e.g.
        # validation_valid=True with validation_status=INVALID_ISIN)
        # without any rejection.
        success_status = self.validation_status in VALIDATION_SUCCESS_STATUSES
        if self.validation_valid and not success_status:
            raise ValueError(
                f"{self.source_id}: validation_valid=True requires validation_status "
                f"to be one of {sorted(s.value for s in VALIDATION_SUCCESS_STATUSES)}, "
                f"got {self.validation_status.value!r}"
            )
        if not self.validation_valid and success_status:
            raise ValueError(
                f"{self.source_id}: validation_valid=False is inconsistent with a "
                f"successful validation_status ({self.validation_status.value!r})"
            )
        # validation_valid=False with fresh=True is deliberately NOT rejected:
        # this is a real, meaningful state — the source responded quickly/
        # recently (fresh) but its content failed structural/ISIN validation
        # (e.g. NSE served corrupted or malformed content on a fresh fetch).
        # Justified exception to the "reject contradictory combinations" rule.
        # available=True, fresh=True, validation_valid=False (with a
        # matching non-success validation_status, e.g. INVALID_HEADER) is
        # exactly this "recently retrieved but failed validation" state,
        # and remains representable — it is simply never counted as
        # ready, since is_source_ready() requires validation_valid=True.


def is_source_ready(status: SourceReadinessStatus) -> bool:
    """Ready requires all three: available, validation_valid, and fresh.
    Never inferred from any one field alone."""
    return status.available and status.validation_valid and status.fresh


# ---------------------------------------------------------------------------
# Manifest completeness/freshness contract.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceErrorDetail:
    source_id: SourceId
    error_class: str
    message: str
    attempt_count: int


@dataclass(frozen=True)
class CompletenessContract:
    """Deterministic manifest fields, per the ratified Phase C1-C2 §12/§13
    contract plus the required approved_for_daily_picks_non_sme amendment.
    No field here is ever inferred from mere snapshot/file existence —
    every flag is an explicit, validated construction (see
    build_completeness_contract())."""

    snapshot_complete: bool
    taxonomy_complete: bool
    source_set_complete: bool
    stale_source_count: int
    unavailable_sources: tuple[SourceId, ...]
    source_errors: tuple[SourceErrorDetail, ...]
    oldest_source_age_days: int | None
    newest_source_age_days: int | None
    approved_for_daily_picks: bool
    approved_for_daily_picks_non_sme: bool
    approved_for_screener: bool
    approved_for_recommendations: bool
    manual_source_present: bool
    manual_source_reviewed_by: str | None
    manual_source_reviewed_at: str | None


class CompletenessContractError(ValueError):
    """Raised by build_completeness_contract() when a required invariant
    (per ratified Phase C1-C2 decisions) would be violated."""


def build_completeness_contract(
    *,
    source_statuses: dict[SourceId, SourceReadinessStatus],
    source_errors: tuple[SourceErrorDetail, ...],
    oldest_source_age_days: int | None,
    newest_source_age_days: int | None,
    business_policy_permits_daily_picks: bool,
    business_policy_permits_daily_picks_non_sme: bool,
    business_policy_permits_screener: bool,
    business_policy_permits_recommendations: bool,
    sme_explicitly_excluded_from_eligible_universe: bool,
    unknown_unclassified_securities_excluded: bool,
    manual_source_present: bool,
    manual_source_reviewed_by: str | None,
    manual_source_reviewed_at: str | None,
) -> CompletenessContract:
    """Constructs a CompletenessContract enforcing every invariant listed
    in the C1-D1 authorization §11 and the Phase C1-D2/C1-D3/C1-D5
    corrections.

    Phase C1-D5 correction: there is now exactly ONE authoritative
    structured input for source readiness — `source_statuses` — covering
    all 11 registered sources. The three overlapping, partially-trusted
    Boolean parameters this function previously accepted
    (`critical_required_sources_ok`, `required_for_complete_taxonomy_sources_ok`,
    `all_required_sources_ok`) no longer exist. `unavailable_sources` and
    `stale_source_count` are no longer separate caller inputs either —
    both are derived here, directly from `source_statuses`, so they
    cannot contradict it.

      - Every one of the 11 registered sources (per source_registry.py's
        SOURCE_REGISTRY) must have an entry in `source_statuses`;
        missing any raises CompletenessContractError. This covers
        nse_equity_current, nse_etf_current, and nse_sme_current
        automatically — there is no separate presence check needed for
        any of the three, since all 11 are required.
      - Every `SourceReadinessStatus` dict key must equal its own
        `.source_id` — a mismatch raises CompletenessContractError.
      - `taxonomy_complete` is derived from `source_statuses` plus the
        ratified criticality data in source_registry.py: every
        CRITICAL_REQUIRED and REQUIRED_FOR_COMPLETE_TAXONOMY source must
        be ready (available AND validation_valid AND fresh, per
        is_source_ready()).
      - `source_set_complete` is derived the same way across all 11
        sources, regardless of criticality (the strict "all 11" C1-C1
        definition).
      - `approved_for_daily_picks_non_sme` requires nse_equity_current
        AND nse_etf_current to both be ready (derived, never
        caller-overridable), PLUS explicit
        `sme_explicitly_excluded_from_eligible_universe=True` and
        `unknown_unclassified_securities_excluded=True`, PLUS an
        explicit business-policy grant. There is no parameter through
        which a caller can assert equity or ETF readiness independently
        of `source_statuses` — the Phase C1-D4 residual gap (equity
        readiness trusted via a plain Boolean) is now structurally
        impossible, not merely checked-and-rejected.
      - approved_for_daily_picks/approved_for_daily_picks_non_sme/
        approved_for_screener/approved_for_recommendations are NEVER
        inferred from snapshot existence alone — each requires both the
        relevant derived completeness precondition AND an explicit
        business-policy input (business_policy_permits_*).
      - manual_source_present never implies manual_source_reviewed —
        caller must supply reviewed_by/reviewed_at independently; this
        function raises if they are inconsistent with each other.
    """
    registry_entries = all_entries()  # fixed order, all 11 SourceId members

    for entry in registry_entries:
        if entry.source_id not in source_statuses:
            raise CompletenessContractError(
                f"{entry.source_id} status must be explicitly represented in source_statuses "
                "(all 11 registered sources are required, not just equity/ETF/SME)"
            )
    for key, status in source_statuses.items():
        if key != status.source_id:
            raise CompletenessContractError(
                f"source_statuses key {key!r} does not match status.source_id {status.source_id!r}"
            )

    if manual_source_present and manual_source_reviewed_by is None:
        # manual_source_present=True with no reviewer on file is a valid,
        # representable state (an unreviewed manual submission) — but
        # reviewed_at without reviewed_by, or vice versa, is not.
        if manual_source_reviewed_at is not None:
            raise CompletenessContractError(
                "manual_source_reviewed_at set without manual_source_reviewed_by"
            )
    if manual_source_reviewed_by is not None and manual_source_reviewed_at is None:
        raise CompletenessContractError(
            "manual_source_reviewed_by set without manual_source_reviewed_at"
        )
    if not manual_source_present and (manual_source_reviewed_by is not None or manual_source_reviewed_at is not None):
        raise CompletenessContractError(
            "manual_source_reviewed_by/at set while manual_source_present=False"
        )

    # Derived entirely from source_statuses — never a separate,
    # independently-trusted caller input.
    unavailable_sources = tuple(
        sorted((sid for sid, s in source_statuses.items() if not s.available), key=lambda sid: sid.value)
    )
    stale_source_count = sum(1 for s in source_statuses.values() if s.available and not s.fresh)

    # Criticality partitions derived from source_registry.py's own
    # ratified data — never hardcoded to a specific SourceId, so this
    # remains correct if the registry's criticality assignments ever
    # change.
    critical_entries = [e for e in registry_entries if e.criticality == SourceCriticality.CRITICAL_REQUIRED]
    taxonomy_entries = [
        e for e in registry_entries
        if e.criticality in (SourceCriticality.CRITICAL_REQUIRED, SourceCriticality.REQUIRED_FOR_COMPLETE_TAXONOMY)
    ]

    critical_ready = all(is_source_ready(source_statuses[e.source_id]) for e in critical_entries)
    taxonomy_complete = all(is_source_ready(source_statuses[e.source_id]) for e in taxonomy_entries)
    source_set_complete = all(is_source_ready(source_statuses[e.source_id]) for e in registry_entries)

    # Phase C1-D7 correction of a Phase C1-D6 finding: snapshot_complete
    # means "every CRITICAL_REQUIRED source is ready" and nothing more —
    # it must NOT be additionally gated by unavailable_sources across all
    # 11 registered sources. An OPTIONAL_ENRICHMENT/HISTORY_ONLY source
    # being unavailable, invalid, or stale must never affect this field;
    # that is exactly what source_set_complete (below) already exists to
    # capture, with its own, deliberately stricter, all-11 definition.
    snapshot_complete = critical_ready

    approved_for_daily_picks = taxonomy_complete and business_policy_permits_daily_picks

    # Equity and ETF readiness, derived directly from the single
    # structured source_statuses mapping — no aggregate Boolean, no
    # parameter through which a caller could assert either independently.
    equity_ready = is_source_ready(source_statuses[SourceId.NSE_EQUITY_CURRENT])
    etf_ready = is_source_ready(source_statuses[SourceId.NSE_ETF_CURRENT])
    non_sme_sources_ok = equity_ready and etf_ready

    approved_for_daily_picks_non_sme = (
        non_sme_sources_ok
        and sme_explicitly_excluded_from_eligible_universe
        and unknown_unclassified_securities_excluded
        and business_policy_permits_daily_picks_non_sme
    )

    approved_for_screener = critical_ready and business_policy_permits_screener
    approved_for_recommendations = critical_ready and business_policy_permits_recommendations

    return CompletenessContract(
        snapshot_complete=snapshot_complete,
        taxonomy_complete=taxonomy_complete,
        source_set_complete=source_set_complete,
        stale_source_count=stale_source_count,
        unavailable_sources=unavailable_sources,
        source_errors=source_errors,
        oldest_source_age_days=oldest_source_age_days,
        newest_source_age_days=newest_source_age_days,
        approved_for_daily_picks=approved_for_daily_picks,
        approved_for_daily_picks_non_sme=approved_for_daily_picks_non_sme,
        approved_for_screener=approved_for_screener,
        approved_for_recommendations=approved_for_recommendations,
        manual_source_present=manual_source_present,
        manual_source_reviewed_by=manual_source_reviewed_by,
        manual_source_reviewed_at=manual_source_reviewed_at,
    )


def _contract_to_canonical_dict(contract: CompletenessContract) -> dict:
    return {
        "snapshot_complete": contract.snapshot_complete,
        "taxonomy_complete": contract.taxonomy_complete,
        "source_set_complete": contract.source_set_complete,
        "stale_source_count": contract.stale_source_count,
        "unavailable_sources": sorted(s.value for s in contract.unavailable_sources),
        "source_errors": sorted(
            (
                {
                    "source_id": e.source_id.value,
                    "error_class": e.error_class,
                    "message": e.message,
                    "attempt_count": e.attempt_count,
                }
                for e in contract.source_errors
            ),
            key=lambda d: (d["source_id"], d["error_class"], d["message"]),
        ),
        "oldest_source_age_days": contract.oldest_source_age_days,
        "newest_source_age_days": contract.newest_source_age_days,
        "approved_for_daily_picks": contract.approved_for_daily_picks,
        "approved_for_daily_picks_non_sme": contract.approved_for_daily_picks_non_sme,
        "approved_for_screener": contract.approved_for_screener,
        "approved_for_recommendations": contract.approved_for_recommendations,
        "manual_source_present": contract.manual_source_present,
        "manual_source_reviewed_by": contract.manual_source_reviewed_by,
        "manual_source_reviewed_at": contract.manual_source_reviewed_at,
    }


def canonical_completeness_contract_bytes(contract: CompletenessContract) -> bytes:
    return canonical_json_bytes(_contract_to_canonical_dict(contract))


def canonical_completeness_contract_hash(contract: CompletenessContract) -> str:
    return sha256_hex(canonical_completeness_contract_bytes(contract))
