"""
Deterministic postmortem generation service — Trade Postmortem Engine,
Sprint 2.

Ties together everything Sprint 1 and Sprint 2 already built:
`deterministic.compute_postmortem` (Phase 1 P&L/duration), `evidence_
attribution.build_evidence_attribution` (Sprint 1 claim-governed
attribution — which validates its own referential integrity internally
and raises `ReportIntegrityError` rather than ever returning a broken
report), and `report_store.persist_report` (Sprint 2 versioned
persistence).

Deliberately split into a PURE function (`build_report_payload` — no I/O,
easily unit-tested without a database) and a persistence wrapper
(`generate_and_persist` — the only function here that touches `conn`).
Loading the trade record and entry snapshot from the database is the
CALLER's responsibility (the API router already has `_fetch_entry_
snapshot`/`_row_to_closed_trade_record` for exactly this — this module
does not duplicate that data-access logic, it consumes already-loaded
`ClosedTradeRecord`/`EntrySnapshot` objects).

No LLM, no external AI dependency, no arbitrary web lookup — identical to
every module it calls. Deterministic for identical evidence and
versions: the SAME (postmortem, entry_snapshot) pair, run through
`build_report_payload` twice, produces byte-identical evidence_items and
claims (already proven by evidence_attribution.py's own determinism
test) and therefore an identical `evidence_hash`.
"""

import dataclasses
from dataclasses import dataclass
from datetime import date

from services.postmortem.deterministic import (
    CALCULATION_VERSION,
    ClosedTradeRecord,
    DeterministicPostmortem,
    EvidenceCompleteness,
)
from services.postmortem.entry_snapshot import EntrySnapshot
from services.postmortem.evidence_attribution import ATTRIBUTION_RULES_VERSION, EvidenceAttributionResult, build_evidence_attribution
from services.postmortem import outbox as outbox_ops
from services.postmortem import report_store
from services.postmortem.report_store import PersistedReport

EVIDENCE_BUNDLE_VERSION = "1.0.0"
REPORT_SCHEMA_VERSION = "1.0.0"

_STATUS_COMPLETE = "COMPLETE"
_STATUS_LIMITED_EVIDENCE = "LIMITED_EVIDENCE"


@dataclass(frozen=True)
class ReportPayload:
    status: str
    structured_report: dict
    evidence_items: list
    claims: list
    source_manifest: dict
    evidence_gaps: list
    warnings: list


def _asdict(obj):
    return dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj


