"""
Exit-snapshot evidence integration — Trade Postmortem Engine, Sprint 2
correction phase.

Problem this solves: the original generation_service only ever received
`exit_snapshot_present: bool` — a report could tell you WHETHER an exit
snapshot existed, but never WHAT it recorded. This module turns an
`ExitSnapshot` (services.postmortem.exit_snapshot) into claim/evidence
objects using Sprint 1's governed vocabulary (services.postmortem.
evidence — EvidenceItem/PostmortemClaim/make_evidence_id/make_claim_id),
so exit-time facts enter a report with the same claim-level provenance
every other factor already has, never as unverified prose copied
straight from the row.

Trust boundary, unchanged from exit_snapshot.py's own: fields the
close-time database transaction itself computed and stored (financial_
outcome, closure_classification, exit_mechanism, exit_price, exit_
quantity, closed_at, final_stop_loss, final_target_price, management_
mode, levels_modified_after_entry) are SERVER_STORED/MECHANICALLY_
VERIFIED — the backend computed and wrote them at the moment of close,
nothing about them was merely reported by a client. `trigger_timing_
verification`, by contrast, reflects whether a browser-detected stop/
target trigger was ever corroborated by a genuine server-side
observation (it never is today — see exit_snapshot.py's own trust-
boundary note) — that fact is reported honestly as CLIENT_REPORTED /
UNVERIFIED, never upgraded.
"""

from services.postmortem.evidence import (
    ConfidenceBand,
    EvidenceClass,
    EvidenceItem,
    EvidenceVerificationLevel,
    FreshnessStatus,
    PostmortemClaim,
    SourceType,
    insufficient_evidence_claim,
    make_claim_id,
    make_evidence_id,
)
from services.postmortem.exit_snapshot import ExitSnapshot, TriggerTimingVerification

EXIT_EVIDENCE_RULES_VERSION = "1.0.0"

_REPORT_SECTION = "exit_evidence"


def _server_stored_item(trade_id: int, name: str, value, *, units: str | None = None) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=make_evidence_id(trade_id, "EXIT", name),
        category="EXIT",
        name=name,
        value=value,
        units=units,
        observation_timestamp=None,
        source="paper_trade_exit_snapshot",
        source_type=SourceType.SERVER_STORED.value,
        verification_level=EvidenceVerificationLevel.MECHANICALLY_VERIFIED.value,
        freshness_status=FreshnessStatus.POINT_IN_TIME_VALID.value,
        limitations=[],
    )


