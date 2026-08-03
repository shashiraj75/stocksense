"""
Trade Postmortem Sprint 3A, Wave B Stage J4E/J4F — the CURRENT
(report_schema_version 1.2.0) governed report generation path.

This module is the ONE shared processing orchestrator (Gate 'J4F — One
Shared Processing Orchestrator') used by close-time best-effort
generation, explicit POST recovery, and background outbox recovery —
none of those three call sites re-implement generation independently.

Sole semantic authority for the 1.2.0 governed touch/order section:
services.postmortem.governed_price_path_conclusions.classify_governed_
level_touch / classify_governed_order. Never detect_touches,
classify_touch_order, or build_touch_order_claim (those remain the
frozen, historical 1.1.0 authority in price_path_generation.py /
price_path_claims.py — unchanged, still reachable, never mixed with a
1.2.0 report).

Does NOT require an already-persisted Sprint 2 (1.0.0) report as a
functional prerequisite — the base report payload is built purely, in
memory, via generation_service.build_report_payload, directly from the
closed trade record and its immutable entry/exit snapshots.

Five phases (mirrors price_path_generation.py's own phase boundaries):
  1. short database read (trade + snapshots + compatible evidence);
  2. no-database external acquisition or evidence replay;
  3. short evidence-persistence transaction;
  4. pure report construction (this module's own governed section);
  5. short atomic report + outbox-terminal transaction.
"""
import dataclasses
import logging
from datetime import date, datetime

from services.postmortem import current_report_metrics, deterministic, generation_service, outbox as outbox_ops, price_path_generation, report_store
from services.postmortem.deterministic import ClosedTradeRecord, DeterministicPostmortem, compute_postmortem
from services.postmortem.entry_snapshot import EntrySnapshot
from services.postmortem.evidence import EvidenceItem, PostmortemClaim
from services.postmortem.exit_snapshot import ExitSnapshot
from services.postmortem.governed_price_path_claims import build_governed_order_claim, build_governed_touch_claim
from services.postmortem.governed_price_path_conclusions import (
    CANONICAL_FALLBACK,
    GovernedLevelTouchConclusion,
    GovernedOrderConclusion,
    classify_governed_level_touch,
    classify_governed_order,
)
from services.postmortem.level_history_eligibility import (
    LEVEL_HISTORY_CONTRACT_VERSION_1,
    classify_endpoint_evidence,
    classify_level_history_status,
    classify_price_basis_eligibility,
)
from services.postmortem.price_path_calculator import (
    STOP_VALUE,
    TARGET_VALUE,
    ObservedNumericalCrossingSummary,
    compute_excursion,
    observe_numerical_level_crossing,
    summarize_observed_numerical_crossings,
)
from services.postmortem.price_path_evidence import PricePathEvidenceBundle
from services.postmortem.price_path_evidence_decision import compute_report_completeness_ceiling
from services.postmortem.price_path_identity import (
    GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION,
    GOVERNED_PRICE_PATH_REPORT_SCHEMA_VERSION,
    governed_calculation_version,
)
from services.postmortem.report_store import PersistedReport

log = logging.getLogger(__name__)

# --- Version identity (Gate 'J4E — Version and Authority Contract') ---
CURRENT_REPORT_SCHEMA_VERSION = GOVERNED_PRICE_PATH_REPORT_SCHEMA_VERSION

# --- process_current_report outcome vocabulary ---
CURRENT_REPORT_GENERATED = "CURRENT_REPORT_GENERATED"
CURRENT_REPORT_ALREADY_COMPLETE = "CURRENT_REPORT_ALREADY_COMPLETE"
CURRENT_REPORT_GENERATION_IN_PROGRESS = "CURRENT_REPORT_GENERATION_IN_PROGRESS"
CURRENT_REPORT_FAILED_RETRYABLE = "CURRENT_REPORT_FAILED_RETRYABLE"
CURRENT_REPORT_FAILED_TERMINAL = "CURRENT_REPORT_FAILED_TERMINAL"
CURRENT_REPORT_NOT_YET_AVAILABLE = "CURRENT_REPORT_NOT_YET_AVAILABLE"
# A terminal-success (COMPLETE/LIMITED_EVIDENCE) outbox row whose report
# is genuinely missing — an inconsistent state (e.g. manual DB
# intervention). Never silently regenerated (that would fabricate a
# second "success" under a row that already claims one happened) —
# surfaced distinctly for operational remediation instead.
CURRENT_REPORT_INTEGRITY_CONTRADICTION = "CURRENT_REPORT_INTEGRITY_CONTRADICTION"


def current_target_identity(*, base_calculation_version: str, numerical_rules_version: str, source_version: str) -> tuple[str, str, str]:
    """The exact (report_schema_version, calculation_version,
    attribution_rules_version) triple every 1.2.0 outbox row, lookup and
    persisted report must agree on bit-for-bit (Gate 'J4F — Exact
    Version-Scoped Lookups'). ONE authoritative formatter — never
    hand-assembled at a call site."""
    calc_version = governed_calculation_version(
        base_calculation_version=base_calculation_version,
        numerical_rules_version=numerical_rules_version, source_version=source_version,
    )
    return CURRENT_REPORT_SCHEMA_VERSION, calc_version, GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION


# ============================= Per-level governed evidence ============================= #

@dataclasses.dataclass(frozen=True)
class GovernedLevelEvidence:
    level_kind: str
    invariant_version: str | None
    initial_modified_flag: bool | None
    final_modified_flag: bool | None
    entry_value: float | None
    exit_value: float | None
    level_history_status: str
    endpoint_status: str
    price_basis_status: str
    touch: GovernedLevelTouchConclusion


