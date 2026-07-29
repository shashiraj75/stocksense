"""
Price-path-supported postmortem generation orchestration — Trade
Postmortem Sprint 3A, Stage H.

Five explicit phases, deliberately split into separate functions so no
external provider call can ever occur while a database connection or
transaction is open — this is enforced by construction (Phase 2's own
function accepts no `conn` parameter at all), not merely by convention:

  PHASE 1 (short read)     -> load_generation_context
  PHASE 2 (no database)    -> acquire_evidence_outside_transaction
  PHASE 3 (short write)    -> persist_price_path_evidence
  PHASE 4 (pure)           -> build_price_path_report_payload
  PHASE 5 (short write)    -> persist_price_path_report

Callers wire these together (see api/routers/paper_trading.py's own
call site for the real sequencing: PHASE 1 and PHASE 3/5 each run in
their own short-lived connection/transaction, with PHASE 2 running
entirely outside any of them) rather than this module owning connection
lifecycle itself, matching generation_service.py's existing "pure
compute, thin persistence wrapper" split.

Report identity (Stage I): a price-path-enhanced report is a
GENUINELY NEW row, never an update to the prior (Sprint 1/2) report.
Its identity uses PRICE_PATH_REPORT_SCHEMA_VERSION ("1.1.0", distinct
from Sprint 2's "1.0.0") combined with a calculation_version SUFFIX
encoding the price-path rules version and the evidence's own
source_version — both under the SAME
(paper_trade_id, report_schema_version, calculation_version,
attribution_rules_version) unique index Sprint 2 already established.
No schema/index migration is needed: bumping report_schema_version
alone already produces a distinct row through that existing index.

This is a deliberate design choice, not an oversight: like every other
"current version identity" table in this codebase (Sprint 1 evidence,
Sprint 2 exit-snapshot/outbox/report, this sprint's own
price_path_store), identity is an INTENTIONAL VERSION TUPLE, not a
content hash. A re-acquisition that returns numerically identical bars
under the SAME price-path source_version is expected to return the
EXISTING report unchanged (idempotent) — exactly like
price_path_store.persist_evidence's own "same version-triple = same
row" contract. A genuinely different acquisition source_version, or a
bumped PRICE_PATH_CALC_RULES_VERSION, changes the calculation_version
suffix and therefore creates a new, distinct report row.
"""

import dataclasses
from datetime import date, datetime

from services.postmortem import generation_service, outbox as outbox_ops, price_path_store, report_store
from services.postmortem.price_path_acquisition import (
    acquire_price_path_evidence,
    fetch_dividend_events,
    fetch_raw_daily_bars,
    fetch_split_events,
)
from services.postmortem.price_path_calculator import (
    EXCURSION_COMPLETE,
    RULES_VERSION as PRICE_PATH_CALC_RULES_VERSION,
    classify_touch_order,
    compute_excursion,
    detect_touches,
)
from services.postmortem.price_path_claims import build_mae_claim, build_mfe_claim, build_touch_order_claim
from services.postmortem.price_path_evidence import (
    EVIDENCE_BUNDLE_SCHEMA_VERSION,
    PricePathBar,
    PricePathEvidenceBundle,
    STATUS_COMPLETE,
)
from services.postmortem.report_store import PersistedReport

PRICE_PATH_REPORT_SCHEMA_VERSION = "1.1.0"


def price_path_calculation_suffix(source_version: str) -> str:
    """Public so callers/tests can compute the exact expected
    calculation_version without duplicating the format string."""
    return f"{generation_service.CALCULATION_VERSION}+price_path:{PRICE_PATH_CALC_RULES_VERSION}+src:{source_version}"


@dataclasses.dataclass(frozen=True)
class GenerationContext:
    """PHASE 1 result — everything the rest of the flow needs, loaded in
    one short read before the database context closes. Loading the
    trade/entry/exit snapshot themselves is the CALLER's responsibility
    (matching generation_service.py's own convention) — this dataclass
    just carries the already-loaded values plus this module's own
    price-path evidence lookup."""

    trade_id: int
    user_id: str
    symbol: str
    market: str
    market_timezone_name: str
    entry_timestamp: datetime
    entry_price: float
    exit_price: float | None
    applicable_stop: float | None
    applicable_target: float | None
    exit_timestamp: datetime
    level_history_complete: bool
    prior_report: PersistedReport | None
    compatible_evidence: object | None  # price_path_store.PersistedPricePathEvidence | None


