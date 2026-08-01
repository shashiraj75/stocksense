"""
Trade Postmortem Sprint 3A, Wave B Stage J4E — governed claim path.

Turns Wave A's governed conclusions (services.postmortem.governed_
price_path_conclusions.GovernedLevelTouchConclusion / GovernedOrder
Conclusion) into Sprint 1's governed EvidenceItem/PostmortemClaim
objects, for the CURRENT (1.2.0) report only.

Deliberately separate from price_path_claims.py: the historical 1.1.0
report path (build_touch_order_claim, the legacy TouchResult-based
claims) is frozen and remains the sole claim authority for 1.1.0
reports. Nothing in this module is ever invoked by 1.1.0 generation,
and nothing in price_path_claims.py is ever invoked for the 1.2.0
governed touch/order section — one report version, one claim
authority, never mixed (Gate "J4E — No Dual Semantic Authority").

Every rule this module can emit is registered in evidence.RULE_REGISTRY
at import time, matching price_path_claims.py's own convention.
"""
from services.postmortem.evidence import (
    ConfidenceBand,
    EvidenceClass,
    EvidenceItem,
    EvidenceVerificationLevel,
    FreshnessStatus,
    PostmortemClaim,
    RuleDefinition,
    SourceType,
    insufficient_evidence_claim,
    make_claim_id,
    make_evidence_id,
    register_rule,
)
from services.postmortem.governed_price_path_conclusions import (
    CANONICAL_FALLBACK,
    GOVERNED_ORDER_NEITHER_OBSERVED,
    GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN,
    GOVERNED_ORDER_STOP_ONLY_OBSERVED,
    GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET,
    GOVERNED_ORDER_TARGET_ONLY_OBSERVED,
    GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP,
    GOVERNED_ORDER_UNAVAILABLE,
    GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
    GOVERNED_TOUCH_INCOMPATIBLE_BASIS,
    GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE,
    GOVERNED_TOUCH_NO_BARS,
    GOVERNED_TOUCH_NO_COMPATIBLE_CROSSING,
    GOVERNED_TOUCH_NO_VALUE_SUPPLIED,
    GOVERNED_TOUCH_SUPPORTED,
    GovernedLevelTouchConclusion,
    GovernedOrderConclusion,
)
from services.postmortem.price_path_calculator import STOP_VALUE, TARGET_VALUE
from services.postmortem.price_path_identity import GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION

_REPORT_SECTION = "price_path"
_RULES_VERSION = GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION

_RULE_ID_BY_LEVEL_KIND = {TARGET_VALUE: "GOVERNED_TARGET_TOUCH", STOP_VALUE: "GOVERNED_STOP_TOUCH"}
_FACTOR_BY_LEVEL_KIND = {TARGET_VALUE: "target_touch", STOP_VALUE: "stop_touch"}

# Fallback touch statuses whose `detail` is already, by
# GovernedLevelTouchConclusion's own construction invariant, exactly
# CANONICAL_FALLBACK — safe to route through insufficient_evidence_claim
# unconditionally (item 4/5/6 of the required behavior list).
_TOUCH_FALLBACK_STATUSES = frozenset({
    GOVERNED_TOUCH_INCOMPATIBLE_BASIS, GOVERNED_TOUCH_NO_BARS,
    GOVERNED_TOUCH_INSUFFICIENT_EVIDENCE, GOVERNED_TOUCH_CONTRADICTORY_ENDPOINT_EVIDENCE,
})

for _level_kind, _rule_id in _RULE_ID_BY_LEVEL_KIND.items():
    register_rule(RuleDefinition(
        rule_id=_rule_id, rule_version=_RULES_VERSION, report_section=_REPORT_SECTION,
        required_evidence=("governed_level_touch_conclusion",), optional_evidence=(),
        supporting_condition="conclusion.status == SUPPORTED_TOUCH or a definitive NO_COMPATIBLE_CROSSING/NO_VALUE_SUPPLIED negative",
        opposing_condition="never opposed — a per-level touch conclusion is never contradicted by itself",
        insufficient_evidence_condition="conclusion.status in (INCOMPATIBLE_BASIS, NO_BARS, INSUFFICIENT_EVIDENCE, CONTRADICTORY_ENDPOINT_EVIDENCE)",
        permitted_output_classes=(
            EvidenceClass.DIRECTLY_OBSERVED.value, EvidenceClass.MECHANICALLY_VERIFIED.value,
            EvidenceClass.INSUFFICIENT_EVIDENCE.value,
        ),
    ))