def _build_level_evidence(
    *, level_kind: str, entry_snapshot: EntrySnapshot | None, exit_snapshot: ExitSnapshot | None,
    entry_value: float | None, exit_value: float | None, observation, price_basis_status: str, bars_available: bool,
) -> GovernedLevelEvidence:
    # The invariant version and per-level modified flag are read from
    # the EXIT snapshot's final state when present (it's the later,
    # more complete durable record of the trade's whole lifecycle);
    # fall back to the entry snapshot's initial state only when no exit
    # snapshot exists at all (never mix a governed exit version with a
    # legacy entry version or vice versa — both always agree for any
    # trade closed under the Wave A/J4D invariant, since the version is
    # immutable once set).
    if exit_snapshot is not None:
        invariant_version = exit_snapshot.level_history_contract_version
        final_flag = exit_snapshot.final_stop_modified_after_entry if level_kind == STOP_VALUE else exit_snapshot.final_target_modified_after_entry
    else:
        invariant_version = entry_snapshot.level_history_contract_version if entry_snapshot is not None else None
        final_flag = None
    initial_flag = None
    if entry_snapshot is not None:
        initial_flag = entry_snapshot.initial_stop_modified_after_entry if level_kind == STOP_VALUE else entry_snapshot.initial_target_modified_after_entry

    # Gate 'J4E — Immutable Snapshot Consumption': an inconsistent
    # entry/exit invariant-version pair (only possible for corrupt or
    # cross-wired data, since Wave A's DB trigger makes the version
    # immutable) fails closed to CONTRADICTORY rather than picking one.
    entry_version = entry_snapshot.level_history_contract_version if entry_snapshot is not None else None
    if entry_snapshot is not None and exit_snapshot is not None and entry_version != invariant_version:
        level_history_status = "CONTRADICTORY"
    else:
        level_history_status = classify_level_history_status(invariant_version=invariant_version, level_modified_flag=final_flag)

    endpoint_status = classify_endpoint_evidence(
        entry_snapshot_present=entry_snapshot is not None, exit_snapshot_present=exit_snapshot is not None,
        entry_value=entry_value, exit_value=exit_value,
    )

    touch = classify_governed_level_touch(
        level_kind=level_kind, observation=observation, level_history_status=level_history_status,
        price_basis_status=price_basis_status, bars_available=bars_available,
        entry_snapshot_present=entry_snapshot is not None, exit_snapshot_present=exit_snapshot is not None,
        entry_value=entry_value, exit_value=exit_value,
    )
    return GovernedLevelEvidence(
        level_kind=level_kind, invariant_version=invariant_version,
        initial_modified_flag=initial_flag, final_modified_flag=final_flag,
        entry_value=entry_value, exit_value=exit_value,
        level_history_status=level_history_status, endpoint_status=endpoint_status,
        price_basis_status=price_basis_status, touch=touch,
    )


# ============================= Phase 4 — pure governed payload ============================= #

@dataclasses.dataclass(frozen=True)
class GovernedPricePathPayload:
    status: str
    price_path_section: dict
    evidence_items: list[EvidenceItem]
    claims: list[PostmortemClaim]
    limitations: list[str]


def _level_section(evidence: GovernedLevelEvidence) -> dict:
    return {
        "contract_version": evidence.invariant_version,
        "initial_modification_state": evidence.initial_modified_flag,
        "final_modification_state": evidence.final_modified_flag,
        "entry_endpoint_value": evidence.entry_value,
        "exit_endpoint_value": evidence.exit_value,
        "level_history_status": evidence.level_history_status,
        "endpoint_evidence_status": evidence.endpoint_status,
        "price_basis_eligibility": evidence.price_basis_status,
        "governed_touch_status": evidence.touch.status,
        "governed_touch_detail": evidence.touch.detail,
    }