def load_generation_context(
    conn, *, trade_id: int, user_id: str, symbol: str, market: str, market_timezone_name: str,
    entry_timestamp: datetime, entry_price: float, exit_price: float | None,
    applicable_stop: float | None, applicable_target: float | None, exit_timestamp: datetime,
    level_history_complete: bool, prior_report: PersistedReport | None,
    evidence_bundle_version: str = EVIDENCE_BUNDLE_SCHEMA_VERSION,
    source_id: str = "yfinance_daily", source_version: str = "1.0.0",
) -> GenerationContext:
    """PHASE 1 — short read. This function's own database work is
    limited to the price-path evidence lookup (ownership/trade/snapshot
    loading is the caller's job, done via the existing
    paper_trading.py helpers before calling this)."""
    compatible = price_path_store.get_current_evidence(
        conn, paper_trade_id=trade_id, user_id=user_id,
        evidence_bundle_version=evidence_bundle_version, source_id=source_id, source_version=source_version,
    )
    return GenerationContext(
        trade_id=trade_id, user_id=user_id, symbol=symbol, market=market,
        market_timezone_name=market_timezone_name, entry_timestamp=entry_timestamp, entry_price=entry_price,
        exit_price=exit_price, applicable_stop=applicable_stop, applicable_target=applicable_target,
        exit_timestamp=exit_timestamp, level_history_complete=level_history_complete,
        prior_report=prior_report, compatible_evidence=compatible,
    )


def acquire_evidence_outside_transaction(
    ctx: GenerationContext, *, market_tzinfo,
    fetch_bars_fn=fetch_raw_daily_bars, fetch_splits_fn=fetch_split_events, fetch_dividends_fn=fetch_dividend_events,
) -> PricePathEvidenceBundle:
    """PHASE 2 — no database connection anywhere in this function's own
    signature or body. Delegates entirely to
    price_path_acquisition.acquire_price_path_evidence, which itself
    accepts no `conn` parameter by construction — this function cannot
    accidentally be called with one even by mistake."""
    return acquire_price_path_evidence(
        paper_trade_id=ctx.trade_id, user_id=ctx.user_id, symbol=ctx.symbol, market=ctx.market,
        market_timezone_name=ctx.market_timezone_name, market_tzinfo=market_tzinfo,
        entry_timestamp=ctx.entry_timestamp, exit_timestamp=ctx.exit_timestamp,
        fetch_bars_fn=fetch_bars_fn, fetch_splits_fn=fetch_splits_fn, fetch_dividends_fn=fetch_dividends_fn,
    )


def persist_price_path_evidence(conn, bundle: PricePathEvidenceBundle):
    """PHASE 3 — short write. Thin wrapper so callers don't need to
    import price_path_store directly; adds nothing beyond what
    price_path_store.persist_evidence already guarantees (idempotent,
    immutable, no partial row on failure since the INSERT either fully
    commits or the whole transaction rolls back)."""
    return price_path_store.persist_evidence(conn, bundle)


def _persisted_evidence_to_bundle(evidence) -> PricePathEvidenceBundle:
    """PHASE 4 helper — reconstructs a PricePathEvidenceBundle purely
    from an already-PERSISTED evidence row's own stored fields/bars,
    NEVER from a transient provider response. This is the concrete
    mechanism behind Stage H's 'reload rather than calculate from a
    transient provider response' requirement."""
    bars = tuple(
        PricePathBar(
            timestamp=datetime.fromisoformat(b["timestamp"]), interval=b["interval"],
            open=b["open"], high=b["high"], low=b["low"], close=b["close"], volume=b.get("volume"),
            session_date=date.fromisoformat(b["session_date"]), source_id=b["source_id"],
            adjustment_basis=b["adjustment_basis"], verification_level=b["verification_level"],
        )
        for b in evidence.bars
    )
    return PricePathEvidenceBundle(
        evidence_bundle_version=evidence.evidence_bundle_version, paper_trade_id=evidence.paper_trade_id,
        user_id=evidence.user_id, symbol=evidence.symbol, market=evidence.market,
        source_id=evidence.source_id, source_type=evidence.source_type, source_version=evidence.source_version,
        provider_symbol=evidence.provider_symbol, price_adjustment_basis=evidence.price_adjustment_basis,
        bar_interval=evidence.bar_interval, market_timezone=evidence.market_timezone,
        entry_timestamp=evidence.entry_timestamp, exit_timestamp=evidence.exit_timestamp,
        entry_bar_policy=evidence.entry_bar_policy, exit_bar_policy=evidence.exit_bar_policy,
        requested_window_start=evidence.requested_window_start, requested_window_end=evidence.requested_window_end,
        observed_window_start=evidence.observed_window_start, observed_window_end=evidence.observed_window_end,
        bars_expected=evidence.bars_expected, bars_observed=evidence.bars_observed,
        missing_bar_count=evidence.missing_bar_count, data_completeness=evidence.data_completeness,
        freshness_basis=evidence.freshness_basis, acquisition_timestamp=evidence.acquisition_timestamp,
        source_manifest=evidence.source_manifest, limitations=tuple(evidence.limitations),
        bars=bars, evidence_hash=evidence.evidence_hash,
    )


