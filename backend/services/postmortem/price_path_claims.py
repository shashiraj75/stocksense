"""
Price-path claim-level provenance — Trade Postmortem Sprint 3A, Stages 9/10.

Turns the pure computation results from price_path_calculator.py into
Sprint 1's governed EvidenceItem/PostmortemClaim objects. Every rule this
module can emit is registered in evidence.RULE_REGISTRY at import time —
no inline orphan rule IDs (Stage 10's own explicit requirement).
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
from services.postmortem.price_path_calculator import (
    BOTH_SAME_BAR_AMBIGUOUS,
    BOUNDARY_BAR_AMBIGUOUS,
    EXCURSION_COMPLETE,
    ExcursionResult,
    INSUFFICIENT_EVIDENCE as TOUCH_ORDER_INSUFFICIENT_EVIDENCE,
    LEVEL_HISTORY_INCOMPLETE,
    NEITHER_OBSERVED,
    RULES_VERSION,
    STOP_BEFORE_TARGET,
    STOP_ONLY,
    TARGET_BEFORE_STOP,
    TARGET_ONLY,
    TouchResult,
)
from services.postmortem.price_path_evidence import PricePathEvidenceBundle

_REPORT_SECTION = "price_path"

# --- Stage 10: rule registry (registered once at import time) ---
register_rule(RuleDefinition(
    rule_id="PRICE_PATH_WINDOW", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("price_path_evidence_bundle",), optional_evidence=(),
    supporting_condition="bundle.data_completeness == COMPLETE",
    opposing_condition="never opposed — descriptive, not evaluative",
    insufficient_evidence_condition="bundle.data_completeness in (UNAVAILABLE, AMBIGUOUS_RESOLUTION)",
    permitted_output_classes=(EvidenceClass.MECHANICALLY_VERIFIED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="MFE", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("interior_bars",), optional_evidence=(),
    supporting_condition="excursion.evidence_completeness == COMPLETE",
    opposing_condition="never opposed",
    insufficient_evidence_condition="no interior bars, indeterminate entry price, or incompatible basis",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="MAE", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("interior_bars",), optional_evidence=(),
    supporting_condition="excursion.evidence_completeness == COMPLETE",
    opposing_condition="never opposed",
    insufficient_evidence_condition="no interior bars, indeterminate entry price, or incompatible basis",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="TARGET_TOUCH", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("applicable_target", "bars"), optional_evidence=(),
    supporting_condition="a bar's high >= applicable_target",
    opposing_condition="never opposed — a touch is DIRECTLY_OBSERVED or absent",
    insufficient_evidence_condition="applicable_target is None",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="STOP_TOUCH", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("applicable_stop", "bars"), optional_evidence=(),
    supporting_condition="a bar's low <= applicable_stop",
    opposing_condition="never opposed — a touch is DIRECTLY_OBSERVED or absent",
    insufficient_evidence_condition="applicable_stop is None",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="TOUCH_ORDER", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("target_touch", "stop_touch"), optional_evidence=(),
    supporting_condition="touches occur on different, non-boundary bars",
    opposing_condition="never opposed",
    insufficient_evidence_condition="neither level configured",
    permitted_output_classes=(EvidenceClass.EVIDENCE_SUPPORTED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="SAME_BAR_AMBIGUITY", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("target_touch", "stop_touch"), optional_evidence=(),
    supporting_condition="both touches occur on the identical session_date",
    opposing_condition="never opposed — ambiguity is asserted, not evaluated for/against",
    insufficient_evidence_condition="not applicable when this rule fires",
    permitted_output_classes=(EvidenceClass.EVIDENCE_SUPPORTED.value,),
))
register_rule(RuleDefinition(
    rule_id="ENTRY_BOUNDARY_AMBIGUITY", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("entry_bar",), optional_evidence=(),
    supporting_condition="a touch's first_observed_bar is the entry-date bar",
    opposing_condition="never opposed",
    insufficient_evidence_condition="not applicable when this rule fires",
    permitted_output_classes=(EvidenceClass.EVIDENCE_SUPPORTED.value,),
))
register_rule(RuleDefinition(
    rule_id="EXIT_BOUNDARY_AMBIGUITY", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("exit_bar",), optional_evidence=(),
    supporting_condition="a touch's first_observed_bar is the exit-date bar",
    opposing_condition="never opposed",
    insufficient_evidence_condition="not applicable when this rule fires",
    permitted_output_classes=(EvidenceClass.EVIDENCE_SUPPORTED.value,),
))
register_rule(RuleDefinition(
    rule_id="GAP_THROUGH_LEVEL", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("touch",), optional_evidence=(),
    supporting_condition="the touching bar's open itself was already beyond the level",
    opposing_condition="never opposed",
    insufficient_evidence_condition="not applicable when this rule fires",
    permitted_output_classes=(EvidenceClass.DIRECTLY_OBSERVED.value,),
))
register_rule(RuleDefinition(
    rule_id="MFE_GIVEBACK", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("excursion", "exit_price"), optional_evidence=(),
    supporting_condition="excursion.evidence_completeness == COMPLETE and exit_price is known",
    opposing_condition="never opposed",
    insufficient_evidence_condition="excursion incomplete or exit_price unknown",
    permitted_output_classes=(EvidenceClass.EVIDENCE_SUPPORTED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="CAPTURED_MFE", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("excursion", "exit_price"), optional_evidence=(),
    supporting_condition="mfe_abs > 0 and P&L is determinate and basis is compatible",
    opposing_condition="never opposed",
    insufficient_evidence_condition="mfe_abs <= 0, indeterminate P&L, or incompatible basis",
    permitted_output_classes=(EvidenceClass.EVIDENCE_SUPPORTED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="PRICE_BASIS_COMPATIBILITY", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("bundle.price_adjustment_basis",), optional_evidence=(),
    supporting_condition="adjustment_basis is UNADJUSTED and no split occurred in-window",
    opposing_condition="a split occurred in-window",
    insufficient_evidence_condition="adjustment_basis is UNKNOWN_ADJUSTMENT",
    permitted_output_classes=(EvidenceClass.MECHANICALLY_VERIFIED.value, EvidenceClass.INSUFFICIENT_EVIDENCE.value),
))
register_rule(RuleDefinition(
    rule_id="MISSING_LEVEL_HISTORY", rule_version=RULES_VERSION, report_section=_REPORT_SECTION,
    required_evidence=("level_history_complete",), optional_evidence=(),
    supporting_condition="never applicable — this rule only ever produces a limitation, not a positive claim",
    opposing_condition="never opposed",
    insufficient_evidence_condition="level_history_complete is False",
    permitted_output_classes=(EvidenceClass.INSUFFICIENT_EVIDENCE.value,),
))


def _bar_evidence_item(trade_id: int, bar, name: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=make_evidence_id(trade_id, "PRICE_PATH", name),
        category="PRICE_PATH", name=name, value={"high": bar.high, "low": bar.low, "session_date": bar.session_date.isoformat()},
        units=None, observation_timestamp=bar.timestamp, source=bar.source_id,
        source_type=SourceType.EXTERNAL_UNOFFICIAL_DAILY.value,
        verification_level=EvidenceVerificationLevel.DIRECTLY_OBSERVED.value,
        freshness_status=FreshnessStatus.POINT_IN_TIME_VALID.value, limitations=[],
    )


def build_mfe_claim(trade_id: int, excursion: ExcursionResult) -> tuple[list[EvidenceItem], PostmortemClaim]:
    if excursion.evidence_completeness != EXCURSION_COMPLETE:
        claim = insufficient_evidence_claim(
            claim_id=make_claim_id(trade_id, "MFE"), report_section=_REPORT_SECTION, factor="mfe",
            rule_id="MFE", rule_version=RULES_VERSION,
            missing_evidence=["interior_bars"], limitations=list(excursion.limitations),
        )
        return [], claim
    item = EvidenceItem(
        evidence_id=make_evidence_id(trade_id, "PRICE_PATH", "mfe"), category="PRICE_PATH", name="mfe",
        value=excursion.mfe_price, units="price", observation_timestamp=excursion.mfe_timestamp_first_observed,
        source="price_path_evidence", source_type=SourceType.EXTERNAL_UNOFFICIAL_DAILY.value,
        verification_level=EvidenceVerificationLevel.DIRECTLY_OBSERVED.value,
        freshness_status=FreshnessStatus.POINT_IN_TIME_VALID.value, limitations=[],
    )
    claim = PostmortemClaim(
        claim_id=make_claim_id(trade_id, "MFE"), report_section=_REPORT_SECTION, factor="mfe",
        claim_text=f"Maximum favorable excursion reached {excursion.mfe_pct:.2f}% ({excursion.mfe_abs:+.2f}) above entry.",
        evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.HIGH.value,
        supporting_evidence_ids=[item.evidence_id], opposing_evidence_ids=[], missing_evidence=[],
        contradiction_flags=[], rule_id="MFE", rule_version=RULES_VERSION,
    )
    return [item], claim


def build_mae_claim(trade_id: int, excursion: ExcursionResult) -> tuple[list[EvidenceItem], PostmortemClaim]:
    if excursion.evidence_completeness != EXCURSION_COMPLETE:
        claim = insufficient_evidence_claim(
            claim_id=make_claim_id(trade_id, "MAE"), report_section=_REPORT_SECTION, factor="mae",
            rule_id="MAE", rule_version=RULES_VERSION,
            missing_evidence=["interior_bars"], limitations=list(excursion.limitations),
        )
        return [], claim
    item = EvidenceItem(
        evidence_id=make_evidence_id(trade_id, "PRICE_PATH", "mae"), category="PRICE_PATH", name="mae",
        value=excursion.mae_price, units="price", observation_timestamp=excursion.mae_timestamp_first_observed,
        source="price_path_evidence", source_type=SourceType.EXTERNAL_UNOFFICIAL_DAILY.value,
        verification_level=EvidenceVerificationLevel.DIRECTLY_OBSERVED.value,
        freshness_status=FreshnessStatus.POINT_IN_TIME_VALID.value, limitations=[],
    )
    claim = PostmortemClaim(
        claim_id=make_claim_id(trade_id, "MAE"), report_section=_REPORT_SECTION, factor="mae",
        claim_text=f"Maximum adverse excursion reached {excursion.mae_magnitude_pct:.2f}% ({excursion.mae_magnitude_abs:.2f}) below entry.",
        evidence_class=EvidenceClass.DIRECTLY_OBSERVED.value, confidence_band=ConfidenceBand.HIGH.value,
        supporting_evidence_ids=[item.evidence_id], opposing_evidence_ids=[], missing_evidence=[],
        contradiction_flags=[], rule_id="MAE", rule_version=RULES_VERSION,
    )
    return [item], claim


def build_touch_order_claim(
    trade_id: int, order: str, target_touch: TouchResult, stop_touch: TouchResult,
) -> tuple[list[EvidenceItem], PostmortemClaim]:
    items: list[EvidenceItem] = []
    supporting: list[str] = []
    if target_touch.touched:
        items.append(_bar_evidence_item(trade_id, target_touch.first_observed_bar, "target_touch_bar"))
        supporting.append(items[-1].evidence_id)
    if stop_touch.touched:
        items.append(_bar_evidence_item(trade_id, stop_touch.first_observed_bar, "stop_touch_bar"))
        supporting.append(items[-1].evidence_id)

    if order in (TOUCH_ORDER_INSUFFICIENT_EVIDENCE, NEITHER_OBSERVED):
        claim = insufficient_evidence_claim(
            claim_id=make_claim_id(trade_id, "TOUCH_ORDER"), report_section=_REPORT_SECTION, factor="touch_order",
            rule_id="TOUCH_ORDER", rule_version=RULES_VERSION,
            missing_evidence=["target_touch", "stop_touch"],
            limitations=["neither stop nor target level produced a determinable touch"],
        )
        return [], claim

    text_by_order = {
        TARGET_BEFORE_STOP: "The target level was observed touched before the stop level.",
        STOP_BEFORE_TARGET: "The stop level was observed touched before the target level.",
        TARGET_ONLY: "Only the target level was observed touched during the holding period.",
        STOP_ONLY: "Only the stop level was observed touched during the holding period.",
        BOTH_SAME_BAR_AMBIGUOUS: (
            "Both the stop and target levels were touched within the same daily bar — which occurred "
            "first cannot be determined from daily OHLC data alone."
        ),
        BOUNDARY_BAR_AMBIGUOUS: (
            "A stop/target touch occurred in the entry or exit session's own bar, whose price action "
            "cannot be confidently attributed to the in-trade portion of that session."
        ),
        LEVEL_HISTORY_INCOMPLETE: (
            "Stop/target levels may have been edited during the trade and only entry and final values "
            "are known, so a full level-change history is not available to determine touch order."
        ),
    }
    ambiguous = order in (BOTH_SAME_BAR_AMBIGUOUS, BOUNDARY_BAR_AMBIGUOUS, LEVEL_HISTORY_INCOMPLETE)
    claim = PostmortemClaim(
        claim_id=make_claim_id(trade_id, "TOUCH_ORDER"), report_section=_REPORT_SECTION, factor="touch_order",
        claim_text=text_by_order[order],
        evidence_class=(EvidenceClass.CONFLICTING_EVIDENCE.value if ambiguous else EvidenceClass.EVIDENCE_SUPPORTED.value),
        confidence_band=(ConfidenceBand.LOW.value if ambiguous else ConfidenceBand.MODERATE.value),
        supporting_evidence_ids=supporting, opposing_evidence_ids=(supporting if order == BOTH_SAME_BAR_AMBIGUOUS else []),
        missing_evidence=[], contradiction_flags=([order] if ambiguous else []),
        rule_id="TOUCH_ORDER", rule_version=RULES_VERSION,
        limitations=[text_by_order[order]] if ambiguous else [],
    )
    return items, claim