def build_governed_price_path_payload(
    trade_id: int, bundle: PricePathEvidenceBundle | None, *,
    entry_snapshot: EntrySnapshot | None, exit_snapshot: ExitSnapshot | None,
    entry_price: float, exit_price: float | None,
    entry_stop_value: float | None, exit_stop_value: float | None,
    entry_target_value: float | None, exit_target_value: float | None,
) -> GovernedPricePathPayload:
    """PHASE 4 — pure, no I/O. `bundle=None` means no compatible price-
    path evidence exists at all (provider never reachable / not yet
    acquired) — produces a governed LIMITED_EVIDENCE section rather than
    fabricating a positive claim (Gate 'J4F — LIMITED-EVIDENCE
    COMPLETION')."""
    if bundle is None:
        # No compatible price-path evidence exists at all (never
        # acquired / provider permanently unreachable for this trade) —
        # classify_governed_level_touch requires a real
        # NumericalLevelCrossingObservation, which itself requires a
        # real bundle to construct honestly, so its NO_BARS fallback
        # status is constructed directly here instead (the exact status
        # classify_governed_level_touch itself would return for
        # bars_available=False, bypassing only the object it can't be
        # given — never fabricating a different conclusion).
        limitations = ["no compatible price-path evidence is available for this trade"]
        target_touch = GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status="NO_BARS", detail=CANONICAL_FALLBACK)
        stop_touch = GovernedLevelTouchConclusion(level_kind=STOP_VALUE, status="NO_BARS", detail=CANONICAL_FALLBACK)
        target_items, target_claim = build_governed_touch_claim(trade_id, TARGET_VALUE, target_touch)
        stop_items, stop_claim = build_governed_touch_claim(trade_id, STOP_VALUE, stop_touch)
        order_conclusion = GovernedOrderConclusion(status="ORDER_UNAVAILABLE", detail=CANONICAL_FALLBACK)
        order_items, order_claim = build_governed_order_claim(
            trade_id, order_conclusion, [i.evidence_id for i in target_items], [i.evidence_id for i in stop_items],
        )
        return GovernedPricePathPayload(
            status="LIMITED_EVIDENCE",
            price_path_section={
                "raw_evidence": None,
                "level_history": {
                    "target": _level_section_from_touch(target_touch, entry_target_value, exit_target_value),
                    "stop": _level_section_from_touch(stop_touch, entry_stop_value, exit_stop_value),
                },
                "governed_conclusions": {
                    "target_touch_status": target_touch.status, "target_touch_detail": target_touch.detail,
                    "stop_touch_status": stop_touch.status, "stop_touch_detail": stop_touch.detail,
                    "order_status": order_conclusion.status, "order_detail": order_conclusion.detail,
                },
            },
            evidence_items=target_items + stop_items + order_items,
            claims=[target_claim, stop_claim, order_claim],
            limitations=limitations,
        )

    excursion = compute_excursion(bundle, entry_price=entry_price, exit_price=exit_price)
    price_basis_status = classify_price_basis_eligibility(bundle.price_adjustment_basis)
    bars_available = len(bundle.bars) > 0

    target_observation = observe_numerical_level_crossing(bundle, exit_target_value, TARGET_VALUE)
    stop_observation = observe_numerical_level_crossing(bundle, exit_stop_value, STOP_VALUE)
    summary = summarize_observed_numerical_crossings(target_observation, stop_observation)

    target_evidence = _build_level_evidence(
        level_kind=TARGET_VALUE, entry_snapshot=entry_snapshot, exit_snapshot=exit_snapshot,
        entry_value=entry_target_value, exit_value=exit_target_value, observation=target_observation,
        price_basis_status=price_basis_status, bars_available=bars_available,
    )
    stop_evidence = _build_level_evidence(
        level_kind=STOP_VALUE, entry_snapshot=entry_snapshot, exit_snapshot=exit_snapshot,
        entry_value=entry_stop_value, exit_value=exit_stop_value, observation=stop_observation,
        price_basis_status=price_basis_status, bars_available=bars_available,
    )

    order_conclusion = classify_governed_order(
        summary=summary,
        target_level_history_status=target_evidence.level_history_status,
        stop_level_history_status=stop_evidence.level_history_status,
        target_price_basis_status=price_basis_status, stop_price_basis_status=price_basis_status,
        target_bars_available=bars_available, stop_bars_available=bars_available,
        target_entry_snapshot_present=entry_snapshot is not None, target_exit_snapshot_present=exit_snapshot is not None,
        target_entry_value=entry_target_value, target_exit_value=exit_target_value,
        stop_entry_snapshot_present=entry_snapshot is not None, stop_exit_snapshot_present=exit_snapshot is not None,
        stop_entry_value=entry_stop_value, stop_exit_value=exit_stop_value,
    )

    target_items, target_claim = build_governed_touch_claim(trade_id, TARGET_VALUE, target_evidence.touch)
    stop_items, stop_claim = build_governed_touch_claim(trade_id, STOP_VALUE, stop_evidence.touch)
    order_items, order_claim = build_governed_order_claim(
        trade_id, order_conclusion, [i.evidence_id for i in target_items], [i.evidence_id for i in stop_items],
    )

    # Gate 'J4E — Report Status Ceiling': COMPLETE requires the raw
    # price-path evidence itself to be COMPLETE (never PARTIAL/
    # UNAVAILABLE/etc.) AND both per-level touch conclusions to have
    # resolved to something other than a canonical-fallback status —
    # a definitive negative (NO_COMPATIBLE_CROSSING) or NO_VALUE_
    # SUPPLIED counts as resolved, exactly like SUPPORTED, since neither
    # requires the fallback sentence for a material configured factor.
    _TOUCH_FALLBACKS = frozenset({"INCOMPATIBLE_BASIS", "NO_BARS", "INSUFFICIENT_EVIDENCE", "CONTRADICTORY_ENDPOINT_EVIDENCE"})
    raw_ceiling = "COMPLETE" if bundle.data_completeness == "COMPLETE" else "LIMITED_EVIDENCE"
    governed_ceiling = (
        "LIMITED_EVIDENCE"
        if target_evidence.touch.status in _TOUCH_FALLBACKS or stop_evidence.touch.status in _TOUCH_FALLBACKS
        else "COMPLETE"
    )
    status = compute_report_completeness_ceiling(raw_ceiling, governed_ceiling)

    return GovernedPricePathPayload(
        status=status,
        price_path_section={
            "raw_evidence": {
                "evidence_bundle_version": bundle.evidence_bundle_version,
                "source_id": bundle.source_id, "source_version": bundle.source_version,
                "price_adjustment_basis": bundle.price_adjustment_basis,
                "observed_window_start": bundle.observed_window_start.isoformat() if bundle.observed_window_start else None,
                "observed_window_end": bundle.observed_window_end.isoformat() if bundle.observed_window_end else None,
                "any_observation_pattern": summary.any_observation_pattern,
                "safely_attributable_pattern": summary.safely_attributable_pattern,
                "partial_boundary_observation_present": summary.partial_boundary_observation_present,
                "mfe_pct": excursion.mfe_pct, "mae_magnitude_pct": excursion.mae_magnitude_pct,
                "captured_mfe_pct": excursion.captured_mfe_pct,
                "evidence_completeness": excursion.evidence_completeness,
                "limitations": list(excursion.limitations) + list(bundle.limitations),
            },
            "level_history": {
                "target": _level_section(target_evidence),
                "stop": _level_section(stop_evidence),
            },
            "governed_conclusions": {
                "target_touch_status": target_evidence.touch.status, "target_touch_detail": target_evidence.touch.detail,
                "stop_touch_status": stop_evidence.touch.status, "stop_touch_detail": stop_evidence.touch.detail,
                "order_status": order_conclusion.status, "order_detail": order_conclusion.detail,
            },
        },
        evidence_items=target_items + stop_items + order_items,
        claims=[target_claim, stop_claim, order_claim],
        limitations=list(bundle.limitations),
    )