@dataclasses.dataclass(frozen=True)
class PricePathReportPayload:
    status: str
    price_path_section: dict
    evidence_items: list
    claims: list
    limitations: list


def build_price_path_report_payload(
    evidence, *, entry_price: float, exit_price: float | None,
    applicable_stop: float | None, applicable_target: float | None, level_history_complete: bool,
    trade_id: int,
) -> PricePathReportPayload:
    """PHASE 4 — pure, no I/O. `evidence` is an already-persisted
    price-path evidence row (see _persisted_evidence_to_bundle)."""
    bundle = _persisted_evidence_to_bundle(evidence)
    excursion = compute_excursion(bundle, entry_price=entry_price, exit_price=exit_price)
    target_touch, stop_touch = detect_touches(bundle, applicable_stop=applicable_stop, applicable_target=applicable_target)
    order = classify_touch_order(
        target_touch, stop_touch, applicable_stop=applicable_stop, applicable_target=applicable_target,
        level_history_complete=level_history_complete,
    )

    evidence_items: list[dict] = []
    claims: list[dict] = []
    for items, claim in (
        build_mfe_claim(trade_id, excursion),
        build_mae_claim(trade_id, excursion),
        build_touch_order_claim(trade_id, order, target_touch, stop_touch),
    ):
        evidence_items.extend(dataclasses.asdict(i) for i in items)
        claims.append(dataclasses.asdict(claim))

    status = "COMPLETE" if (
        bundle.data_completeness == STATUS_COMPLETE and excursion.evidence_completeness == EXCURSION_COMPLETE
    ) else "LIMITED_EVIDENCE"

    section = {
        "price_path_status": bundle.data_completeness,
        "price_path_evidence_id": evidence.id,
        "mfe_abs": excursion.mfe_abs, "mfe_pct": excursion.mfe_pct,
        "mae_signed_abs": excursion.mae_signed_abs, "mae_signed_pct": excursion.mae_signed_pct,
        "mae_magnitude_abs": excursion.mae_magnitude_abs, "mae_magnitude_pct": excursion.mae_magnitude_pct,
        "mfe_timestamp": excursion.mfe_timestamp_first_observed.isoformat() if excursion.mfe_timestamp_first_observed else None,
        "mae_timestamp": excursion.mae_timestamp_first_observed.isoformat() if excursion.mae_timestamp_first_observed else None,
        "target_touch": target_touch.touched, "target_touch_type": target_touch.touch_type,
        "stop_touch": stop_touch.touched, "stop_touch_type": stop_touch.touch_type,
        "touch_order": order,
        "giveback_abs": excursion.exit_vs_mfe_giveback_abs, "giveback_pct": excursion.exit_vs_mfe_giveback_pct_of_entry,
        "captured_mfe_pct": excursion.captured_mfe_pct,
        "price_path_evidence_ids": list(excursion.bar_evidence_ids),
        "price_path_limitations": list(excursion.limitations),
    }
    return PricePathReportPayload(
        status=status, price_path_section=section, evidence_items=evidence_items, claims=claims,
        limitations=list(excursion.limitations),
    )


