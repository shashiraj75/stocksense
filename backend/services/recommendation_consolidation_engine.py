"""
Recommendation Consolidation Intelligence — Pure Core (Epic 005, Sprint #003).

`compute_recommendation_consolidation()` is a pure function: no network
calls, no environment-variable reads, no database writes, no mutation of
its input, no call into Prediction Engine code, no new master score, no
replacement signal, no replacement confidence. It reads a
RecommendationEvidenceSnapshot (already-captured, normalized engine
evidence) and returns a RecommendationConsolidationResponse — additive,
explainability-focused output only, per SSDS-009 §2/§8 and Sprint #002's
Evidence Contract §8.

V1 implements a deliberately small, 5-pattern subset of Sprint #002's
8-pattern conflict taxonomy (CP-02, CP-01, CP-03, CP-08, CP-07) — per this
sprint's own "start with no more than five patterns unless direct
evidence justifies more" instruction. The remaining three (CP-04, CP-05,
CP-06) are deferred, not implemented, named explicitly in the
accompanying Sprint #003 report.
"""

from services.recommendation_consolidation_contract import (
    EngineEvidence, EvidenceStatus, ConflictMatch, RecommendationConsolidationResponse,
    CONTRACT_VERSION, NEVER_NEGATIVE_STATUSES, SUPPORTING_STATUSES, OPPOSING_STATUSES,
    utc_now_iso,
)
from services.recommendation_consolidation_contract import RecommendationEvidenceSnapshot

_ENGINE_LABELS = {
    "business_quality": "Business Quality",
    "financial_strength": "Financial Strength",
    "growth_intelligence": "Growth Intelligence",
    "valuation_intelligence": "Valuation Intelligence",
}


def _label(engine_name: str) -> str:
    return _ENGINE_LABELS.get(engine_name, engine_name)


def _by_name(snapshot: RecommendationEvidenceSnapshot) -> dict[str, EngineEvidence]:
    return {e.engine_name: e for e in snapshot.engine_evidence}


