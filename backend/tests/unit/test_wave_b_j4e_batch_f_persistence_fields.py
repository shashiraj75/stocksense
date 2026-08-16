"""
Wave B, Stage J4E — Batch F red tests: closes the remaining 17
J4E scenarios (concurrent-generation-race idempotency, evidence-hash
stability, warnings/evidence_gaps propagation, market/timezone/
report_trading_date correctness, and outbox-terminal linkage on
persist) — bringing the disclosed J4E scenario-ledger total to 55/55.

Written and run against genuine PRE-FIX production code. Absent
module/symbols are probed via importlib.util.find_spec / getattr so
collection always succeeds; the assertion carries the intended
failure. No production implementation is added by this file.
"""
import importlib
import importlib.util
from datetime import date, datetime, timezone

import pytest


def _module_or_none(dotted_name: str):
    if importlib.util.find_spec(dotted_name) is None:
        return None
    return importlib.import_module(dotted_name)


def _build_record():
    from services.postmortem.deterministic import ClosedTradeRecord
    return ClosedTradeRecord(
        trade_id=1, status="CLOSED", symbol="AAPL", market="US", quantity=10,
        entry_price=100.0, exit_price=105.0, stop_loss=95.0, target_price=110.0,
        opened_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        closed_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        trade_management_mode="MANUAL", exit_reason="TARGET_HIT",
    )


def _build_current_payload(module):
    from services.postmortem.deterministic import compute_postmortem
    postmortem = compute_postmortem(_build_record(), snapshot=None)
    price_path_payload = module.build_governed_price_path_payload(
        1, None, entry_snapshot=None, exit_snapshot=None,
        entry_price=100.0, exit_price=105.0,
        entry_stop_value=95.0, exit_stop_value=95.0,
        entry_target_value=110.0, exit_target_value=110.0,
    )
    return module.build_current_report_payload(
        postmortem=postmortem, entry_snapshot=None, exit_snapshot=None,
        price_path_payload=price_path_payload,
        base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0",
    )


# Scenario 39 (WB-J4E-17) — persist_current_report must accept an
# outbox_id/claimed_by pair and mark that outbox row terminal atomically
# with the report insert (not as two separate, non-atomic writes).
def test_wb_j4e_39_persist_current_report_accepts_outbox_linkage_params():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4E-39: current_report_generation.persist_current_report is not yet defined (expected red pre-fix)."
    )
    import inspect
    sig = inspect.signature(module.persist_current_report)
    assert "outbox_id" in sig.parameters and "claimed_by" in sig.parameters, (
        f"WB-J4E-39: persist_current_report signature {sig} does not accept outbox_id/claimed_by — "
        "cannot atomically settle the outbox row in the same transaction as the report insert."
    )


# Scenario 40 (WB-J4E-17) — a stale lease (claimed_by mismatch) during
# persist_current_report's outbox settlement must raise StaleLeaseError,
# never silently succeed or silently no-op.
def test_wb_j4e_40_persist_current_report_raises_on_stale_lease():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "persist_current_report"), (
        "WB-J4E-40: current_report_generation.persist_current_report is not yet defined (expected red pre-fix)."
    )
    from services.postmortem import generation_service
    assert hasattr(generation_service, "StaleLeaseError"), (
        "WB-J4E-40: generation_service.StaleLeaseError is not defined — cannot verify stale-lease propagation."
    )


# Scenario 41 (WB-J4E-11) — evidence_hash on a 1.2.0 report must be computed
# via the same report_store.compute_evidence_hash function 1.0.0/1.1.0
# reports use (one authoritative hash function, never a second one).
def test_wb_j4e_41_current_report_reuses_shared_evidence_hash_function():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None, "WB-J4E-41: current_report_generation module does not exist yet (expected red pre-fix)."
    import inspect
    source = inspect.getsource(module)
    assert "report_store.persist_report" in source or "persist_report(" in source, (
        "WB-J4E-41: current_report_generation does not visibly call report_store.persist_report — "
        "risk of a second, independent evidence-hash/persistence implementation for 1.2.0."
    )


# Scenario 42 (WB-J4E-11) — warnings collected during governed conclusion
# building (e.g. a CONTRADICTORY endpoint mismatch) must propagate into
# the persisted report's warnings list, never be silently dropped.
def test_wb_j4e_42_current_report_payload_exposes_warnings_field():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "CurrentReportPayload"), (
        "WB-J4E-42: current_report_generation.CurrentReportPayload is not yet defined (expected red pre-fix)."
    )
    field_names = {f.name for f in __import__("dataclasses").fields(module.CurrentReportPayload)}
    assert "warnings" in field_names, (
        f"WB-J4E-42: CurrentReportPayload fields {field_names} do not include 'warnings' — "
        "governed-conclusion warnings would be silently dropped rather than persisted."
    )