register_rule(RuleDefinition(
    rule_id="GOVERNED_TOUCH_ORDER", rule_version=_RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("governed_order_conclusion",), optional_evidence=(),
    supporting_condition="conclusion.status not in (ORDER_UNAVAILABLE,)",
    opposing_condition="conclusion.status == SAME_BAR_ORDER_UNKNOWN (intrabar order genuinely ambiguous)",
    insufficient_evidence_condition="conclusion.status == ORDER_UNAVAILABLE",
    permitted_output_classes=(
        EvidenceClass.MECHANICALLY_VERIFIED.value, EvidenceClass.EVIDENCE_SUPPORTED.value,
        EvidenceClass.CONFLICTING_EVIDENCE.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value,
    ),
))


def _touch_evidence_item(trade_id: int, level_kind: str, conclusion: GovernedLevelTouchConclusion) -> EvidenceItem:
    factor = _FACTOR_BY_LEVEL_KIND[level_kind]
    return EvidenceItem(
        evidence_id=make_evidence_id(trade_id, _REPORT_SECTION, f"governed_{factor}"),
        category=_REPORT_SECTION, name=f"governed_{factor}",
        value=conclusion.status, units=None, observation_timestamp=None,
        source="governed_price_path_conclusions.classify_governed_level_touch",
        source_type=SourceType.SERVER_DERIVED.value,
        verification_level=EvidenceVerificationLevel.MECHANICALLY_VERIFIED.value,
        freshness_status=FreshnessStatus.NOT_APPLICABLE.value,
        limitations=[conclusion.detail] if conclusion.status != GOVERNED_TOUCH_SUPPORTED else [],
    )


def build_governed_touch_claim(
    trade_id: int, level_kind: str, conclusion: GovernedLevelTouchConclusion,
) -> tuple[list[EvidenceItem], PostmortemClaim]:
    """The sole claim-construction path for a governed per-level touch
    conclusion (Gate 'J4E — Claim Contract', items 1-3). Never invoked
    with a legacy TouchResult — level_kind/conclusion must come from
    classify_governed_level_touch itself."""
    rule_id = _RULE_ID_BY_LEVEL_KIND[level_kind]
    factor = _FACTOR_BY_LEVEL_KIND[level_kind]

    if conclusion.status in _TOUCH_FALLBACK_STATUSES:
        assert conclusion.detail == CANONICAL_FALLBACK  # guaranteed by GovernedLevelTouchConclusion itself
        claim = insufficient_evidence_claim(
            claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor=factor,
            rule_id=rule_id, rule_version=_RULES_VERSION,
            missing_evidence=[f"governed_{factor}"], limitations=[conclusion.status],
        )
        return [], claim

    item = _touch_evidence_item(trade_id, level_kind, conclusion)

    if conclusion.status == GOVERNED_TOUCH_NO_VALUE_SUPPLIED:
        claim = PostmortemClaim(
            claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor=factor,
            claim_text=conclusion.detail,
            evidence_class=EvidenceClass.MECHANICALLY_VERIFIED.value, confidence_band=ConfidenceBand.HIGH.value,
            supporting_evidence_ids=[item.evidence_id], opposing_evidence_ids=[],
            missing_evidence=[], contradiction_flags=[],
            rule_id=rule_id, rule_version=_RULES_VERSION,
        )
        return [item], claim

    # SUPPORTED_TOUCH (positive) or NO_COMPATIBLE_CROSSING (definitive
    # governed negative — reachable, by governed_price_path_conclusions'
    # own single-source-eligibility guarantee, ONLY under complete
    # GOVERNED_UNMODIFIED evidence, never as a guess).
    claim = PostmortemClaim(
        claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor=factor,
        claim_text=conclusion.detail,
        evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value,
        confidence_band=ConfidenceBand.HIGH.value,
        supporting_evidence_ids=[item.evidence_id], opposing_evidence_ids=[],
        missing_evidence=[], contradiction_flags=[],
        rule_id=rule_id, rule_version=_RULES_VERSION,
    )
    return [item], claim