def build_report_payload(
    postmortem: DeterministicPostmortem,
    entry_snapshot: EntrySnapshot | None,
    *,
    exit_snapshot_present: bool,
) -> ReportPayload:
    """Pure: no database access. Raises services.postmortem.
    evidence_attribution.ReportIntegrityError (propagated from
    build_evidence_attribution) if the computed attribution ever fails
    its own referential-integrity check — this function never persists
    or returns a broken report; it simply doesn't complete.

    Status determination (Sprint 2, Stage 7/12): LIMITED_EVIDENCE when
    either half of the evidence this report depends on is missing (no
    entry snapshot — the common case for a manually-opened or historical
    trade — or no exit snapshot, e.g. a trade closed before Sprint 2
    existed). COMPLETE means both halves exist; it does NOT claim every
    individual claim inside the report is itself EVIDENCE_SUPPORTED —
    Sprint 1's per-claim evidence classes remain the fine-grained truth,
    this is only the coarse top-level status."""
    attribution: EvidenceAttributionResult = build_evidence_attribution(postmortem, entry_snapshot)

    status = _STATUS_COMPLETE
    evidence_gaps: list[str] = []
    if entry_snapshot is None:
        status = _STATUS_LIMITED_EVIDENCE
        evidence_gaps.append("no entry snapshot exists for this trade — entry-time evidence is unavailable")
    if not exit_snapshot_present:
        status = _STATUS_LIMITED_EVIDENCE
        evidence_gaps.append("no exit snapshot exists for this trade — this is a historical trade closed before "
                              "Sprint 2's durable close lifecycle existed")
    if postmortem.evidence_completeness == EvidenceCompleteness.LIMITED:
        status = _STATUS_LIMITED_EVIDENCE
        if "no entry snapshot exists for this trade — entry-time evidence is unavailable" not in evidence_gaps:
            evidence_gaps.append("entry evidence completeness is LIMITED")

    structured_report = {
        "trade_id": postmortem.trade_id,
        "postmortem": _asdict(postmortem) | {
            "outcome": postmortem.outcome.value,
            "exit_mechanism": postmortem.exit_mechanism.value,
            "auto_close_timing_evidence": postmortem.auto_close_timing_evidence.value,
            "evidence_completeness": postmortem.evidence_completeness.value,
        },
        "attribution": {
            "primary_contributor": attribution.primary_contributor,
            "primary_contributor_claim_id": attribution.primary_contributor_claim_id,
            "thesis_verdict": attribution.thesis_verdict,
            "thesis_verdict_claim_id": attribution.thesis_verdict_claim_id,
            "signal_scorecard": [_asdict(s) for s in attribution.signal_scorecard],
            "contributor_assessments": [_asdict(c) for c in attribution.contributor_assessments],
            "rule_registry_version": attribution.rule_registry_version,
            "calculation_version": attribution.calculation_version,
        },
    }
    evidence_items = [_asdict(e) for e in attribution.evidence_items]
    claims = [_asdict(c) for c in attribution.claims]
    source_manifest = {
        "has_entry_snapshot": entry_snapshot is not None,
        "has_exit_snapshot": exit_snapshot_present,
        "phase1_calculation_version": CALCULATION_VERSION,
        "attribution_rules_version": ATTRIBUTION_RULES_VERSION,
    }

    return ReportPayload(
        status=status,
        structured_report=structured_report,
        evidence_items=evidence_items,
        claims=claims,
        source_manifest=source_manifest,
        evidence_gaps=evidence_gaps,
        warnings=list(postmortem.warnings) + list(attribution.warnings),
    )


def generate_and_persist(
    conn,
    *,
    trade_id: int,
    user_id: str,
    market: str,
    report_trading_date: date,
    market_timezone: str,
    postmortem: DeterministicPostmortem,
    entry_snapshot: EntrySnapshot | None,
    exit_snapshot_present: bool,
    outbox_id: int | None = None,
) -> tuple[PersistedReport, bool]:
    """The only function in this module that touches `conn`. Computes the
    payload via the pure `build_report_payload` above, persists it
    (idempotently — see report_store.persist_report), and — when
    `outbox_id` is supplied — marks that outbox row terminal with the
    resulting status in the SAME call. Callers that already hold an open
    transaction should call this within it; callers recovering a PENDING
    row on a GET request may call it in its own short transaction."""
    payload = build_report_payload(postmortem, entry_snapshot, exit_snapshot_present=exit_snapshot_present)

    report, created = report_store.persist_report(
        conn,
        paper_trade_id=trade_id,
        user_id=user_id,
        market=market,
        report_trading_date=report_trading_date,
        market_timezone=market_timezone,
        report_schema_version=REPORT_SCHEMA_VERSION,
        calculation_version=CALCULATION_VERSION,
        attribution_rules_version=ATTRIBUTION_RULES_VERSION,
        evidence_bundle_version=EVIDENCE_BUNDLE_VERSION,
        status=payload.status,
        structured_report=payload.structured_report,
        evidence_items=payload.evidence_items,
        claims=payload.claims,
        source_manifest=payload.source_manifest,
        evidence_gaps=payload.evidence_gaps,
        warnings=payload.warnings,
    )

    if outbox_id is not None:
        outbox_ops.mark_terminal(conn, outbox_id=outbox_id, status=report.status)

    return report, created