def _level_section_from_touch(touch: GovernedLevelTouchConclusion, entry_value, exit_value) -> dict:
    return {
        "contract_version": None, "initial_modification_state": None, "final_modification_state": None,
        "entry_endpoint_value": entry_value, "exit_endpoint_value": exit_value,
        "level_history_status": "REQUIRED_EVIDENCE_MISSING", "endpoint_evidence_status": None,
        "price_basis_eligibility": "EVIDENCE_UNAVAILABLE",
        "governed_touch_status": touch.status, "governed_touch_detail": touch.detail,
    }




# ============================= Base + governed payload merge ============================= #

@dataclasses.dataclass(frozen=True)
class CurrentReportPayload:
    status: str
    structured_report: dict
    evidence_items: list
    claims: list
    source_manifest: dict
    evidence_gaps: list
    warnings: list


def build_current_report_payload(
    *, postmortem: DeterministicPostmortem, entry_snapshot: EntrySnapshot | None, exit_snapshot: ExitSnapshot | None,
    price_path_payload: GovernedPricePathPayload, base_calculation_version: str, numerical_rules_version: str, source_version: str,
) -> CurrentReportPayload:
    """PHASE 4 (continued) — pure. Builds the SAME base payload
    generation_service.build_report_payload already produces (Sprint 1
    attribution + exit evidence), entirely in memory, WITHOUT requiring
    an already-persisted Sprint 2 report (Gate 'J4E — Current Report
    Generation Architecture'), then additively merges the governed
    price_path section on top — the exact same additive merge pattern
    price_path_generation.persist_price_path_report already uses for
    the historical 1.1.0 report, applied here to an in-memory base
    instead of a persisted row."""
    base = generation_service.build_report_payload(postmortem, entry_snapshot, exit_snapshot=exit_snapshot)

    structured_report = dict(base.structured_report)
    structured_report["price_path"] = price_path_payload.price_path_section
    structured_report["price_path"]["version_and_provenance"] = {
        "report_schema_version": CURRENT_REPORT_SCHEMA_VERSION,
        "calculation_version": governed_calculation_version(
            base_calculation_version=base_calculation_version, numerical_rules_version=numerical_rules_version,
            source_version=source_version,
        ),
        "numerical_rules_version": numerical_rules_version,
        "governed_semantic_rules_version": price_path_identity_rules_version(),
        "governed_claim_rules_version": GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION,
        "entry_snapshot_schema_version": entry_snapshot.snapshot_schema_version if entry_snapshot else None,
        "exit_snapshot_schema_version": exit_snapshot.exit_snapshot_schema_version if exit_snapshot else None,
        "level_history_contract_version": exit_snapshot.level_history_contract_version if exit_snapshot else (
            entry_snapshot.level_history_contract_version if entry_snapshot else None
        ),
        "source_version": source_version,
    }

    # `base.evidence_items`/`base.claims` are ALREADY plain dicts
    # (generation_service.build_report_payload converts them via its
    # own `_asdict`). `price_path_payload.evidence_items`/`.claims` are
    # raw EvidenceItem/PostmortemClaim dataclass INSTANCES (see
    # GovernedPricePathPayload's own field types) — never converted
    # before this merge. Left unconverted, report_store.persist_report's
    # `json.dumps(..., default=str)` would silently stringify them via
    # their dataclass repr (e.g. "EvidenceItem(evidence_id='...', ...)")
    # instead of persisting a governed JSON object — a real defect found
    # by real-PostgreSQL CI (a price-path-enhanced report's claims/
    # evidence_items array became a heterogeneous mix of dicts and
    # repr strings). Canonicalize explicitly here, the single authoritative
    # merge point, rather than trusting every caller to do it.
    governed_evidence_items = [
        dataclasses.asdict(item) if dataclasses.is_dataclass(item) else item
        for item in price_path_payload.evidence_items
    ]
    governed_claims = [
        dataclasses.asdict(claim) if dataclasses.is_dataclass(claim) else claim
        for claim in price_path_payload.claims
    ]
    evidence_items = list(base.evidence_items) + governed_evidence_items
    claims = list(base.claims) + governed_claims
    for item in evidence_items:
        if not isinstance(item, dict):
            raise TypeError(
                f"build_current_report_payload: evidence_items must all be dicts after "
                f"canonicalization, got {type(item).__name__} — refusing to persist an "
                f"unsupported object that would otherwise be silently stringified"
            )
    for claim in claims:
        if not isinstance(claim, dict):
            raise TypeError(
                f"build_current_report_payload: claims must all be dicts after "
                f"canonicalization, got {type(claim).__name__} — refusing to persist an "
                f"unsupported object that would otherwise be silently stringified"
            )
    status = compute_report_completeness_ceiling(base.status, price_path_payload.status)
    warnings = list(base.warnings) + list(price_path_payload.limitations)
    source_manifest = dict(base.source_manifest)
    source_manifest["price_path_rules_version"] = numerical_rules_version
    source_manifest["governed_rules_version"] = price_path_identity_rules_version()

    return CurrentReportPayload(
        status=status, structured_report=structured_report, evidence_items=evidence_items, claims=claims,
        source_manifest=source_manifest, evidence_gaps=list(base.evidence_gaps), warnings=warnings,
    )