def _detect_conflicts(by_name: dict[str, EngineEvidence]) -> list[ConflictMatch]:
    """Implements V1's 5-pattern subset. Each check is independent and
    deterministic — order of evaluation never affects which patterns are
    detected, only the (also deterministic) order they're returned in."""
    conflicts: list[ConflictMatch] = []

    bq = by_name.get("business_quality")
    fs = by_name.get("financial_strength")
    gi = by_name.get("growth_intelligence")
    vi = by_name.get("valuation_intelligence")

    # CP-02 — attractive Valuation + Growth avoid. The exact pattern
    # Epic 004 Sprint #005's RELINFRA finding validated against real
    # outcome data (a 73/100 score followed by a real -82.0% return) —
    # used here only as the evidence justifying this pattern's
    # inclusion, never as stock-specific hard-coded logic.
    if (vi and vi.status in (EvidenceStatus.SUPPORTED, EvidenceStatus.MIXED)
            and vi.grade in ("strong_buy", "buy")
            and gi and gi.status == EvidenceStatus.AVOID):
        conflicts.append(ConflictMatch(
            conflict_id="CP-02-cheap-but-avoid-growth",
            headline="Statistically cheap, but growth evidence raises a value-trap concern",
            narrative=(
                "Valuation Intelligence suggests this stock is undervalued, but Growth "
                "Intelligence independently flags weak or deteriorating growth — a pattern "
                "Epic 004's own outcome validation found associated with real value traps, "
                "not a contradiction to explain away."
            ),
            supporting_engines=("valuation_intelligence",),
            opposing_engines=("growth_intelligence",),
            severity="high",
        ))

    # CP-01 — high Business Quality + weak Financial Strength.
    if (bq and bq.grade in ("strong_buy", "buy")
            and fs and fs.status in (EvidenceStatus.AVOID, EvidenceStatus.WARNING)):
        conflicts.append(ConflictMatch(
            conflict_id="CP-01-quality-vs-strength",
            headline="Good business, fragile finances",
            narrative=(
                "Business Quality rates this a strong business, but Financial Strength "
                "raises real solvency concerns — business quality does not offset "
                "financial fragility."
            ),
            supporting_engines=("business_quality",),
            opposing_engines=("financial_strength",),
            severity="moderate",
        ))

    # CP-03 — strong Growth + expensive Valuation.
    if (gi and gi.grade in ("strong_buy", "buy")
            and vi and vi.status in (EvidenceStatus.AVOID, EvidenceStatus.WARNING)
            and vi.status != EvidenceStatus.FEATURE_DISABLED):
        conflicts.append(ConflictMatch(
            conflict_id="CP-03-growth-priced-in",
            headline="Quality growth, priced for it",
            narrative=(
                "Growth Intelligence is positive, but Valuation Intelligence finds the "
                "stock richly valued — a real, limited margin of safety, not a defect in "
                "either engine's reading (Epic 004 confirmed premium-growth names are "
                "correctly classified as expensive when they genuinely are)."
            ),
            supporting_engines=("growth_intelligence",),
            opposing_engines=("valuation_intelligence",),
            severity="moderate",
        ))

    # CP-07 — a key engine unavailable/not applicable. Purely descriptive,
    # never a judgment — explicitly allowed to fire even with only one
    # engine present, per Sprint #002's own taxonomy.
    missing = [e for e in by_name.values() if e.status in (EvidenceStatus.UNAVAILABLE, EvidenceStatus.NOT_APPLICABLE)]
    if missing:
        names = ", ".join(_label(e.engine_name) for e in missing)
        conflicts.append(ConflictMatch(
            conflict_id="CP-07-missing-engine",
            headline=f"{names} evidence not available for this company",
            narrative=(
                f"{names} could not be evaluated for this company (unavailable or not "
                "applicable for its market/sector) — this is an informational limitation, "
                "not treated as supporting or opposing evidence."
            ),
            supporting_engines=(), opposing_engines=(),
            severity="informational",
        ))

    # CP-08 — favorable available evidence, but low aggregate completeness.
    completeness_values = [e.data_completeness_pct for e in by_name.values() if e.data_completeness_pct is not None]
    avg_completeness = sum(completeness_values) / len(completeness_values) if completeness_values else None
    favorable = [e for e in by_name.values() if e.status == EvidenceStatus.SUPPORTED]
    if avg_completeness is not None and avg_completeness < 60.0 and favorable:
        conflicts.append(ConflictMatch(
            conflict_id="CP-08-low-completeness-favorable",
            headline="This conclusion rests on incomplete evidence",
            narrative=(
                f"Available evidence is favorable, but aggregate data completeness is low "
                f"({avg_completeness:.0f}%) — this caveat applies to the thesis itself, not "
                "hidden behind an otherwise-positive score."
            ),
            supporting_engines=tuple(e.engine_name for e in favorable), opposing_engines=(),
            severity="moderate",
        ))

    return conflicts


def _thesis_state(by_name: dict[str, EngineEvidence]) -> tuple[str, str]:
    """Returns (thesis_state, engine_agreement_text). Never a blended
    score — both are categorical, derived purely from status counts."""
    applicable = [e for e in by_name.values() if e.status not in (
        EvidenceStatus.NOT_APPLICABLE, EvidenceStatus.UNAVAILABLE, EvidenceStatus.EXECUTION_ERROR,
    )]
    supporting = [e for e in applicable if e.status in SUPPORTING_STATUSES]
    opposing = [e for e in applicable if e.status in OPPOSING_STATUSES]

    if not applicable:
        return "insufficient_evidence", "No applicable engine evidence available"

    agreement_text = f"{len(supporting)} of {len(applicable)} applicable engines support this thesis"
    if opposing:
        agreement_text += f"; {len(opposing)} raise concerns"

    if supporting and not opposing:
        state = "supported"
    elif opposing and not supporting:
        state = "conflicted" if len(opposing) < len(applicable) else "conflicted"
    elif supporting and opposing:
        state = "conflicted"
    else:
        state = "mixed"

    return state, agreement_text


