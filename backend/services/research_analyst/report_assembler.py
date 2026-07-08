"""
AI Equity Research Analyst — Phase 2: deterministic ResearchReportAssembler.

Maps one VALIDATED LiveIntelligenceSnapshot into the fixed Phase 2
six-section report (Phase 2 spec §4/§5). Presentation derivations only
(Engineering Contract §5.3): selection, grouping, ordering, formatting,
and plain per-status counts. No calculation, no new metric, no
probability, no scenario weighting, no synthesis of a conclusion an
owner did not itself supply — the report never carries a value the
snapshot does not.

Fail-closed for ungrounded research (Contract §13.1): the assembler
validates the snapshot itself as its first act and refuses an invalid
one with the validator's own rule ids — it never repairs or partially
renders.

Deterministic by construction: no clock, randomness, environment read,
network, or I/O of any kind; identical snapshot → byte-identical report.

Every renderable string below is a fixed template. None may contain
advice/directive phrasing — FORBIDDEN_ADVICE_TOKENS is exported as the
canonical list the test suite scans every assembled report against.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from services.research_analyst.contract_validator import validate_snapshot
from services.research_analyst.intelligence_snapshot import (
    EvidenceState,
    LiveIntelligenceSnapshot,
    _jsonable,
)

REPORT_CONTRACT_VERSION = "1.0"

# Canonical forbidden-token list (Phase 2 spec §4 rule 6). The test suite
# scans every renderable string of every assembled report against these,
# case-insensitively. Extending report wording requires keeping this scan
# green — advice language is a contract violation, not a style choice.
FORBIDDEN_ADVICE_TOKENS = (
    "you should",
    "buy now",
    "sell now",
    "we recommend",
    "must buy",
    "must sell",
    "guaranteed",
    "cannot lose",
    "allocate",
)

SECTION_ORDER = (
    "executive_snapshot",
    "current_signal_context",
    "key_evidence",
    "risks_and_invalidation",
    "data_availability",
    "disclaimer",
)

# Fixed honest-absence / boundary texts (Phase 2 spec §5). Facts about the
# snapshot, never safety claims or inferred directions.
TEXT_DECISION_UNAVAILABLE = "Current decision output unavailable."
TEXT_NO_KEY_EVIDENCE = "No supported evidence items beyond the decision context are present in this snapshot."
TEXT_NO_RISK_ITEMS = "No counter-evidence items are present in this snapshot."
TEXT_NO_INVALIDATION = "No approved invalidation condition is currently available for this scope."
TEXT_ALL_OWNERS_SUPPORTED = "All registered evidence owners supplied evidence for this scope."
TEXT_RESEARCH_NOT_ADVICE = (
    "This report is research and explanation generated from validated platform "
    "evidence. It is not financial advice, a guarantee, or an instruction to trade."
)

# key_evidence owner ordering — fixed so output is deterministic.
_KEY_EVIDENCE_OWNERS = (
    "BusinessQuality",
    "FinancialStrength",
    "GrowthIntelligence",
    "ValuationIntelligence",
    "QualityFactorsLegacy",
    "MarketRegime",
    "GlobalContext",
)


@dataclass(frozen=True)
class ReportSection:
    section_id: str
    title: str
    entries: tuple[Mapping[str, Any], ...]
    evidence_ids: tuple[str, ...]
    text: str | None


@dataclass(frozen=True)
class ResearchReport:
    report_contract_version: str
    snapshot_id: str
    snapshot_hash: str
    scope: Mapping[str, Any]
    generated_at: str | None
    data_as_of: str | None
    overall_status: str
    sections: tuple[ReportSection, ...]
    disclosures: tuple[str, ...]


@dataclass(frozen=True)
class ReportRejection:
    """Structured refusal: the snapshot failed contract validation. Carries
    the validator's own rule ids/reasons; no sections are rendered."""

    rule_ids: tuple[str, ...]
    reasons: tuple[str, ...]


def report_to_dict(report: ResearchReport) -> dict:
    """Plain JSON-safe dict form of an assembled report."""
    return _jsonable(report)