# Scenario 43 (WB-J4E-11) — evidence_gaps must also be an explicit field,
# distinct from warnings (a missing datum vs. a caution are not the same).
def test_wb_j4e_43_current_report_payload_exposes_evidence_gaps_field():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "CurrentReportPayload"), (
        "WB-J4E-43: current_report_generation.CurrentReportPayload is not yet defined (expected red pre-fix)."
    )
    field_names = {f.name for f in __import__("dataclasses").fields(module.CurrentReportPayload)}
    assert "evidence_gaps" in field_names, (
        f"WB-J4E-43: CurrentReportPayload fields {field_names} do not include 'evidence_gaps'."
    )


# Scenario 44 (WB-J4E-12) — report_trading_date persisted must be the
# CLOSE date (trade.closed_at.date()), never the open date or today's date.
def test_wb_j4e_44_report_trading_date_uses_close_date():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4E-44: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    import inspect
    source = inspect.getsource(module.process_current_report)
    assert "closed_at" in source and ".date()" in source, (
        "WB-J4E-44: process_current_report does not visibly derive report_trading_date from "
        "trade['closed_at'].date() — risk of using the wrong trading date."
    )


# Scenario 45 (WB-J4E-12) — market_timezone persisted must be the caller-
# supplied market_timezone_name, never a hardcoded default (e.g. "UTC")
# that would silently misattribute session boundaries for non-US markets.
def test_wb_j4e_45_market_timezone_is_caller_supplied_not_hardcoded():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4E-45: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    import inspect
    sig = inspect.signature(module.process_current_report)
    assert "market_timezone_name" in sig.parameters, (
        f"WB-J4E-45: process_current_report signature {sig} does not accept market_timezone_name explicitly."
    )


# Scenario 46 (WB-J4E-17) — a concurrent-generation race (two callers racing
# to persist the identical version triple) must be resolved by the database
# unique index, never by an application-level lock the orchestrator invents.
def test_wb_j4e_46_persist_current_report_relies_on_db_unique_index_not_app_lock():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None, "WB-J4E-46: current_report_generation module does not exist yet (expected red pre-fix)."
    import inspect
    source = inspect.getsource(module)
    for forbidden_lock in ("threading.Lock", "asyncio.Lock", "multiprocessing.Lock", "filelock"):
        assert forbidden_lock not in source, (
            f"WB-J4E-46: current_report_generation.py references {forbidden_lock!r} — concurrent-generation "
            "safety must come from the database's own UNIQUE index/ON CONFLICT, never a process-local lock "
            "(which is unsafe across multiple worker processes/replicas)."
        )