def build_governed_order_claim(
    trade_id: int, conclusion: GovernedOrderConclusion,
    target_evidence_ids: list[str], stop_evidence_ids: list[str],
) -> tuple[list[EvidenceItem], PostmortemClaim]:
    """The sole claim-construction path for the governed order
    conclusion. `target_evidence_ids`/`stop_evidence_ids` are the
    evidence_id(s) already produced by build_governed_touch_claim for
    each side, reused here rather than re-derived, so a claim never
    cites an evidence_id that doesn't already exist in the same report
    (Gate 'J4E — Claim Contract', item 12)."""
    rule_id = "GOVERNED_TOUCH_ORDER"

    if conclusion.status == GOVERNED_ORDER_UNAVAILABLE:
        assert conclusion.detail == CANONICAL_FALLBACK
        claim = insufficient_evidence_claim(
            claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor="touch_order",
            rule_id=rule_id, rule_version=_RULES_VERSION,
            missing_evidence=["governed_order_conclusion"],
            limitations=[conclusion.status],
        )
        return [], claim

    if conclusion.status == GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN:
        supporting = target_evidence_ids + stop_evidence_ids
        claim = PostmortemClaim(
            claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor="touch_order",
            claim_text=conclusion.detail,
            evidence_class=EvidenceClass.CONFLICTING_EVIDENCE.value, confidence_band=ConfidenceBand.LOW.value,
            supporting_evidence_ids=supporting, opposing_evidence_ids=supporting,
            missing_evidence=[], contradiction_flags=[GOVERNED_ORDER_SAME_BAR_ORDER_UNKNOWN],
            rule_id=rule_id, rule_version=_RULES_VERSION,
            limitations=[conclusion.detail],
        )
        return [], claim

    if conclusion.status == GOVERNED_ORDER_NEITHER_OBSERVED:
        supporting = target_evidence_ids + stop_evidence_ids
        claim = PostmortemClaim(
            claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor="touch_order",
            claim_text=conclusion.detail,
            evidence_class=EvidenceClass.MECHANICALLY_VERIFIED.value, confidence_band=ConfidenceBand.HIGH.value,
            supporting_evidence_ids=supporting, opposing_evidence_ids=[],
            missing_evidence=[], contradiction_flags=[],
            rule_id=rule_id, rule_version=_RULES_VERSION,
        )
        return [], claim

    if conclusion.status in (GOVERNED_ORDER_TARGET_ONLY_OBSERVED, GOVERNED_ORDER_STOP_ONLY_OBSERVED):
        supporting = target_evidence_ids if conclusion.status == GOVERNED_ORDER_TARGET_ONLY_OBSERVED else stop_evidence_ids
        claim = PostmortemClaim(
            claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor="touch_order",
            claim_text=conclusion.detail,
            evidence_class=EvidenceClass.EVIDENCE_SUPPORTED.value, confidence_band=ConfidenceBand.MODERATE.value,
            supporting_evidence_ids=supporting, opposing_evidence_ids=[],
            missing_evidence=[], contradiction_flags=[],
            rule_id=rule_id, rule_version=_RULES_VERSION,
        )
        return [], claim

    # Ordered pattern: TARGET_SAFELY_BEFORE_STOP / STOP_SAFELY_BEFORE_TARGET.
    assert conclusion.status in (GOVERNED_ORDER_TARGET_SAFELY_BEFORE_STOP, GOVERNED_ORDER_STOP_SAFELY_BEFORE_TARGET)
    supporting = target_evidence_ids + stop_evidence_ids
    claim = PostmortemClaim(
        claim_id=make_claim_id(trade_id, rule_id), report_section=_REPORT_SECTION, factor="touch_order",
        claim_text=conclusion.detail,
        evidence_class=EvidenceClass.EVIDENCE_SUPPORTED.value, confidence_band=ConfidenceBand.HIGH.value,
        supporting_evidence_ids=supporting, opposing_evidence_ids=[],
        missing_evidence=[], contradiction_flags=[],
        rule_id=rule_id, rule_version=_RULES_VERSION,
    )
    return [], claim
