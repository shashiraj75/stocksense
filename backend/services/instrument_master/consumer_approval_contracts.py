"""
Consumer-approval evidence and contract types (Phase C2-D2).

This module implements ONLY the enums, frozen dataclasses, constructor
validation, and deterministic canonical serialization ratified by the
Phase C2-D1B design correction. It performs no network I/O, no database
I/O, reads no environment variable, and never reads the wall clock —
every timestamp appearing anywhere in this module is supplied explicitly
by the caller.

This module deliberately does NOT:
  - acquire, download, or fetch anything from NSE or any other host;
  - calculate freshness (it validates structural consistency of
    caller-supplied freshness fields only — it never derives
    warning_deadline_at/hard_stale_deadline_at/age_seconds/
    age_days_display from source_effective_at + a registry's
    freshness_warning_days/hard_stale_days; that derivation is a future,
    separately authorized phase's responsibility);
  - decide consumer approval (ConsumerApprovalEvaluation instances are
    constructed and validated here; nothing in this module computes one
    from SourceAcquisitionRecord/SourceReadinessStatus inputs);
  - ratify a FreshnessBasis for any real SourceId. FreshnessBasis.
    ACQUISITION_TIME_PROXY is representable (a SourceAcquisitionRecord
    may be constructed with it) but is NOT ratified for any of the 11
    real SourceId members as of this phase — per the explicit Phase
    C2-D1B/C2-D2 policy decision, that ratification requires a
    separately authorized future evidence-gathering phase. Only test
    fixtures should construct a record using this basis today.

Legacy isolation: the structured contracts in this module are NOT
integrated with, and do not modify, manifest_contract.py's
CompletenessContract — in particular its existing unsuffixed
approved_for_screener/approved_for_recommendations/
approved_for_daily_picks/approved_for_daily_picks_non_sme fields, which
remain at their exact Phase C1 derivation. New future consumers must not
use those legacy unsuffixed fields — actual integration of this
module's contracts with any live consumer, or with CompletenessContract
itself, requires a separately authorized future phase.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from .canonicalize import canonical_json_bytes, sha256_hex
from .enums import (
    AcquisitionResultStatus,
    CalibrationStatus,
    ConsumerApprovalMode,
    ConsumerType,
    FreshnessBasis,
    InstrumentClass,
    SourceDataOrigin,
    SourceErrorCode,
    SourceId,
    TaxonomyReadinessMode,
    ValidationResultStatus,
)

# ---------------------------------------------------------------------------
# Timestamp validation — exact UTC ISO-8601 only, per Phase C2-D1B §13/§6.
# ---------------------------------------------------------------------------

_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?Z$")


def _validate_utc_timestamp(field_name: str, value: str) -> None:
    """Rejects: timezone offsets other than 'Z'; naive timestamps;
    malformed dates; impossible calendar dates/times; whitespace;
    date-only values. Never normalizes or converts the input — a value
    that fails this check is rejected outright, not coerced."""
    if not isinstance(value, str) or not _UTC_TIMESTAMP_RE.fullmatch(value):
        raise ValueError(
            f"{field_name}: must be an exact UTC ISO-8601 timestamp "
            f"('YYYY-MM-DDTHH:MM:SSZ' or 'YYYY-MM-DDTHH:MM:SS.ffffffZ'), "
            f"got {value!r}"
        )
    fmt = "%Y-%m-%dT%H:%M:%S.%fZ" if "." in value else "%Y-%m-%dT%H:%M:%SZ"
    try:
        datetime.strptime(value, fmt)
    except ValueError as exc:
        raise ValueError(f"{field_name}: not a valid calendar date/time: {value!r}") from exc


def _validate_optional_utc_timestamp(field_name: str, value: str | None) -> None:
    if value is not None:
        _validate_utc_timestamp(field_name, value)


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256(field_name: str, value: str) -> None:
    if not isinstance(value, str) or not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError(
            f"{field_name}: must be exactly 64 lowercase hexadecimal characters, got {value!r}"
        )


def _validate_non_negative_int(field_name: str, value: int | None) -> None:
    if value is not None and value < 0:
        raise ValueError(f"{field_name}: must be non-negative, got {value!r}")


def _sorted_by_value(items: tuple) -> tuple:
    return tuple(sorted(items, key=lambda member: member.value))


def _require_canonical_order(field_name: str, items: tuple) -> None:
    """Structural determinism invariant: any valid instance's
    enum-valued tuple fields are already in their canonical
    (sorted-by-.value) order at construction time — determinism is
    enforced by the type itself, not left to callers to remember."""
    if tuple(items) != _sorted_by_value(tuple(items)):
        raise ValueError(
            f"{field_name}: must already be sorted by member .value "
            f"(canonical order) — got {tuple(m.value for m in items)!r}"
        )


# ---------------------------------------------------------------------------
# SourceAcquisitionRecord — validated acquisition evidence for one source,
# one evaluation instant. Stores caller-supplied evidence only.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceAcquisitionRecord:
    source_id: SourceId
    canonical_url: str
    acquisition_started_at: str
    acquisition_completed_at: str | None
    result_status: AcquisitionResultStatus
    response_status_code: int | None
    retrieved_at: str | None
    source_effective_at: str | None
    freshness_evaluated_at: str | None
    freshness_basis: FreshnessBasis
    source_data_origin: SourceDataOrigin
    raw_byte_size: int | None
    raw_sha256: str | None
    validation_status: ValidationResultStatus
    validation_error_codes: tuple[SourceErrorCode, ...]
    warning_codes: tuple[SourceErrorCode, ...]
    row_count: int | None
    accepted_row_count: int | None
    rejected_row_count: int | None
    duplicate_count: int
    warning_deadline_at: str | None
    hard_stale_deadline_at: str | None
    age_seconds: int | None
    age_days_display: int | None
    snapshot_id: str
    last_known_good_snapshot_id: str | None
    using_last_known_good: bool

    def __post_init__(self) -> None:
        if not self.canonical_url:
            raise ValueError("canonical_url must be non-empty")
        if not self.snapshot_id:
            raise ValueError("snapshot_id must be non-empty")

        _validate_utc_timestamp("acquisition_started_at", self.acquisition_started_at)
        _validate_optional_utc_timestamp("acquisition_completed_at", self.acquisition_completed_at)
        _validate_optional_utc_timestamp("retrieved_at", self.retrieved_at)
        _validate_optional_utc_timestamp("source_effective_at", self.source_effective_at)
        _validate_optional_utc_timestamp("freshness_evaluated_at", self.freshness_evaluated_at)
        _validate_optional_utc_timestamp("warning_deadline_at", self.warning_deadline_at)
        _validate_optional_utc_timestamp("hard_stale_deadline_at", self.hard_stale_deadline_at)

        _require_canonical_order("validation_error_codes", self.validation_error_codes)
        _require_canonical_order("warning_codes", self.warning_codes)

        # -- Freshness invariants (Phase C2-D1B §6/§7 — structural only,
        # never a live SourceId-specific policy decision) --
        effective_at_is_none = self.source_effective_at is None
        basis_is_unknown = self.freshness_basis is FreshnessBasis.UNKNOWN
        if basis_is_unknown and not effective_at_is_none:
            raise ValueError(
                "freshness_basis=UNKNOWN requires source_effective_at=None"
            )
        if not basis_is_unknown and effective_at_is_none:
            raise ValueError(
                f"freshness_basis={self.freshness_basis.value!r} requires a non-None source_effective_at"
            )

        # -- Source-data-origin / last-known-good invariants --
        if self.using_last_known_good:
            if self.source_data_origin is not SourceDataOrigin.LAST_KNOWN_GOOD:
                raise ValueError(
                    "using_last_known_good=True requires source_data_origin=LAST_KNOWN_GOOD"
                )
            if self.last_known_good_snapshot_id is None:
                raise ValueError(
                    "using_last_known_good=True requires a non-None last_known_good_snapshot_id"
                )
        if self.source_data_origin is SourceDataOrigin.LAST_KNOWN_GOOD and not self.using_last_known_good:
            raise ValueError(
                "source_data_origin=LAST_KNOWN_GOOD requires using_last_known_good=True"
            )
        if self.source_data_origin is SourceDataOrigin.CURRENT and self.using_last_known_good:
            raise ValueError(
                "source_data_origin=CURRENT requires using_last_known_good=False"
            )

        # -- Count/hash invariants (Phase C2-D2B correction: explicitly
        # documented two-phase order, per the ratified C2-D2A audit
        # finding). Phase 1 (scalar-domain validation) always runs before
        # Phase 2 (cross-field validation) -- every individual count field
        # is checked against its own valid domain (non-negative) before any
        # relationship between two fields is evaluated. This was already
        # the actual execution order; these section markers make it an
        # explicit, self-documenting contract rather than an implicit
        # consequence of statement order, so a future edit cannot silently
        # reorder these two phases relative to each other. --

        # Phase 1: scalar-domain validation (each field checked independently).
        _validate_non_negative_int("raw_byte_size", self.raw_byte_size)
        _validate_non_negative_int("row_count", self.row_count)
        _validate_non_negative_int("accepted_row_count", self.accepted_row_count)
        _validate_non_negative_int("rejected_row_count", self.rejected_row_count)
        _validate_non_negative_int("duplicate_count", self.duplicate_count)
        _validate_non_negative_int("age_seconds", self.age_seconds)
        _validate_non_negative_int("age_days_display", self.age_days_display)
        if self.response_status_code is not None and self.response_status_code < 0:
            raise ValueError("response_status_code must be non-negative")

        # Phase 2: cross-field validation (only reachable once every
        # individual field above has already passed its own scalar check).
        if self.row_count is not None and self.duplicate_count > self.row_count:
            raise ValueError("duplicate_count must not exceed row_count")
        if (
            self.row_count is not None
            and self.accepted_row_count is not None
            and self.rejected_row_count is not None
            and (self.accepted_row_count + self.rejected_row_count) > self.row_count
        ):
            raise ValueError("accepted_row_count + rejected_row_count must not exceed row_count")

        if self.raw_sha256 is not None:
            _validate_sha256("raw_sha256", self.raw_sha256)

        # -- Successful-acquisition structural completeness --
        if self.result_status is AcquisitionResultStatus.SUCCESS:
            missing = [
                name
                for name, value in (
                    ("acquisition_completed_at", self.acquisition_completed_at),
                    ("raw_byte_size", self.raw_byte_size),
                    ("raw_sha256", self.raw_sha256),
                    ("row_count", self.row_count),
                )
                if value is None
            ]
            if missing:
                raise ValueError(
                    f"result_status=SUCCESS requires the following fields to be non-None: {missing}"
                )

        # -- Failed acquisition must not pretend to be a successful,
        # non-substituted current snapshot --
        if (
            self.result_status is AcquisitionResultStatus.NOT_ATTEMPTED
            and self.validation_status in (ValidationResultStatus.VALID, ValidationResultStatus.VALID_EMPTY)
            and not self.using_last_known_good
        ):
            raise ValueError(
                "result_status=NOT_ATTEMPTED cannot be paired with a successful "
                "validation_status unless using_last_known_good=True"
            )


def _source_acquisition_record_to_canonical_dict(record: SourceAcquisitionRecord) -> dict:
    return {
        "source_id": record.source_id.value,
        "canonical_url": record.canonical_url,
        "acquisition_started_at": record.acquisition_started_at,
        "acquisition_completed_at": record.acquisition_completed_at,
        "result_status": record.result_status.value,
        "response_status_code": record.response_status_code,
        "retrieved_at": record.retrieved_at,
        "source_effective_at": record.source_effective_at,
        "freshness_evaluated_at": record.freshness_evaluated_at,
        "freshness_basis": record.freshness_basis.value,
        "source_data_origin": record.source_data_origin.value,
        "raw_byte_size": record.raw_byte_size,
        "raw_sha256": record.raw_sha256,
        "validation_status": record.validation_status.value,
        "validation_error_codes": [c.value for c in record.validation_error_codes],
        "warning_codes": [c.value for c in record.warning_codes],
        "row_count": record.row_count,
        "accepted_row_count": record.accepted_row_count,
        "rejected_row_count": record.rejected_row_count,
        "duplicate_count": record.duplicate_count,
        "warning_deadline_at": record.warning_deadline_at,
        "hard_stale_deadline_at": record.hard_stale_deadline_at,
        "age_seconds": record.age_seconds,
        "age_days_display": record.age_days_display,
        "snapshot_id": record.snapshot_id,
        "last_known_good_snapshot_id": record.last_known_good_snapshot_id,
        "using_last_known_good": record.using_last_known_good,
    }


def canonical_source_acquisition_record_bytes(record: SourceAcquisitionRecord) -> bytes:
    return canonical_json_bytes(_source_acquisition_record_to_canonical_dict(record))


def canonical_source_acquisition_record_hash(record: SourceAcquisitionRecord) -> str:
    return sha256_hex(canonical_source_acquisition_record_bytes(record))


# ---------------------------------------------------------------------------
# ClassificationConflictPolicy — versioned, non-authoritative policy object.
# A PROVISIONAL or SHADOW_VALIDATED instance must never, by itself,
# authorize a production consumer-integration decision (Phase C2-D1B §11).
# This module deliberately defines no module-level default/global
# instance — every caller (including tests) must construct one explicitly.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassificationConflictPolicy:
    policy_version: str
    isolated_max_count: int
    isolated_max_ratio: float
    systemic_source_ratio: float
    minimum_compared_row_count: int
    effective_from: str
    calibration_status: CalibrationStatus

    def __post_init__(self) -> None:
        if not self.policy_version or not self.policy_version.strip():
            raise ValueError("policy_version must be non-empty")
        if self.isolated_max_count < 0:
            raise ValueError("isolated_max_count must be non-negative")
        if not (0.0 <= self.isolated_max_ratio <= 1.0):
            raise ValueError("isolated_max_ratio must be within [0.0, 1.0]")
        if not (0.0 <= self.systemic_source_ratio <= 1.0):
            raise ValueError("systemic_source_ratio must be within [0.0, 1.0]")
        if self.minimum_compared_row_count <= 0:
            raise ValueError("minimum_compared_row_count must be positive")
        _validate_utc_timestamp("effective_from", self.effective_from)


def _classification_conflict_policy_to_canonical_dict(policy: ClassificationConflictPolicy) -> dict:
    return {
        "policy_version": policy.policy_version,
        "isolated_max_count": policy.isolated_max_count,
        "isolated_max_ratio": policy.isolated_max_ratio,
        "systemic_source_ratio": policy.systemic_source_ratio,
        "minimum_compared_row_count": policy.minimum_compared_row_count,
        "effective_from": policy.effective_from,
        "calibration_status": policy.calibration_status.value,
    }


def canonical_classification_conflict_policy_bytes(policy: ClassificationConflictPolicy) -> bytes:
    return canonical_json_bytes(_classification_conflict_policy_to_canonical_dict(policy))


def canonical_classification_conflict_policy_hash(policy: ClassificationConflictPolicy) -> str:
    return sha256_hex(canonical_classification_conflict_policy_bytes(policy))


# ---------------------------------------------------------------------------
# ConsumerApprovalEvaluation — the authoritative, structured audit record
# for one consumer's approval decision. This module validates
# caller-supplied instances only; it never computes one from
# SourceAcquisitionRecord/SourceReadinessStatus inputs (that derivation
# is a future, separately authorized phase's responsibility).
# ---------------------------------------------------------------------------

# Every InstrumentClass except ORDINARY_EQUITY — the exhaustive exclusion
# set every non-BLOCKED initial evaluation must declare (Phase C2-D1B §2/§5).
_ALL_NON_ORDINARY_EQUITY_CLASSES = frozenset(InstrumentClass) - {InstrumentClass.ORDINARY_EQUITY}


@dataclass(frozen=True)
class ConsumerApprovalEvaluation:
    evaluation_id: str
    consumer: ConsumerType
    approved: bool
    approval_mode: ConsumerApprovalMode
    taxonomy_readiness_mode: TaxonomyReadinessMode
    taxonomy_classified_instrument_classes: tuple[InstrumentClass, ...]
    consumer_included_instrument_classes: tuple[InstrumentClass, ...]
    consumer_excluded_instrument_classes: tuple[InstrumentClass, ...]
    required_source_ids: tuple[SourceId, ...]
    current_source_ids: tuple[SourceId, ...]
    using_last_known_good_sources: frozenset[SourceId]
    blocking_reason_codes: tuple[SourceErrorCode, ...]
    warning_codes: tuple[SourceErrorCode, ...]
    source_snapshot_id: str | None
    conflict_policy_version: str
    evaluated_at: str

    def __post_init__(self) -> None:
        if not self.evaluation_id:
            raise ValueError("evaluation_id must be non-empty")
        if not self.conflict_policy_version:
            raise ValueError("conflict_policy_version must be non-empty")
        _validate_utc_timestamp("evaluated_at", self.evaluated_at)

        _require_canonical_order("taxonomy_classified_instrument_classes", self.taxonomy_classified_instrument_classes)
        _require_canonical_order("consumer_included_instrument_classes", self.consumer_included_instrument_classes)
        _require_canonical_order("consumer_excluded_instrument_classes", self.consumer_excluded_instrument_classes)
        _require_canonical_order("required_source_ids", self.required_source_ids)
        _require_canonical_order("current_source_ids", self.current_source_ids)
        _require_canonical_order("blocking_reason_codes", self.blocking_reason_codes)
        _require_canonical_order("warning_codes", self.warning_codes)

        if self.approved != (self.approval_mode is not ConsumerApprovalMode.BLOCKED):
            raise ValueError(
                "approved must equal (approval_mode != BLOCKED) — "
                f"got approved={self.approved!r}, approval_mode={self.approval_mode.value!r}"
            )

        # EQUITY_ONLY_UNSAFE_FOR_CONSUMERS (and, by the same logic, BLOCKED
        # taxonomy readiness) can never coexist with a non-BLOCKED approval.
        if self.taxonomy_readiness_mode in (
            TaxonomyReadinessMode.BLOCKED,
            TaxonomyReadinessMode.EQUITY_ONLY_UNSAFE_FOR_CONSUMERS,
        ) and self.approval_mode is not ConsumerApprovalMode.BLOCKED:
            raise ValueError(
                f"taxonomy_readiness_mode={self.taxonomy_readiness_mode.value!r} "
                "requires approval_mode=BLOCKED"
            )

        if self.approval_mode is ConsumerApprovalMode.BLOCKED:
            if self.approved:
                raise ValueError("approval_mode=BLOCKED requires approved=False")
            if self.consumer_included_instrument_classes:
                raise ValueError("approval_mode=BLOCKED requires an empty consumer_included_instrument_classes")
        else:
            if not self.approved:
                raise ValueError(f"approval_mode={self.approval_mode.value!r} requires approved=True")
            if self.source_snapshot_id is None:
                raise ValueError(f"approval_mode={self.approval_mode.value!r} requires a non-None source_snapshot_id")
            if not self.required_source_ids:
                raise ValueError(f"approval_mode={self.approval_mode.value!r} requires non-empty required_source_ids")
            if self.blocking_reason_codes:
                raise ValueError(f"approval_mode={self.approval_mode.value!r} requires empty blocking_reason_codes")

            if tuple(self.consumer_included_instrument_classes) != (InstrumentClass.ORDINARY_EQUITY,):
                raise ValueError(
                    "every non-BLOCKED initial consumer mode must serve exactly "
                    f"(ORDINARY_EQUITY,) — got {self.consumer_included_instrument_classes!r}"
                )
            if set(self.consumer_excluded_instrument_classes) != _ALL_NON_ORDINARY_EQUITY_CLASSES:
                raise ValueError(
                    "every non-BLOCKED initial consumer mode must exclude exactly every "
                    f"InstrumentClass other than ORDINARY_EQUITY — got "
                    f"{set(self.consumer_excluded_instrument_classes)!r}"
                )

        if self.approval_mode is ConsumerApprovalMode.RESTRICTED_NON_SME:
            if self.taxonomy_readiness_mode not in (
                TaxonomyReadinessMode.EQUITY_ETF_CLASSIFIED,
                TaxonomyReadinessMode.CORE_TAXONOMY_COMPLETE,
            ):
                raise ValueError(
                    "approval_mode=RESTRICTED_NON_SME requires taxonomy_readiness_mode to be "
                    "EQUITY_ETF_CLASSIFIED or CORE_TAXONOMY_COMPLETE, got "
                    f"{self.taxonomy_readiness_mode.value!r}"
                )
        elif self.approval_mode is ConsumerApprovalMode.CORE_TAXONOMY_COMPLETE:
            if self.taxonomy_readiness_mode is not TaxonomyReadinessMode.CORE_TAXONOMY_COMPLETE:
                raise ValueError(
                    "approval_mode=CORE_TAXONOMY_COMPLETE requires "
                    "taxonomy_readiness_mode=CORE_TAXONOMY_COMPLETE, got "
                    f"{self.taxonomy_readiness_mode.value!r}"
                )

        if not self.using_last_known_good_sources.issubset(set(self.required_source_ids)):
            raise ValueError("using_last_known_good_sources must be a subset of required_source_ids")
        if not set(self.current_source_ids).issubset(set(self.required_source_ids)):
            raise ValueError("current_source_ids must be a subset of required_source_ids")

        # -- Current/last-known-good evidence-origin disjointness (Phase
        # C2-D2B correction, ratified C2-D2A audit finding #3): a SourceId
        # cannot simultaneously represent current evidence and active
        # last-known-good fallback evidence within the same evaluation.
        # Applies to every approval mode, including BLOCKED -- the two
        # evidence origins are logically mutually exclusive regardless of
        # the resulting approval outcome. --
        overlapping_sources = set(self.current_source_ids) & self.using_last_known_good_sources
        if overlapping_sources:
            raise ValueError(
                "current_source_ids and using_last_known_good_sources must be "
                "disjoint -- a SourceId cannot be both current and last-known-good "
                f"evidence simultaneously; overlapping: {sorted(s.value for s in overlapping_sources)}"
            )

        # -- Required-source evidence coverage (Phase C2-D2B correction,
        # ratified C2-D2A audit finding #4): for every non-BLOCKED
        # evaluation, every required source must be covered by exactly one
        # active evidence origin (current or last-known-good) -- since the
        # two origins are already proven disjoint above, "covered by
        # current or LKG" and "covered by exactly one origin" are
        # equivalent for any source that passes this check. A BLOCKED
        # evaluation's incomplete required-source evidence remains
        # representable -- an audit record explaining why something is
        # blocked does not itself need pre-existing evidence coverage. --
        if self.approval_mode is not ConsumerApprovalMode.BLOCKED:
            covered_sources = set(self.current_source_ids) | self.using_last_known_good_sources
            missing_sources = set(self.required_source_ids) - covered_sources
            if missing_sources:
                raise ValueError(
                    "every required source in a non-BLOCKED evaluation must be "
                    "covered by exactly one active evidence origin (current or "
                    f"last-known-good); missing coverage for: {sorted(s.value for s in missing_sources)}"
                )


def _consumer_approval_evaluation_to_canonical_dict(evaluation: ConsumerApprovalEvaluation) -> dict:
    return {
        "evaluation_id": evaluation.evaluation_id,
        "consumer": evaluation.consumer.value,
        "approved": evaluation.approved,
        "approval_mode": evaluation.approval_mode.value,
        "taxonomy_readiness_mode": evaluation.taxonomy_readiness_mode.value,
        "taxonomy_classified_instrument_classes": [c.value for c in evaluation.taxonomy_classified_instrument_classes],
        "consumer_included_instrument_classes": [c.value for c in evaluation.consumer_included_instrument_classes],
        "consumer_excluded_instrument_classes": [c.value for c in evaluation.consumer_excluded_instrument_classes],
        "required_source_ids": [s.value for s in evaluation.required_source_ids],
        "current_source_ids": [s.value for s in evaluation.current_source_ids],
        "using_last_known_good_sources": sorted(s.value for s in evaluation.using_last_known_good_sources),
        "blocking_reason_codes": [c.value for c in evaluation.blocking_reason_codes],
        "warning_codes": [c.value for c in evaluation.warning_codes],
        "source_snapshot_id": evaluation.source_snapshot_id,
        "conflict_policy_version": evaluation.conflict_policy_version,
        "evaluated_at": evaluation.evaluated_at,
    }


def canonical_consumer_approval_evaluation_bytes(evaluation: ConsumerApprovalEvaluation) -> bytes:
    return canonical_json_bytes(_consumer_approval_evaluation_to_canonical_dict(evaluation))


def canonical_consumer_approval_evaluation_hash(evaluation: ConsumerApprovalEvaluation) -> str:
    return sha256_hex(canonical_consumer_approval_evaluation_bytes(evaluation))