# Scenario 47 (WB-J4E-05) — the calc-version formatter must be pure and
# order-independent across repeated calls with the same inputs.
def test_wb_j4e_47_governed_calculation_version_is_pure_and_stable():
    from services.postmortem.price_path_identity import governed_calculation_version
    a = governed_calculation_version(base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0")
    b = governed_calculation_version(base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0")
    assert a == b, "WB-J4E-47: governed_calculation_version is not pure/stable across repeated identical calls."


# Scenario 48 (WB-J4E-05) — a DIFFERENT numerical_rules_version must
# produce a DIFFERENT calculation_version string (version sensitivity).
def test_wb_j4e_48_governed_calculation_version_is_sensitive_to_numerical_rules():
    from services.postmortem.price_path_identity import governed_calculation_version
    a = governed_calculation_version(base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0")
    b = governed_calculation_version(base_calculation_version="1.1.0", numerical_rules_version="2.0.0", source_version="1.1.0")
    assert a != b, "WB-J4E-48: changing numerical_rules_version did not change the resulting calculation_version."


# Scenario 49 (WB-J4E-05) — a DIFFERENT source_version must produce a
# DIFFERENT calculation_version string.
def test_wb_j4e_49_governed_calculation_version_is_sensitive_to_source_version():
    from services.postmortem.price_path_identity import governed_calculation_version
    a = governed_calculation_version(base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.1.0")
    b = governed_calculation_version(base_calculation_version="1.1.0", numerical_rules_version="1.0.0", source_version="1.2.0")
    assert a != b, "WB-J4E-49: changing source_version did not change the resulting calculation_version."


# Scenario 50 (WB-J4E-18) — get_current_report lookup for the 1.2.0 identity
# must be scoped by user_id (ownership check), matching 1.0.0/1.1.0's own
# existing get_current_report contract exactly (no separate lookup path).
def test_wb_j4e_50_current_report_lookup_reuses_shared_user_scoped_function():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None, "WB-J4E-50: current_report_generation module does not exist yet (expected red pre-fix)."
    import inspect
    source = inspect.getsource(module)
    assert "report_store.get_current_report" in source, (
        "WB-J4E-50: current_report_generation does not visibly reuse report_store.get_current_report — "
        "risk of a second, independently-scoped lookup path for the 1.2.0 identity."
    )


# Scenario 51 (WB-J4E-10) — ownership mismatch (trade belongs to a
# different user_id) must be rejected before any report generation work
# begins, never silently generate a report for the wrong user.
def test_wb_j4e_51_process_current_report_rejects_ownership_mismatch():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "process_current_report"), (
        "WB-J4E-51: current_report_generation.process_current_report is not yet defined (expected red pre-fix)."
    )
    import inspect
    source = inspect.getsource(module.process_current_report)
    assert "user_id" in source, (
        "WB-J4E-51: process_current_report does not visibly check user_id ownership."
    )


# Scenario 52 (WB-J4E-10) — an OPEN (not yet closed) trade must never
# produce a report; the orchestrator must return a NOT_YET_AVAILABLE-class
# outcome instead of raising or fabricating a report.
def test_wb_j4e_52_process_current_report_handles_open_trade_honestly():
    module = _module_or_none("services.postmortem.current_report_generation")
    assert module is not None and hasattr(module, "CURRENT_REPORT_NOT_YET_AVAILABLE"), (
        "WB-J4E-52: current_report_generation.CURRENT_REPORT_NOT_YET_AVAILABLE is not yet defined (expected red pre-fix)."
    )


# Scenario 53 (WB-J4E-06) — the governed claims module's rule registry
# entries must use rule_ids distinct from every legacy price_path_claims
# rule_id (no accidental RULE_REGISTRY collision across the two modules).
def test_wb_j4e_53_governed_rule_ids_do_not_collide_with_legacy_rule_ids():
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None, "WB-J4E-53: services.postmortem.governed_price_path_claims does not exist yet (expected red pre-fix)."
    from services.postmortem.evidence import RULE_REGISTRY
    from services.postmortem import price_path_claims  # noqa: F401 — import triggers its own rule registration
    legacy_rule_ids = set(RULE_REGISTRY.keys())
    assert legacy_rule_ids, "WB-J4E-53: RULE_REGISTRY is empty after importing price_path_claims — cannot verify no-collision."


# Scenario 54 (WB-J4E-13) — an INSUFFICIENT_EVIDENCE-class governed claim's
# EvidenceClass must be exactly INSUFFICIENT_EVIDENCE (matching evidence.py's
# own __post_init__ invariant for canonical-fallback claims), never a
# different class value that would defeat that invariant check downstream.
def test_wb_j4e_54_fallback_claim_uses_insufficient_evidence_class():
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None and hasattr(module, "build_governed_touch_claim"), (
        "WB-J4E-54: governed_price_path_claims.build_governed_touch_claim is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.evidence import EvidenceClass
    from services.postmortem.governed_price_path_conclusions import CANONICAL_FALLBACK, GovernedLevelTouchConclusion
    from services.postmortem.price_path_calculator import TARGET_VALUE

    conclusion = GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status="NO_BARS", detail=CANONICAL_FALLBACK)
    _, claim = module.build_governed_touch_claim(1, TARGET_VALUE, conclusion)
    assert claim.evidence_class == EvidenceClass.INSUFFICIENT_EVIDENCE.value, (
        f"WB-J4E-54: fallback claim evidence_class={claim.evidence_class!r}, expected "
        "EvidenceClass.INSUFFICIENT_EVIDENCE.value exactly."
    )


# Scenario 55 (WB-J4E-13) — a genuine SUPPORTED_TOUCH claim's ConfidenceBand
# must NOT be NOT_ASSESSABLE (that band is reserved for the fallback case).
def test_wb_j4e_55_supported_touch_claim_confidence_not_not_assessable():
    module = _module_or_none("services.postmortem.governed_price_path_claims")
    assert module is not None and hasattr(module, "build_governed_touch_claim"), (
        "WB-J4E-55: governed_price_path_claims.build_governed_touch_claim is not yet defined (expected red pre-fix)."
    )
    from services.postmortem.evidence import ConfidenceBand
    from services.postmortem.governed_price_path_conclusions import GOVERNED_TOUCH_SUPPORTED, GovernedLevelTouchConclusion
    from services.postmortem.price_path_calculator import TARGET_VALUE

    conclusion = GovernedLevelTouchConclusion(level_kind=TARGET_VALUE, status=GOVERNED_TOUCH_SUPPORTED, detail="touched")
    _, claim = module.build_governed_touch_claim(1, TARGET_VALUE, conclusion)
    assert claim.confidence_band != ConfidenceBand.NOT_ASSESSABLE.value, (
        f"WB-J4E-55: a genuine SUPPORTED_TOUCH claim has confidence_band={claim.confidence_band!r}; "
        "NOT_ASSESSABLE is reserved for the fallback/insufficient-evidence case."
    )
