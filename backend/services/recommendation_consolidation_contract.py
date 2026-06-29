"""
Recommendation Consolidation Intelligence — Evidence Contract (Epic 005,
SSDS-009, Sprint #002's Evidence Contract specification, implemented
Sprint #003).

Defines the immutable data shapes Recommendation Consolidation Intelligence
(RCI) reads and returns. Per Sprint #002's own Evidence Checkpoint finding,
this module introduces ONLY new, additive structures — it does not modify
any existing engine, EngineResponse, or Prediction Engine code. Per SSDS-009
§5.A: RCI is read-only with respect to every existing score/grade/signal;
nothing here creates a master score, a replacement signal, or a replacement
confidence value.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

CONTRACT_VERSION = 1


class EvidenceStatus(str, Enum):
    """The 10-status taxonomy specified in Sprint #002's Evidence Contract
    §5. Critical rule, enforced by every adapter in this package: UNAVAILABLE,
    NOT_APPLICABLE, FEATURE_DISABLED, EXECUTION_ERROR, and STALE_SNAPSHOT must
    never be silently converted into negative investment evidence — a low
    Valuation grade, Financial Strength liquidity distress, missing data, and
    Bank/NBFC non-applicability are four genuinely distinct statuses."""

    SUPPORTED = "supported"
    MIXED = "mixed"
    WARNING = "warning"
    AVOID = "avoid"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"
    NOT_APPLICABLE = "not_applicable"
    EXECUTION_ERROR = "execution_error"
    FEATURE_DISABLED = "feature_disabled"
    STALE_SNAPSHOT = "stale_snapshot"


# Statuses that may never contribute negative evidence or lower a thesis's
# standing on their own — confirmed directly from Sprint #002's own status
# table (Evidence Contract §5's "Negative evidence?" column).
NEVER_NEGATIVE_STATUSES = frozenset({
    EvidenceStatus.UNAVAILABLE,
    EvidenceStatus.NOT_APPLICABLE,
    EvidenceStatus.FEATURE_DISABLED,
    EvidenceStatus.EXECUTION_ERROR,
    EvidenceStatus.STALE_SNAPSHOT,
})

# Statuses that count as positive support for a thesis.
SUPPORTING_STATUSES = frozenset({EvidenceStatus.SUPPORTED})

# Statuses that count as opposing evidence (real, but not necessarily a veto).
OPPOSING_STATUSES = frozenset({EvidenceStatus.WARNING, EvidenceStatus.AVOID})


class HardGateType(str, Enum):
    """Per Sprint #002's Hard-Gate, Warning, and Veto Contract (§6) — a weak
    Valuation signal must never be tagged the same as a Financial Strength
    liquidity-distress veto."""

    NONE = "none"
    TRUE_VETO = "true_veto"
    CONDITIONAL_BLOCK = "conditional_block"


@dataclass(frozen=True)
class EngineEvidence:
    """One engine's normalized contribution to a
    RecommendationEvidenceSnapshot. Immutable (frozen) — confirms by
    construction that nothing downstream can mutate a captured engine
    result, per this sprint's "mutate no input objects" requirement.

    `score`/`grade`/`confidence` are passed through UNCHANGED from the
    source engine — RCI never recomputes them, only reads and tags them
    with a normalized status.

    `engine_version_provenance` and `currently_enforced` were added in
    Sprint #004's Contract Integrity Review, after that review found Sprint
    #003's original implementation conflated two genuinely different real
    states under one identical tag:

    - `engine_version_provenance` ("engine_reported" | "adapter_supplied_default"
      | "unknown") makes explicit when `engine_version` was supplied by the
      adapter (Business Quality, today) rather than reported by the engine
      itself — confirmed by Sprint #004's review that the un-flagged Sprint
      #003 shape could let an auditor wrongly believe Business Quality had
      emitted that version itself.
    - `currently_enforced` (bool) distinguishes a hard-gate tag that is
      genuinely acted on somewhere in production TODAY (Financial Strength's
      `liquidity_distress`, confirmed enforced via `daily_picks.py`'s own
      `"liquidity distress"` phrase check) from one that is merely computed
      and DESCRIBED, never enforced (Business Quality's `fraud_risk`/
      `distress_and_aggressive_accruals`, confirmed via Sprint #002/#003's
      own review that no downstream code references Business Quality at
      all). Sprint #003 tagged both identically as `HardGateType.TRUE_VETO`
      — a real, found defect, corrected here, not a hypothetical concern.
    """

    engine_name: str
    engine_version: str | None
    engine_version_provenance: str
    market: str | None
    sector_bucket: str | None
    score: float | None
    grade: str | None
    confidence: float | None
    status: EvidenceStatus
    hard_gate: HardGateType
    currently_enforced: bool
    positive_evidence: tuple[str, ...]
    negative_evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    reason_code: str | None
    data_completeness_pct: float | None


@dataclass(frozen=True)
class RecommendationEvidenceSnapshot:
    """The immutable, structured evidence available from a single
    Prediction Engine analysis run, captured BEFORE any RCI consolidation
    logic runs — per SSDS-009 §3's "Pre-Consolidation Evidence Snapshot"
    requirement. Contains only facts and statuses; never a newly
    calculated master score or signal (enforced by this dataclass's own
    field set — there is no field for either)."""

    contract_version: int
    snapshot_id: str
    analysis_timestamp: str
    market: str
    symbol: str
    sector_bucket: str | None
    engine_evidence: tuple[EngineEvidence, ...]
    is_snapshot: bool = False  # False = live; True = a frozen, historical capture


@dataclass(frozen=True)
class ConflictMatch:
    """One detected conflict-pattern match, per Sprint #002's Conflict
    Pattern Contract §7 (CP-01 through CP-08, V1 implements a 5-pattern
    subset — see recommendation_consolidation_engine.py)."""

    conflict_id: str
    headline: str
    narrative: str
    supporting_engines: tuple[str, ...]
    opposing_engines: tuple[str, ...]
    severity: str


@dataclass(frozen=True)
class RecommendationConsolidationResponse:
    """RCI's entire output — additive, explainability-focused only. Per
    this sprint's explicit rule, this dataclass has NO field for a
    replacement signal, a replacement confidence, or a blended master
    score — confirmed by the absence of any such field below, not merely
    by a comment promising one won't be added."""

    contract_version: int
    snapshot_id: str
    computed_at: str
    is_snapshot: bool
    thesis_state: str  # "supported" | "mixed" | "conflicted" | "insufficient_evidence"
    engine_agreement: str  # e.g. "3 of 4 applicable engines support this thesis"
    conflicts: tuple[ConflictMatch, ...]
    # Sprint #005's Structural Coverage Narrative Refinement: a market-
    # structural unavailability (e.g. Financial Strength has no India
    # coverage at all) is NEVER a "conflict" — it is a platform-level
    # coverage fact, true for every company in that market, never
    # evidence that THIS company has a weakness. Surfaced here,
    # separately from `conflicts`, using cautious, evidence-led wording
    # — never counted toward thesis_state, engine_agreement, or any
    # supporting/opposing evidence list.
    coverage_notices: tuple[str, ...]
    supporting_evidence: tuple[str, ...]
    opposing_evidence: tuple[str, ...]
    active_gates: tuple[str, ...]  # ONLY gates confirmed `currently_enforced=True` in production today
    unresolved_risk_flags: tuple[str, ...]  # computed, described, but NOT enforced anywhere (e.g. Business Quality fraud-risk)
    material_warnings: tuple[str, ...]
    evidence_completeness_pct: float | None
    explanation_confidence_category: str  # "high" | "moderate" | "low"
    narrative: str
    engine_versions_used: dict[str, str | None] = field(default_factory=dict)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