def persist_price_path_report(
    conn, *, prior_report: PersistedReport, payload: PricePathReportPayload, trade_id: int, user_id: str,
    market: str, report_trading_date, market_timezone: str, source_version: str,
    outbox_id: int | None = None, claimed_by: str | None = None,
    trade_context_ceiling: str = "COMPLETE",
    evidence_decision=None,
) -> tuple[PersistedReport, bool]:
    """PHASE 5 — short write. Merges the price-path payload additively
    on top of the prior report's own structured_report/evidence_items/
    claims (Stage I requirement 1 — Sprint 2 report rows are never
    updated; `prior_report` is only READ here, never written to), and
    persists under PRICE_PATH_REPORT_SCHEMA_VERSION with
    supersedes_report_id=prior_report.id. Settles the SAME leased
    outbox attempt in the SAME transaction as generate_and_persist
    already does, using the identical StaleLeaseError contract — a
    stale lease rolls back BOTH the report insert and the outbox
    mark (the caller's own `with conn.transaction():` block does that
    rollback; this function only raises)."""
    structured_report = dict(prior_report.structured_report)
    structured_report["price_path"] = payload.price_path_section
    evidence_items = list(prior_report.evidence_items) + payload.evidence_items
    claims = list(prior_report.claims) + payload.claims
    source_manifest = dict(prior_report.source_manifest)
    calc_suffix = price_path_calculation_suffix(source_version)
    source_manifest["price_path_calculation_version"] = calc_suffix

    # Stage J3 — the final status is the MINIMUM of every applicable
    # ceiling: the prior (Sprint 1/2) report's own status, this price-
    # path payload's own bar/excursion-completeness status, the Stage
    # J1A trade-context ceiling (missing/invalid entry-exit snapshot,
    # missing exit price) computed by price_path_eligibility.
    # evaluate_eligibility BEFORE acquisition ever ran, AND (Stage J1B
    # load-bearing integration) the evidence decision's own ceiling
    # (compatible-replay/acquisition-required/source-unavailable/
    # source-invalid/basis-incompatible/partial/ambiguous), computed by
    # price_path_evidence_decision AFTER evidence became available.
    # Composing all four through compute_report_completeness_ceiling
    # (never a two-way comparison) is what actually makes J1B's ceiling
    # load-bearing rather than merely advisory — a classifier called but
    # never consulted here would still let a downstream COMPLETE value
    # override it.
    from services.postmortem.price_path_evidence_decision import compute_report_completeness_ceiling
    evidence_ceiling = evidence_decision.report_completeness_ceiling if evidence_decision is not None else "COMPLETE"
    status = compute_report_completeness_ceiling(
        prior_report.status, payload.status, trade_context_ceiling, evidence_ceiling,
    )

    # Stage J7 (decision provenance) — the J1B fields that explain WHY a
    # report is limited are persisted alongside the rest of the price-
    # path section, never as an unrestricted raw provider payload; a
    # deterministic replay of the same trade/evidence reproduces the
    # identical decision fields.
    if evidence_decision is not None:
        structured_report["price_path"] = dict(structured_report["price_path"])
        structured_report["price_path"]["evidence_decision"] = {
            "evidence_status": evidence_decision.evidence_status,
            "calculation_status": evidence_decision.calculation_status,
            "provider_call_expected": evidence_decision.provider_call_expected,
            "reason_codes": list(evidence_decision.reason_codes),
        }

    report, created = report_store.persist_report(
        conn, paper_trade_id=trade_id, user_id=user_id, market=market,
        report_trading_date=report_trading_date, market_timezone=market_timezone,
        report_schema_version=PRICE_PATH_REPORT_SCHEMA_VERSION,
        calculation_version=calc_suffix,
        attribution_rules_version=prior_report.attribution_rules_version,
        evidence_bundle_version=prior_report.evidence_bundle_version,
        status=status, structured_report=structured_report,
        evidence_items=evidence_items, claims=claims, source_manifest=source_manifest,
        evidence_gaps=list(prior_report.evidence_gaps) + list(payload.limitations)
        + (list(evidence_decision.limitations) if evidence_decision is not None else []),
        warnings=list(prior_report.warnings),
        supersedes_report_id=prior_report.id,
    )

    if outbox_id is not None:
        marked = outbox_ops.mark_terminal(conn, outbox_id=outbox_id, status=report.status, claimed_by=claimed_by)
        if not marked:
            raise generation_service.StaleLeaseError(
                f"outbox row {outbox_id} lease no longer held by this attempt at mark-terminal time"
            )

    return report, created