def _entry(label: str, value: Any, evidence_ids: tuple[str, ...], provenance_ref: str,
           owner: str | None = None, display_value: str | None = None) -> Mapping[str, Any]:
    e: dict[str, Any] = {
        "label": label,
        "value": value,
        "evidence_ids": list(evidence_ids),
        "provenance_ref": provenance_ref,
    }
    if owner is not None:
        e["owner"] = owner
    if display_value is not None:
        e["display_value"] = display_value
    return e


def _supported(item) -> bool:
    return item.status is EvidenceState.SUPPORTED or getattr(item.status, "value", item.status) == "SUPPORTED"


def assemble_research_report(snapshot: LiveIntelligenceSnapshot) -> ResearchReport | ReportRejection:
    """Assemble the fixed Phase 2 six-section report from one validated
    snapshot, or refuse with the validator's rule ids."""
    if not isinstance(snapshot, LiveIntelligenceSnapshot):
        return ReportRejection(rule_ids=("TYPE",), reasons=("input is not a LiveIntelligenceSnapshot",))

    validation = validate_snapshot(snapshot)
    if not validation.valid:
        return ReportRejection(
            rule_ids=tuple(v.rule_id for v in validation.violations),
            reasons=tuple(v.reason for v in validation.violations),
        )

    items = snapshot.evidence_items
    by_owner: dict[str, list] = {}
    for item in items:
        by_owner.setdefault(item.owner, []).append(item)
    owner_statuses = snapshot.availability.owner_statuses

    # ── 1. executive_snapshot ────────────────────────────────────────────
    scope = snapshot.scope
    state_counts: dict[str, int] = {}
    for item in items:
        state = getattr(item.status, "value", item.status)
        state_counts[state] = state_counts.get(state, 0) + 1
    builder_ids = tuple(i.evidence_id for i in by_owner.get("SnapshotBuilder", ()))
    executive = ReportSection(
        section_id="executive_snapshot",
        title="Executive Snapshot",
        entries=(
            _entry("Scope", f"{scope.symbol} ({scope.market}/{scope.exchange}, {scope.currency}) — {scope.horizon} horizon",
                   builder_ids, "scope"),
            _entry("Generated at", snapshot.timestamps.generated_at, builder_ids, "timestamps.generated_at"),
            _entry("Price data as of", snapshot.timestamps.data_as_of, builder_ids, "timestamps.data_as_of"),
            _entry("Snapshot status", snapshot.availability.overall_status, builder_ids, "availability.overall_status"),
            # Plain per-status counts — never converted into a score (Contract §8.1).
            _entry("Evidence state counts", dict(sorted(state_counts.items())), builder_ids, "evidence_items"),
        ),
        evidence_ids=builder_ids,
        text=None,
    )

    # ── 2. current_signal_context ────────────────────────────────────────
    decision_items = [i for i in by_owner.get("PredictionEngine", ()) if i.domain == "decision"]
    decision_supported = owner_statuses.get("PredictionEngine") == "SUPPORTED"
    if decision_supported:
        decision_ids = tuple(i.evidence_id for i in decision_items if _supported(i))
        entries = [
            _entry(i.label, i.value, (i.evidence_id,), i.provenance_ref,
                   owner="PredictionEngine", display_value=i.display_value)
            for i in decision_items
            if _supported(i) and i.claim_type == "fact"
        ]
        trade_levels = snapshot.decision_context.get("trade_levels")
        if trade_levels is not None:
            # Verbatim owned values from decision_context; cited to the
            # decision items collectively (they share the same owner scope).
            entries.append(_entry("Trade levels", dict(trade_levels), decision_ids,
                                  "decision_context.trade_levels", owner="PredictionEngine"))
        signal_section = ReportSection(
            section_id="current_signal_context",
            title="Current Signal Context",
            entries=tuple(entries),
            evidence_ids=decision_ids,
            text=None,
        )
    else:
        status_ids = tuple(i.evidence_id for i in decision_items) or tuple(
            i.evidence_id for i in by_owner.get("PredictionEngine", ()))
        signal_section = ReportSection(
            section_id="current_signal_context",
            title="Current Signal Context",
            entries=(),
            evidence_ids=status_ids,
            text=TEXT_DECISION_UNAVAILABLE,
        )

    # ── 3. key_evidence ──────────────────────────────────────────────────
    key_entries: list[Mapping[str, Any]] = []
    for owner in _KEY_EVIDENCE_OWNERS:
        for item in by_owner.get(owner, ()):
            if _supported(item):
                key_entries.append(_entry(item.label, item.value, (item.evidence_id,),
                                          item.provenance_ref, owner=owner,
                                          display_value=item.display_value))
    for item in decision_items:
        if _supported(item) and item.claim_type == "owner_conclusion":
            key_entries.append(_entry(item.label, item.value, (item.evidence_id,),
                                      item.provenance_ref, owner="PredictionEngine",
                                      display_value=item.display_value))
    key_evidence = ReportSection(
        section_id="key_evidence",
        title="Key Evidence",
        entries=tuple(key_entries),
        evidence_ids=tuple(eid for e in key_entries for eid in e["evidence_ids"]),
        text=None if key_entries else TEXT_NO_KEY_EVIDENCE,
    )

    # ── 4. risks_and_invalidation ────────────────────────────────────────
    risk_items = [i for i in items if i.claim_type in ("risk", "counter_evidence") and _supported(i)]
    risk_entries = tuple(
        _entry(i.label, i.value, (i.evidence_id,), i.provenance_ref,
               owner=i.owner, display_value=i.display_value)
        for i in risk_items
    )
    # Phase 1 emits no `invalidation` claim-type items (no owner supplies
    # structured invalidation conditions today) — the fixed honest text
    # below is a standing Phase 2 reality, never a generated inference
    # (Contract §4.2 row 10: never invent thresholds, dates, or triggers).
    risks = ReportSection(
        section_id="risks_and_invalidation",
        title="Main Risks and Invalidation Conditions",
        entries=risk_entries,
        evidence_ids=tuple(i.evidence_id for i in risk_items),
        text=(TEXT_NO_INVALIDATION if risk_entries
              else f"{TEXT_NO_RISK_ITEMS} {TEXT_NO_INVALIDATION}"),
    )

    # ── 5. data_availability ─────────────────────────────────────────────
    availability_entries: list[Mapping[str, Any]] = []
    for owner in sorted(owner_statuses.keys()):
        status = owner_statuses[owner]
        if status == "SUPPORTED":
            continue
        status_items = [i for i in by_owner.get(owner, ()) if not _supported(i)]
        reason = next((i.reason for i in status_items if i.reason), None)
        availability_entries.append(_entry(
            f"{owner}: {status}", reason,
            tuple(i.evidence_id for i in status_items),
            status_items[0].provenance_ref if status_items else owner,
            owner=owner))
    data_availability = ReportSection(
        section_id="data_availability",
        title="Data Availability and Limitations",
        entries=tuple(availability_entries),
        evidence_ids=tuple(eid for e in availability_entries for eid in e["evidence_ids"]),
        text=None if availability_entries else TEXT_ALL_OWNERS_SUPPORTED,
    )

    # ── 6. disclaimer ────────────────────────────────────────────────────
    disclaimer = ReportSection(
        section_id="disclaimer",
        title="Disclosures",
        entries=tuple(_entry("Disclosure", d, builder_ids, "disclosures") for d in snapshot.disclosures),
        evidence_ids=builder_ids,
        text=TEXT_RESEARCH_NOT_ADVICE,
    )

    return ResearchReport(
        report_contract_version=REPORT_CONTRACT_VERSION,
        snapshot_id=snapshot.snapshot_id,
        snapshot_hash=snapshot.snapshot_hash,
        scope={
            "symbol": scope.symbol, "market": scope.market, "exchange": scope.exchange,
            "currency": scope.currency, "horizon": scope.horizon,
        },
        generated_at=snapshot.timestamps.generated_at,
        data_as_of=snapshot.timestamps.data_as_of,
        overall_status=snapshot.availability.overall_status,
        sections=(executive, signal_section, key_evidence, risks, data_availability, disclaimer),
        disclosures=snapshot.disclosures,
    )