def price_path_identity_rules_version() -> str:
    from services.postmortem.price_path_identity import GOVERNED_PRICE_PATH_RULES_VERSION
    return GOVERNED_PRICE_PATH_RULES_VERSION


# ============================= Phase 5 — persist + settle ============================= #

def persist_current_report(
    conn, *, trade_id: int, user_id: str, market: str, report_trading_date: date, market_timezone: str,
    payload: CurrentReportPayload, calculation_version: str, evidence_bundle_version: str,
    outbox_id: int | None = None, claimed_by: str | None = None,
) -> tuple[PersistedReport, bool]:
    """PHASE 5 — short atomic transaction. Determines the deterministic
    supersession predecessor (Gate 'J4E — Versioned Persistence and
    Supersession': the latest existing report for this trade whose
    schema_version is not 1.2.0 itself — 1.1.0 if present, else 1.0.0 if
    present, else None), inserts the new immutable 1.2.0 row (idempotent
    via report_store.persist_report's own ON CONFLICT DO NOTHING), then
    settles the outbox row terminal in the SAME transaction when one was
    supplied — raising generation_service.StaleLeaseError (letting it
    propagate to roll back the whole transaction, INCLUDING the report
    insert) if this claimant's lease was lost."""
    predecessor = report_store.get_latest_predecessor_report(
        conn, paper_trade_id=trade_id, user_id=user_id, exclude_report_schema_version=CURRENT_REPORT_SCHEMA_VERSION,
    )
    report, created = report_store.persist_report(
        conn, paper_trade_id=trade_id, user_id=user_id, market=market,
        report_trading_date=report_trading_date, market_timezone=market_timezone,
        report_schema_version=CURRENT_REPORT_SCHEMA_VERSION, calculation_version=calculation_version,
        attribution_rules_version=GOVERNED_PRICE_PATH_CLAIM_RULES_VERSION, evidence_bundle_version=evidence_bundle_version,
        status=payload.status, structured_report=payload.structured_report,
        evidence_items=payload.evidence_items, claims=payload.claims,
        source_manifest=payload.source_manifest, evidence_gaps=payload.evidence_gaps, warnings=payload.warnings,
        supersedes_report_id=(predecessor.id if predecessor is not None else None),
    )
    if outbox_id is not None:
        settled = outbox_ops.mark_terminal(conn, outbox_id=outbox_id, status=report.status, claimed_by=claimed_by)
        if not settled:
            raise generation_service.StaleLeaseError(outbox_id=outbox_id, claimed_by=claimed_by)
    return report, created


# ============================= Phase 1 — self-contained trade/snapshot read ============================= #
# Deliberately self-contained (does not import api.routers.paper_trading,
# which would create a circular import — paper_trading.py imports THIS
# module to invoke the shared orchestrator). Column layouts mirror
# paper_trading.py's own _POSTMORTEM_ROW_COLUMNS / entry+exit snapshot
# SELECTs exactly (Wave A/J4D field order), so any future drift between
# the two independent readers would be caught by this module's own
# integration tests reading real rows those writers actually produced.

_TRADE_ROW_COLUMNS = (
    "id, user_id, status, symbol, market, quantity, entry_price, exit_price, "
    "stop_loss, target_price, opened_at, closed_at, trade_management_mode, exit_reason"
)

_ENTRY_SNAPSHOT_SELECT_COLUMNS = (
    "paper_trade_id, user_id, symbol, market, snapshot_schema_version, evidence_source, "
    "daily_pick_run_id, daily_pick_rank, recommendation_signal, recommendation_generated_at, "
    "recommendation_reference_price, recommendation_entry_low, recommendation_entry_high, "
    "simulated_execution_price, execution_slippage_pct, execution_range_position, "
    "recommended_stop_loss, recommended_target_price, user_selected_stop_loss, user_selected_target_price, "
    "user_overrode_recommendation, reward_to_risk_ratio, confidence_score, technical_signal, technical_rsi, "
    "technical_macd_diff, fundamental_score, sentiment_score, sentiment_label, market_regime_trend, "
    "market_regime_score_adj, market_regime_reason, recommendation_reasoning, model_version, verification_levels, "
    "level_history_contract_version, initial_stop_modified_after_entry, initial_target_modified_after_entry, "
    "initial_levels_modified_after_entry"
)

_EXIT_SNAPSHOT_SELECT_COLUMNS = (
    "paper_trade_id, user_id, symbol, market, exit_snapshot_schema_version, financial_outcome, "
    "closure_classification, exit_mechanism, exit_mechanism_raw, exit_price, exit_quantity, closed_at, "
    "final_stop_loss, final_target_price, trailing_stop_level, time_exit_rule, market_close_rule, "
    "management_mode, levels_modified_after_entry, level_history_contract_version, "
    "final_stop_modified_after_entry, final_target_modified_after_entry, source_request_id, "
    "trigger_observation_timestamp, trigger_observation_price, trigger_timing_verification, source_metadata"
)


def _json_field(value):
    import json as _json
    if value is None or isinstance(value, (dict, list)):
        return value
    return _json.loads(value)


def _fetch_trade_row(conn, trade_id: int):
    row = conn.execute(f"SELECT {_TRADE_ROW_COLUMNS} FROM paper_trades WHERE id = %s", (trade_id,)).fetchone()
    if row is None:
        return None
    (id_, user_id, status, symbol, market, quantity, entry_price, exit_price, stop_loss, target_price,
     opened_at, closed_at, trade_management_mode, exit_reason) = row
    return dict(
        trade_id=id_, user_id=user_id, status=status, symbol=symbol, market=market, quantity=quantity,
        entry_price=entry_price, exit_price=exit_price, stop_loss=stop_loss, target_price=target_price,
        opened_at=opened_at, closed_at=closed_at, trade_management_mode=trade_management_mode, exit_reason=exit_reason,
    )