def _explanation_confidence_category(by_name: dict[str, EngineEvidence], conflicts: list[ConflictMatch]) -> str:
    """A category (high/moderate/low), never a number — explicitly
    avoiding the false-precision risk SSDS-009 §4.A named for the
    rejected Weighted Composite model."""
    completeness_values = [e.data_completeness_pct for e in by_name.values() if e.data_completeness_pct is not None]
    avg_completeness = sum(completeness_values) / len(completeness_values) if completeness_values else 0.0
    has_high_severity_conflict = any(c.severity == "high" for c in conflicts)

    if has_high_severity_conflict:
        return "low"
    if avg_completeness >= 80.0:
        return "high"
    if avg_completeness >= 50.0:
        return "moderate"
    return "low"


def compute_recommendation_consolidation(
    snapshot: RecommendationEvidenceSnapshot,
) -> RecommendationConsolidationResponse:
    """The entire entry point. Deterministic: identical input always
    produces identical output (confirmed by golden-equivalent tests)."""
    by_name = _by_name(snapshot)

    conflicts = _detect_conflicts(by_name)
    thesis_state, engine_agreement = _thesis_state(by_name)

    supporting_evidence = tuple(
        f"{_label(e.engine_name)}: {e.positive_evidence[0]}" if e.positive_evidence
        else f"{_label(e.engine_name)} supports the thesis"
        for e in by_name.values() if e.status in SUPPORTING_STATUSES
    )
    opposing_evidence = tuple(
        f"{_label(e.engine_name)}: {e.negative_evidence[0]}" if e.negative_evidence
        else f"{_label(e.engine_name)} raises concerns"
        for e in by_name.values() if e.status in OPPOSING_STATUSES
    )
    # Sprint #004's Contract Integrity Review finding: a hard_gate tag
    # alone does not mean it is acted on anywhere in production today.
    # `active_gates` is restricted to currently_enforced=True (confirmed,
    # real examples: Financial Strength's liquidity_distress) -- never
    # claiming a gate is active unless it genuinely is, per this engine's
    # own permanent principle. A computed-but-unenforced flag (Business
    # Quality's fraud_risk) is surfaced separately, honestly, as
    # `unresolved_risk_flags`, never mislabeled as an active gate.
    active_gates = tuple(
        f"{_label(e.engine_name)}: {e.hard_gate.value} (enforced)"
        for e in by_name.values() if e.hard_gate.value != "none" and e.currently_enforced
    )
    unresolved_risk_flags = tuple(
        f"{_label(e.engine_name)}: {e.hard_gate.value} flag present, not currently enforced as an exclusion"
        for e in by_name.values() if e.hard_gate.value != "none" and not e.currently_enforced
    )
    material_warnings = tuple(
        f"{_label(e.engine_name)}: {e.warnings[0]}"
        for e in by_name.values() if e.warnings and e.status not in NEVER_NEGATIVE_STATUSES
    )

    completeness_values = [e.data_completeness_pct for e in by_name.values() if e.data_completeness_pct is not None]
    evidence_completeness_pct = (
        round(sum(completeness_values) / len(completeness_values), 1) if completeness_values else None
    )

    explanation_confidence_category = _explanation_confidence_category(by_name, conflicts)

    if conflicts:
        narrative = " ".join(c.narrative for c in conflicts)
    else:
        narrative = f"{engine_agreement}. No conflicting evidence pattern detected for this company."

    return RecommendationConsolidationResponse(
        contract_version=CONTRACT_VERSION,
        snapshot_id=snapshot.snapshot_id,
        computed_at=utc_now_iso(),
        is_snapshot=snapshot.is_snapshot,
        thesis_state=thesis_state,
        engine_agreement=engine_agreement,
        conflicts=tuple(conflicts),
        supporting_evidence=supporting_evidence,
        opposing_evidence=opposing_evidence,
        active_gates=active_gates,
        unresolved_risk_flags=unresolved_risk_flags,
        material_warnings=material_warnings,
        evidence_completeness_pct=evidence_completeness_pct,
        explanation_confidence_category=explanation_confidence_category,
        narrative=narrative,
        engine_versions_used={e.engine_name: e.engine_version for e in by_name.values()},
    )