def build_exit_evidence(trade_id: int, exit_snapshot: ExitSnapshot | None) -> tuple[list[EvidenceItem], list[PostmortemClaim]]:
    """Pure function: no I/O. Returns (evidence_items, claims) built from
    an already-loaded ExitSnapshot. When `exit_snapshot` is None (a
    historical trade closed before this sprint's durable close lifecycle
    existed), returns a single INSUFFICIENT_EVIDENCE claim using the
    exact fallback sentence — never a fabricated exit fact."""
    if exit_snapshot is None:
        claim = insufficient_evidence_claim(
            claim_id=make_claim_id(trade_id, "EXIT_EVIDENCE_MISSING_V1"),
            report_section=_REPORT_SECTION,
            factor="exit_evidence",
            rule_id="EXIT_EVIDENCE_MISSING_V1",
            rule_version=EXIT_EVIDENCE_RULES_VERSION,
            missing_evidence=["paper_trade_exit_snapshot"],
            limitations=["no exit snapshot exists for this trade — it closed before Sprint 2's durable close lifecycle existed"],
        )
        return [], [claim]

    evidence_items: list[EvidenceItem] = []
    claims: list[PostmortemClaim] = []

    # --- Claim 1: what kind of event closed the trade, and its financial outcome ---
    outcome_item = _server_stored_item(trade_id, "financial_outcome", exit_snapshot.financial_outcome)
    closure_item = _server_stored_item(trade_id, "closure_classification", exit_snapshot.closure_classification)
    mechanism_item = _server_stored_item(trade_id, "exit_mechanism", exit_snapshot.exit_mechanism)
    mechanism_raw_item = _server_stored_item(trade_id, "exit_mechanism_raw", exit_snapshot.exit_mechanism_raw)
    evidence_items += [outcome_item, closure_item, mechanism_item, mechanism_raw_item]
    claims.append(PostmortemClaim(
        claim_id=make_claim_id(trade_id, "EXIT_MECHANISM_V1"),
        report_section=_REPORT_SECTION,
        factor="exit_mechanism",
        claim_text=(
            f"This trade closed via {exit_snapshot.exit_mechanism} "
            f"({exit_snapshot.closure_classification}), with a financial outcome of {exit_snapshot.financial_outcome}."
        ),
        evidence_class=EvidenceClass.MECHANICALLY_VERIFIED.value,
        confidence_band=ConfidenceBand.HIGH.value,
        supporting_evidence_ids=[outcome_item.evidence_id, closure_item.evidence_id, mechanism_item.evidence_id],
        opposing_evidence_ids=[],
        missing_evidence=[],
        contradiction_flags=[],
        rule_id="EXIT_MECHANISM_V1",
        rule_version=EXIT_EVIDENCE_RULES_VERSION,
    ))

    # --- Claim 2: exact execution facts (price, quantity, timestamp) ---
    price_item = _server_stored_item(trade_id, "exit_price", exit_snapshot.exit_price)
    qty_item = _server_stored_item(trade_id, "exit_quantity", exit_snapshot.exit_quantity)
    closed_at_item = _server_stored_item(
        trade_id, "closed_at", exit_snapshot.closed_at.isoformat() if exit_snapshot.closed_at else None
    )
    evidence_items += [price_item, qty_item, closed_at_item]
    claims.append(PostmortemClaim(
        claim_id=make_claim_id(trade_id, "EXIT_EXECUTION_V1"),
        report_section=_REPORT_SECTION,
        factor="exit_execution",
        claim_text=(
            f"The position was closed at {exit_snapshot.exit_price} for {exit_snapshot.exit_quantity} "
            f"units on {exit_snapshot.closed_at.isoformat() if exit_snapshot.closed_at else 'an unrecorded timestamp'}."
        ),
        evidence_class=EvidenceClass.MECHANICALLY_VERIFIED.value,
        confidence_band=ConfidenceBand.HIGH.value,
        supporting_evidence_ids=[price_item.evidence_id, qty_item.evidence_id, closed_at_item.evidence_id],
        opposing_evidence_ids=[],
        missing_evidence=[],
        contradiction_flags=[],
        rule_id="EXIT_EXECUTION_V1",
        rule_version=EXIT_EVIDENCE_RULES_VERSION,
    ))

    # --- Claim 3: risk-management levels in effect at close ---
    mgmt_items = [
        _server_stored_item(trade_id, "final_stop_loss", exit_snapshot.final_stop_loss),
        _server_stored_item(trade_id, "final_target_price", exit_snapshot.final_target_price),
        _server_stored_item(trade_id, "management_mode", exit_snapshot.management_mode),
        _server_stored_item(trade_id, "levels_modified_after_entry", exit_snapshot.levels_modified_after_entry),
    ]
    evidence_items += mgmt_items
    claims.append(PostmortemClaim(
        claim_id=make_claim_id(trade_id, "EXIT_MANAGEMENT_LEVELS_V1"),
        report_section=_REPORT_SECTION,
        factor="exit_management_levels",
        claim_text=(
            f"At close, the trade was under {exit_snapshot.management_mode} management with stop-loss "
            f"{exit_snapshot.final_stop_loss} and target {exit_snapshot.final_target_price}."
        ),
        evidence_class=EvidenceClass.MECHANICALLY_VERIFIED.value,
        confidence_band=ConfidenceBand.HIGH.value,
        supporting_evidence_ids=[item.evidence_id for item in mgmt_items],
        opposing_evidence_ids=[],
        missing_evidence=[],
        contradiction_flags=[],
        rule_id="EXIT_MANAGEMENT_LEVELS_V1",
        rule_version=EXIT_EVIDENCE_RULES_VERSION,
    ))

    # --- Claim 4: trigger timing trust — never upgraded beyond what's genuinely known ---
    trigger_verification_level = (
        EvidenceVerificationLevel.CLIENT_REPORTED.value
        if exit_snapshot.trigger_timing_verification == TriggerTimingVerification.CLIENT_REPORTED_UNVERIFIED.value
        else EvidenceVerificationLevel.UNAVAILABLE.value
    )
    trigger_source_type = (
        SourceType.CLIENT_REPORTED.value
        if exit_snapshot.trigger_timing_verification == TriggerTimingVerification.CLIENT_REPORTED_UNVERIFIED.value
        else SourceType.UNAVAILABLE.value
    )
    trigger_limitations = []
    if exit_snapshot.trigger_timing_verification == TriggerTimingVerification.CLIENT_REPORTED_UNVERIFIED.value:
        trigger_limitations = [
            "the auto-close trigger was detected client-side (browser) — no server-side observation "
            "exists to corroborate the reported trigger price/timestamp"
        ]
    trigger_item = EvidenceItem(
        evidence_id=make_evidence_id(trade_id, "EXIT", "trigger_timing_verification"),
        category="EXIT",
        name="trigger_timing_verification",
        value=exit_snapshot.trigger_timing_verification,
        units=None,
        observation_timestamp=exit_snapshot.trigger_observation_timestamp,
        source="paper_trade_exit_snapshot",
        source_type=trigger_source_type,
        verification_level=trigger_verification_level,
        freshness_status=(
            FreshnessStatus.NOT_APPLICABLE.value
            if exit_snapshot.trigger_timing_verification == TriggerTimingVerification.NOT_APPLICABLE.value
            else FreshnessStatus.POINT_IN_TIME_VALID.value
        ),
        limitations=trigger_limitations,
    )
    evidence_items.append(trigger_item)

    if exit_snapshot.trigger_timing_verification == TriggerTimingVerification.NOT_APPLICABLE.value:
        # A manual close has no trigger to verify at all — this is not a
        # gap in evidence, it's a category that doesn't apply; still
        # represented as a claim (never silently omitted) but with
        # NOT_ASSESSABLE handled via its own non-INSUFFICIENT wording.
        claims.append(PostmortemClaim(
            claim_id=make_claim_id(trade_id, "EXIT_TRIGGER_TIMING_V1"),
            report_section=_REPORT_SECTION,
            factor="exit_trigger_timing",
            claim_text="This trade was closed manually — there is no automated trigger timing to verify.",
            evidence_class=EvidenceClass.MECHANICALLY_VERIFIED.value,
            confidence_band=ConfidenceBand.HIGH.value,
            supporting_evidence_ids=[trigger_item.evidence_id],
            opposing_evidence_ids=[],
            missing_evidence=[],
            contradiction_flags=[],
            rule_id="EXIT_TRIGGER_TIMING_V1",
            rule_version=EXIT_EVIDENCE_RULES_VERSION,
        ))
    else:
        claims.append(PostmortemClaim(
            claim_id=make_claim_id(trade_id, "EXIT_TRIGGER_TIMING_V1"),
            report_section=_REPORT_SECTION,
            factor="exit_trigger_timing",
            claim_text=(
                "This trade's automated exit trigger was detected by the client (browser) and reported to "
                "the server — it has never been independently verified by a server-side observation."
            ),
            evidence_class=EvidenceClass.EVIDENCE_SUPPORTED.value,
            confidence_band=ConfidenceBand.LOW.value,
            supporting_evidence_ids=[trigger_item.evidence_id],
            opposing_evidence_ids=[],
            missing_evidence=[],
            contradiction_flags=[],
            rule_id="EXIT_TRIGGER_TIMING_V1",
            rule_version=EXIT_EVIDENCE_RULES_VERSION,
            limitations=trigger_limitations,
        ))

    return evidence_items, claims