def fetch_entry_snapshot(conn, trade_id: int) -> EntrySnapshot | None:
    row = conn.execute(
        f"SELECT {_ENTRY_SNAPSHOT_SELECT_COLUMNS} FROM paper_trade_entry_snapshot WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()
    if row is None:
        return None
    (ptid, uid, sym, mkt, schema_version, evidence_source, pick_run_id, pick_rank, rec_signal, rec_gen_at,
     rec_ref_price, rec_entry_low, rec_entry_high, sim_price, slippage_pct, range_position, rec_stop, rec_target,
     user_stop, user_target, overrode, rr_ratio, confidence, tech_signal, tech_rsi, tech_macd, fund_score,
     sent_score, sent_label, regime_trend, regime_adj, regime_reason, reasoning, model_version, verification_levels,
     level_history_contract_version, initial_stop_modified, initial_target_modified, initial_levels_modified) = row
    return EntrySnapshot(
        paper_trade_id=ptid, user_id=uid, symbol=sym, market=mkt, snapshot_schema_version=schema_version,
        evidence_source=evidence_source, daily_pick_run_id=pick_run_id, daily_pick_rank=pick_rank,
        recommendation_signal=rec_signal, recommendation_generated_at=rec_gen_at,
        recommendation_reference_price=rec_ref_price, recommendation_entry_low=rec_entry_low,
        recommendation_entry_high=rec_entry_high, simulated_execution_price=sim_price,
        execution_slippage_pct=slippage_pct, execution_range_position=range_position,
        recommended_stop_loss=rec_stop, recommended_target_price=rec_target,
        user_selected_stop_loss=user_stop, user_selected_target_price=user_target,
        user_overrode_recommendation=overrode, reward_to_risk_ratio=rr_ratio, confidence_score=confidence,
        technical_signal=tech_signal, technical_rsi=tech_rsi, technical_macd_diff=tech_macd,
        fundamental_score=fund_score, sentiment_score=sent_score, sentiment_label=sent_label,
        market_regime_trend=regime_trend, market_regime_score_adj=regime_adj, market_regime_reason=regime_reason,
        recommendation_reasoning=_json_field(reasoning), model_version=model_version,
        verification_levels=_json_field(verification_levels) or {},
        level_history_contract_version=level_history_contract_version,
        initial_stop_modified_after_entry=initial_stop_modified,
        initial_target_modified_after_entry=initial_target_modified,
        initial_levels_modified_after_entry=initial_levels_modified,
    )


def fetch_exit_snapshot(conn, trade_id: int) -> ExitSnapshot | None:
    row = conn.execute(
        f"SELECT {_EXIT_SNAPSHOT_SELECT_COLUMNS} FROM paper_trade_exit_snapshot WHERE paper_trade_id = %s", (trade_id,),
    ).fetchone()
    if row is None:
        return None
    (ptid, uid, sym, mkt, schema_version, financial_outcome, closure_classification, exit_mechanism,
     exit_mechanism_raw, exit_price, exit_quantity, closed_at, final_stop, final_target, trailing_stop,
     time_exit_rule, market_close_rule, management_mode, levels_modified, level_history_contract_version,
     final_stop_modified, final_target_modified, source_request_id, trigger_obs_ts, trigger_obs_price,
     trigger_timing_verification, source_metadata) = row
    return ExitSnapshot(
        paper_trade_id=ptid, user_id=uid, symbol=sym, market=mkt, exit_snapshot_schema_version=schema_version,
        financial_outcome=financial_outcome, closure_classification=closure_classification,
        exit_mechanism=exit_mechanism, exit_mechanism_raw=exit_mechanism_raw, exit_price=exit_price,
        exit_quantity=exit_quantity, closed_at=closed_at, final_stop_loss=final_stop, final_target_price=final_target,
        trailing_stop_level=trailing_stop, time_exit_rule=time_exit_rule, market_close_rule=market_close_rule,
        management_mode=management_mode, levels_modified_after_entry=levels_modified,
        level_history_contract_version=level_history_contract_version,
        final_stop_modified_after_entry=final_stop_modified, final_target_modified_after_entry=final_target_modified,
        source_request_id=source_request_id, trigger_observation_timestamp=trigger_obs_ts,
        trigger_observation_price=trigger_obs_price, trigger_timing_verification=trigger_timing_verification,
        source_metadata=_json_field(source_metadata),
    )


# ============================= Outbox row (1.2.0 identity) ============================= #

def insert_current_outbox_record(conn, *, trade_id: int, user_id: str, schema_version: str, calculation_version: str, rules_version: str, source_request_id: str | None = None) -> tuple[int, bool]:
    """Idempotent INSERT ... ON CONFLICT DO NOTHING against the SAME
    unique index every outbox identity uses — a duplicate close or
    idempotent replay for the identical 1.2.0 identity never creates a
    second row (Gate 'J4F — Current Outbox Creation')."""
    row = conn.execute(
        """INSERT INTO paper_trade_postmortem_outbox
           (paper_trade_id, user_id, requested_report_schema_version, requested_calculation_version,
            requested_rules_version, source_request_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (paper_trade_id, requested_report_schema_version, requested_calculation_version, requested_rules_version)
           DO NOTHING
           RETURNING id""",
        (trade_id, user_id, schema_version, calculation_version, rules_version, source_request_id),
    ).fetchone()
    if row is not None:
        return row[0], True
    existing = conn.execute(
        """SELECT id FROM paper_trade_postmortem_outbox
           WHERE paper_trade_id = %s AND requested_report_schema_version = %s
             AND requested_calculation_version = %s AND requested_rules_version = %s""",
        (trade_id, schema_version, calculation_version, rules_version),
    ).fetchone()
    return existing[0], False


# ============================= Shared orchestrator (Phases 1-5) ============================= #
# NOTE: the CURRENT_REPORT_* outcome constants are declared ONCE, near
# the top of this module (Gate 'J4F — Report-Exists Reconciliation'
# section) — a duplicate re-declaration block used to live here with
# DIFFERENT (unprefixed) string values that silently shadowed the
# canonical ones; a genuine defect found via real-PostgreSQL CI
# execution (persisted/compared values disagreed with every caller
# checking against the canonical CURRENT_REPORT_* constants). Removed.

logger = logging.getLogger(__name__)


def process_current_report(
    conn_factory,
    *,
    trade_id: int,
    user_id: str,
    market_tzinfo,
    market_timezone_name: str,
    outbox_id: int | None = None,
    claimed_by: str | None = None,
    numerical_rules_version: str = price_path_generation.PRICE_PATH_CALC_RULES_VERSION,
    source_version: str = price_path_generation.SOURCE_VERSION,
) -> tuple[PersistedReport | None, str]:
    """The ONE shared processing orchestrator for the current (1.2.0)
    governed report identity — called identically by close-time
    best-effort generation, the explicit POST recovery endpoint, and
    background outbox recovery. Returns (report_or_None, outcome), where
    outcome is one of the CURRENT_REPORT_* constants above.

    Phase 1 (short read) -> Phase 2 (no-DB replay/acquire) -> Phase 3
    (short evidence-persist write) -> Phase 4 (pure payload build) ->
    Phase 5 (short atomic report+outbox-terminal write), mirroring
    price_path_generation.py's own established phase boundaries so no
    database connection is ever held across a provider network call."""
    with conn_factory() as conn:
        trade = _fetch_trade_row(conn, trade_id)
        if trade is None or trade["user_id"] != user_id:
            return None, CURRENT_REPORT_FAILED_TERMINAL
        if trade["status"] != "CLOSED" or trade["closed_at"] is None:
            return None, CURRENT_REPORT_NOT_YET_AVAILABLE

        entry_snapshot = fetch_entry_snapshot(conn, trade_id)
        exit_snapshot = fetch_exit_snapshot(conn, trade_id)

        schema_version, calc_version, rules_version = current_target_identity(
            base_calculation_version=deterministic.CALCULATION_VERSION,
            numerical_rules_version=numerical_rules_version,
            source_version=source_version,
        )
        evidence_bundle_version = price_path_generation.CURRENT_PRICE_PATH_SOURCE_IDENTITY.evidence_bundle_version
        source_id = price_path_generation.SOURCE_ID_YFINANCE_DAILY

        existing = report_store.get_current_report(
            conn, paper_trade_id=trade_id, user_id=user_id,
            report_schema_version=schema_version,
            calculation_version=calc_version,
            attribution_rules_version=rules_version,
        )
        if existing is not None:
            if outbox_id is not None and claimed_by is not None:
                # The row's own already-determined status, never an
                # invented value — mark_terminal only accepts COMPLETE/
                # LIMITED_EVIDENCE/FAILED_TERMINAL, and existing.status
                # is always one of those (report_store only ever
                # persists a report under one of those statuses).
                settled = outbox_ops.mark_terminal(
                    conn, outbox_id=outbox_id, status=existing.status, claimed_by=claimed_by,
                )
                if not settled:
                    raise generation_service.StaleLeaseError(
                        f"outbox_id={outbox_id}: lease no longer held by claimant when settling an "
                        "already-existing report — a different claimant has since taken over this row."
                    )
            return existing, CURRENT_REPORT_ALREADY_COMPLETE

        # Gate 'J4F — Report-Exists Reconciliation': a terminal-success
        # (COMPLETE/LIMITED_EVIDENCE) outbox row whose report is
        # genuinely missing is an inconsistent state — never silently
        # self-heal by regenerating (that would fabricate a SECOND
        # "success" under a row that already claims one happened);
        # surface it distinctly for operational remediation instead.
        if outbox_id is not None:
            outbox_row = conn.execute(
                "SELECT status FROM paper_trade_postmortem_outbox WHERE id = %s", (outbox_id,),
            ).fetchone()
            if outbox_row is not None and outbox_row[0] in ("COMPLETE", "LIMITED_EVIDENCE"):
                current_report_metrics.safe_log(
                    logger.warning,
                    "current_report_generation: integrity contradiction — outbox_id=%s already "
                    "terminal-success but no matching report exists", outbox_id,
                )
                current_report_metrics.safe_increment(current_report_metrics.COUNTER_INTEGRITY_CONTRADICTION_DETECTED)
                return None, CURRENT_REPORT_INTEGRITY_CONTRADICTION

        # Gate 'J4E — Immutable Snapshot Consumption': the entry and
        # exit endpoint values are derived INDEPENDENTLY from each
        # snapshot's own immutable fields — never collapsed onto one
        # shared "applicable" value. A missing snapshot means missing
        # endpoint evidence (None), never a silent substitution of the
        # mutable paper_trades row.
        entry_stop_value = entry_snapshot.user_selected_stop_loss if entry_snapshot is not None else None
        entry_target_value = entry_snapshot.user_selected_target_price if entry_snapshot is not None else None
        exit_stop_value = exit_snapshot.final_stop_loss if exit_snapshot is not None else None
        exit_target_value = exit_snapshot.final_target_price if exit_snapshot is not None else None
        # The numerical crossing OBSERVATION itself still needs one
        # configured level per side (raw price-path math has no notion
        # of "entry vs exit" — it observes against whichever level value
        # was actually in force). The EXIT snapshot's final value is
        # used for observation when present (the level that was active
        # at close, which is what a real touch/no-touch determination
        # must be checked against); the mutable trade row is the last
        # resort only when neither snapshot exists at all.
        applicable_stop = exit_stop_value if exit_snapshot is not None else trade["stop_loss"]
        applicable_target = exit_target_value if exit_snapshot is not None else trade["target_price"]

        gen_ctx = price_path_generation.load_generation_context(
            conn, trade_id=trade_id, user_id=user_id, symbol=trade["symbol"], market=trade["market"],
            market_timezone_name=market_timezone_name, entry_timestamp=trade["opened_at"],
            entry_price=trade["entry_price"], exit_price=trade["exit_price"],
            applicable_stop=applicable_stop, applicable_target=applicable_target,
            exit_timestamp=trade["closed_at"], level_history_complete=entry_snapshot is not None,
            prior_report=None, evidence_bundle_version=evidence_bundle_version,
            source_id=source_id, source_version=source_version,
        )

    # Phase 2 — outside any DB connection.
    bundle = None
    if gen_ctx.compatible_evidence is not None:
        # Reusing already-persisted compatible evidence makes no provider
        # network call at all — never counted as a provider "attempt"
        # (§O5: a failure RATE's denominator must only include real
        # attempts, not replays).
        current_report_metrics.safe_increment(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_REPLAY)
        bundle = price_path_generation._persisted_evidence_to_bundle(gen_ctx.compatible_evidence)
    else:
        current_report_metrics.safe_increment(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_ATTEMPT)
        try:
            with current_report_metrics.safe_timed(current_report_metrics.DURATION_PROVIDER_ACQUISITION):
                bundle = price_path_generation.acquire_evidence_outside_transaction(gen_ctx, market_tzinfo=market_tzinfo)
        except Exception:
            current_report_metrics.safe_log(
                logger.warning, "current_report_generation: provider acquisition failed for trade_id=%s", trade_id,
            )
            current_report_metrics.safe_increment(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_FAILURE)
            with conn_factory() as conn:
                if outbox_id is not None and claimed_by is not None:
                    outbox_ops.mark_retryable_failure(
                        conn, outbox_id=outbox_id, error_code="PROVIDER_ACQUISITION_FAILED",
                        error_summary="price-path evidence acquisition raised", claimed_by=claimed_by,
                    )
            return None, CURRENT_REPORT_FAILED_RETRYABLE
        current_report_metrics.safe_increment(current_report_metrics.COUNTER_PROVIDER_ACQUISITION_SUCCESS)

    # Phase 3 — short evidence-persistence transaction, closed before
    # pure report construction begins.
    if bundle is not None and gen_ctx.compatible_evidence is None:
        with conn_factory() as conn:
            price_path_generation.persist_price_path_evidence(conn, bundle)

    # Phase 4 — pure report construction. No database connection is open
    # anywhere in this block (Gate 'J4E — Current Report Generation
    # Architecture').
    price_path_payload = build_governed_price_path_payload(
        trade_id, bundle, entry_snapshot=entry_snapshot, exit_snapshot=exit_snapshot,
        entry_price=trade["entry_price"], exit_price=trade["exit_price"],
        entry_stop_value=entry_stop_value, exit_stop_value=exit_stop_value,
        entry_target_value=entry_target_value, exit_target_value=exit_target_value,
    )
    closed_record = ClosedTradeRecord(
        trade_id=trade["trade_id"], status=trade["status"], symbol=trade["symbol"], market=trade["market"],
        quantity=trade["quantity"], entry_price=trade["entry_price"], exit_price=trade["exit_price"],
        stop_loss=trade["stop_loss"], target_price=trade["target_price"], opened_at=trade["opened_at"],
        closed_at=trade["closed_at"], trade_management_mode=trade["trade_management_mode"],
        exit_reason=trade["exit_reason"],
    )
    postmortem = compute_postmortem(closed_record, snapshot=entry_snapshot)
    current_payload = build_current_report_payload(
        postmortem=postmortem, entry_snapshot=entry_snapshot, exit_snapshot=exit_snapshot,
        price_path_payload=price_path_payload,
        base_calculation_version=generation_service.CALCULATION_VERSION,
        numerical_rules_version=numerical_rules_version, source_version=source_version,
    )

    # Market-local trading date — trade['closed_at'] is a UTC-aware
    # timestamp; the report's trading date must reflect the CLOSE
    # market's own local calendar day, never the bare UTC date (which
    # can differ near day boundaries, especially for Asia/Kolkata's
    # UTC+5:30 offset).
    closed_at = trade["closed_at"]
    if closed_at.tzinfo is None:
        raise ValueError(
            f"process_current_report: trade_id={trade_id} has a timezone-naive closed_at — "
            "refusing to guess a market-local trading date rather than silently using the wrong one."
        )
    report_trading_date = closed_at.astimezone(market_tzinfo).date()

    # Phase 5 — fresh short atomic transaction: predecessor lookup,
    # idempotent report insert, outbox terminal settlement.
    with conn_factory() as conn:
        report, created = persist_current_report(
            conn, trade_id=trade_id, user_id=user_id, market=trade["market"],
            report_trading_date=report_trading_date, market_timezone=market_timezone_name,
            payload=current_payload, calculation_version=calc_version,
            evidence_bundle_version=evidence_bundle_version, outbox_id=outbox_id, claimed_by=claimed_by,
        )
        return report, CURRENT_REPORT_GENERATED
